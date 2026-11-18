"""Модель занятия и связанные перечисления."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.group import Group


class LessonFormat(enum.StrEnum):
    """Формат занятия (openapi ``LessonFormat``)."""

    IN_PERSON = "IN_PERSON"
    ONLINE = "ONLINE"


class LessonStatus(enum.StrEnum):
    """Статус занятия в жизненном цикле (openapi ``LessonStatus``)."""

    PLANNED = "PLANNED"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"


class Lesson(Base):
    """Занятие: интервал, ресурсы, статус и связь с переносом."""

    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_lessons_ends_after_starts"),
        CheckConstraint(
            "ends_at - starts_at >= INTERVAL '30 minutes'"
            " AND ends_at - starts_at <= INTERVAL '8 hours'",
            name="ck_lessons_duration",
        ),
        Index("ix_lessons_group_id", "group_id"),
        Index("ix_lessons_instructor_id", "instructor_id"),
        Index("ix_lessons_classroom_id", "classroom_id"),
        Index("ix_lessons_status", "status"),
        Index("ix_lessons_starts_at", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("groups.id"), nullable=False)
    instructor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    classroom_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    format: Mapped[LessonFormat] = mapped_column(
        Enum(
            LessonFormat,
            name="lesson_format",
            native_enum=False,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    status: Mapped[LessonStatus] = mapped_column(
        Enum(
            LessonStatus,
            name="lesson_status",
            native_enum=False,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("lessons.id"),
        unique=True,
        nullable=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    group: Mapped[Group] = relationship(back_populates="lessons")
