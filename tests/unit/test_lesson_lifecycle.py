"""Юнит-тесты confirm / cancel / complete / reschedule (FR-06…FR-09) без БД и брокера."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.clock import AdjustableClock
from app.domain.rules import HardFailure, RuleOutcome
from app.errors import ApiError
from app.models import LessonFormat, LessonStatus
from app.services.lessons import (
    cancel_lesson,
    complete_lesson,
    confirm_lesson,
    reschedule_lesson,
)

G1 = UUID("9a000000-0000-4000-8000-000000000001")
I1 = UUID("1b000000-0000-4000-8000-000000000001")
C1 = UUID("2c000000-0000-4000-8000-000000000001")
L1 = UUID("3d000000-0000-4000-8000-000000000001")
FROZEN = datetime(2026, 11, 30, 3, 0, 0, tzinfo=UTC)


@dataclass
class FakeLesson:
    """Занятие для lifecycle без ORM."""

    id: UUID
    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    status: LessonStatus
    starts_at: datetime
    ends_at: datetime
    cancel_reason: str | None
    updated_at: datetime


def _lesson(*, status: LessonStatus, ends_at: datetime | None = None) -> FakeLesson:
    return FakeLesson(
        id=L1,
        group_id=G1,
        instructor_id=I1,
        classroom_id=C1,
        format=LessonFormat.IN_PERSON,
        status=status,
        starts_at=datetime(2026, 12, 1, 5, 0, tzinfo=UTC),
        ends_at=ends_at or datetime(2026, 12, 1, 6, 30, tzinfo=UTC),
        cancel_reason=None,
        updated_at=FROZEN,
    )


def _clock() -> AdjustableClock:
    clock = AdjustableClock()
    clock.freeze(FROZEN)
    return clock


@pytest.mark.asyncio
async def test_confirm_planned_builds_event() -> None:
    lesson = _lesson(status=LessonStatus.PLANNED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)
    session.flush = AsyncMock()

    result = await confirm_lesson(session, _clock(), L1)

    assert result.lesson.status is LessonStatus.CONFIRMED
    assert result.event is not None
    assert result.event["event_type"] == "LessonConfirmed"
    assert result.event["idempotency_key"] == f"lesson-confirmed:{L1}"
    assert result.event["occurred_at"] == "2026-11-30T03:00:00Z"
    assert result.event["payload"]["lesson_id"] == str(L1)
    assert result.event["payload"]["classroom_id"] == str(C1)
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_when_already_confirmed_is_forbidden() -> None:
    lesson = _lesson(status=LessonStatus.CONFIRMED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)

    with pytest.raises(ApiError) as exc_info:
        await confirm_lesson(session, _clock(), L1)

    err = exc_info.value
    assert err.status_code == 409
    assert err.code == "TRANSITION_FORBIDDEN"
    assert err.details == {"current_status": "CONFIRMED", "target_status": "CONFIRMED"}


@pytest.mark.asyncio
async def test_confirm_not_found() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    with pytest.raises(ApiError) as exc_info:
        await confirm_lesson(session, _clock(), L1)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_cancel_planned_with_reason() -> None:
    lesson = _lesson(status=LessonStatus.PLANNED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)
    session.flush = AsyncMock()

    result = await cancel_lesson(session, _clock(), L1, reason="Перенос")

    assert result.lesson.status is LessonStatus.CANCELLED
    assert result.lesson.cancel_reason == "Перенос"
    assert result.event is not None
    assert result.event["event_type"] == "LessonCancelled"
    assert result.event["idempotency_key"] == f"lesson-cancelled:{L1}"
    assert result.event["payload"]["previous_status"] == "PLANNED"
    assert result.event["payload"]["reason"] == "Перенос"


@pytest.mark.asyncio
async def test_cancel_from_in_progress_forbidden() -> None:
    lesson = _lesson(status=LessonStatus.IN_PROGRESS)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)

    with pytest.raises(ApiError) as exc_info:
        await cancel_lesson(session, _clock(), L1)

    assert exc_info.value.code == "TRANSITION_FORBIDDEN"
    assert exc_info.value.details == {
        "current_status": "IN_PROGRESS",
        "target_status": "CANCELLED",
    }


@pytest.mark.asyncio
async def test_complete_when_finished() -> None:
    ends = datetime(2026, 11, 30, 2, 0, tzinfo=UTC)
    lesson = _lesson(status=LessonStatus.IN_PROGRESS, ends_at=ends)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)
    session.flush = AsyncMock()

    result = await complete_lesson(session, _clock(), L1)

    assert result.lesson.status is LessonStatus.COMPLETED
    assert result.event is None


@pytest.mark.asyncio
async def test_complete_before_ends_at() -> None:
    ends = datetime(2026, 11, 30, 5, 0, tzinfo=UTC)
    lesson = _lesson(status=LessonStatus.IN_PROGRESS, ends_at=ends)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)

    with pytest.raises(ApiError) as exc_info:
        await complete_lesson(session, _clock(), L1)

    err = exc_info.value
    assert err.code == "LESSON_NOT_FINISHED"
    assert err.details == {"ends_at": "2026-11-30T05:00:00Z", "now": "2026-11-30T03:00:00Z"}


@pytest.mark.asyncio
async def test_complete_wrong_status_before_br08() -> None:
    ends = datetime(2026, 11, 30, 2, 0, tzinfo=UTC)
    lesson = _lesson(status=LessonStatus.PLANNED, ends_at=ends)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)

    with pytest.raises(ApiError) as exc_info:
        await complete_lesson(session, _clock(), L1)

    assert exc_info.value.code == "TRANSITION_FORBIDDEN"
    assert exc_info.value.details == {
        "current_status": "PLANNED",
        "target_status": "COMPLETED",
    }


@pytest.mark.asyncio
async def test_reschedule_success(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _lesson(status=LessonStatus.PLANNED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()

    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome()

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)

    new_starts = datetime(2026, 12, 4, 5, 0, tzinfo=UTC)
    new_ends = datetime(2026, 12, 4, 6, 30, tzinfo=UTC)
    result = await reschedule_lesson(
        session,
        MagicMock(),
        _clock(),
        L1,
        {"starts_at": new_starts, "ends_at": new_ends},
    )

    assert source.status is LessonStatus.RESCHEDULED
    assert result.lesson.status is LessonStatus.PLANNED
    assert result.lesson.group_id == G1
    assert result.lesson.rescheduled_from_id == L1
    assert result.lesson.starts_at == new_starts
    assert result.lesson.instructor_id == I1
    assert result.lesson.classroom_id == C1
    assert result.event["event_type"] == "LessonRescheduled"
    assert result.event["idempotency_key"] == f"lesson-rescheduled:{L1}"
    assert result.event["payload"]["lesson_id"] == str(L1)
    assert result.event["payload"]["rescheduled_to_id"] == str(result.lesson.id)
    assert result.event["payload"]["previous"]["starts_at"] == "2026-12-01T05:00:00Z"
    assert result.event["payload"]["new"]["starts_at"] == "2026-12-04T05:00:00Z"
    session.add.assert_called_once()
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_reschedule_wrong_status() -> None:
    source = _lesson(status=LessonStatus.IN_PROGRESS)
    session = AsyncMock()
    session.get = AsyncMock(return_value=source)

    with pytest.raises(ApiError) as exc_info:
        await reschedule_lesson(
            session,
            MagicMock(),
            _clock(),
            L1,
            {
                "starts_at": datetime(2026, 12, 4, 5, 0, tzinfo=UTC),
                "ends_at": datetime(2026, 12, 4, 6, 30, tzinfo=UTC),
            },
        )

    assert exc_info.value.code == "TRANSITION_FORBIDDEN"
    assert exc_info.value.details == {
        "current_status": "IN_PROGRESS",
        "target_status": "RESCHEDULED",
    }


@pytest.mark.asyncio
async def test_reschedule_already_has_child() -> None:
    source = _lesson(status=LessonStatus.CONFIRMED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.scalar = AsyncMock(return_value=UUID("0f6c2b1e-3d4a-4b5c-9d6e-7f8a9b0c1d2e"))

    with pytest.raises(ApiError) as exc_info:
        await reschedule_lesson(
            session,
            MagicMock(),
            _clock(),
            L1,
            {
                "starts_at": datetime(2026, 12, 4, 5, 0, tzinfo=UTC),
                "ends_at": datetime(2026, 12, 4, 6, 30, tzinfo=UTC),
            },
        )

    assert exc_info.value.code == "TRANSITION_FORBIDDEN"
    assert source.status is LessonStatus.CONFIRMED


@pytest.mark.asyncio
async def test_reschedule_hard_skips_write(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _lesson(status=LessonStatus.PLANNED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()

    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome(
            hard=HardFailure(409, "SCHEDULE_CONFLICT", "Конфликт", {"conflicts": []})
        )

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)

    with pytest.raises(ApiError) as exc_info:
        await reschedule_lesson(
            session,
            MagicMock(),
            _clock(),
            L1,
            {
                "starts_at": datetime(2026, 12, 4, 5, 0, tzinfo=UTC),
                "ends_at": datetime(2026, 12, 4, 6, 30, tzinfo=UTC),
            },
        )

    assert exc_info.value.code == "SCHEDULE_CONFLICT"
    assert source.status is LessonStatus.PLANNED
    session.flush.assert_not_awaited()
