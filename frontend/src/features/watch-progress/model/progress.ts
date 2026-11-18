import { onUnmounted, ref, watch } from 'vue';
import { useMutation, useQueryClient, type Query } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  isIdentifier,
  unwrap,
  writeV1VideosVideoIdProgressPut,
  type Identifier,
  type VideoDetailsResponse,
} from '../../../shared/api';

type Position = { scope: string; id: Identifier; position: number };

export function useAccountWatchProgress(
  currentVideo: () => Identifier | undefined,
  accessLost: () => void,
) {
  const session = useSession(),
    client = useQueryClient();
  const error = ref(false);
  let pending: Position | undefined,
    failed: Position | undefined,
    running = false,
    controller: AbortController | undefined,
    last: Position | undefined,
    writtenAt = -Infinity,
    mounted = true;
  const allowed = (scope: string) =>
    scope === session.scope &&
    !!session.account &&
    !session.loading &&
    !session.changing &&
    !session.error;
  const mutation = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async (value: Position) => {
      if (!allowed(value.scope)) throw new Error('Viewer changed');
      controller = new AbortController();
      return unwrap(
        await writeV1VideosVideoIdProgressPut({
          path: { video_id: value.id },
          body: { position_seconds: value.position },
          signal: controller.signal,
          keepalive: true,
        }),
      );
    },
  });
  async function drain() {
    if (running) return;
    running = true;
    try {
      while (pending) {
        const value = pending;
        pending = undefined;
        if (!allowed(value.scope)) continue;
        try {
          const result = await mutation.mutateAsync(value);
          if (!allowed(value.scope)) continue;
          failed = undefined;
          error.value = false;
          client.setQueryData<VideoDetailsResponse>(
            ['video', value.scope, value.id],
            (old) => (old ? { ...old, watch_progress: result } : undefined),
          );
          const lists = {
            predicate: (query: Query) =>
              query.queryKey[1] === value.scope &&
              [
                'feed',
                'search',
                'channel-videos',
                'playlist',
                'library-videos',
                'watch-feed',
              ].includes(String(query.queryKey[0])),
          };
          // A first list load can have started before the final write committed.
          // Cancel it before invalidation so Query cannot reuse that old response.
          await client.cancelQueries(lists);
          if (allowed(value.scope)) void client.invalidateQueries(lists);
        } catch (reason) {
          if (!allowed(value.scope) || controller?.signal.aborted) continue;
          if (value.id !== currentVideo()) continue;
          if (reason instanceof ApiError && reason.status === 404) {
            pending = undefined;
            if (mounted) accessLost();
          } else {
            failed = value;
            error.value = true;
          }
        }
      }
    } finally {
      running = false;
      controller = undefined;
    }
  }
  function record(
    scope: string | undefined,
    id: string,
    position: number,
    duration: number,
    flush: boolean,
  ) {
    if (
      !scope ||
      !allowed(scope) ||
      !isIdentifier(id) ||
      !Number.isFinite(position) ||
      position < 0 ||
      !Number.isFinite(duration) ||
      duration <= 0
    )
      return;
    const now = performance.now();
    const value = { scope, id, position: Math.min(position, duration) };
    const same = last?.scope === scope && last.id === id;
    if (same && !failed && last?.position === value.position) return;
    if (!flush && same && now - writtenAt < 5000) return;
    last = value;
    writtenAt = now;
    pending = value;
    void drain();
  }
  function retry() {
    if (running || !failed || !allowed(failed.scope)) return;
    pending = failed;
    failed = undefined;
    error.value = false;
    void drain();
  }
  watch(
    () => [session.scope, session.changing, session.loading, session.error],
    () => {
      pending = undefined;
      failed = undefined;
      last = undefined;
      error.value = false;
      controller?.abort();
    },
    { flush: 'sync' },
  );
  watch(currentVideo, () => {
    failed = undefined;
    error.value = false;
  });
  // The child player flushes before onUnmounted. Let that last request finish;
  // the live session check also protects a queued flush after this page is gone.
  onUnmounted(() => {
    mounted = false;
  });
  return { record, error, retry };
}
