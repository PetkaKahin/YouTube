<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { useInfiniteQuery } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import { VideoCard, useVideoCardVerification } from '../../../entities/video';
import { isIdentifier, searchV1SearchGet, unwrap } from '../../../shared/api';
import { AppIcon, PageState, StudioDialog } from '../../../shared/ui';
import SearchFilters from './SearchFilters.vue';
import { readFilters, type FilterKey } from '../model/filters';
import { useNextPage } from '../../../shared/lib/pagination';
const route = useRoute();
const router = useRouter();
const session = useSession();
const filtersOpen = ref(false);
const next = ref<HTMLElement>();
const query = computed(() =>
  typeof route.query.search_query === 'string'
    ? route.query.search_query.trim()
    : '',
);
const filterState = computed(() => readFilters(route.query));
const params = computed(() => ({
  q: query.value,
  ...filterState.value.values,
}));
const filterButton = ref<HTMLButtonElement>();
const filterPanel = ref<HTMLElement>();
const phoneMedia = window.matchMedia('(max-width: 767px)');
const phone = ref(phoneMedia.matches);
let previousOverflow: string | undefined;
function unlockScroll() {
  if (previousOverflow !== undefined) {
    document.body.style.overflow = previousOverflow;
    previousOverflow = undefined;
  }
}
watch(
  () => phone.value && filtersOpen.value,
  (open) => {
    if (open && previousOverflow === undefined) {
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    } else if (!open) unlockScroll();
  },
  { flush: 'sync' },
);
function closeFilters(restore = true) {
  const wasOpen = filtersOpen.value;
  filtersOpen.value = false;
  if (restore && wasOpen) void nextTick(() => filterButton.value?.focus());
}
function viewportChanged() {
  closeFilters(false);
  phone.value = phoneMedia.matches;
}
function outside(event: PointerEvent) {
  const target = event.target as Node;
  if (
    !phone.value &&
    filtersOpen.value &&
    !filterPanel.value?.contains(target) &&
    !filterButton.value?.contains(target)
  )
    closeFilters(false);
}
function escape(event: KeyboardEvent) {
  if (!phone.value && filtersOpen.value && event.key === 'Escape') {
    event.preventDefault();
    closeFilters();
  }
}
phoneMedia.addEventListener('change', viewportChanged);
document.addEventListener('pointerdown', outside);
document.addEventListener('keydown', escape);
onBeforeUnmount(() => {
  unlockScroll();
  phoneMedia.removeEventListener('change', viewportChanged);
  document.removeEventListener('pointerdown', outside);
  document.removeEventListener('keydown', escape);
});
watch(
  () => [route.fullPath, session.scope],
  () => closeFilters(false),
);
const valid = computed(
  () => [...query.value].length > 0 && [...query.value].length <= 100,
);
const results = useInfiniteQuery({
  queryKey: computed(() => ['search', session.scope, params.value]),
  enabled: computed(() => valid.value && filterState.value.valid),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      await searchV1SearchGet({
        query: { ...params.value, cursor: pageParam },
        signal,
      }),
    );
    const identifiers = page.items
      .flatMap((item) => [
        item.video?.id,
        item.video?.channel.id,
        item.channel?.id,
        item.playlist?.id,
        item.playlist?.channel.id,
      ])
      .filter((value) => value !== undefined);
    if (identifiers.some((value) => !isIdentifier(value)))
      throw new Error('Unsupported identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
  gcTime: 0,
});
const verifyPreviewAccess = useVideoCardVerification(
  () => results.refetch(),
  () => results.isError.value,
);
const items = computed(() => [
  ...new Map(
    results.data.value?.pages
      .flatMap((page) => page.items)
      .map((item) => [
        `${item.type}:${item.video?.id ?? item.channel?.id ?? item.playlist?.id}`,
        item,
      ]) ?? [],
  ).values(),
]);
function select(key: FilterKey, value: string) {
  const current = filterState.value.values[key];
  let next: string | string[] | undefined;
  if (key === 'feature') {
    const values = filterState.value.values.feature;
    next = values.includes(value)
      ? values.filter((item) => item !== value)
      : [...values, value];
  } else next = current === value ? undefined : value;
  closeFilters();
  void router.push({
    query: { ...route.query, cursor: undefined, [key]: next },
  });
}
function more() {
  if (
    results.hasNextPage.value &&
    !results.isFetching.value &&
    !results.isFetchNextPageError.value
  )
    void results.fetchNextPage();
}
useNextPage(next, more);
</script>
<template>
  <main
    id="main-content"
    class="search-page"
    aria-label="Результаты поиска"
    tabindex="-1"
  >
    <button
      ref="filterButton"
      class="filters-toggle"
      type="button"
      :aria-expanded="filtersOpen"
      aria-controls="search-filters"
      :aria-haspopup="phone ? 'dialog' : undefined"
      @click="filtersOpen = !filtersOpen"
    >
      <AppIcon name="filter" />Фильтры
    </button>
    <section
      v-if="filtersOpen && !phone"
      id="search-filters"
      ref="filterPanel"
      class="search-page__filters"
      aria-label="Фильтры поиска"
    >
      <SearchFilters :values="filterState.values" @select="select" />
    </section>
    <StudioDialog
      v-if="phone"
      :open="filtersOpen"
      title="Фильтры поиска"
      @close="closeFilters()"
    >
      <section id="search-filters" aria-label="Параметры поиска">
        <SearchFilters :values="filterState.values" @select="select" />
      </section>
    </StudioDialog>
    <PageState
      v-if="!valid"
      title="Введите поисковый запрос"
      description="От 1 до 100 символов."
    />
    <PageState
      v-else-if="!filterState.valid"
      title="Некорректные фильтры поиска"
      description="Проверьте параметры в адресе или сбросьте фильтры."
      role="alert"
    >
      <RouterLink
        class="button button--secondary"
        :to="{ path: '/results', query: { search_query: query } }"
        >Сбросить фильтры</RouterLink
      >
    </PageState>
    <p
      v-else-if="results.isPending.value"
      role="status"
      class="search-page__loading"
    >
      Ищем…
    </p>
    <PageState
      v-else-if="results.isError.value && !results.isFetchNextPageError.value"
      title="Не удалось выполнить поиск"
      description="Проверьте параметры или повторите запрос."
      error
      @retry="results.refetch()"
    />
    <PageState
      v-else-if="!items.length"
      title="Ничего не найдено"
      description="Попробуйте изменить запрос или фильтры."
    />
    <div v-else class="search-results">
      <template
        v-for="item in items"
        :key="`${item.type}:${item.video?.id ?? item.channel?.id ?? item.playlist?.id}`"
      >
        <VideoCard
          v-if="item.type === 'video' && item.video"
          :video="item.video"
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
        <article
          v-else-if="item.type === 'channel' && item.channel"
          class="channel-result"
        >
          <RouterLink
            :to="`/@${encodeURIComponent(item.channel.tag)}`"
            class="channel-result__avatar"
            :aria-label="item.channel.name"
            >{{
              [...item.channel.name][0]?.toLocaleUpperCase('ru')
            }}</RouterLink
          >
          <div>
            <h2>
              <RouterLink :to="`/@${encodeURIComponent(item.channel.tag)}`">{{
                item.channel.name
              }}</RouterLink>
            </h2>
            <p>@{{ item.channel.tag }}</p>
            <p>Канал</p>
          </div>
        </article>
        <article
          v-else-if="item.type === 'playlist' && item.playlist"
          class="playlist-result"
        >
          <RouterLink
            :to="{
              path: `/@${encodeURIComponent(item.playlist.channel.tag)}/playlists`,
              query: { list: item.playlist.id },
            }"
            class="playlist-result__preview"
            :aria-label="item.playlist.title"
            ><AppIcon name="playlist"
          /></RouterLink>
          <div>
            <h2>
              <RouterLink
                :to="{
                  path: `/@${encodeURIComponent(item.playlist.channel.tag)}/playlists`,
                  query: { list: item.playlist.id },
                }"
                >{{ item.playlist.title }}</RouterLink
              >
            </h2>
            <RouterLink
              :to="`/@${encodeURIComponent(item.playlist.channel.tag)}`"
              >{{ item.playlist.channel.name }}</RouterLink
            >
            <p>Плейлист</p>
            <p class="playlist-result__description">
              {{ item.playlist.description }}
            </p>
          </div>
        </article>
      </template>
    </div>
    <div
      v-if="results.hasNextPage.value"
      ref="next"
      class="search-page__pagination"
    >
      <button
        class="button button--secondary"
        :disabled="results.isFetchingNextPage.value"
        type="button"
        @click="results.fetchNextPage()"
      >
        {{
          results.isFetchNextPageError.value
            ? 'Повторить загрузку'
            : results.isFetchingNextPage.value
              ? 'Загружаем…'
              : 'Ещё результаты'
        }}
      </button>
    </div>
  </main>
