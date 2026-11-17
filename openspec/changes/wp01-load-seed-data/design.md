# Технический дизайн: WP-01

- **Слой моделей и защиты инвариантов:**
  - Модели `Lesson` и `Group` на SQLAlchemy 2.0 (async).
  - На уровне БД: FK занятия на группу, unique + FK на `rescheduled_from_id`, CHECK длительности (BR-01). Преподаватели и аудитории без локального FK — они в people-stub.
- **Управление транзакциями:**
  - Чтение (`GET /lessons/{lesson_id}`, `GET /groups/{group_id}`) в сессии БД.
  - `POST /__test/reset` и seed выполняются атомарно; часы не меняются.
