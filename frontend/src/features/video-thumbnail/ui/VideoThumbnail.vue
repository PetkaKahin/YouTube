<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  optionsV1VideosVideoIdThumbnailGet,
  selectV1VideosVideoIdThumbnailPut,
  startV1VideosVideoIdThumbnailUploadPost,
  refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost,
  completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost,
  unwrap,
  type Identifier,
  type ThumbnailResponse,
} from '../../../shared/api';
import { mediaUrl } from '../../../shared/lib/media';
import { transferParts, validateUpload } from '../../../shared/lib/uploads';
import { useOperations } from '../../../shared/lib/operations';
import { AppIcon, StudioDialog } from '../../../shared/ui';

const props = defineProps<{ videoId?: Identifier; disabled?: boolean }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient();
const input = ref<HTMLInputElement>(),
  file = shallowRef<File>(),
  open = ref(false),
  pending = ref(false),
  error = ref(''),
  notice = ref(''),
  denied = ref(false),
  imageFailed = ref(false),
  loaded = ref(0);
let uploadId: Identifier | undefined;
const begin = useOperations(() => `${session.scope}:${props.videoId ?? 'new'}`);
const key = computed(() => ['video-thumbnail', session.scope, props.videoId]);
function validate(value: ThumbnailResponse) {
  const prefix = `/api/v1/videos/${props.videoId}/thumbnail/`;
  if (value.current_url) mediaUrl(value.current_url, prefix);
  for (const url of value.candidates) {
    mediaUrl(url, prefix);
    if (!/\/generated\/[012]$/.test(url))
      throw new Error('Invalid thumbnail candidate');
  }
  return value;
}
const query = useQuery({
  queryKey: key,
  enabled: computed(() => !!props.videoId && !denied.value),
  gcTime: 0,
  queryFn: async ({ signal }) =>
    validate(
      unwrap(
        await optionsV1VideosVideoIdThumbnailGet({
          path: { video_id: props.videoId! },
          signal,
        }),
      ),
    ),
  refetchInterval: (q) =>
    q.state.error ||
    (q.state.data &&
      ['ready', 'failed', 'empty'].includes(q.state.data.status) &&
      (q.state.data.custom_status === null ||
        ['ready', 'failed', 'empty', 'expired'].includes(
          q.state.data.custom_status,
        )))
      ? false
      : 2000,
  retry: false,
});
const imageKey = computed(
  () =>
    `${query.data.value?.selected}:${query.data.value?.slot}:${query.data.value?.custom_status}`,
);
watch(imageKey, () => {
  imageFailed.value = false;
});
function reset() {
  open.value = false;
  pending.value = false;
  file.value = undefined;
  uploadId = undefined;
  loaded.value = 0;
  error.value = '';
  notice.value = '';
  denied.value = false;
  imageFailed.value = false;
  if (input.value) input.value.value = '';
}
watch(
  () => props.videoId,
  (_, previous) => {
    if (previous) reset();
  },
);
watch(() => session.scope, reset);
function failure(cause: unknown) {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    reset();
    denied.value = true;
    client.removeQueries({ queryKey: key.value, exact: true });
    emit('accessLost');
  }
}
watch(query.error, failure);
async function persist(target: Identifier, signal?: AbortSignal) {
  if (!file.value) return;
  if (pending.value) throw new Error('Thumbnail is already saving');
  const source = file.value;
  pending.value = true;
  error.value = '';
  const work = begin(signal);
  try {
    const plan = validateUpload(
      unwrap(
        uploadId
          ? await refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost({
              path: { video_id: target, upload_id: uploadId },
              signal: work.signal,
            })
          : await startV1VideosVideoIdThumbnailUploadPost({
              path: { video_id: target },
              body: { size: source.size },
              signal: work.signal,
            }),
      ),
      source.size,
    );
    work.check();
    uploadId = plan.id;
    await transferParts(source, plan, work.signal, (value) => {
      if (work.current()) loaded.value = value;
    });
    work.check();
    const result = unwrap(
      await completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost({
        path: { video_id: target, upload_id: plan.id },
        signal: work.signal,
      }),
    );
    work.check();
    if (result.status !== 'uploaded')
      throw new Error('Thumbnail upload not confirmed');
    file.value = undefined;
    uploadId = undefined;
    notice.value = 'Файл превью передан. Обрабатываем изображение.';
    if (input.value) input.value.value = '';
    await query.refetch();
    work.check();
  } catch (cause) {
    if (work.current()) {
      error.value = apiMessage(cause);
      if (cause instanceof ApiError && cause.code === 'upload_expired')
        uploadId = undefined;
    }
    failure(cause);
    throw cause;
  } finally {
    work.finish();
    if (work.current()) pending.value = false;
  }
}
async function chooseFile(files: FileList | null) {
  if (pending.value || props.disabled || !files?.length) return;
  const selected = files[0]!;
  if (
    files.length !== 1 ||
    !selected.size ||
    selected.size > 10 * 1024 * 1024
  ) {
    error.value = 'Выберите одно изображение размером до 10 МиБ.';
    return;
  }
  if (file.value && uploadId) {
    error.value = 'Сначала повторите передачу выбранного изображения.';
    return;
  }
  file.value = selected;
  error.value = '';
  loaded.value = 0;
  notice.value = 'Превью будет передано при сохранении видео';
  if (props.videoId)
    try {
      await persist(props.videoId);
    } catch (cause) {
      if (props.videoId) error.value = apiMessage(cause);
    }
}
async function select(url: string) {
  if (!props.videoId || pending.value) return;
  const slot = Number(url.at(-1));
  pending.value = true;
  error.value = '';
  const work = begin();
  try {
    const result = validate(
      unwrap(
        await selectV1VideosVideoIdThumbnailPut({
          path: { video_id: props.videoId },
          body: { slot },
          signal: work.signal,
        }),
      ),
    );
    work.check();
    client.setQueryData(key.value, result);
    file.value = undefined;
    uploadId = undefined;
    if (input.value) input.value.value = '';
    notice.value = 'Превью сохранено';
    open.value = false;
  } catch (cause) {
    if (work.current()) error.value = apiMessage(cause);
    failure(cause);
  } finally {
    work.finish();
    if (work.current()) pending.value = false;
  }
}
async function retry() {
  imageFailed.value = false;
  if (props.videoId && file.value)
    try {
      await persist(props.videoId);
    } catch (cause) {
      error.value = apiMessage(cause);
    }
  else await query.refetch();
}
defineExpose({ persist });
onBeforeUnmount(reset);
</script>
<template>
  <section class="video-thumbnail">
    <h4>Значок</h4>
    <p>Выберите кадр из видео или загрузите своё изображение.</p>
    <div class="thumbnail-actions">
      <input
        id="thumbnail-file"
        ref="input"
        type="file"
        hidden
        @change="chooseFile(($event.target as HTMLInputElement).files)"
      /><button
        type="button"
        :disabled="disabled || pending || denied"
        @click="input?.click()"
      >
        <AppIcon name="upload" />Загрузить файл</button
      ><button
        type="button"
        :disabled="
          disabled || pending || denied || !query.data.value?.candidates.length
        "
        @click="open = true"
      >
        <AppIcon name="video" />Выбрать из видео</button
      ><img
        v-if="query.data.value?.current_url && !denied && !imageFailed"
        :key="imageKey"
        :src="query.data.value.current_url"
        alt="Текущее превью"
        @error="
          imageFailed = true;
          query.refetch();
        "
      />
    </div>
    <p
      v-if="
        !videoId ||
        (query.data.value &&
          !query.data.value.candidates.length &&
          query.data.value.status !== 'failed')
      "
      class="thumbnail-hint"
    >
      Кадры появятся после передачи и обработки видео.
    </p>
    <p class="thumbnail-hint">
      До 10 МиБ и 40 мегапикселей. Формат проверяет сервер; изображение не
      должно быть анимированным.
    </p>
    <p v-if="file" class="thumbnail-file">
      {{ file.name }}
      <span v-if="pending"
        >— передано {{ Math.floor((loaded / file.size) * 100) }}%</span
      >
    </p>
    <p
      v-if="notice && !query.data.value?.error_code"
      class="thumbnail-hint"
      role="status"
    >
      {{ notice }}
    </p>
    <p
      v-if="
        query.data.value?.error_code ||
        query.data.value?.custom_status === 'failed'
      "
      class="form-error"
      role="alert"
    >
      Не удалось подготовить новое превью. Предыдущий выбор сохранён.
    </p>
    <p
      v-if="query.data.value?.status === 'failed'"
      class="form-error"
      role="alert"
    >
      Не удалось подготовить кадры из видео. Можно загрузить своё изображение.
    </p>
    <p
      v-if="error || query.isError.value || imageFailed"
      class="form-error"
      role="alert"
    >
      {{
        error ||
        (imageFailed
          ? 'Не удалось загрузить изображение.'
          : apiMessage(query.error.value))
      }}
      <button
        type="button"
        class="text-link"
        :disabled="pending || disabled"
        @click="retry"
      >
        Повторить
      </button>
    </p>
    <StudioDialog
      :open="open"
      :busy="pending"
      title="Выберите превью из видео"
      @close="open = false"
      ><div class="thumbnail-candidates">
        <button
          v-for="url in query.data.value?.candidates"
          :key="url"
          type="button"
          :disabled="pending"
          :aria-pressed="
            query.data.value?.selected === 'generated' &&
            query.data.value.slot === Number(url.at(-1))
          "
          :aria-label="`Выбрать кадр ${Number(url.at(-1)) + 1}`"
          @click="select(url)"
        >
          <img
            :src="url"
            alt=""
            @error="
              imageFailed = true;
              query.refetch();
            "
          /><AppIcon
            v-if="
              query.data.value?.selected === 'generated' &&
              query.data.value.slot === Number(url.at(-1))
            "
            name="check"
          />
        </button>
      </div>
      <p v-if="error" class="form-error" role="alert">
        {{ error }}
      </p></StudioDialog
    >
  </section>
