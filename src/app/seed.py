"""Seed: детерминированные фикстуры из ``seed/*.json`` (TASK.md, §3.5).

Каркас умеет читать файлы seed. Загрузка в БД (``load_seed``) появляется вместе с моделями:
её вызывают ``just seed`` (``python -m app.seed``), ``SEED_ON_START=1`` и ``POST /__test/reset``.
Загрузчик обязан быть идемпотентным (повторный запуск на уже засеянной БД не падает)
и обходить доменную валидацию: seed не должен зависеть от заглушки.
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import make_engine, make_session_factory
from app.models import Group, Lesson, LessonFormat, LessonStatus
from app.settings import get_settings

# Корень репозитория: src/app/seed.py → parents[2]. В образе это /app (editable-установка пакета).
SEED_DIR = Path(__file__).resolve().parents[2] / "seed"

log = logging.getLogger(__name__)

SeedRecords = list[dict[str, Any]]


def read_seed_file(name: str, seed_dir: Path = SEED_DIR) -> SeedRecords:
    """Прочитать ``seed/<name>.json``: список объектов с фиксированными UUID."""
    path = seed_dir / f"{name}.json"
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        msg = f"{path}: ожидается список записей"
        raise ValueError(msg)
    records: SeedRecords = []
    for item in data:
        if not isinstance(item, dict):
            msg = f"{path}: каждая запись должна быть объектом"
            raise ValueError(msg)
        records.append(item)
    return records


def read_all(seed_dir: Path = SEED_DIR) -> dict[str, SeedRecords]:
    """Все файлы seed по имени файла без расширения."""
    return {
        path.stem: read_seed_file(path.stem, seed_dir) for path in sorted(seed_dir.glob("*.json"))
    }


async def load_seed(session: AsyncSession, data: dict[str, SeedRecords]) -> None:
    """Записать seed в БД поверх пустых таблиц.

    Прямые INSERT без доменной валидации и без people-stub. Повторный запуск на тех же
    UUID не падает и не плодит строки (``ON CONFLICT DO NOTHING``). Сначала группы,
    потом занятия — у L7 есть ``rescheduled_from_id`` на L6.
    """
    groups = data.get("groups", [])
    if groups:
        group_rows = [
            {
                "id": uuid.UUID(row["id"]),
                "name": row["name"],
                "size": row["size"],
            }
            for row in groups
        ]
        await session.execute(
            insert(Group).values(group_rows).on_conflict_do_nothing(index_elements=["id"])
        )

    lessons = data.get("lessons", [])
    if lessons:
        lesson_rows = [
            {
                "id": uuid.UUID(row["id"]),
                "group_id": uuid.UUID(row["group_id"]),
                "instructor_id": uuid.UUID(row["instructor_id"]),
                "classroom_id": (
                    uuid.UUID(row["classroom_id"]) if row.get("classroom_id") else None
                ),
                "format": LessonFormat(row["format"]),
                "status": LessonStatus(row["status"]),
                "starts_at": datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00")),
                "ends_at": datetime.fromisoformat(row["ends_at"].replace("Z", "+00:00")),
                "rescheduled_from_id": (
                    uuid.UUID(row["rescheduled_from_id"])
                    if row.get("rescheduled_from_id")
                    else None
                ),
                "cancel_reason": row.get("cancel_reason"),
                "created_at": datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
                "updated_at": datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
            }
            for row in lessons
        ]
        await session.execute(
            insert(Lesson).values(lesson_rows).on_conflict_do_nothing(index_elements=["id"])
        )


async def main() -> int:
    """``python -m app.seed``: загрузить seed в БД из ``DATABASE_URL``."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    data = read_all()
    log.info("Прочитан seed: %s", {name: len(rows) for name, rows in data.items()})
    try:
        async with factory() as session:
            await load_seed(session, data)
            await session.commit()
    finally:
        await engine.dispose()
    log.info("Seed загружен")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
