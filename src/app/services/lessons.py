"""Операции с занятиями: create/validate (FR-01/02), список (FR-03), PATCH (FR-05),
confirm/cancel/complete/reschedule (FR-06…FR-09).

Create/validate/update/reschedule: группа → local BR-01…03 → PeopleClient → existing → домен.
Список: фильтры в SQL, сортировка и пагинация без stub.
Lifecycle: смена статуса в сессии; конверт события — вызывающему для publish после commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker import build_envelope
from app.clients.people import PeopleClient
from app.clock import Clock, to_iso_z
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
    """Вход create/validate/update (поля занятия без HTTP)."""

    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    starts_at: datetime
    ends_at: datetime
    exclude_lesson_id: UUID | None = None


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


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Успешный переход статуса; ``event`` публикуется после commit (или None)."""

    lesson: Lesson
    event: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class RescheduleResult:
    """Успешный перенос: новое занятие, soft-warnings и событие после commit."""

    lesson: Lesson
    warnings: tuple[WarningItem, ...]
    event: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LessonPage:
    """Страница списка занятий (openapi LessonPage)."""

    items: tuple[Lesson, ...]
    total: int


async def list_lessons(
    session: AsyncSession,
    *,
    group_id: UUID | None = None,
    instructor_id: UUID | None = None,
    classroom_id: UUID | None = None,
    status: LessonStatus | None = None,
    starts_from: datetime | None = None,
    starts_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LessonPage:
    """Список с фильтрами (И), сортировкой и пагинацией (FR-03)."""
    filters: list[Any] = []
    if group_id is not None:
        filters.append(Lesson.group_id == group_id)
    if instructor_id is not None:
        filters.append(Lesson.instructor_id == instructor_id)
    if classroom_id is not None:
        filters.append(Lesson.classroom_id == classroom_id)
    if status is not None:
        filters.append(Lesson.status == status)
    if starts_from is not None:
        filters.append(Lesson.starts_at >= starts_from)
    if starts_to is not None:
        filters.append(Lesson.starts_at < starts_to)

    count_stmt = select(func.count()).select_from(Lesson)
    list_stmt = select(Lesson)
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    total = int(await session.scalar(count_stmt) or 0)
    rows = (
        await session.scalars(
            list_stmt.order_by(Lesson.starts_at.asc(), Lesson.id.asc()).offset(offset).limit(limit)
        )
    ).all()
    return LessonPage(items=tuple(rows), total=total)


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


async def update_lesson(
    session: AsyncSession,
    people: PeopleClient,
    clock: Clock,
    lesson_id: UUID,
    patch: dict[str, Any],
) -> CreateResult:
    """Частично изменить занятие в PLANNED (FR-05).

    ``patch`` — только переданные поля (exclude_unset). Hard → ApiError без UPDATE.
    """
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise ApiError(404, "NOT_FOUND", "Занятие не найдено", {})
    if lesson.status is not LessonStatus.PLANNED:
        raise ApiError(
            409,
            "LESSON_NOT_EDITABLE",
            "Изменять можно только занятие в статусе PLANNED",
            {"current_status": lesson.status.value},
        )

    data = LessonInput(
        group_id=lesson.group_id,
        instructor_id=patch.get("instructor_id", lesson.instructor_id),
        classroom_id=patch.get("classroom_id", lesson.classroom_id),
        format=patch.get("format", lesson.format),
        starts_at=patch.get("starts_at", lesson.starts_at),
        ends_at=patch.get("ends_at", lesson.ends_at),
        exclude_lesson_id=lesson.id,
    )
    outcome = await _evaluate(session, people, data)
    if outcome.hard is not None:
        raise _hard_to_api_error(outcome.hard)

    lesson.instructor_id = data.instructor_id
    lesson.classroom_id = data.classroom_id
    lesson.format = data.format
    lesson.starts_at = data.starts_at
    lesson.ends_at = data.ends_at
    lesson.updated_at = clock.now()
    await session.flush()
    return CreateResult(lesson=lesson, warnings=outcome.warnings)


async def confirm_lesson(
    session: AsyncSession,
    clock: Clock,
    lesson_id: UUID,
) -> TransitionResult:
    """PLANNED → CONFIRMED (FR-06); конверт ``LessonConfirmed`` для publish после commit."""
    lesson = await _get_lesson(session, lesson_id)
    _assert_transition(lesson.status, {LessonStatus.PLANNED}, LessonStatus.CONFIRMED)

    now = clock.now()
    lesson.status = LessonStatus.CONFIRMED
    lesson.updated_at = now
    await session.flush()

    event = build_envelope(
        event_type="LessonConfirmed",
        lesson_id=lesson.id,
        occurred_at=now,
        payload=_confirmed_payload(lesson),
    )
    return TransitionResult(lesson=lesson, event=event)


async def cancel_lesson(
    session: AsyncSession,
    clock: Clock,
    lesson_id: UUID,
    reason: str | None = None,
) -> TransitionResult:
    """PLANNED|CONFIRMED → CANCELLED (FR-07); конверт ``LessonCancelled`` после commit."""
    lesson = await _get_lesson(session, lesson_id)
    allowed = {LessonStatus.PLANNED, LessonStatus.CONFIRMED}
    _assert_transition(lesson.status, allowed, LessonStatus.CANCELLED)

    previous_status = lesson.status
    now = clock.now()
    lesson.status = LessonStatus.CANCELLED
    lesson.cancel_reason = reason
    lesson.updated_at = now
    await session.flush()

    event = build_envelope(
        event_type="LessonCancelled",
        lesson_id=lesson.id,
        occurred_at=now,
        payload=_cancelled_payload(lesson, previous_status=previous_status, reason=reason),
    )
    return TransitionResult(lesson=lesson, event=event)


async def complete_lesson(
    session: AsyncSession,
    clock: Clock,
    lesson_id: UUID,
) -> TransitionResult:
    """IN_PROGRESS → COMPLETED (FR-09); переход раньше BR-08; события нет."""
    lesson = await _get_lesson(session, lesson_id)
    _assert_transition(lesson.status, {LessonStatus.IN_PROGRESS}, LessonStatus.COMPLETED)

    now = clock.now()
    if now < lesson.ends_at:
        raise ApiError(
            409,
            "LESSON_NOT_FINISHED",
            "Занятие ещё не закончилось",
            {"ends_at": to_iso_z(lesson.ends_at), "now": to_iso_z(now)},
        )

    lesson.status = LessonStatus.COMPLETED
    lesson.updated_at = now
    await session.flush()
    return TransitionResult(lesson=lesson, event=None)


async def reschedule_lesson(
    session: AsyncSession,
    people: PeopleClient,
    clock: Clock,
    lesson_id: UUID,
    fields: dict[str, Any],
) -> RescheduleResult:
    """Атомарный перенос (FR-08): исходное → RESCHEDULED, новое PLANNED + BR-01…BR-07.

    ``fields`` — тело RescheduleRequest (exclude_unset): обязательны starts_at/ends_at;
    instructor_id / classroom_id / format берутся из исходного, если не переданы.
    """
    source = await _get_lesson(session, lesson_id)
    allowed = {LessonStatus.PLANNED, LessonStatus.CONFIRMED}
    _assert_transition(source.status, allowed, LessonStatus.RESCHEDULED)

    if await _has_reschedule_child(session, source.id):
        raise ApiError(
            409,
            "TRANSITION_FORBIDDEN",
            "Недопустимый переход",
            {
                "current_status": source.status.value,
                "target_status": LessonStatus.RESCHEDULED.value,
            },
        )

    data = LessonInput(
        group_id=source.group_id,
        instructor_id=fields.get("instructor_id", source.instructor_id),
        classroom_id=fields.get("classroom_id", source.classroom_id),
        format=fields.get("format", source.format),
        starts_at=fields["starts_at"],
        ends_at=fields["ends_at"],
        exclude_lesson_id=source.id,
    )
    outcome = await _evaluate(session, people, data)
    if outcome.hard is not None:
        raise _hard_to_api_error(outcome.hard)

    now = clock.now()
    status_before = source.status.value
    source.status = LessonStatus.RESCHEDULED
    source.updated_at = now

    new_lesson = Lesson(
        id=uuid4(),
        group_id=data.group_id,
        instructor_id=data.instructor_id,
        classroom_id=data.classroom_id,
        format=data.format,
        status=LessonStatus.PLANNED,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        rescheduled_from_id=source.id,
        cancel_reason=None,
        created_at=now,
        updated_at=now,
    )
    session.add(new_lesson)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409,
            "TRANSITION_FORBIDDEN",
            "Недопустимый переход",
            {
                "current_status": status_before,
                "target_status": LessonStatus.RESCHEDULED.value,
            },
        ) from exc

    event = build_envelope(
        event_type="LessonRescheduled",
        lesson_id=source.id,
        occurred_at=now,
        payload=_rescheduled_payload(source, new_lesson),
    )
    return RescheduleResult(lesson=new_lesson, warnings=outcome.warnings, event=event)


