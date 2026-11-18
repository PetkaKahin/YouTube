<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { useRoute, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  stateV1VideosVideoIdLikeGet,
  setStateV1VideosVideoIdLikePut,
  setDislikeV1VideosVideoIdDislikePut,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
const props = defineProps<{ videoId: Identifier }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient(),
  route = useRoute(),
  router = useRouter();
const key = computed(
  () => ['video-like', session.scope, props.videoId] as const,
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
  enabled: computed(() => !pending.value),
  queryFn: async ({ queryKey: [, , videoId], signal }) =>
    unwrap(
      await stateV1VideosVideoIdLikeGet({
        path: { video_id: videoId },
        signal,
      }),
    ),
  retry: false,
  gcTime: 0,
});
function accessDenied(reason: unknown) {
  return (
    reason instanceof ApiError &&
    (reason.status === 404 ||
      (reason.status === 403 && reason.code !== 'csrf_failed'))
  );
}
watch(query.error, (error) => {
  if (accessDenied(error)) emit('accessLost');
});
async function toggle(reaction: 'like' | 'dislike') {
  if (pending.value || session.loading || session.changing || session.error)
    return;
  if (!session.account) {
    await router.push({ path: '/login', query: { returnTo: route.fullPath } });
    return;
  }
  if (
    !query.data.value ||
    !(reaction === 'like'
      ? query.data.value.can_like
      : query.data.value.can_dislike)
  )
    return;
  const scope = session.scope,
    requestKey = key.value,
    videoId = props.videoId,
    selected =
      reaction === 'like'
        ? !query.data.value.liked
        : !query.data.value.disliked;
  pending.value = true;
  error.value = '';
  try {
    // An older state read must not replace the command's confirmed result.
    await client.cancelQueries({ queryKey: requestKey, exact: true });
    if (disposed || session.scope !== scope || props.videoId !== videoId)
      return;
    controller = new AbortController();
    const value = unwrap(
      reaction === 'like'
        ? await setStateV1VideosVideoIdLikePut({
            path: { video_id: videoId },
            body: { liked: selected },
            signal: controller.signal,
          })
        : await setDislikeV1VideosVideoIdDislikePut({
            path: { video_id: videoId },
            body: { disliked: selected },
            signal: controller.signal,
          }),
    );
    if (session.scope !== scope) return;
    if (!disposed && props.videoId === videoId)
      client.setQueryData(requestKey, value);
    {
      await client.cancelQueries({
        queryKey: ['library-videos', scope, 'liked'],
      });
      if (session.scope === scope)
        void client.resetQueries({
          queryKey: ['library-videos', scope, 'liked'],
        });
    }
  } catch (reason) {
    if (!disposed && session.scope === scope && props.videoId === videoId) {
      error.value = apiMessage(reason);
      if (accessDenied(reason)) emit('accessLost');
    }
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
  <div class="video-like">
    <div
      v-if="query.data.value && !query.isError.value"
      class="video-like__buttons"
      role="group"
      aria-label="Оценка видео"
    >
      <button
        class="button video-like__like"
        :class="{ 'video-like--selected': query.data.value.liked }"
        type="button"
        :aria-pressed="query.data.value.liked"
        :aria-disabled="pending"
        :aria-busy="pending"
        :aria-label="
          query.data.value.liked ? 'Снять отметку Нравится' : 'Нравится'
        "
        :disabled="
          session.loading ||
          session.changing ||
          (!!session.account && !query.data.value.can_like)
        "
        @click="toggle('like')"
      >
        <AppIcon name="like" /><span>{{
          new Intl.NumberFormat('ru-RU').format(query.data.value.like_count)
        }}</span>
      </button>
      <button
        class="button video-like__dislike"
        :class="{ 'video-like--selected': query.data.value.disliked }"
        type="button"
        :aria-label="
          query.data.value.disliked
            ? 'Снять отметку Не нравится'
            : 'Не нравится'
        "
        :aria-pressed="query.data.value.disliked"
        :aria-disabled="pending"
        :aria-busy="pending"
        :disabled="
          session.loading ||
          session.changing ||
          (!!session.account && !query.data.value.can_dislike)
        "
        @click="toggle('dislike')"
      >
        <AppIcon name="dislike" />
      </button>
    </div>
    <span
      v-else-if="query.isPending.value"
      class="video-like__loading"
      role="status"
      >Загрузка…</span
    >
    <button v-else class="text-link" type="button" @click="query.refetch()">
      Повторить загрузку лайков
    </button>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </div>
</template>
<style scoped lang="scss">
.video-like {
  margin-left: auto;
  .button {
    background: transparent;
    min-width: 104px;
    min-height: 48px;
    border-radius: 24px;
    gap: 10px;
    padding: 0 20px;
    svg {
      width: 28px;
      height: 28px;
    }
    &[aria-disabled='true'] {
      opacity: 0.6;
      cursor: wait;
    }
  }
  .video-like--selected {
    color: var(--color-text);
    svg {
      fill: currentColor;
    }
  }
  &__buttons {
    display: inline-flex;
    align-items: stretch;
    border-radius: 24px;
    background: var(--color-surface);
    overflow: hidden;
  }
  .video-like__like {
    border-radius: 24px 0 0 24px;
  }
  .video-like__dislike {
    min-width: 64px;
    padding: 0 20px;
    border-radius: 0 24px 24px 0;
    position: relative;
    &::before {
      content: '';
      position: absolute;
      left: 0;
      top: 12px;
      bottom: 12px;
      width: 1px;
      background: var(--color-border);
    }
  }
  .button:hover {
    background: var(--color-hover);
  }
  p {
    font-size: 12px;
    max-width: 250px;
    margin: 4px 0;
  }
  &__loading {
    font-size: 12px;
    color: var(--color-muted);
  }
}
</style>
