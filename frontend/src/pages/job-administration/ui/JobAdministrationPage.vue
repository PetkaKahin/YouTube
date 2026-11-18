<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/vue-query';
import { StudioShell } from '../../../widgets/studio-shell';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  errorsV1AdminJobErrorsGet,
  retryV1AdminJobRetriesPost,
  retryOneV1AdminJobsJobIdRetryPost,
  retryStatusV1AdminJobRetriesRequestIdGet,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { useOperations } from '../../../shared/lib/operations';
import { useNextPage } from '../../../shared/lib/pagination';
import { PageState, StudioDialog } from '../../../shared/ui';
const route = useRoute(),
  router = useRouter(),
  session = useSession(),
  client = useQueryClient();
const admin = computed(() => session.account?.role === 'admin'),
  denied = ref(false),
  pending = ref(false),
  error = ref('');
const allowed = computed(
  () => admin.value && !session.changing && !denied.value,
);
const requestId = computed(() =>
  isIdentifier(route.query.retry) ? route.query.retry : undefined,
);
const selected = ref(new Set<Identifier>());
type Command =
  | { mode: 'one'; jobId: Identifier }
  | { mode: 'selected'; ids: Identifier[] }
  | { mode: 'all' };
const command = ref<Command>();
const identity = () =>
  `${session.scope}:${session.account?.role}:${route.fullPath}:${denied.value}`;
const begin = useOperations(identity);
function purge() {
  const old = new Set(
    client.getQueryCache().findAll({ queryKey: ['job-admin'] }),
  );
  const filter = {
    predicate: (q: Parameters<typeof old.has>[0]) => old.has(q),
  };
  void client.cancelQueries(filter).then(() => client.removeQueries(filter));
}
function loseAccess() {
  denied.value = true;
  pending.value = false;
  command.value = undefined;
  selected.value.clear();
  error.value = '';
  purge();
  void session.refresh();
}
const history = useInfiniteQuery({
  queryKey: computed(() => [
    'job-admin',
    session.scope,
    session.account?.role,
    'errors',
  ]),
  enabled: allowed,
  gcTime: 0,
  retry: false,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) =>
    unwrap(
      await errorsV1AdminJobErrorsGet({ query: { cursor: pageParam }, signal }),
    ),
  getNextPageParam: (p) => p.next_cursor ?? undefined,
});
const status = useInfiniteQuery({
  queryKey: computed(() => [
    'job-admin',
    session.scope,
    session.account?.role,
    'request',
    requestId.value,
  ]),
  enabled: computed(() => allowed.value && !!requestId.value),
  gcTime: 0,
  retry: false,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) => {
    const value = unwrap(
      await retryStatusV1AdminJobRetriesRequestIdGet({
        path: { request_id: requestId.value! },
        query: { cursor: pageParam },
        signal,
      }),
    );
    if (value.id !== requestId.value) throw new Error('Invalid request ID');
    return value;
  },
  getNextPageParam: (p) => p.next_cursor ?? undefined,
});
watch([history.error, status.error], (values) => {
  if (
    values.some(
      (x) => x instanceof ApiError && [401, 403, 404].includes(x.status),
    )
  )
    loseAccess();
});
watch(
  [() => session.scope, () => session.account?.role],
  () => {
    denied.value = false;
    pending.value = false;
    selected.value.clear();
    command.value = undefined;
    error.value = '';
    mutation.reset();
  },
  { flush: 'sync' },
);
watch(
  () => route.fullPath,
  () => {
    denied.value = false;
    pending.value = false;
    command.value = undefined;
    error.value = '';
    mutation.reset();
  },
  { flush: 'sync' },
);
function unique<T extends { id: Identifier }>(items: T[]) {
  const seen = new Set<string>();
  return items.filter((x) => !seen.has(x.id) && !!seen.add(x.id));
}
const rows = computed(() =>
  allowed.value
    ? unique(history.data.value?.pages.flatMap((p) => p.items) ?? [])
    : [],
);
const progress = computed(() =>
  allowed.value ? status.data.value?.pages[0] : undefined,
);
const results = computed(() => {
  if (!allowed.value) return [];
  const seen = new Set<string>();
  return (
    status.data.value?.pages
      .flatMap((p) => p.items)
      .filter((x) => !seen.has(x.job_id) && !!seen.add(x.job_id)) ?? []
  );
});
const sentinel = ref<HTMLElement>();
function more() {
  if (
    history.hasNextPage.value &&
    !history.isFetching.value &&
    !history.isError.value
  )
    void history.fetchNextPage();
}
useNextPage(sentinel, more);
function historyRetry() {
  if (history.isFetchNextPageError.value) void history.fetchNextPage();
  else void history.refetch();
}
function statusRetry() {
  if (status.isFetchNextPageError.value) void status.fetchNextPage();
  else void status.refetch();
}
function toggle(id: Identifier) {
  if (pending.value) return;
  const next = new Set(selected.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selected.value = next;
}
function open(value: Command) {
  if (!allowed.value || pending.value) return;
  if (value.mode === 'selected' && !value.ids.length) return;
  error.value = '';
  command.value = value;
}
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const work = begin(),
      value = command.value!;
    try {
      const result =
        value.mode === 'one'
          ? unwrap(
              await retryOneV1AdminJobsJobIdRetryPost({
                path: { job_id: value.jobId },
                signal: work.signal,
              }),
            )
          : unwrap(
              await retryV1AdminJobRetriesPost({
                body:
                  value.mode === 'all'
                    ? { mode: 'all' }
                    : { mode: 'selected', job_ids: value.ids },
                signal: work.signal,
              }),
            );
      work.check();
      if (!isIdentifier(result.id)) throw new Error('Invalid retry receipt');
      selected.value.clear();
      command.value = undefined;
      await router.replace({ path: route.path, query: { retry: result.id } });
    } finally {
      work.finish();
    }
  },
});
async function submit() {
  if (pending.value || !allowed.value || !command.value) return;
  const current = identity();
  pending.value = true;
  error.value = '';
  try {
    await mutation.mutateAsync();
  } catch (cause) {
    if (current !== identity()) return;
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      loseAccess();
      return;
    }
    error.value = apiMessage(cause);
  } finally {
    if (current === identity()) pending.value = false;
  }
}
const stateLabel = (value: string) =>
  ({
    pending: 'Обрабатывается запрос на повтор',
    submitted: 'Обработка запроса завершена',
    cancelled: 'Остаток запроса отменён',
  })[value] ?? value;