async def _get_lesson(session: AsyncSession, lesson_id: UUID) -> Lesson:
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise ApiError(404, "NOT_FOUND", "Занятие не найдено", {})
    return lesson


def _assert_transition(
    current: LessonStatus,
    allowed: set[LessonStatus],
    target: LessonStatus,
) -> None:
    if current not in allowed:
        raise ApiError(
            409,
            "TRANSITION_FORBIDDEN",
            "Недопустимый переход",
            {"current_status": current.value, "target_status": target.value},
        )


def _confirmed_payload(lesson: Lesson) -> dict[str, Any]:
    return {
        "lesson_id": str(lesson.id),
        "group_id": str(lesson.group_id),
        "instructor_id": str(lesson.instructor_id),
        "classroom_id": str(lesson.classroom_id) if lesson.classroom_id is not None else None,
        "format": lesson.format.value,
        "starts_at": to_iso_z(lesson.starts_at),
        "ends_at": to_iso_z(lesson.ends_at),
    }


def _cancelled_payload(
    lesson: Lesson,
    *,
    previous_status: LessonStatus,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "lesson_id": str(lesson.id),
        "group_id": str(lesson.group_id),
        "instructor_id": str(lesson.instructor_id),
        "starts_at": to_iso_z(lesson.starts_at),
        "ends_at": to_iso_z(lesson.ends_at),
        "previous_status": previous_status.value,
        "reason": reason,
    }


def _interval_payload(lesson: Lesson) -> dict[str, Any]:
    return {
        "starts_at": to_iso_z(lesson.starts_at),
        "ends_at": to_iso_z(lesson.ends_at),
        "instructor_id": str(lesson.instructor_id),
        "classroom_id": str(lesson.classroom_id) if lesson.classroom_id is not None else None,
    }


def _rescheduled_payload(source: Lesson, new_lesson: Lesson) -> dict[str, Any]:
    return {
        "lesson_id": str(source.id),
        "rescheduled_to_id": str(new_lesson.id),
        "group_id": str(source.group_id),
        "instructor_id": str(source.instructor_id),
        "previous": _interval_payload(source),
        "new": _interval_payload(new_lesson),
    }


async def _has_reschedule_child(session: AsyncSession, lesson_id: UUID) -> bool:
    """True, если у занятия уже есть перенос (rescheduled_from_id = lesson_id)."""
    found = await session.scalar(
        select(Lesson.id).where(Lesson.rescheduled_from_id == lesson_id).limit(1)
    )
    return found is not None


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
        exclude_lesson_id=data.exclude_lesson_id,
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
