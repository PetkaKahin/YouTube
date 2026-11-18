# Docker и деплой

Статус: dev/prod собраны и запущены, проверки и аудит секретов выполнены
2026-11-12. Lock-файлы созданы. [T01](../tasks/T01-docker.md),
[ADR-0017](../decisions/0017-docker-environment.md).

## Файлы и стадии

Compose и локальные настройки приложения находятся в корне,
файлы контейнеров — в `docker/`:

```text
docker-compose.yml                 # prod
docker-compose.dev.yml             # дополнение dev
docker-compose.bootstrap.yml       # только base Python/Node, без lock-файлов
.env.example                       # настройки без секретов, хранится в Git
.env                               # локальные значения, игнорируется Git
.dockerignore                      # общие исключения контекста сборки
docker/
  run                              # инициализация, запуск, проверки
  versions.env                     # закреплённые образы
  secrets/                         # локальные файлы Compose Secrets, вне Git и сборки
  python/                          # Dockerfile, init, smoke, проверки утечек/контекста
  node/                            # Dockerfile, синхронизация dev node_modules
  nginx/                           # Dockerfile, HTTP-конфигурация
  valkey/                          # конфигурация потеряемого хранилища
  rustfs/init.sh                    # создание и проверка приватного bucket videos
```

В Dockerfile `python`, `node`, `nginx` ровно три стадии: `base`, `dev`, `prod`.
`dev` и `prod` напрямую наследуются от `base`. PostgreSQL, Valkey и RustFS
используют готовые образы и не требуют собственных Dockerfile.
Nginx получает `/app/dist` из сборки `node` через
[additional_contexts](https://docs.docker.com/compose/how-tos/dependent-images/).
Тот же механизм передаёт бинарники uv в Python без четвёртой стадии.

Python `base` включает FFmpeg и `mesa-vulkan-drivers`; переменная
`VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json` выбирает программный
Mesa/Lavapipe. GPU хоста, GPU-драйверы и передача видеоустройств не требуются.
При использовании `libplacebo` FFmpeg также явно получает созданное CPU-устройство:
`-init_hw_device vulkan=cpu:0 -filter_hw_device cpu`.
Квалификация на Linux/amd64 и границы переносимости —
[ADR-0046](../decisions/0046-cpu-hdr-conversion.md).

## Версии

Полные теги и digest — в [versions.env](../../docker/versions.env).
Проверены 2026-11-12: Python 3.14.10, Node 26.13.0, Nginx 1.30.6,
PostgreSQL 18.6, Valkey 9.1.3, RustFS 1.0.5, uv 0.13.7.

Используем поддерживаемые стабильные ветки, LTS там, где он существует:
[Node 26 LTS](https://nodejs.org/en/about/previous-releases),
[PostgreSQL 18 с пятилетней поддержкой](https://www.postgresql.org/support/versioning/),
[Valkey 9.1 с расширенной поддержкой](https://valkey.io/topics/releases/).
У Python нет отдельного LTS-обозначения; выбрана поддерживаемая 3.14.
Nginx — [stable](https://nginx.org/en/download.html), RustFS — стабильный релиз,
без приписывания им статуса LTS.
Зависимости закреплены в `backend/pyproject.toml` и `frontend/package.json`.
TypeScript 6.0 выбран для совместимости с typescript-eslint;
следующая major требует проверки совместимости.
В npm `overrides` закреплены `js-yaml` 4.3.2 и `minimatch` 11.0.1:
исключены уязвимые транзитивные версии инструментов; ESLint и сборка проверены
с этими заменами. Пересматривать overrides при обновлении поставляющих их пакетов.

## Первый запуск и зависимости

Для команд Makefile нужны Docker Engine с BuildKit, Compose 2.24+, Bash и GNU Make.
Python, Node, npm, uv,
FFmpeg и линтеры на хост устанавливать не нужно.

```bash
make init
make up
```

`init` собирает `base`, затем в контейнерах:

1. Создаёт корневой `.env` из `.env.example` и добавляет недостающие несекретные настройки.
2. Создаёт отсутствующие файлы Compose Secrets в `docker/secrets/` без вывода значений.
   Старые значения паролей/ключей из `.env` переносит в файлы и удаляет из `.env`.
   Существующие секреты сохраняет; при конфликте со старым env-значением останавливается.
   Пустой существующий secret-файл считается ошибкой и не заменяется новым паролем.
3. Создаёт недостающие `backend/uv.lock` и `frontend/package-lock.json`.
   Они уже созданы и проверены; оба lock-файла должны храниться в Git.

Первый `up`, `build` или `run` автоматически вызывает `init`, если этих файлов нет
или отсутствует непустой обязательный secret-файл.
Для осознанного обновления lock-файлов — `./docker/run lock`.
Обычная сборка использует `uv sync --locked` / `npm ci` и не обновляет зависимости.
Lock-файлы фиксируют Python/npm-пакеты; FFmpeg устанавливается из Debian
при сборке и не закреплён отдельным digest пакета.

Bootstrap пишет файлы с UID/GID пользователя хоста. Dev-контейнеры работают
не от root с UID 1000; при другом владельце checkout права на создаваемые
из контейнера файлы нужно согласовать до работы с ними.

## Команды dev через Makefile

Корневой [Makefile](../../Makefile) вызывает `docker/run`: Compose-файлы,
инициализация и работа с secrets остаются в одном месте. `make` без цели
показывает справку; цели выполняются последовательно, включая `make -j`.

| Команда | Назначение |
| --- | --- |
| make help | Полный список команд |
| make init | Несекретный env, secrets, недостающие lock-файлы |
| make up / make build | Сборка и запуск с ожиданием готовности / только сборка |
| make frontend-dev | Собрать Node/Nginx и поднять dev-фронтенд с зависимостями API |
| make frontend-build | Одноразовый Node-контейнер: vue-tsc и Vite в frontend/dist |
| make start / make stop | Поднять без принудительной сборки / остановить контейнеры |
| make restart | Перезапустить и дождаться готовности |
| make down | Удалить контейнеры и сеть, сохранив тома |
| make logs / make ps | Логи / состояние, включая завершённый rustfs-init |
| make test / make check | pytest / полный набор проверок |
| make role get EMAIL=адрес | Прочитать текущую роль существующего аккаунта |
| make role set EMAIL=адрес ROLE=роль | Назначить роль, включая первого администратора |
| make audit / make context-check | Утечки секретов / контексты BuildKit |
| make lock | Обновить lock-файлы в Docker |
| make shell / make shell-front | Shell Python / Node |

Для test/check, назначения ролей и shell требуется поднятый dev.
`frontend-build` использует `dev run --rm --no-deps`, поэтому работающий
Node-контейнер не требуется. `frontend-dev` собирает только Node/Nginx,
поднимает их и зависимости API; фоновые worker запускаются через `make up`.
Роли читаются/меняются через `app.cli role get/set` и User.public.
EMAIL обязателен для обеих команд, ROLE — для set; пустые значения отклоняются до Docker.
Make не перечисляет названия ролей: допустимые значения проверяет бэкенд.
У команды role ровно одна подкоманда get либо set; смешивание с другими целями
отклоняется до выполнения, отдельные make get/set не определены.
Значения передаются через экспортированные переменные и quoted shell arguments,
без вставки пользовательского текста в код рецепта. Перед email используется
`--`, чтобы начальный дефис не превращал значение в опцию CLI.
EMAIL/ROLE сохраняются через GNU Make `value`: символ `$` и выражения Make
не вычисляются при экспорте аргументов. Регрессионная проверка —
[docker/python/check_make.py](../../docker/python/check_make.py): она перехватывает
argv вместо реальных вызовов Docker и проверяет команды, отказы и инъекции.
Повторный запуск в одноразовом контейнере (GNU Make устанавливается только в нём):

```bash
./docker/run dev run --rm --no-deps -T --user 0 --volume "$PWD:/project:ro" python sh -c \
  'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends make >/dev/null && python /project/docker/python/check_make.py'
```

Prod пока запускается напрямую
через `docker/run prod ...`. Перед переключением режима использовать `down`
текущего режима: dev/prod используют один Compose-проект.

## Переменные и ignore-файлы

[.env.example](../../.env.example) хранится в Git; `.env` — локальный файл,
его можно создать копированием примера, затем выполнить `./docker/run init`.
`.env` содержит только несекретные настройки. Secret-файлы создаёт init
в Docker; в текущем checkout они уже созданы и проверены.
Версии образов остаются в `docker/versions.env`.

Compose читает `docker/versions.env` и корневой `.env`; `docker/run` передаёт
оба через `--env-file`. Значения попадают в контейнеры через явные
[`environment`](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/):
Python получает `APP_NAME`, адреса/названия PostgreSQL/Valkey/RustFS, `UVICORN_ROOT_PATH`, `COOKIE_SECURE`
и пути `*_FILE`; Node — `OPENAPI_URL` для Hey API.
Значения паролей/ключей не передаются через Compose environment, build args или команды.
FastAPI читает обычные настройки через pydantic-settings из environment,
секреты — из файлов `/run/secrets/`.
Файл `.env` в контейнер приложения не копируется, dotenv не требуется.

Все секреты конфигурации (пароли сервисов, API-токены, ключи) передавать через
[Compose Secrets](https://docs.docker.com/compose/how-tos/use-secrets/).
Для новых секретов добавить локальный файл, top-level `secrets`, доступ только
нужным сервисам и чтение из файла в потребителе; в `.env.example` значение не помещать.
Пользовательские пароли и сессии сохраняют [правила User](../backend/modules/user.md):
хеши паролей находятся в PostgreSQL, серверные сессии — в Valkey.

| Secret | Кому предоставлен |
| --- | --- |
| postgres_password | Python, PostgreSQL |
| valkey_password | Python, Valkey |
| rustfs_access_key, rustfs_secret_key | Python, RustFS, rustfs-init |
| csrf_secret | Только Python |

Node и Nginx секреты не получают. PostgreSQL использует `POSTGRES_PASSWORD_FILE`,
RustFS — [RUSTFS_ACCESS_KEY_FILE / RUSTFS_SECRET_KEY_FILE](https://docs.rustfs.com/en/operations/credentials).
Valkey читает файл через `docker/valkey/start.sh`, создаёт ACL с SHA-256 хешем
в tmpfs `/data` и запускается с путём конфигурации. Healthcheck читает пароль
через stdin (`--askpass`), не через аргументы или экспорт переменной с его значением.
Python проверяет secret-файлы при старте; пустые/некорректные файлы и передача
значений через обычный environment приводят к ошибке без вывода содержимого.

Текущие пять secret-файлов содержат одну непустую строку UTF-8
с необязательным завершающим LF.
Каталог на хосте — 0700, файлы — 0444: Compose монтирует их read-only,
а разные UID сервисов должны иметь возможность их читать. Для file secrets
[uid/gid/mode не переназначаются Compose](https://docs.docker.com/reference/compose-file/services/#secrets);
каталог 0700 защищает чтение другими обычными пользователями хоста.
Локальный Compose использует файлы хоста, не шифрует их хранилище как Swarm.
`init` не ротирует существующие значения. После осознанной замены secret-файла
пересоздать контейнеры через `docker/run dev up -d --force-recreate` либо prod;
смена пароля существующей роли PostgreSQL требует также обновления пароля в БД.

Пользовательские значения находятся в `.env.example`; внутренние адреса
`postgres:5432`, `valkey:6379`, `http://rustfs:9000`, bucket `videos` и
префикс `/api` задаёт Compose в соответствии с конфигурацией контейнеров.
Браузерных переменных пока нет. При их добавлении передавать в сборку фронтенда
только явно выбранные публичные значения; пароли сервисов в Node не передаются.
После изменения `.env` пересоздать контейнеры через `docker/run dev up -d` либо prod.
Добавляя настройку, обновлять `.env.example`, явный список `environment`
нужного сервиса и чтение переменной в приложении; не передавать весь env-файл
каждому контейнеру.

`.gitignore` исключает локальные env-файлы на любой глубине, кэши, зависимости,
сборки, покрытия, логи и настройки IDE. Примеры `.env.example`, `uv.lock` и
`package-lock.json` остаются доступными для Git.
`.gitignore` и `.dockerignore` также исключают `docker/secrets/` целиком.
Корневой `.dockerignore` исключает env-файлы, Git/IDE и артефакты.
У каждого Dockerfile дополнительно ограничен набор входных файлов;
исключения секретов повторены после разрешающих правил там, где разрешены
целые деревья исходников. Dockerfile-specific файл
[заменяет корневой](https://docs.docker.com/build/concepts/context/#dockerignore-files),
поэтому одного корневого исключения недостаточно.
Разрешающее правило родительской папки может повторно включить её содержимое:
после `!docker/` снова исключается `docker/**`, затем разрешены только нужные
файлы контейнера; `docker/secrets/` явно исключается в конце каждого списка.
Эти правила проверены экспортом реального контекста BuildKit, не только разбором текста.
Исключение из сборки не влияет на dev bind mounts, но корневой `.env`
не находится внутри монтируемых `backend/` и `frontend/`.

## Режимы и сервисы

| Сервис | Dev | Prod |
| --- | --- | --- |
| python | Uvicorn reload, bind backend/, инструменты проверки | Uvicorn без reload, только runtime-зависимости |
| node | Vite, bind frontend/, отдельный том node_modules | Одноразовая проверка собранного dist, затем exit 0 |
| nginx | Прокси Vite с HMR и API | Статика SPA и прокси API |
| postgres | Постоянный том, healthcheck | То же |
| valkey | Пароль, без AOF/snapshot, tmpfs | То же |
| rustfs | Закрытый bucket, API/console на loopback | Закрытый bucket, без host ports и console |
| rustfs-init | Создаёт приватный videos и завершается с кодом 0 | То же |

RustFS 1.0.5 содержит CLI rc 0.1.37 без команды Public Access Block, на которую
рассчитывает встроенный bucket bootstrap. Поэтому этот bootstrap не включается.
`rustfs-init` импортирует credentials из secret-файлов в приватный tmpfs-конфиг CLI,
идемпотентно создаёт bucket и проверяет HTTP 403 для анонимного чтения.
Ключи не передаются через argv/RC_HOST; временный конфиг удаляется после выполнения.

В `python/base` есть FFmpeg и ffprobe. Сервис `upload-maintenance` наследует
Python-конфигурацию и запускает Procrastinate для очистки передач. В dev ему
явно заданы target=dev и bind backend: Compose extends не переносит последующие
переопределения python из dev-файла автоматически. Для изменения кода worker
нужен его перезапуск. Миграция 0004 устанавливает очередь и таблицу попыток.
Nginx `/uploads/` пропускает только подписанные PUT частей в RustFS, снимает
cookie и фиксирует upstream Host; байты не идут через FastAPI. Error log для
этого пути отключён, чтобы не писать URI с подписью; access log остаётся безопасным.
Отдельный `media-worker` проверяет исходник локальным ffprobe с чтением всех
сжатых пакетов и вычислением доверенных слепков, строит постоянный план
и кодирует отдельные закрытые AV1/AAC HLS-пакеты. Подключены
[готовность владельца и внутренний master](../backend/readiness.md);
подключены [публикация и защищённая раздача](../backend/media-delivery.md).

Раздача использует внутренний Nginx-путь `/_media_storage/`:
FastAPI проверяет права и подписывает запрос, Nginx читает закрытый RustFS.
Подписи не попадают в браузер и журналы; обычный GET/HEAD и одиночный Range
обрабатываются без прокачивания файла через Python. Плейлисты формирует API.
`./docker/check_delivery dev` (либо `prod` после соответствующей сборки)
проверяет настоящий HTTP-путь в двух временных контейнерах без публикации портов,
с отдельной БД, полным конвейером и уборкой собственных объектов/БД/сессий.
Квалификация использует собранные образы выбранного режима; prod проверяет
отсутствие pytest. API-контейнер ограничен согласованными 2 CPU/8 ГиБ без swap;
служебный Nginx использует реальные snippets продукта, не тестовый обработчик байтов.

HTTP — `127.0.0.1:8080`; `HTTP_BIND`/`HTTP_PORT` меняются в корневом `.env`.
При локальной проверке порт переопределён на 18080, поскольку 8080 занят другим проектом.
`/api/` проксируется в Python со снятием префикса; Compose задаёт
`UVICORN_ROOT_PATH=/api`, Uvicorn передаёт root_path в ASGI. Swagger UI —
`/api/docs`, внешний `/docs` перенаправляет туда; OpenAPI — `/api/openapi.json`.
Прикладной префикс — `/api/v1`. Healthcheck Python проверяет
`/v1/health/ready` с обращением ко всем трём хранилищам.
`COOKIE_SECURE=true` в prod, dev переопределяет на false для локального HTTP.
Настройки cookie и запросов — [жизненный цикл](../backend/request-lifecycle.md).
В dev RustFS доступен на `127.0.0.1:9000`, console — `127.0.0.1:9001`.
Порты PostgreSQL и Valkey на хост не публикуются.
Лимит Nginx 6g оставляет место для multipart; бизнес-лимит файла 5 ГБ
должен отдельно проверяться будущим API загрузки.

```bash
./docker/run dev logs -f
./docker/run dev down
./docker/run prod up --build -d --wait
./docker/run prod down
```

Dev/prod используют одно имя проекта, порт и постоянные тома: перед сменой
режима остановить предыдущий через `down`. Эта команда не удаляет тома.
PostgreSQL монтируется в `/var/lib/postgresql` по схеме образа 18+;
RustFS сохраняет `/data` и `/logs`. Потеря Valkey допустима для кэша и сессий,
задания будут храниться в PostgreSQL. Секреты не встраиваются в образы.
Том `job_error_spool` сохраняет резервный буфер диагностики между контейнерами
по пути `JOB_ERROR_SPOOL_PATH=/var/lib/youtube/job-errors`. Каталог принадлежит
app (0700), файлы событий — 0600; Nginx не имеет доступа к этому тому.
Миграция 0002 создаёт постоянный журнал в PostgreSQL. `upload-maintenance`
переносит буфер и восстанавливает потерянные задачи —
[правила журнала](../backend/background-jobs.md).

## Проверки

После успешного запуска dev:

```bash
./docker/run check
./docker/run audit dev
./docker/run context-check
./docker/run dev exec python pytest
./docker/run dev exec node npm run api:generate
```

`check` включает Compose config, `nginx -t`, проверку CPU-медиазависимостей и упаковки HLS,
а также отдельный контейнер с контролем ресурсов и реальным FFmpeg,
Ruff с правилами `S`, Ruff Formatter,
Basedpyright, Tach (`check` и `check-external --exclude 'tests,src/*/tests'`), pytest, `uv audit --locked`, `npm audit`, ESLint, Prettier,
vue-tsc/Vite build и HTTP smoke check через Nginx (SPA, API docs, OpenAPI,
префикс `/api` и HTTP 404 для `.env`/secret-путей).
Для аудита нужен интернет. pytest проверяет secret-файлы, HTTP-контракты,
сессии/CSRF и реальные PostgreSQL/Valkey/RustFS; бизнес-тесты находятся
в `backend/src/<module>/tests`.

Проверка [check_media.py](../../docker/python/check_media.py) запускается
в работающем пересобранном dev отдельно от pytest:

```bash
./docker/run dev exec -T python python - < docker/python/check_media.py
```

Она не требует сети или внешних видео: проверяет Lavapipe на CPU от app,
отказ отсутствующего ICD, преобразование синтетических PQ/HLG кадров,
AV1 и цветовые поля результатов. Тестовые настройки параллелизма относятся
только к этой короткой пробе, не задают ресурсы production worker.

[check_hls.py](../../docker/python/check_hls.py) также запускается через stdin:

```bash
./docker/run dev exec -T python python - < docker/python/check_hls.py
```

Проба без сети создаёт локальные AV1/fMP4 и отдельный AAC: 24/29,97 FPS,
два малых разрешения, моно/стерео, SDR/PQ и 8/10 бит. Проверяет границы,
независимое декодирование сегментов, общее аудио, а затем использует
[генератор master](../backend/hls.md) и декодирует через полный плейлист.
Проверяет CODECS по выходным AV1/AAC, цвет, FPS, разрешение и битрейты;
пустые/неполные метаданные покрываются модульными тестами генератора.
Границы квалификации — [ADR-0047](../decisions/0047-hls-packaging.md).
Это не проверка браузера или прав доступа.

В ту же пробу включены встроенные SRT/ASS/WebVTT из Matroska и mov_text из MP4:
извлечение выбранной дорожки, язык, UTF-8, подготовка WebVTT-сегментов,
пересечение реплики с границей, пустой хвост, `X-TIMESTAMP-MAP` по реальному
началу видео и общая группа в master. FFmpeg 7.1.5 по умолчанию пропускает
внешние HLS WebVTT при чтении master; поэтому непустые VTT проверяются
отдельно через ffprobe, а через master декодируются видео/аудио.
Ограничение не скрывается успешной проверкой — [подробности](../backend/subtitles.md).

Для изолированных медиа-проверок добавлена отдельная конфигурация
[docker-compose.media.yml](../../docker-compose.media.yml): без secret mounts,
сети/GPU, PostgreSQL/Valkey/RustFS и потребления очереди. `media-tools`
работает от app; CPU=2, RAM=8g и memory+swap=8g по ADR-0051.
Временные данные — дисковый том `media_scratch`, не tmpfs.
Общая политика задана в [docker/media-runtime.yml](../../docker/media-runtime.yml).
Её наследует и постоянный `media-worker` из основной конфигурации: один слот,
сеть для PostgreSQL/RustFS, три секрета БД/хранилища, без зависимости от Valkey
и без секретов сессий/CSRF. В dev задан bind backend и target=dev;
prod наследует тот же base. Миграции 0005–0007 связывают исходник с заданием,
сохраняют безопасный код отказа и номер попытки.
Миграция 0008 добавляет общие записи дорожек/состава и связи каждой передачи;
старые проверки одних заголовков повторно ставятся в очередь обслуживания.
Миграции 0009/0010 добавляют планы, общие выходные дорожки и журнал префиксов
незавершённых попыток с индивидуальными сроками безопасной очистки.
Миграции 0011/0012 подключают подготовку своего изображения и выбор превью
с защитой от запоздавших подтверждений.
Тот же media-worker создаёт общие WebP/тройки, а обслуживание очищает временные
изображения и просроченные попытки. Воспроизводимый prod-прогон —
[контракт превью](../backend/thumbnails.md#проверки).
Миграция 0013 подключает общие WebVTT-задания и ссылки на видеошкалы.
FFmpeg и дочерний Python-упаковщик работают в том же media-слоте/контейнере,
с общей квотой RAM/диска и без новых зависимостей. Prod-скрипт превью теперь
проверяет также встроенный mov_text и закрытый WebVTT —
[контракт субтитров](../backend/subtitles.md#реализация-очереди).
Этап B18 не меняет схему: готовность и master собираются одним SQL-снимком
постоянных дорожек. Тот же prod-скрипт проверяет состояния двух владельцев
и декодирование внутреннего master без Valkey — [границы](../backend/readiness.md).
Миграция 0014 добавляет постоянное намерение удаления оригинала и индексы
зависимостей/кандидатов. Цикл recovery ставит video.cleanup_originals в maintenance
каждые 15 секунд с lock/queueing_lock. DELETE выполняется между двумя транзакциями,
с прежними повторами и журналом. Тот же prod-скрипт проверяет удаление двух
оригиналов и успешный повтор Complete/Refresh при сохранности пакетов —
[условия очистки](../backend/original-cleanup.md).
Миграция 0015 добавляет email-разрешения независимо от регистрации получателя.
Статическая политика Casbin и свежий SQL-снимок защищают метаданные;
prod-прогон проверяет выдачу/отзыв, public/hidden после очистки оригиналов,
также без Valkey/pytest — [границы доступа](../backend/private-access.md).

```bash
./docker/run media build
./docker/run media run --rm -T media-tools python - < docker/python/check_media_resources.py
```

Без локального `.env` для этой команды используется `.env.example`, без init
и создания секретов. Сборка dev: `MEDIA_BUILD_TARGET=dev ./docker/run media build`;
без переменной собирается prod. Переменные `MEDIA_*` и пределы —
[ресурсы медиа](../backend/media-resources.md).
`docker/run check` собирает/запускает короткую пробу в этом отдельном
контейнере; ограничения не применяются к API или upload-maintenance.
Проверка `media-tools` остаётся изолированной; обработка настоящего принятого
исходника проверена отдельно в `media-worker`, включая настоящее кодирование,
декодирование результата и запись закрытых пакетов в RustFS.

Hey API настроен на внутренний OpenAPI Python; при отсутствии бизнес-контракта
генерация не создаёт рабочую интеграцию экранов.

Проверены также API reload, HMR через WebSocket Nginx, сохранность PostgreSQL/RustFS
после перезапуска, потеря Valkey и повторный init без изменения секретов.
В prod проверены собранные assets, cache headers и отсутствие dev-инструментов Python.

## Проверка утечек

`./docker/run audit dev` или `prod` сравнивает реальные значения секретов и их
base64/URL/SHA-256 представления с Compose config, container/image inspect,
историей образов, логами, `/proc/*/environ`, `/proc/*/cmdline`, файлами приложения
и HTTP-ответами. Проверяются также фактические mounts secrets по сервисам.
Вывод содержит только имена секретов/поверхностей, без значений. Последний результат:
в T02 — 51 поверхность dev (включая одноразовый prod Python во время проверки),
совпадений нет; непрочитанных process-файлов нет. Отдельный полный аудит prod
в T01 охватил 40 поверхностей.
Скрытые/секретные HTTP-пути Nginx возвращают 404.

Аудит запускается в bootstrap Python с read-only проектом и Docker socket.
Доступ к Docker socket привилегирован даже при mount `:ro`; используемый скрипт
выполняет inspect и команды чтения только для контейнеров текущего Compose-проекта.
Проверкой подтверждено: администратор Docker может читать `/run/secrets/` через daemon.
Compose Secrets защищает от случайной публикации значений в метаданных, а не от
владельца Docker/хоста. Проверка не является полным аудитом всех слоёв образов
или доказательством отсутствия любых возможных утечек.

`./docker/run context-check` экспортирует scratch-копию контекста каждого Dockerfile
и корневых ignore-правил, проверяет обязательные исходники/lock-файлы и отсутствие
env/secrets/артефактов/реальных значений секретов. Используется отдельный временный
BuildKit builder; его состояние и кэш удаляются даже при неудачной проверке.

## Деплой на чистой Ubuntu

[`install.sh`](../../install.sh) запускается из распакованного архива командой
`bash install.sh` на Ubuntu 24.04 LTS или 26.04 LTS. Нужны root/sudo и сеть для
пакетов и образов. Скрипт устанавливает Make, curl, сертификаты, Docker Engine,
Compose и Buildx; Python/Node остаются внутри контейнеров. Рабочий Docker
используется повторно. Для новой установки настраивается
[официальный apt-репозиторий Docker](https://docs.docker.com/engine/install/ubuntu/)
для noble/resolute; инструкция сверена 2026-11-18.
Конфликтующие установки containerd/runc/Docker автоматически не удаляются.

Установщик задаёт `Использовать сидеры для заполнения тестовыми данными? (Y/n)`;
Enter — да, n — без сидеров, EOF отменяет работу. Затем `docker/run setup`
создаёт недостающие `.env`/secrets, собирает dev, поднимает хранилища, применяет
миграции одноразовым контейнером и только затем запускает приложение и worker.
При повторе сборка завершается до остановки приложения, а старые API/worker
останавливаются перед миграциями, с сохранением хранилищ.
После выбранного сидирования проверяется HTTP readiness через Nginx.
Повтор сохраняет конфигурацию и данные. Пользователь sudo сохраняет владение
создаваемыми файлами; после добавления в группу docker для последующих команд
без sudo нужен новый сеанс. Запуск — HTTP на локальном адресе из `.env`,
как при разработке; домен/TLS и внешний Nginx настраиваются отдельно.

Для уже установленного Docker доступны `./docker/run setup` или прежний ручной
порядок `make init`, `make up`, `./docker/run dev exec -T python alembic upgrade head`.
`make refresh` вызывает только dev: сначала собирает образы, затем останавливает
проект и удаляет его тома postgres_data, job_error_spool и media_scratch.
Это сбрасывает БД, сессии Valkey (tmpfs), буфер старых ошибок и seed-манифест.
RustFS, `.env`, secrets и node_modules сохраняются. После миграций запускается
приложение и полный сидер. При обнаружении контейнеров без dev-конфигурации
или тома с чужой меткой Compose-проекта сброс отклоняется до удаления данных.
[Объём и результаты проверки T11](../tasks/T11-deploy.md),
[согласованные параметры Q09](../tasks/open-questions.md#q09-установка-на-чистой-ubuntu).
CLI ролей реализована: команды dev описаны в
[README для разработчиков](../../README.md#роли-пользователей).
Для существующего аккаунта в production-окружении, например:
`./docker/run prod exec python python -m app.cli role get -- user@example.com`.

## Python-пакеты и точка входа

После ADR-0023 ASGI entry point — `app.main:app`. Wheel включает `app`, `common`
и пять каркасов бизнес-модулей из `backend/src/`; пакет `youtube` удалён.
Контроллеры и маршруты — в `app/http`, общие HTTP-средства — в `common/http`.
Проверки границ выполняются обеими командами Tach в `docker/run check`;
точные ограничения — [архитектура](../backend/architecture.md).
