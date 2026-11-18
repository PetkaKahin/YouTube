<script setup lang="ts">
import { RouterLink } from 'vue-router';
import { PageState } from '../../../shared/ui';
</script>
<template>
  <main id="main-content" tabindex="-1">
    <PageState
      title="Страница не найдена"
      description="Проверьте адрес или перейдите на главную."
      ><RouterLink to="/" class="button button--primary"
        >На главную</RouterLink
      ></PageState
    >
  </main>
</template>
