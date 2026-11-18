<script setup lang="ts">
import Hls from 'hls.js';
import { useAmbientLight } from '../model/ambient';
import type { Identifier } from '../../../shared/api';
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';
import { AppIcon } from '../../../shared/ui';
import { mediaUrl, playbackTime, qualityName } from '../../../shared/lib/media';

const props = defineProps<{
  initialPosition?: number;
  autoplay?: boolean;
  ambient?: boolean;
  reportId?: Identifier;
  viewerScope?: string;
  src: string;
  poster: string;
  videoId: string;
  qualities: { quality: number; range: string }[];
}>();
const playbackScope = props.viewerScope;
// Capture once per viewer/video mount; progress responses must never seek a live player.
const initialPosition =
  !props.reportId &&
  Number.isFinite(props.initialPosition) &&
  (props.initialPosition ?? 0) > 0
    ? props.initialPosition!
    : 0;
const emit = defineEmits<{
  accessLost: [status: number];
  verifyAccess: [];
  ambientChange: [
    scope: string | undefined,
    id: string,
    color: string | undefined,
  ];
  progress: [
    scope: string | undefined,
    id: string,
    position: number,
    duration: number,
    flush: boolean,
  ];
}>();
const root = ref<HTMLElement>();
const video = ref<HTMLVideoElement>();
const settingsButton = ref<HTMLButtonElement>();
const menuElement = ref<HTMLElement>();
const paused = ref(true);
const started = ref(false);
const busy = ref(false);
const ready = ref(false);
const failure = ref('');
useAmbientLight(
  video,
  () =>
    props.ambient === true &&
    !props.reportId &&
    !failure.value &&
    props.viewerScope === playbackScope,
  (color) => emit('ambientChange', playbackScope, props.videoId, color),
);
const notice = ref('');
const position = ref(0);
const duration = ref(0);
const buffered = ref(0);
const volume = ref(1);
const muted = ref(false);
const fullscreen = ref(false);
const canFullscreen = ref(false);
const controlsVisible = ref(true);
const menu = ref<'' | 'settings' | 'quality' | 'subtitles'>('');
const levels = ref<{ index: number; label: string }[]>([]);
const requestedLevel = ref(-1);
const activeLevel = ref(-1);
const tracks = ref<{ index: number; label: string }[]>([]);
const selectedTrack = ref(-1);
let hls: Hls | undefined;
let hideTimer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
let generation = 0;
let autoplayArmed = false;
const restoring = ref(false);
let restoreTarget = 0;
let resumePlaying = false;
let restoredEnd = false;
let progressActive = false;
let failedPlayback: { position: number; playing: boolean } | undefined;
let preferredSubtitle: { lang?: string; name?: string } | undefined;
let subtitleFailed = false;
function subtitleKey(track: { lang?: string; name?: string }) {
  return JSON.stringify([track.lang ?? '', track.name ?? '']);
}
function hlsTracks(instance: Hls) {
  tracks.value = instance.subtitleTracks.map((track, index) => ({
    index,
    label: track.name || track.lang || `Дорожка ${index + 1}`,
  }));
  const index =
    preferredSubtitle === undefined
      ? -1
      : instance.subtitleTracks.findIndex(
          (track) => subtitleKey(track) === subtitleKey(preferredSubtitle!),
        );
  instance.subtitleTrack = index;
  instance.subtitleDisplay = index !== -1;
  selectedTrack.value = index;
}
const progress = computed(() =>
  duration.value ? (position.value / duration.value) * 100 : 0,
);
const currentQuality = computed(
  () => levels.value.find((item) => item.index === activeLevel.value)?.label,
);
const qualityLabel = computed(() =>
  requestedLevel.value === -1
    ? `Авто${currentQuality.value ? ` (${currentQuality.value})` : ''}`
    : levels.value.find((item) => item.index === requestedLevel.value)?.label ||
      'Авто',
);
const subtitleLabel = computed(
  () =>
    tracks.value.find((item) => item.index === selectedTrack.value)?.label ||
    'Выкл.',
);
const prefix = computed(() =>
  props.reportId
    ? `/api/v1/moderation/reports/${props.reportId}/hls/`
    : `/api/v1/videos/${props.videoId}/hls/`,
);

