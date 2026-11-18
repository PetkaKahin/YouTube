<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  shallowRef,
  watch,
} from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import { VideoThumbnail } from '../../../features/video-thumbnail';
import { VideoPlaylists } from '../../../features/video-playlists';
import { VideoPermissions } from '../../../features/video-permissions';
import { CreatePlaylist } from '../../../features/create-playlist';
import {
  ApiError,
  apiMessage,
  createV1VideosPost,
  settingsV1VideosVideoIdSettingsGet,
  editV1VideosVideoIdSettingsPut,
  processingV1VideosVideoIdProcessingGet,
  startV1VideosVideoIdUploadPost,
  refreshV1VideosVideoIdUploadUploadIdRefreshPost,
  completeV1VideosVideoIdUploadUploadIdCompletePost,
  publishV1VideosVideoIdPublishPost,
  isIdentifier,
  unwrap,
  type Identifier,
  type VideoSettingsResponse,
} from '../../../shared/api';
import {
  maxVideoBytes,
  transferParts,
  validateUpload,
} from '../../../shared/lib/uploads';
import { AppIcon, PageState } from '../../../shared/ui';
import { processingFailure } from '../model/processing';

const route = useRoute(),
  router = useRouter(),
  session = useSession(),
  client = useQueryClient();
const readId = () =>
  isIdentifier(route.query.video) ? route.query.video : undefined;
const id = ref<Identifier | undefined>(readId());
const invalidId = computed(
  () => route.query.video !== undefined && !isIdentifier(route.query.video),
);
const dialog = ref<HTMLDialogElement>(),
  fileInput = ref<HTMLInputElement>();
const file = shallowRef<File>();
const thumbnailEditor = ref<InstanceType<typeof VideoThumbnail>>(),
  playlistEditor = ref<InstanceType<typeof VideoPlaylists>>(),
  permissionEditor = ref<InstanceType<typeof VideoPermissions>>();
const step = ref<'choose' | 'information' | 'access'>(
  id.value ? 'information' : 'choose',
);
const form = reactive({
  title: '',
  description: '',
  visibility: '' as '' | 'public' | 'private',
});
const error = ref(''),
  fields = ref<Record<string, string>>({}),
  denied = ref(false),
  saved = ref(false);
const phase = ref<
  'idle' | 'saving' | 'uploading' | 'confirming' | 'publishing'
>('idle');
const sentBytes = ref(0),
  totalBytes = ref(0),
  dragOver = ref(false);
let controller: AbortController | undefined,
  uploadId: Identifier | undefined,
  generation = 0,
  disposed = false,
  hydrated: Identifier | undefined;
