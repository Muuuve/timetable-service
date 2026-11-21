# Спецификация: WP-07

## Требование NFR-06: README и OpenSpec

- **WHEN** README просматривают на сдаче
- **THEN** есть все шесть заголовков шаблона; «Запуск», «Решения», «Async-кейс», «AI notes», «Время» заполнены; «Не сделано» описывает Should/Could или явно пусто с пояснением.

- **WHEN** смотрят `openspec/changes/`
- **THEN** завершённые пакеты Must лежат в `archive/`; артефакты proposal/specs/design/tasks на месте.

## Требование NFR-05: чистый инструмент

- **WHEN** на `master` после пакета запускают `just check`
- **THEN** ruff, mypy и unit-тесты проходят без ошибок.

- **WHEN** запускают `just lint-commits`
- **THEN** нет FAIL по истории коммитов (Conventional Commits, merge footers).
