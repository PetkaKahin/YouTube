# T01: основа репозитория и контейнеров

Статус: `done`, проверено 2026-11-12. Dev/prod собраны и запущены через Docker.
После проверки оставлен dev на `http://localhost:18080`; 8080 занят другим проектом.
Бизнес-функции, worker, миграции и установка Docker на чистую Ubuntu сюда не входят.

## Результат

- Compose prod/dev/bootstrap в корне; файлы контейнеров — в `docker/`.
- Python/Node/Nginx: ровно base/dev/prod, dev/prod напрямую наследуются от base.
- PostgreSQL/RustFS с постоянными томами, потеряемый Valkey, FFmpeg в Python.
- Закреплённые образы/зависимости и созданные `backend/uv.lock`, `frontend/package-lock.json`.
- `docker/run`: init, lock, dev/prod, check, audit, context-check.
- Корневой Makefile с базовыми командами dev и справкой; вызывает `docker/run`.
- Корневые env/example только для несекретных настроек; Compose Secrets по сервисам.
- Минимальные точки входа FastAPI/Vue, инструменты качества и тесты secret-файлов.

Команды и ограничения — [Docker](../infrastructure/docker.md).
Решения: [ADR-0017](../decisions/0017-docker-environment.md),
[ADR-0018](../decisions/0018-application-environment.md),
[ADR-0019](../decisions/0019-compose-secrets.md).

## Проверка

| Область | Фактический результат |
| --- | --- |
| Bootstrap | Секреты/locks созданы внутри Docker; повторный init сохраняет все значения |
| Dev | Сервисы healthy, приватный bucket создан; API reload и Vite HMR через Nginx проверены |
| Backend | Ruff, Formatter, Basedpyright, Tach проходят; pytest: 11 passed |
| Makefile | make help/ps/test выполнены; 11 тестов проходят; остальные цели проверены через dry-run |
| Frontend | ESLint, Prettier, vue-tsc/Vite build проходят |
| Зависимости | uv audit: 42 пакета без известных проблем; npm audit: 0 vulnerabilities |
| Prod | Сборка и startup проходят; node/rustfs-init exit 0; Nginx отдаёт assets с cache headers |
| HTTP | SPA/API docs/OpenAPI доступны; .env и secret-пути дают 404; dev-инструментов Python в prod нет |
| Данные | PostgreSQL/RustFS сохраняют контрольные записи/объект после рестарта; Valkey теряет ключ; пробы удалены |
| Утечки | 43 поверхности dev, 40 prod: совпадений секретов/кодировок нет; process-файлы прочитаны без пропусков |
| Доступ | Фактические secret mounts соответствуют сервисам; Node/Nginx их не получают |
| Контексты | Реальный экспорт BuildKit для Python/Node/Nginx/root: исходники/locks есть, secrets/env/артефактов нет |
| Репозиторий | Проверено 97 исходников/документов/настроек, значений секретов нет; Git исключает env/secrets и сохраняет locks |

## Найдено и исправлено

- `!docker/` повторно включал secrets в build context. Добавлены повторные
  исключения содержимого родителя и явное исключение secrets в каждом ignore-файле.
  В используемых образах этих файлов не было: COPY выбирал другие пути.
  Кэш неудачной пробы и её контексты удалены точечно; новые пробы используют
  временный builder, который удаляется вместе с кэшем.
- Встроенный bootstrap RustFS 1.0.5 требует неподдерживаемую команду CLI.
  Приватный bucket теперь создаёт `rustfs-init`, без credentials в argv/RC_HOST.
- Автоматическое преобразование CRLF при чтении обходило валидацию secret-файлов.
  Использовано чтение bytes/decode; соответствующий тест проходит.
- npm сообщил о 14 уязвимостях. Закреплены исправленные транзитивные
  `js-yaml`/`minimatch`; совместимость проверена линтером, типами и сборкой.

Docker administrator по-прежнему может читать смонтированные secrets через daemon:
это проверенная граница доверия, а не утечка через inspect. Проверка не является
полным исследованием всех слоёв образов или всей безопасности будущего приложения.
T02–T11 сохраняют свои вопросы и критерии; T11 на чистой Ubuntu ещё не проверена.
