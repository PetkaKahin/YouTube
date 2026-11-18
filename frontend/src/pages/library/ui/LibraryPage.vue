<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query';
import { VideoList } from '../../../widgets/video-list';
import { useSession } from '../../../entities/session';
import { PlaylistCard } from '../../../entities/playlist';
import {
  validateCards,
  useVideoCardVerification,
} from '../../../entities/video';
import {
  ApiError,
  isIdentifier,
  libraryHistoryV1VideosHistoryGet,
  libraryLikedV1VideosLikedGet,
  librarySubscriptionsV1VideosSubscriptionsGet,
  mineV1PlaylistsMineGet,
  unwrap,
} from '../../../shared/api';
import { PageState } from '../../../shared/ui';
import { useNextPage } from '../../../shared/lib/pagination';
const props = defineProps<{
  kind: 'subscriptions' | 'history' | 'liked' | 'playlists';
}>();
const client = useQueryClient();
const session = useSession(),
  route = useRoute();
const ready = computed(
  () => !session.loading && !session.changing && !session.error,
);
const titles = {
  subscriptions: 'Подписки',
  history: 'История просмотра',
  liked: 'Понравившиеся',
  playlists: 'Плейлисты',
};
const empty = {
  subscriptions: 'В подписках пока нет видео',
  history: 'История просмотра пуста',
  liked: 'Нет понравившихся видео',
  playlists: 'У вас пока нет плейлистов',
};
const query = useInfiniteQuery({
  queryKey: computed(() => ['library-videos', session.scope, props.kind]),
  enabled: computed(
    () => ready.value && !!session.account && props.kind !== 'playlists',
  ),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ queryKey: [, , kind], pageParam, signal }) => {
    const request =
      kind === 'history'
        ? libraryHistoryV1VideosHistoryGet
        : kind === 'liked'
          ? libraryLikedV1VideosLikedGet
          : librarySubscriptionsV1VideosSubscriptionsGet;
    return validateCards(
      unwrap(await request({ query: { cursor: pageParam }, signal })),
    );
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  gcTime: 0,
  retry: false,
});
const playlists = useInfiniteQuery({
  queryKey: computed(() => ['library-playlists', session.scope]),
  enabled: computed(
    () => ready.value && !!session.account && props.kind === 'playlists',
  ),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      await mineV1PlaylistsMineGet({ query: { cursor: pageParam }, signal }),
    );
    if (
      page.items.some((p) => !isIdentifier(p.id) || !isIdentifier(p.channel_id))
    )
      throw new Error('Invalid playlist identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  gcTime: 0,
  retry: false,
});
const videos = computed(() =>
  ready.value &&
  session.account &&
  query.isFetchedAfterMount.value &&
  !(query.isError.value && !query.isFetchNextPageError.value)
    ? [
        ...new Map(
          query.data.value?.pages
            .flatMap((p) => p.items)
            .map((v) => [v.id, v]) ?? [],
        ).values(),
      ]
    : [],
);
const items = computed(() =>
  ready.value &&
  session.account &&
  playlists.isFetchedAfterMount.value &&
  !(playlists.isError.value && !playlists.isFetchNextPageError.value)
    ? [
        ...new Map(
          playlists.data.value?.pages
            .flatMap((p) => p.items)
            .map((p) => [p.id, p]) ?? [],
        ).values(),
      ]
    : [],
);
const active = computed(() => (props.kind === 'playlists' ? playlists : query));
const denied = ref(false);
watch(
  () => session.scope,
  () => {
    denied.value = false;
  },
  { flush: 'sync' },
);
watch(
  () => active.value.error.value,
  (cause) => {
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      denied.value = true;
      client.removeQueries({
        queryKey:
          props.kind === 'playlists'
            ? ['library-playlists', session.scope]
            : ['library-videos', session.scope, props.kind],
        exact: true,
      });
    }
  },
);
function retryAccess() {
  denied.value = false;
  void active.value.refetch();
}
const cover = computed(() =>
  props.kind === 'liked' ? videos.value[0]?.preview_url : undefined,
);
const coverBroken = ref(false);
watch(
  () => [session.scope, props.kind, cover.value],
  () => {
    coverBroken.value = false;
  },
);
const verify = useVideoCardVerification(
  () => query.refetch(),
  () => query.isError.value,
);
const next = ref<HTMLElement>();
function morePlaylists() {
  if (playlists.hasNextPage.value && !playlists.isFetching.value)
    void playlists.fetchNextPage();
}
useNextPage(next, () => {
  if (!playlists.isFetchNextPageError.value) morePlaylists();
});
</script>
<template>
  <main
    id="main-content"
    class="library-page"
    :class="{
      'home-page': kind === 'subscriptions',
      'library-page--liked': kind === 'liked',
    }"
    :aria-label="titles[kind]"
    tabindex="-1"
  >
    <PageState
      v-if="session.loading || session.changing"
      title="Проверяем аккаунт…"
    />
    <PageState
      v-else-if="session.error"
      title="Не удалось проверить аккаунт"
      error
      @retry="session.refresh()"
    />
    <PageState
      v-else-if="!session.account"
      :title="titles[kind]"
      description="Войдите, чтобы открыть этот раздел."
    >
      <RouterLink
        class="button button--primary library-page__login"
        :to="{ path: '/login', query: { returnTo: route.fullPath } }"
        >Войти</RouterLink
      >
    </PageState>
    <PageState
      v-else-if="denied"
      title="Раздел недоступен"
      description="Не удалось подтвердить доступ к личному списку."
      error
      @retry="retryAccess"
    />
    <template v-else>
      <aside v-if="kind === 'liked'" class="library-page__summary">
        <img
          v-if="cover && !coverBroken"
          :src="cover"
          alt=""
          @error="
            coverBroken = true;
            verify();
          "
        />
        <h1>{{ titles[kind] }}</h1>
        <p>{{ session.account.name }}</p>
      </aside>
      <section class="library-page__content" :aria-label="titles[kind]">
        <h1 v-if="kind !== 'subscriptions' && kind !== 'liked'">
          {{ titles[kind] }}
        </h1>
        <template v-if="kind === 'playlists'">
          <PageState
            v-if="
              playlists.isPending.value ||
              (!playlists.isFetchedAfterMount.value && !playlists.isError.value)
            "
            title="Загрузка плейлистов…"
          />
          <PageState
            v-else-if="
              playlists.isError.value && !playlists.isFetchNextPageError.value
            "
            title="Не удалось загрузить плейлисты"
            error
            @retry="playlists.refetch()"
          />
          <PageState v-else-if="!items.length" :title="empty[kind]" />
          <div v-else class="library-page__playlists">
            <PlaylistCard
              v-for="item in items"
              :key="item.id"
              :playlist="item"
              show-visibility
            />
          </div>
          <div
            v-if="
              playlists.hasNextPage.value &&
              !playlists.isPending.value &&
              (!playlists.isError.value || playlists.isFetchNextPageError.value)
            "
            ref="next"
            class="library-page__pagination"
          >
            <button
              type="button"
              class="button button--secondary"
              :disabled="playlists.isFetching.value"
              @click="morePlaylists"
            >
              {{
                playlists.isFetching.value
                  ? 'Загружаем…'
                  : playlists.isFetchNextPageError.value
                    ? 'Повторить загрузку'
                    : 'Ещё плейлисты'
              }}
            </button>
          </div>
        </template>
        <VideoList
          v-else
          :videos="videos"
          :horizontal="kind !== 'subscriptions'"
          :empty-title="empty[kind]"
          empty-description=""
          :loading="
            query.isPending.value ||
            (!query.isFetchedAfterMount.value && !query.isError.value)
          "
          :failed="query.isError.value && !query.isFetchNextPageError.value"
          :next-failed="query.isFetchNextPageError.value"
          :fetching="query.isFetching.value"
          :has-next="!!query.hasNextPage.value"
          @more="query.fetchNextPage()"
          @retry="query.refetch()"
          @preview-error="verify"
        />
      </section>
    </template>
  </main>
