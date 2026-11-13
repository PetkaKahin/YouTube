# Библиотеки бэкенда

Статус на 2026-11-13: перечисленные инструменты установлены и подключены
к технической основе [T02](../tasks/T02-backend-foundation.md). Точные пакеты —
в `backend/pyproject.toml` и `uv.lock`. [ADR-0020](../decisions/0020-backend-libraries.md).

## Согласовано

Механизмы сессий/CSRF и движок политик предоставляются готовыми библиотеками.
Самописное управление идентификаторами сессий и собственный policy engine не вводятся.

| Область | Выбор |
| --- | --- |
| PostgreSQL | SQLAlchemy ORM + Psycopg 3; ORM-модели только в infrastructure |
| Миграции | Alembic |
| Пароли | pwdlib[argon2] |
| Настройки | pydantic-settings; секреты только из Compose Secret files |
| Email | email-validator, без проверки доставляемости/DNS |
| RustFS | boto3; нужные S3-операции проверить интеграционно |
| Valkey | redis-py, пакет redis; общий клиент для сессий и кэша |

Это не расширяет ТЗ: JWT/Bearer API для внешних клиентов и прикладное шифрование
не добавляются без конкретного сценария. Имеющиеся cookie-сессии, срок 30 дней
и серверные данные в Valkey сохраняют [правила User](modules/user.md).
Версии закреплены при установке; совместимость основы и аудит проверены в Docker.

## Выбранные механизмы защиты

| Область | Выбор | Контрольные сценарии T02 и последующих бизнес-задач |
| --- | --- | --- |
| Сессии | starsessions[redis], RedisStore через redis-py к Valkey | Python 3.14/Starlette, rolling cookie/TTL 30 дней, регенерация ID, logout, потеря Valkey, конкурентные запросы |
| CSRF | starlette-csrf | Подписанный double-submit token, защищённые cookie, интеграция Vue и проверка небезопасных методов |
| Политики | PyCasbin, пакет casbin | Роли, атрибуты владельца/видимости/скрытия, разрешения по email; отзыв доступа без устаревшего кэша |

RedisStore starsessions требует redis-py; он выбран и для кэша.
Для Q25 используется наше расширение SessionStore с условной записью,
атомарной ротацией и подавлением устаревшей cookie; пакет библиотеки не изменяется.
Сам сервер остаётся Valkey. Базовые операции подтверждены тестами T02.

