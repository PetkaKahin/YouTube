export function processingFailure(code: string | null | undefined) {
  switch (code) {
    case 'duration_limit':
      return 'Продолжительность видео превышает 60 минут.';
    case 'source_size_changed':
      return 'Размер принятого исходника не совпадает с размером загрузки.';
    case 'no_video':
      return 'Исходный файл не содержит видеодорожки.';
    case 'invalid_duration':
      return 'Не удалось определить длительность видео.';
    case 'invalid_media':
      return 'Не удалось прочитать видео. Файл повреждён или его формат не поддерживается.';
    default:
      return 'Сервер не смог подготовить видео.';
  }
}
