<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useMutation } from '@tanstack/vue-query';
import { RouterLink, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  createV1PlaylistsPost,
  isIdentifier,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';

const emit = defineEmits<{ created: []; accessLost: [] }>();
const props = defineProps<{
  videoId?: Identifier;
  compact?: boolean;
  disabled?: boolean;
  navigate?: boolean;
  publicHeader?: boolean;
}>();
const router = useRouter();
const session = useSession();
const menuOpen = ref(false),
  error = ref(''),
  sending = ref(false);
const fields = ref<Record<string, string>>({});
const button = ref<HTMLButtonElement>(),
  menu = ref<HTMLElement>(),
  dialog = ref<HTMLDialogElement>(),
  titleInput = ref<HTMLTextAreaElement>();
const form = reactive({ title: '', description: '', visibility: 'public' });
const titleLength = computed(() => [...form.title.trim()].length);
const descriptionLength = computed(() => [...form.description].length);
const valid = computed(
  () =>
    titleLength.value > 0 &&
    titleLength.value <= 100 &&
    descriptionLength.value <= 5000 &&
    !form.title.includes('\0') &&
    !form.description.includes('\0'),
);
let controller: AbortController | undefined,
  disposed = false,
  revision = 0;
let submitted:
  | {
      scope: string;
      videoId?: Identifier;
      title: string;
      description: string;
      visibility: string;
      signal: AbortSignal;
    }
  | undefined;
const identityReady = () =>
  !!session.account && !session.loading && !session.changing && !session.error;
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const draft = submitted;
    if (!draft || disposed || !identityReady() || draft.scope !== session.scope)
      throw new DOMException('Aborted', 'AbortError');
    const playlist = unwrap(
      await createV1PlaylistsPost({
        body: {
          ...(draft.videoId ? { video_id: draft.videoId } : {}),
          title: draft.title,
          description: draft.description,
          visibility: draft.visibility,
        },
        signal: draft.signal,
      }),
    );
    if (!isIdentifier(playlist.id) || !isIdentifier(playlist.channel_id))
      throw new Error('Invalid playlist identifier');
    return { scope: draft.scope };
  },
});
function outside(event: PointerEvent) {
  if (
    !menu.value?.contains(event.target as Node) &&
    !button.value?.contains(event.target as Node)
  )
    menuOpen.value = false;
}
document.addEventListener('pointerdown', outside);
async function toggleMenu() {
  menuOpen.value = !menuOpen.value;
  await nextTick();
  if (menuOpen.value)
    menu.value?.querySelector<HTMLElement>('a,button')?.focus();
}
function closeMenu() {
  menuOpen.value = false;
  button.value?.focus();
}
async function open() {
  if (disposed || sending.value || props.disabled || !identityReady()) return;
  menuOpen.value = false;
  form.title = '';
  form.description = '';
  form.visibility = 'public';
  error.value = '';
  fields.value = {};
  mutation.reset();
  dialog.value?.showModal();
  await nextTick();
  titleInput.value?.focus();
}
function create() {
  menuOpen.value = false;
  if (props.navigate)
    void router.push({
      path: '/studio/content/playlists',
      query: { create: 'playlist' },
    });
  else void open();
}
defineExpose({ open });
function clear() {
  revision++;
  submitted = undefined;
  sending.value = false;
  form.title = '';
  form.description = '';
  error.value = '';
  fields.value = {};
  mutation.reset();
}
function close() {
  if (sending.value) return;
  dialog.value?.close();
  clear();
  button.value?.focus();
}
function closed() {
  if (!dialog.value?.open) clear();
}
watch(
  () => `${session.scope}:${props.videoId ?? ''}`,
  () => {
    controller?.abort();
    menuOpen.value = false;
    dialog.value?.close();
    clear();
  },
);
onBeforeUnmount(() => {
  disposed = true;
  controller?.abort();
  dialog.value?.close();
  clear();
  document.removeEventListener('pointerdown', outside);
});
async function submit() {
  if (sending.value || !identityReady() || !dialog.value?.open) return;
  error.value = '';
  fields.value = {};
  if (
    !titleLength.value ||
    titleLength.value > 100 ||
    form.title.includes('\0')
  )
    fields.value.title = 'Введите от 1 до 100 символов.';
  if (descriptionLength.value > 5000 || form.description.includes('\0'))
    fields.value.description = 'Описание — не более 5000 символов.';
  if (Object.keys(fields.value).length) {
    await focusError();
    return;
  }
  const scope = session.scope,
    currentRevision = revision;
  controller = new AbortController();
  submitted = {
    scope,
    videoId: props.videoId,
    title: form.title.trim(),
    description: form.description,
    visibility: form.visibility,
    signal: controller.signal,
  };
  sending.value = true;
  try {
    const result = await mutation.mutateAsync();
    if (
      disposed ||
      result.scope !== session.scope ||
      currentRevision !== revision
    )
      return;
    sending.value = false;
    emit('created');
    close();
  } catch (cause) {
    if (disposed || scope !== session.scope || currentRevision !== revision)
      return;
    sending.value = false;
    if (
      props.videoId &&
      cause instanceof ApiError &&
      (cause.status === 404 ||
        (cause.status === 403 && cause.code !== 'csrf_failed'))
    )
      emit('accessLost');
    if (cause instanceof ApiError) {
      for (const field of cause.fields) {
        const name = field.location.at(-1);
        if (name === 'title' || name === 'description' || name === 'visibility')
          fields.value[name] = 'Проверьте значение поля.';
      }
    }
    error.value = apiMessage(cause);
    await focusError();
  } finally {
    if (currentRevision === revision) {
      sending.value = false;
      submitted = undefined;
    }
  }
}
async function focusError() {
  await nextTick();
  const name = ['title', 'description', 'visibility'].find(
    (field) => fields.value[field],
  );
  if (name)
    dialog.value?.querySelector<HTMLElement>(`#playlist-${name}`)?.focus();
}
function trapFocus(event: KeyboardEvent) {
  const controls = [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea:not(:disabled), select:not(:disabled)',
    ) ?? []),
  ];
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
</script>
<template>
  <div class="create-playlist" @keydown.esc.stop="closeMenu">
    <button
      v-if="compact"
      ref="button"
      :disabled="disabled"
      type="button"
      class="button button--secondary"
      @click="open"
    >
      <AppIcon name="playlistAdd" />Создать плейлист
    </button>
    <button
      v-else
      ref="button"
      type="button"
      class="studio-create"
      :class="{ 'studio-create--public': publicHeader }"
      aria-label="Создать"
      aria-haspopup="true"
      :aria-expanded="menuOpen"
      aria-controls="studio-create-menu"
      @click="toggleMenu"
    >
      <AppIcon :name="publicHeader ? 'plus' : 'create'" /><span>Создать</span>
    </button>
    <div
      v-if="menuOpen"
      id="studio-create-menu"
      ref="menu"
      class="studio-create-menu"
      aria-label="Создать контент"
    >
      <RouterLink to="/studio/upload"
        ><AppIcon name="video" />Добавить видео</RouterLink
      >
      <button type="button" @click="create">
        <AppIcon name="playlistAdd" />Создать плейлист
      </button>
    </div>
    <Teleport v-if="!navigate" to="body"
      ><dialog
        ref="dialog"
        class="playlist-dialog"
        aria-labelledby="playlist-dialog-title"
        @cancel.prevent="close"
        @keydown.esc.stop.prevent="close"
        @keydown.tab="trapFocus"
        @close="closed"
      >
        <form novalidate :aria-busy="sending" @submit.stop.prevent="submit">
          <header>
            <h2 id="playlist-dialog-title">Новый плейлист</h2>
            <button
              type="button"
              class="icon-button"
              aria-label="Закрыть создание плейлиста"
              :disabled="sending"
              @click="close"
            >
              <AppIcon name="close" />
            </button>
          </header>
          <div class="playlist-dialog__body">
            <label
              class="studio-field"
              :class="{
                'studio-field--invalid': fields.title || titleLength > 100,
              }"
              for="playlist-title"
              ><span>Название (обязательное поле)</span
              ><textarea
                id="playlist-title"
                ref="titleInput"
                v-model="form.title"
                placeholder="Введите название"
                rows="2"
                :disabled="sending"
                :aria-invalid="!!fields.title || titleLength > 100"
                aria-describedby="playlist-title-hint"
                @input="delete fields.title"
              /><small
                id="playlist-title-hint"
                :class="{ field__error: fields.title || titleLength > 100 }"
                >{{
                  fields.title ||
                  (titleLength > 100 ? `${titleLength}/100` : '')
                }}</small
              ></label
            >
            <label
              class="studio-field studio-field--description"
              :class="{
                'studio-field--invalid':
                  fields.description || descriptionLength > 5000,
              }"
              for="playlist-description"
              ><span>Описание</span
              ><textarea
                id="playlist-description"
                v-model="form.description"
                placeholder="Добавьте описание"
                rows="5"
                :disabled="sending"
                :aria-invalid="!!fields.description || descriptionLength > 5000"
                aria-describedby="playlist-description-hint"
                @input="delete fields.description"
              /><small
                id="playlist-description-hint"
                :class="{
                  field__error: fields.description || descriptionLength > 5000,
                }"
                >{{
                  fields.description ||
                  (descriptionLength > 5000 ? `${descriptionLength}/5000` : '')
                }}</small
              ></label
            >
            <h3>Доступ</h3>
            <label
              class="studio-field studio-field--select"
              for="playlist-visibility"
              ><span>Параметры доступа</span
              ><select
                id="playlist-visibility"
                v-model="form.visibility"
                :disabled="sending"
                :aria-invalid="!!fields.visibility"
                aria-describedby="playlist-visibility-hint"
                @change="delete fields.visibility"
              >
                <option value="public">Открытый доступ</option>
                <option value="unlisted">Доступ по ссылке</option>
                <option value="private">Ограниченный доступ</option></select
              ><AppIcon name="down"
            /></label>
            <p
              v-if="fields.visibility"
              id="playlist-visibility-hint"
              class="field__error"
            >
              {{ fields.visibility }}
            </p>
            <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          </div>
          <footer>
            <button
              type="submit"
              class="button playlist-dialog__submit"
              :disabled="!valid || sending"
            >
              {{ sending ? 'Создаём…' : 'Создать' }}
            </button>
          </footer>
        </form>
      </dialog></Teleport
    >
  </div>