function showControls() {
  controlsVisible.value = true;
  clearTimeout(hideTimer);
  if (!paused.value && !menu.value)
    hideTimer = setTimeout(() => {
      hideControls();
    }, 3000);
}
function hideControls() {
  if (
    !paused.value &&
    !menu.value &&
    !root.value?.querySelector(':focus-visible')
  )
    controlsVisible.value = false;
}
function stop() {
  restoring.value = true;
  generation++;
  ready.value = false;
  hls?.destroy();
  hls = undefined;
  const element = video.value;
  if (element) {
    element.pause();
    element.removeAttribute('src');
    element.load();
  }
}
function fail(message: string, status?: number) {
  const element = video.value;
  if (
    !failedPlayback &&
    !restoring.value &&
    element &&
    Number.isFinite(element.currentTime) &&
    Number.isFinite(element.duration) &&
    element.duration > 0
  )
    failedPlayback = {
      position: element.currentTime,
      playing: !element.paused,
    };
  failure.value = message;
  busy.value = false;
  menu.value = '';
  stop();
  if (status && [401, 403, 404].includes(status)) emit('accessLost', status);
}
function mediaError() {
  if (failure.value || disposed) return;
  fail('Не удалось воспроизвести видео.');
  // Native media errors hide HTTP status; ask the API to recheck current rights.
  emit('verifyAccess');
}
function updateTime() {
  const element = video.value;
  if (!element) return;
  position.value = Number.isFinite(element.currentTime)
    ? element.currentTime
    : 0;
  duration.value = Number.isFinite(element.duration) ? element.duration : 0;
  buffered.value =
    element.buffered.length && duration.value
      ? Math.min(
          100,
          (element.buffered.end(element.buffered.length - 1) / duration.value) *
            100,
        )
      : 0;
}
function reportProgress(flush = false) {
  const element = video.value;
  if (
    failure.value ||
    disposed ||
    !ready.value ||
    restoring.value ||
    !progressActive ||
    !element
  )
    return;
  if (
    Number.isFinite(element.currentTime) &&
    Number.isFinite(element.duration) &&
    element.duration > 0
  ) {
    failedPlayback = {
      position: element.currentTime,
      playing: !element.paused,
    };
    if (props.reportId) return;
    emit(
      'progress',
      playbackScope,
      props.videoId,
      element.currentTime,
      element.duration,
      flush,
    );
  }
}
function flushProgress() {
  reportProgress(true);
}
function hiddenProgress() {
  if (document.hidden) reportProgress(true);
}
function timeChanged() {
  applyInitialPosition();
  updateTime();
  reportProgress();
}
function syncPlayback() {
  paused.value = video.value?.paused ?? true;
  if (!paused.value) {
    if (!restoring.value) progressActive = true;
    started.value = true;
    busy.value = false;
  }
  showControls();
  reportProgress(true);
}
async function togglePlay() {
  if (restoring.value) return;
  autoplayArmed = false;
  const element = video.value;
  if (!element || failure.value) return;
  if (restoredEnd) {
    restoredEnd = false;
    element.currentTime = 0;
    updateTime();
  }
  if (!element.paused) element.pause();
  else {
    notice.value = '';
    try {
      await element.play();
    } catch (error) {
      if (
        !disposed &&
        error instanceof DOMException &&
        error.name !== 'AbortError'
      )
        notice.value =
          'Не удалось начать воспроизведение. Нажмите «Смотреть» ещё раз.';
    }
  }
  showControls();
}
function seek(value: number) {
  if (!video.value || !duration.value || restoring.value) return;
  progressActive = true;
  restoredEnd = false;
  video.value.currentTime = Math.max(0, Math.min(duration.value, value));
  updateTime();
  showControls();
}
function changeVolume(value: number) {
  if (!video.value) return;
  video.value.volume = Math.max(0, Math.min(1, value));
  video.value.muted = value === 0;
  syncVolume();
  showControls();
}
function toggleMute() {
  if (!video.value) return;
  if (video.value.volume === 0) video.value.volume = 1;
  video.value.muted = !video.value.muted;
  syncVolume();
  showControls();
}
function syncVolume() {
  volume.value = video.value?.volume ?? 1;
  muted.value = video.value?.muted ?? false;
}
async function toggleFullscreen() {
  notice.value = '';
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await root.value?.requestFullscreen();
  } catch {
    notice.value = 'Не удалось открыть полноэкранный режим.';
  }
  showControls();
}
function syncFullscreen() {
  fullscreen.value = document.fullscreenElement === root.value;
}
async function openMenu(value: typeof menu.value) {
  menu.value = menu.value === value ? '' : value;
  showControls();
  await nextTick();
  if (menu.value)
    menuElement.value?.querySelector<HTMLButtonElement>('button')?.focus();
  else settingsButton.value?.focus();
}
async function closeMenu() {
  menu.value = '';
  showControls();
  await nextTick();
  settingsButton.value?.focus();
}
function chooseQuality(index: number) {
  if (!hls) return;
  requestedLevel.value = index;
  if (index === -1 || hls.currentLevel === index) hls.loadLevel = index;
  else hls.currentLevel = index;
  void closeMenu();
}
function chooseSubtitle(index: number) {
  notice.value = '';
  if (hls) {
    const track = hls.subtitleTracks[index];
    preferredSubtitle = track
      ? { lang: track.lang, name: track.name }
      : undefined;
    hls.config.subtitlePreference = preferredSubtitle;
    hls.subtitleTrack = index;
    hls.subtitleDisplay = index !== -1;
    if (subtitleFailed && index !== -1) {
      subtitleFailed = false;
      hls.startLoad(video.value?.currentTime ?? position.value);
    }
  } else if (video.value) {
    const track = video.value.textTracks[index];
    preferredSubtitle = track
      ? { lang: track.language, name: track.label }
      : undefined;
    for (let i = 0; i < video.value.textTracks.length; i++)
      video.value.textTracks[i]!.mode = i === index ? 'showing' : 'disabled';
  }
  selectedTrack.value = index;
  void closeMenu();
}
function nativeTracks() {
  if (hls || !video.value) return;
  tracks.value = Array.from(video.value.textTracks).map((track, index) => ({
    index,
    label: track.label || track.language || `Дорожка ${index + 1}`,
  }));
  if (preferredSubtitle !== undefined)
    for (const track of Array.from(video.value.textTracks))
      track.mode =
        subtitleKey({ lang: track.language, name: track.label }) ===
        subtitleKey(preferredSubtitle)
          ? 'showing'
          : 'disabled';
  selectedTrack.value = Array.from(video.value.textTracks).findIndex(
    (track) => track.mode === 'showing',
  );
}
function keydown(event: KeyboardEvent) {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const target = event.target as HTMLElement;
  if (menu.value) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      void closeMenu();
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const items = [
        ...(menuElement.value?.querySelectorAll<HTMLButtonElement>(
          'button:not(:disabled)',
        ) ?? []),
      ];
      const index = items.indexOf(target as HTMLButtonElement);
      items[
        event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? items.length - 1
            : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) %
              items.length
      ]?.focus();
    }
    return;
  }
  if (
    target.matches('input,button,a,textarea,select') ||
    target.isContentEditable
  )
    return;
  const key = event.key.toLowerCase();
  if (key === ' ' || key === 'k') {
    event.preventDefault();
    void togglePlay();
  } else if (key === 'arrowleft' || key === 'j') {
    event.preventDefault();
    seek(position.value - (key === 'j' ? 10 : 5));
  } else if (key === 'arrowright' || key === 'l') {
    event.preventDefault();
    seek(position.value + (key === 'l' ? 10 : 5));
  } else if (key === 'arrowup' || key === 'arrowdown') {
    event.preventDefault();
    changeVolume(volume.value + (key === 'arrowup' ? 0.05 : -0.05));
  } else if (key === 'm') {
    event.preventDefault();
    toggleMute();
  } else if (key === 'f' && canFullscreen.value) {
    event.preventDefault();
    void toggleFullscreen();
  } else if (key === 'c' && tracks.value.length) {
    event.preventDefault();
    chooseSubtitle(selectedTrack.value === -1 ? tracks.value[0]!.index : -1);
  } else if (key === 'home') {
    event.preventDefault();
    seek(0);
  } else if (key === 'end') {
    event.preventDefault();
    seek(duration.value);
  }
}
function outside(event: PointerEvent) {
  if (
    menu.value &&
    !menuElement.value?.contains(event.target as Node) &&
    !settingsButton.value?.contains(event.target as Node)
  )
    menu.value = '';
}
function applyInitialPosition() {
  const element = video.value;
  updateTime();
  if (
    !restoring.value ||
    !element ||
    element.readyState < 1 ||
    !Number.isFinite(element.duration) ||
    element.duration <= 0
  )
    return;
  const target = Math.min(restoreTarget, element.duration);
  if (Math.abs(element.currentTime - target) > 0.1) {
    if (!element.seeking) {
      try {
        element.currentTime = target;
      } catch {
        /* A later metadata/canplay event can retry the seek. */
      }
    }
    return;
  }
  // Browsers can keep seeking at the exact end when audio/video endpoints differ.
  // No playback is started there; an explicit replay clears that terminal seek.
  if (element.seeking && target < element.duration) return;
  restoring.value = false;
  updateTime();
  // play() on an ended element restarts at zero. Keep the restored end until explicit replay.
  if (target >= element.duration) {
    restoredEnd = true;
    autoplayArmed = false;
    resumePlaying = false;
    return;
  }
  if (resumePlaying) {
    resumePlaying = false;
    void togglePlay();
  } else if (element.readyState >= 3) void tryAutoplay();
}
function seekCompleted() {
  if (restoring.value) {
    applyInitialPosition();
    return;
  }
  reportProgress(true);
}
async function tryAutoplay() {
  const element = video.value;
  if (
    !autoplayArmed ||
    !element ||
    disposed ||
    failure.value ||
    restoring.value
  )
    return;
  autoplayArmed = false;
  const epoch = generation;
  try {
    await element.play();
  } catch (error) {
    if (disposed || epoch !== generation) return;
    if (
      error instanceof DOMException &&
      ['NotAllowedError', 'AbortError'].includes(error.name)
    )
      return;
    notice.value = 'Нажмите «Смотреть», чтобы начать воспроизведение.';
  }
}
function retryPlayback() {
  attach(failedPlayback);
}
function attach(resume?: { position: number; playing: boolean }) {
  const chosenQuality = levels.value.find(
    (item) => item.index === requestedLevel.value,
  )?.label;
  stop();
  const epoch = generation;
  autoplayArmed =
    !!props.autoplay && !props.reportId && !resume && !started.value;
  const element = video.value;
  if (!element || disposed) return;
  restoredEnd = false;
  restoreTarget = resume?.position ?? initialPosition;
  restoring.value = restoreTarget > 0;
  resumePlaying = !!resume?.playing;
  failure.value = '';
  notice.value = '';
  ready.value = false;
  subtitleFailed = false;
  busy.value = true;
  levels.value = [];
  tracks.value = [];
  requestedLevel.value = -1;
  activeLevel.value = -1;
  selectedTrack.value = -1;
  let src: string;
  try {
    src = mediaUrl(props.src, prefix.value);
  } catch {
    fail('Не удалось загрузить видео.');
    return;
  }
  if (Hls.isSupported()) {
    const instance = new Hls({
      // Initial stored positions are clamped only after real media metadata exists.
      startPosition: resume?.position ?? -1,
      subtitlePreference: preferredSubtitle,
      // AV1/fMP4 needs no transmux worker. No external worker or script is loaded.
      enableWorker: false,
      xhrSetup: (_xhr, url) => {
        mediaUrl(url, prefix.value);
      },
    });
    hls = instance;
    instance.on(Hls.Events.MANIFEST_PARSED, () => {
      if (epoch !== generation) return;
      levels.value = instance.levels
        .flatMap((level, index) => {
          const label = qualityName(
            level.width,
            level.height,
            level.videoRange,
            props.qualities,
          );
          return label ? [{ index, label }] : [];
        })
        .reverse();
      const chosen = levels.value.find((item) => item.label === chosenQuality);
      if (chosen) {
        requestedLevel.value = chosen.index;
        instance.loadLevel = chosen.index;
      }
      hlsTracks(instance);
      ready.value = true;
      busy.value = false;
    });
    instance.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => {
      if (epoch !== generation || disposed) return;
      activeLevel.value = data.level;
    });
    instance.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, () => {
      if (epoch !== generation || disposed) return;
      hlsTracks(instance);
    });
    instance.on(Hls.Events.SUBTITLE_TRACK_SWITCH, (_event, data) => {
      if (epoch !== generation || disposed) return;
      selectedTrack.value = data.id;
    });
    instance.on(Hls.Events.ERROR, (_event, data) => {
      if (epoch !== generation || disposed) return;
      const status = data.response?.code;
      if (status && [401, 403, 404].includes(status))
        fail('Видео недоступно. Доступ мог измениться.', status);
      else if (data.details === Hls.ErrorDetails.INTERNAL_ABORTED) return;
      else if (
        data.fatal &&
        [
          Hls.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR,
          Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR,
          Hls.ErrorDetails.BUFFER_ADD_CODEC_ERROR,
        ].includes(data.details)
      )
        fail('Браузер не поддерживает кодек этого видео или звука.');
      else if (
        data.details.toLowerCase().includes('subtitle') ||
        data.frag?.type === 'subtitle'
      ) {
        instance.subtitleTrack = -1;
        instance.subtitleDisplay = false;
        selectedTrack.value = -1;
        preferredSubtitle = undefined;
        instance.config.subtitlePreference = undefined;
        subtitleFailed = true;
        notice.value =
          'Не удалось загрузить субтитры. Выберите дорожку ещё раз.';
      } else if (data.fatal)
        fail(
          'Не удалось воспроизвести видео. Проверьте соединение и попробуйте ещё раз.',
        );
    });
    instance.loadSource(src);
    instance.attachMedia(element);
  } else if (element.canPlayType('application/vnd.apple.mpegurl')) {
    element.src = src;
    element.load();
  } else fail('Этот браузер не поддерживает воспроизведение этого видео.');
}
watch(
  () =>
    props.qualities.map((item) => `${item.quality}:${item.range}`).join(','),
  () => {
    if (video.value && !failure.value)
      attach({ position: position.value, playing: !paused.value });
  },
);
onMounted(() => {
  canFullscreen.value =
    !!document.fullscreenEnabled && !!root.value?.requestFullscreen;
  window.addEventListener('pagehide', flushProgress);
  document.addEventListener('visibilitychange', hiddenProgress);
  document.addEventListener('fullscreenchange', syncFullscreen);
  document.addEventListener('pointerdown', outside);
  video.value?.textTracks.addEventListener('addtrack', nativeTracks);
  video.value?.textTracks.addEventListener('change', nativeTracks);
  attach();
});
onBeforeUnmount(() => {
  reportProgress(true);
  disposed = true;
  clearTimeout(hideTimer);
  window.removeEventListener('pagehide', flushProgress);
  document.removeEventListener('visibilitychange', hiddenProgress);
  document.removeEventListener('fullscreenchange', syncFullscreen);
  document.removeEventListener('pointerdown', outside);
  video.value?.textTracks.removeEventListener('addtrack', nativeTracks);
  video.value?.textTracks.removeEventListener('change', nativeTracks);
  stop();
});
</script>

