<script setup lang="ts">
import { ref, watch } from 'vue';
import type { Identifier } from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
const props = defineProps<{ videoId: Identifier }>();
const failed = ref(false);
watch(
  () => props.videoId,
  () => {
    failed.value = false;
  },
);
</script>
<template>
  <span class="owner-video-preview" aria-hidden="true"
    ><AppIcon name="video" /><img
      v-if="!failed"
      :src="`/api/v1/videos/${videoId}/thumbnail/image`"
      alt=""
      loading="lazy"
      @error="failed = true"
  /></span>
</template>
<style scoped lang="scss">
.owner-video-preview {
  display: grid;
  place-items: center;
  position: relative;
  width: 144px;
  aspect-ratio: 16 / 9;
  flex-shrink: 0;
  background: var(--color-surface);
  border-radius: 4px;
  overflow: hidden;
  color: var(--color-muted);
  img {
    position: absolute;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: var(--color-surface);
  }
}
</style>
