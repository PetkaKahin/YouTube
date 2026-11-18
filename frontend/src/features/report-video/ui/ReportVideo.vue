<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { useMutation } from '@tanstack/vue-query';
import { useRoute, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  submitV1VideosVideoIdReportsPost,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';

const props = defineProps<{ videoId: Identifier }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  route = useRoute(),
  router = useRouter();
const reasons = [
  ['sexual_content', 'Содержание сексуального характера'],
  ['violence', 'Жестокое или отталкивающее содержание'],
  ['harassment', 'Оскорбления или травля'],
  ['spam', 'Спам или мошенничество'],
  ['copyright', 'Нарушение авторских прав'],
  ['other', 'Другое'],
] as const;
type Reason = (typeof reasons)[number][0];
const reason = ref<Reason>(),
  menuOpen = ref(false),
  pending = ref(false),
  error = ref(''),
  notice = ref('');
const root = ref<HTMLElement>(),
  button = ref<HTMLButtonElement>(),
  dialog = ref<HTMLDialogElement>();
const begin = useOperations(() => `${session.scope}:${props.videoId}`);
function closeMenu() {
  menuOpen.value = false;
  button.value?.focus();
}
async function toggleMenu() {
  menuOpen.value = !menuOpen.value;
  await nextTick();
  if (menuOpen.value)
    root.value
      ?.querySelector<HTMLButtonElement>('.report-menu button')
      ?.focus();
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) menuOpen.value = false;
}
document.addEventListener('pointerdown', outside);
function clear() {
  reason.value = undefined;
  error.value = '';
  mutation.reset();
}
function close() {
  if (pending.value) return;
  dialog.value?.close();
  clear();
  button.value?.focus();
}
async function open() {
  closeMenu();
  notice.value = '';
  clear();
  if (!session.account) {
    await router.push({ path: '/login', query: { returnTo: route.fullPath } });
    return;
  }
  dialog.value?.showModal();
  await nextTick();
  dialog.value?.querySelector<HTMLInputElement>('input')?.focus();
}
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const work = begin();
    try {
      const result = unwrap(
        await submitV1VideosVideoIdReportsPost({
          path: { video_id: props.videoId },
          body: { reason: reason.value! },
          signal: work.signal,
        }),
      );
      work.check();
      if (result.accepted !== true) throw new Error('Report not accepted');
    } finally {
      work.finish();
    }
  },
});
async function submit() {
  if (pending.value || !reason.value || !dialog.value?.open || !session.account)
    return;
  pending.value = true;
  error.value = '';
  notice.value = '';
  const identity = `${session.scope}:${props.videoId}`;
  try {
    await mutation.mutateAsync();
    if (identity !== `${session.scope}:${props.videoId}` || disposed) return;
    pending.value = false;
    close();
    notice.value = 'Жалоба отправлена на рассмотрение.';
  } catch (cause) {
    if (identity !== `${session.scope}:${props.videoId}` || disposed) return;
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      pending.value = false;
      close();
      emit('accessLost');
      return;
    }
    error.value = apiMessage(cause);
  } finally {
    if (identity === `${session.scope}:${props.videoId}` && !disposed)
      pending.value = false;
  }
}
function trap(event: KeyboardEvent) {
  // Native radio groups have one Tab stop; arrows change the selection.
  const controls = [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'input:not(:disabled),button:not(:disabled)',
    ) ?? []),
  ].filter(
    (x) =>
      !(x instanceof HTMLInputElement) ||
      x.value === (reason.value ?? reasons[0][0]),
  );
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
watch(
  () => `${session.scope}:${props.videoId}`,
  () => {
    pending.value = false;
    dialog.value?.close();
    menuOpen.value = false;
    notice.value = '';
    clear();
  },
);
let disposed = false;
onBeforeUnmount(() => {
  disposed = true;
  dialog.value?.close();
  mutation.reset();
  document.removeEventListener('pointerdown', outside);
});
</script>
<template>
  <div ref="root" class="report-video" @keydown.esc.stop="closeMenu">
    <button
      ref="button"
      type="button"
      class="icon-button report-trigger"
      aria-label="Другие действия"
      aria-haspopup="true"
      :aria-expanded="menuOpen"
      :disabled="session.loading || session.changing"
      @click="toggleMenu"
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <circle cx="5" cy="12" r="2" fill="currentColor" />
        <circle cx="12" cy="12" r="2" fill="currentColor" />
        <circle cx="19" cy="12" r="2" fill="currentColor" />
      </svg>
    </button>
    <div v-if="menuOpen" class="report-menu">
      <button type="button" @click="open">Пожаловаться</button>
    </div>
    <p v-if="notice" class="report-notice" role="status">{{ notice }}</p>
    <Teleport to="body"
      ><dialog
        ref="dialog"
        class="report-dialog"
        aria-labelledby="report-title"
        :aria-busy="pending"
        @cancel.prevent="close"
        @keydown.esc.stop.prevent="close"
        @keydown.tab="trap"
      >
        <form novalidate @submit.prevent="submit">
          <h2 id="report-title">Причина жалобы</h2>
          <fieldset :disabled="pending">
            <legend class="sr-only">Выберите причину жалобы</legend>
            <label v-for="[value, label] in reasons" :key="value"
              ><input
                v-model="reason"
                type="radio"
                name="report-reason"
                :value="value"
              /><span>{{ label }}</span></label
            >
          </fieldset>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <footer>
            <button type="button" :disabled="pending" @click="close">
              Отмена</button
            ><button type="submit" :disabled="!reason || pending">
              {{ pending ? 'Отправляем…' : 'Пожаловаться' }}
            </button>
          </footer>
        </form>
      </dialog></Teleport
    >
  </div>
