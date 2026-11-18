<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { useQuery, useInfiniteQuery } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  VideoCard,
  validateCards,
  useVideoCardVerification,
} from '../../../entities/video';
import { PlaylistCard } from '../../../entities/playlist';
import { SubscribeChannel } from '../../../features/subscribe-channel';
import { PlaylistContents } from '../../../widgets/playlist-contents';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  channelV1ChannelsTagGet,
  videosV1ChannelsTagVideosGet,
  playlistsV1ChannelsTagPlaylistsGet,
  unwrap,
} from '../../../shared/api';
import { PageState } from '../../../shared/ui';
import { useNextPage } from '../../../shared/lib/pagination';
const session = useSession(),
  route = useRoute();
const next = ref<HTMLElement>();
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
const tag = computed(() =>
  typeof route.params.tag === 'string' &&
  /^[a-z0-9._-]{3,30}$/i.test(route.params.tag)
    ? route.params.tag.toLowerCase()
    : undefined,
);
const tab = computed(() =>
  route.params.tab === 'playlists' ? 'playlists' : 'videos',
);
const selected = computed(() =>
  isIdentifier(route.query.list) ? route.query.list : undefined,
);
const invalidList = computed(
  () =>
    tab.value === 'playlists' &&
    route.query.list !== undefined &&
    !selected.value,
);
const profile = useQuery({
  queryKey: computed(() => ['channel', session.scope, tag.value] as const),
  enabled: computed(() => !!tag.value && identityReady.value),
  queryFn: async ({ queryKey: [, , requestedTag], signal }) => {
    if (!requestedTag) throw new ApiError(404, 'not_found');
    const value = unwrap(
      await channelV1ChannelsTagGet({ path: { tag: requestedTag }, signal }),
    );
    if (!isIdentifier(value.id) || value.tag.toLowerCase() !== requestedTag)
      throw new Error('Invalid channel');
    return value;
  },
  retry: false,
  gcTime: 0,
});
const unavailable = computed(
  () =>
    !tag.value ||
    (profile.error.value instanceof ApiError &&
      [403, 404].includes(profile.error.value.status)),
);
const channel = computed(() =>
  identityReady.value && !profile.isError.value
    ? profile.data.value
    : undefined,
);
const videoChannel = computed(() =>
  tab.value === 'videos' ? channel.value : undefined,
);
const playlistChannel = computed(() =>
  tab.value === 'playlists' && !selected.value && !invalidList.value
    ? channel.value
    : undefined,
);
const videosQuery = useInfiniteQuery({
  queryKey: computed(
    () =>
      [
        'channel-videos',
        session.scope,
        videoChannel.value?.tag,
        videoChannel.value?.id,
      ] as const,
  ),
  enabled: computed(() => !!videoChannel.value),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({
    queryKey: [, , requestedTag, channelId],
    signal,
    pageParam,
  }) => {
    if (!requestedTag || !channelId) throw new ApiError(404, 'not_found');
    const page = validateCards(
      unwrap(
        await videosV1ChannelsTagVideosGet({
          path: { tag: requestedTag },
          query: { cursor: pageParam },
          signal,
        }),
      ),
    );
    if (page.items.some((item) => item.channel.id !== channelId))
      throw new Error('Invalid channel video');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
  gcTime: 0,
});
const playlistsQuery = useInfiniteQuery({
  queryKey: computed(
    () =>
      [
        'channel-playlists',
        session.scope,
        playlistChannel.value?.tag,
        playlistChannel.value?.id,
      ] as const,
  ),
  enabled: computed(() => !!playlistChannel.value),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({
    queryKey: [, , requestedTag, channelId],
    signal,
    pageParam,
  }) => {
    if (!requestedTag || !channelId) throw new ApiError(404, 'not_found');
    const page = unwrap(
      await playlistsV1ChannelsTagPlaylistsGet({
        path: { tag: requestedTag },
        query: { cursor: pageParam },
        signal,
      }),
    );
    if (
      page.items.some(
        (item) =>
          !isIdentifier(item.id) ||
          item.channel_id !== channelId ||
          item.visibility !== 'public',
      )
    )
      throw new Error('Invalid playlist');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
  gcTime: 0,
});
const active = computed(() =>
  tab.value === 'videos' ? videosQuery : playlistsQuery,
);
const verifyPreviewAccess = useVideoCardVerification(
  () => videosQuery.refetch(),
  () => videosQuery.isError.value,
);
const videos = computed(() => [
  ...new Map(
    videosQuery.data.value?.pages
      .flatMap((page) => page.items)
      .map((item) => [item.id, item]) ?? [],
  ).values(),
]);
const playlists = computed(() => [
  ...new Map(
    playlistsQuery.data.value?.pages
      .flatMap((page) => page.items)
      .map((item) => [item.id, item]) ?? [],
  ).values(),
]);
function more() {
  const query = active.value;
  if (
    query.hasNextPage.value &&
    !query.isFetching.value &&
    !query.isError.value
  )
    void query.fetchNextPage();
}
useNextPage(next, more);
let disposed = false;
watch([channel, selected, tab], async ([value, list, section]) => {
  await nextTick();
  if (!disposed && !(list && section === 'playlists'))
    document.title = value ? `${value.name} - YouTube` : 'YouTube';
});
onBeforeUnmount(() => {
  disposed = true;
  document.title = 'YouTube';
});
</script>
<template>
  <main id="main-content" class="channel-page">
    <PageState
      v-if="tag && (session.loading || session.changing)"
      title="Загрузка канала…"
    />
    <PageState
      v-else-if="tag && session.error"
      title="Не удалось проверить аккаунт"
      description="Повторите проверку, чтобы открыть канал."
      error
      @retry="session.refresh()"
    />
    <PageState
      v-else-if="unavailable"
      title="Канал недоступен"
      description="Проверьте ссылку на канал."
    />
    <PageState v-else-if="profile.isPending.value" title="Загрузка канала…" />
    <PageState
      v-else-if="profile.isError.value"
      title="Не удалось загрузить канал"
      :description="apiMessage(profile.error.value)"
      error
      @retry="profile.refetch()"
    />
    <template v-else-if="channel">
      <header class="channel-page__header">
        <div class="channel-page__avatar" aria-hidden="true">
          {{ [...channel.name][0]?.toLocaleUpperCase('ru') }}
        </div>
        <div class="channel-page__identity">
          <h1>{{ channel.name }}</h1>
          <p>@{{ channel.tag }}</p>
          <SubscribeChannel
            :key="`${session.scope}:${channel.tag}`"
            :tag="channel.tag"
          />
        </div>
      </header>
      <nav class="channel-page__tabs" aria-label="Разделы канала">
        <RouterLink
          :to="`/@${encodeURIComponent(channel.tag)}/videos`"
          :aria-current="tab === 'videos' ? 'page' : undefined"
          >Видео</RouterLink
        ><RouterLink
          :to="`/@${encodeURIComponent(channel.tag)}/playlists`"
          :aria-current="tab === 'playlists' ? 'page' : undefined"
          >Плейлисты</RouterLink
        >
      </nav>
      <div class="channel-page__content">
        <PageState v-if="invalidList" title="Плейлист недоступен" />
        <PlaylistContents
          v-else-if="tab === 'playlists' && selected"
          :key="`${session.scope}:${channel.id}:${selected}`"
          :playlist-id="selected"
          :channel-id="channel.id"
          :tag="channel.tag"
        />
        <template v-else>
          <PageState
            v-if="active.isPending.value"
            :title="
              tab === 'videos' ? 'Загрузка видео…' : 'Загрузка плейлистов…'
            "
          />
          <PageState
            v-else-if="
              active.isError.value && !active.isFetchNextPageError.value
            "
            title="Не удалось загрузить список"
            :description="apiMessage(active.error.value)"
            error
            @retry="active.refetch()"
          />
          <PageState
            v-else-if="tab === 'videos' && !videos.length"
            title="На канале пока нет видео"
            description="Здесь появятся опубликованные публичные видео."
          />
          <PageState
            v-else-if="tab === 'playlists' && !playlists.length"
            title="На канале пока нет публичных плейлистов"
          />
          <div
            v-else-if="tab === 'videos'"
            class="channel-page__grid channel-page__grid--videos"
          >
            <VideoCard
              v-for="video in videos"
              :key="video.id"
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
              @preview-error="verifyPreviewAccess"
            />
          </div>
          <div v-else class="channel-page__grid channel-page__grid--playlists">
            <PlaylistCard
              v-for="playlist in playlists"
              :key="playlist.id"
              :playlist="playlist"
              :tag="channel.tag"
            />
          </div>
          <div
            v-if="active.hasNextPage.value || active.isFetchNextPageError.value"
            ref="next"
            class="channel-page__more"
          >
            <p
              v-if="active.isFetchNextPageError.value"
              role="alert"
              class="form-error"
            >
              Не удалось продолжить список.
            </p>
            <button
              class="button button--secondary"
              type="button"
              :disabled="active.isFetching.value"
              @click="active.fetchNextPage()"
            >
              {{
                active.isFetching.value
                  ? 'Загрузка…'
                  : active.isFetchNextPageError.value
                    ? 'Повторить'
                    : 'Показать ещё'
              }}
            </button>
          </div>
        </template>
      </div>
    </template>
  </main>
</template>
<style scoped lang="scss">
.channel-page {
  max-width: 1332px;
  margin: 0 auto;
  padding: 24px 24px 48px;
  &__header {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 20px;
    min-height: 176px;
  }
  &__avatar {
    display: grid;
    place-items: center;
    width: 160px;
    height: 160px;
    background: var(--color-surface);
    border-radius: 50%;
    flex-shrink: 0;
    font-size: 72px;
  }
  &__identity {
    min-width: 0;
    h1 {
      font-size: 36px;
      line-height: 50px;
      font-weight: 700;
      margin: 0;
      overflow-wrap: anywhere;
    }
    p {
      font-size: 14px;
      line-height: 22px;
      color: var(--color-muted);
      margin: 4px 0 12px;
    }
    :deep(.subscribe) {
      margin-left: 0;
      display: inline-block;
    }
  }
  &__tabs {
    display: flex;
    align-items: center;
    gap: 24px;
    height: 48px;
    border-bottom: 1px solid var(--color-border);
    a {
      position: relative;
      font-size: 16px;
      line-height: 24px;
      font-weight: 500;
      height: 48px;
      display: flex;
      align-items: center;
      color: var(--color-muted);
      &[aria-current='page'] {
        color: var(--color-text);
        &::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          height: 3px;
          border-radius: 3px;
          background: var(--color-text);
        }
      }
    }
  }
  &__content {
    padding-top: 24px;
  }
  &__grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 32px 16px;
    &--playlists {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }
  }
  :deep(.video-card__avatar) {
    display: none;
  }
  :deep(.video-card__channel) {
    display: none;
  }
  &__more {
    text-align: center;
    padding: 32px 0;
  }
}
@media (max-width: 1500px) {
  .channel-page__grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .channel-page__grid--playlists {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (max-width: 1000px) {
  .channel-page__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .channel-page__grid--playlists {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 767px) {
  .channel-page {
    padding: 16px 16px 32px;
    &__header {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr);
      align-items: flex-start;
      gap: 16px;
      min-height: 0;
      margin-bottom: 16px;
    }
    &__avatar {
      width: 72px;
      height: 72px;
      font-size: 32px;
      grid-column: 1;
      grid-row: 1/3;
    }
    &__identity {
      display: contents;
      flex: 1;
      h1 {
        grid-column: 2;
        grid-row: 1;
        font-size: 24px;
        line-height: 32px;
      }
      p {
        grid-column: 2;
        grid-row: 2;
        font-size: 12px;
        line-height: 18px;
        margin: 4px 0 12px;
      }
      :deep(.subscribe) {
        grid-column: 1/-1;
        grid-row: 3;
        display: block;
        width: 100%;
      }
      :deep(.subscribe .button) {
        width: 100%;
        height: 40px;
      }
    }
    &__tabs {
      gap: 24px;
    }
    &__grid {
      gap: 24px 12px;
      grid-template-columns: minmax(0, 1fr);
    }
    &__content {
      padding-top: 16px;
    }
    :deep(.video-card__preview) {
      border-radius: 8px;
    }
    :deep(.video-card__details) {
      padding: 0;
    }
    :deep(.video-card h2) {
      font-size: 14px;
      line-height: 20px;
    }
    :deep(.video-card__date) {
      font-size: 12px;
      line-height: 18px;
    }
    .channel-page__grid--videos :deep(.video-card) {
      display: grid;
      grid-template-columns: 160px minmax(0, 1fr);
      gap: 8px;
    }
    .channel-page__grid--videos :deep(.video-card__details) {
      margin: 0;
    }
    .channel-page__grid--videos :deep(.video-card h2) {
      font-weight: 400;
    }
    .channel-page__grid--videos :deep(.video-card__duration) {
      font-size: 10px;
      bottom: 4px;
      right: 4px;
    }
  }
}
@media (max-width: 380px) {
  .channel-page__grid {
    grid-template-columns: 1fr;
  }
}
</style>
