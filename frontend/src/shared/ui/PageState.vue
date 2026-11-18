<script setup lang="ts">
defineProps<{ title: string; description?: string; error?: boolean }>();
defineEmits<{ retry: [] }>();
</script>
<template>
  <section class="page-state" :role="error ? 'alert' : 'status'">
    <h1>{{ title }}</h1>
    <p v-if="description">{{ description }}</p>
    <button
      v-if="error"
      class="button button--secondary"
      type="button"
      @click="$emit('retry')"
    >
      Повторить
    </button>
    <slot />
  </section>
</template>
<style scoped lang="scss">
.page-state {
  text-align: center;
  max-width: 640px;
  margin: 48px auto;
  padding: 24px;
  border-radius: 12px;
  background: var(--color-panel);
  h1 {
    font-size: 24px;
    line-height: 28px;
    margin: 0;
  }
  p {
    color: var(--color-muted);
    line-height: 1.5;
    margin: 8px 0 0;
  }
  button {
    margin-top: 20px;
  }
}
</style>
