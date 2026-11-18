<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  permissionsV1VideosVideoIdPermissionsGet,
  setPermissionV1VideosVideoIdPermissionsPut,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';
import { AppIcon, StudioDialog } from '../../../shared/ui';

const props = defineProps<{ videoId?: Identifier; disabled?: boolean }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient();
const open = ref(false),
  pending = ref(false),
  text = ref(''),
  error = ref(''),
  notice = ref(''),
  denied = ref(false);
const changes = ref(new Map<string, boolean>()),
  staged = ref(new Map<string, boolean>());
const begin = useOperations(() => `${session.scope}:${props.videoId ?? 'new'}`);
const key = computed(() => ['video-permissions', session.scope, props.videoId]);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(() => !!props.videoId && !denied.value),
  gcTime: 0,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) =>
    unwrap(
      await permissionsV1VideosVideoIdPermissionsGet({
        path: { video_id: props.videoId! },
        query: { cursor: pageParam },
        signal,
      }),
    ),
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
});
const emails = computed(() => {
  if (denied.value) return [];
  const result = new Set(
    query.data.value?.pages.flatMap((page) => page.emails) ?? [],
  );
  for (const [email, allowed] of changes.value) {
    if (allowed) result.add(email);
    else result.delete(email);
  }
  return [...result];
});
function reset() {
  open.value = false;
  pending.value = false;
  text.value = '';
  error.value = '';
  notice.value = '';
  changes.value.clear();
  staged.value.clear();
  denied.value = false;
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
function show() {
  changes.value = new Map(staged.value);
  text.value = '';
  error.value = '';
  open.value = true;
}
function add() {
  error.value = '';
  const values = text.value.split(/[\s,;]+/u).filter(Boolean);
  if (
    values.some(
      (value) =>
        !/^[^@\s]+@[^@\s]+\.[^@\s]+$/u.test(value) ||
        /[\0\uD800-\uDFFF]/u.test(value),
    )
  ) {
    error.value = 'Проверьте адрес электронной почты.';
    document.getElementById('permission-emails')?.focus();
    return false;
  }
  for (const value of values) changes.value.set(value.toLowerCase(), true);
  text.value = '';
  return true;
}
function remove(email: string) {
  const known = query.data.value?.pages.some((page) =>
    page.emails.includes(email),
  );
  if (known) changes.value.set(email, false);
  else changes.value.delete(email);
}
async function apply(
  target: Identifier,
  values: Map<string, boolean>,
  signal?: AbortSignal,
) {
  if (!values.size) return;
  if (pending.value) throw new Error('Permissions are already saving');
  pending.value = true;
  const work = begin(signal);
  try {
    await client.cancelQueries({ queryKey: key.value, exact: true });
    work.check();
    for (const [email, allowed] of [...values]) {
      const page = unwrap(
        await setPermissionV1VideosVideoIdPermissionsPut({
          path: { video_id: target },
          body: { email, allowed },
          signal: work.signal,
        }),
      );
      work.check();
      client.setQueryData(key.value, {
        pages: [page],
        pageParams: [undefined],
      });
      values.delete(email);
      staged.value.delete(email);
    }
    notice.value = 'Доступ сохранён';
  } catch (cause) {
    if (work.current()) error.value = apiMessage(cause);
    failure(cause);
    throw cause;
  } finally {
    work.finish();
    if (work.current()) pending.value = false;
  }
}
async function done() {
  if (pending.value || !add()) return;
  const identity = `${session.scope}:${props.videoId}`;
  try {
    if (props.videoId) {
      await apply(props.videoId, changes.value);
      staged.value.clear();
    } else {
      staged.value = new Map(changes.value);
      notice.value = staged.value.size
        ? 'Доступ будет применён при сохранении видео'
        : '';
    }
    open.value = false;
  } catch (cause) {
    if (identity === `${session.scope}:${props.videoId}`)
      error.value = apiMessage(cause);
  }
}
async function persist(target: Identifier, signal?: AbortSignal) {
  await apply(target, staged.value, signal);
}
defineExpose({ persist });
onBeforeUnmount(reset);
</script>
<template>
  <div class="video-permissions">
    <button
      type="button"
      class="button button--secondary"
      :disabled="disabled || pending || denied"
      @click="show"
    >
      <AppIcon name="user" />Указать пользователей
    </button>
    <p v-if="notice" class="permission-notice" role="status">{{ notice }}</p>
    <StudioDialog
      :open="open"
      :busy="pending"
      title="Кому вы хотите открыть доступ к видео?"
      @close="open = false"
    >
      <p class="permission-intro">
        Введите адреса электронной почты. Для просмотра пользователям нужно
        войти в аккаунт с указанным адресом.
      </p>
      <form @submit.stop.prevent="add">
        <label class="permission-field" for="permission-emails"
          ><span>Пользователи, которые получат доступ</span
          ><textarea
            id="permission-emails"
            v-model="text"
            autofocus
            rows="4"
            :disabled="pending"
            placeholder="Адреса через запятую или с новой строки"
            :aria-invalid="!!error"
          /></label
        ><button
          type="submit"
          class="text-link"
          :disabled="pending || !text.trim()"
        >
          Добавить адреса
        </button>
      </form>
      <p v-if="videoId && query.isPending.value" role="status">
        Загружаем адреса…
      </p>
      <p v-if="query.isError.value" class="form-error" role="alert">
        {{ apiMessage(query.error.value) }}
        <button type="button" class="text-link" @click="query.refetch()">
          Повторить
        </button>
      </p>
      <ul class="permission-list">
        <li v-for="email in emails" :key="email">
          <span>{{ email }}</span><button
            type="button"
            class="icon-button"
            :aria-label="`Отозвать доступ: ${email}`"
            :disabled="pending"
            @click="remove(email)"
          >
            <AppIcon name="close" />
          </button>
        </li>
      </ul>
      <button
        v-if="query.hasNextPage.value"
        type="button"
        class="text-link"
        :disabled="query.isFetching.value"
        @click="query.fetchNextPage()"
      >
        {{ query.isFetching.value ? 'Загружаем…' : 'Показать ещё' }}
      </button>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <template #footer
        ><button
          type="button"
          class="button button--secondary"
          :disabled="pending"
          @click="open = false"
        >
          Отмена</button
        ><button
          type="button"
          class="button permission-done"
          :disabled="pending || (videoId && query.isError.value)"
          @click="done"
        >
          {{ pending ? 'Сохраняем…' : 'Готово' }}
        </button></template
      >
    </StudioDialog>
  </div>
</template>
<style scoped lang="scss">
.permission-intro {
  font-size: 14px;
  line-height: 20px;
  margin: 0 0 12px;
}
.permission-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid var(--studio-input-border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  > span {
    color: var(--color-muted);
    font-size: 12px;
  }
  textarea {
    resize: vertical;
    min-height: 100px;
    background: transparent;
    border: 0;
    outline: 0;
    font-size: 16px;
    line-height: 24px;
  }
  &:focus-within {
    border-color: var(--color-accent);
  }
}
.permission-list {
  list-style: none;
  padding: 0;
  margin: 16px 0 0;
  li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    > span {
      flex: 1;
      overflow-wrap: anywhere;
      font-size: 14px;
    }
  }
}
.permission-done {
  background: var(--color-text);
  color: var(--color-background);
}
.permission-notice {
  font-size: 12px;
  color: var(--color-muted);
}
</style>
