"""Юнит-тесты списка занятий и PATCH (FR-03, FR-05) без реальной БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.broker import UnconfiguredBrokerProbe
from app.clock import AdjustableClock, SystemClock
from app.context import AppContext
from app.db import make_engine, make_session_factory
from app.domain.rules import HardFailure, RuleOutcome
from app.errors import ApiError
from app.jobs import JobRegistry
from app.main import create_app
from app.models import LessonFormat, LessonStatus
from app.services.lessons import list_lessons, update_lesson
from app.settings import Settings

G1 = UUID("9a000000-0000-4000-8000-000000000001")
I1 = UUID("1b000000-0000-4000-8000-000000000001")
I2 = UUID("1b000000-0000-4000-8000-000000000002")
L1 = UUID("3d000000-0000-4000-8000-000000000001")


@dataclass
class FakeLesson:
    """Простой объект занятия для update_lesson без ORM."""

    id: UUID
    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    status: LessonStatus
    starts_at: datetime
    ends_at: datetime
    updated_at: datetime


def _planned_lesson() -> FakeLesson:
    return FakeLesson(
        id=L1,
        group_id=G1,
        instructor_id=I1,
        classroom_id=None,
        format=LessonFormat.ONLINE,
        status=LessonStatus.PLANNED,
        starts_at=datetime(2026, 12, 1, 5, 0, tzinfo=UTC),
        ends_at=datetime(2026, 12, 1, 6, 30, tzinfo=UTC),
        updated_at=datetime(2026, 11, 30, 3, 0, tzinfo=UTC),
    )


def _build_app() -> FastAPI:
    settings = Settings(
        test_mode=True,
        database_url="postgresql+asyncpg://wa:wa@127.0.0.1:1/unreachable",
    )
    engine = make_engine(settings.database_url)
    context = AppContext(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        clock=AdjustableClock(),
        broker_probe=UnconfiguredBrokerProbe(),
        jobs=JobRegistry(),
    )
    app = create_app()
    app.state.context = context
    return app


@pytest.mark.asyncio
async def test_list_lessons_returns_total_and_page() -> None:
    lesson_a = MagicMock(id=uuid4())
    lesson_b = MagicMock(id=uuid4())
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=5)
    scalars_result = MagicMock()
    scalars_result.all.return_value = [lesson_a, lesson_b]
    session.scalars = AsyncMock(return_value=scalars_result)

    page = await list_lessons(session, instructor_id=I1, limit=2, offset=0)

    assert page.total == 5
    assert page.items == (lesson_a, lesson_b)
    session.scalar.assert_awaited()
    session.scalars.assert_awaited()


@pytest.mark.asyncio
async def test_update_lesson_not_found() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    with pytest.raises(ApiError) as raised:
        await update_lesson(
            session,
            MagicMock(),
            AdjustableClock(SystemClock()),
            L1,
            {"instructor_id": I2},
        )
    assert raised.value.status_code == 404
    assert raised.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_update_lesson_not_planned() -> None:
    lesson = _planned_lesson()
    lesson.status = LessonStatus.CONFIRMED
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)

    with pytest.raises(ApiError) as raised:
        await update_lesson(
            session,
            MagicMock(),
            AdjustableClock(SystemClock()),
            L1,
            {"instructor_id": I2},
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "LESSON_NOT_EDITABLE"
    assert raised.value.details["current_status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_update_lesson_success_applies_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = _planned_lesson()
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)
    session.flush = AsyncMock()

    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome()

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)
    clock = AdjustableClock(SystemClock())
    clock.freeze(datetime(2026, 12, 2, 10, 0, tzinfo=UTC))

    result = await update_lesson(
        session,
        MagicMock(),
        clock,
        L1,
        {"instructor_id": I2},
    )

    assert result.lesson.instructor_id == I2
    assert result.lesson.status is LessonStatus.PLANNED
    assert result.lesson.updated_at == datetime(2026, 12, 2, 10, 0, tzinfo=UTC)
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_update_lesson_hard_failure_skips_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = _planned_lesson()
    original_instructor = lesson.instructor_id
    session = AsyncMock()
    session.get = AsyncMock(return_value=lesson)
    session.flush = AsyncMock()

    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome(
            hard=HardFailure(409, "SCHEDULE_CONFLICT", "Конфликт", {"conflicts": []})
        )

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)

    with pytest.raises(ApiError) as raised:
        await update_lesson(
            session,
            MagicMock(),
            AdjustableClock(SystemClock()),
            L1,
            {"instructor_id": I2},
        )
    assert raised.value.code == "SCHEDULE_CONFLICT"
    assert lesson.instructor_id == original_instructor
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_lessons_limit_over_max_is_422() -> None:
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/lessons", params={"limit": 201})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_patch_empty_body_is_422() -> None:
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.patch(f"/lessons/{L1}", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
