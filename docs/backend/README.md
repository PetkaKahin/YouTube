# Бэкенд

Статус: подготовлена техническая основа FastAPI: подключения PostgreSQL/Valkey/RustFS,
Alembic, HTTP middleware, валидация, ошибки, Swagger и адаптер политик.
Добавлен каркас пяти бизнес-модулей; их реализации, бизнес-таблиц и worker пока нет. Результаты и границы проверки —
[T02](../tasks/T02-backend-foundation.md), [жизненный цикл](request-lifecycle.md).

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
| Dishka | DI и области жизни приложения/запроса |
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
Установлены SQLAlchemy ORM, Psycopg 3, Alembic, pwdlib[argon2], pydantic-settings,
email-validator и boto3. Подключены starsessions[redis], redis-py к Valkey,
starlette-csrf и адаптер PyCasbin (пакет casbin). Аутентификации реальных
аккаунтов и бизнес-политик ещё нет; ограничения — [библиотеки](libraries.md).
Очередь пока не выбрана.

Несекретные настройки приходят из Docker Compose через pydantic-settings;
локальные значения — в корневом `.env`, шаблон — [.env.example](../../.env.example).
Пароли и ключи — Compose Secrets, пути передаются как `*_FILE`.
`app/secrets.py` читает файлы, исключает значения из repr настроек
и запрещает подстановку секретов через обычные переменные. Файлы читаются
при сборке/старте API; lifespan проверяет доступность хранилищ и закрывает клиенты.
`APP_NAME` задаёт заголовок API; адреса сервисов и `/api` задаёт Compose.
Swagger: `/docs` → `/api/docs`; прикладной префикс `/api/v1`.

## Навигация

- [Обязательный порядок работы над задачей в скилле backend](../../skills/backend/SKILL.md#порядок-работы-над-задачей)
- [Бизнес-задачи B01–B36: описания и требования](../tasks/backend/README.md)
- [Архитектура и границы](architecture.md)
- [Жизненный цикл, маршруты, middleware, ошибки и Swagger](request-lifecycle.md)
- [Выбор библиотек и готовых механизмов защиты](libraries.md)
- [Q25: сравнение вариантов исправления гонки сессий](session-options.md)
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
`basedpyright`, `tach check`, `tach check-external --exclude 'tests,src/*/tests'`,
`uv audit --locked` с тем же Docker-префиксом.
pytest: `./docker/run dev exec python pytest`; проверяются secret-файлы,
HTTP-контракты, DI, сессии/CSRF и реальные подключения. Бизнес-тестов пока нет;
они размещаются в `src/<module>/tests`, общие проверки — в `tests`.
pytest также входит в `docker/run check`.
Ruff/Formatter, Basedpyright, оба аудита Tach, 203 теста и uv audit
выполнены успешно 2026-11-13. Правило чистого `public` —
[ADR-0024](../decisions/0024-public-dependencies.md); подключение провайдеров —
[ADR-0027](../decisions/0027-explicit-module-providers.md), Q29 закрыт.
Q25 закрыт расширением starsessions вне пакета —
[ADR-0028](../decisions/0028-session-concurrency.md).
Для видео обязательны проверки прав на HLS, повторного выполнения задания,
потери Valkey, доступности низкого качества при ошибке высокого.
