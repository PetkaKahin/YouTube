# Начальная схема тестовой БД

Статус: `done`. [Решение пользователя](../decisions/0069-development-database-and-initial-schema.md).

История 0001–0028 заменена начальной схемой: один файл на таблицу
с окончательными колонками, ограничениями и индексами. Дорожки и загруженные
превью создаются вместе из-за взаимных внешних ключей. Схема Procrastinate
устанавливается отдельно и сохраняется при downgrade, как прежде.
Всего 30 таблиц приложения, 29 файлов их определения и один файл схемы очереди;
текущая ревизия — `initial_030`. Число файлов определяется таблицами,
а не количеством прошлых изменений.

Текущая dev-БД не требует переноса данных: после смены истории применяется
`make refresh`. Не используйте stamp для объявления старой схемы новой.
Миграции независимы от ORM-классов и не меняют бизнес-правила и HTTP-контракты.

Подключение из IDE: хост `127.0.0.1`, порт из POSTGRES_PORT, имя БД из
POSTGRES_DB и пользователь POSTGRES_USER — в корневом локальном `.env`.
Пароль берётся из `docker/secrets/postgres_password`; все секреты остаются
в `docker/secrets/` по [ADR-0070](../decisions/0070-secret-files-only.md).

## Проверка

Прошли четыре профильных теста [начальной схемы](../../backend/tests/test_initial_migrations.py):
создание таблиц без цепочки изменений колонок, соответствие ORM, даты/ограничения
реакций и полный downgrade/upgrade. Каталог новой dev-БД совпал с сохранённым
каталогом до свёртки по колонкам, ограничениям, индексам, последовательностям
и функциям. `make refresh` пересоздал dev: 5 аккаунтов, 12 видео и демоактивность.

TCP-подключение к `127.0.0.1:5432` с текущим паролем подтверждено.
В prod PostgreSQL не опубликован; пароль отсутствует в environment сервисов.
Проверка установщика после изменений — [T11](../tasks/T11-deploy.md).

## Таблицы и файлы

| Таблица | Начальная миграция |
| --- | --- |
| `background_job_errors` | [0002_background_job_errors.py](../../backend/migrations/versions/0002_background_job_errors.py) |
| `background_job_retry_items` | [0007_background_job_retry_items.py](../../backend/migrations/versions/0007_background_job_retry_items.py) |
| `background_job_retry_requests` | [0003_background_job_retry_requests.py](../../backend/migrations/versions/0003_background_job_retry_requests.py) |
| `channel_playlist_entries` | [0018_channel_playlist_entries.py](../../backend/migrations/versions/0018_channel_playlist_entries.py) |
| `channel_playlists` | [0010_channel_playlists.py](../../backend/migrations/versions/0010_channel_playlists.py) |
| `channel_profiles` | [0008_channel_profiles.py](../../backend/migrations/versions/0008_channel_profiles.py) |
| `channel_subscriptions` | [0011_channel_subscriptions.py](../../backend/migrations/versions/0011_channel_subscriptions.py) |
| `comment_entries` | [0012_comment_entries.py](../../backend/migrations/versions/0012_comment_entries.py) |
| `comment_likes` | [0019_comment_likes.py](../../backend/migrations/versions/0019_comment_likes.py) |
| `moderation_accesses` | [0020_moderation_accesses.py](../../backend/migrations/versions/0020_moderation_accesses.py) |
| `moderation_decisions` | [0021_moderation_decisions.py](../../backend/migrations/versions/0021_moderation_decisions.py) |
| `moderation_reports` | [0013_moderation_reports.py](../../backend/migrations/versions/0013_moderation_reports.py) |
| `user_accounts` | [0004_user_accounts.py](../../backend/migrations/versions/0004_user_accounts.py) |
| `video_encoded_tracks` | [0022_video_encoding_and_thumbnails.py](../../backend/migrations/versions/0022_video_encoding_and_thumbnails.py) |
| `video_encoding_attempts` | [0024_video_encoding_attempts.py](../../backend/migrations/versions/0024_video_encoding_attempts.py) |
| `video_likes` | [0014_video_likes.py](../../backend/migrations/versions/0014_video_likes.py) |
| `video_media_contents` | [0006_video_media_contents.py](../../backend/migrations/versions/0006_video_media_contents.py) |
| `video_media_plans` | [0027_video_media_plans.py](../../backend/migrations/versions/0027_video_media_plans.py) |
| `video_media_tracks` | [0005_video_media_tracks.py](../../backend/migrations/versions/0005_video_media_tracks.py) |
| `video_plan_tracks` | [0029_video_plan_tracks.py](../../backend/migrations/versions/0029_video_plan_tracks.py) |
| `video_private_grants` | [0015_video_private_grants.py](../../backend/migrations/versions/0015_video_private_grants.py) |
| `video_publications` | [0009_video_publications.py](../../backend/migrations/versions/0009_video_publications.py) |
| `video_source_processing` | [0023_video_source_processing.py](../../backend/migrations/versions/0023_video_source_processing.py) |
| `video_source_tracks` | [0028_video_source_tracks.py](../../backend/migrations/versions/0028_video_source_tracks.py) |
| `video_subtitle_links` | [0030_video_subtitle_links.py](../../backend/migrations/versions/0030_video_subtitle_links.py) |
| `video_thumbnail_choices` | [0025_video_thumbnail_choices.py](../../backend/migrations/versions/0025_video_thumbnail_choices.py) |
| `video_thumbnail_preparations` | [0026_video_thumbnail_preparations.py](../../backend/migrations/versions/0026_video_thumbnail_preparations.py) |
| `video_thumbnail_uploads` | [0022_video_encoding_and_thumbnails.py](../../backend/migrations/versions/0022_video_encoding_and_thumbnails.py) |
| `video_uploads` | [0016_video_uploads.py](../../backend/migrations/versions/0016_video_uploads.py) |
| `video_watch_progress` | [0017_video_watch_progress.py](../../backend/migrations/versions/0017_video_watch_progress.py) |
