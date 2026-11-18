<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router';
const route = useRoute();
</script>
<template>
  <nav class="studio-content-tabs" aria-label="Контент на канале">
    <RouterLink
      to="/studio/content/videos"
      :aria-current="
        route.path === '/studio/content/videos' ? 'page' : undefined
      "
      >Видео</RouterLink
    ><RouterLink
      to="/studio/content/playlists"
      :aria-current="
        route.path === '/studio/content/playlists' ? 'page' : undefined
      "
      >Плейлисты</RouterLink
    >
  </nav>
</template>
<style scoped lang="scss">
.studio-content-tabs {
  display: flex;
  padding: 0 32px;
  min-height: 48px;
  border-bottom: 1px solid var(--color-border);
  gap: 32px;
  a {
    display: flex;
    align-items: center;
    position: relative;
    font-size: 16px;
    font-weight: 500;
    padding: 12px 0;
    color: var(--color-muted);
    &[aria-current] {
      color: var(--color-text);
      &::after {
        content: '';
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: var(--color-text);
      }
    }
  }
}
@media (max-width: 767px) {
  .studio-content-tabs {
    padding: 0 16px;
  }
}
</style>
