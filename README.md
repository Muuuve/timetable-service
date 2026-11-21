# timetable-service

## Запуск

```bash
just doctor      # проверка окружения: Docker, Compose v2.20+, buildx 0.17+, uv 0.13+
docker compose up --build   # так стек запускает проверка; .env не нужен
just up          # то же в фоне с ожиданием healthcheck: app, postgres, people-stub, брокер
just migrate     # alembic upgrade head в контейнере app (entrypoint делает это и при старте)
just seed        # загрузка seed (python -m app.seed)
just check       # ruff + mypy + pytest tests/unit
just e2e-public  # публичные e2e против поднятого стека → e2e-report.json
just setup       # шаблон коммита и хук проверки (CONTRIBUTING.md)
curl -s localhost:8000/health/ready
```

Стек поднимается командами выше. В качестве брокера выбран RabbitMQ
(`BROKER=rabbitmq`, порт 5672): для Must достаточно одного брокера, а в
compose он уже есть по умолчанию, поэтому Kafka не подключали.

После `just up` готовность проверяется так:

```bash
curl.exe -s http://localhost:8000/health/ready
# ожидаемо: {"status":"ok","db":"ok","broker":"ok"}
```

## Решения

События confirm / cancel / reschedule уходят в RabbitMQ сразу после commit  
транзакции (at-least-once). Transactional outbox не сделан.

Для `/health/ready` недостаточно смотреть на `connection.is_closed`: при
`connect_robust` после остановки RabbitMQ флаг ещё долго остаётся «открыт»
и readiness врёт `broker: ok`. Поэтому ping делает AMQP-запрос —
passive `declare_exchange`.

Клиент `aio-pika` лежит в основных dependencies, а не только в группе e2e:
Docker-образ ставит пакеты с `--no-default-groups`, иначе приложение
не стартует с `ModuleNotFoundError`.

## Не сделано

Should/Could из задания не брали — хватило Must (WP-01…WP-07):

- **FR-14** (`POST /lesson-series`) — серия занятий;
- **FR-15** — отчёт нагрузки преподавателя за неделю;
- **FR-16** — `GET /groups`;
- **FR-17** — кэш ответов people-stub.

Kafka как второй брокер тоже не подключали: для Must выбран один — RabbitMQ.

## Async-кейс

При подготовке данных занятия клиент `people-stub` параллельно вызывает профиль преподавателя, окна доступности и (для занятия со статусом `IN_PERSON`) аудиторию через `asyncio.gather`.

Воспроизведение замера (фейковый транспорт, без сети; задержка 300 мс на вызов):

```bash
just test tests/unit/test_people_client_async.py -q
```

Ожидаемо: параллельный вызов `fetch_for_lesson` для очного занятия работает быстрее трёх последовательных вызовов (~300 мс против ~900 мс) и держит `max_in_flight == 3`.

## AI notes

Пользовался **Cursor**. Ключевые решения принимал сам. Агент помогал  
быстрее разобраться  в проекте, написать шаблонный код, тесты и черновики текстов.  Я читал изменения, правил стиль под репозиторий, решал, что коммитить и мёржить.

Заметки в ходе работы:

- разобрал падение app в Docker (`aio-pika` только в e2e);
- поймал ложный `broker: ok` после stop RabbitMQ и заменил ping на реальный
AMQP-запрос;
- устанавливал недостающие пакеты uv.

Перед мёржем каждого пакета запускал`just check` / e2e и правил то, что не сходилось с заданием.

## Время

| Пакет                        | Оценка задания, ч | Моя оценка до старта, ч | Факт, ч | Куда ушло время (при факте ≥ 2× и ≥ +1 ч)                  |
| ---------------------------- | ----------------- | ----------------------- | ------- | ---------------------------------------------------------- |
| WP-00 Процесс                | 0,75              | 1,5                     | 2,0     |                                                            |
| wp01-load-seed-data          | 0,8               | 2,0                     | 3,5     | Отладка windows: python3, git. Изменение CRLF в entrypoint |
| wp02-add-people-client       | 0,75              | 1,5                     | 2,0     |                                                            |
| wp03-detect-lesson-conflicts | 1,75              | 3,0                     | 3,5     |                                                            |
| wp04-list-and-edit-lessons   | 0,8               | 1,5                     | 1,5     |                                                            |
| wp05-manage-lesson-lifecycle | 1,5               | 2,0                     | 2,0     |                                                            |
| wp06-publish-lesson-events   | 0,7               | 1,0                     | 1,0     |                                                            |
| wp07-finalize-docs           | 0,4               | 2,0                     | 2,0     |                                                            |
| Итого                        | 7,45              | 14,5                    | 17,5    |                                                            |
