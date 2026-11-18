"""Async-клиент people-stub (FR-12, NFR-07).

Три независимых вызова (профиль, окна доступности, аудитория) выполняются параллельно.
Предметные правила BR сюда не входят — только HTTP и маппинг отказов заглушки.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from app.errors import ApiError
from app.models import LessonFormat

_UPSTREAM = "people-stub"


@dataclass(frozen=True, slots=True)
class AvailabilityWindow:
    """Окно доступности по местному времени преподавателя."""

    weekday: int
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class InstructorProfile:
    """Профиль преподавателя из stub."""

    id: UUID
    full_name: str
    max_hours_per_day: float
    timezone: str


@dataclass(frozen=True, slots=True)
class InstructorAvailability:
    """Недельные окна доступности."""

    instructor_id: UUID
    timezone: str
    windows: tuple[AvailabilityWindow, ...]


@dataclass(frozen=True, slots=True)
class Classroom:
    """Аудитория из stub."""

    id: UUID
    name: str
    capacity: int
    online_capable: bool


@dataclass(frozen=True, slots=True)
class PeopleContext:
    """Данные stub, нужные для проверки одного занятия."""

    instructor: InstructorProfile
    availability: InstructorAvailability
    classroom: Classroom | None


class PeopleClient:
    """Клиент ``STUB_BASE_URL`` с таймаутом ``STUB_TIMEOUT_MS`` на каждый вызов."""

    def __init__(self, http: httpx.AsyncClient, timeout_ms: int) -> None:
        self._http = http
        self._timeout = httpx.Timeout(timeout_ms / 1000)
        self._timeout_ms = timeout_ms

    async def get_instructor(self, instructor_id: UUID) -> InstructorProfile:
        """``GET /instructors/{instructor_id}``."""
        payload = await self._get(
            f"/instructors/{instructor_id}",
            not_found_code="INSTRUCTOR_NOT_FOUND",
            not_found_message="Преподаватель не найден",
            not_found_details={"instructor_id": str(instructor_id)},
        )
        return InstructorProfile(
            id=UUID(str(payload["id"])),
            full_name=str(payload["full_name"]),
            max_hours_per_day=float(payload["max_hours_per_day"]),
            timezone=str(payload["timezone"]),
        )

    async def get_availability(self, instructor_id: UUID) -> InstructorAvailability:
        """``GET /instructors/{instructor_id}/availability``."""
        payload = await self._get(
            f"/instructors/{instructor_id}/availability",
            not_found_code="INSTRUCTOR_NOT_FOUND",
            not_found_message="Преподаватель не найден",
            not_found_details={"instructor_id": str(instructor_id)},
        )
        windows = tuple(
            AvailabilityWindow(
                weekday=int(item["weekday"]),
                start=str(item["from"]),
                end=str(item["to"]),
            )
            for item in payload["windows"]
        )
        return InstructorAvailability(
            instructor_id=UUID(str(payload["instructor_id"])),
            timezone=str(payload["timezone"]),
            windows=windows,
        )

    async def get_classroom(self, classroom_id: UUID) -> Classroom:
        """``GET /classrooms/{classroom_id}``."""
        payload = await self._get(
            f"/classrooms/{classroom_id}",
            not_found_code="CLASSROOM_NOT_FOUND",
            not_found_message="Аудитория не найдена",
            not_found_details={"classroom_id": str(classroom_id)},
        )
        return Classroom(
            id=UUID(str(payload["id"])),
            name=str(payload["name"]),
            capacity=int(payload["capacity"]),
            online_capable=bool(payload["online_capable"]),
        )

    async def fetch_for_lesson(
        self,
        *,
        instructor_id: UUID,
        classroom_id: UUID | None,
        lesson_format: LessonFormat | str,
    ) -> PeopleContext:
        """Параллельно запросить данные stub для одного занятия.

        Для ``ONLINE`` аудиторию не запрашиваем. Для ``IN_PERSON`` ``classroom_id`` обязателен
        на уровне вызова (проверка BR-02 — в WP-03).
        """
        fmt = LessonFormat(lesson_format) if isinstance(lesson_format, str) else lesson_format
        if fmt is LessonFormat.IN_PERSON:
            if classroom_id is None:
                raise ApiError(
                    422,
                    "CLASSROOM_REQUIRED",
                    "Для очного занятия нужна аудитория",
                    {},
                )
            instructor, availability, classroom = await asyncio.gather(
                self.get_instructor(instructor_id),
                self.get_availability(instructor_id),
                self.get_classroom(classroom_id),
            )
            return PeopleContext(
                instructor=instructor, availability=availability, classroom=classroom
            )

        instructor, availability = await asyncio.gather(
            self.get_instructor(instructor_id),
            self.get_availability(instructor_id),
        )
        return PeopleContext(instructor=instructor, availability=availability, classroom=None)

    async def _get(
        self,
        path: str,
        *,
        not_found_code: str,
        not_found_message: str,
        not_found_details: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._http.get(path, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise ApiError(
                504,
                "UPSTREAM_TIMEOUT",
                "Сервис людей и аудиторий не ответил вовремя",
                {"upstream": _UPSTREAM, "timeout_ms": self._timeout_ms},
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                502,
                "UPSTREAM_UNAVAILABLE",
                "Сервис людей и аудиторий недоступен",
                {"upstream": _UPSTREAM},
            ) from exc

        if response.status_code == 404:
            raise ApiError(422, not_found_code, not_found_message, not_found_details)
        if response.status_code >= 500:
            raise ApiError(
                502,
                "UPSTREAM_UNAVAILABLE",
                "Сервис людей и аудиторий недоступен",
                {"upstream": _UPSTREAM},
            )
        if response.status_code >= 400:
            raise ApiError(
                502,
                "UPSTREAM_UNAVAILABLE",
                "Сервис людей и аудиторий недоступен",
                {"upstream": _UPSTREAM, "status_code": response.status_code},
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ApiError(
                502,
                "UPSTREAM_UNAVAILABLE",
                "Сервис людей и аудиторий недоступен",
                {"upstream": _UPSTREAM},
            )
        return payload