const settingsKey = computed(
  () =>
    ['upload-settings', session.scope, id.value, session.account?.id] as const,
);
const processingKey = computed(
  () => ['upload-processing', session.scope, id.value] as const,
);
const identityReady = computed(
  () =>
    !!session.account &&
    !session.loading &&
    !session.changing &&
    !session.error,
);
const allowed = computed(
  () => identityReady.value && !!id.value && !invalidId.value && !denied.value,
);
function validSettings(
  value: VideoSettingsResponse,
  requestedId = id.value,
  ownerId = session.account?.id,
) {
  if (
    !isIdentifier(value.id) ||
    value.id !== requestedId ||
    value.owner_id !== ownerId
  )
    throw new Error('Invalid video settings');
  return value;
}
const settings = useQuery({
  queryKey: settingsKey,
  enabled: allowed,
  gcTime: 0,
  queryFn: async ({ queryKey: [, , videoId, ownerId], signal }) => {
    if (!videoId || !ownerId) throw new ApiError(404, 'not_found');
    return validSettings(
      unwrap(
        await settingsV1VideosVideoIdSettingsGet({
          path: { video_id: videoId },
          signal,
        }),
      ),
      videoId,
      ownerId,
    );
  },
  retry: false,
});
const processing = useQuery({
  queryKey: processingKey,
  enabled: computed(
    () => allowed.value && settings.data.value?.upload === 'uploaded',
  ),
  gcTime: 0,
  queryFn: async ({ queryKey: [, , videoId], signal }) => {
    if (!videoId) throw new ApiError(404, 'not_found');
    return unwrap(
      await processingV1VideosVideoIdProcessingGet({
        path: { video_id: videoId },
        signal,
      }),
    );
  },
  refetchInterval: (query) =>
    query.state.error ||
    ['ready', 'partial', 'failed'].includes(query.state.data?.processing ?? '')
      ? false
      : 2000,
  retry: false,
});
watch(settings.data, (value) => {
  if (!value || hydrated === value.id) return;
  hydrated = value.id;
  form.title = value.title;
  form.description = value.description;
  form.visibility = value.visibility === 'public' ? 'public' : 'private';
  saved.value = value.publication === 'published';
});
watch(
  () => [form.title, form.description, form.visibility],
  () => {
    const value = settings.data.value;
    if (
      value &&
      (form.title.trim() !== value.title ||
        form.description !== value.description ||
        form.visibility !== value.visibility)
    )
      saved.value = false;
  },
);
watch([settings.error, processing.error], (causes) => {
  if (
    causes.some(
      (cause) =>
        cause instanceof ApiError && [401, 403, 404].includes(cause.status),
    )
  ) {
    denied.value = true;
    controller?.abort();
    file.value = undefined;
    form.title = '';
    form.description = '';
    form.visibility = '';
    error.value = '';
    client.removeQueries({ queryKey: settingsKey.value, exact: true });
    client.removeQueries({ queryKey: processingKey.value, exact: true });
  }
});
watch(
  () => route.query.video,
  () => {
    const next = readId();
    if (next === id.value) return;
    generation++;
    controller?.abort();
    id.value = next;
    hydrated = undefined;
    uploadId = undefined;
    denied.value = false;
    saved.value = false;
    file.value = undefined;
    form.title = '';
    form.description = '';
    form.visibility = '';
    error.value = '';
    fields.value = {};
    phase.value = 'idle';
    sentBytes.value = 0;
    totalBytes.value = 0;
    step.value = next ? 'information' : 'choose';
  },
);
const titleLength = computed(() => [...form.title.trim()].length),
  descriptionLength = computed(() => [...form.description].length);
