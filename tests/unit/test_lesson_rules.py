"""Unit-тесты доменных правил BR-01…BR-07 (без HTTP и БД)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.clients.people import (
    AvailabilityWindow,
    Classroom,
    InstructorAvailability,
    InstructorProfile,
    PeopleContext,
)
from app.domain.rules import (
    ExistingLesson,
    LessonCandidate,
    evaluate_lesson_rules,
    intervals_overlap,
)
from app.models import LessonFormat, LessonStatus

G1 = UUID("1a000000-0000-4000-8000-000000000001")
G2 = UUID("1a000000-0000-4000-8000-000000000002")
I1 = UUID("1b000000-0000-4000-8000-000000000001")
I2 = UUID("1b000000-0000-4000-8000-000000000002")
C1 = UUID("1c000000-0000-4000-8000-000000000001")
C2 = UUID("1c000000-0000-4000-8000-000000000002")
L1 = UUID("3d000000-0000-4000-8000-000000000001")
L2 = UUID("3d000000-0000-4000-8000-000000000002")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _people(
    *,
    max_hours: float = 6.0,
    capacity: int = 30,
    classroom: bool = True,
    windows: tuple[AvailabilityWindow, ...] | None = None,
) -> PeopleContext:
    tz = "Asia/Yekaterinburg"
    if windows is None:
        windows = tuple(
            AvailabilityWindow(weekday=d, start="09:00", end="18:00") for d in range(1, 6)
        )
    return PeopleContext(
        instructor=InstructorProfile(
            id=I1, full_name="I1", max_hours_per_day=max_hours, timezone=tz
        ),
        availability=InstructorAvailability(instructor_id=I1, timezone=tz, windows=windows),
        classroom=Classroom(id=C1, name="C1", capacity=capacity, online_capable=True)
        if classroom
        else None,
    )


def _candidate(
    *,
    starts: str = "2026-12-02T07:00:00Z",
    ends: str = "2026-12-02T08:30:00Z",
    fmt: LessonFormat = LessonFormat.IN_PERSON,
    classroom_id: UUID | None = C1,
    group_id: UUID = G1,
    instructor_id: UUID = I1,
    exclude: UUID | None = None,
) -> LessonCandidate:
    return LessonCandidate(
        group_id=group_id,
        instructor_id=instructor_id,
        classroom_id=classroom_id,
        format=fmt,
        starts_at=_dt(starts),
        ends_at=_dt(ends),
        exclude_lesson_id=exclude,
    )


def _existing(
    lesson_id: UUID = L1,
    *,
    starts: str = "2026-12-01T05:00:00Z",
    ends: str = "2026-12-01T06:30:00Z",
    status: LessonStatus = LessonStatus.PLANNED,
    group_id: UUID = G1,
    instructor_id: UUID = I1,
    classroom_id: UUID | None = C1,
) -> ExistingLesson:
    return ExistingLesson(
        id=lesson_id,
        group_id=group_id,
        instructor_id=instructor_id,
        classroom_id=classroom_id,
        starts_at=_dt(starts),
        ends_at=_dt(ends),
        status=status,
    )


def test_intervals_touching_do_not_overlap() -> None:
    a0, a1 = _dt("2026-12-01T05:00:00Z"), _dt("2026-12-01T06:00:00Z")
    b0, b1 = _dt("2026-12-01T06:00:00Z"), _dt("2026-12-01T07:00:00Z")
    assert intervals_overlap(a0, a1, b0, b1) is False
    assert intervals_overlap(a0, a1, _dt("2026-12-01T05:30:00Z"), b1) is True


def test_br01_duration_too_short() -> None:
    result = evaluate_lesson_rules(
        _candidate(starts="2026-12-02T07:00:00Z", ends="2026-12-02T07:20:00Z"),
        group_size=10,
        people=_people(),
        existing=[],
    )
    assert result.hard is not None
    assert result.hard.code == "VALIDATION_ERROR"


def test_br02_in_person_requires_classroom() -> None:
    result = evaluate_lesson_rules(
        _candidate(classroom_id=None),
        group_size=10,
        people=_people(classroom=False),
        existing=[],
    )
    assert result.hard is not None
    assert result.hard.code == "CLASSROOM_REQUIRED"


def test_br02_online_rejects_classroom() -> None:
    result = evaluate_lesson_rules(
        _candidate(fmt=LessonFormat.ONLINE, classroom_id=C1),
        group_size=10,
        people=_people(classroom=False),
        existing=[],
    )
    assert result.hard is not None
    assert result.hard.code == "VALIDATION_ERROR"


def test_br03_group_not_found() -> None:
    result = evaluate_lesson_rules(
        _candidate(),
        group_size=None,
        people=None,
        existing=[],
    )
    assert result.hard is not None
    assert result.hard.code == "GROUP_NOT_FOUND"


def test_br04_classroom_too_small() -> None:
    result = evaluate_lesson_rules(
        _candidate(),
        group_size=40,
        people=_people(capacity=20),
        existing=[],
    )
    assert result.hard is not None
    assert result.hard.code == "CLASSROOM_TOO_SMALL"
    assert result.hard.details == {"capacity": 20, "group_size": 40}


def test_br05_instructor_conflict() -> None:
    result = evaluate_lesson_rules(
        _candidate(
            starts="2026-12-01T06:00:00Z",
            ends="2026-12-01T07:00:00Z",
            fmt=LessonFormat.ONLINE,
            classroom_id=None,
            group_id=G2,
        ),
        group_size=10,
        people=_people(classroom=False),
        existing=[_existing(ends="2026-12-01T06:30:00Z")],
    )
    assert result.hard is not None
    assert result.hard.code == "SCHEDULE_CONFLICT"
    assert result.hard.details["conflicts"] == [
        {
            "resource": "instructor",
            "lesson_id": str(L1),
            "overlap": {"starts_at": "2026-12-01T06:00:00Z", "ends_at": "2026-12-01T06:30:00Z"},
        }
    ]


def test_br05_ignores_cancelled_and_touching() -> None:
    result = evaluate_lesson_rules(
        _candidate(starts="2026-12-02T07:00:00Z", ends="2026-12-02T08:30:00Z"),
        group_size=10,
        people=_people(),
        existing=[
            _existing(
                L1,
                starts="2026-12-02T05:00:00Z",
                ends="2026-12-02T07:00:00Z",
                status=LessonStatus.CANCELLED,
            ),
            _existing(
                L2,
                starts="2026-12-02T05:00:00Z",
                ends="2026-12-02T07:00:00Z",
                status=LessonStatus.RESCHEDULED,
                instructor_id=I2,
                classroom_id=C2,
                group_id=G2,
            ),
        ],
    )
    assert result.hard is None
    assert result.warnings == ()


def test_br06_workload_soft_limit() -> None:
    # Среда 2026-12-02: 4ч уже + 2ч новое > 5ч нормы.
    result = evaluate_lesson_rules(
        _candidate(
            starts="2026-12-02T08:00:00Z",
            ends="2026-12-02T10:00:00Z",
            fmt=LessonFormat.ONLINE,
            classroom_id=None,
            group_id=G2,
        ),
        group_size=10,
        people=_people(max_hours=5.0, classroom=False),
        existing=[
            _existing(
                starts="2026-12-02T04:00:00Z",
                ends="2026-12-02T08:00:00Z",
                classroom_id=None,
            )
        ],
    )
    assert result.hard is None
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "WORKLOAD_SOFT_LIMIT"
    assert result.warnings[0].details["date"] == "2026-12-02"
    assert result.warnings[0].details["max_hours"] == 5.0
    assert result.warnings[0].details["planned_hours"] == 6.0


def test_br07_outside_availability_window() -> None:
    # 19:00–20:00 YEKT (=14:00–15:00Z) — вне окна 09–18.
    result = evaluate_lesson_rules(
        _candidate(
            starts="2026-12-02T14:00:00Z",
            ends="2026-12-02T15:00:00Z",
            fmt=LessonFormat.ONLINE,
            classroom_id=None,
        ),
        group_size=10,
        people=_people(classroom=False),
        existing=[],
    )
    assert result.hard is None
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "INSTRUCTOR_UNAVAILABLE"
    assert result.warnings[0].details["instructor_id"] == str(I1)


def test_ok_in_person_without_warnings() -> None:
    result = evaluate_lesson_rules(
        _candidate(),
        group_size=10,
        people=_people(),
        existing=[],
    )
    assert result.hard is None
    assert result.warnings == ()