const resultLabel = (value: string) =>
  ({
    queued: 'Поставлено в очередь',
    skipped: 'Пропущено',
    unavailable: 'Недоступно',
  })[value] ?? value;
const reasonLabel = (value: string | null) =>
  value
    ? ({
        already_requested: 'Повтор уже запрошен',
        unregistered: 'Задание не поддерживает повтор',
        pruned: 'Исходное задание удалено',
        not_failed: 'Задание не находится в состоянии ошибки',
      }[value] ?? value)
    : '—';
const date = (value: string) => new Date(value).toLocaleString('ru');
const oldTitle = document.title;
document.title = 'Ошибки заданий — YouTube';
onBeforeUnmount(() => {
  purge();
  mutation.reset();
  document.title = oldTitle;
});
</script>
<template>
  <StudioShell
    ><main id="main-content" class="job-admin">
      <h1>Ошибки фоновых заданий</h1>
      <PageState
        v-if="session.loading || session.changing"
        title="Проверяем аккаунт"
      />
      <PageState
        v-else-if="session.error"
        title="Не удалось проверить аккаунт"
        error
        @retry="session.refresh()"
      />
      <PageState
        v-else-if="!session.account"
        title="Войдите для управления заданиями"
        ><RouterLink
          class="button button--primary"
          :to="{ path: '/login', query: { returnTo: route.fullPath } }"
          >Войти</RouterLink
        ></PageState
      >
      <PageState
        v-else-if="!admin || denied"
        title="Раздел недоступен"
        description="Нужны действующие права администратора."
      />
      <template v-else>
        <section
          v-if="route.query.retry !== undefined"
          class="job-receipt"
          aria-label="Результат запроса повтора"
        >
          <RouterLink to="/admin/jobs" class="text-link"
            >Закрыть результат</RouterLink
          >
          <PageState v-if="!requestId" title="Неверный номер запроса" />
          <PageState
            v-else-if="status.isPending.value"
            title="Загружаем результат запроса"
          />
          <PageState
            v-else-if="status.isError.value && !progress"
            title="Не удалось загрузить результат"
            :description="apiMessage(status.error.value)"
            error
            @retry="statusRetry"
          />
          <template v-else-if="progress"
            ><h2>Запрос #{{ progress.id }}</h2>
            <p class="job-request-state" role="status">
              {{ stateLabel(progress.status) }}
            </p>
            <p>
              Запрос относится к постановке заданий в очередь. Их выполнение
              проверяется отдельно; прежняя история ошибок сохраняется.
            </p>
            <dl>
              <dt>Поставлено в очередь</dt>
              <dd>{{ progress.queued }}</dd>
              <dt>Пропущено</dt>
              <dd>{{ progress.skipped }}</dd>
              <dt>Недоступно</dt>
              <dd>{{ progress.unavailable }}</dd>
            </dl>
            <button
              class="button button--secondary job-status-refresh"
              :disabled="status.isFetching.value"
              @click="status.refetch()"
            >
              Обновить состояние
            </button>
            <div
              v-if="results.length"
              class="job-scroll"
              tabindex="0"
              role="region"
              aria-label="Результаты повтора"
            >
              <table class="job-results">
                <thead>
                  <tr>
                    <th>Задание</th>
                    <th>Результат</th>
                    <th>Причина</th>
                    <th>Новый ID</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in results" :key="item.job_id">
                    <td>{{ item.job_id }}</td>
                    <td>{{ resultLabel(item.status) }}</td>
                    <td>{{ reasonLabel(item.reason) }}</td>
                    <td>{{ item.replacement_job_id ?? '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else>
              {{
                progress.status === 'pending'
                  ? 'Результатов по заданиям пока нет.'
                  : 'Нет результатов по заданиям.'
              }}
            </p>
            <button
              v-if="status.isError.value"
              class="button"
              @click="statusRetry"
            >
              Повторить загрузку результата</button
            ><button
              v-else-if="status.hasNextPage.value"
              class="button job-results-more"
              :disabled="status.isFetching.value"
              @click="status.fetchNextPage()"
            >
              Показать ещё результаты
            </button>
          </template>
        </section>
        <h2>История ошибок</h2>
        <p>
          Новые ошибки сверху. Выбор относится к заданиям: несколько ошибок
          одного задания выбираются вместе.
        </p>
        <div class="job-actions">
          <button
            class="button button--secondary job-selected"
            :disabled="pending || !selected.size"
            @click="open({ mode: 'selected', ids: [...selected] })"
          >
            Повторить выбранные ({{ selected.size }})</button
          ><button
            class="button button--primary job-all"
            :disabled="pending"
            @click="open({ mode: 'all' })"
          >
            Повторить все упавшие</button
          ><button
            class="button button--secondary"
            :disabled="history.isFetching.value || pending"
            @click="history.refetch()"
          >
            Обновить историю
          </button>
        </div>
        <PageState
          v-if="history.isPending.value"
          title="Загружаем историю ошибок"
        />
        <PageState
          v-else-if="history.isError.value && !rows.length"
          title="Не удалось загрузить историю"
          :description="apiMessage(history.error.value)"
          error
          @retry="historyRetry"
        />
        <div
          v-else-if="rows.length"
          class="job-scroll"
          tabindex="0"
          role="region"
          aria-label="История ошибок"
        >
          <table class="job-history">
            <thead>
              <tr>
                <th>Выбор</th>
                <th>Время</th>
                <th>Задание / попытка</th>
                <th>Этап / причина</th>
                <th>Диагностика</th>
                <th>Действие</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in rows"
                :key="item.id"
                :data-error-id="item.id"
                :data-job-id="item.job_id"
              >
                <td>
                  <input
                    type="checkbox"
                    :checked="selected.has(item.job_id)"
                    :disabled="pending"
                    :aria-label="`Выбрать задание ${item.job_id}`"
                    @change="toggle(item.job_id)"
                  />
                </td>
                <td>{{ date(item.occurred_at) }}</td>
                <td>
                  {{ item.job_id
                  }}<small
                    >Попытка {{ item.attempt }} ·
                    {{ item.task ?? 'Тип задания недоступен' }}</small
                  ><small
                    >Состояние: {{ item.job_status ?? 'неизвестно'
                    }}<template v-if="item.replacement_job_id">
                      · Новый ID {{ item.replacement_job_id }}</template
                    ></small
                  >
                </td>
                <td>
                  {{ item.stage }}<small>{{ item.reason }}</small>
                </td>
                <td>
                  <details>
                    <summary>{{ item.exception_type }}</summary>
                    <p v-if="item.error_code">Код: {{ item.error_code }}</p>
                    <ul v-if="item.trace.length">
                      <li v-for="(frame, index) in item.trace" :key="index">
                        {{ frame.file }}:{{ frame.line }} · {{ frame.function }}
                      </li>
                    </ul>
                    <p v-else>Стек не сохранён.</p>
                  </details>
                </td>
                <td>
                  <button
                    class="button button--secondary job-one"
                    :disabled="pending"
                    @click="open({ mode: 'one', jobId: item.job_id })"
                  >
                    Повторить
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <PageState v-else title="История ошибок пуста" />
        <div ref="sentinel" class="job-more">
          <p v-if="history.isFetchingNextPage.value" role="status">
            Загружаем…
          </p>
          <button
            v-else-if="history.isFetchNextPageError.value"
            class="button"
            @click="historyRetry"
          >
            Повторить загрузку</button
          ><button
            v-else-if="history.hasNextPage.value"
            class="button"
            @click="more"
          >
            Показать ещё
          </button>
        </div>
      </template>
      <StudioDialog
        :open="!!command && allowed"
        title="Повторить задания?"
        :busy="pending"
        @close="command = undefined"
        ><p v-if="command?.mode === 'all'">
          Будет запрошен повтор всех упавших заданий, включая задания за
          пределами загруженной истории.
        </p>
        <p v-else-if="command?.mode === 'selected'">
          Выбрано заданий: {{ command.ids.length }}. Будет запрошен их повтор.
        </p>
        <p v-else-if="command?.mode === 'one'">
          Будет запрошен повтор задания {{ command.jobId }}.
        </p>
        <p>
          Сервер проверит возможность повтора. Уже запрошенные повторы не
          создадут независимые дубли.
        </p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <template #footer
          ><button
            class="button button--secondary"
            :disabled="pending"
            @click="command = undefined"
          >
            Отмена</button
          ><button
            class="button button--primary job-confirm"
            :disabled="pending"
            @click="submit"
          >
            {{ pending ? 'Отправляем…' : 'Подтвердить' }}
          </button></template
        ></StudioDialog
      >
    </main></StudioShell
  >
</template>
<style scoped lang="scss">
.job-admin {
  padding: 24px 32px 48px;
  h1 {
    font-size: 28px;
    margin: 0 0 24px;
  }
  h2 {
    font-size: 20px;
  }
  p {
    font-size: 14px;
    line-height: 22px;
    color: var(--color-muted);
  }
  input {
    width: 20px;
    height: 20px;
    accent-color: var(--color-accent);
  }
  small {
    display: block;
    margin-top: 8px;
    color: var(--color-muted);
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  th,
  td {
    padding: 16px;
    border-bottom: 1px solid var(--color-border);
    vertical-align: top;
  }
  th {
    font-size: 12px;
    color: var(--color-muted);
  }
  table {
    border-collapse: collapse;
    text-align: left;
    width: 100%;
    min-width: 1000px;
  }
  details {
    max-width: 320px;
    overflow-wrap: anywhere;
  }
  summary {
    cursor: pointer;
  }
  ul {
    padding-left: 16px;
  }
  dl {
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 8px;
  }
  dd {
    margin: 0;
  }
  dt {
    color: var(--color-muted);
  }
  .job-scroll {
    overflow: auto;
    margin: 16px 0;
  }
  .job-actions {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin: 24px 0;
  }
  .job-receipt {
    border: 1px solid var(--color-border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 32px;
    overflow-wrap: anywhere;
  }
  .job-more {
    margin-top: 16px;
  }
  .job-request-state {
    color: var(--color-text);
  }
}
@media (max-width: 767px) {
  .job-admin {
    padding: 16px;
    h1 {
      font-size: 24px;
    }
    .job-receipt {
      padding: 16px;
    }
    dl {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .job-all {
      max-width: 100%;
    }
  }
}
</style>
