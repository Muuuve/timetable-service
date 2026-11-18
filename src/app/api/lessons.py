"""Чтение занятий (FR-04)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_serializer

from app.clock import to_iso_z
from app.context import get_context
from app.errors import not_found
from app.models import Lesson, LessonFormat, LessonStatus

router = APIRouter(prefix="/lessons", tags=["lessons"])


class LessonResponse(BaseModel):
    """Ответ ``GET /lessons/{lesson_id}`` (openapi ``Lesson``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None
    format: LessonFormat
    starts_at: datetime
    ends_at: datetime
    status: LessonStatus
    rescheduled_from_id: UUID | None
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("starts_at", "ends_at", "created_at", "updated_at")
    def _serialize_utc(self, value: datetime) -> str:
        return to_iso_z(value)


@router.get("/{lesson_id}", response_model=LessonResponse)
async def get_lesson(lesson_id: UUID, request: Request) -> LessonResponse:
    """Занятие по идентификатору."""
    context = get_context(request)
    async with context.session_factory() as session:
        lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise not_found("Занятие не найдено")
    return LessonResponse.model_validate(lesson)
