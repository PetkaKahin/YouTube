<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query';
import { StudioShell } from '../../../widgets/studio-shell';
import { StudioContentTabs } from '../../../widgets/studio-content-tabs';
import { CreatePlaylist } from '../../../features/create-playlist';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  mineV1PlaylistsMineGet,
  unwrap,
} from '../../../shared/api';
import { useNextPage } from '../../../shared/lib/pagination';
import { AppIcon, PageState } from '../../../shared/ui';

const session = useSession(),
  client = useQueryClient();
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
let disposed = false;
const creator = ref<InstanceType<typeof CreatePlaylist>>();
const route = useRoute(),
  router = useRouter();
watch(
  [() => route.query.create, () => session.account, creator],
  async () => {
    if (
      route.query.create !== 'playlist' ||
      !identityReady.value ||
      !session.account ||
      !creator.value
    )
      return;
    const scope = session.scope,
      requestedPath = route.fullPath;
    await nextTick();
    if (
      disposed ||
      session.scope !== scope ||
      route.fullPath !== requestedPath ||
      !creator.value
    )
      return;
    await creator.value.open();
    if (disposed || session.scope !== scope || route.fullPath !== requestedPath)
      return;
    await router.replace({
      path: route.path,
      query: { ...route.query, create: undefined },
    });
  },
  { immediate: true },
);
const sentinel = ref<HTMLElement>(),
  notice = ref(''),
  denied = ref(false);
const key = computed(() => ['studio-playlists', session.scope]);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(
    () => !!session.account && identityReady.value && !denied.value,
  ),
  gcTime: 0,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      await mineV1PlaylistsMineGet({ query: { cursor: pageParam }, signal }),
    );
    if (
      page.items.some(
        (item) => !isIdentifier(item.id) || !isIdentifier(item.channel_id),
      )
    )
      throw new Error('Invalid playlist identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
});
const items = computed(() => {
  if (
    !identityReady.value ||
    !session.account ||
    denied.value ||
    !query.isFetchedAfterMount.value
  )
    return [];
  const seen = new Set<string>();
  return (
    query.data.value?.pages
      .flatMap((page) => page.items)
      .filter((item) => !seen.has(item.id) && !!seen.add(item.id)) ?? []
  );
});
watch(
  () => session.scope,
  () => {
    notice.value = '';
    denied.value = false;
  },
);
watch(query.error, (cause) => {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    denied.value = true;
    client.removeQueries({ queryKey: key.value, exact: true });
  }
});
function nextPage() {
  if (
    query.hasNextPage.value &&
    !query.isFetching.value &&
    !query.isError.value
  )
    void query.fetchNextPage();
}
useNextPage(sentinel, nextPage);
function created() {
  notice.value = 'Плейлист создан';
  // A new first item changes the cursor boundary; restart rather than append to old pages.
  void client.resetQueries({ queryKey: key.value, exact: true });
}
function retry() {
  if (denied.value) denied.value = false;
  else if (query.isFetchNextPageError.value) void query.fetchNextPage();
  else void query.refetch();
}
const visibility = {
  public: { label: 'Для всех', icon: 'globe' },
  unlisted: { label: 'Доступ по ссылке', icon: 'link' },
  private: { label: 'Ограниченный', icon: 'lock' },
} as const;
function access(value: string) {
  return visibility[value as keyof typeof visibility];
}
const dateFormat = new Intl.DateTimeFormat('ru', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});
function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : dateFormat.format(date);
}
const previousTitle = document.title;
document.title = 'Плейлисты — YouTube Studio';
onBeforeUnmount(() => {
  disposed = true;
  document.title = previousTitle;
});
</script>
<template>
  <StudioShell>
    <template #actions
      ><CreatePlaylist ref="creator" :key="session.scope" @created="created"
    /></template>
    <main id="main-content" class="studio-playlists">
      <PageState
        v-if="session.loading || session.changing"
        title="Проверяем аккаунт"
        busy
      />
      <PageState
        v-else-if="session.error"
        title="Не удалось проверить аккаунт"
        :description="apiMessage(session.error)"
        error
        @retry="session.refresh()"
      />
      <section v-else-if="!session.account" class="studio-playlists__login">
        <h1>Войдите в YouTube Studio</h1>
        <p>Создавайте плейлисты и управляйте своим контентом.</p>
        <RouterLink
          :to="{
            path: '/login',
            query: { returnTo: '/studio/content/playlists' },
          }"
          class="button button--primary"
          >Войти</RouterLink
        ><RouterLink to="/" class="text-link">Перейти на YouTube</RouterLink>
      </section>
      <template v-else>
        <h1>Контент на канале</h1>
        <StudioContentTabs />
        <p v-if="notice" class="studio-playlists__notice" role="status">
          {{ notice }}
        </p>
        <PageState
          v-if="denied"
          title="Плейлисты недоступны"
          description="Не удалось получить доступ к своим плейлистам."
          error
          @retry="retry"
        />
        <PageState
          v-else-if="query.isPending.value || !query.isFetchedAfterMount.value"
          title="Загружаем плейлисты"
          busy
        />
        <PageState
          v-else-if="query.isError.value && !query.isFetchNextPageError.value"
          title="Не удалось загрузить плейлисты"
          :description="apiMessage(query.error.value)"
          error
          @retry="retry"
        />
        <template v-else>
          <div
            v-if="items.length"
            class="studio-table-scroll"
            tabindex="0"
            role="region"
            aria-label="Свои плейлисты"
          >
            <table class="studio-table">
              <thead>
                <tr>
                  <th scope="col">Плейлист</th>
                  <th scope="col">Тип</th>
                  <th scope="col">Доступ</th>
                  <th scope="col">Последнее изменение</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="playlist in items"
                  :key="playlist.id"
                  :data-playlist-id="playlist.id"
                >
                  <td>
                    <RouterLink
                      :to="{ path: '/playlist', query: { list: playlist.id } }"
                      class="studio-table__playlist"
                      ><span class="studio-table__thumbnail" aria-hidden="true"
                        ><AppIcon name="playlist" /></span
                      ><span class="studio-table__text"
                        ><strong>{{ playlist.title }}</strong
                        ><span>{{
                          playlist.description || 'Без описания'
                        }}</span></span
                      ></RouterLink
                    >
                  </td>
                  <td>Плейлист</td>
                  <td>
                    <span class="studio-table__access"
                      ><AppIcon
                        v-if="access(playlist.visibility)"
                        :name="access(playlist.visibility).icon"
                      />{{
                        access(playlist.visibility)?.label ||
                        'Неизвестный доступ'
                      }}</span
                    >
                  </td>
                  <td>
                    <time :datetime="playlist.updated_at">{{
                      formatDate(playlist.updated_at)
                    }}</time>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PageState
            v-else
            title="Пока нет плейлистов"
            description="Чтобы создать плейлист, нажмите «Создать» в верхней части страницы."
          />
          <div ref="sentinel" class="studio-playlists__more">
            <p
              v-if="query.isFetchNextPageError.value"
              class="form-error"
              role="alert"
            >
              {{ apiMessage(query.error.value) }}
            </p>
            <button
              v-if="query.isFetchNextPageError.value"
              type="button"
              class="button button--secondary"
              @click="retry"
            >
              Повторить загрузку
            </button>
            <button
              v-else-if="query.hasNextPage.value"
              type="button"
              class="button button--secondary"
              :disabled="query.isFetching.value"
              @click="nextPage"
            >
              {{
                query.isFetchingNextPage.value ? 'Загружаем…' : 'Показать ещё'
              }}
            </button>
          </div>
        </template>
      </template>
    </main>
  </StudioShell>
