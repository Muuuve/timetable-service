"""Модель учебной группы."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.lesson import Lesson


class Group(Base):
    """Группа слушателей: ``id``, ``name``, ``size`` (FR-11)."""

    __tablename__ = "groups"
    __table_args__ = (CheckConstraint("size >= 1", name="ck_groups_size_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)

    lessons: Mapped[list[Lesson]] = relationship(back_populates="group")
