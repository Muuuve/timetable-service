# Спецификация: WP-06

## Требование FR-13: События в брокер

- **WHEN** успешный `confirm` / `cancel` / `reschedule`
- **THEN** в RabbitMQ появляется ровно одно событие нужного типа с конвертом платформы и `idempotency_key` вида `lesson-<kebab>:<lesson_id>`.

- **WHEN** операция отклонена (например повторный confirm → 409)
- **THEN** нового события в брокере нет.

- **WHEN** `GET /health/ready` при доступных БД и RabbitMQ
- **THEN** 200 `{"status":"ok","db":"ok","broker":"ok"}`.

- **WHEN** брокер недоступен (нет соединения)
- **THEN** 503, `status=degraded`, `broker=fail` (независимо от `db`).
