<script setup lang="ts">
defineProps<{
  name:
    | 'menu'
    | 'search'
    | 'back'
    | 'close'
    | 'home'
    | 'history'
    | 'subscriptions'
    | 'user'
    | 'logout'
    | 'filter'
    | 'video'
    | 'playlist'
    | 'play'
    | 'pause'
    | 'volume'
    | 'muted'
    | 'settings'
    | 'subtitles'
    | 'fullscreen'
    | 'fullscreenExit'
    | 'forward'
    | 'check'
    | 'create'
    | 'playlistAdd'
    | 'globe'
    | 'lock'
    | 'link'
    | 'down'
    | 'upload'
    | 'plus'
    | 'bookmark'
    | 'dislike'
    | 'like';
}>();
const paths = {
  menu: 'M3 6h18M3 12h18M3 18h18',
  search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  back: 'M20 12H4m8-8-8 8 8 8',
  close: 'm6 6 12 12M6 18 18 6',
  history: 'M3 4v6h6M3 10a9 9 0 1 1 0 5m9-9v6l4 2',
  subscriptions: 'M6 2h12M4 5h16M2 8h20v14H2Zm8 4 6 3-6 3Z',
  home: 'm2 10 10-8 10 8M5 8v13h5v-7h4v7h5V8',
  user: 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM8 9a4 4 0 1 0 8 0 4 4 0 0 0-8 0ZM5 19c1-5 13-5 14 0',
  logout: 'M10 3H3v18h7m-1-9h12m-5-5 5 5-5 5',
  filter: 'M3 6h18M3 12h18M3 18h18M7 3v6m10 0v6M9 15v6',
  video: 'M3 4h18v16H3ZM10 8l6 4-6 4Z',
  playlist: 'M3 5h15M3 10h15M3 15h9m3-1 7 4-7 4Z',
  play: 'M7 4v16l13-8Z',
  pause: 'M7 4v16M17 4v16',
  volume: 'M3 9h4l5-5v16l-5-5H3Zm13-1c3 2 3 6 0 8m3-11c5 4 5 10 0 14',
  muted: 'M3 9h4l5-5v16l-5-5H3Zm13-1 6 8m0-8-6 8',
  settings:
    'm9 3-.5 3-2 .9-2.5-1-2 3.4L4.5 11v2L2 14.7l2 3.4 2.5-1 2 .9.5 3h6l.5-3 2-.9 2.5 1 2-3.4-2.5-1.7v-2L22 9.3l-2-3.4-2.5 1-2-.9L15 3ZM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  subtitles: 'M3 5h18v14H3ZM10 9H6v6h4m8-6h-4v6h4',
  fullscreen: 'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6',
  fullscreenExit: 'M3 9h6V3m6 0v6h6M9 21v-6H3m12 6v-6h6',
  forward: 'm9 4 8 8-8 8',
  check: 'm4 12 5 5L20 6',
  create: 'M3 5h13v14H3Zm13 5 6-4v12l-6-4M6 12h7m-3.5-3.5v7',
  playlistAdd: 'M3 5h15M3 10h15M3 15h9m6-1v8m-4-4h8',
  globe:
    'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM2 12h20M12 2c-6 5-6 15 0 20 6-5 6-15 0-20Z',
  lock: 'M5 10h14v12H5ZM8 10V6a4 4 0 0 1 8 0v4m-4 4v4',
  link: 'm10 7 3-3a5 5 0 0 1 7 7l-3 3M7 10l-3 3a5 5 0 0 0 7 7l3-3M8 16l8-8',
  down: 'm6 9 6 6 6-6',
  upload: 'M12 17V3m-6 6 6-6 6 6M3 17v5h18v-5',
  plus: 'M12 3v18M3 12h18',
  bookmark: 'M5 3h14v19l-7-5-7 5Z',
  like: 'M7 21H3V9h4Zm0-12 5-7c2 0 3 1 2 4l-1 3h6c2 0 2 2 2 3l-2 7c0 1-1 2-3 2H7Z',
};
</script>
<template>
  <svg
    viewBox="0 0 24 24"
    width="24"
    height="24"
    :fill="name === 'play' ? 'currentColor' : 'none'"
    :stroke="name === 'play' ? 'none' : 'currentColor'"
    :stroke-width="name === 'pause' ? 4 : 1.5"
    aria-hidden="true"
    focusable="false"
  >
    <path
      :d="paths[name === 'dislike' ? 'like' : name]"
      :transform="name === 'dislike' ? 'rotate(180 12 12)' : undefined"
    />
  </svg>
</template>