<template>
  <section
    ref="root"
    class="player"
    :class="{
      'player--hidden': !controlsVisible && !paused && !menu,
      'player--started': started,
      'player--restoring': restoring,
    }"
    tabindex="0"
    aria-label="Видеоплеер"
    @keydown="keydown"
    @pointermove="showControls"
    @focusin="showControls"
    @pointerleave="hideControls"
  >
    <video
      ref="video"
      class="player__video"
      :poster="poster"
      playsinline
      preload="metadata"
      @click="menu ? closeMenu() : togglePlay()"
      @dblclick="canFullscreen && toggleFullscreen()"
      @play="syncPlayback"
      @pause="syncPlayback"
      @ended="syncPlayback"
      @timeupdate="timeChanged"
      @seeking="applyInitialPosition"
      @seeked="seekCompleted"
      @durationchange="updateTime"
      @progress="updateTime"
      @volumechange="syncVolume"
      @waiting="busy = true"
      @playing="busy = false"
      @canplay="
        ready = true;
        busy = false;
        applyInitialPosition();
        if (!restoring) {
          if (resumePlaying) {
            resumePlaying = false;
            togglePlay();
          } else tryAutoplay();
        }
      "
      @loadedmetadata="applyInitialPosition"
      @error="mediaError"
    />
    <div v-if="failure" class="player__error" role="alert">
      <AppIcon name="video" />
      <p>{{ failure }}</p>
      <button
        class="button button--secondary"
        type="button"
        @click="retryPlayback"
      >
        Повторить
      </button>
    </div>
    <template v-else>
      <button
        v-if="!started"
        class="player__start"
        type="button"
        aria-label="Смотреть"
        :disabled="!ready || restoring"
        @click="togglePlay"
      >
        <AppIcon name="play" />
      </button>
      <div
        v-if="busy || restoring"
        class="player__buffer"
        role="status"
        aria-label="Загрузка видео"
      />
      <p v-if="notice" class="player__notice" role="alert">{{ notice }}</p>
      <div
        v-if="menu"
        ref="menuElement"
        class="player__menu"
        :aria-label="
          menu === 'quality'
            ? 'Качество'
            : menu === 'subtitles'
              ? 'Субтитры'
              : 'Настройки плеера'
        "
      >
        <template v-if="menu === 'settings'">
          <button
            v-if="tracks.length"
            type="button"
            @click="openMenu('subtitles')"
          >
            <AppIcon name="subtitles" /><span>Субтитры</span
            ><small>{{ subtitleLabel }}</small
            ><AppIcon name="forward" />
          </button>
          <button
            type="button"
            :disabled="!levels.length"
            @click="openMenu('quality')"
          >
            <AppIcon name="settings" /><span>Качество</span
            ><small>{{ qualityLabel }}</small
            ><AppIcon v-if="levels.length" name="forward" />
          </button>
        </template>
        <template v-else>
          <button
            class="player__menu-heading"
            type="button"
            @click="openMenu('settings')"
          >
            <AppIcon name="back" />{{
              menu === 'quality' ? 'Качество' : 'Субтитры'
            }}
          </button>
          <template v-if="menu === 'quality'">
            <button
              type="button"
              :aria-pressed="requestedLevel === -1"
              @click="chooseQuality(-1)"
            >
              <AppIcon v-if="requestedLevel === -1" name="check" /><span
                v-else
                class="player__check-space"
              />Авто
            </button>
            <button
              v-for="level in levels"
              :key="level.index"
              type="button"
              :aria-pressed="requestedLevel === level.index"
              @click="chooseQuality(level.index)"
            >
              <AppIcon
                v-if="requestedLevel === level.index"
                name="check"
              /><span v-else class="player__check-space" />{{ level.label }}
            </button>
          </template>
          <template v-else>
            <button
              type="button"
              :aria-pressed="selectedTrack === -1"
              @click="chooseSubtitle(-1)"
            >
              <AppIcon v-if="selectedTrack === -1" name="check" /><span
                v-else
                class="player__check-space"
              />Выкл.
            </button>
            <button
              v-for="track in tracks"
              :key="track.index"
              type="button"
              :aria-pressed="selectedTrack === track.index"
              @click="chooseSubtitle(track.index)"
            >
              <AppIcon v-if="selectedTrack === track.index" name="check" /><span
                v-else
                class="player__check-space"
              />{{ track.label }}
            </button>
          </template>
        </template>
      </div>
      <div class="player__controls">
        <input
          class="player__seek"
          type="range"
          min="0"
          :max="duration || 1"
          step="0.1"
          :value="position"
          :disabled="!duration || restoring"
          aria-label="Позиция воспроизведения"
          :aria-valuetext="`${playbackTime(position)} из ${playbackTime(duration)}`"
          :style="{
            '--progress': `${progress}%`,
            '--buffered': `${buffered}%`,
          }"
          @input="seek(Number(($event.target as HTMLInputElement).value))"
        />
        <div class="player__bar">
          <div class="player__controls-left">
            <button
              class="player__control"
              type="button"
              :aria-label="paused ? 'Смотреть (k)' : 'Пауза (k)'"
              :disabled="!ready || restoring"
              @click="togglePlay"
            >
              <AppIcon :name="paused ? 'play' : 'pause'" />
            </button>
            <div class="player__volume">
              <button
                class="player__control"
                type="button"
                :aria-label="
                  muted || !volume ? 'Включить звук (m)' : 'Отключить звук (m)'
                "
                @click="toggleMute"
              >
                <AppIcon
                  :name="muted || !volume ? 'muted' : 'volume'"
                /></button
              ><input
                type="range"
                min="0"
                max="1"
                step="0.05"
                :value="muted ? 0 : volume"
                aria-label="Громкость"
                @input="
                  changeVolume(
                    Number(($event.target as HTMLInputElement).value),
                  )
                "
              />
            </div>
            <span class="player__time"
              >{{ playbackTime(position) }} / {{ playbackTime(duration) }}</span
            >
          </div>
          <span class="player__spacer" />
          <div class="player__controls-right">
            <button
              v-if="tracks.length"
              class="player__control player__captions"
              :class="{ 'player__captions--on': selectedTrack !== -1 }"
              type="button"
              aria-label="Субтитры (c)"
              :aria-pressed="selectedTrack !== -1"
              @click="
                chooseSubtitle(selectedTrack === -1 ? tracks[0]!.index : -1)
              "
            >
              <AppIcon name="subtitles" />
            </button>
            <button
              ref="settingsButton"
              class="player__control"
              type="button"
              aria-label="Настройки"
              :aria-expanded="!!menu"
              :disabled="!ready || restoring"
              @click="openMenu('settings')"
            >
              <AppIcon name="settings" />
            </button>
            <button
              v-if="canFullscreen"
              class="player__control"
              type="button"
              :aria-label="
                fullscreen
                  ? 'Выйти из полноэкранного режима (f)'
                  : 'Во весь экран (f)'
              "
              @click="toggleFullscreen"
            >
              <AppIcon :name="fullscreen ? 'fullscreenExit' : 'fullscreen'" />
            </button>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped lang="scss">
