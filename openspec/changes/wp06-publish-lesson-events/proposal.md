# Предложение: WP-06 Публикация событий занятий

## Цель

Закрыть FR-13: публикация `LessonConfirmed` / `LessonCancelled` / `LessonRescheduled` в выбранный брокер по `asyncapi.yaml` и учёт брокера в `GET /health/ready`. Брокер пакета — RabbitMQ (`BROKER=rabbitmq`).

## Trade-offs и альтернативы

- **Выбранный подход:** брокер — RabbitMQ; публикация сразу после commit (at-least-once), без transactional outbox. Конверт и publisher из WP-05; readiness — реальный AMQP-ping (passive declare), не флаг `is_closed`; `aio-pika` в runtime; выбор брокера в README.
- **Альтернатива:** Kafka — отвергнута. Ping только по `is_closed` — отвергнут: при `connect_robust` после падения брокера даёт ложный `ok`.