const textValid = computed(
  () =>
    titleLength.value > 0 &&
    titleLength.value <= 100 &&
    descriptionLength.value <= 5000 &&
    !/[\0\uD800-\uDFFF]/u.test(form.title + form.description),
);
const accepted = computed(() => settings.data.value?.upload === 'uploaded');
const processingData = computed(() =>
  processing.isError.value ? undefined : processing.data.value,
);
const incompleteRestore = computed(
  () =>
    !!id.value &&
    !!settings.data.value &&
    !file.value &&
    !accepted.value &&
    !saved.value,
);
const canView = computed(
  () =>
    settings.data.value?.publication === 'published' &&
    settings.data.value.moderation === 'visible' &&
    !!processingData.value?.available_qualities.length &&
    !denied.value,
);
const progress = computed(() =>
  totalBytes.value
    ? Math.floor(Math.min(sentBytes.value / totalBytes.value, 1) * 100)
    : 0,
);
const processingLabel = computed(() => {
  if (processing.isError.value) return 'Состояние обработки недоступно';
  const state = processingData.value?.processing;
  if (!state) return 'Проверяем состояние обработки';
  if (state === 'failed') return 'Не удалось обработать видео';
  if (state === 'partial') return 'Видео готово частично';
  if (state === 'ready') return 'Обработка завершена';
  if (state === 'processing') return 'Видео обрабатывается';
  if (state === 'pending') return 'Ожидаем обработку видео';
  return 'Состояние обработки неизвестно';
});
function choose(files: FileList | null) {
  if (disposed || !identityReady.value || phase.value !== 'idle' || id.value)
    return;
  error.value = '';
  if (!files?.length) return;
  if (files.length !== 1) {
    error.value = 'Выберите один видеофайл.';
    return;
  }
  const selected = files[0]!;
  if (!selected.size || selected.size > maxVideoBytes) {
    error.value = 'Размер файла должен быть от 1 байта до 5 ГБ.';
    return;
  }
  file.value = selected;
  sentBytes.value = 0;
  totalBytes.value = selected.size;
  if (!id.value) form.title = selected.name.replace(/\.[^.]+$/, '');
  step.value = 'information';
  void nextTick(() => {
    if (!disposed && file.value === selected)
      dialog.value?.querySelector<HTMLElement>('#video-title')?.focus();
  });
}
function drop(event: DragEvent) {
  dragOver.value = false;
  choose(event.dataTransfer?.files ?? null);
}
async function validateText() {
  fields.value = {};
  if (
    !titleLength.value ||
    titleLength.value > 100 ||
    /[\0\uD800-\uDFFF]/u.test(form.title)
  )
    fields.value.title = 'Введите от 1 до 100 символов.';
  if (
    descriptionLength.value > 5000 ||
    /[\0\uD800-\uDFFF]/u.test(form.description)
  )
    fields.value.description = 'Описание — не более 5000 символов.';
  await focusError();
  return !Object.keys(fields.value).length;
}
async function focusError() {
  await nextTick();
  const name = ['title', 'description', 'visibility'].find(
    (field) => fields.value[field],
  );
  if (name)
    dialog.value
      ?.querySelector<HTMLElement>(
        name === 'visibility' ? '#video-private' : `#video-${name}`,
      )
      ?.focus();
}
async function next() {
  if (phase.value !== 'idle' || disposed || !identityReady.value) return;
  const version = generation,
    scope = session.scope;
  if (
    (await validateText()) &&
    !disposed &&
    version === generation &&
    scope === session.scope
  ) {
    step.value = 'access';
    await nextTick();
    dialog.value?.querySelector<HTMLElement>('#video-private')?.focus();
  }
}
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const scope = session.scope,
      version = generation;
    controller = new AbortController();
    const signal = controller.signal;
    const ensure = () => {
      if (
        disposed ||
        scope !== session.scope ||
        version !== generation ||
        signal.aborted
      )
        throw new DOMException('Aborted', 'AbortError');
    };
    ensure();
    const body = {
      title: form.title.trim(),
      description: form.description,
      visibility: form.visibility as 'public' | 'private',
    };
    if (!id.value) {
      const value = unwrap(await createV1VideosPost({ body, signal }));
      ensure();
      if (!isIdentifier(value.id) || value.owner_id !== session.account?.id)
        throw new Error('Invalid video identifier');
      client.setQueryData(
        ['upload-settings', scope, value.id, session.account?.id],
        value,
      );
      id.value = value.id;
      hydrated = value.id;
      await router.replace({
        path: '/studio/upload',
        query: { video: value.id },
      });
      ensure();
    } else {
      const value = unwrap(
        await editV1VideosVideoIdSettingsPut({
          path: { video_id: id.value },
          body,
          signal,
        }),
      );
      ensure();
      client.setQueryData(settingsKey.value, validSettings(value));
    }
    const videoId = id.value!;
    if (!accepted.value) {
      const source = file.value;
      if (!source) throw new Error('Source file required');
      const plan = validateUpload(
        unwrap(
          uploadId
            ? await refreshV1VideosVideoIdUploadUploadIdRefreshPost({
                path: { video_id: videoId, upload_id: uploadId },
                signal,
              })
            : await startV1VideosVideoIdUploadPost({
                path: { video_id: videoId },
                body: { size: source.size },
                signal,
              }),
        ),
        source.size,
      );
      ensure();
      uploadId = plan.id;
      phase.value = 'uploading';
      await transferParts(source, plan, signal, (value) => {
        if (scope === session.scope && version === generation && !disposed)
          sentBytes.value = value;
      });
      ensure();
      phase.value = 'confirming';
      const completed = unwrap(
        await completeV1VideosVideoIdUploadUploadIdCompletePost({
          path: { video_id: videoId, upload_id: plan.id },
          signal,
        }),
      );
      ensure();
      if (completed.status !== 'uploaded')
        throw new Error('Upload not confirmed');
      await settings.refetch();
      ensure();
      if (!accepted.value) throw new Error('Upload not confirmed');
    }
    phase.value = 'saving';
    await nextTick();
    ensure();
    await playlistEditor.value?.persist(videoId, signal);
    ensure();
    await permissionEditor.value?.persist(videoId, signal);
    ensure();
    await thumbnailEditor.value?.persist(videoId, signal);
    ensure();
    phase.value = 'publishing';
    const published = unwrap(
      await publishV1VideosVideoIdPublishPost({
        path: { video_id: videoId },
        signal,
      }),
    );
    ensure();
    validSettings(published);
    if (
      published.publication !== 'published' ||
      published.upload !== 'uploaded'
    )
      throw new Error('Publication not confirmed');
    client.setQueryData(settingsKey.value, published);
    saved.value = true;
    file.value = undefined;
    uploadId = undefined;
    if (fileInput.value) fileInput.value.value = '';
    void processing.refetch();
  },
});
async function save() {
  if (
    disposed ||
    !identityReady.value ||
    phase.value !== 'idle' ||
    saved.value ||
    denied.value
  )
    return;
  const version = generation,
    scope = session.scope;
  phase.value = 'saving';
  error.value = '';
  const valid = await validateText();
  if (disposed || version !== generation || scope !== session.scope) return;
  if (!valid) {
    phase.value = 'idle';
    step.value = 'information';
    await focusError();
    return;
  }
  if (!form.visibility) {
    phase.value = 'idle';
    fields.value.visibility = 'Выберите параметры доступа.';
    await focusError();
    return;
  }
  if (!accepted.value && !file.value) {
    phase.value = 'idle';
    error.value = 'Выберите исходный видеофайл.';
    return;
  }
  if (disposed || !session.account || denied.value) return;
  try {
    await mutation.mutateAsync();
  } catch (cause) {
    if (disposed || version !== generation || denied.value) return;
    if (
      cause instanceof ApiError &&
      (cause.status === 404 ||
        (cause.status === 403 && cause.code !== 'csrf_failed'))
    ) {
      loseAccess();
      return;
    }
    phase.value = 'idle';
    if (cause instanceof ApiError)
      for (const field of cause.fields) {
        const name = field.location.at(-1);
        if (name === 'title' || name === 'description' || name === 'visibility')
          fields.value[name] = 'Проверьте значение поля.';
      }
    error.value = apiMessage(cause);
    if (fields.value.title || fields.value.description)
      step.value = 'information';
    await focusError();
  } finally {
    if (!disposed && version === generation) phase.value = 'idle';
  }
}
function loseAccess() {
  denied.value = true;
  controller?.abort();
  file.value = undefined;
  form.title = '';
  form.description = '';
  form.visibility = '';
  error.value = '';
  client.removeQueries({ queryKey: settingsKey.value, exact: true });
  client.removeQueries({ queryKey: processingKey.value, exact: true });
}
function trapFocus(event: KeyboardEvent) {
  const controls = [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled):not([type=hidden]), textarea:not(:disabled), a[href]',
    ) ?? []),
  ].filter((item) => item.getClientRects().length);
  const first = controls[0],
    last = controls.at(-1);
  if (
    (event.shiftKey && document.activeElement === first) ||
    (!event.shiftKey && document.activeElement === last)
  ) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
}
async function close() {
  await router.push({
    path: '/studio/content/videos',
    query:
      settings.data.value?.publication === 'draft'
        ? { publication: 'draft' }
        : {},
  });
  await nextTick();
  document.querySelector<HTMLElement>('.studio-create')?.focus();
}
const previousTitle = document.title;
onMounted(() => {
  document.title = 'Загрузка видео — YouTube Studio';
  dialog.value?.showModal();
});
onBeforeUnmount(() => {
  disposed = true;
  generation++;
  controller?.abort();
  dialog.value?.close();
  file.value = undefined;
  form.title = '';
  form.description = '';
  form.visibility = '';
  mutation.reset();
  document.title = previousTitle;
});
</script>
<template>
  <dialog
    ref="dialog"
    class="upload-dialog"
    aria-labelledby="upload-dialog-title"
    @cancel.prevent="close"
    @keydown.esc.stop.prevent="close"
    @keydown.tab="trapFocus"
  >
    <header class="upload-dialog__header">
      <h2 id="upload-dialog-title">
        {{ step === 'choose' ? 'Загрузка видео' : form.title || 'Новое видео' }}
      </h2>
      <span v-if="saved" class="upload-dialog__saved">Сохранено</span
      ><button
        type="button"
        class="icon-button"
        aria-label="Закрыть загрузку видео"
        @click="close"
      >
        <AppIcon name="close" />
      </button>
    </header>
    <PageState v-if="invalidId || denied" title="Видео недоступно" />
    <PageState
      v-else-if="id && settings.isPending.value"
      title="Загружаем сведения о видео"
    />
    <PageState
      v-else-if="settings.isError.value"
      title="Не удалось загрузить сведения"
      :description="apiMessage(settings.error.value)"
      error
      @retry="settings.refetch()"
    />
    <PageState
      v-else-if="incompleteRestore"
      title="Передача файла не завершена"
      description="Исходный файл не сохраняется в браузере. Начните новую загрузку, чтобы передать видео целиком."
      ><RouterLink to="/studio/upload" class="button upload-white-button"
        >Начать новую загрузку</RouterLink
      ></PageState
    >
    <template v-else>
      <input
        id="video-file"
        ref="fileInput"
        type="file"
        hidden
        class="upload-file-input"
        aria-label="Исходный видеофайл"
        :disabled="phase !== 'idle'"
        @change="choose(($event.target as HTMLInputElement).files)"
      />
      <section
        v-if="step === 'choose'"
        class="upload-drop"
        :class="{ 'upload-drop--over': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave="dragOver = false"
        @drop.prevent="drop"
      >
        <div class="upload-drop__icon"><AppIcon name="upload" /></div>
        <p>
          Перетащите файл сюда или нажмите кнопку ниже, чтобы выбрать его на
          компьютере.
        </p>
        <button
          type="button"
          class="button upload-white-button"
          @click="fileInput?.click()"
        >
          Выбрать файл
        </button>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <p class="upload-drop__limits">
          До 5 ГБ и 60 минут. Поддерживаются видео без звука и ниже 360p.<br />Формат
          и содержимое файла проверяет сервер после передачи.
        </p>
      </section>
      <form
        v-else
        class="upload-form"
        novalidate
        :aria-busy="phase !== 'idle'"
        @submit.prevent="step === 'information' ? next() : save()"
      >
        <ol class="upload-steps" aria-label="Этапы загрузки">
          <li :aria-current="step === 'information' ? 'step' : undefined">
            Информация<span
              ><AppIcon v-if="step === 'access'" name="check"
            /></span>
          </li>
          <li :aria-current="step === 'access' ? 'step' : undefined">
            Доступ<span />
          </li>
        </ol>
        <div class="upload-form__scroll">
          <div class="upload-form__columns">
            <section v-show="step === 'information'" class="upload-information">
              <h3>Информация</h3>
              <label class="upload-field" for="video-title"
                ><span>Название (обязательное поле)</span
                ><textarea
                  id="video-title"
                  v-model="form.title"
                  rows="3"
                  :disabled="phase !== 'idle' || saved"
                  :aria-invalid="!!fields.title || titleLength > 100"
                  aria-describedby="video-title-hint"
                  placeholder="Введите название"
                  @input="delete fields.title"
                /><small
                  id="video-title-hint"
                  :class="{ field__error: fields.title || titleLength > 100 }"
                  >{{ fields.title || `${titleLength}/100` }}</small
                ></label
              >
              <label class="upload-field" for="video-description"
                ><span>Описание</span
                ><textarea
                  id="video-description"
                  v-model="form.description"
                  rows="6"
                  :disabled="phase !== 'idle' || saved"
                  :aria-invalid="
                    !!fields.description || descriptionLength > 5000
                  "
                  aria-describedby="video-description-hint"
                  placeholder="Расскажите, о чём ваше видео"
                  @input="delete fields.description"
                /><small
                  id="video-description-hint"
                  :class="{
                    field__error:
                      fields.description || descriptionLength > 5000,
                  }"
                  >{{
                    fields.description || `${descriptionLength}/5000`
                  }}</small
                ></label
              >
              <VideoThumbnail
                ref="thumbnailEditor"
                :video-id="id"
                :disabled="phase !== 'idle'"
                @access-lost="loseAccess"
              />
              <VideoPlaylists
                ref="playlistEditor"
                :video-id="id"
                :disabled="phase !== 'idle'"
                @access-lost="loseAccess"
                ><template #create="{ refresh, disabled: creationDisabled }"
                  ><CreatePlaylist
                    compact
                    :disabled="creationDisabled"
                    @created="refresh" /></template
              ></VideoPlaylists>
              <p class="upload-help">
                До 5 ГБ и 60 минут. Видео без звука и ниже 360p разрешены.
                Допустимость формата проверяет сервер.
              </p>
              <button
                v-if="!id && !saved"
                type="button"
                class="button button--secondary"
                :disabled="phase !== 'idle'"
                @click="fileInput?.click()"
              >
                {{ file ? 'Выбрать другой файл' : 'Выбрать исходный файл' }}
              </button>
            </section>
            <section v-show="step === 'access'" class="upload-access">
              <h3>Параметры доступа</h3>
              <p>Укажите, кто сможет смотреть видео.</p>
              <fieldset
                id="video-visibility"
                class="upload-access__options"
                :disabled="phase !== 'idle' || saved"
                :aria-invalid="!!fields.visibility"
                aria-describedby="video-visibility-error"
              >
                <legend>Сохранить или опубликовать</legend>
                <label
                  ><input
                    id="video-private"
                    v-model="form.visibility"
                    type="radio"
                    value="private"
                    name="visibility"
                    @change="delete fields.visibility"
                  /><span
                    >Ограниченный доступ<small
                      >Видео доступно только вам и пользователям, которым вы
                      выдали доступ.</small
                    ></span
                  ></label
                >
                <label
                  ><input
                    id="video-public"
                    v-model="form.visibility"
                    type="radio"
                    value="public"
                    name="visibility"
                    @change="delete fields.visibility"
                  /><span
                    >Открытый доступ<small
                      >Видео доступно всем пользователям.</small
                    ></span
                  ></label
                >
              </fieldset>
              <p
                v-if="fields.visibility"
                id="video-visibility-error"
                class="field__error"
              >
                {{ fields.visibility }}
              </p>
              <VideoPermissions
                v-show="form.visibility === 'private'"
                ref="permissionEditor"
                :video-id="id"
                :disabled="phase !== 'idle'"
                @access-lost="loseAccess"
              />
              <p v-if="saved" class="upload-result" role="status">
                {{
                  canView
                    ? 'Видео сохранено и доступно для просмотра.'
                    : settings.data.value?.moderation === 'hidden'
                      ? 'Видео скрыто. Просмотр недоступен.'
                      : processing.isError.value
                        ? 'Публикация сохранена. Не удалось проверить готовность видео.'
                        : processingData?.processing === 'failed'
                          ? 'Публикация сохранена. Обработка завершилась ошибкой; просмотр недоступен.'
                          : 'Публикация сохранена. Ожидаем готовый вариант видео.'
                }}
              </p>
            </section>
            <aside class="upload-summary" aria-label="Состояние видео">
              <div class="upload-summary__preview">
                <AppIcon v-if="!accepted" name="video" /><span v-else>{{
                  processingLabel
                }}</span>
              </div>
              <div class="upload-summary__body">
                <span>Название файла</span>
                <p>
                  {{
                    file?.name ||
                    (accepted
                      ? 'Исходник принят сервером'
                      : 'Выберите исходный файл')
                  }}
                </p>
                <template v-if="id"
                  ><span>Сведения о загрузке</span
                  ><RouterLink
                    :to="{ path: '/studio/upload', query: { video: id } }"
                    class="text-link"
                    >Вернуться к этому видео</RouterLink
                  ></template
                ><RouterLink
                  v-if="id"
                  :to="`/studio/video/${id}/edit`"
                  class="text-link upload-settings-link"
                  >Настройки видео</RouterLink
                ><RouterLink
                  v-if="canView"
                  :to="{ path: '/watch', query: { v: id } }"
                  class="text-link upload-watch-link"
                  >Смотреть видео</RouterLink
                >
              </div>
            </aside>
          </div>
          <section
            v-if="accepted"
            class="upload-processing"
            aria-label="Обработка видео"
            :aria-busy="processing.isFetching.value"
          >
            <h4>{{ processingLabel }}</h4>
            <p v-if="processing.isError.value" class="form-error" role="alert">
              Не удалось обновить состояние обработки.
              <button
                type="button"
                class="text-link"
                :disabled="processing.isFetching.value"
                @click="processing.refetch()"
              >
                {{ processing.isFetching.value ? 'Проверяем…' : 'Повторить' }}
              </button>
            </p>
            <p
              v-else-if="processingData?.processing === 'failed'"
              class="form-error"
            >
              {{ processingFailure(processingData?.error_code) }} Просмотр
              недоступен.
            </p>
            <ul v-if="processingData?.qualities.length">
              <li
                v-for="quality in processingData.qualities"
                :key="`${quality.quality}:${quality.range}`"
              >
                {{ quality.quality }}p{{
                  quality.range.toUpperCase() === 'SDR' ? '' : ' HDR'
                }}
                —
                {{
                  quality.status === 'ready'
                    ? 'Готово'
                    : quality.status === 'failed'
                      ? 'Не удалось подготовить'
                      : quality.status === 'pending'
                        ? 'Ожидает обработки'
                        : quality.status === 'processing'
                          ? 'Обрабатывается'
                          : 'Состояние неизвестно'
                }}
              </li>
            </ul>
            <p v-if="processingData?.processing === 'partial'">
              Доступны готовые качества. Часть вариантов не удалось подготовить.
            </p>
          </section>
          <p v-if="error" class="form-error upload-error" role="alert">
            {{ error }}
          </p>
        </div>
        <footer class="upload-dialog__footer">
          <div class="upload-progress" role="status">
            <AppIcon name="upload" /><span v-if="phase === 'uploading'"
              >Передача файла: {{ progress }}%<progress
                :value="sentBytes"
                :max="totalBytes"
                aria-label="Передано байт" /></span
            ><span v-else-if="phase === 'confirming'"
              >Файл передан. Подтверждаем загрузку…</span
            ><span v-else-if="phase === 'saving'">Сохраняем сведения…</span
            ><span v-else-if="phase === 'publishing'"
              >Подтверждаем публикацию…</span
            ><span v-else-if="accepted"
              >Загрузка видео завершена. {{ processingLabel }}.</span
            ><span v-else-if="id && error">{{
              sentBytes >= totalBytes
                ? 'Файл передан. Приём сервером не подтверждён.'
                : 'Передача прервана. Повторите загрузку.'
            }}</span
            ><span v-else>Файл выбран. Загрузка ещё не началась.</span>
          </div>
          <div class="upload-dialog__buttons">
            <button
              v-if="step === 'access'"
              type="button"
              class="button button--secondary"
              :disabled="phase !== 'idle'"
              @click="step = 'information'"
            >
              Назад</button
            ><button
              v-if="saved && step === 'access'"
              type="button"
              class="button upload-white-button"
              @click="close"
            >
              Закрыть</button
            ><button
              v-else
              type="submit"
              class="button upload-white-button"
              :disabled="
                phase !== 'idle' || (step === 'information' && !textValid)
              "
            >
              {{
                step === 'information'
                  ? 'Далее'
                  : error
                    ? 'Повторить'
                    : 'Сохранить'
              }}
            </button>
          </div>
        </footer>
      </form>
    </template>
  </dialog>
