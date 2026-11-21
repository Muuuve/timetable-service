"""Брокер событий: конверт платформы, публикация в RabbitMQ, пинг для readiness.

Контракт — ``asyncapi.yaml`` / FR-13. Публикация сразу после commit (at-least-once),
без outbox. Брокер — RabbitMQ (``BROKER=rabbitmq``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection

from app.clock import to_iso_z

log = logging.getLogger(__name__)

PRODUCER = "timetable-service"
ROUTING_PREFIX = "timetable"
EXCHANGE_NAME = "wa.events"

EventType = Literal["LessonConfirmed", "LessonCancelled", "LessonRescheduled"]

_IDEMPOTENCY_PREFIX: dict[EventType, str] = {
    "LessonConfirmed": "lesson-confirmed",
    "LessonCancelled": "lesson-cancelled",
    "LessonRescheduled": "lesson-rescheduled",
}


class BrokerProbe(Protocol):
    """Проверка доступности брокера для readiness."""

    async def ping(self) -> bool:
        """True, если брокер отвечает."""
        ...


class EventPublisher(Protocol):
    """Публикация JSON-конверта в брокер."""

    async def publish(self, event: dict[str, Any]) -> None:
        """Отправить уже собранный конверт."""
        ...


class UnconfiguredBrokerProbe:
    """Брокер ещё не подключён: readiness сообщает ``broker: fail``."""

    async def ping(self) -> bool:
        """Всегда False — до тех пор, пока не подключён реальный брокер."""
        return False


class NullEventPublisher:
    """Издатель-заглушка для unit-тестов без сети."""

    async def publish(self, event: dict[str, Any]) -> None:
        """Ничего не делает."""


def build_envelope(
    *,
    event_type: EventType,
    lesson_id: UUID,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Собрать конверт платформы для события занятия."""
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "occurred_at": to_iso_z(occurred_at),
        "idempotency_key": f"{_IDEMPOTENCY_PREFIX[event_type]}:{lesson_id}",
        "producer": PRODUCER,
        "version": 1,
        "payload": payload,
    }


class RabbitMqEventBroker:
    """RabbitMQ: topic exchange ``wa.events``, routing key ``timetable.<EventType>``."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        """Открыть соединение и объявить durable topic exchange."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        log.info("RabbitMQ connected: exchange=%s", EXCHANGE_NAME)

    async def ping(self) -> bool:
        """True, если брокер отвечает (passive declare exchange), не только локальный флаг."""
        if self._connection is None or self._connection.is_closed:
            return False
        try:
            channel = await self._connection.channel()
            try:
                await channel.declare_exchange(
                    EXCHANGE_NAME,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                    passive=True,
                )
            finally:
                await channel.close()
        except Exception:
            return False
        return True

    async def publish(self, event: dict[str, Any]) -> None:
        """Опубликовать конверт: JSON, ``message_id=event_id``, persistent."""
        if self._exchange is None:
            msg = "RabbitMqEventBroker не подключён: вызовите connect()"
            raise RuntimeError(msg)
        event_type = event["event_type"]
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            message_id=str(event["event_id"]),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self._exchange.publish(message, routing_key=f"{ROUTING_PREFIX}.{event_type}")

    async def aclose(self) -> None:
        """Закрыть соединение."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None
