# YouTube

Учебный проект: копия интерфейса YouTube для ПК и мобильного веба в тёмной теме.
Сердце системы — загрузка, обработка, хранение и защищённая раздача видео.

**Состояние:** подготовлены документация, три скилла, Docker-окружение
и минимальные каркасы FastAPI/Vue. Dev/prod собраны и проверены в Docker;
бизнес-функций пока нет. Результаты — [T01](docs/tasks/T01-docker.md).

При доступном Docker Engine с BuildKit, Compose 2.24+, Bash и GNU Make:

```bash
make init
make up
make check
make audit
make context-check
make down
```

Первый `up` создаёт секреты и недостающие lock-файлы в контейнерах.
Все команды dev — `make help`; логи — `make logs`, состояние — `make ps`,
тесты — `make test`, shell — `make shell` / `make shell-front`.
Настройки — в корневом `.env`, шаблон — [.env.example](.env.example).
После копирования шаблона выполнить `./docker/run init`: он создаёт secret-файлы
в игнорируемом `docker/secrets/`. Пароли и ключи передаются через Compose Secrets;
в environment остаются несекретные значения и пути `*_FILE`.
Приложение читает окружение и `/run/secrets/`, без чтения `.env` из кода.
По умолчанию приложение — `http://localhost:8080`, API docs —
`http://localhost:8080/api/docs`; порт меняется через `HTTP_PORT` в `.env`.
В текущем локальном окружении используется 18080, поскольку 8080 занят.
Сейчас фронтенд — пустая тёмная страница.
Для prod: `./docker/run prod up --build -d --wait` после остановки dev.
Оба режима используют одни тома; одновременно их запускать не следует.

- [Документация и карта чтения](docs/README.md)
- [ТЗ и границы проекта](docs/project/requirements.md)
- [Архитектура бэкенда](docs/backend/architecture.md)
- [Архитектура фронтенда](docs/frontend/architecture.md)
- [Задачи](docs/tasks/README.md)
- [Инструкции и скиллы Codex](AGENTS.md)

Команды, структура и ограничения: [Docker](docs/infrastructure/docker.md).
Установка на чистую Ubuntu одной командой остаётся задачей T11.
