# Спецификация: WP-03

## Требование FR-01: Создание занятия

- **WHEN** `POST /lessons` с валидным очным занятием без нарушений
- **THEN** 201 и тело `{lesson, warnings}` (warnings могут быть пусты); занятие в БД со статусом `PLANNED`.

- **WHEN** нарушено hard-правило (например BR-05 пересечение с L1 по преподавателю)
- **THEN** занятие не создаётся; ответ с кодом из таблицы BR (например 409 `SCHEDULE_CONFLICT` с `details.conflicts`).

- **WHEN** срабатывает только soft BR-06 или BR-07
- **THEN** 201, занятие создано, в `warnings[]` соответствующий code (`WORKLOAD_SOFT_LIMIT` / `INSTRUCTOR_UNAVAILABLE`).

- **WHEN** stub недоступен (5xx) или не отвечает вовремя
- **THEN** 502 `UPSTREAM_UNAVAILABLE` или 504 `UPSTREAM_TIMEOUT`; записи нет.

## Требование FR-02: Валидация без создания

- **WHEN** `POST /lessons/validate` для тех же данных, что дали бы 409 при create
- **THEN** 200 `{ok: false, violations: [...], warnings: [...]}`; в БД ничего не появляется.

- **WHEN** данные проходят все hard-правила
- **THEN** 200 `{ok: true, violations: [], warnings: [...]}` (warnings — только soft).

- **WHEN** ошибка 422/502/504
- **THEN** те же HTTP-коды, что у `POST /lessons` (не маскируются в `violations`).

## Правила BR (краевые случаи для тестов)

- **WHEN** `ends_at` одного активного занятия равен `starts_at` другого
- **THEN** BR-05 конфликта нет (касание границ).

- **WHEN** конфликт возможен только с `CANCELLED` или `RESCHEDULED`
- **THEN** BR-05/BR-06 их не учитывают.
