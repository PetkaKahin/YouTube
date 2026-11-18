<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/vue-query';
import { StudioShell } from '../../../widgets/studio-shell';
import { VideoThumbnail } from '../../../features/video-thumbnail';
import { VideoPlaylists } from '../../../features/video-playlists';
import { VideoPermissions } from '../../../features/video-permissions';
import { CreatePlaylist } from '../../../features/create-playlist';
import { useSession } from '../../../entities/session';
import { videoState, canWatchOwnedVideo } from '../../../entities/video';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  settingsV1VideosVideoIdSettingsGet,
  editV1VideosVideoIdSettingsPut,
  unwrap,
  type VideoSettingsResponse,
  type VideoSettingsListResponse,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';
import { AppIcon, PageState } from '../../../shared/ui';

const session = useSession(),
  route = useRoute(),
  client = useQueryClient();
const id = computed(() =>
  isIdentifier(route.params.videoId) ? route.params.videoId : undefined,
);
const key = computed(() => ['video-settings', session.scope, id.value]);
const denied = ref(false),
  pending = ref(false),
  error = ref(''),
  notice = ref(''),
  fields = ref<Record<string, string>>({});
const form = reactive({
  title: '',
  description: '',
  visibility: 'private' as 'public' | 'private',
});
let hydrated = false;
const begin = useOperations(
  () => `${session.scope}:${id.value}:${denied.value}`,
);
function validateResponse(value: VideoSettingsResponse) {
  if (
    value.id !== id.value ||
    value.owner_id !== session.account?.id ||
    !['public', 'private'].includes(value.visibility)
  )
    throw new Error('Invalid video settings');
  return value;
}
const query = useQuery({
  queryKey: key,
  enabled: computed(
    () => !!session.account && !!id.value && !session.changing && !denied.value,
  ),
  gcTime: 0,
  queryFn: async ({ signal }) =>
    validateResponse(
      unwrap(
        await settingsV1VideosVideoIdSettingsGet({
          path: { video_id: id.value! },
          signal,
        }),
      ),
    ),
  retry: false,
});
const dirty = computed(
  () =>
    !!query.data.value &&
    (form.title.trim() !== query.data.value.title ||
      form.description !== query.data.value.description ||
      form.visibility !== query.data.value.visibility),
);
function fill(value: VideoSettingsResponse) {
  form.title = value.title;
  form.description = value.description;
  form.visibility = value.visibility as 'public' | 'private';
}
function reset() {
  hydrated = false;
  form.title = '';
  form.description = '';
  form.visibility = 'private';
  error.value = '';
  notice.value = '';
  fields.value = {};
  pending.value = false;
  denied.value = false;
}
watch([id, () => session.scope], reset, { flush: 'sync' });
watch(
  query.data,
  (value) => {
    if (value && !hydrated) {
      fill(value);
      hydrated = true;
    }
  },
  { immediate: true },
);
watch(dirty, (value) => {
  if (value) notice.value = '';
});
function loseAccess() {
  denied.value = true;
  form.title = '';
  form.description = '';
  error.value = '';
  notice.value = '';
  fields.value = {};
  client.removeQueries({ queryKey: key.value, exact: true });
}
watch(query.error, (cause) => {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status))
    loseAccess();
});
const titleLength = computed(() => [...form.title.trim()].length),
  descriptionLength = computed(() => [...form.description].length);
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const work = begin();
    try {
      const value = unwrap(
        await editV1VideosVideoIdSettingsPut({
          path: { video_id: id.value! },
          body: {
            title: form.title.trim(),
            description: form.description,
            visibility: form.visibility,
          },
          signal: work.signal,
        }),
      );
      work.check();
      const confirmed = validateResponse(value);
      client.setQueryData(key.value, confirmed);
      client.setQueriesData<InfiniteData<VideoSettingsListResponse>>(
        { queryKey: ['studio-videos', session.scope] },
        (old) =>
          old && {
            ...old,
            pages: old.pages.map((page) => ({
              ...page,
              items: page.items.map((item) =>
                item.id === confirmed.id ? confirmed : item,
              ),
            })),
          },
      );
      fill(confirmed);
      notice.value = 'Изменения сохранены';
    } finally {
      work.finish();
    }
  },
});
async function focusError() {
  await nextTick();
  const name = ['title', 'description', 'visibility'].find(
    (field) => fields.value[field],
  );
  if (name) document.getElementById(`settings-${name}`)?.focus();
}
async function save() {
  if (pending.value || !dirty.value || denied.value || !id.value) return;
  pending.value = true;
  error.value = '';
  notice.value = '';
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
  if (Object.keys(fields.value).length) {
    pending.value = false;
    await focusError();
    return;
  }
  const identity = `${session.scope}:${id.value}`;
  try {
    await mutation.mutateAsync();
  } catch (cause) {
    if (identity !== `${session.scope}:${id.value}` || denied.value) return;
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      loseAccess();
      return;
    }
    if (cause instanceof ApiError)
      for (const field of cause.fields) {
        const name = field.location.at(-1);
        if (
          typeof name === 'string' &&
          ['title', 'description', 'visibility'].includes(name)
        )
          fields.value[name] = 'Проверьте значение поля.';
      }
    error.value = apiMessage(cause);
    await focusError();
  } finally {
    if (identity === `${session.scope}:${id.value}`) pending.value = false;
  }
}
function discard() {
  if (!query.data.value || pending.value) return;
  fill(query.data.value);
  fields.value = {};
  error.value = '';
  notice.value = '';
  document.getElementById('settings-title')?.focus();
}
const listTo = computed(() => ({
  path: '/studio/content/videos',
  query:
    query.data.value?.publication === 'draft' ? { publication: 'draft' } : {},
}));
const previousTitle = document.title;
document.title = 'Сведения о видео — YouTube Studio';
onBeforeUnmount(() => {
  document.title = previousTitle;
  reset();
  mutation.reset();
  const old = new Set(
    client.getQueryCache().findAll({ queryKey: key.value, exact: true }),
  );
  const filter = {
    predicate: (item: Parameters<typeof old.has>[0]) => old.has(item),
  };
  void client.cancelQueries(filter).then(() => client.removeQueries(filter));
});
</script>
<template>
  <StudioShell
    ><main id="main-content" class="video-settings">
      <RouterLink :to="listTo" class="video-settings__back"
        ><AppIcon name="back" />Контент на канале</RouterLink
      >
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
      <PageState
        v-else-if="!session.account"
        title="Войдите, чтобы изменить видео"
        ><RouterLink
          :to="{ path: '/login', query: { returnTo: route.fullPath } }"
          class="button button--primary"
          >Войти</RouterLink
        ></PageState
      >
      <PageState v-else-if="!id || denied" title="Видео недоступно" />
      <PageState
        v-else-if="query.isPending.value"
        title="Загружаем сведения о видео"
      />
      <PageState
        v-else-if="query.isError.value"
        title="Не удалось загрузить сведения"
        :description="apiMessage(query.error.value)"
        error
        @retry="query.refetch()"
      />
      <form
        v-else-if="query.data.value"
        :key="`${session.scope}:${id}`"
        class="video-settings__form"
        novalidate
        :aria-busy="pending"
        @submit.prevent="save"
      >
        <header class="video-settings__heading">
          <div>
            <h1>Сведения о видео</h1>
            <span v-if="dirty" class="settings-unsaved"
              >Есть несохранённые изменения</span
            ><span v-else-if="notice" class="settings-notice" role="status">{{
              notice
            }}</span>
          </div>
          <div class="video-settings__actions">
            <button
              type="button"
              class="button button--secondary"
              :disabled="!dirty || pending"
              @click="discard"
            >
              Отменить изменения</button
            ><button
              type="submit"
              class="button button--primary settings-save"
              :disabled="!dirty || pending"
            >
              {{ pending ? 'Сохраняем…' : 'Сохранить' }}
            </button>
          </div>
        </header>
        <div class="video-settings__columns">
          <section>
            <label class="settings-field" for="settings-title"
              ><span>Название (обязательное поле)</span
              ><textarea
                id="settings-title"
                v-model="form.title"
                rows="3"
                :disabled="pending"
                :aria-invalid="!!fields.title || titleLength > 100"
                aria-describedby="settings-title-hint"
              /><small
                id="settings-title-hint"
                :class="{ field__error: fields.title || titleLength > 100 }"
                >{{ fields.title || `${titleLength}/100` }}</small
              ></label
            >
            <label class="settings-field" for="settings-description"
              ><span>Описание</span
              ><textarea
                id="settings-description"
                v-model="form.description"
                rows="6"
                :disabled="pending"
                :aria-invalid="!!fields.description || descriptionLength > 5000"
                aria-describedby="settings-description-hint"
              /><small
                id="settings-description-hint"
                :class="{
                  field__error: fields.description || descriptionLength > 5000,
                }"
                >{{ fields.description || `${descriptionLength}/5000` }}</small
              ></label
            >
            <VideoThumbnail
              :video-id="id"
              :disabled="pending"
              @access-lost="loseAccess"
            />
            <VideoPlaylists
              :video-id="id"
              :disabled="pending"
              @access-lost="loseAccess"
              ><template #create="{ refresh, disabled: creationDisabled }"
                ><CreatePlaylist
                  compact
                  :disabled="creationDisabled"
                  @created="refresh" /></template
            ></VideoPlaylists>
            <label class="settings-field" for="settings-visibility"
              ><span>Параметры доступа</span
              ><select
                id="settings-visibility"
                v-model="form.visibility"
                :disabled="pending"
              >
                <option value="private">Ограниченный доступ</option>
                <option value="public">Открытый доступ</option></select
              ><small v-if="fields.visibility" class="field__error">{{
                fields.visibility
              }}</small></label
            >
            <VideoPermissions
              v-show="form.visibility === 'private'"
              :video-id="id"
              :disabled="pending"
              @access-lost="loseAccess"
            />
          </section>
          <aside class="settings-summary">
            <h2>Состояние видео</h2>
            <dl>
              <dt>Публикация</dt>
              <dd>
                {{
                  query.data.value.publication === 'draft'
                    ? 'Черновик'
                    : 'Опубликовано'
                }}
              </dd>
              <dt>Обработка</dt>
              <dd>{{ videoState(query.data.value) }}</dd>
              <dt>Добавлено</dt>
              <dd>
                {{
                  query.data.value.added_at
                    ? new Date(query.data.value.added_at).toLocaleDateString(
                        'ru',
                      )
                    : 'Файл ещё не принят'
                }}
              </dd>
            </dl>
            <RouterLink
              v-if="canWatchOwnedVideo(query.data.value)"
              :to="{ path: '/watch', query: { v: id } }"
              class="text-link settings-watch"
              >Смотреть видео</RouterLink
            ><RouterLink
              v-if="query.data.value.publication === 'draft'"
              :to="{ path: '/studio/upload', query: { video: id } }"
              class="text-link settings-upload"
              >Открыть загрузку и публикацию</RouterLink
            >
            <p
              v-if="query.data.value.moderation === 'hidden'"
              class="form-error"
            >
              Видео скрыто. Изменение настроек не открывает просмотр.
            </p>
          </aside>
        </div>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      </form>
    </main></StudioShell
  >
