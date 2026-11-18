<script setup lang="ts">
import { ref } from 'vue';
import { useSession } from '../../../entities/session';
import { VideoCard } from '../../../entities/video';
import type { FeedCardResponse } from '../../../shared/api';
import { PageState } from '../../../shared/ui';
import { useNextPage } from '../../../shared/lib/pagination';
const props = defineProps<{
  videos: FeedCardResponse[];
  loading: boolean;
  failed: boolean;
  nextFailed: boolean;
  fetching: boolean;
  hasNext: boolean;
  horizontal?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
}>();
const emit = defineEmits<{ more: []; retry: []; previewError: [] }>();
const session = useSession();
const sentinel = ref<HTMLElement>();
useNextPage(sentinel, () => {
  if (props.hasNext && !props.fetching && !props.nextFailed) emit('more');
});
</script>
<template>
  <div
    v-if="loading"
    class="video-grid"
    role="status"
    aria-label="Загружаем видео"
  >
    <div v-for="index in 6" :key="index" class="video-skeleton">
      <span /><i /><i />
    </div>
  </div>
  <PageState
    v-else-if="failed"
    title="Не удалось загрузить видео"
    description="Проверьте подключение и попробуйте ещё раз."
    error
    @retry="emit('retry')"
  />
  <PageState
    v-else-if="!videos.length"
    :title="emptyTitle || 'Пока нет видео'"
    :description="emptyDescription ?? 'Здесь появятся опубликованные видео.'"
  />
  <div v-else :class="horizontal ? 'video-list' : 'video-grid'">
    <VideoCard
      v-for="video in videos"
      :key="video.id"
      :video="video"
      :horizontal="horizontal"
      :scope="
        !session.loading && !session.changing && !session.error
          ? session.scope
          : undefined
      "
      :guest="
        !session.account &&
        !session.loading &&
        !session.changing &&
        !session.error
      "
      @preview-error="emit('previewError')"
    />
  </div>
  <div v-if="hasNext && !failed && !loading" ref="sentinel" class="pagination">
    <button
      type="button"
      class="button button--secondary"
      :disabled="fetching"
      @click="emit('more')"
    >
      {{
        fetching
          ? 'Загружаем…'
          : nextFailed
            ? 'Повторить загрузку'
            : 'Ещё видео'
      }}
    </button>
  </div>
</template>
<style scoped lang="scss">
.video-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 36px 16px;
}
.pagination {
  display: flex;
  justify-content: center;
  padding: 32px 0;
}
.video-skeleton {
  span,
  i {
    display: block;
    background: var(--color-surface);
    border-radius: 8px;
  }
  span {
    aspect-ratio: 16/9;
    border-radius: 12px;
  }
  i {
    height: 16px;
    margin-top: 12px;
    width: 85%;
    &:last-child {
      width: 55%;
    }
  }
}
@media (min-width: 2200px) {
  .video-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (max-width: 1100px) {
  .video-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 767px) {
  .home-page {
    padding: 12px 0 24px;
  }
  .video-grid {
    gap: 28px;
    grid-template-columns: 1fr;
    max-width: 600px;
    margin: auto;
  }
  .video-skeleton i {
    margin-left: 12px;
  }
}

.video-list {
  display: grid;
  gap: 16px;
  :deep(.video-card--horizontal) {
    grid-template-columns: 250px minmax(0, 1fr);
  }
}
@media (max-width: 767px) {
  .video-list :deep(.video-card--horizontal) {
    grid-template-columns: minmax(120px, 40%) minmax(0, 1fr);
  }
}
</style>
