"""Чтение групп (FR-11)."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.context import get_context
from app.errors import not_found
from app.models import Group

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupResponse(BaseModel):
    """Ответ ``GET /groups/{group_id}`` (openapi ``Group``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    size: int


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(group_id: UUID, request: Request) -> GroupResponse:
    """Группа по идентификатору."""
    context = get_context(request)
    async with context.session_factory() as session:
        group = await session.get(Group, group_id)
    if group is None:
        raise not_found("Группа не найдена")
    return GroupResponse.model_validate(group)
