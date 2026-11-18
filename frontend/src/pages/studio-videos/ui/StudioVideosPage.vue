<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query';
import { StudioShell } from '../../../widgets/studio-shell';
import { StudioContentTabs } from '../../../widgets/studio-content-tabs';
import { useSession } from '../../../entities/session';
import {
  OwnerVideoPreview,
  videoState,
  canWatchOwnedVideo,
} from '../../../entities/video';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  mineV1VideosMineGet,
  unwrap,
} from '../../../shared/api';
import { useNextPage } from '../../../shared/lib/pagination';
import { AppIcon, PageState } from '../../../shared/ui';

const session = useSession(),
  route = useRoute(),
  client = useQueryClient();
const sentinel = ref<HTMLElement>(),
  denied = ref(false);
const publication = computed(() =>
  route.query.publication === undefined ||
  route.query.publication === 'published'
    ? 'published'
    : route.query.publication === 'draft'
      ? 'draft'
      : undefined,
);
const key = computed(() => ['studio-videos', session.scope, publication.value]);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(
    () =>
      !!session.account &&
      !session.changing &&
      !!publication.value &&
      !denied.value,
  ),
  gcTime: 0,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      await mineV1VideosMineGet({
        query: {
          publication: publication.value === 'draft' ? 'draft' : undefined,
          cursor: pageParam,
        },
        signal,
      }),
    );
    if (
      page.items.some(
        (item) =>
          !isIdentifier(item.id) ||
          item.owner_id !== session.account?.id ||
          item.publication !== publication.value ||
          !['public', 'private'].includes(item.visibility),
      )
    )
      throw new Error('Invalid own video response');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
});
const items = computed(() => {
  if (!session.account || denied.value) return [];
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
    denied.value = false;
  },
);
watch(publication, () => {
  denied.value = false;
});
watch(query.error, (cause) => {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    denied.value = true;
    client.removeQueries({ queryKey: key.value, exact: true });
  }
});
function more() {
  if (
    query.hasNextPage.value &&
    !query.isFetching.value &&
    !query.isError.value
  )
    void query.fetchNextPage();
}
useNextPage(sentinel, more);
function retry() {
  if (denied.value) denied.value = false;
  else if (query.isFetchNextPageError.value) void query.fetchNextPage();
  else void query.refetch();
}
const dateFormat = new Intl.DateTimeFormat('ru', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});
function date(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? '—' : dateFormat.format(parsed);
}
const previousTitle = document.title;
document.title = 'Видео — YouTube Studio';
onBeforeUnmount(() => {
  document.title = previousTitle;
  const old = new Set(
    client.getQueryCache().findAll({ queryKey: key.value, exact: true }),
  );
  const filter = {
    predicate: (item: Parameters<typeof old.has>[0]) => old.has(item),
  };
  void client.cancelQueries(filter).then(() => client.removeQueries(filter));
});
</script>
<template>
  <StudioShell
    ><main id="main-content" class="studio-videos">
      <h1>Контент на канале</h1>
      <StudioContentTabs />
      <PageState
        v-if="session.loading || session.changing"
        title="Проверяем аккаунт"
      />
      <PageState
        v-else-if="session.error"
        title="Не удалось проверить аккаунт"
        :description="apiMessage(session.error)"
        error
        @retry="session.refresh()"
      />
      <PageState v-else-if="!session.account" title="Войдите в YouTube Studio"
        ><RouterLink
          :to="{ path: '/login', query: { returnTo: route.fullPath } }"
          class="button button--primary"
          >Войти</RouterLink
        ></PageState
      >
      <template v-else
        ><nav class="publication-tabs" aria-label="Публикация">
          <RouterLink
            to="/studio/content/videos"
            :aria-current="publication === 'published' ? 'page' : undefined"
            >Опубликованные</RouterLink
          ><RouterLink
            :to="{
              path: '/studio/content/videos',
              query: { publication: 'draft' },
            }"
            :aria-current="publication === 'draft' ? 'page' : undefined"
            >Черновики</RouterLink
          >
        </nav>
        <PageState v-if="!publication" title="Неверный раздел"
          ><RouterLink to="/studio/content/videos" class="text-link"
            >Открыть опубликованные видео</RouterLink
          ></PageState
        >
        <PageState
          v-else-if="denied"
          title="Видео недоступны"
          error
          @retry="retry"
        />
        <PageState v-else-if="query.isPending.value" title="Загружаем видео" />
        <PageState
          v-else-if="query.isError.value && !items.length"
          title="Не удалось загрузить видео"
          :description="apiMessage(query.error.value)"
          error
          @retry="retry"
        />
        <template v-else
          ><div
            v-if="items.length"
            class="studio-videos__scroll"
            tabindex="0"
            role="region"
            aria-label="Свои видео"
          >
            <table class="own-video-table">
              <thead>
                <tr>
                  <th scope="col">Видео</th>
                  <th scope="col">Доступ</th>
                  <th scope="col">Состояние</th>
                  <th scope="col">Дата создания</th>
                  <th scope="col">Последнее изменение</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="video in items"
                  :key="`${session.scope}:${video.id}`"
                  :data-video-id="video.id"
                >
                  <td>
                    <RouterLink
                      :to="`/studio/video/${video.id}/edit`"
                      class="own-video-title"
                      ><OwnerVideoPreview :video-id="video.id" /><span
                        ><strong>{{ video.title }}</strong
                        ><small>{{
                          video.description || 'Без описания'
                        }}</small></span
                      ></RouterLink
                    >
                    <div class="own-video-actions">
                      <RouterLink :to="`/studio/video/${video.id}/edit`"
                        >Изменить</RouterLink
                      ><RouterLink
                        v-if="canWatchOwnedVideo(video)"
                        :to="{ path: '/watch', query: { v: video.id } }"
                        >Смотреть</RouterLink
                      >
                    </div>
                  </td>
                  <td>
                    <span class="own-video-access"
                      ><AppIcon
                        :name="
                          video.visibility === 'private' ? 'lock' : 'globe'
                        "
                      />{{
                        video.visibility === 'private'
                          ? 'Ограниченный'
                          : 'Для всех'
                      }}</span
                    >
                  </td>
                  <td
                    :class="{
                      'form-error':
                        video.moderation === 'hidden' ||
                        video.processing === 'failed' ||
                        video.upload === 'failed',
                    }"
                  >
                    {{ videoState(video) }}
                  </td>
                  <td>
                    <time :datetime="video.created_at">{{
                      date(video.created_at)
                    }}</time>
                  </td>
                  <td>
                    <time :datetime="video.updated_at">{{
                      date(video.updated_at)
                    }}</time>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PageState
            v-else
            :title="
              publication === 'draft'
                ? 'Пока нет черновиков'
                : 'Пока нет опубликованных видео'
            "
            ><RouterLink to="/studio/upload" class="button button--secondary"
              >Добавить видео</RouterLink
            ></PageState
          >
          <div ref="sentinel" class="studio-videos__more">
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
              Повторить загрузку</button
            ><button
              v-else-if="query.hasNextPage.value"
              type="button"
              class="button button--secondary"
              :disabled="query.isFetching.value"
              @click="more"
            >
              {{
                query.isFetchingNextPage.value ? 'Загружаем…' : 'Показать ещё'
              }}
            </button>
          </div>
        </template>
      </template>
    </main></StudioShell
  >
