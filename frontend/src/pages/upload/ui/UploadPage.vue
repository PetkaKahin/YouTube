<script setup lang="ts">
import { RouterLink } from 'vue-router';
import { StudioShell } from '../../../widgets/studio-shell';
import { VideoUpload } from '../../../widgets/video-upload';
import { useSession } from '../../../entities/session';
import { apiMessage } from '../../../shared/api';
import { PageState } from '../../../shared/ui';
const session = useSession();
</script>
<template>
  <StudioShell>
    <main id="main-content" class="upload-page">
      <h1>Загрузка видео</h1>
      <PageState
        v-if="session.loading || session.changing"
        title="Проверяем аккаунт"
      />
      <PageState
        v-else-if="session.error"
        title="Не удалось проверить аккаунт"
        :description="apiMessage(session.error)"
        error
        @retry="session.refresh()"
      />
      <VideoUpload v-else-if="session.account" :key="session.scope" />
      <PageState v-else title="Войдите, чтобы добавить видео"
        ><RouterLink
          :to="{ path: '/login', query: { returnTo: $route.fullPath } }"
          class="button button--primary"
          >Войти</RouterLink
        ><RouterLink to="/studio" class="text-link"
          >Вернуться в Studio</RouterLink
        ></PageState
      >
    </main>
  </StudioShell>
</template>
<style scoped lang="scss">
.upload-page {
  padding: 24px 32px;
  h1 {
    margin: 0;
    font-size: 32px;
  }
  .text-link {
    display: block;
    margin-top: 16px;
  }
}
@media (max-width: 767px) {
  .upload-page {
    padding: 24px 16px;
  }
}
</style>
