<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, RouterLink } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import {
  useGuestWatchProgress,
  useVideoCardVerification,
} from '../../../entities/video';
import { VideoList } from '../../../widgets/video-list';
import { useWatchFeed } from '../model/feed';
import { useSession } from '../../../entities/session';
import { VideoPlayer } from '../../../widgets/video-player';
import { Discussion } from '../../../widgets/discussion';
import { SaveVideo } from '../../../features/save-video';
import { CreatePlaylist } from '../../../features/create-playlist';
import { VideoLike } from '../../../features/video-like';
import { SubscribeChannel } from '../../../features/subscribe-channel';
import { ReportVideo } from '../../../features/report-video';
import { useAccountWatchProgress } from '../../../features/watch-progress';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  unwrap,
  viewV1VideosVideoIdGet,
} from '../../../shared/api';
import { watchAppearance } from '../../../shared/lib/watch-appearance';
import { mediaUrl } from '../../../shared/lib/media';
import { PageState } from '../../../shared/ui';

const route = useRoute();
const id = computed(() =>
  isIdentifier(route.query.v) ? route.query.v : undefined,
);
const session = useSession();
const guestProgress = useGuestWatchProgress();
const accountProgress = useAccountWatchProgress(
  () => id.value,
  () => {
    void accessLost();
  },
);
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
const viewerScope = computed(() =>
  identityReady.value ? session.scope : undefined,
);
function saveProgress(
  scope: string | undefined,
  videoId: string,
  position: number,
  duration: number,
  flush: boolean,
) {
  if (mediaDenied.value) return;
  if (session.account) {
    accountProgress.record(scope, videoId, position, duration, flush);
    return;
  }
  if (
    scope === session.scope &&
    !session.account &&
    !session.loading &&
    !session.changing &&
    !session.error &&
    !mediaDenied.value &&
    isIdentifier(videoId)
  )
    guestProgress.record(videoId, position, duration, flush);
}
const client = useQueryClient();
const expanded = ref(false);
const saveVideo = ref<InstanceType<typeof SaveVideo>>();
const ambientColor = ref<string>();
const appearance = inject(watchAppearance);
const appearanceOwner = Symbol('watch-page');
watch(ambientColor, (color) => appearance?.set(appearanceOwner, color), {
  flush: 'sync',
});
const mediaDenied = ref(false);
const key = computed(() => ['video', session.scope, id.value] as const);
const query = useQuery({
  queryKey: key,
  enabled: computed(() => !!id.value && identityReady.value),
  queryFn: async ({ queryKey: [, , requestedId], signal }) => {
    if (!requestedId) throw new ApiError(404, 'not_found');
    const data = unwrap(
      await viewV1VideosVideoIdGet({ path: { video_id: requestedId }, signal }),
    );
    if (data.id !== requestedId || !isIdentifier(data.channel.id))
      throw new Error('Invalid video response');
    mediaUrl(data.hls_url, `/api/v1/videos/${requestedId}/hls/`);
    mediaUrl(data.preview_url, `/api/v1/videos/${requestedId}/`);
    return data;
  },
  retry: false,
  gcTime: 0,
  refetchOnWindowFocus: true,
});
const unavailable = computed(
  () =>
    !id.value ||
    mediaDenied.value ||
    (query.error.value instanceof ApiError &&
      [401, 403, 404].includes(query.error.value.status)),
);
const video = computed(() =>
  identityReady.value && !unavailable.value && !query.isError.value
    ? query.data.value
    : undefined,
);
const feed = useWatchFeed(
  () => id.value,
  () => !!video.value,
);
const feedVideos = computed(() =>
  video.value && !(feed.isError.value && !feed.isFetchNextPageError.value)
    ? [
        ...new Map(
          feed.data.value?.pages
            .flatMap((page) => page.items)
            .map((item) => [item.id, item]) ?? [],
        ).values(),
      ]
    : [],
);
const verifyFeed = useVideoCardVerification(
  () => feed.refetch(),
  () => feed.isError.value,
);
function ambientChanged(
  scope: string | undefined,
  videoId: string,
  color: string | undefined,
) {
  if (scope === viewerScope.value && videoId === id.value && video.value)
    ambientColor.value = color;
}
const date = computed(() =>
  video.value?.added_at
    ? new Intl.DateTimeFormat('ru-RU', {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      }).format(new Date(video.value.added_at))
    : '',
);
watch(key, () => {
  ambientColor.value = undefined;
  expanded.value = false;
  mediaDenied.value = false;
});
watch(video, (value) => {
  if (!value) ambientColor.value = undefined;
  document.title = value ? `${value.title} - YouTube` : 'YouTube';
});
watch(query.error, (error) => {
  if (error instanceof ApiError && [401, 403, 404].includes(error.status))
    void accessLost();
});
onBeforeUnmount(() => {
  document.title = 'YouTube';
  appearance?.clear(appearanceOwner);
});
async function accessLost() {
  const deniedScope = session.scope,
    deniedId = id.value;
  const filter = {
    predicate: (item: { queryKey: readonly unknown[] }) =>
      [
        'video',
        'video-like',
        'comments',
        'watch-feed',
        'saved-playlists',
      ].includes(String(item.queryKey[0])) &&
      item.queryKey[1] === deniedScope &&
      item.queryKey[2] === deniedId,
  };
  if (id.value && !session.account) guestProgress.remove(id.value);
  mediaDenied.value = true;
  await client.cancelQueries(filter);
  client.removeQueries(filter);
}
async function retry() {
  mediaDenied.value = false;
  await query.refetch();
}
</script>
<template>
  <main
    id="main-content"
    class="watch-page"
    :class="{ 'watch-page--ambient': !!ambientColor }"
    :style="{ '--watch-ambient': ambientColor || '0 0 0' }"
  >
    <PageState
      v-if="id && (session.loading || session.changing)"
      title="Загрузка видео…"
    />
    <PageState
      v-else-if="id && session.error"
      title="Не удалось проверить аккаунт"
      description="Повторите проверку, чтобы открыть видео."
      error
      @retry="session.refresh()"
    />
    <PageState
      v-else-if="unavailable"
      title="Видео недоступно"
      description="Оно могло быть удалено, скрыто или доступно только определённым пользователям."
    >
      <RouterLink
        v-if="identityReady && !session.account"
        class="button button--primary watch-page__login"
        :to="{ path: '/login', query: { returnTo: route.fullPath } }"
        >Войти</RouterLink
      >
    </PageState>
    <PageState
      v-else-if="query.isPending.value || session.changing"
      title="Загрузка видео…"
    />
    <PageState
      v-else-if="query.isError.value"
      title="Не удалось загрузить видео"
      :description="apiMessage(query.error.value)"
      error
      @retry="retry"
    />
    <article v-else-if="video" class="watch-page__primary">
      <VideoPlayer
        :key="`${viewerScope ?? 'unknown'}:${video.id}`"
        :video-id="video.id"
        :initial-position="
          session.account
            ? (video.watch_progress?.position_seconds ?? 0)
            : guestProgress.position(video.id)
        "
        :viewer-scope="viewerScope"
        :src="video.hls_url"
        :poster="video.preview_url"
        :qualities="video.qualities"
        ambient
        autoplay
        @access-lost="accessLost"
        @verify-access="query.refetch()"
        @ambient-change="ambientChanged"
        @progress="saveProgress"
      />
      <p
        v-if="accountProgress.error.value"
        class="watch-page__progress-error"
        role="status"
      >
        Не удалось сохранить позицию просмотра.
        <button type="button" @click="accountProgress.retry">Повторить</button>
      </p>
      <div class="watch-page__metadata">
        <h1>{{ video.title }}</h1>
        <div class="watch-page__owner">
          <RouterLink
            :to="`/@${encodeURIComponent(video.channel.tag)}`"
            class="watch-page__avatar"
            :aria-label="`Канал ${video.channel.name}`"
            >{{
              [...video.channel.name][0]?.toLocaleUpperCase('ru')
            }}</RouterLink
          >
          <RouterLink
            :to="`/@${encodeURIComponent(video.channel.tag)}`"
            class="watch-page__author"
            >{{ video.channel.name }}</RouterLink
          >
          <SubscribeChannel
            :key="`${session.scope}:${video.channel.tag}`"
            :tag="video.channel.tag"
          />
          <VideoLike
            :key="`${session.scope}:${video.id}`"
            :video-id="video.id"
            @access-lost="accessLost"
          />
          <SaveVideo
            ref="saveVideo"
            :key="`save:${session.scope}:${video.id}`"
            :video-id="video.id"
            @access-lost="accessLost"
          >
            <template #default="{ disabled }"
              ><CreatePlaylist
                compact
                :video-id="video.id"
                :disabled="disabled"
                @created="saveVideo?.created()"
                @access-lost="accessLost"
            /></template>
          </SaveVideo>
          <ReportVideo
            :key="`${session.scope}:${video.id}`"
            :video-id="video.id"
            @access-lost="accessLost"
          />
        </div>
        <section
          class="watch-page__description"
          :class="{ 'watch-page__description--expanded': expanded }"
          aria-label="Описание видео"
        >
          <strong v-if="date" class="watch-page__date">{{ date }}</strong>
          <p v-if="video.description" id="video-description">
            {{ video.description }}
          </p>
          <button
            v-if="video.description"
            type="button"
            :aria-expanded="expanded"
            aria-controls="video-description"
            @click="expanded = !expanded"
          >
            {{ expanded ? 'Свернуть' : 'Ещё' }}
          </button>
        </section>
        <Discussion
          v-if="video.visibility === 'public'"
          :key="`${session.scope}:${video.id}`"
          :video-id="video.id"
          @access-lost="query.refetch()"
        />
        <p v-else class="watch-page__private-discussion">
          Комментарии к приватному видео недоступны.
        </p>
      </div>
    </article>
    <aside v-if="video" class="watch-page__feed" aria-label="Видео с главной">
      <VideoList
        :videos="feedVideos"
        horizontal
        :loading="feed.isPending.value"
        :failed="feed.isError.value && !feed.isFetchNextPageError.value"
        :next-failed="feed.isFetchNextPageError.value"
        :fetching="feed.isFetching.value"
        :has-next="!!feed.hasNextPage.value"
        empty-title="Пока нет других видео"
        empty-description=""
        @more="feed.fetchNextPage()"
        @retry="feed.refetch()"
        @preview-error="verifyFeed"
      />
    </aside>
  </main>
