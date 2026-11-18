<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { useQuery } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  unwrap,
  viewV1PlaylistsPlaylistIdGet,
} from '../../../shared/api';
import { PageState } from '../../../shared/ui';
const route = useRoute(),
  router = useRouter(),
  session = useSession();
const id = computed(() =>
  isIdentifier(route.query.list) ? route.query.list : undefined,
);
const key = computed(() => ['playlist-link', session.scope, id.value] as const);
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
const navigating = ref(false),
  navigationError = ref(false);
let disposed = false;
const query = useQuery({
  queryKey: key,
  enabled: computed(() => !!id.value && identityReady.value),
  queryFn: async ({ queryKey: [, , requestedId], signal }) => {
    if (!requestedId) throw new ApiError(404, 'not_found');
    const page = unwrap(
      await viewV1PlaylistsPlaylistIdGet({
        path: { playlist_id: requestedId },
        signal,
      }),
    );
    if (
      page.playlist.id !== requestedId ||
      !isIdentifier(page.channel.id) ||
      page.channel.id !== page.playlist.channel_id ||
      !/^[a-z0-9._-]{3,30}$/i.test(page.channel.tag)
    )
      throw new Error('Invalid playlist channel');
    return page;
  },
  retry: false,
  gcTime: 0,
});
const unavailable = computed(
  () =>
    !id.value ||
    (query.error.value instanceof ApiError &&
      [401, 403, 404].includes(query.error.value.status)),
);
watch(key, () => {
  navigating.value = false;
  navigationError.value = false;
});
watch(
  [query.data, query.dataUpdatedAt, () => session.scope, identityReady],
  async ([page]) => {
    if (
      !page ||
      query.isError.value ||
      !identityReady.value ||
      !query.isFetchedAfterMount.value ||
      navigating.value ||
      disposed
    )
      return;
    const scope = session.scope,
      requestedId = id.value;
    if (page.playlist.id !== requestedId) return;
    navigating.value = true;
    try {
      // The server provides the owner even when the playlist contains no available videos.
      if (scope === session.scope && !disposed)
        await router.replace({
          path: `/@${encodeURIComponent(page.channel.tag)}/playlists`,
          query: { list: requestedId },
          hash: route.hash,
        });
    } catch {
      if (!disposed) navigationError.value = true;
    } finally {
      if (!disposed) navigating.value = false;
    }
  },
);
onBeforeUnmount(() => {
  disposed = true;
});
</script>
<template>
  <main id="main-content" class="playlist-link-page">
    <PageState
      v-if="id && (session.loading || session.changing)"
      title="Загрузка плейлиста…"
    />
    <PageState
      v-else-if="id && session.error"
      title="Не удалось проверить аккаунт"
      description="Повторите проверку, чтобы открыть плейлист."
      error
      @retry="session.refresh()"
    />
    <PageState
      v-else-if="unavailable"
      title="Плейлист недоступен"
      description="Он мог быть удалён или доступен только владельцу."
      ><RouterLink
        v-if="!session.account && !session.loading"
        class="button button--primary playlist-link-page__login"
        :to="{ path: '/login', query: { returnTo: route.fullPath } }"
        >Войти</RouterLink
      ></PageState
    >
    <PageState
      v-else-if="query.isError.value || navigationError"
      title="Не удалось открыть плейлист"
      :description="apiMessage(query.error.value)"
      error
      @retry="
        navigationError = false;
        query.refetch();
      "
    />
    <PageState v-else title="Загрузка плейлиста…" />
  </main>
</template>
<style scoped lang="scss">
.playlist-link-page {
  padding: 24px 16px;
  &__login {
    margin-top: 20px;
  }
}
</style>
