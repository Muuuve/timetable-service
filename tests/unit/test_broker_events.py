"""Юнит-тесты конверта событий и публикации в RabbitMQ (без сети)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import aio_pika
import pytest

from app.broker import RabbitMqEventBroker, build_envelope

LESSON_ID = UUID("3d000000-0000-4000-8000-000000000001")
OCCURRED = datetime(2026, 11, 30, 3, 0, 0, tzinfo=UTC)


def test_build_envelope_lesson_confirmed() -> None:
    payload = {"lesson_id": str(LESSON_ID)}
    event = build_envelope(
        event_type="LessonConfirmed",
        lesson_id=LESSON_ID,
        occurred_at=OCCURRED,
        payload=payload,
    )
    assert set(event) == {
        "event_id",
        "event_type",
        "occurred_at",
        "idempotency_key",
        "producer",
        "version",
        "payload",
    }
    UUID(event["event_id"])
    assert event["event_type"] == "LessonConfirmed"
    assert event["occurred_at"] == "2026-11-30T03:00:00Z"
    assert event["idempotency_key"] == f"lesson-confirmed:{LESSON_ID}"
    assert event["producer"] == "timetable-service"
    assert event["version"] == 1
    assert event["payload"] is payload


def test_build_envelope_idempotency_keys() -> None:
    cancelled = build_envelope(
        event_type="LessonCancelled",
        lesson_id=LESSON_ID,
        occurred_at=OCCURRED,
        payload={},
    )
    rescheduled = build_envelope(
        event_type="LessonRescheduled",
        lesson_id=LESSON_ID,
        occurred_at=OCCURRED,
        payload={},
    )
    assert cancelled["idempotency_key"] == f"lesson-cancelled:{LESSON_ID}"
    assert rescheduled["idempotency_key"] == f"lesson-rescheduled:{LESSON_ID}"


@pytest.mark.asyncio
async def test_rabbit_publish_uses_exchange_and_routing_key() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    exchange = AsyncMock()
    broker._exchange = exchange
    event = build_envelope(
        event_type="LessonConfirmed",
        lesson_id=LESSON_ID,
        occurred_at=OCCURRED,
        payload={"lesson_id": str(LESSON_ID)},
    )

    await broker.publish(event)

    exchange.publish.assert_awaited_once()
    message, kwargs = exchange.publish.await_args.args[0], exchange.publish.await_args.kwargs
    assert kwargs["routing_key"] == "timetable.LessonConfirmed"
    assert isinstance(message, aio_pika.Message)
    assert message.message_id == event["event_id"]
    assert message.content_type == "application/json"
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT


@pytest.mark.asyncio
async def test_rabbit_publish_requires_connect() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    with pytest.raises(RuntimeError, match="connect"):
        await broker.publish({"event_type": "LessonConfirmed", "event_id": "x"})


@pytest.mark.asyncio
async def test_rabbit_ping_false_until_connected() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    assert await broker.ping() is False


@pytest.mark.asyncio
async def test_rabbit_ping_true_when_passive_declare_ok() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    channel = AsyncMock()
    channel.declare_exchange = AsyncMock()
    channel.close = AsyncMock()
    connection = MagicMock()
    connection.is_closed = False
    connection.channel = AsyncMock(return_value=channel)
    broker._connection = connection

    assert await broker.ping() is True
    channel.declare_exchange.assert_awaited_once()
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rabbit_ping_false_when_broker_unreachable() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    connection = MagicMock()
    connection.is_closed = False
    connection.channel = AsyncMock(side_effect=ConnectionError("down"))
    broker._connection = connection

    assert await broker.ping() is False


@pytest.mark.asyncio
async def test_rabbit_ping_false_when_connection_closed() -> None:
    broker = RabbitMqEventBroker("amqp://test")
    connection = MagicMock()
    connection.is_closed = True
    broker._connection = connection

    assert await broker.ping() is False