.player {
  --player-control-height: 44px;
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #000;
  border-radius: 12px;
  overflow: hidden;
  color: #fff;
  isolation: isolate;
  &--restoring .player__video {
    visibility: hidden;
  }
  &__video {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  &__start {
    position: absolute;
    inset: 0;
    margin: auto;
    width: 68px;
    height: 48px;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 12px;
    background: #212121cc;
    cursor: pointer;
    &:hover {
      background: #f03;
    }
    svg {
      width: 36px;
      height: 36px;
    }
  }
  &__buffer {
    position: absolute;
    inset: 0;
    margin: auto;
    width: 48px;
    height: 48px;
    border: 4px solid #ffffff40;
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    pointer-events: none;
  }
  &__error {
    position: absolute;
    inset: 0;
    background: #181818;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 20px;
    text-align: center;
    font-size: 16px;
    line-height: 24px;
    svg {
      width: 40px;
      height: 40px;
    }
    p {
      max-width: 540px;
    }
  }
  &__notice {
    z-index: 4;
    position: absolute;
    left: 16px;
    right: 16px;
    top: 12px;
    padding: 8px 12px;
    background: #000c;
    border-radius: 4px;
    font-size: 14px;
  }
  &__controls {
    z-index: 1;
    position: absolute;
    inset: auto 0 0;
    padding: 20px 12px 0;
    background: linear-gradient(transparent, #000c);
    transition: opacity 0.15s;
  }
  &__seek {
    display: block;
    width: 100%;
    height: 6px;
    margin: 0;
    appearance: none;
    border: 0;
    border-radius: 0;
    cursor: pointer;
    background: linear-gradient(
      to right,
      #f03 0 var(--progress),
      #ffffff80 var(--progress) var(--buffered),
      #ffffff40 var(--buffered) 100%
    );
    &::-webkit-slider-thumb {
      appearance: none;
      border: 0;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #f03;
    }
    &::-moz-range-thumb {
      border: 0;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #f03;
    }
  }
  &__bar {
    display: flex;
    align-items: center;
    height: 56px;
  }
  &__controls-left,
  &__controls-right {
    display: flex;
    align-items: center;
    background: #0005;
    border-radius: 20px;
    height: 40px;
  }
  &__control {
    background: transparent;
    color: #fff;
    border: 0;
    display: grid;
    place-items: center;
    width: 40px;
    height: var(--player-control-height);
    padding: 0;
    cursor: pointer;
    flex-shrink: 0;
    svg {
      width: 24px;
      height: 24px;
    }
    &:hover {
      color: #ddd;
    }
  }
  &__time {
    font-size: 12px;
    line-height: 20px;
    white-space: nowrap;
    margin-left: 4px;
    padding-right: 12px;
  }
  &__spacer {
    flex: 1;
    min-width: 0;
  }
  &__volume {
    display: flex;
    align-items: center;
    input {
      appearance: none;
      width: 0;
      opacity: 0;
      margin: 0;
      background: #fff;
      height: 3px;
      transition:
        width 0.15s,
        opacity 0.15s;
      cursor: pointer;
      &::-webkit-slider-thumb {
        appearance: none;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #fff;
        border: 0;
      }
      &::-moz-range-thumb {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #fff;
        border: 0;
      }
    }
    &:hover input,
    &:focus-within input {
      width: 52px;
      opacity: 1;
      margin-right: 8px;
    }
  }
  &__captions {
    position: relative;
    &--on::after {
      content: '';
      position: absolute;
      bottom: 6px;
      left: 12px;
      right: 12px;
      height: 3px;
      background: #f03;
    }
  }
  &__menu {
    z-index: 3;
    position: absolute;
    bottom: 64px;
    right: 12px;
    background: #1c1c1cf2;
    max-width: calc(100% - 24px);
    max-height: calc(100% - 80px);
    overflow-y: auto;
    min-width: 250px;
    border-radius: 12px;
    padding: 8px 0;
    box-shadow: 0 4px 24px #0006;
    font-size: 13px;
    button {
      display: flex;
      align-items: center;
      gap: 12px;
      width: 100%;
      min-height: 40px;
      background: transparent;
      border: 0;
      color: #fff;
      text-align: left;
      padding: 8px 16px;
      cursor: pointer;
      &:hover,
      &:focus-visible {
        background: #ffffff20;
      }
      small {
        margin-left: auto;
        font-size: 12px;
      }
      svg {
        width: 20px;
        height: 20px;
        flex-shrink: 0;
      }
    }
  }
  &__menu-heading {
    border-bottom: 1px solid #ffffff30 !important;
    margin-bottom: 8px;
  }
  &__check-space {
    width: 20px;
    flex-shrink: 0;
  }
  &--hidden {
    cursor: none;
    .player__controls {
      opacity: 0;
      pointer-events: none;
    }
  }
  &:fullscreen {
    border-radius: 0;
    aspect-ratio: auto;
    width: 100%;
    height: 100%;
  }
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .player__buffer {
    animation: none;
  }
  .player__controls {
    transition: none;
  }
}
@media (max-width: 767px) {
  .player {
    --player-control-height: 44px;
    border-radius: 0;
    &__controls {
      padding-left: 12px;
      padding-right: 12px;
    }
    &__controls-left,
    &__controls-right {
      height: 36px;
    }
    &__control {
      width: 36px;
    }
    &__time {
      font-size: 12px;
      margin-left: 4px;
    }
    &__volume input {
      display: none;
    }
    &__menu {
      bottom: 52px;
      right: 8px;
      max-height: calc(100% - 60px);
      border-radius: 8px;
      min-width: 230px;
    }
  }
}

.player {
  &__controls {
    padding: 0 12px 12px;
  }
  &__bar {
    gap: 12px;
  }
  &__controls-left,
  &__controls-right {
    gap: 8px;
  }
  &__controls-left {
    background: transparent;
    height: 44px;
  }
  &__controls-right {
    height: 44px;
  }
  &__control {
    border-radius: 50%;
    background: var(--player-control-background);
    width: 44px;
    height: 44px;
    flex-shrink: 0;
    &:hover {
      background: var(--player-control-hover);
    }
  }
  &__time {
    padding: 12px 16px;
    border-radius: 24px;
    background: var(--player-control-background);
    font-size: 14px;
    line-height: 20px;
  }
  &__volume {
    border-radius: 24px;
    background: var(--player-control-background);
    .player__control {
      background: transparent;
    }
  }
  &__controls-right {
    padding: 0 4px;
    border-radius: 24px;
    background: var(--player-control-background);
    .player__control {
      background: transparent;
    }
  }
}
@media (max-width: 767px) {
  .player {
    --player-control-height: 36px;
    &__controls {
      padding: 0 8px 8px;
    }
    &__bar,
    &__controls-left,
    &__controls-right {
      gap: 4px;
    }
    &__controls-left,
    &__controls-right {
      height: 36px;
    }
    &__control {
      width: 36px;
      height: 36px;
      svg {
        width: 22px;
        height: 22px;
      }
    }
    &__time {
      padding: 8px;
      font-size: 12px;
      line-height: 20px;
    }
  }
}
</style>
