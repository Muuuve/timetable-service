"""Создание и валидация занятия (FR-01, FR-02).

Оркестрация: группа из БД → local BR-01…03 → PeopleClient → existing → домен → INSERT.
HTTP-роуты сюда не входят (пункт 3 tasks.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.people import PeopleClient
from app.clock import Clock
from app.domain.rules import (
    ExistingLesson,
    HardFailure,
    LessonCandidate,
    RuleOutcome,
    WarningItem,
    evaluate_lesson_rules,
    local_hard_failure,
)
from app.errors import ApiError
from app.models import Group, Lesson, LessonFormat, LessonStatus

# Коды hard, которые validate отдаёт в violations (200), а не как HTTP-ошибку.
_VALIDATE_AS_VIOLATION = frozenset({"CLASSROOM_TOO_SMALL", "SCHEDULE_CONFLICT"})


@dataclass(frozen=True, slots=True)
class LessonInput:
    """Вход create/validate (тело LessonCreate без HTTP)."""

    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class ValidateResult:
    """Результат validate без записи (openapi ValidationResult)."""

    ok: bool
    violations: tuple[dict[str, Any], ...]
    warnings: tuple[WarningItem, ...]


@dataclass(frozen=True, slots=True)
class CreateResult:
    """Успешное создание: занятие + soft-warnings."""

    lesson: Lesson
    warnings: tuple[WarningItem, ...]


async def validate_lesson(
    session: AsyncSession,
    people: PeopleClient,
    data: LessonInput,
) -> ValidateResult:
    """Проверить занятие без INSERT.

    - hard 409 → violations, ok=False (не ApiError);
    - hard 422 / stub 502/504 → ApiError (как у create);
    - soft → warnings.
    """
    outcome = await _evaluate(session, people, data)
    return _to_validate_result(outcome)


async def create_lesson(
    session: AsyncSession,
    people: PeopleClient,
    clock: Clock,
    data: LessonInput,
) -> CreateResult:
    """Проверить и создать занятие (PLANNED) в текущей транзакции.

    - hard → ApiError, записи нет;
    - успех → INSERT + warnings (могут быть пусты).
    Commit делает вызывающий (``session_scope``).
    """
    outcome = await _evaluate(session, people, data)
    if outcome.hard is not None:
        raise _hard_to_api_error(outcome.hard)

    now = clock.now()
    lesson = Lesson(
        id=uuid4(),
        group_id=data.group_id,
        instructor_id=data.instructor_id,
        classroom_id=data.classroom_id,
        format=data.format,
        status=LessonStatus.PLANNED,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        rescheduled_from_id=None,
        cancel_reason=None,
        created_at=now,
        updated_at=now,
    )
    session.add(lesson)
    await session.flush()
    return CreateResult(lesson=lesson, warnings=outcome.warnings)


async def _evaluate(
    session: AsyncSession,
    people: PeopleClient,
    data: LessonInput,
) -> RuleOutcome:
    """Общий пайплайн: группа → local BR-01…03 → stub → existing → evaluate_lesson_rules."""
    candidate = LessonCandidate(
        group_id=data.group_id,
        instructor_id=data.instructor_id,
        classroom_id=data.classroom_id,
        format=data.format,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
    )

    group_size = await _load_group_size(session, data.group_id)

    early = local_hard_failure(candidate, group_size)
    if early is not None:
        return RuleOutcome(hard=early)

    # Сюда доходим только если группа есть и BR-01/02 ок — stub нужен.
    people_ctx = await people.fetch_for_lesson(
        instructor_id=data.instructor_id,
        classroom_id=data.classroom_id,
        lesson_format=data.format,
    )
    existing = await _load_existing_lessons(session, candidate)

    return evaluate_lesson_rules(
        candidate,
        group_size=group_size,
        people=people_ctx,
        existing=existing,
    )


async def _load_group_size(session: AsyncSession, group_id: UUID) -> int | None:
    """Вернуть size группы или None, если записи нет."""
    group = await session.get(Group, group_id)
    if group is None:
        return None
    return group.size


async def _load_existing_lessons(
    session: AsyncSession, candidate: LessonCandidate
) -> list[ExistingLesson]:
    """Загрузить занятия по тем же ресурсам (instructor / group / classroom).

    Фильтр по статусу и пересечению интервалов — в домене (BR-05/06).
    """
    clauses = [
        Lesson.instructor_id == candidate.instructor_id,
        Lesson.group_id == candidate.group_id,
    ]
    if candidate.classroom_id is not None:
        clauses.append(Lesson.classroom_id == candidate.classroom_id)

    stmt = select(Lesson).where(or_(*clauses))
    if candidate.exclude_lesson_id is not None:
        stmt = stmt.where(Lesson.id != candidate.exclude_lesson_id)

    rows = (await session.scalars(stmt)).all()
    return [
        ExistingLesson(
            id=row.id,
            group_id=row.group_id,
            instructor_id=row.instructor_id,
            classroom_id=row.classroom_id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            status=row.status,
        )
        for row in rows
    ]


def _hard_to_api_error(hard: HardFailure) -> ApiError:
    """HardFailure → ApiError для create (и для validate при не-409)."""
    return ApiError(hard.status_code, hard.code, hard.message, hard.details)


def _to_validate_result(outcome: RuleOutcome) -> ValidateResult:
    """Смапить outcome в ValidationResult; 409 → violations, остальной hard → ApiError."""
    if outcome.hard is None:
        return ValidateResult(ok=True, violations=(), warnings=outcome.warnings)

    if outcome.hard.code in _VALIDATE_AS_VIOLATION:
        violation = {
            "code": outcome.hard.code,
            "message": outcome.hard.message,
            "details": outcome.hard.details,
        }
        return ValidateResult(ok=False, violations=(violation,), warnings=outcome.warnings)

    raise _hard_to_api_error(outcome.hard)