</template>
<style scoped lang="scss">
.search-page {
  max-width: 1298px;
  margin: 0 auto;
  padding: 16px 24px 48px;
  &__filters {
    border-bottom: 1px solid var(--color-border);
  }
  &__loading {
    padding: 32px;
    color: var(--color-muted);
  }
  &__pagination {
    text-align: center;
    padding: 32px;
  }
}
.filters-toggle {
  height: 40px;
  border: 0;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px;
  font-weight: 500;
  font-size: 14px;
  border-radius: 20px;
  cursor: pointer;
  &:hover {
    background: var(--color-surface);
  }
}
.search-results {
  display: flex;
  flex-direction: column;
  gap: 16px;
  border-top: 1px solid var(--color-border);
}
.channel-result,
.playlist-result {
  display: grid;
  grid-template-columns: minmax(200px, 500px) minmax(0, 1fr);
  gap: 16px;
  align-items: center;
  min-width: 0;
  h2 {
    margin: 0 0 8px;
    font-weight: 400;
    font-size: 18px;
    overflow-wrap: anywhere;
  }
  p,
  > div > a {
    color: var(--color-muted);
    font-size: 12px;
    line-height: 18px;
    margin: 4px 0;
    overflow-wrap: anywhere;
  }
}
.channel-result__avatar {
  border-radius: 50%;
  height: 136px;
  width: 136px;
  justify-self: center;
  display: grid;
  place-items: center;
  background: var(--color-surface);
  font-size: 56px;
}
.playlist-result {
  align-items: start;
  &__preview {
    aspect-ratio: 16/9;
    display: grid;
    place-items: center;
    background: var(--color-surface);
    border-radius: 12px;
    svg {
      width: 48px;
      height: 48px;
    }
  }
  &__description {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
}
@media (max-width: 767px) {
  .search-page {
    padding: 8px 12px 24px;
  }
  .channel-result,
  .playlist-result {
    grid-template-columns: 160px minmax(0, 1fr);
    gap: 8px;
    h2 {
      font-size: 14px;
      line-height: 20px;
    }
  }
  .channel-result__avatar {
    width: 96px;
    height: 96px;
    font-size: 36px;
  }
}
@media (max-width: 380px) {
  .channel-result,
  .playlist-result {
    grid-template-columns: 140px minmax(0, 1fr);
  }
}
</style>
