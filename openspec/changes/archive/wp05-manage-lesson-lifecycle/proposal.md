# Предложение: WP-05 Жизненный цикл занятия

## Цель

Добавить смену статусов занятия: `confirm` (FR-06), `cancel` (FR-07), `complete` (FR-09, BR-08), атомарный `reschedule` (FR-08) и фоновый `lesson-status-sweeper` (FR-10). После успешного confirm / cancel / reschedule публиковать события в брокер по `asyncapi.yaml` (FR-13).

## Trade-offs и альтернативы

- **Выбранный подход:** переходы статуса в сервисе по таблице допустимых; события — публикация сразу после commit (at-least-once), без outbox. Reschedule в одной транзакции: исходное → `RESCHEDULED`, новое → `PLANNED` через существующий пайплайн BR.
