<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink } from 'vue-router';
import type { PlaylistResponse } from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
const props = defineProps<{
  playlist: PlaylistResponse;
  tag?: string;
  showVisibility?: boolean;
}>();
const to = computed(() => ({
  path: props.tag
    ? `/@${encodeURIComponent(props.tag)}/playlists`
    : '/playlist',
  query: { list: props.playlist.id },
}));
</script>
<template>
  <article class="playlist-card">
    <RouterLink
      class="playlist-card__preview"
      :to="to"
      :aria-label="playlist.title"
      ><AppIcon name="playlist"
    /></RouterLink>
    <h2>
      <RouterLink :to="to">{{ playlist.title }}</RouterLink>
    </h2>
    <p>
      {{
        showVisibility
          ? ({
              public: 'Открытый доступ',
              unlisted: 'Доступ по ссылке',
              private: 'Ограниченный доступ',
            }[playlist.visibility] || '') + ' · '
          : ''
      }}Плейлист
    </p>
    <RouterLink class="playlist-card__open" :to="to"
      >Посмотреть весь плейлист</RouterLink
    >
  </article>
</template>
<style scoped lang="scss">
.playlist-card {
  min-width: 0;
  &__preview {
    position: relative;
    display: grid;
    place-items: center;
    width: 100%;
    aspect-ratio: 16/9;
    border-radius: 12px;
    background: var(--color-surface);
    margin-top: 8px;
    isolation: isolate;
    svg {
      width: 48px;
      height: 48px;
      color: var(--color-muted);
    }
    &::before {
      content: '';
      position: absolute;
      inset: -8px 8px auto;
      height: 8px;
      background: var(--color-hover);
      border-radius: 8px 8px 0 0;
      z-index: -1;
    }
  }
  h2 {
    font-size: 14px;
    line-height: 20px;
    font-weight: 500;
    margin: 12px 0 4px;
    overflow-wrap: anywhere;
  }
  p,
  &__open {
    font-size: 12px;
    line-height: 18px;
    color: var(--color-muted);
    margin: 0;
  }
  &__open:hover {
    color: var(--color-text);
  }
}
@media (max-width: 767px) {
  .playlist-card {
    display: grid;
    grid-template-columns: 160px minmax(0, 1fr);
    column-gap: 8px;
    row-gap: 0;
    &__preview {
      grid-row: 1/4;
    }
    h2 {
      margin: 8px 0 0;
      font-weight: 400;
    }
    p {
      margin-top: 0;
    }
    &__open {
      align-self: start;
    }
  }
}
</style>
