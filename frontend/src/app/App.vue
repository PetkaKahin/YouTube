<script setup lang="ts">
import { provide, ref, watch } from 'vue';
import { watchAppearance } from '../shared/lib/watch-appearance';
import { RouterView, useRoute } from 'vue-router';
import { AppShell } from '../widgets/app-shell';
const route = useRoute();
const color = ref<string>();
let owner: symbol | undefined;
provide(watchAppearance, {
  set(source, value) {
    owner = source;
    color.value = value;
  },
  clear(source) {
    if (owner === source) {
      owner = undefined;
      color.value = undefined;
    }
  },
});
watch(
  () => `${route.path}:${String(route.query.v ?? '')}`,
  () => {
    owner = undefined;
    color.value = undefined;
  },
  { flush: 'sync' },
);
</script>

<template>
  <RouterView v-if="route.meta.standalone" />
  <AppShell v-else :ambient-color="route.meta.watchLayout ? color : undefined"
    ><RouterView :key="route.meta.library ? route.path : undefined"
  /></AppShell>
</template>
