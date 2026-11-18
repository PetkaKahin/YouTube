export function safeReturnTo(value: unknown, origin = window.location.origin) {
  if (
    typeof value !== 'string' ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    [...value].some(
      (character) => character === '\\' || character.charCodeAt(0) < 32,
    )
  )
    return '/';
  try {
    const url = new URL(value, origin);
    const path = decodeURIComponent(url.pathname);
    if (
      url.origin !== origin ||
      path.startsWith('//') ||
      [...path].some(
        (character) =>
          character === '\\' ||
          character.charCodeAt(0) < 32 ||
          character.charCodeAt(0) === 127,
      ) ||
      ['/login', '/register'].includes(path.replace(/\/+$/, '').toLowerCase())
    )
      return '/';
    return url.pathname + url.search + url.hash;
  } catch {
    return '/';
  }
}
