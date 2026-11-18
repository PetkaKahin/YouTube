<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  choicesV1VideosVideoIdPlaylistsGet,
  mineV1PlaylistsMineGet,
  selectV1VideosVideoIdPlaylistsPut,
  isIdentifier,
  unwrap,
  type Identifier,
  type PlaylistListResponse,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';
import { AppIcon, StudioDialog } from '../../../shared/ui';

const props = defineProps<{ videoId?: Identifier; disabled?: boolean }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient();
const open = ref(false),
  pending = ref(false),
  dirty = ref(false),
  error = ref(''),
  notice = ref(''),
  denied = ref(false);
const changes = ref(new Map<Identifier, boolean>()),
  staged = ref(new Map<Identifier, boolean>());
const begin = useOperations(() => `${session.scope}:${props.videoId ?? 'new'}`);
const key = computed(() => [
  'video-playlists',
  session.scope,
  props.videoId ?? 'new',
]);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(() => !!session.account && !denied.value),
  gcTime: 0,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      props.videoId
        ? await choicesV1VideosVideoIdPlaylistsGet({
            path: { video_id: props.videoId },
            query: { cursor: pageParam },
            signal,
          })
        : await mineV1PlaylistsMineGet({
            query: { cursor: pageParam },
            signal,
          }),
    );
    if (page.items.some((item) => !isIdentifier(item.id)))
      throw new Error('Invalid playlist identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
});
const items = computed(() => {
  if (denied.value) return [];
  const seen = new Set<string>();
  return (
    query.data.value?.pages
      .flatMap((page) => page.items)
      .filter((item) => !seen.has(item.id) && !!seen.add(item.id)) ?? []
  );
});
function reset() {
  open.value = false;
  pending.value = false;
  dirty.value = false;
  error.value = '';
  notice.value = '';
  changes.value.clear();
  staged.value.clear();
  denied.value = false;
}
watch(
  () => props.videoId,
  (_, previous) => {
    if (previous) reset();
  },
);
watch(() => session.scope, reset);
function failure(cause: unknown) {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    reset();
    denied.value = true;
    client.removeQueries({ queryKey: key.value, exact: true });
    emit('accessLost');
  }
}
watch(query.error, failure);
function show() {
  changes.value = new Map(staged.value);
  error.value = '';
  open.value = true;
}
function selected(id: Identifier, base: boolean) {
  return changes.value.get(id) ?? base;
}
async function apply(
  target: Identifier,
  values: Map<Identifier, boolean>,
  signal?: AbortSignal,
) {
  if (!values.size && !dirty.value) return;
  if (pending.value) throw new Error('Playlists are already saving');
  pending.value = true;
  const work = begin(signal);
  try {
    // PUT replaces the complete set: read every page before preserving untouched selections.
    await query.refetch({ throwOnError: true });
    work.check();
    while (query.hasNextPage.value) {
      await query.fetchNextPage({ throwOnError: true });
      work.check();
    }
    const ids = new Set(
      items.value.filter((item) => item.selected).map((item) => item.id),
    );
    for (const [id, value] of values) {
      if (value) ids.add(id);
      else ids.delete(id);
    }
    const result = unwrap(
      await selectV1VideosVideoIdPlaylistsPut({
        path: { video_id: target },
        body: { playlist_ids: [...ids] },
        signal: work.signal,
      }),
    );
    work.check();
    if (result.playlist_ids.some((id) => !isIdentifier(id)))
      throw new Error('Invalid playlist identifier');
    const confirmed = new Set(result.playlist_ids);
    client.setQueryData<InfiniteData<PlaylistListResponse>>(
      key.value,
      (old) =>
        old && {
          ...old,
          pages: old.pages.map((page) => ({
            ...page,
            items: page.items.map((item) => ({
              ...item,
              selected: confirmed.has(item.id),
            })),
          })),
        },
    );
    values.clear();
    staged.value.clear();
    dirty.value = false;
    notice.value = 'Плейлисты сохранены';
  } catch (cause) {
    failure(cause);
    throw cause;
  } finally {
    work.finish();
    if (work.current()) pending.value = false;
  }
}
async function done() {
  if (pending.value) return;
  const identity = `${session.scope}:${props.videoId}`;
  try {
    if (props.videoId) await apply(props.videoId, changes.value);
    else {
      staged.value = new Map(changes.value);
      dirty.value = staged.value.size > 0;
      notice.value = dirty.value
        ? 'Выбор будет применён при сохранении видео'
        : '';
    }
    open.value = false;
  } catch (cause) {
    if (identity === `${session.scope}:${props.videoId}`)
      error.value = apiMessage(cause);
  }
}
async function persist(target: Identifier, signal?: AbortSignal) {
  await apply(target, staged.value, signal);
}
function refresh() {
  void query.refetch();
}
defineExpose({ persist });
onBeforeUnmount(reset);
</script>
<template>
  <section class="video-playlists">
    <h4>Плейлисты</h4>
    <p>Добавьте видео в один или несколько плейлистов.</p>
    <button
      type="button"
      class="playlist-choose"
      :disabled="disabled || pending || denied"
      @click="show"
    >
      Выбрать плейлисты<AppIcon name="down" />
    </button>
    <p v-if="notice" class="playlist-notice" role="status">{{ notice }}</p>
    <StudioDialog
      :open="open"
      :busy="pending"
      title="Плейлисты"
      @close="open = false"
    >
      <p v-if="query.isPending.value" role="status">Загружаем плейлисты…</p>
      <p v-else-if="query.isError.value" class="form-error" role="alert">
        {{ apiMessage(query.error.value) }}
        <button type="button" class="text-link" @click="query.refetch()">
          Повторить
        </button>
      </p>
      <p v-else-if="!items.length">Пока нет плейлистов.</p>
      <div class="playlist-choices">
        <label v-for="playlist in items" :key="playlist.id"
          ><input
            type="checkbox"
            :checked="selected(playlist.id, playlist.selected)"
            :disabled="pending"
            :data-playlist-id="playlist.id"
            @change="
              changes.set(
                playlist.id,
                ($event.target as HTMLInputElement).checked,
              )
            " /><span>{{ playlist.title }}</span
          ><AppIcon
            :name="
              playlist.visibility === 'private'
                ? 'lock'
                : playlist.visibility === 'unlisted'
                  ? 'link'
                  : 'globe'
            "
        /></label>
      </div>
      <button
        v-if="query.hasNextPage.value"
        type="button"
        class="text-link"
        :disabled="query.isFetching.value || pending"
        @click="query.fetchNextPage()"
      >
        {{ query.isFetching.value ? 'Загружаем…' : 'Показать ещё' }}
      </button>
      <div class="playlist-create">
        <slot
          name="create"
          :refresh="refresh"
          :disabled="pending || props.disabled"
        />
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <template #footer
        ><button
          type="button"
          class="button button--secondary"
          :disabled="pending"
          @click="open = false"
        >
          Отмена</button
        ><button
          type="button"
          class="button playlist-done"
          :disabled="pending || query.isPending.value || query.isError.value"
          @click="done"
        >
          {{ pending ? 'Сохраняем…' : 'Готово' }}
        </button></template
      >
    </StudioDialog>
  </section>
</template>
<style scoped lang="scss">
.video-playlists {
  margin: 24px 0;
  h4 {
    margin: 0 0 8px;
    font-size: 16px;
  }
  > p {
    color: var(--color-muted);
    font-size: 14px;
    line-height: 20px;
  }
}
.playlist-choose {
  border: 1px solid var(--studio-input-border);
  background: transparent;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  gap: 24px;
  align-items: center;
  cursor: pointer;
}
.playlist-choices label {
  display: flex;
  gap: 16px;
  align-items: center;
  padding: 12px 0;
  cursor: pointer;
  input {
    width: 20px;
    height: 20px;
    flex-shrink: 0;
    accent-color: var(--color-text);
  }
  span {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  svg {
    width: 20px;
    height: 20px;
    color: var(--color-muted);
    flex-shrink: 0;
  }
}
.playlist-create {
  margin-top: 16px;
}
.playlist-done {
  background: var(--color-text);
  color: var(--color-background);
}
.playlist-notice {
  font-size: 12px;
}
</style>
