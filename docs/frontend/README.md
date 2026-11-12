# Фронтенд

Статус: создан минимальный Vue bootstrap и пустая тёмная страница,
подключены Router, Pinia и Query; настроены инструменты. Бизнес-экранов нет.
Dev/prod, HMR через Nginx, ESLint/Prettier/vue-tsc/Vite и npm audit проверены
в Docker; [T01](../tasks/T01-docker.md).

| Компонент | Назначение |
| --- | --- |
| Vue 3 + TypeScript + Vite | SPA, типизация и сборка |
| Vue Router | Маршруты перечисленных в ТЗ экранов |
| Pinia | Клиентское состояние |
| Hey API | Генерируемые из OpenAPI клиент и типы |
| TanStack Query для Vue | Запросы, серверный кэш и мутации |
| vue-tsc | Проверка типов TypeScript и Vue |
| ESLint | Статические проверки |
| Prettier | Форматирование |

Архитектура — FSD. Поддержка — ПК и мобильный веб, тёмная тема.
Пакетный менеджер — npm. Версии закреплены в
[package.json](../../frontend/package.json) и [Docker](../infrastructure/docker.md).
Библиотека HLS-плеера и UI-подход пока не выбраны.
`OPENAPI_URL` задаётся в корневом `.env` и передаётся Node через Compose
для Hey API; это внутренний адрес, не URL для браузера. Секреты хранилищ
Node не получает; браузерные переменные появятся вместе с интеграцией API.

- [FSD, состояние и контракт API](architecture.md)
- [Экраны и состояния](screens.md)
- [Правила бизнес-модулей](../backend/README.md)
- [ТЗ](../project/requirements.md)
- [Открытые вопросы](../tasks/open-questions.md)
- [Docker](../infrastructure/docker.md)

После успешного запуска dev: `./docker/run check` либо
`./docker/run dev exec node npm run lint`, `npm run format:check`,
`npm run typecheck`, `npm run build` с тем же Docker-префиксом.
`npm run api:generate` запускает Hey API из OpenAPI внутри контейнера.
Линтинг, форматирование, типы, сборка и npm audit прошли 2026-11-12;
генерация Hey API ожидает бизнес-контрактов. Для поведения будут нужны проверки
HLS, сессии, приватности, загрузки и адаптивности; средство браузерных проверок
ещё не выбрано.