</template>
<style scoped lang="scss">
.create-playlist {
  position: relative;
}
.studio-create {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--color-border);
  border-radius: 20px;
  background: transparent;
  height: 36px;
  padding: 0 12px;
  font-weight: 500;
  cursor: pointer;
  &:hover {
    background: var(--color-surface);
  }
}
.studio-create--public {
  background: var(--color-surface);
  border-color: transparent;
}
@media (max-width: 767px) {
  .studio-create {
    width: 40px;
    padding: 8px;
    gap: 0;
    span {
      display: none;
    }
  }
}
.studio-create-menu {
  position: absolute;
  top: 40px;
  right: 0;
  width: 225px;
  background: var(--studio-dialog);
  border-radius: 12px;
  padding: 12px 0;
  box-shadow: 0 4px 16px #0004;
  button,
  a {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    padding: 8px 24px;
    background: transparent;
    border: 0;
    cursor: pointer;
    font-size: 16px;
    text-align: left;
    &:hover {
      background: var(--color-hover);
    }
    svg {
      flex-shrink: 0;
    }
  }
}
.playlist-dialog {
  padding: 0;
  border: 0;
  border-radius: var(--studio-dialog-radius);
  width: 576px;
  max-width: calc(100vw - 32px);
  max-height: calc(100dvh - 48px);
  background: var(--studio-dialog);
  color: var(--color-text);
  overflow-y: auto;
  &::backdrop {
    background: #0009;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    min-height: 68px;
    padding: 12px 16px 12px 24px;
    border-bottom: 1px solid var(--color-border);
  }
  h2 {
    font-size: 20px;
    line-height: 28px;
    margin: 0;
  }
  h3 {
    font-size: 16px;
    line-height: 24px;
    font-weight: 500;
    margin: 0 0 8px;
  }
  &__body {
    padding: 24px;
  }
  footer {
    border-top: 1px solid var(--color-border);
    padding: 16px 24px;
    display: flex;
    justify-content: flex-end;
  }
  &__submit {
    background: var(--color-text);
    color: var(--color-background);
    &:disabled {
      background: var(--color-hover);
      color: var(--color-muted);
      opacity: 1;
    }
  }
}
.studio-field {
  display: flex;
  flex-direction: column;
  position: relative;
  border: 1px solid var(--studio-input-border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 24px;
  min-height: 82px;
  > span {
    color: var(--color-muted);
    font-size: 12px;
    line-height: 16px;
  }
  textarea,
  select {
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 16px;
    line-height: 24px;
    outline: 0;
    width: 100%;
    resize: none;
  }
  textarea {
    flex: 1;
  }
  small:empty {
    display: none;
  }
  &:focus-within {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 1px var(--color-accent);
  }
  &--invalid {
    border-color: var(--color-error);
  }
  &--description {
    min-height: 156px;
  }
  &--select {
    min-height: 56px;
    margin-bottom: 0;
    select {
      appearance: none;
      padding-right: 32px;
      height: 22px;
      line-height: 22px;
      cursor: pointer;
    }
    option {
      background: var(--studio-dialog);
    }
    svg {
      position: absolute;
      right: 12px;
      top: 16px;
      pointer-events: none;
    }
  }
}
@media (max-width: 767px) {
  .playlist-dialog {
    max-height: calc(100dvh - 24px);
  }
}
</style>
