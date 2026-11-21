# Технический дизайн: WP-06

- **Брокер:** `BROKER=rabbitmq`, `BROKER_URL` (amqp). Kafka в этом пакете не подключается.
- **Публикация:** exchange `wa.events` (topic, durable); routing key `timetable.<EventType>`; JSON-конверт, `content_type=application/json`, `message_id=event_id`, `delivery_mode=2`.
- **Конверт:** `event_id`, `event_type`, `occurred_at` (Clock), `idempotency_key` (`lesson-confirmed|cancelled|rescheduled:<lesson_id>`), `producer=timetable-service`, `version=1`, `payload` по `asyncapi.yaml`.
- **Когда:** только после успешного commit confirm / cancel / reschedule; отклонённая операция события не даёт.
- **Readiness:** `ping()` — не флаг `is_closed` (у `connect_robust` он врёт при падении брокера), а passive `declare_exchange` на коротком канале; успех → `broker: ok`, иначе `fail` и 503 `degraded`.
- **Зависимости:** `aio-pika` в основных `dependencies` (образ без e2e-группы).