starsessions управляет идентификаторами, cookie, серверным хранением и rolling-сроком;
используем GuardedRedisStore, данные сессии остаются в Valkey. При входе helper
использует библиотечную генерацию ID и атомарный перенос записи; при выходе
запись удаляется, библиотека очищает cookie.
starlette-csrf отвечает за подписанный double-submit token и проверку заголовка.
Ключи подписи предоставляются через Compose Secrets согласно
[политике секретов](../decisions/0019-compose-secrets.md).
Базовые интеграции проверены; реализована аутентификация реальных аккаунтов
через Authenticate и HTTP login/me/logout.
Проверки и ограничение конкурентного завершения сессии —
[жизненный цикл](request-lifecycle.md#сессии-и-границы-текущей-готовности).
Сравнение опубликованных альтернатив и результаты отдельных проб в Docker —
[Q25: варианты сессий](session-options.md). Расширение starsessions выбрано
пользователем и реализовано вне пакета —
[ADR-0028](../decisions/0028-session-concurrency.md).

Casbin вызывается через порт авторизации: HTTP, CLI и фоновые сценарии используют
одни правила. Роль/владелец/права загружаются сервером через public владельцев модулей;
входные attributes клиента не считаются доверенными. Сами бизнес-политики необходимо
описать и проверить, библиотека исполняет их, но не определяет правила нашего проекта.
Политики должны учитывать [защиту медиапотока](video-pipeline.md), а не только URL API.

Пользователь выбрал Procrastinate для очереди PostgreSQL —
[ADR-0034](../decisions/0034-sha256-and-procrastinate.md). Параметры Q02 согласованы
в [ADR-0035](../decisions/0035-worker-policy-and-job-errors.md).
На 2026-11-13 проверена информация о релизе 3.11.0 от 2026-11-11:
очередь в PostgreSQL, Psycopg 3, sync/async-вызовы, повторы и блокировки задач.
Предоставляется постановка задания через внешнее соединение для общей транзакции.
Зависшие задания после гибели worker требуют настроенного механизма повторного
планирования по heartbeat; одно наличие библиотеки не завершает приёмку B19.
Релиз 3.11.0 установлен, зависимости закреплены в pyproject.toml/uv.lock.
На Python 3.14.10 прошли семь интеграционных проверок с реальным PostgreSQL:
общая транзакция SQLAlchemy, очередь/блокировка, ограниченные повторы, закрытие
и открытие приложения, SIGKILL worker и ручной recovery идемпотентного эффекта.
Важно: max_attempts=2 в этой версии даёт три запуска всего. Схема очереди
устанавливается миграцией 0004; worker обслуживания загрузок и recovery подключены.
Реализованы и отдельно проверены worker middleware для постоянного журнала
ошибок и безопасный обработчик логов — [диагностика](background-jobs.md).
Использован публичный механизм
[worker middleware](https://procrastinate.readthedocs.io/en/stable/howto/advanced/middleware.html),
документация и установленный код 3.11.0 проверены 2026-11-13.
Подробности и оставшиеся параметры — [T04](../tasks/T04-video-pipeline.md).
Источники: [PyPI](https://pypi.org/project/procrastinate/),
[внешняя транзакция](https://procrastinate.readthedocs.io/en/main/howto/production/external_connection.html),
[зависшие задания](https://procrastinate.readthedocs.io/en/main/howto/production/retry_stalled_jobs.html).

## DI: Dishka

Пользователь выбрал **Dishka** 2026-11-13. Технические провайдеры подключены
в T02: APP-ресурсы и REQUEST-сессия БД, готовая интеграция FastAPI и области
операций вне HTTP. [ADR-0025](../decisions/0025-dishka.md),
[рабочее использование](request-lifecycle.md#di-и-время-жизни).
На Python 3.14 с установленными FastAPI/Starlette проверены получение зависимостей,
разделение областей, закрытие при ошибке/стриминге и rollback сессии БД.

Согласованный подход: обычные конструкторы сценариев без DI-маркеров
в `domain/application/public`; провайдеры реализаций в инфраструктуре;
DI-маркеры в HTTP-контроллерах приложения. Контейнеру нужно явно передать
провайдеры. Пользователь выбрал явный реестр `app/module_providers.py` с узким
доступом только к классам провайдеров инфраструктуры. Правило `public` сохраняется;
подключение — [ADR-0027](../decisions/0027-explicit-module-providers.md).

Источники, проверены 2026-11-13:
[Provider](https://dishka.readthedocs.io/en/stable/provider/index.html),
[FastAPI](https://dishka.readthedocs.io/en/stable/integrations/fastapi.html),
[scopes](https://dishka.readthedocs.io/en/stable/advanced/scopes.html),
[метаданные PyPI](https://pypi.org/project/dishka/).

### Подключение провайдеров: исследование Q29

Проверено 2026-11-13. По результатам обсуждения выбран явный реестр,
а не декларативное обнаружение: ADR-0027. Ниже сохранено обоснование выбора.
Сборка зависимостей своего модуля принадлежит его инфраструктуре. `application`
принимает порты через обычные конструкторы; контроллер получает сценарий через
`FromDishka`, без самостоятельного обращения к контейнеру.

- В [fastapi-clean-example](https://github.com/ivan-borovets/fastapi-clean-example/blob/d60ced71deeeaf129aa87192158899a941b3e629/src/app/main/ioc/core.py)
  `CoreProvider` связывает `UserReader` с `SqlaUserReader` и регистрирует `ListUsers`.
  Сценарий принимает порт конструктором; список провайдеров собирает отдельный
  `main/ioc/provider_registry.py`. Это пример clean architecture, не нашей
  строгой схемы межмодульного `public`.
- В [python-modular-monolith](https://github.com/SergeyevSergey/python-modular-monolith/blob/84b18a7a47e6cfc370d8e2dbfec82c3db4d8a3bc/src/bootstrap.py)
  общий bootstrap явно подключает `MarketProvider` и `StorageProvider`, определённые
  в bootstrap своих модулей. В примере есть и прямые межмодульные импорты;
  переносить его архитектуру целиком нельзя.
- [Dishka provide](https://dishka.readthedocs.io/en/stable/provider/provide.html)
  анализирует аннотации конструктора зарегистрированного класса; привязка порта
  к реализации задаётся через `provides`. Автоматическое разрешение зависимостей
  после регистрации и обнаружение самих провайдеров — разные операции.
- Импорт провайдера в единственной точке сборки не является Service Locator.
  Locator появляется, когда потребитель сам запрашивает зависимости у контейнера;
  различие — [Fowler](https://martinfowler.com/articles/injection.html#ServiceLocatorVsDependencyInjection).

Приняты модульные провайдеры в infrastructure и одна явная точка подключения
при старте. Только `app.module_providers` получает узкое разрешение импортировать
классы провайдеров. Автообнаружение и entry points не используются; Q29 закрыт.
Контракта поверх `Dishka.Provider` не требуется. Бизнес-код не знает ни
провайдеры, ни контейнер. Бизнес-привязки добавляются с реальными сценариями:
User/Channel обслуживают аккаунты и регистрацию, остальные три провайдера пока пусты.

## Проверка технической основы

В Docker на Python 3.14.10 выполнены обязательные проверки качества и 203 теста (2026-11-13);
uv audit не обнаружил известных уязвимостей. Подтверждены реальные подключения,
S3 upload/get/head/delete, Argon2id, email без DNS, rolling-сессии и CSRF.
Casbin проверен на пустой политике, RBAC и немедленном отзыве тестовой роли.
В prod проверены запуск без dev-зависимостей и доступность Alembic.
Это не подтверждает ещё не реализованные ресурсные ABAC-политики, интеграцию Vue
или ресурсные сценарии аккаунтов. Гонка конкурентного выхода проверяется
отдельными тестами Q25. Подробная приёмка — [T02](../tasks/T02-backend-foundation.md).

## Проверенные источники

Документация и метаданные PyPI проверены 2026-11-12; runtime-результаты приведены выше:

- [starsessions](https://github.com/alex-oleshkevich/starsessions),
  [релиз PyPI](https://pypi.org/project/starsessions/): 2.2.1 от 2024-10-23.
  README основной ветки может отличаться от опубликованного релиза.
- [starlette-csrf](https://github.com/frankie567/starlette-csrf),
  [релиз PyPI](https://pypi.org/project/starlette-csrf/): 3.0.0 от 2023-06-27.
- [PyCasbin](https://github.com/apache/casbin-pycasbin),
  [ABAC](https://casbin.apache.org/docs/abac/).
- [Совместимость клиентов Valkey с Redis-протоколом](https://valkey.io/clients/).

Возраст релиза и наличие готовой библиотеки сами по себе не доказывают безопасность.
Перед интеграцией нужны проверка опубликованного пакета, аудит зависимостей
и тесты значимых сценариев. Самописной заменой непройденные проверки не обходятся.
