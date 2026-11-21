"""Юнит-тесты lesson-status-sweeper (FR-10) без реальной БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.clock import AdjustableClock
from app.models import LessonStatus
from app.services.lessons import LESSON_STATUS_SWEEPER, sweep_confirmed_lessons

FROZEN = datetime(2026, 11, 30, 3, 0, 0, tzinfo=UTC)


@dataclass
class FakeLesson:
    """Занятие для sweeper без ORM."""

    id: UUID
    status: LessonStatus
    starts_at: datetime
    updated_at: datetime


def _clock() -> AdjustableClock:
    clock = AdjustableClock()
    clock.freeze(FROZEN)
    return clock


@pytest.mark.asyncio
async def test_sweep_moves_due_confirmed() -> None:
    due = FakeLesson(
        id=uuid4(),
        status=LessonStatus.CONFIRMED,
        starts_at=datetime(2026, 11, 30, 2, 0, tzinfo=UTC),
        updated_at=FROZEN,
    )
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = [due]
    session.scalars = AsyncMock(return_value=scalars_result)
    session.flush = AsyncMock()

    processed = await sweep_confirmed_lessons(session, _clock())

    assert processed == 1
    assert due.status is LessonStatus.IN_PROGRESS
    assert due.updated_at == FROZEN
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_sweep_noop_when_none_due() -> None:
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    session.scalars = AsyncMock(return_value=scalars_result)
    session.flush = AsyncMock()

    processed = await sweep_confirmed_lessons(session, _clock())

    assert processed == 0
    session.flush.assert_not_awaited()


def test_sweeper_job_name() -> None:
    assert LESSON_STATUS_SWEEPER == "lesson-status-sweeper"