</template>
<style scoped lang="scss">
.studio-playlists {
  padding-top: 24px;
  h1 {
    margin: 0 32px 20px;
    font-size: 32px;
    line-height: 40px;
  }
  &__login {
    padding: 32px;
    h1 {
      margin: 0;
    }
    .text-link {
      display: inline-block;
      margin-left: 24px;
    }
  }
  &__notice {
    padding: 12px 32px;
    margin: 0;
  }
  &__more {
    padding: 24px 32px;
    text-align: center;
  }
}
.studio-table-scroll {
  overflow-x: auto;
}
.studio-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  th,
  td {
    padding: 8px 24px;
    text-align: left;
    border-bottom: 1px solid var(--color-border);
    vertical-align: top;
  }
  th {
    color: var(--color-muted);
    font-size: 12px;
    font-weight: 400;
    height: 48px;
    vertical-align: middle;
  }
  th:first-child {
    width: 43%;
    padding-left: 32px;
  }
  th:nth-child(2) {
    width: 14%;
  }
  th:nth-child(3) {
    width: 22%;
  }
  td {
    height: 85px;
    padding-top: 16px;
  }
  td:first-child {
    padding: 8px 24px 8px 28px;
  }
  &__playlist {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    &:hover strong {
      text-decoration: underline;
    }
  }
  &__thumbnail {
    display: grid;
    place-items: center;
    width: 128px;
    height: 72px;
    background: var(--color-surface);
    color: var(--color-muted);
    flex-shrink: 0;
  }
  &__text {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-top: 6px;
    min-width: 0;
    strong {
      font-size: 14px;
      font-weight: 500;
    }
    > span {
      color: var(--color-muted);
      font-size: 12px;
    }
    strong,
    > span {
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow-wrap: anywhere;
    }
  }
  &__access {
    display: flex;
    align-items: center;
    gap: 12px;
    svg {
      width: 20px;
      height: 20px;
      flex-shrink: 0;
    }
  }
}
@media (max-width: 767px) {
  .studio-playlists {
    h1 {
      font-size: 24px;
      line-height: 32px;
      margin: 0 16px 16px;
    }
    &__login {
      padding: 24px 16px;
      h1 {
        margin: 0;
      }
      .text-link {
        margin: 20px 0 0;
        display: block;
      }
    }
  }
  .studio-table {
    min-width: 760px;
    th:first-child {
      width: 40%;
      padding-left: 16px;
    }
    td:first-child {
      padding-left: 16px;
    }
    &__thumbnail {
      width: 96px;
      height: 54px;
    }
  }
}
</style>
