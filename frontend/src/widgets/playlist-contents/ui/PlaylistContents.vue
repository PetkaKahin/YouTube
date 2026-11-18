<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  VideoCard,
  validateCards,
  useVideoCardVerification,
} from '../../../entities/video';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  unwrap,
  viewV1PlaylistsPlaylistIdGet,
  type Identifier,
} from '../../../shared/api';
import { useNextPage } from '../../../shared/lib/pagination';
import { AppIcon, PageState } from '../../../shared/ui';
const props = defineProps<{
  playlistId: Identifier;
  channelId: Identifier;
  tag: string;
}>();
const session = useSession(),
  client = useQueryClient();
const next = ref<HTMLElement>(),
  denied = ref(false);
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
const key = computed(
  () =>
    [
      'playlist',
      session.scope,
      props.playlistId,
      props.channelId,
      props.tag.toLowerCase(),
    ] as const,
);
let disposed = false;
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(() => identityReady.value && !denied.value),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({
    queryKey: [, , playlistId, channelId, tag],
    signal,
    pageParam,
  }) => {
    const page = validateCards(
      unwrap(
        await viewV1PlaylistsPlaylistIdGet({
          path: { playlist_id: playlistId },
          query: { cursor: pageParam },
          signal,
        }),
      ),
    );
    if (
      page.playlist.id !== playlistId ||
      page.playlist.channel_id !== channelId ||
      !isIdentifier(page.channel.id) ||
      page.channel.id !== channelId ||
      page.channel.tag.toLowerCase() !== tag
    )
      throw new ApiError(404, 'not_found');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
  gcTime: 0,
});
const unavailable = computed(
  () =>
    denied.value ||
    (query.error.value instanceof ApiError &&
      [401, 403, 404].includes(query.error.value.status)),
);
const playlist = computed(() =>
  identityReady.value &&
  query.isFetchedAfterMount.value &&
  !unavailable.value &&
  (!query.isError.value || query.isFetchNextPageError.value)
    ? query.data.value?.pages[0]?.playlist
    : undefined,
);
const verifyPreviewAccess = useVideoCardVerification(
  () => query.refetch(),
  () => query.isError.value,
);
const videos = computed(() => {
  const seen = new Set<string>();
  return playlist.value
    ? (query.data.value?.pages
        .flatMap((page) => page.items)
        .filter((item) => !seen.has(item.id) && !!seen.add(item.id)) ?? [])
    : [];
});
watch(query.error, (error) => {
  if (
    !disposed &&
    !denied.value &&
    error instanceof ApiError &&
    [401, 403, 404].includes(error.status)
  ) {
    denied.value = true;
    const oldQuery = client
      .getQueryCache()
      .find({ queryKey: key.value, exact: true });
    const filter = { predicate: (item: unknown) => item === oldQuery };
    void client.cancelQueries(filter);
    client.removeQueries(filter);
  }
});
watch(playlist, (value) => {
  if (!disposed)
    document.title = value ? `${value.title} - YouTube` : 'YouTube';
});
onBeforeUnmount(() => {
  disposed = true;
  document.title = 'YouTube';
});
function more() {
  if (
    query.hasNextPage.value &&
    !query.isFetching.value &&
    !query.isError.value
  )
    void query.fetchNextPage();
}
function continueList() {
  if (denied.value || !identityReady.value || query.isFetching.value) return;
  if (query.isFetchNextPageError.value) void query.fetchNextPage();
  else more();
}
useNextPage(next, more);
const visibility = computed(() =>
  playlist.value?.visibility === 'private'
    ? 'Приватный плейлист'
    : playlist.value?.visibility === 'unlisted'
      ? 'Доступ по ссылке'
      : 'Публичный плейлист',
);
</script>
<template>
  <section class="playlist-contents" aria-label="Содержимое плейлиста">
    <RouterLink
      class="playlist-contents__back"
      :to="`/@${encodeURIComponent(tag)}/playlists`"
      ><AppIcon name="back" />К плейлистам канала</RouterLink
    >
    <PageState
      v-if="unavailable"
      title="Плейлист недоступен"
      description="Он мог быть удалён или доступен только владельцу."
    />
    <PageState
      v-else-if="query.isPending.value || !query.isFetchedAfterMount.value"
      title="Загрузка плейлиста…"
    />
    <PageState
      v-else-if="query.isError.value && !query.isFetchNextPageError.value"
      title="Не удалось загрузить плейлист"
      :description="apiMessage(query.error.value)"
      error
      @retry="query.refetch()"
    />
    <template v-else-if="playlist">
      <header class="playlist-contents__header">
        <div class="playlist-contents__image" aria-hidden="true">
          <AppIcon name="playlist" />
        </div>
        <div>
          <h2>{{ playlist.title }}</h2>
          <p class="playlist-contents__visibility">{{ visibility }}</p>
          <p class="playlist-contents__description">
            {{ playlist.description }}
          </p>
        </div>
      </header>
      <PageState
        v-if="!videos.length"
        title="Здесь пока нет доступных видео"
        description="Доступ к каждому видео определяется отдельно."
      />
      <ol v-else class="playlist-contents__videos">
        <li v-for="(video, index) in videos" :key="video.id">
          <span class="playlist-contents__number">{{
            index + 1
          }}</span><VideoCard
            :video="video"
            :scope="
              !session.loading && !session.changing && !session.error
                ? session.scope
                : undefined
            "
            :guest="
              !session.account &&
              !session.loading &&
              !session.changing &&
              !session.error
            "
            horizontal
            @preview-error="verifyPreviewAccess"
          />
        </li>
      </ol>
      <div
        v-if="query.hasNextPage.value || query.isFetchNextPageError.value"
        ref="next"
        class="playlist-contents__more"
      >
        <p
          v-if="query.isFetchNextPageError.value"
          class="form-error"
          role="alert"
        >
          Не удалось продолжить список.
        </p>
        <button
          class="button button--secondary"
          type="button"
          :disabled="query.isFetching.value"
          @click="continueList"
        >
          {{
            query.isFetching.value
              ? 'Загрузка…'
              : query.isFetchNextPageError.value
                ? 'Повторить'
                : 'Показать ещё'
          }}
        </button>
      </div>
    </template>
  </section>
