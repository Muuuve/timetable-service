# Технический дизайн: WP-05

- **Переходы:** таблица допустимых смен статуса; иначе 409 `TRANSITION_FORBIDDEN` (`current_status`, `target_status`). Нет занятия → 404.
- **confirm:** `PLANNED → CONFIRMED`; после commit — `LessonConfirmed`.
- **cancel:** `PLANNED|CONFIRMED → CANCELLED`; опциональный `reason` → `cancel_reason`; после commit — `LessonCancelled`.
- **complete:** сначала переход `IN_PROGRESS → COMPLETED`, затем BR-08: `now >= ends_at` (часы сервиса), иначе 409 `LESSON_NOT_FINISHED` (`ends_at`, `now`). События нет.
- **reschedule:** только `PLANNED|CONFIRMED`; в одной транзакции исходное → `RESCHEDULED`, новое `PLANNED` (та же группа, `rescheduled_from_id`); незаданные поля из исходного; BR-01…BR-07 с исключением исходного; не более одного переноса; 201 `{lesson, warnings}`; после commit — `LessonRescheduled`.
- **sweeper (FR-10):** `CONFIRMED` с `starts_at ≤ now` → `IN_PROGRESS`; регистрация в `JobRegistry` как `lesson-status-sweeper`; в `TEST_MODE=0` — периодический запуск.
- **События:** конверт платформы (`event_id`, `event_type`, `occurred_at`, `idempotency_key`, `producer=timetable-service`, `version=1`, `payload`); брокер по `BROKER` (RabbitMQ).
