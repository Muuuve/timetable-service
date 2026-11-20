"""Маппинг ошибок из validate: create → ApiError; validate при 409 → violations (FR-02)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.domain.rules import HardFailure, RuleOutcome, WarningItem
from app.errors import ApiError
from app.models import LessonFormat
from app.services.lessons import (
    LessonInput,
    _to_validate_result,
    create_lesson,
    validate_lesson,
)

G1 = UUID("9a000000-0000-4000-8000-000000000001")
I1 = UUID("1b000000-0000-4000-8000-000000000001")


def _input() -> LessonInput:
    return LessonInput(
        group_id=G1,
        instructor_id=I1,
        classroom_id=None,
        format=LessonFormat.ONLINE,
        starts_at=datetime(2026, 12, 1, 6, 0, tzinfo=UTC),
        ends_at=datetime(2026, 12, 1, 7, 0, tzinfo=UTC),
    )


def _conflict() -> HardFailure:
    return HardFailure(
        409,
        "SCHEDULE_CONFLICT",
        "Обнаружен конфликт расписания",
        {"conflicts": [{"resource": "instructor"}]},
    )


def test_validate_maps_schedule_conflict_to_violations() -> None:
    outcome = RuleOutcome(hard=_conflict())
    result = _to_validate_result(outcome)
    assert result.ok is False
    assert len(result.violations) == 1
    assert result.violations[0]["code"] == "SCHEDULE_CONFLICT"
    assert result.violations[0]["details"]["conflicts"] == [{"resource": "instructor"}]


def test_validate_maps_classroom_too_small_to_violations() -> None:
    hard = HardFailure(
        409,
        "CLASSROOM_TOO_SMALL",
        "Аудитория мала для группы",
        {"capacity": 10, "group_size": 25},
    )
    result = _to_validate_result(RuleOutcome(hard=hard))
    assert result.ok is False
    assert result.violations[0]["code"] == "CLASSROOM_TOO_SMALL"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("VALIDATION_ERROR", 422),
        ("CLASSROOM_REQUIRED", 422),
        ("GROUP_NOT_FOUND", 422),
    ],
)
def test_validate_rethrows_non_409_as_api_error(code: str, status: int) -> None:
    hard = HardFailure(status, code, "Невалидные данные", {"fields": {"ends_at": "…"}})
    with pytest.raises(ApiError) as raised:
        _to_validate_result(RuleOutcome(hard=hard))
    assert raised.value.status_code == status
    assert raised.value.code == code


def test_validate_ok_keeps_soft_warnings() -> None:
    warning = WarningItem(
        code="WORKLOAD_SOFT_LIMIT",
        message="Превышена дневная норма преподавателя",
        details={"date": "2026-12-01", "max_hours": 4, "planned_hours": 5},
    )
    result = _to_validate_result(RuleOutcome(warnings=(warning,)))
    assert result.ok is True
    assert result.violations == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "WORKLOAD_SOFT_LIMIT"


@pytest.mark.asyncio
async def test_create_raises_api_error_on_schedule_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome(hard=_conflict())

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)

    with pytest.raises(ApiError) as raised:
        await create_lesson(MagicMock(), MagicMock(), MagicMock(), _input())
    assert raised.value.status_code == 409
    assert raised.value.code == "SCHEDULE_CONFLICT"


@pytest.mark.asyncio
async def test_validate_lesson_returns_violations_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_evaluate(*_args: object, **_kwargs: object) -> RuleOutcome:
        return RuleOutcome(hard=_conflict())

    monkeypatch.setattr("app.services.lessons._evaluate", fake_evaluate)

    result = await validate_lesson(MagicMock(), MagicMock(), _input())
    assert result.ok is False
    assert result.violations[0]["code"] == "SCHEDULE_CONFLICT"
