<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { useRoute, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  apiMessage,
  subscriptionV1ChannelsTagSubscriptionGet,
  setSubscriptionV1ChannelsTagSubscriptionPut,
  unwrap,
} from '../../../shared/api';
const props = defineProps<{ tag: string }>();
const session = useSession(),
  client = useQueryClient(),
  route = useRoute(),
  router = useRouter();
const tag = computed(() => props.tag.replace(/^@/, '').toLowerCase());
const key = computed(() => ['subscription', session.scope, tag.value] as const);
const identityReady = computed(
  () => !session.loading && !session.changing && !session.error,
);
const pending = ref(false),
  error = ref('');
let controller: AbortController | undefined;
watch(
  () => session.scope,
  () => controller?.abort(),
  { flush: 'sync' },
);
let disposed = false;
const query = useQuery({
  queryKey: key,
  enabled: computed(
    () => !!session.account && identityReady.value && !pending.value,
  ),
  queryFn: async ({ queryKey: [, , requestedTag], signal }) =>
    unwrap(
      await subscriptionV1ChannelsTagSubscriptionGet({
        path: { tag: requestedTag },
        signal,
      }),
    ),
  retry: false,
  gcTime: 0,
});
const selected = computed(
  () => !!session.account && !!query.data.value?.subscribed,
);
async function toggle() {
  if (pending.value || !identityReady.value) return;
  if (!session.account) {
    await router.push({ path: '/login', query: { returnTo: route.fullPath } });
    return;
  }
  if (
    query.isError.value ||
    !query.isFetchedAfterMount.value ||
    !query.data.value?.can_subscribe
  )
    return;
  const scope = session.scope,
    requestKey = key.value,
    requestedTag = tag.value,
    subscribed = !query.data.value.subscribed;
  pending.value = true;
  error.value = '';
  try {
    await client.cancelQueries({ queryKey: requestKey, exact: true });
    if (disposed || session.scope !== scope || tag.value !== requestedTag)
      return;
    controller = new AbortController();
    const value = unwrap(
      await setSubscriptionV1ChannelsTagSubscriptionPut({
        path: { tag: requestedTag },
        body: { subscribed },
        signal: controller.signal,
      }),
    );
    if (session.scope !== scope) return;
    if (!disposed && tag.value === requestedTag)
      client.setQueryData(requestKey, value);
    {
      const personal = {
        predicate: (q: { queryKey: readonly unknown[] }) =>
          q.queryKey[1] === scope &&
          (q.queryKey[0] === 'subscribed-channels' ||
            (q.queryKey[0] === 'library-videos' &&
              q.queryKey[2] === 'subscriptions')),
      };
      await client.cancelQueries(personal);
      if (session.scope === scope) void client.resetQueries(personal);
    }
  } catch (reason) {
    if (!disposed && session.scope === scope && tag.value === requestedTag)
      error.value = apiMessage(reason);
  } finally {
    pending.value = false;
  }
}
onBeforeUnmount(() => {
  disposed = true;
  // Let this small command settle after SPA navigation so personal lists refresh.
  // Scope changes still abort it; late results cannot update another viewer.
});
</script>
<template>
  <div class="subscribe">
    <button
      v-if="
        identityReady &&
        (!session.account ||
          (!query.isError.value &&
            query.isFetchedAfterMount.value &&
            query.data.value?.can_subscribe))
      "
      type="button"
      class="button"
      :class="{ 'subscribe--active': selected }"
      :aria-pressed="selected"
      :aria-disabled="pending"
      :aria-busy="pending"
      @click="toggle"
    >
      {{ selected ? 'Вы подписаны' : 'Подписаться' }}
    </button>
    <span
      v-else-if="
        !identityReady ||
        query.isPending.value ||
        (!!session.account && !query.isFetchedAfterMount.value)
      "
      class="subscribe__loading"
      role="status"
      >Загрузка подписки…</span
    >
    <button
      v-else-if="query.isError.value"
      class="text-link"
      type="button"
      @click="query.refetch()"
    >
      Повторить загрузку подписки
    </button>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </div>
</template>
<style scoped lang="scss">
.subscribe {
  margin-left: 12px;
  .button {
    background: var(--color-text);
    color: var(--color-background);
    white-space: nowrap;
    &[aria-disabled='true'] {
      opacity: 0.6;
      cursor: wait;
    }
  }
  .subscribe--active {
    background: var(--color-surface);
    color: var(--color-text);
  }
  p {
    font-size: 12px;
    max-width: 250px;
    margin: 4px 0;
  }
  &__loading {
    color: var(--color-muted);
    font-size: 12px;
  }
}
</style>
