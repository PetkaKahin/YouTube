export function mediaUrl(
  value: string,
  prefix: string,
  origin = window.location.origin,
): string {
  const url = new URL(value, origin);
  if (
    url.origin !== origin ||
    !url.pathname.startsWith(prefix) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new TypeError('Invalid media URL');
  return url.href;
}

export function playbackTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  return whole >= 3600
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`
    : `${minutes}:${String(whole % 60).padStart(2, '0')}`;
}

export function qualityName(
  width: number,
  height: number,
  range: string,
  ready: readonly { quality: number; range: string }[],
) {
  const candidates = ready.filter(
    (item) => item.range.toUpperCase() === range.toUpperCase(),
  );
  if (!candidates.length) return undefined;
  const bound = Math.max(
    Math.min(width, height),
    (Math.max(width, height) * 9) / 16,
  );
  const match = [...candidates].sort(
    (a, b) => Math.abs(a.quality - bound) - Math.abs(b.quality - bound),
  )[0]!;
  return `${match.quality}p${range.toUpperCase() === 'SDR' ? '' : ' HDR'}`;
}
