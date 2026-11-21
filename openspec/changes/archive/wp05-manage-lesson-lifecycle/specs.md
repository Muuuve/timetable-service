# Спецификация: WP-05

## Требование FR-06: Confirm

- **WHEN** `POST /lessons/{id}/confirm` для `PLANNED`
- **THEN** 200, статус `CONFIRMED`; в брокере одно `LessonConfirmed` с `idempotency_key=lesson-confirmed:<id>`.

- **WHEN** повторный confirm для уже `CONFIRMED`
- **THEN** 409 `TRANSITION_FORBIDDEN`; нового события нет.

## Требование FR-07: Cancel

- **WHEN** cancel для `PLANNED` или `CONFIRMED` (с причиной или без)
- **THEN** 200, статус `CANCELLED`, `cancel_reason` сохранён при передаче; событие `LessonCancelled`.

- **WHEN** cancel из другого статуса
- **THEN** 409 `TRANSITION_FORBIDDEN`.

## Требование FR-08: Reschedule

- **WHEN** reschedule для `PLANNED`/`CONFIRMED` с валидным интервалом
- **THEN** 201 `{lesson, warnings}` с новым занятием; исходное `RESCHEDULED`; событие `LessonRescheduled`.

- **WHEN** у занятия уже был перенос или статус недопустим
- **THEN** 409; исходное и новое не меняются / не создаются.

## Требование FR-09: Complete

- **WHEN** complete для `IN_PROGRESS` и `now >= ends_at`
- **THEN** 200, статус `COMPLETED`.

- **WHEN** `now < ends_at`
- **THEN** 409 `LESSON_NOT_FINISHED`.

- **WHEN** статус не `IN_PROGRESS`
- **THEN** 409 `TRANSITION_FORBIDDEN` (раньше BR-08).

## Требование FR-10: Sweeper

- **WHEN** `POST /__test/jobs/lesson-status-sweeper/run` и есть `CONFIRMED` с `starts_at ≤ now`
- **THEN** ответ `{"processed": N}`; эти занятия в `IN_PROGRESS`.

- **WHEN** занятие не `CONFIRMED` или `starts_at > now`
- **THEN** sweeper его не меняет.