</template>
<style scoped lang="scss">
.video-settings {
  padding: 24px 32px 48px;
  &__back {
    display: inline-flex;
    gap: 12px;
    align-items: center;
    font-size: 14px;
    color: var(--color-muted);
    margin-bottom: 24px;
  }
  &__heading {
    display: flex;
    gap: 24px;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 28px;
    h1 {
      font-size: 28px;
      line-height: 36px;
      margin: 0;
    }
  }
  &__actions {
    display: flex;
    gap: 12px;
    flex-shrink: 0;
  }
  &__columns {
    display: grid;
    grid-template-columns: minmax(0, 720px) minmax(240px, 320px);
    gap: 32px;
  }
}
.settings-unsaved,
.settings-notice {
  font-size: 12px;
  line-height: 20px;
  color: var(--color-muted);
}
.settings-field {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--studio-input-border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 24px;
  > span {
    color: var(--color-muted);
    font-size: 12px;
    line-height: 16px;
  }
  textarea,
  select {
    border: 0;
    background: transparent;
    padding: 6px 0;
    font-size: 16px;
    line-height: 24px;
    width: 100%;
    min-width: 0;
    outline: 0;
  }
  textarea {
    resize: vertical;
  }
  select option {
    background: var(--studio-dialog);
  }
  small {
    color: var(--color-muted);
    font-size: 12px;
    text-align: right;
    &.field__error {
      color: var(--color-error);
    }
  }
  &:focus-within {
    border-color: var(--color-accent);
  }
}
.settings-summary {
  align-self: start;
  border-radius: 12px;
  background: var(--color-surface);
  padding: 24px;
  h2 {
    font-size: 18px;
    margin: 0 0 20px;
  }
  dl {
    margin: 0 0 24px;
    font-size: 14px;
  }
  dt {
    color: var(--color-muted);
    font-size: 12px;
    margin-top: 16px;
  }
  dd {
    margin: 4px 0 0;
  }
  a {
    display: block;
    margin-top: 16px;
    font-size: 14px;
  }
}
@media (max-width: 1000px) {
  .video-settings__heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .video-settings__columns {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 767px) {
  .video-settings {
    padding: 16px 16px 32px;
    &__heading h1 {
      font-size: 24px;
    }
    &__actions {
      flex-wrap: wrap;
      gap: 8px;
    }
  }
}
</style>