</template>
<style scoped lang="scss">
.studio-videos {
  padding-top: 24px;
  h1 {
    margin: 0 32px 20px;
    font-size: 32px;
    line-height: 40px;
  }
  &__scroll {
    overflow-x: auto;
  }
  &__more {
    text-align: center;
    padding: 24px;
  }
}
.publication-tabs {
  display: flex;
  gap: 12px;
  padding: 16px 32px;
  a {
    border: 1px solid var(--color-border);
    border-radius: 20px;
    padding: 8px 16px;
    &[aria-current] {
      background: var(--color-text);
      color: var(--color-background);
    }
  }
}
.own-video-table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  table-layout: fixed;
  th,
  td {
    padding: 16px 24px;
    border-bottom: 1px solid var(--color-border);
    text-align: left;
    vertical-align: top;
    font-size: 14px;
  }
  th {
    color: var(--color-muted);
    font-size: 12px;
    font-weight: 400;
  }
  th:first-child {
    width: 43%;
    padding-left: 32px;
  }
  td:first-child {
    padding-left: 32px;
  }
  th:nth-child(2) {
    width: 16%;
  }
}
.own-video-title {
  display: flex;
  gap: 16px;
  > span:last-child {
    min-width: 0;
    padding-top: 4px;
  }
  strong,
  small {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  strong {
    font-size: 14px;
    font-weight: 500;
  }
  small {
    margin-top: 6px;
    color: var(--color-muted);
    font-size: 12px;
  }
  &:hover strong {
    text-decoration: underline;
  }
}
.own-video-actions {
  display: flex;
  gap: 20px;
  margin: 10px 0 0 160px;
  font-size: 12px;
  color: var(--color-accent);
}
.own-video-access {
  display: flex;
  align-items: center;
  gap: 8px;
  svg {
    width: 20px;
    height: 20px;
    flex-shrink: 0;
  }
}
@media (max-width: 767px) {
  .studio-videos h1 {
    margin: 0 16px 16px;
    font-size: 24px;
    line-height: 32px;
  }
  .publication-tabs {
    padding: 16px;
    gap: 8px;
    a {
      font-size: 12px;
    }
  }
}
</style>
