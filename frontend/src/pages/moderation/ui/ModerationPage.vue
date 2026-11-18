<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/vue-query';
import { StudioShell } from '../../../widgets/studio-shell';
import { VideoPlayer } from '../../../widgets/video-player';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  reportsV1ModerationReportsGet,
  reportV1ModerationReportsReportIdGet,
  reportedVideoV1ModerationReportsReportIdVideoGet,
  decideV1ModerationReportsReportIdDecisionPut,
  unwrap,
  type ReportResponse,
} from '../../../shared/api';
import { mediaUrl } from '../../../shared/lib/media';
import { useNextPage } from '../../../shared/lib/pagination';
import { useOperations } from '../../../shared/lib/operations';
import { PageState, StudioDialog } from '../../../shared/ui';

const route = useRoute(),
  session = useSession(),
  client = useQueryClient();
const staff = computed(
  () =>
    !!session.account && ['moderator', 'admin'].includes(session.account.role),
);
const id = computed(() =>
  isIdentifier(route.params.reportId) ? route.params.reportId : undefined,
);
const detail = computed(
  () => route.params.reportId !== undefined && route.params.reportId !== '',
);
const denied = ref(false),
  pending = ref(false),
  error = ref(''),
  notice = ref(''),
  confirm = ref<'' | 'dismiss' | 'hide'>('');
const reasons = [
  ['spam', 'Спам или мошенничество'],
  ['harassment', 'Оскорбления или травля'],
  ['violence', 'Жестокое содержание'],
  ['sexual_content', 'Содержание сексуального характера'],
  ['copyright', 'Нарушение авторских прав'],
  ['other', 'Другое'],
] as const;
type Reason = (typeof reasons)[number][0];
const reason = ref<Reason>();
const reasonLabel = (value: string) =>
  reasons.find((x) => x[0] === value)?.[1] ?? 'Неизвестная причина';
const date = (value: string) => new Date(value).toLocaleString('ru');
const identity = () =>
  `${session.scope}:${session.account?.role}:${id.value}:${denied.value}`;
