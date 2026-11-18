import { client } from './generated/client.gen';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public fields: { location: (string | number)[]; code: string }[] = [],
  ) {
    super(code);
  }
}

export function configureApi(
  onUnauthorized: () => void,
  currentScope: () => string = () => '',
) {
  const scopes = new WeakMap<Request, string>();
  const scopedFetch: typeof fetch = (input, init) => {
    if (
      input instanceof Request &&
      (input.signal.aborted || scopes.get(input) !== currentScope())
    )
      return Promise.reject(new DOMException('Viewer changed', 'AbortError'));
    return globalThis.fetch(input, init);
  };
  client.setConfig({
    baseUrl: '/api',
    credentials: 'same-origin',
    fetch: scopedFetch,
  });
  client.interceptors.request.use(async (request) => {
    const scope = currentScope();
    scopes.set(request, scope);
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
      if (
        !document.cookie
          .split('; ')
          .some((value) => value.startsWith('csrftoken='))
      ) {
        await fetch('/api/v1/auth/me', {
          credentials: 'same-origin',
          cache: 'no-store',
        });
      }
      const cookie = document.cookie
        .split('; ')
        .find((value) => value.startsWith('csrftoken='));
      if (cookie)
        request.headers.set(
          'X-CSRFToken',
          decodeURIComponent(cookie.slice('csrftoken='.length)),
        );
    }
    if (request.signal.aborted || scope !== currentScope())
      throw new DOMException('Viewer changed', 'AbortError');
    return request;
  });
  client.interceptors.response.use((response, request) => {
    if (
      response.status === 401 &&
      !request.signal.aborted &&
      scopes.get(request) === currentScope() &&
      !request.url.endsWith('/auth/login') &&
      !request.url.endsWith('/auth/me')
    )
      onUnauthorized();
    return response;
  });
}

export function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response?: Response;
}): T {
  if (result.error || !result.response?.ok) {
    const error = result.error as
      { code?: string; errors?: ApiError['fields'] } | undefined;
    throw new ApiError(
      result.response?.status ?? 0,
      error?.code ?? 'network_error',
      error?.errors ?? [],
    );
  }
  return result.data as T;
}

export function apiMessage(error: unknown) {
  if (!(error instanceof ApiError))
    return 'Не удалось выполнить запрос. Попробуйте ещё раз.';
  if (error.code === 'invalid_credentials') return 'Неверная почта или пароль.';
  if (error.code === 'csrf_failed')
    return 'Обновите страницу и повторите действие.';
  if (error.status === 401) return 'Войдите в аккаунт, чтобы продолжить.';
  if (error.status === 403) return 'Нет доступа к этому действию.';
  if (error.status === 404) return 'Объект недоступен.';
  if (error.status === 409)
    return 'Данные уже используются или были изменены. Проверьте форму.';
  if (error.status === 422) return 'Проверьте введённые данные.';
  return 'Не удалось связаться с сервером. Попробуйте ещё раз.';
}