</template>
<style scoped lang="scss">
.library-page {
  padding: 24px 24px 48px;
  h1 {
    font-size: 36px;
    line-height: 44px;
    margin: 0 0 24px;
  }
  &__content {
    min-width: 0;
  }
  &__login {
    margin-top: 20px;
  }
  &__playlists {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 36px 16px;
  }
  &__pagination {
    padding: 32px 0;
    text-align: center;
  }
  &--liked {
    display: grid;
    grid-template-columns: 360px minmax(0, 1fr);
    gap: 40px;
  }
  &__summary {
    padding: 24px;
    border-radius: 16px;
    background: linear-gradient(
      var(--color-library-liked),
      var(--color-background)
    );
    min-height: calc(100dvh - 104px);
    align-self: start;
    position: sticky;
    top: 80px;
    img {
      width: 100%;
      aspect-ratio: 16/9;
      object-fit: contain;
      border-radius: 8px;
    }
    h1 {
      font-size: 28px;
      line-height: 34px;
      margin-top: 20px;
      overflow-wrap: anywhere;
    }
    p {
      font-size: 14px;
      overflow-wrap: anywhere;
    }
  }
  &--liked > :deep(.page-state) {
    grid-column: 1/-1;
  }
}
@media (max-width: 1500px) {
  .library-page__playlists {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .library-page--liked {
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 24px;
  }
}
@media (max-width: 1100px) {
  .library-page__playlists {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .library-page--liked {
    display: block;
  }
  .library-page__summary {
    min-height: 0;
    position: static;
    margin-bottom: 24px;
    img {
      max-width: 240px;
    }
  }
}
@media (max-width: 767px) {
  .library-page {
    padding: 16px 12px 24px;
    h1 {
      font-size: 26px;
      line-height: 32px;
    }
  }
  .library-page.home-page {
    padding: 12px 0 24px;
  }
  .library-page__playlists {
    display: grid;
    grid-template-columns: 1fr;
    gap: 24px;
  }
}
</style>
