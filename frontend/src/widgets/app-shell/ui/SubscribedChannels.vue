<script setup lang="ts">
import { computed, ref } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { useInfiniteQuery } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  isIdentifier,
  subscriptionsV1ChannelsSubscriptionsGet,
  unwrap,
} from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
const session = useSession(),
  route = useRoute();
const expanded = ref(false);
const query = useInfiniteQuery({
  queryKey: computed(() => ['subscribed-channels', session.scope]),
  enabled: computed(
    () =>
      !!session.account &&
      !session.changing &&
      !session.loading &&
      !session.error,
  ),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const page = unwrap(
      await subscriptionsV1ChannelsSubscriptionsGet({
        query: { cursor: pageParam },
        signal,
      }),
    );
    if (
      page.items.some(
        (c) => !isIdentifier(c.id) || !/^[a-z0-9._-]{3,30}$/i.test(c.tag),
      )
    )
      throw new Error('Invalid subscribed channel');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  gcTime: 0,
  retry: false,
});
const channels = computed(() =>
  session.account &&
  !session.changing &&
  !session.error &&
  query.isFetchedAfterMount.value &&
  (!query.isError.value || query.isFetchNextPageError.value)
    ? [
        ...new Map(
          query.data.value?.pages
            .flatMap((p) => p.items)
            .map((c) => [c.id, c]) ?? [],
        ).values(),
      ]
    : [],
);
const shown = computed(() =>
  expanded.value ? channels.value : channels.value.slice(0, 7),
);
</script>
<template>
  <div class="subscribed-channels">
    <p v-if="query.isPending.value" role="status">Загружаем каналы…</p>
    <button
      v-else-if="query.isError.value && !query.isFetchNextPageError.value"
      class="subscribed-channels__retry"
      type="button"
      @click="query.refetch()"
    >
      Повторить загрузку каналов
    </button>
    <p v-else-if="!channels.length">Вы пока ни на кого не подписаны</p>
    <template v-else>
      <RouterLink
        v-for="channel in shown"
        :key="channel.id"
        :to="`/@${encodeURIComponent(channel.tag)}`"
        class="subscribed-channels__channel"
        :aria-current="route.params.tag === channel.tag ? 'page' : undefined"
      >
        <span class="subscribed-channels__avatar" aria-hidden="true">{{
          [...channel.name][0]?.toLocaleUpperCase('ru')
        }}</span><span class="subscribed-channels__name">{{
          channel.name
        }}</span>
      </RouterLink>
      <button
        v-if="!expanded && (channels.length > 7 || query.hasNextPage.value)"
        type="button"
        :aria-expanded="expanded"
        @click="expanded = true"
      >
        <AppIcon name="down" />Развернуть
      </button>
      <template v-if="expanded">
        <button
          v-if="query.hasNextPage.value"
          type="button"
          :disabled="query.isFetching.value"
          @click="query.fetchNextPage()"
        >
          {{
            query.isFetching.value
              ? 'Загружаем…'
              : query.isFetchNextPageError.value
                ? 'Повторить загрузку каналов'
                : 'Ещё каналы'
          }}
        </button>
        <button
          type="button"
          :aria-expanded="expanded"
          @click="expanded = false"
        >
          <AppIcon name="down" class="subscribed-channels__up" />Свернуть
        </button>
      </template>
    </template>
  </div>
</template>
<style scoped lang="scss">
.subscribed-channels {
  min-width: 0;
  p {
    color: var(--color-muted);
    font-size: 12px;
    line-height: 18px;
    padding: 0 12px;
  }
  &__channel,
  button {
    width: 100%;
    display: flex;
    align-items: center;
    min-height: 40px;
    gap: 24px;
    padding: 0 12px;
    border: 0;
    border-radius: 10px;
    background: transparent;
    text-align: left;
    font-size: 14px;
    cursor: pointer;
    &:hover,
    &[aria-current='page'] {
      background: var(--color-surface);
    }
  }
  &__avatar {
    display: grid;
    place-items: center;
    height: 24px;
    width: 24px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--color-surface);
  }
  &__name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  &__up {
    transform: rotate(180deg);
  }
  &__retry {
    line-height: 18px;
  }
}
</style>
