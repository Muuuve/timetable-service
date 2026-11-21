"""Контекст приложения: всё, что создаётся в lifespan и нужно обработчикам и задачам."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.broker import BrokerProbe, EventPublisher, NullEventPublisher
from app.clock import AdjustableClock
from app.jobs import JobRegistry
from app.settings import Settings


@dataclass
class AppContext:
    """Зависимости процесса. Хранится в ``app.state.context``; доступ через ``get_context``."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    clock: AdjustableClock
    broker_probe: BrokerProbe
    event_publisher: EventPublisher = field(default_factory=NullEventPublisher)
    jobs: JobRegistry = field(default_factory=JobRegistry)
    # POST /__test/reset: очистка данных и повторный seed. В каркасе не задан (ответ 501),
    # реализация появляется вместе с моделями и загрузчиком seed (src/app/seed.py).
    reset_state: Callable[[], Awaitable[None]] | None = None


def get_context(request: Request) -> AppContext:
    """Типизированный доступ к контексту из обработчика FastAPI."""
    context = request.app.state.context
    if not isinstance(context, AppContext):
        msg = "Контекст приложения не инициализирован: lifespan не отработал"
        raise RuntimeError(msg)
    return context