</template>
<style scoped lang="scss">
.watch-page {
  position: relative;
  isolation: isolate;
  &::before {
    content: '';
    position: absolute;
    inset: -40px 0 auto;
    height: min(1100px, 110vh);
    z-index: -1;
    pointer-events: none;
    background: radial-gradient(
      ellipse at 30% 25%,
      rgb(var(--watch-ambient) / var(--watch-ambient-strength)),
      transparent 75%
    );
    opacity: 0;
    transition: opacity 600ms ease;
  }
  &--ambient::before {
    opacity: 1;
  }
  &__feed {
    min-width: 0;
    :deep(.video-card--horizontal) {
      grid-template-columns: minmax(140px, 58%) minmax(0, 1fr);
      gap: 12px;
    }
    :deep(.video-card h2) {
      font-size: 14px;
      line-height: 20px;
      font-weight: 500;
    }
    :deep(.video-card__channel) {
      margin-top: 4px;
    }
  }

  &__progress-error {
    color: var(--color-muted);
    font-size: 14px;
    button {
      border: 0;
      background: transparent;
      color: var(--color-accent);
      cursor: pointer;
    }
  }
  display: grid;
  grid-template-columns: minmax(0, 72fr) minmax(300px, 28fr);
  gap: 16px;
  padding: 12px 16px 48px;
  max-width: 1920px;
  margin: 0 auto;
  > .page-state {
    grid-column: 1/-1;
    width: 100%;
  }
  &__primary {
    min-width: 0;
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    font-weight: 700;
    margin: 12px 0 8px;
    overflow-wrap: anywhere;
  }
  &__owner {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 42px;
    flex-wrap: wrap;
  }
  &__avatar {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--color-surface);
    font-size: 20px;
    flex-shrink: 0;
  }
  &__author {
    font-size: 16px;
    line-height: 22px;
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  &__description {
    margin-top: 12px;
    padding: 12px;
    border-radius: 12px;
    background: var(--color-surface);
    font-size: 14px;
    line-height: 20px;
    overflow-wrap: anywhere;
    p {
      margin: 4px 0 0;
      white-space: pre-wrap;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      overflow: hidden;
    }
    button {
      border: 0;
      background: transparent;
      font-weight: 500;
      padding: 0;
      margin-top: 4px;
      cursor: pointer;
    }
    &--expanded p {
      display: block;
    }
  }
  &__date {
    font-weight: 500;
  }
  &__login {
    margin-top: 20px;
  }
  &__private-discussion {
    color: var(--color-muted);
    font-size: 14px;
    margin-top: 24px;
  }
}
@media (max-width: 1000px) {
  .watch-page {
    grid-template-columns: minmax(0, 1fr);
    &__feed :deep(.video-card--horizontal) {
      grid-template-columns: minmax(140px, 40%) minmax(0, 1fr);
    }
  }
}
@media (max-width: 767px) {
  .watch-page {
    padding: 0 0 24px;
    gap: 0;
    h1 {
      font-size: 18px;
      line-height: 24px;
      margin: 12px 0;
    }
    &__feed {
      padding: 24px 12px 0;
    }
    &__metadata {
      padding: 0 12px;
    }
    &__author {
      font-size: 14px;
      line-height: 20px;
    }
    &__avatar {
      width: 32px;
      height: 32px;
      font-size: 16px;
    }
    &__description {
      font-size: 12px;
      line-height: 18px;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .watch-page::before {
    transition: none;
  }
}
</style>
