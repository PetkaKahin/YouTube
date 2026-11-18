<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
const props = defineProps<{ open: boolean; title: string; busy?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
let previous: HTMLElement | null = null;
watch(
  [() => props.open, dialog],
  async () => {
    if (props.open && dialog.value && !dialog.value.open) {
      previous = document.activeElement as HTMLElement;
      dialog.value.showModal();
      await nextTick();
      dialog.value.querySelector<HTMLElement>('[autofocus]')?.focus();
    } else if (!props.open && dialog.value?.open) {
      dialog.value.close();
      if (previous?.isConnected) previous.focus();
    }
  },
  { flush: 'post' },
);
function close() {
  if (!props.busy) emit('close');
}
function trap(event: KeyboardEvent) {
  const controls = [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled):not([hidden]), textarea:not(:disabled), select:not(:disabled), a[href]',
    ) ?? []),
  ].filter((item) => item.getClientRects().length);
  const first = controls[0],
    last = controls.at(-1);
  if (
    !first ||
    (event.shiftKey && document.activeElement === first) ||
    (!event.shiftKey && document.activeElement === last)
  ) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
}
onBeforeUnmount(() => {
  dialog.value?.close();
  if (previous?.isConnected) previous.focus();
});
</script>
<template>
  <Teleport to="body"
    ><dialog
      ref="dialog"
      class="studio-dialog"
      :aria-label="title"
      :aria-busy="busy"
      @cancel.prevent="close"
      @keydown.esc.stop.prevent="close"
      @keydown.tab.stop="trap"
    >
      <header>
        <h2>{{ title }}</h2>
        <button
          type="button"
          class="icon-button"
          aria-label="Закрыть окно"
          :disabled="busy"
          @click="close"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <div class="studio-dialog__body"><slot /></div>
      <footer v-if="$slots.footer"><slot name="footer" /></footer></dialog
  ></Teleport>
</template>
<style scoped lang="scss">
.studio-dialog {
  color: var(--color-text);
  background: var(--studio-dialog);
  border: 0;
  border-radius: 24px;
  padding: 0;
  width: 576px;
  max-width: calc(100vw - 32px);
  max-height: calc(100dvh - 32px);
  overflow-y: auto;
  &::backdrop {
    background: #0009;
  }
  header {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 12px 16px 12px 24px;
    h2 {
      margin: 0;
      flex: 1;
      font-size: 20px;
      line-height: 28px;
    }
  }
  &__body {
    padding: 0 24px 24px;
  }
  footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    padding: 0 24px 16px;
  }
}
</style>
