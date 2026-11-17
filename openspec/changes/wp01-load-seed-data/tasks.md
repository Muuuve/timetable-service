# Задачи: WP-01

- [x] Описать асинхронные SQLAlchemy-модели для `Lesson` и `Group`
- [ ] Сгенерировать и проверить миграцию Alembic на чистой БД (`alembic upgrade head`)
- [ ] Реализовать скрипт загрузки seed-данных с поддержкой `SEED_ON_START=1` и `just seed`
- [ ] Реализовать эндпоинты `GET /lessons/{lesson_id}` и `GET /groups/{group_id}` с обработкой 404
- [ ] Реализовать сброс состояния в `POST /__test/reset` (при `TEST_MODE=1`)
