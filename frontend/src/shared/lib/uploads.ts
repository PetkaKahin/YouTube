import { isIdentifier } from '../api/identifiers.ts';
import type { UploadResponse } from '../api';

export const maxVideoBytes = 5_000_000_000;

export function uploadUrl(value: string, origin = window.location.origin) {
  const url = new URL(value, origin);
  if (
    url.origin !== origin ||
    !url.pathname.startsWith('/uploads/') ||
    url.username ||
    url.password ||
    url.hash
  )
    throw new TypeError('Invalid upload URL');
  return url.href;
}

export function validateUpload(
  plan: UploadResponse,
  size: number,
  origin = window.location.origin,
) {
  if (
    !isIdentifier(plan.id) ||
    !Number.isSafeInteger(size) ||
    size < 1 ||
    size > maxVideoBytes ||
    plan.size !== size ||
    !Number.isSafeInteger(plan.part_size) ||
    plan.part_size < 1 ||
    !Number.isSafeInteger(plan.parallel_parts) ||
    plan.parallel_parts < 1 ||
    !['uploading', 'uploaded'].includes(plan.status)
  )
    throw new TypeError('Invalid upload plan');
  const seen = new Set<number>();
  for (const part of plan.parts) {
    if (
      !Number.isSafeInteger(part.number) ||
      part.number < 1 ||
      part.number > Math.ceil(size / plan.part_size) ||
      seen.has(part.number) ||
      part.size !==
        Math.min(plan.part_size, size - (part.number - 1) * plan.part_size)
    )
      throw new TypeError('Invalid upload part');
    seen.add(part.number);
    uploadUrl(part.url, origin);
  }
  if (plan.status === 'uploaded' && plan.parts.length)
    throw new TypeError('Invalid uploaded plan');
  return plan;
}

function putPart(
  url: string,
  body: Blob,
  signal: AbortSignal,
  progress: (loaded: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', uploadUrl(url));
    // PUT has no useful response body; avoid implicit XML parsing of empty replies.
    xhr.responseType = 'text';
    xhr.withCredentials = false;
    const abort = () => xhr.abort();
    const finish = (error?: Error) => {
      signal.removeEventListener('abort', abort);
      if (error) reject(error);
      else resolve();
    };
    signal.addEventListener('abort', abort, { once: true });
    xhr.upload.onprogress = (event) =>
      progress(Math.min(event.loaded, body.size));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        progress(body.size);
        finish();
      } else finish(new Error('Upload part failed'));
    };
    xhr.onerror = () => finish(new Error('Upload network failure'));
    xhr.onabort = () => finish(new DOMException('Aborted', 'AbortError'));
    xhr.send(body);
  });
}

export async function transferParts(
  file: File,
  plan: UploadResponse,
  signal: AbortSignal,
  progress: (loaded: number) => void,
) {
  validateUpload(plan, file.size);
  const loaded = new Map(plan.parts.map((part) => [part.number, 0]));
  const accepted =
    file.size - plan.parts.reduce((sum, part) => sum + part.size, 0);
  const report = () =>
    progress(
      accepted + [...loaded.values()].reduce((sum, value) => sum + value, 0),
    );
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  let next = 0;
  report();
  try {
    await Promise.all(
      Array.from(
        { length: Math.min(plan.parallel_parts, plan.parts.length) },
        async () => {
          while (next < plan.parts.length) {
            const part = plan.parts[next++]!;
            const start = (part.number - 1) * plan.part_size;
            await putPart(
              part.url,
              file.slice(start, start + part.size),
              controller.signal,
              (value) => {
                loaded.set(part.number, value);
                report();
              },
            );
          }
        },
      ),
    );
  } catch (error) {
    controller.abort();
    throw error;
  } finally {
    signal.removeEventListener('abort', abort);
  }
}
