import type { VideoSettingsResponse } from '../../../shared/api';

export function videoState(video: VideoSettingsResponse) {
  if (video.moderation === 'hidden') return 'Скрыто';
  if (video.upload !== 'uploaded')
    return (
      (
        {
          pending: 'Ожидает загрузки',
          uploading: 'Загрузка файла',
          failed: 'Ошибка загрузки',
        } as Record<string, string>
      )[video.upload] ?? 'Загрузка не завершена'
    );
  return (
    (
      {
        pending: 'Ожидает обработки',
        processing: 'Обрабатывается',
        ready: 'Готово',
        partial: 'Частично готово',
        failed: 'Ошибка обработки',
      } as Record<string, string>
    )[video.processing] ?? 'Состояние неизвестно'
  );
}
export function canWatchOwnedVideo(video: VideoSettingsResponse) {
  return (
    video.publication === 'published' &&
    video.moderation === 'visible' &&
    video.upload === 'uploaded' &&
    ['ready', 'partial'].includes(video.processing)
  );
}
