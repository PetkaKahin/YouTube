<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';
import { useQuery } from '@tanstack/vue-query';
import type Hls from 'hls.js';
import {
  ApiError,
  viewV1VideosVideoIdGet,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { mediaUrl, playbackTime } from '../../../shared/lib/media';
import { AppIcon } from '../../../shared/ui';
import { claimVideoPreview } from '../model/preview-lease';
const props = defineProps<{
  videoId: Identifier;
  scope: string;
  title: string;
}>();
const emit = defineEmits<{ stop: []; failed: [access: boolean] }>();
const root = ref<HTMLElement>(),
  video = ref<HTMLVideoElement>();
const playing = ref(false),
  muted = ref(true),
  captions = ref(false),
  hasCaptions = ref(false),
  position = ref(0),
  duration = ref(0);
let hls: Hls | undefined,
  disposed = false,
  attached = false,
  release: (() => void) | undefined,
  observer: IntersectionObserver | undefined;
const prefix = `/api/v1/videos/${props.videoId}/hls/`;
function destroy() {
  hls?.destroy();
  hls = undefined;
  const element = video.value;
  if (element) {
    element.pause();
    element.removeAttribute('src');
    element.load();
  }
  playing.value = false;
}
function failure(access = false) {
  if (disposed) return;
  destroy();
  emit('failed', access);
}
const query = useQuery({
  queryKey: ['card-preview', props.scope, props.videoId],
  gcTime: 0,
  retry: false,
  queryFn: async ({ signal }) => {
    const value = unwrap(
      await viewV1VideosVideoIdGet({
        path: { video_id: props.videoId },
        signal,
      }),
    );
    if (value.id !== props.videoId) throw new Error('Invalid preview video');
    mediaUrl(value.hls_url, prefix);
    return value;
  },
});
watch(query.error, (error) => {
  if (error)
    failure(
      error instanceof ApiError && [401, 403, 404].includes(error.status),
    );
});
async function attach() {
  if (attached || !query.data.value || !video.value || disposed) return;
  attached = true;
  const element = video.value,
    src = mediaUrl(query.data.value.hls_url, prefix);
  try {
    const { default: Engine } = await import('hls.js');
    if (disposed) return;
    element.muted = true;
    if (Engine.isSupported()) {
      const instance = new Engine({
        enableWorker: false,
        autoStartLoad: false,
        backBufferLength: 0,
        maxBufferLength: 8,
        maxMaxBufferLength: 15,
      });
      hls = instance;
      instance.subtitleDisplay = false;
      instance.config.xhrSetup = (_xhr, url) => {
        mediaUrl(url, prefix);
      };
      instance.on(Engine.Events.MANIFEST_PARSED, () => {
        if (disposed || instance !== hls) return;
        const lowest = instance.levels.reduce(
          (best, level, index) =>
            level.height < instance.levels[best]!.height ||
            (level.height === instance.levels[best]!.height &&
              level.bitrate < instance.levels[best]!.bitrate)
              ? index
              : best,
          0,
        );
        instance.loadLevel = lowest;
        instance.autoLevelCapping = lowest;
        instance.startLevel = lowest;
        hasCaptions.value = instance.subtitleTracks.length > 0;
        instance.startLoad(0);
        void element.play().catch(() => {
          if (!disposed) failure();
        });
      });
      instance.on(Engine.Events.SUBTITLE_TRACKS_UPDATED, () => {
        if (!disposed) hasCaptions.value = instance.subtitleTracks.length > 0;
      });
      instance.on(Engine.Events.ERROR, (_event, data) => {
        if (disposed) return;
        const status = data.response?.code;
        if (status && [401, 403, 404].includes(status)) failure(true);
        else if (data.fatal) failure();
      });
      instance.loadSource(src);
      instance.attachMedia(element);
    } else if (element.canPlayType('application/vnd.apple.mpegurl')) {
      element.src = src;
      await element.play();
    } else failure();
  } catch {
    if (!disposed) failure();
  }
}
watch(
  [query.data, video],
  () => {
    void attach();
  },
  { flush: 'post' },
);
function update() {
  const element = video.value;
  if (!element) return;
  position.value = Number.isFinite(element.currentTime)
    ? element.currentTime
    : 0;
  duration.value = Number.isFinite(element.duration) ? element.duration : 0;
}
function seek(event: Event) {
  const value = Number((event.target as HTMLInputElement).value);
  if (video.value && Number.isFinite(value) && duration.value > 0) {
    video.value.currentTime = Math.max(0, Math.min(duration.value, value));
    update();
  }
}
function toggleMute() {
  if (video.value) {
    video.value.muted = !video.value.muted;
    muted.value = video.value.muted;
  }
}
function toggleCaptions() {
  captions.value = !captions.value;
  if (hls) {
    hls.subtitleTrack = captions.value ? 0 : -1;
    hls.subtitleDisplay = captions.value;
  } else if (video.value) {
    Array.from(video.value.textTracks).forEach((track, index) => {
      track.mode = captions.value && index === 0 ? 'showing' : 'disabled';
    });
  }
}
function tracks() {
  if (video.value && !hls)
    hasCaptions.value = video.value.textTracks.length > 0;
}
function hidden() {
  if (document.hidden) {
    destroy();
    emit('stop');
  }
}
const percentage = computed(() =>
  duration.value > 0
    ? Math.min(100, (position.value / duration.value) * 100)
    : 0,
);
onMounted(() => {
  release = claimVideoPreview(() => {
    destroy();
    emit('stop');
  });
  document.addEventListener('visibilitychange', hidden);
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => !entry.isIntersecting)) {
      destroy();
      emit('stop');
    }
  });
  if (root.value) observer.observe(root.value);
});
onBeforeUnmount(() => {
  disposed = true;
  release?.();
  observer?.disconnect();
  document.removeEventListener('visibilitychange', hidden);
  destroy();
});
</script>
<template>
  <div
    ref="root"
    class="hover-preview"
    :data-preview-id="videoId"
    :class="{ 'hover-preview--playing': playing }"
  >
    <video
      ref="video"
      muted
      playsinline
      preload="none"
      @playing="playing = true"
      @timeupdate="update"
      @durationchange="update"
      @loadedmetadata="tracks"
      @canplay="tracks"
      @error="failure()"
    />
    <RouterLink
      class="hover-preview__open"
      :to="{ path: '/watch', query: { v: videoId } }"
      :aria-label="title"
      tabindex="-1"
    />
    <div v-if="playing" class="hover-preview__controls">
      <button
        type="button"
        class="hover-preview__mute"
        :aria-label="
          muted ? 'Включить звук предпросмотра' : 'Выключить звук предпросмотра'
        "
        @click.stop="toggleMute"
      >
        <AppIcon :name="muted ? 'muted' : 'volume'" />
      </button>
      <button
        v-if="hasCaptions"
        type="button"
        class="hover-preview__captions"
        aria-label="Субтитры предпросмотра"
        :aria-pressed="captions"
        @click.stop="toggleCaptions"
      >
        <AppIcon name="subtitles" />
      </button>
      <input
        class="hover-preview__seek"
        type="range"
        min="0"
        :max="duration || 1"
        step="0.1"
        :value="position"
        :disabled="!duration"
        aria-label="Перемотка предпросмотра"
        :aria-valuetext="playbackTime(position)"
        :style="{ '--preview-progress': percentage + '%' }"
        @input.stop="seek"
      />
      <span class="hover-preview__time"
        >{{ playbackTime(position) }} / {{ playbackTime(duration) }}</span
      >
    </div>
  </div>
</template>
<style scoped lang="scss">
.hover-preview {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  overflow: hidden;
  video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    opacity: 0;
    background: var(--color-surface);
  }
  &--playing video {
    opacity: 1;
  }
  &__open {
    position: absolute;
    inset: 0;
  }
  &__controls {
    position: absolute;
    inset: 0;
    pointer-events: none;
    button,
    input {
      pointer-events: auto;
    }
  }
  button {
    position: absolute;
    right: 8px;
    width: 36px;
    height: 36px;
    padding: 6px;
    border-radius: 50%;
    border: 0;
    background: #000c;
    color: var(--color-text);
    cursor: pointer;
  }
  &__mute {
    top: 8px;
  }
  &__captions {
    top: 52px;
  }
  &__seek {
    position: absolute;
    left: 8px;
    bottom: 4px;
    width: calc(100% - 16px);
    margin: 0;
    height: 20px;
    accent-color: var(--color-watch-progress);
    cursor: pointer;
  }
  &__time {
    position: absolute;
    right: 8px;
    bottom: 30px;
    padding: 2px 4px;
    border-radius: 4px;
    background: #000c;
    font-size: 12px;
    color: var(--color-text);
    pointer-events: none;
  }
}
</style>