</template>
<style scoped lang="scss">
.playlist-contents {
  padding-top: 24px;
  &__back {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    margin-bottom: 24px;
    color: var(--color-muted);
  }
  &__header {
    display: flex;
    align-items: flex-start;
    gap: 24px;
    margin-bottom: 24px;
    h2 {
      font-size: 24px;
      line-height: 32px;
      margin: 0 0 12px;
      overflow-wrap: anywhere;
    }
  }
  &__image {
    display: grid;
    place-items: center;
    width: 240px;
    aspect-ratio: 16/9;
    border-radius: 12px;
    background: var(--color-surface);
    flex-shrink: 0;
    svg {
      width: 56px;
      height: 56px;
      color: var(--color-muted);
    }
  }
  &__visibility {
    font-size: 12px;
    color: var(--color-muted);
    line-height: 18px;
    margin: 0 0 12px;
  }
  &__description {
    font-size: 14px;
    line-height: 20px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    margin: 0;
  }
  &__videos {
    list-style: none;
    padding: 0;
    margin: 0;
    li {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }
  }
  &__number {
    font-size: 14px;
    color: var(--color-muted);
    min-width: 20px;
    text-align: center;
  }
  &__videos :deep(.video-card) {
    flex: 1;
    grid-template-columns: minmax(160px, 240px) minmax(0, 1fr);
  }
  &__more {
    text-align: center;
    padding: 24px 0;
  }
}
@media (max-width: 767px) {
  .playlist-contents {
    padding-top: 16px;
    &__header {
      gap: 12px;
      flex-direction: column;
      h2 {
        font-size: 20px;
        line-height: 28px;
      }
    }
    &__image {
      width: 100%;
      max-width: 360px;
    }
    &__videos li {
      gap: 8px;
    }
    &__videos :deep(.video-card) {
      grid-template-columns: 140px minmax(0, 1fr);
    }
    &__videos :deep(.video-card h2) {
      font-size: 14px;
      line-height: 20px;
    }
  }
}
@media (max-width: 380px) {
  .playlist-contents__videos :deep(.video-card) {
    grid-template-columns: 110px minmax(0, 1fr);
  }
}
</style>
