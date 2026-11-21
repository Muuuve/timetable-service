# Предложение: WP-03 Правила занятий и создание/validate

## Цель
Реализовать проверку BR-01…BR-07 (hard и soft) и эндпоинты `POST /lessons` (FR-01) и `POST /lessons/validate` (FR-02): создание с ответом `{lesson, warnings}` и проверка без записи с `{ok, violations, warnings}`. Использовать клиент people-stub из WP-02 и модели/seed из WP-01.

## Trade-offs и альтернативы
- **Выбранный подход:** доменный слой с правилами BR (тестируется без FastAPI); HTTP только принимает тело и отдаёт `ApiError` / warnings. Порядок hard-ошибок — по таблице BR-01…BR-05 (возврат кода самой ранней по порядку ошибки); soft BR-06/BR-07 не блокируют create.