</template>
<style scoped lang="scss">
.video-thumbnail {
  margin: 24px 0;
  h4 {
    font-size: 16px;
    margin: 0 0 8px;
  }
  > p {
    font-size: 14px;
    line-height: 20px;
  }
}
.thumbnail-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 8px;
  button {
    width: 150px;
    min-height: 88px;
    border: 1px dashed var(--studio-input-border);
    border-radius: 8px;
    background: transparent;
    display: flex;
    flex-direction: column;
    gap: 8px;
    justify-content: center;
    align-items: center;
    cursor: pointer;
    font-size: 12px;
  }
  > img {
    width: 150px;
    height: 88px;
    object-fit: contain;
    border-radius: 8px;
    background: var(--color-background);
  }
}
.thumbnail-hint {
  color: var(--color-muted);
  font-size: 12px !important;
}
.thumbnail-file {
  overflow-wrap: anywhere;
}
.thumbnail-candidates {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  button {
    position: relative;
    flex: 1 1 140px;
    padding: 0;
    border: 2px solid transparent;
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-background);
    cursor: pointer;
    &[aria-pressed='true'] {
      border-color: var(--color-text);
    }
    img {
      display: block;
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: contain;
    }
    svg {
      position: absolute;
      top: 4px;
      right: 4px;
      border-radius: 50%;
      padding: 2px;
      background: var(--color-background);
    }
  }
}
</style>
