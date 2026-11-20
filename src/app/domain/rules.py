"""Правила BR-01…BR-07 для создания/валидации занятия.

Чистые функции: без FastAPI, SQLAlchemy и HTTP. Порядок hard-ошибок — по таблице BR
(первое найденное побеждает). Soft BR-06/BR-07 только добавляют warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.clients.people import InstructorAvailability, PeopleContext
from app.clock import to_iso_z
from app.models import LessonFormat, LessonStatus

ACTIVE_STATUSES = frozenset(
    {
        LessonStatus.PLANNED,
        LessonStatus.CONFIRMED,
        LessonStatus.IN_PROGRESS,
        LessonStatus.COMPLETED,
    }
)

MIN_DURATION = timedelta(minutes=30)
MAX_DURATION = timedelta(hours=8)

ConflictResource = Literal["instructor", "classroom", "group"]


@dataclass(frozen=True, slots=True)
class LessonCandidate:
    """Занятие, которое проверяем (ещё не обязательно в БД)."""

    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    starts_at: datetime
    ends_at: datetime
    exclude_lesson_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ExistingLesson:
    """Уже сохранённое занятие для проверки пересечений и нагрузки."""

    id: UUID
    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    starts_at: datetime
    ends_at: datetime
    status: LessonStatus


@dataclass(frozen=True, slots=True)
class HardFailure:
    """Жёсткое нарушение: для create — HTTP-ошибка; для validate при 409 — violation."""

    status_code: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WarningItem:
    """Мягкое предупреждение BR-06 / BR-07."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """Результат проверки: не больше одной hard-ошибки + список soft."""

    hard: HardFailure | None = None
    warnings: tuple[WarningItem, ...] = ()


def intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """Пересечение полуинтервалов; касание границ (a_end == b_start) — не конфликт."""
    return a_start < b_end and b_start < a_end


def overlap_bounds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> tuple[datetime, datetime]:
    """Границы пересечения двух интервалов (вызывающий уже знает, что они пересекаются)."""
    return max(a_start, b_start), min(a_end, b_end)


def evaluate_lesson_rules(
    candidate: LessonCandidate,
    *,
    group_size: int | None,
    people: PeopleContext | None,
    existing: list[ExistingLesson],
) -> RuleOutcome:
    """Проверить BR-01…BR-07 по порядку. ``group_size is None`` — группа не найдена (BR-03).

    ``people`` нужен с BR-04 (после успешного fetch stub). Вызывающий обязан сначала
    отловить 404/5xx/таймаут клиента как ``ApiError``.
    """
    hard = _br01_duration(candidate)
    if hard is not None:
        return RuleOutcome(hard=hard)

    hard = _br02_classroom_format(candidate)
    if hard is not None:
        return RuleOutcome(hard=hard)

    hard = _br03_group_exists(group_size)
    if hard is not None:
        return RuleOutcome(hard=hard)

    assert group_size is not None
    assert people is not None

    hard = _br04_classroom_capacity(candidate, group_size, people)
    if hard is not None:
        return RuleOutcome(hard=hard)

    hard = _br05_schedule_conflicts(candidate, existing)
    if hard is not None:
        return RuleOutcome(hard=hard)

    warnings: list[WarningItem] = []
    soft = _br06_workload(candidate, people, existing)
    if soft is not None:
        warnings.append(soft)
    soft = _br07_availability(candidate, people.availability)
    if soft is not None:
        warnings.append(soft)
    return RuleOutcome(warnings=tuple(warnings))


def _br01_duration(candidate: LessonCandidate) -> HardFailure | None:
    if candidate.starts_at.tzinfo is None or candidate.ends_at.tzinfo is None:
        return HardFailure(
            422,
            "VALIDATION_ERROR",
            "Невалидные данные",
            {"fields": {"starts_at": "требуется смещение часового пояса"}},
        )
    if candidate.ends_at <= candidate.starts_at:
        return HardFailure(
            422,
            "VALIDATION_ERROR",
            "Невалидные данные",
            {"fields": {"ends_at": "должно быть позже starts_at"}},
        )
    duration = candidate.ends_at - candidate.starts_at
    if duration < MIN_DURATION or duration > MAX_DURATION:
        return HardFailure(
            422,
            "VALIDATION_ERROR",
            "Невалидные данные",
            {"fields": {"ends_at": "длительность должна быть от 30 минут до 8 часов"}},
        )
    return None


def _br02_classroom_format(candidate: LessonCandidate) -> HardFailure | None:
    if candidate.format is LessonFormat.IN_PERSON and candidate.classroom_id is None:
        return HardFailure(
            422,
            "CLASSROOM_REQUIRED",
            "Для очного занятия нужна аудитория",
            {},
        )
    if candidate.format is LessonFormat.ONLINE and candidate.classroom_id is not None:
        return HardFailure(
            422,
            "VALIDATION_ERROR",
            "Невалидные данные",
            {"fields": {"classroom_id": "для ONLINE должно быть null"}},
        )
    return None


def _br03_group_exists(group_size: int | None) -> HardFailure | None:
    if group_size is None:
        return HardFailure(422, "GROUP_NOT_FOUND", "Группа не найдена", {})
    return None


def _br04_classroom_capacity(
    candidate: LessonCandidate, group_size: int, people: PeopleContext
) -> HardFailure | None:
    if candidate.format is not LessonFormat.IN_PERSON:
        return None
    assert people.classroom is not None
    if people.classroom.capacity < group_size:
        return HardFailure(
            409,
            "CLASSROOM_TOO_SMALL",
            "Аудитория мала для группы",
            {"capacity": people.classroom.capacity, "group_size": group_size},
        )
    return None


def _br05_schedule_conflicts(
    candidate: LessonCandidate, existing: list[ExistingLesson]
) -> HardFailure | None:
    conflicts: list[dict[str, Any]] = []
    for lesson in existing:
        if lesson.status not in ACTIVE_STATUSES:
            continue
        if candidate.exclude_lesson_id is not None and lesson.id == candidate.exclude_lesson_id:
            continue
        if not intervals_overlap(
            candidate.starts_at, candidate.ends_at, lesson.starts_at, lesson.ends_at
        ):
            continue

        ov_start, ov_end = overlap_bounds(
            candidate.starts_at, candidate.ends_at, lesson.starts_at, lesson.ends_at
        )
        overlap = {"starts_at": to_iso_z(ov_start), "ends_at": to_iso_z(ov_end)}

        if lesson.instructor_id == candidate.instructor_id:
            conflicts.append(
                {"resource": "instructor", "lesson_id": str(lesson.id), "overlap": overlap}
            )
        if (
            candidate.classroom_id is not None
            and lesson.classroom_id is not None
            and lesson.classroom_id == candidate.classroom_id
        ):
            conflicts.append(
                {"resource": "classroom", "lesson_id": str(lesson.id), "overlap": overlap}
            )
        if lesson.group_id == candidate.group_id:
            conflicts.append({"resource": "group", "lesson_id": str(lesson.id), "overlap": overlap})

    if not conflicts:
        return None
    return HardFailure(
        409,
        "SCHEDULE_CONFLICT",
        "Обнаружен конфликт расписания",
        {"conflicts": conflicts},
    )


def _br06_workload(
    candidate: LessonCandidate,
    people: PeopleContext,
    existing: list[ExistingLesson],
) -> WarningItem | None:
    tz = ZoneInfo(people.instructor.timezone)
    local_start = candidate.starts_at.astimezone(tz)
    day = local_start.date()
    day_start = datetime(day.year, day.month, day.day, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    total = candidate.ends_at - candidate.starts_at
    for lesson in existing:
        if lesson.status not in ACTIVE_STATUSES:
            continue
        if lesson.instructor_id != candidate.instructor_id:
            continue
        if candidate.exclude_lesson_id is not None and lesson.id == candidate.exclude_lesson_id:
            continue
        if not intervals_overlap(lesson.starts_at, lesson.ends_at, day_start, day_end):
            continue
        ov_start, ov_end = overlap_bounds(lesson.starts_at, lesson.ends_at, day_start, day_end)
        total += ov_end - ov_start

    planned_hours = total.total_seconds() / 3600
    max_hours = float(people.instructor.max_hours_per_day)
    if planned_hours <= max_hours:
        return None
    return WarningItem(
        code="WORKLOAD_SOFT_LIMIT",
        message="Превышена дневная норма преподавателя",
        details={
            "date": day.isoformat(),
            "max_hours": max_hours,
            "planned_hours": planned_hours,
        },
    )


def _br07_availability(
    candidate: LessonCandidate, availability: InstructorAvailability
) -> WarningItem | None:
    tz = ZoneInfo(availability.timezone)
    local_start = candidate.starts_at.astimezone(tz)
    local_end = candidate.ends_at.astimezone(tz)
    if local_start.date() != local_end.date():
        return WarningItem(
            code="INSTRUCTOR_UNAVAILABLE",
            message="Занятие вне окон доступности преподавателя",
            details={"instructor_id": str(candidate.instructor_id)},
        )

    weekday = local_start.isoweekday()
    start_hm = local_start.strftime("%H:%M")
    end_hm = local_end.strftime("%H:%M")
    for window in availability.windows:
        if window.weekday != weekday:
            continue
        if window.start <= start_hm and end_hm <= window.end:
            return None
    return WarningItem(
        code="INSTRUCTOR_UNAVAILABLE",
        message="Занятие вне окон доступности преподавателя",
        details={"instructor_id": str(candidate.instructor_id)},
    )