const begin = useOperations(identity);
const allowed = computed(
  () => staff.value && !session.changing && !denied.value,
);
function purge() {
  const old = new Set(
    client.getQueryCache().findAll({ queryKey: ['moderation'] }),
  );
  const filter = {
    predicate: (q: Parameters<typeof old.has>[0]) => old.has(q),
  };
  void client.cancelQueries(filter).then(() => client.removeQueries(filter));
}
function loseAccess() {
  denied.value = true;
  confirm.value = '';
  reason.value = undefined;
  notice.value = '';
  error.value = '';
  purge();
  void session.refresh();
}
const queue = useInfiniteQuery({
  queryKey: computed(() => [
    'moderation',
    session.scope,
    session.account?.role,
    'queue',
  ]),
  enabled: computed(() => allowed.value && !detail.value),
  gcTime: 0,
  retry: false,
  initialPageParam: undefined as string | undefined,
  queryFn: async ({ pageParam, signal }) =>
    unwrap(
      await reportsV1ModerationReportsGet({
        query: { cursor: pageParam },
        signal,
      }),
    ),
  getNextPageParam: (page) => page.next_cursor ?? undefined,
});
const report = useQuery({
  queryKey: computed(() => [
    'moderation',
    session.scope,
    session.account?.role,
    id.value,
  ]),
  enabled: computed(() => allowed.value && detail.value && !!id.value),
  gcTime: 0,
  retry: false,
  queryFn: async ({ signal }) => {
    const value = unwrap(
      await reportV1ModerationReportsReportIdGet({
        path: { report_id: id.value! },
        signal,
      }),
    );
    if (
      value.id !== id.value ||
      !isIdentifier(value.video_id) ||
      !isIdentifier(value.reporter_id)
    )
      throw new Error('Invalid report');
    return value;
  },
});
const material = useQuery({
  queryKey: computed(() => [
    'moderation',
    session.scope,
    session.account?.role,
    id.value,
    'video',
    report.data.value?.revision,
  ]),
  enabled: computed(
    () =>
      allowed.value &&
      detail.value &&
      !!id.value &&
      report.data.value?.state === 'pending' &&
      !pending.value,
  ),
  gcTime: 0,
  retry: false,
  queryFn: async ({ signal }) => {
    const value = unwrap(
      await reportedVideoV1ModerationReportsReportIdVideoGet({
        path: { report_id: id.value! },
        signal,
      }),
    );
    if (value.id !== report.data.value?.video_id)
      throw new Error('Invalid report video');
    const prefix = `/api/v1/moderation/reports/${id.value}/`;
    mediaUrl(value.hls_url, prefix + 'hls/');
    mediaUrl(value.preview_url, prefix);
    return value;
  },
});
watch([queue.error, report.error, material.error], (errors) => {
  if (
    errors.some(
      (e) => e instanceof ApiError && [401, 403, 404].includes(e.status),
    )
  )
    loseAccess();
});
watch(
  [() => session.scope, () => session.account?.role, id],
  () => {
    denied.value = false;
    pending.value = false;
    confirm.value = '';
    reason.value = undefined;
    error.value = '';
    notice.value = '';
    mutation.reset();
  },
  { flush: 'sync' },
);
watch(
  () => report.data.value?.revision,
  () => {
    confirm.value = '';
    reason.value = undefined;
  },
);
const rows = computed(() => {
  if (!allowed.value) return [];
  const seen = new Set<string>();
  return (
    queue.data.value?.pages
      .flatMap((p) => p.items)
      .filter((r) => !seen.has(r.id) && !!seen.add(r.id)) ?? []
  );
});
const sentinel = ref<HTMLElement>();
function more() {
  if (
    queue.hasNextPage.value &&
    !queue.isFetching.value &&
    !queue.isError.value
  )
    void queue.fetchNextPage();
}
useNextPage(sentinel, more);
function retryQueue() {
  if (queue.isFetchNextPageError.value) void queue.fetchNextPage();
  else void queue.refetch();
}
function openDecision(action: 'dismiss' | 'hide') {
  if (pending.value || report.data.value?.state !== 'pending') return;
  error.value = '';
  notice.value = '';
  confirm.value = action;
}
const mutation = useMutation({
  gcTime: 0,
  mutationFn: async () => {
    const work = begin(),
      current = report.data.value!,
      action = confirm.value;
    try {
      await client.cancelQueries({
        queryKey: [
          'moderation',
          session.scope,
          session.account?.role,
          id.value,
          'video',
        ],
      });
      work.check();
      const result = unwrap(
        await decideV1ModerationReportsReportIdDecisionPut({
          path: { report_id: id.value! },
          body:
            action === 'hide'
              ? {
                  revision: current.revision,
                  action: 'hide',
                  reason: reason.value!,
                }
              : { revision: current.revision, action: 'dismiss' },
          signal: work.signal,
        }),
      );
      work.check();
      if (
        result.id !== id.value ||
        result.revision !== current.revision ||
        result.decision?.action !== action
      )
        throw new Error('Invalid decision confirmation');
      client.setQueryData(
        ['moderation', session.scope, session.account?.role, id.value],
        result,
      );
      client.setQueryData<
        InfiniteData<{ items: ReportResponse[]; next_cursor: string | null }>
      >(
        ['moderation', session.scope, session.account?.role, 'queue'],
        (old) =>
          old && {
            ...old,
            pages: old.pages.map((page) => ({
              ...page,
              items: page.items.filter((item) => item.id !== result.id),
            })),
          },
      );
      await client.cancelQueries({
        queryKey: [
          'moderation',
          session.scope,
          session.account?.role,
          id.value,
          'video',
        ],
      });
      client.removeQueries({
        queryKey: [
          'moderation',
          session.scope,
          session.account?.role,
          id.value,
          'video',
        ],
      });
      work.check();
      notice.value =
        action === 'hide'
          ? 'Видео скрыто. Решение сохранено.'
          : 'Жалоба отклонена. Решение сохранено.';
    } finally {
      work.finish();
    }
  },
});
async function decide() {
  if (
    pending.value ||
    !allowed.value ||
    !confirm.value ||
    report.data.value?.state !== 'pending' ||
    (confirm.value === 'hide' && !reason.value)
  )
    return;
  const current = identity();
  pending.value = true;
  error.value = '';
  try {
    await mutation.mutateAsync();
    if (current === identity()) confirm.value = '';
  } catch (cause) {
    if (current !== identity()) return;
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      loseAccess();
      return;
    }
    if (cause instanceof ApiError && cause.status === 409) {
      confirm.value = '';
      error.value =
        'Жалоба изменилась. Данные обновлены; проверьте их перед новым решением.';
      await report.refetch();
    } else error.value = apiMessage(cause);
  } finally {
    if (current === identity()) pending.value = false;
  }
}
function refresh() {
  denied.value = false;
  if (detail.value) void report.refetch();
  else void queue.refetch();
}
const oldTitle = document.title;
document.title = 'Модерация — YouTube';
onBeforeUnmount(() => {
  purge();
  mutation.reset();
  document.title = oldTitle;
});
</script>
<template>
  <StudioShell
    ><main id="main-content" class="moderation">
      <h1>Модерация</h1>
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
      <PageState v-else-if="!session.account" title="Войдите для модерации"
        ><RouterLink
          class="button button--primary"
          :to="{ path: '/login', query: { returnTo: route.fullPath } }"
          >Войти</RouterLink
        ></PageState
      >
      <PageState
        v-else-if="!staff || denied"
        title="Раздел недоступен"
        description="Для просмотра нужны действующие права модератора или администратора."
        :error="staff"
        @retry="refresh"
      />
      <template v-else-if="!detail">
        <h2>Ожидают рассмотрения</h2>
        <p class="muted">Сначала самые старые обращения</p>
        <PageState v-if="queue.isPending.value" title="Загружаем жалобы" />
        <PageState
          v-else-if="queue.isError.value && !rows.length"
          title="Не удалось загрузить очередь"
          :description="apiMessage(queue.error.value)"
          error
          @retry="retryQueue"
        />
        <div
          v-else-if="rows.length"
          class="moderation-scroll"
          tabindex="0"
          role="region"
          aria-label="Очередь жалоб"
        >
          <table class="moderation-table">
            <thead>
              <tr>
                <th>Жалоба</th>
                <th>Причина</th>
                <th>Видео</th>
                <th>Отправитель</th>
                <th>Отправлена</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in rows" :key="item.id">
                <td>
                  <RouterLink :to="`/moderation/${item.id}`"
                    >#{{ item.id }}</RouterLink
                  >
                </td>
                <td>{{ reasonLabel(item.reason) }}</td>
                <td>{{ item.video_id }}</td>
                <td>{{ item.reporter_id }}</td>
                <td>{{ date(item.submitted_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <PageState v-else title="Нет жалоб для рассмотрения" />
        <div ref="sentinel" class="moderation-more">
          <p v-if="queue.isFetchingNextPage.value" role="status">Загружаем…</p>
          <button
            v-else-if="queue.isFetchNextPageError.value"
            class="button"
            @click="retryQueue"
          >
            Повторить загрузку</button
          ><button
            v-else-if="queue.hasNextPage.value"
            class="button"
            @click="more"
          >
            Показать ещё
          </button>
        </div>
      </template>
      <template v-else>
        <RouterLink to="/moderation" class="text-link"
          >← Очередь жалоб</RouterLink
        >
        <PageState v-if="!id" title="Жалоба недоступна" />
        <PageState
          v-else-if="report.isPending.value"
          title="Загружаем жалобу"
        />
        <PageState
          v-else-if="report.isError.value"
          title="Не удалось загрузить жалобу"
          :description="apiMessage(report.error.value)"
          error
          @retry="report.refetch()"
        />
        <section
          v-else-if="report.data.value"
          :key="`${session.scope}:${id}`"
          class="report-context"
        >
          <h2>Жалоба #{{ report.data.value.id }}</h2>
          <dl>
            <dt>Причина</dt>
            <dd>{{ reasonLabel(report.data.value.reason) }}</dd>
            <dt>Отправлена</dt>
            <dd>{{ date(report.data.value.submitted_at) }}</dd>
            <dt>Отправитель</dt>
            <dd>{{ report.data.value.reporter_id }}</dd>
            <dt>Видео</dt>
            <dd>{{ report.data.value.video_id }}</dd>
            <dt>Ревизия</dt>
            <dd>{{ report.data.value.revision }}</dd>
          </dl>
          <template v-if="report.data.value.state === 'pending'">
            <p class="service-notice">
              Служебный просмотр по жалобе. Доступ действует только для её
              рассмотрения.
            </p>
            <PageState
              v-if="material.isPending.value"
              title="Загружаем материал"
            />
            <PageState
              v-else-if="material.isError.value"
              title="Не удалось загрузить материал"
              :description="apiMessage(material.error.value)"
              error
              @retry="material.refetch()"
            />
            <div
              v-else-if="material.data.value && id"
              class="moderation-material"
            >
              <VideoPlayer
                v-if="!pending"
                :key="`${session.scope}:${id}:${report.data.value.revision}`"
                :report-id="id"
                :video-id="material.data.value.id"
                :src="material.data.value.hls_url"
                :poster="material.data.value.preview_url"
                :qualities="material.data.value.qualities"
                @access-lost="loseAccess"
                @verify-access="report.refetch()"
              />
              <p v-else role="status">Сохраняем решение…</p>
              <h3>{{ material.data.value.title }}</h3>
              <p class="moderation-description">
                {{ material.data.value.description }}
              </p>
            </div>
            <div class="moderation-actions">
              <button
                class="button button--secondary"
                :disabled="pending"
                @click="openDecision('dismiss')"
              >
                Отклонить жалобу</button
              ><button
                class="button button--primary"
                :disabled="pending"
                @click="openDecision('hide')"
              >
                Скрыть видео
              </button>
            </div>
          </template>
          <div v-else class="moderation-decision">
            <h3>
              {{
                report.data.value.decision?.action === 'hide'
                  ? 'Видео скрыто'
                  : 'Жалоба отклонена'
              }}
            </h3>
            <p v-if="report.data.value.decision?.reason">
              {{ reasonLabel(report.data.value.decision.reason) }}
            </p>
            <p v-if="report.data.value.decision">
              Решение: {{ date(report.data.value.decision.created_at) }}
            </p>
          </div>
          <p v-if="notice" class="moderation-notice" role="status">
            {{ notice }}
          </p>
          <p v-if="error && !confirm" class="form-error" role="alert">
            {{ error }}
          </p>
        </section>
      </template>
      <StudioDialog
        :open="!!confirm && allowed"
        :title="confirm === 'hide' ? 'Скрыть видео?' : 'Отклонить жалобу?'"
        :busy="pending"
        @close="confirm = ''"
      >
        <form id="moderation-decision-form" @submit.prevent="decide">
          <p>
            {{
              confirm === 'hide'
                ? 'Видео станет недоступно в обычном просмотре. Выберите причину скрытия.'
                : 'Жалоба будет закрыта без скрытия видео.'
            }}
          </p>
          <label v-if="confirm === 'hide'" for="hide-reason"
            >Причина скрытия<select
              id="hide-reason"
              v-model="reason"
              :disabled="pending"
            >
              <option :value="undefined" disabled>Выберите причину</option>
              <option
                v-for="[value, label] in reasons"
                :key="value"
                :value="value"
              >
                {{ label }}
              </option>
            </select></label
          >
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        </form>
        <template #footer
          ><button
            class="button button--secondary"
            :disabled="pending"
            @click="confirm = ''"
          >
            Отмена</button
          ><button
            class="button button--primary moderation-confirm"
            type="submit"
            form="moderation-decision-form"
            :disabled="pending || (confirm === 'hide' && !reason)"
          >
            {{ pending ? 'Сохраняем…' : 'Подтвердить' }}
          </button></template
        >
      </StudioDialog>
    </main></StudioShell
  >
</template>
<style scoped lang="scss">
.moderation {
  padding: 24px 32px 48px;
  h1 {
    font-size: 28px;
    margin: 0 0 24px;
  }
  h2 {
    font-size: 20px;
  }
  h3 {
    overflow-wrap: anywhere;
  }
  .muted {
    color: var(--color-muted);
  }
  .moderation-scroll {
    overflow: auto;
  }
  .moderation-table {
    width: 100%;
    min-width: 850px;
    border-collapse: collapse;
    text-align: left;
    th,
    td {
      padding: 16px;
      border-bottom: 1px solid var(--color-border);
    }
    th {
      color: var(--color-muted);
      font-size: 12px;
    }
    a {
      color: var(--color-accent);
    }
  }
  .moderation-more {
    margin-top: 16px;
  }
  .report-context {
    max-width: 1100px;
  }
  dl {
    display: grid;
    grid-template-columns: 130px minmax(0, 1fr);
    gap: 8px;
    font-size: 14px;
  }
  dt {
    color: var(--color-muted);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .service-notice,
  .moderation-decision {
    padding: 16px;
    border: 1px solid var(--color-border);
    border-radius: 12px;
  }
  .moderation-description {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .moderation-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin: 24px 0;
  }
  .moderation-notice {
    color: var(--color-muted);
  }
}
#moderation-decision-form {
  font-size: 14px;
  line-height: 22px;
  label {
    display: block;
  }
  select {
    display: block;
    width: 100%;
    margin-top: 8px;
    padding: 10px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 8px;
  }
}
@media (max-width: 767px) {
  .moderation {
    padding: 16px;
    h1 {
      font-size: 24px;
    }
    dl {
      grid-template-columns: 100px minmax(0, 1fr);
    }
  }
}
</style>
