# YouTube

Учебная копия YouTube: FastAPI + Vue 3/TypeScript.

## Установка и запуск

Требования:

- Ubuntu 24.04 LTS или 26.04 LTS, x86_64.
- Root или sudo, доступ в интернет.
- Для медиаворкера: 2 CPU, 8 ГиБ ОЗУ и до 32 ГиБ рабочего диска;
  дополнительно ресурсы для остальных контейнеров и хранения медиа.

Из корня распакованного проекта:

```bash
bash install.sh
```

Конфигурация — `.env`, все секреты — `docker/secrets/`.

| Сервис | Адрес |
| --- | --- |
| Приложение | http://localhost:8080 |
| Swagger UI | http://localhost:8080/api/docs |
| OpenAPI | http://localhost:8080/api/openapi.json |

Ручная установка:
```
git clone https://github.com/PetkaKahin/YouTube.git
cd YouTube
make init
make up
```

## Тестовые и демонстрационные данные

```bash
make seed
```

5 аккаунтов, 12 готовых природных видео, частичные просмотры, лайки,
подписки, плейлисты и комментарии.

| Email | Пароль | Роль |
| --- | --- | --- |
| `admin.demo@example.com` | `Demo-YouTube-2026` | `admin` |
| `alice.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `bob.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `charlie.demo@example.com` | `Demo-YouTube-2026` | `user` |
| `diana.demo@example.com` | `Demo-YouTube-2026` | `user` |

Prod: `make seed SEED_MODE=prod`.

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
