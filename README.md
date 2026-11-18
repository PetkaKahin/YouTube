# YouTube

Учебная копия YouTube: FastAPI + Vue 3/TypeScript, тёмная тема, ПК и мобильный веб.
Запуск, сборка и проверки — через Docker.

## Установка и запуск

На Ubuntu 24.04 LTS или 26.04 LTS распакуйте архив и выполните из корня проекта:

```bash
bash install.sh
```

Установщик использует root/sudo, устанавливает Docker, Compose, Buildx и Make,
готовит конфигурацию, применяет миграции и запускает dev по HTTP.
На вопрос `Использовать сидеры для заполнения тестовыми данными? (Y/n)`
Enter означает «да». Для загрузки пакетов и образов нужен интернет.
Повтор сохраняет данные, существующий `.env` и secrets в `docker/secrets/`.
В `.env` настраиваются порты и ресурсы; домен и HTTPS настраиваются отдельно.
[Ручной запуск с установленным Docker](docs/infrastructure/docker.md).

При стандартном `HTTP_PORT=8080`:

| Сервис | Адрес |
| --- | --- |
| Приложение | http://localhost:8080 |
| Swagger UI | http://localhost:8080/api/docs |
| OpenAPI | http://localhost:8080/api/openapi.json |

## Тестовые и демонстрационные данные

После запуска и применения миграций:

```bash
make seed
```

Будут созданы 5 тестовых аккаунтов с каналами и 12 готовых природных видео.
Данные для входа:

| Email | Пароль | Роль |
| --- | --- | --- |
| `admin.demo@example.com` | `Demo-YouTube-2026` | `admin` |
| `alice.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `bob.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `charlie.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `diana.demo@example.com` | `Demo-YouTube-2026` | `user` |

Четыре автора получают по три ролика, у администратора канал пустой.
Видео хранятся в проекте готовыми пакетами:
при сидировании интернет и повторное кодирование не нужны.
Повторный запуск сохраняет аккаунты и ID роликов.
Аккаунты получают частичные просмотры, лайки, подписки, комментарии и
плейлисты разных режимов доступа. Повтор не дублирует комментарии/плейлисты
и сохраняет уже записанные позиции просмотра.
Для работающего prod: `make seed SEED_MODE=prod`.
[Подробнее о сидере](docs/tasks/demo-seed.md).

## Основные команды

| Команда | Назначение |
| --- | --- |
| `make help` | Все доступные команды |
| `make start` | Запустить dev без пересборки |
| `make refresh` | **Стереть БД dev**, применить миграции и заново заполнить демоданными |
| `make stop` | Остановить контейнеры |
| `make down` | Удалить контейнеры, сохранив данные в томах |
| `make logs` | Смотреть логи |
| `make frontend-build` | Проверить типы и собрать фронтенд |
| `make test` | Запустить тесты бэкенда |
| `make check` | Выполнить полный набор проверок |

## Роли пользователей

Назначение роли существующему аккаунту в dev:

```bash
make role get EMAIL=admin.demo@example.com
make role set EMAIL=alice.demo@example.com ROLE=moderator
```

Доступные роли: `user`, `moderator`, `admin`.

## Документация

[Карта проекта](docs/README.md) · [ТЗ](docs/project/requirements.md) ·
[Docker и эксплуатация](docs/infrastructure/docker.md) · [Правила проверок](docs/testing.md).
