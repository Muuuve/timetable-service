"""HTTP-слой занятий: создание, validate, список и чтение (FR-01…FR-04)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer

from app.clients.people import PeopleClient
from app.clock import to_iso_z
from app.context import get_context
from app.db import session_scope
from app.domain.rules import WarningItem
from app.errors import not_found
from app.models import Lesson, LessonFormat, LessonStatus
from app.services.lessons import (
    LessonInput,
    create_lesson,
    list_lessons,
    validate_lesson,
)

router = APIRouter(prefix="/lessons", tags=["lessons"])


class LessonCreateBody(BaseModel):
    """Тело ``POST /lessons`` и ``POST /lessons/validate`` (openapi ``LessonCreate``)."""

    model_config = ConfigDict(extra="forbid")

    group_id: UUID
    instructor_id: UUID
    classroom_id: UUID | None = None
    format: LessonFormat
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class LessonResponse(BaseModel):
    """Ответ ``Lesson`` (openapi)."""

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


class WarningResponse(BaseModel):
    """Элемент ``warnings[]`` (openapi ``Warning``)."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LessonWithWarningsResponse(BaseModel):
    """Ответ ``POST /lessons`` (openapi ``LessonWithWarnings``)."""

    lesson: LessonResponse
    warnings: list[WarningResponse]


class ValidationResultResponse(BaseModel):
    """Ответ ``POST /lessons/validate`` (openapi ``ValidationResult``)."""

    ok: bool
    violations: list[dict[str, Any]]
    warnings: list[WarningResponse]


class LessonPageResponse(BaseModel):
    """Ответ ``GET /lessons`` (openapi ``LessonPage``)."""

    items: list[LessonResponse]
    total: int


def _to_warning(item: WarningItem) -> WarningResponse:
    return WarningResponse(code=item.code, message=item.message, details=dict(item.details))


def _to_input(body: LessonCreateBody) -> LessonInput:
    return LessonInput(
        group_id=body.group_id,
        instructor_id=body.instructor_id,
        classroom_id=body.classroom_id,
        format=body.format,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )


@router.post("", status_code=201, response_model=LessonWithWarningsResponse)
async def post_create_lesson(
    body: LessonCreateBody, request: Request
) -> LessonWithWarningsResponse:
    """Создать занятие (FR-01)."""
    context = get_context(request)
    data = _to_input(body)
    async with httpx.AsyncClient(base_url=context.settings.stub_base_url) as http:
        people = PeopleClient(http, context.settings.stub_timeout_ms)
        async with session_scope(context.session_factory) as session:
            result = await create_lesson(session, people, context.clock, data)
    return LessonWithWarningsResponse(
        lesson=LessonResponse.model_validate(result.lesson),
        warnings=[_to_warning(item) for item in result.warnings],
    )


@router.post("/validate", response_model=ValidationResultResponse)
async def post_validate_lesson(
    body: LessonCreateBody, request: Request
) -> ValidationResultResponse:
    """Проверить занятие без создания (FR-02)."""
    context = get_context(request)
    data = _to_input(body)
    async with httpx.AsyncClient(base_url=context.settings.stub_base_url) as http:
        people = PeopleClient(http, context.settings.stub_timeout_ms)
        async with session_scope(context.session_factory) as session:
            result = await validate_lesson(session, people, data)
    return ValidationResultResponse(
        ok=result.ok,
        violations=list(result.violations),
        warnings=[_to_warning(item) for item in result.warnings],
    )


@router.get("", response_model=LessonPageResponse)
async def get_lessons(
    request: Request,
    group_id: UUID | None = None,
    instructor_id: UUID | None = None,
    classroom_id: UUID | None = None,
    status: LessonStatus | None = None,
    starts_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    starts_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LessonPageResponse:
    """Список занятий с фильтрами (FR-03)."""
    context = get_context(request)
    async with context.session_factory() as session:
        page = await list_lessons(
            session,
            group_id=group_id,
            instructor_id=instructor_id,
            classroom_id=classroom_id,
            status=status,
            starts_from=starts_from,
            starts_to=starts_to,
            limit=limit,
            offset=offset,
        )
    return LessonPageResponse(
        items=[LessonResponse.model_validate(item) for item in page.items],
        total=page.total,
    )


@router.get("/{lesson_id}", response_model=LessonResponse)
async def get_lesson(lesson_id: UUID, request: Request) -> LessonResponse:
    """Занятие по идентификатору."""
    context = get_context(request)
    async with context.session_factory() as session:
        lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise not_found("Занятие не найдено")
    return LessonResponse.model_validate(lesson)