</template>
<style scoped lang="scss">
.report-video {
  position: relative;
}
.report-trigger {
  width: 48px;
  height: 48px;
  background: var(--color-surface);
  border-radius: 50%;
}
.report-menu {
  position: absolute;
  z-index: 20;
  top: 44px;
  right: 0;
  width: 200px;
  padding: 8px 0;
  border-radius: 12px;
  background: var(--color-surface);
  box-shadow: 0 4px 16px #0004;
  button {
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
    padding: 12px 24px;
    cursor: pointer;
    &:hover {
      background: var(--color-hover);
    }
  }
}
.report-notice {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: 24px;
  max-width: calc(100vw - 32px);
  z-index: 20;
  width: 250px;
  padding: 12px;
  background: var(--color-surface);
  border-radius: 8px;
  font-size: 14px;
}
.report-dialog {
  width: 450px;
  max-width: calc(100vw - 16px);
  max-height: calc(100dvh - 24px);
  overflow-y: auto;
  padding: 0;
  border: 0;
  border-radius: 12px;
  color: var(--color-text);
  background: var(--color-surface);
  &::backdrop {
    background: #0009;
  }
  form {
    padding: 20px 24px 8px;
  }
  h2 {
    margin: 0 0 4px;
    font-size: 16px;
    line-height: 24px;
    font-weight: 400;
  }
  legend {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
  }
  label {
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 40px;
    font-size: 14px;
    line-height: 20px;
    cursor: pointer;
  }
  input {
    appearance: none;
    margin: 0;
    width: 20px;
    height: 20px;
    border: 2px solid currentColor;
    border-radius: 50%;
    flex-shrink: 0;
    &:checked {
      background: currentColor;
      box-shadow: inset 0 0 0 3px var(--color-surface);
    }
  }
  footer {
    display: flex;
    justify-content: flex-end;
    gap: 16px;
    margin-top: 36px;
    button {
      border: 0;
      background: transparent;
      padding: 8px;
      font-size: 14px;
      line-height: 20px;
      font-weight: 500;
      cursor: pointer;
      &:disabled {
        color: var(--color-muted);
        opacity: 0.55;
        cursor: default;
      }
    }
  }
  .form-error {
    font-size: 14px;
  }
}
</style>
