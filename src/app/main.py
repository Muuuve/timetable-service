"""Точка входа приложения: сборка FastAPI, lifespan, подключение роутеров.

Это каркас. Он поднимает процесс с БД, часами, реестром фоновых задач и служебными эндпоинтами.
Предметные роутеры, модели, клиент заглушки и издатель событий добавляются автором сервиса;
место для их подключения помечено комментариями ``# ---> точка расширения``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import groups, health, lessons, testing
from app.broker import (
    BrokerProbe,
    EventPublisher,
    NullEventPublisher,
    RabbitMqEventBroker,
    UnconfiguredBrokerProbe,
)
from app.clock import AdjustableClock
from app.context import AppContext
from app.db import make_engine, make_session_factory, session_scope
from app.errors import install_error_handlers
from app.jobs import JobRegistry, run_periodically
from app.seed import reset_database
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)


def build_context(settings: Settings) -> AppContext:
    """Создать зависимости процесса (вне lifespan, чтобы тесты собирали контекст без сети)."""
    engine = make_engine(settings.database_url)
    broker_probe: BrokerProbe
    event_publisher: EventPublisher
    if settings.broker == "rabbitmq":
        rabbit = RabbitMqEventBroker(settings.broker_url)
        broker_probe = rabbit
        event_publisher = rabbit
    else:
        # Kafka — отдельный шаг; до подключения readiness честно говорит broker: fail.
        broker_probe = UnconfiguredBrokerProbe()
        event_publisher = NullEventPublisher()
    context = AppContext(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        clock=AdjustableClock(),
        broker_probe=broker_probe,
        event_publisher=event_publisher,
        jobs=JobRegistry(),
    )
    # ---> точка расширения: регистрация фоновых задач, например
    # context.jobs.register("<job-name>", <корутина, возвращающая int>)

    async def reset_state() -> None:
        async with session_scope(context.session_factory) as session:
            await reset_database(session)

    context.reset_state = reset_state
    return context


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл процесса: ресурсы создаются при старте и закрываются при остановке."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    context = build_context(settings)
    app.state.context = context

    rabbit = (
        context.event_publisher
        if isinstance(context.event_publisher, RabbitMqEventBroker)
        else None
    )
    if rabbit is not None:
        await rabbit.connect()

    stop = asyncio.Event()
    periodic: asyncio.Task[None] | None = None
    if settings.test_mode:
        log.info("TEST_MODE=1: /__test/* включены, периодические задачи отключены")
    else:
        periodic = asyncio.create_task(
            run_periodically(context.jobs, settings.jobs_interval_seconds, stop),
            name="periodic-jobs",
        )
    try:
        yield
    finally:
        stop.set()
        if periodic is not None:
            await periodic
        if rabbit is not None:
            await rabbit.aclose()
        await context.engine.dispose()


def create_app() -> FastAPI:
    """Собрать приложение."""
    app = FastAPI(
        title="WA service",
        version="0.1.0",
        lifespan=lifespan,
        # Нормативный контракт — openapi.yaml в корне репозитория; автогенерируемая схема
        # остаётся для удобства разработки.
    )
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(testing.router)
    app.include_router(groups.router)
    app.include_router(lessons.router)
    # ---> точка расширения: app.include_router(<предметный роутер>)
    return app


app = create_app()
