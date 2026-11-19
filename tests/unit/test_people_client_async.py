"""Async-кейс people-stub клиента (NFR-07): параллельный и последовательный вызов.

Без сети и без реального stub: httpx.AsyncClient с транспортом, который спит LATENCY_S
на каждый запрос. При трёх вызовах подряд ожидание ~900 мс, через gather — ~300 мс.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.clients.people import PeopleClient
from app.errors import ApiError
from app.models import LessonFormat

LATENCY_S = 0.3
INSTRUCTOR_ID = UUID("1b000000-0000-4000-8000-000000000001")
CLASSROOM_ID = UUID("2c000000-0000-4000-8000-000000000001")

JsonDict = dict[str, Any]
RouteMap = dict[str, tuple[int, JsonDict]]

_INSTRUCTOR_JSON: JsonDict = {
    "id": str(INSTRUCTOR_ID),
    "full_name": "Анна Королёва",
    "max_hours_per_day": 6,
    "timezone": "Asia/Yekaterinburg",
}
_AVAILABILITY_JSON: JsonDict = {
    "instructor_id": str(INSTRUCTOR_ID),
    "timezone": "Asia/Yekaterinburg",
    "windows": [{"weekday": 1, "from": "09:00", "to": "18:00"}],
}
_CLASSROOM_JSON: JsonDict = {
    "id": str(CLASSROOM_ID),
    "name": "Ауд. 101",
    "capacity": 30,
    "online_capable": False,
}


class _DelayedTransport(httpx.AsyncBaseTransport):
    """Транспорт: искусственная задержка и счётчик одновременных запросов."""

    def __init__(self, latency_s: float, routes: RouteMap) -> None:
        self.latency_s = latency_s
        self.routes = routes
        self.in_flight = 0
        self.max_in_flight = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency_s)
            status, body = self.routes[request.url.path]
            return httpx.Response(status, json=body)
        finally:
            self.in_flight -= 1


def _ok_routes() -> RouteMap:
    return {
        f"/instructors/{INSTRUCTOR_ID}": (200, _INSTRUCTOR_JSON),
        f"/instructors/{INSTRUCTOR_ID}/availability": (200, _AVAILABILITY_JSON),
        f"/classrooms/{CLASSROOM_ID}": (200, _CLASSROOM_JSON),
    }


async def test_fetch_for_lesson_runs_three_calls_in_parallel() -> None:
    transport = _DelayedTransport(LATENCY_S, _ok_routes())
    async with httpx.AsyncClient(transport=transport, base_url="http://people-stub") as http:
        client = PeopleClient(http, timeout_ms=2000)
        started = time.perf_counter()
        ctx = await client.fetch_for_lesson(
            instructor_id=INSTRUCTOR_ID,
            classroom_id=CLASSROOM_ID,
            lesson_format=LessonFormat.IN_PERSON,
        )
        elapsed = time.perf_counter() - started

    assert ctx.instructor.full_name == "Анна Королёва"
    assert ctx.classroom is not None
    assert ctx.classroom.capacity == 30
    # Три задержки по 300 мс параллельно ≈ 300 мс; подряд было бы ≈ 900 мс.
    assert elapsed < 2 * LATENCY_S
    assert transport.max_in_flight == 3


async def test_sequential_calls_take_about_three_latencies() -> None:
    transport = _DelayedTransport(LATENCY_S, _ok_routes())
    async with httpx.AsyncClient(transport=transport, base_url="http://people-stub") as http:
        client = PeopleClient(http, timeout_ms=2000)
        started = time.perf_counter()
        await client.get_instructor(INSTRUCTOR_ID)
        await client.get_availability(INSTRUCTOR_ID)
        await client.get_classroom(CLASSROOM_ID)
        elapsed = time.perf_counter() - started

    assert elapsed >= 3 * LATENCY_S - 0.05
    assert transport.max_in_flight == 1


async def test_online_lesson_skips_classroom_call() -> None:
    transport = _DelayedTransport(LATENCY_S, _ok_routes())
    async with httpx.AsyncClient(transport=transport, base_url="http://people-stub") as http:
        client = PeopleClient(http, timeout_ms=2000)
        ctx = await client.fetch_for_lesson(
            instructor_id=INSTRUCTOR_ID,
            classroom_id=None,
            lesson_format=LessonFormat.ONLINE,
        )

    assert ctx.classroom is None
    assert transport.max_in_flight == 2


async def test_stub_404_maps_to_instructor_not_found() -> None:
    routes = {
        f"/instructors/{INSTRUCTOR_ID}": (404, {"error": {"code": "NOT_FOUND"}}),
    }
    transport = _DelayedTransport(0.0, routes)
    async with httpx.AsyncClient(transport=transport, base_url="http://people-stub") as http:
        client = PeopleClient(http, timeout_ms=2000)
        with pytest.raises(ApiError) as raised:
            await client.get_instructor(INSTRUCTOR_ID)

    assert raised.value.status_code == 422
    assert raised.value.code == "INSTRUCTOR_NOT_FOUND"


class _TimeoutTransport(httpx.AsyncBaseTransport):
    """Имитирует истечение STUB_TIMEOUT_MS (кастомный transport не режет sleep сам)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated stub timeout", request=request)


async def test_stub_timeout_maps_to_upstream_timeout() -> None:
    async with httpx.AsyncClient(
        transport=_TimeoutTransport(), base_url="http://people-stub"
    ) as http:
        client = PeopleClient(http, timeout_ms=50)
        with pytest.raises(ApiError) as raised:
            await client.get_instructor(INSTRUCTOR_ID)

    assert raised.value.status_code == 504
    assert raised.value.code == "UPSTREAM_TIMEOUT"
