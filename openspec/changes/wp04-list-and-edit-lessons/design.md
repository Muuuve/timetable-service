# Технический дизайн: WP-04

- **Список (FR-03):**
  - Фильтры объединяются по: `group_id`, `instructor_id`, `classroom_id`, `status`, `from`, `to`.
  - Период: `starts_at >= from` и `starts_at < to` (оба опциональны).
  - Сортировка: `starts_at` ASC, затем `id` ASC.
  - Пагинация: `limit` (по умолчанию 50, максимум 200), `offset` (по умолчанию 0); в ответе `items` и `total` (число до limit/offset).
- **PATCH (FR-05):**
  - Статус не `PLANNED` → 409 `LESSON_NOT_EDITABLE`.
  - Нет занятия → 404 `NOT_FOUND`.
  - Тело `LessonPatch` (min 1 поле): слить с текущими полями; `group_id` и `status` не меняются.
  - Кандидат с `exclude_lesson_id = lesson_id`, затем BR-01…BR-07 через существующий сервисный/доменный путь; soft → `warnings[]`.
  - Успех: UPDATE, 200 `{lesson, warnings}`.
- **Время:** вход со смещением; ответ через `to_iso_z`.
