# Бэкенд

Статус: минимальная точка входа FastAPI и контейнерная конфигурация созданы;
бизнес-модули, доступ к данным и worker ещё не реализованы. Сборки dev/prod,
11 тестов и проверки качества прошли в Docker; [T01](../tasks/T01-docker.md).

## Утверждённый стек

| Компонент | Назначение |
| --- | --- |
| Python + FastAPI | HTTP API и OpenAPI |
| uv | Зависимости, окружение и lock-файл |
| PostgreSQL | Аккаунты, роли, метаданные, права доступа, постоянная очередь заданий |
| Valkey | Потеряемые кэш, сессии и серверные данные cookie-сессий |
| RustFS | Исходники, превью и HLS-артефакты |
| FFmpeg / ffprobe | Фоновое исследование и обработка видео |
| pytest | Проверка поведения |
| Tach | Границы импортов модулей |
| Basedpyright | Проверка типов |
| Ruff + Ruff Formatter | Линтинг и форматирование |
| uv audit + Ruff Security | Аудит зависимостей и security-правила Ruff |

Ruff Security здесь означает семейство правил `S` (`flake8-bandit`), а не
отдельный сервис. Источники: [Ruff](https://docs.astral.sh/ruff/rules/),
[uv audit](https://docs.astral.sh/uv/reference/cli/#uv-audit), проверены 2026-11-11.
Очередь заданий хранится в PostgreSQL; каждое качество видео обрабатывается
отдельной задачей. [Принятое решение](../decisions/0006-video-jobs-and-audio.md).
Версии Python/сервисов закреплены в [Docker-конфигурации](../infrastructure/docker.md),
пакеты — в [pyproject.toml](../../backend/pyproject.toml).
ORM, миграции, драйвер БД и библиотека заданий пока не выбраны.
PostgreSQL не подразумевает автоматически SQLAlchemy или Alembic.

Несекретные настройки приходят из Docker Compose через `os.environ`;
локальные значения — в корневом `.env`, шаблон — [.env.example](../../.env.example).
Пароли и ключи — Compose Secrets, пути передаются как `*_FILE`.
`bootstrap/secrets.py` читает файлы, исключает значения из repr настроек
и запрещает подстановку секретов через обычные переменные. Lifespan проверяет
наличие и содержимое файлов перед стартом API.
`APP_NAME` задаёт заголовок API; адреса сервисов и `/api` задаёт Compose.
Подключение к хранилищам ещё не реализовано.

## Навигация

- [Архитектура и границы](architecture.md)
- [Загрузка, обработка и раздача](video-pipeline.md)
- [User: аккаунты, роли, сессии](modules/user.md)
- [Video: метаданные, доступ, выдача, лайки](modules/video.md)
- [Поиск: типы, фильтры и сортировки](../project/search.md)
- [Channel: канал, подписки, плейлисты](modules/channel.md)
- [Comment: комментарии](modules/comment.md)
- [Moderation: жалобы и скрытие](modules/moderation.md)
- [Docker](../infrastructure/docker.md), [задачи](../tasks/README.md)

## Проверки

После успешного запуска dev: `./docker/run check` либо отдельные команды
`./docker/run dev exec python ruff check .`, `ruff format --check .`,
`basedpyright`, `tach check`, `uv audit --locked` с тем же Docker-префиксом.
pytest: `./docker/run dev exec python pytest`; добавлены тесты чтения secret-файлов
и защиты диагностик, бизнес-тестов пока нет. pytest также входит в `docker/run check`.
Ruff/Formatter, Basedpyright, Tach, pytest и uv audit выполнены успешно 2026-11-12.
Для видео обязательны проверки прав на HLS, повторного выполнения задания,
потери Valkey, доступности низкого качества при ошибке высокого.
