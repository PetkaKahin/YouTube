<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
const props = defineProps<{
  open: boolean;
  anchor?: HTMLElement;
  title: string;
}>();
const emit = defineEmits<{ close: [restoreFocus: boolean] }>();
const panel = ref<HTMLElement>();
const position = ref({ left: 0, top: 0, width: 400, maxHeight: 500 });
const style = computed(() => ({
  left: position.value.left + 'px',
  top: position.value.top + 'px',
  width: position.value.width + 'px',
  maxHeight: position.value.maxHeight + 'px',
}));
function place() {
  if (!props.open || !props.anchor) return;
  const box = props.anchor.getBoundingClientRect(),
    margin = 12,
    gap = 8;
  const width = Math.min(400, window.innerWidth - margin * 2),
    height = Math.min(
      panel.value?.scrollHeight ?? 420,
      window.innerHeight - margin * 2,
    );
  const above = box.top - margin - gap,
    below = window.innerHeight - box.bottom - margin - gap;
  const useAbove = above > below;
  position.value = {
    width,
    left: Math.max(
      margin,
      Math.min(window.innerWidth - width - margin, box.right - width),
    ),
    top: useAbove
      ? Math.max(margin, box.top - gap - Math.min(height, above))
      : Math.max(
          margin,
          Math.min(box.bottom + gap, window.innerHeight - margin),
        ),
    maxHeight: Math.max(120, useAbove ? above : below),
  };
}
function modalOpen() {
  return !!document.querySelector('dialog[open]');
}
function outside(event: PointerEvent) {
  if (
    props.open &&
    !modalOpen() &&
    !panel.value?.contains(event.target as Node) &&
    !props.anchor?.contains(event.target as Node)
  )
    emit('close', false);
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open && !modalOpen()) {
    event.preventDefault();
    event.stopPropagation();
    emit('close', true);
  }
}
function blur(event: FocusEvent) {
  if (
    props.open &&
    !modalOpen() &&
    event.relatedTarget &&
    !panel.value?.contains(event.relatedTarget as Node) &&
    !props.anchor?.contains(event.relatedTarget as Node)
  )
    emit('close', false);
}
let observer: ResizeObserver | undefined;
watch(
  [() => props.open, panel],
  async ([open]) => {
    observer?.disconnect();
    if (!open) return;
    await nextTick();
    place();
    if (panel.value) {
      observer = new ResizeObserver(place);
      observer.observe(panel.value);
      panel.value
        .querySelector<HTMLElement>('button:not(:disabled),a[href]')
        ?.focus();
    }
  },
  { flush: 'post' },
);
window.addEventListener('resize', place);
window.addEventListener('scroll', place, true);
document.addEventListener('pointerdown', outside, true);
onBeforeUnmount(() => {
  observer?.disconnect();
  window.removeEventListener('resize', place);
  window.removeEventListener('scroll', place, true);
  document.removeEventListener('pointerdown', outside, true);
});
</script>
<template>
  <Teleport to="body"
    ><section
      v-if="open"
      ref="panel"
      class="anchored-dropdown"
      :style="style"
      role="dialog"
      :aria-label="title"
      @keydown="escape"
      @focusout="blur"
    >
      <header>
        <h2>{{ title }}</h2>
        <button
          type="button"
          class="icon-button"
          aria-label="Закрыть список"
          @click="emit('close', true)"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <div class="anchored-dropdown__body"><slot /></div>
      <footer v-if="$slots.footer"><slot name="footer" /></footer></section
  ></Teleport>
</template>
<style scoped lang="scss">
.anchored-dropdown {
  position: fixed;
  z-index: 45;
  background: var(--color-menu);
  color: var(--color-text);
  border-radius: 12px;
  box-shadow: 0 8px 32px #0006;
  overflow: auto;
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px 0 16px;
    gap: 12px;
    h2 {
      font-size: 16px;
      line-height: 24px;
      margin: 0;
    }
  }
  &__body {
    padding: 8px 16px 12px;
  }
  footer {
    border-top: 1px solid var(--color-border);
    padding: 12px;
    :deep(.create-playlist),
    :deep(.create-playlist > button) {
      width: 100%;
    }
  }
}
</style>