</template>
<style scoped lang="scss">
.upload-dialog {
  --upload-width: 960px;
  --upload-height: 840px;
  --upload-padding: 48px;
  background: var(--studio-dialog);
  color: var(--color-text);
  border: 0;
  border-radius: 24px;
  padding: 0;
  width: var(--upload-width);
  max-width: calc(100vw - 24px);
  height: var(--upload-height);
  max-height: calc(100dvh - 24px);
  overflow: hidden;
  &::backdrop {
    background: #0009;
  }
  &__header {
    height: 60px;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 24px;
    border-bottom: 1px solid var(--studio-input-border);
    h2 {
      margin: 0;
      font-size: 20px;
      line-height: 28px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
  }
  &__saved {
    color: var(--color-muted);
    font-size: 12px;
  }
  &__footer {
    min-height: 68px;
    border-top: 1px solid var(--studio-input-border);
    padding: 12px 24px;
    display: flex;
    gap: 16px;
    align-items: center;
    justify-content: space-between;
  }
  &__buttons {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
}
.upload-white-button {
  background: var(--color-text);
  color: var(--color-background);
}
.upload-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
}
.upload-drop {
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  height: calc(100% - 60px);
  padding: 48px 24px 120px;
  text-align: center;
  position: relative;
  p {
    font-size: 16px;
    line-height: 24px;
    margin: 24px 0;
  }
  &__icon {
    display: grid;
    place-items: center;
    width: 136px;
    height: 136px;
    border-radius: 50%;
    background: var(--color-hover);
    svg {
      width: 56px;
      height: 56px;
      color: var(--color-muted);
    }
  }
  &--over {
    outline: 2px solid var(--color-accent);
    outline-offset: -8px;
  }
  .upload-drop__limits {
    position: absolute;
    bottom: 24px;
    margin: 0;
    color: var(--color-muted);
    font-size: 12px;
    line-height: 20px;
    padding: 0 24px;
  }
}
.upload-form {
  display: flex;
  flex-direction: column;
  height: calc(100% - 60px);
  &__scroll {
    overflow-y: auto;
    min-height: 0;
    flex: 1;
    padding: 20px var(--upload-padding) 32px;
  }
  &__columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 304px;
    gap: 24px;
  }
  h3 {
    font-size: 32px;
    line-height: 40px;
    margin: 0 0 20px;
  }
}
.upload-steps {
  display: flex;
  justify-content: space-between;
  margin: 0;
  padding: 16px 88px 12px;
  list-style: none;
  li {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    font-size: 16px;
    font-weight: 500;
    position: relative;
    color: var(--color-muted);
    &[aria-current] {
      color: var(--color-text);
    }
    &::after {
      content: '';
      position: absolute;
      height: 2px;
      top: 34px;
      left: 50%;
      width: 100%;
      background: var(--color-muted);
    }
    &:last-child::after {
      display: none;
    }
    > span {
      z-index: 1;
      width: 20px;
      height: 20px;
      border: 4px solid var(--color-muted);
      border-radius: 50%;
      background: var(--studio-dialog);
      color: var(--color-text);
      display: grid;
      place-items: center;
      font-size: 12px;
    }
    &[aria-current] > span {
      border-color: var(--color-text);
    }
  }
}
.upload-steps svg {
  width: 12px;
  height: 12px;
}
.upload-field {
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
  border: 1px solid var(--studio-input-border);
  border-radius: 8px;
  margin-bottom: 24px;
  > span {
    font-size: 12px;
    line-height: 16px;
    color: var(--color-muted);
  }
  textarea {
    background: transparent;
    border: 0;
    outline: 0;
    resize: vertical;
    min-height: 64px;
    font-size: 16px;
    line-height: 24px;
    padding: 4px 0;
  }
  small {
    color: var(--color-muted);
    text-align: right;
    font-size: 12px;
    &.field__error {
      color: var(--color-error);
    }
  }
  &:focus-within {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 1px var(--color-accent);
  }
}
.upload-help {
  color: var(--color-muted);
  line-height: 20px;
}
.upload-summary {
  align-self: start;
  background: var(--color-hover);
  border-radius: 8px;
  overflow: hidden;
  margin-top: 60px;
  &__preview {
    aspect-ratio: 16 / 9;
    background: var(--studio-input-border);
    display: grid;
    place-items: center;
    padding: 16px;
    text-align: center;
    color: var(--color-muted);
    svg {
      width: 48px;
      height: 48px;
    }
  }
  &__body {
    padding: 16px;
    overflow-wrap: anywhere;
    > span {
      color: var(--color-muted);
      font-size: 12px;
    }
    p {
      margin: 4px 0 16px;
    }
    a {
      display: block;
      margin-top: 6px;
    }
  }
}
.upload-access {
  > p {
    line-height: 20px;
  }
  &__options {
    border: 1px solid var(--color-text);
    border-radius: 16px;
    padding: 20px;
    margin: 24px 0 0;
    legend {
      padding: 0 4px;
      font-weight: 500;
    }
    label {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      margin: 8px 0 24px;
      font-size: 16px;
      line-height: 24px;
    }
    input {
      accent-color: var(--color-text);
      width: 20px;
      height: 20px;
      flex-shrink: 0;
    }
    small {
      display: block;
      font-size: 14px;
      color: var(--color-muted);
      line-height: 20px;
      margin-top: 4px;
    }
  }
}
.upload-processing {
  padding: 16px 0;
  h4 {
    font-size: 16px;
    margin: 0 0 12px;
  }
  ul {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 24px;
    list-style: none;
    padding: 0;
    font-size: 14px;
  }
}
.upload-progress {
  display: flex;
  gap: 16px;
  align-items: center;
  font-size: 14px;
  line-height: 20px;
  color: var(--color-muted);
  svg {
    flex-shrink: 0;
    color: var(--color-text);
  }
  progress {
    display: block;
    width: 100%;
    height: 4px;
    margin-top: 4px;
    accent-color: var(--color-text);
  }
}
@media (max-width: 767px) {
  .upload-dialog {
    --upload-padding: 20px;
    border-radius: 16px;
    &__header {
      padding: 0 12px 0 20px;
    }
    &__footer {
      padding: 12px 16px;
      flex-wrap: wrap;
    }
    &__buttons {
      margin-left: auto;
    }
  }
  .upload-form {
    &__columns {
      grid-template-columns: 1fr;
    }
    h3 {
      font-size: 24px;
      line-height: 32px;
    }
  }
  .upload-summary {
    margin-top: 0;
  }
  .upload-steps {
    padding-left: 24px;
    padding-right: 24px;
  }
  .upload-drop {
    padding-top: 24px;
    padding-bottom: 140px;
    &__icon {
      width: 96px;
      height: 96px;
    }
    p {
      font-size: 14px;
      line-height: 20px;
    }
  }
  .upload-progress {
    font-size: 12px;
  }
}
</style>
