<script setup lang="ts">
import { computed } from 'vue';
import { VideoList } from '../../widgets/video-list';
import { useVideoCardVerification } from '../../entities/video';
import { useFeed } from './model/feed';
const feed = useFeed();
const videos = computed(() => [
  ...new Map(
    feed.data.value?.pages
      .flatMap((page) => page.items)
      .map((video) => [video.id, video]) ?? [],
  ).values(),
]);
const verifyPreviewAccess = useVideoCardVerification(
  () => feed.refetch(),
  () => feed.isError.value,
);
</script>
<template>
  <main id="main-content" class="home-page" aria-label="Главная" tabindex="-1">
    <VideoList
      :videos="videos"
      :loading="feed.isPending.value"
      :failed="feed.isError.value && !feed.isFetchNextPageError.value"
      :next-failed="feed.isFetchNextPageError.value"
      :fetching="feed.isFetching.value"
      :has-next="!!feed.hasNextPage.value"
      @more="feed.fetchNextPage()"
      @retry="feed.refetch()"
      @preview-error="verifyPreviewAccess"
    />
  </main>
</template>
<style scoped lang="scss">
.home-page {
  padding: 24px 24px 48px;
  :deep(.page-state) {
    max-width: 702px;
    margin-top: 0;
  }
}
@media (max-width: 767px) {
  .home-page {
    padding: 12px 0 24px;
  }
}
</style>
