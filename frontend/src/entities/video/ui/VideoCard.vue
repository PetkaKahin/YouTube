<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';
import type { FeedCardResponse } from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
import VideoHoverPreview from './VideoHoverPreview.vue';
import { useGuestWatchProgress } from '../model/guest-progress';
const props = defineProps<{
  video: FeedCardResponse;
  horizontal?: boolean;
  scope?: string;
  guest?: boolean;
}>();
const guestProgress = useGuestWatchProgress();
const active = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined,
  blocked = false;
function stopPreview() {
  clearTimeout(timer);
  active.value = false;
}
function startPreview(event: PointerEvent | FocusEvent) {
  if (
    ('pointerType' in event && event.pointerType === 'touch') ||
    !props.scope ||
    active.value ||
    blocked
  )
    return;
  clearTimeout(timer);
  timer = setTimeout(() => {
    active.value = true;
  }, 200);
}
function leavePreview() {
  stopPreview();
  blocked = false;
}
function blurPreview(event: FocusEvent) {
  if (
    !(event.currentTarget as HTMLElement).contains(
      event.relatedTarget as Node | null,
    )
  )
    leavePreview();
}
function previewStopped() {
  stopPreview();
}
function hoverFailed(access: boolean) {
  blocked = true;
  stopPreview();
  if (access) emit('previewError');
}
watch(
  () => `${props.scope}:${props.video.id}`,
  () => {
    stopPreview();
    blocked = false;
  },
);
onBeforeUnmount(stopPreview);
const watched = computed(() => {
  const progress = props.scope ? props.video.watch_progress : null;
  const position = props.guest
    ? guestProgress.position(props.video.id)
    : (progress?.position_seconds ?? 0);
  const duration = props.guest
    ? props.video.duration_seconds
    : (progress?.duration_seconds ?? 0);
  return Number.isFinite(position) && Number.isFinite(duration) && duration > 0
    ? Math.max(0, Math.min(100, (position / duration) * 100))
    : 0;
});
const emit = defineEmits<{ previewError: [] }>();
const broken = ref(false);
function previewFailed() {
  if (broken.value) return;
  broken.value = true;
  emit('previewError');
}
const duration = computed(() => {
  const seconds = Math.floor(props.video.duration_seconds);
  return seconds >= 3600
    ? `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
});
const date = computed(() =>
  new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(props.video.added_at)),
);
const channelTo = computed(
  () => `/@${encodeURIComponent(props.video.channel.tag)}`,
);
</script>
<template>
  <article
    class="video-card"
    :class="{
      'video-card--horizontal': horizontal,
      'video-card--previewing': active,
    }"
    @pointerenter="startPreview"
    @pointerleave="leavePreview"
    @focusin="startPreview"
    @focusout="blurPreview"
  >
    <div class="video-card__media">
      <RouterLink
        class="video-card__preview"
        :to="{ path: '/watch', query: { v: String(video.id) } }"
        :aria-label="video.title"
        ><img
          v-if="video.preview_url && !broken"
          :src="video.preview_url"
          alt=""
          loading="lazy"
          @error="previewFailed" /><AppIcon v-else name="video" /><span
          class="video-card__duration"
          >{{ duration }}</span
        ><span
          v-if="watched > 0 && !active"
          class="video-card__watched"
          role="progressbar"
          :aria-valuemin="0"
          :aria-valuemax="100"
          :aria-valuenow="Math.round(watched)"
          aria-label="Сохранённая позиция просмотра"
          ><i :style="{ width: watched + '%' }" /></span
      ></RouterLink>
      <VideoHoverPreview
        v-if="active && scope"
        :key="`${scope}:${video.id}`"
        :video-id="video.id"
        :scope="scope"
        :title="video.title"
        @stop="previewStopped"
        @failed="hoverFailed"
      />
    </div>
    <div class="video-card__details">
      <RouterLink
        :to="channelTo"
        class="video-card__avatar"
        :aria-label="`Канал ${video.channel.name}`"
        >{{ [...video.channel.name][0]?.toLocaleUpperCase('ru') }}</RouterLink
      >
      <div class="video-card__text">
        <h2>
          <RouterLink
            :to="{ path: '/watch', query: { v: String(video.id) } }"
            >{{ video.title }}</RouterLink
          >
        </h2>
        <RouterLink :to="channelTo" class="video-card__channel">{{
          video.channel.name
        }}</RouterLink><span class="video-card__date">{{ date }}</span>
      </div>
    </div>
  </article>
</template>
<style scoped lang="scss">
.video-card {
  min-width: 0;
  &--previewing {
    background: var(--color-surface);
    border-radius: 12px;
  }
  &__media {
    position: relative;
    min-width: 0;
    border-radius: 12px;
  }
  &__watched {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: #ffffff55;
    i {
      display: block;
      height: 100%;
      background: var(--color-watch-progress);
    }
  }
  &__preview {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    aspect-ratio: 16/9;
    background: var(--color-surface);
    position: relative;
    border-radius: 12px;
    overflow: hidden;
    img {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
    > svg {
      width: 48px;
      height: 48px;
      color: var(--color-muted);
    }
  }
  &__duration {
    position: absolute;
    bottom: 8px;
    right: 8px;
    padding: 2px 4px;
    background: #000c;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 500;
    line-height: 14px;
  }
  &__details {
    display: flex;
    gap: 12px;
    margin-top: 12px;
    min-width: 0;
  }
  &__avatar {
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--color-surface);
    flex-shrink: 0;
    font-size: 18px;
  }
  &__text {
    min-width: 0;
  }
  h2 {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 500;
    line-height: 22px;
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
  }
  &__channel,
  &__date {
    color: var(--color-muted);
    font-size: 14px;
    line-height: 20px;
    display: block;
    overflow-wrap: anywhere;
  }
  &__channel:hover {
    color: var(--color-text);
  }
  &--horizontal {
    display: grid;
    grid-template-columns: minmax(200px, 500px) minmax(0, 1fr);
    gap: 16px;
    .video-card__details {
      margin-top: 0;
    }
    .video-card__avatar {
      display: none;
    }
    h2 {
      font-size: 18px;
      line-height: 26px;
      font-weight: 400;
    }
    .video-card__date {
      font-size: 12px;
    }
    .video-card__channel {
      margin-top: 12px;
      font-size: 12px;
    }
  }
}
@media (max-width: 767px) {
  .video-card {
    &__media {
      border-radius: 0;
    }
    &__preview {
      border-radius: 0;
    }
    &__details {
      padding: 0 12px;
    }
    &--horizontal {
      grid-template-columns: 160px minmax(0, 1fr);
      gap: 8px;
      .video-card__media {
        border-radius: 8px;
      }
      .video-card__preview {
        border-radius: 8px;
      }
      .video-card__details {
        padding: 0;
      }
      h2 {
        font-size: 14px;
        line-height: 20px;
      }
      .video-card__channel {
        margin-top: 4px;
      }
    }
  }
}
@media (max-width: 380px) {
  .video-card--horizontal {
    grid-template-columns: 140px minmax(0, 1fr);
  }
}
</style>
