<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/vue-query';
import { useRoute, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  savedPlaylistsV1VideosVideoIdSavedPlaylistsGet,
  savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';
import { AppIcon, AnchoredDropdown } from '../../../shared/ui';
const props = defineProps<{ videoId: Identifier }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient(),
  route = useRoute(),
  router = useRouter();
const open = ref(false),
  pending = ref(false),
  error = ref('');
const button = ref<HTMLButtonElement>();
let restoreFocus = true;
const begin = useOperations(() => `${session.scope}:${props.videoId}`);
const key = computed(
  () => ['saved-playlists', session.scope, props.videoId] as const,
);
const ready = computed(
  () => !session.loading && !session.changing && !session.error,
);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(() => ready.value && !!session.account && !pending.value),
  initialPageParam: undefined as string | undefined,
  gcTime: 0,
  retry: false,
  queryFn: async ({ queryKey: [, , id], pageParam, signal }) => {
    const page = unwrap(
      await savedPlaylistsV1VideosVideoIdSavedPlaylistsGet({
        path: { video_id: id },
        query: { cursor: pageParam },
        signal,
      }),
    );
    if (
      page.items.some((p) => !isIdentifier(p.id) || !isIdentifier(p.channel_id))
    )
      throw new Error('Invalid playlist identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
});
type Page = NonNullable<typeof query.data.value>['pages'][number];
const saved = computed(
  () =>
    ready.value &&
    session.account &&
    !query.isError.value &&
    query.isFetchedAfterMount.value &&
    query.data.value?.pages[0]?.saved,
);
const items = computed(() =>
  ready.value &&
  session.account &&
  (!query.isError.value || query.isFetchNextPageError.value) &&
  query.isFetchedAfterMount.value
    ? [
        ...new Map(
          query.data.value?.pages
            .flatMap((p) => p.items)
            .map((p) => [p.id, p]) ?? [],
        ).values(),
      ]
    : [],
);
function denied(cause: unknown) {
  if (
    cause instanceof ApiError &&
    (cause.status === 404 ||
      (cause.status === 403 && cause.code !== 'csrf_failed'))
  ) {
    open.value = false;
    client.removeQueries({ queryKey: key.value, exact: true });
    emit('accessLost');
  }
}
watch(query.error, denied);
watch(
  key,
  () => {
    open.value = false;
    error.value = '';
    pending.value = false;
  },
  { flush: 'sync' },
);
async function toggleOpen() {
  if (!ready.value || pending.value) return;
  if (!session.account) {
    await router.push({ path: '/login', query: { returnTo: route.fullPath } });
    return;
  }
  restoreFocus = true;
  open.value = !open.value;
  if (!open.value) return;
  error.value = '';
  void query.refetch();
}
async function select(id: Identifier, selected: boolean) {
  if (pending.value || !ready.value || !session.account) return;
  const operation = begin(),
    requestKey = key.value;
  pending.value = true;
  error.value = '';
  try {
    await client.cancelQueries({ queryKey: requestKey, exact: true });
    operation.check();
    const result = unwrap(
      await savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut({
        path: { playlist_id: id, video_id: props.videoId },
        body: { selected },
        signal: operation.signal,
      }),
    );
    operation.check();
    if (
      result.playlist_id !== id ||
      result.video_id !== props.videoId ||
      result.selected !== selected
    )
      throw new Error('Invalid saved selection');
    client.setQueryData<InfiniteData<Page>>(requestKey, (old) =>
      old
        ? {
            ...old,
            pages: old.pages.map((page) => ({
              ...page,
              saved: result.saved,
              items: page.items.map((item) =>
                item.id === id ? { ...item, selected: result.selected } : item,
              ),
            })),
          }
        : undefined,
    );
    void client.invalidateQueries({
      predicate: (q) =>
        q.queryKey[1] === session.scope &&
        [
          'library-playlists',
          'playlist',
          'video-playlists',
          'studio-playlists',
        ].includes(String(q.queryKey[0])),
    });
  } catch (cause) {
    if (operation.current()) {
      error.value = apiMessage(cause);
      denied(cause);
    }
  } finally {
    if (operation.current()) pending.value = false;
    operation.finish();
  }
}
function created() {
  void client.resetQueries({ queryKey: key.value, exact: true });
  void client.invalidateQueries({
    queryKey: ['library-playlists', session.scope],
  });
}
defineExpose({ created });
function close(restore = true) {
  restoreFocus = restore;
  open.value = false;
}
onBeforeUnmount(() => {
  open.value = false;
});
watch(open, async (value, old) => {
  if (!value && old && restoreFocus) {
    await nextTick();
    button.value?.focus();
  }
});
</script>
<template>
  <div class="save-video">
    <button
      ref="button"
      type="button"
      class="icon-button save-video__trigger"
      aria-label="Сохранить в плейлист"
      :aria-pressed="!!saved"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :disabled="!ready"
      @click="toggleOpen"
    >
      <AppIcon name="bookmark" />
    </button>
    <AnchoredDropdown
      :open="open"
      title="Выберите плейлист"
      :anchor="button"
      @close="close"
    >
      <p
        v-if="
          query.isPending.value ||
          (!query.isFetchedAfterMount.value && !query.isError.value)
        "
        role="status"
      >
        Загружаем плейлисты…
      </p>
      <div
        v-else-if="query.isError.value && !query.isFetchNextPageError.value"
        role="alert"
      >
        <p>Не удалось загрузить плейлисты.</p>
        <button type="button" class="text-link" @click="query.refetch()">
          Повторить
        </button>
      </div>
      <p v-else-if="!items.length">У вас пока нет плейлистов.</p>
      <div v-else class="save-video__list">
        <button
          v-for="item in items"
          :key="item.id"
          type="button"
          class="save-video__item"
          :data-playlist-id="item.id"
          :aria-pressed="item.selected"
          :disabled="pending || query.isFetching.value"
          @click="select(item.id, !item.selected)"
        >
          <AppIcon name="playlist" /><span
            ><strong>{{ item.title }}</strong
            ><small>{{
              {
                public: 'Открытый доступ',
                unlisted: 'Доступ по ссылке',
                private: 'Ограниченный доступ',
              }[item.visibility]
            }}</small></span
          ><AppIcon :name="item.selected ? 'check' : 'bookmark'" />
        </button>
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button
        v-if="
          query.hasNextPage.value &&
          (!query.isError.value || query.isFetchNextPageError.value)
        "
        type="button"
        class="text-link save-video__more"
        :disabled="pending || query.isFetching.value"
        @click="query.fetchNextPage()"
      >
        {{
          query.isFetching.value
            ? 'Загружаем…'
            : query.isFetchNextPageError.value
              ? 'Повторить загрузку'
              : 'Ещё плейлисты'
        }}
      </button>
      <template #footer
        ><slot :disabled="pending || query.isFetching.value"
      /></template>
    </AnchoredDropdown>
  </div>
</template>
<style scoped lang="scss">
.save-video {
  &__trigger {
    width: 48px;
    height: 48px;
    :deep(svg) {
      width: 28px;
      height: 28px;
    }
    background: var(--color-surface);
    &[aria-pressed='true'] :deep(svg) {
      fill: currentColor;
    }
  }
  &__list {
    display: grid;
    max-height: 50dvh;
    overflow: auto;
  }
  &__item {
    display: flex;
    align-items: center;
    gap: 12px;
    border: 0;
    background: transparent;
    padding: 10px 0;
    width: 100%;
    text-align: left;
    cursor: pointer;
    &:hover {
      background: var(--color-hover);
    }
    > svg {
      flex-shrink: 0;
    }
    > span {
      flex: 1;
      min-width: 0;
    }
    strong,
    small {
      display: block;
      overflow-wrap: anywhere;
    }
    strong {
      font-size: 14px;
      font-weight: 400;
    }
    small {
      font-size: 12px;
      color: var(--color-muted);
      margin-top: 4px;
    }
  }
  &__more {
    margin-top: 16px;
  }
}
</style>
