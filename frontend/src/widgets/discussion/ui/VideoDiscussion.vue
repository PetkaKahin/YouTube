<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  shallowReactive,
  watch,
} from 'vue';
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/vue-query';
import { useRoute, RouterLink } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  isIdentifier,
  pageV1VideosVideoIdCommentsGet,
  createV1VideosVideoIdCommentsPost,
  setStateV1VideosVideoIdCommentsCommentIdLikePut,
  unwrap,
  type Identifier,
} from '../../../shared/api';
import { useNextPage } from '../../../shared/lib/pagination';
import { AppIcon } from '../../../shared/ui';
import CommentItem from './CommentItem.vue';
const props = defineProps<{ videoId: Identifier }>();
const emit = defineEmits<{ accessLost: [] }>();
const session = useSession(),
  client = useQueryClient(),
  route = useRoute();
const sort = ref<'newest' | 'popular'>('newest'),
  sortOpen = ref(false),
  text = ref(''),
  error = ref(''),
  pending = ref(false),
  composing = ref(false),
  notice = ref('');
const closed = ref(false);
const likeRequests = shallowReactive(new Map<Identifier, AbortController>());
const textarea = ref<HTMLTextAreaElement>(),
  sortButton = ref<HTMLButtonElement>(),
  menu = ref<HTMLElement>(),
  sentinel = ref<HTMLElement>();
const controller = new AbortController();
let disposed = false;
function outsideSort(event: PointerEvent) {
  if (
    sortOpen.value &&
    !menu.value?.contains(event.target as Node) &&
    !sortButton.value?.contains(event.target as Node)
  )
    sortOpen.value = false;
}
document.addEventListener('pointerdown', outsideSort);
const key = computed(
  () => ['comments', session.scope, props.videoId, sort.value] as const,
);
const query = useInfiniteQuery({
  queryKey: key,
  enabled: computed(() => !closed.value && !likeRequests.size),
  initialPageParam: undefined as string | undefined,
  queryFn: async ({
    queryKey: [, , videoId, requestedSort],
    pageParam,
    signal,
  }) => {
    const page = unwrap(
      await pageV1VideosVideoIdCommentsGet({
        path: { video_id: videoId },
        query: { sort: requestedSort, cursor: pageParam },
        signal,
      }),
    );
    if (page.items.some((item) => !isIdentifier(item.id)))
      throw new Error('Invalid comment identifier');
    return page;
  },
  getNextPageParam: (page) => page.next_cursor ?? undefined,
  retry: false,
  gcTime: 0,
});
type Page = NonNullable<typeof query.data.value>['pages'][number];
const items = computed(() => {
  const seen = new Set<string>();
  return (
    query.data.value?.pages
      .flatMap((page) => page.items)
      .filter((item) => !seen.has(item.id) && !!seen.add(item.id)) ?? []
  );
});
const unavailable = computed(
  () =>
    closed.value ||
    (query.error.value instanceof ApiError &&
      [403, 404].includes(query.error.value.status)),
);
const count = computed(() => [...text.value].length);
const valid = computed(
  () =>
    !!text.value.trim() && count.value <= 2000 && !text.value.includes('\0'),
);
function nextPage() {
  if (
    query.hasNextPage.value &&
    !likeRequests.size &&
    !query.isFetching.value &&
    !query.isError.value
  )
    void query.fetchNextPage();
}
function continueList() {
  if (closed.value || likeRequests.size || query.isFetching.value) return;
  if (query.isFetchNextPageError.value) void query.fetchNextPage();
  else if (query.isError.value) void query.refetch();
  else nextPage();
}
useNextPage(sentinel, nextPage);
function accessDenied(reason: unknown) {
  return (
    reason instanceof ApiError &&
    (reason.status === 404 ||
      (reason.status === 403 && reason.code !== 'csrf_failed'))
  );
}
async function loseDiscussion() {
  if (disposed || closed.value) return;
  const requestKey = ['comments', session.scope, props.videoId];
  closed.value = true;
  for (const request of likeRequests.values()) request.abort();
  text.value = '';
  error.value = '';
  notice.value = '';
  sortOpen.value = false;
  await client.cancelQueries({ queryKey: requestKey });
  client.removeQueries({ queryKey: requestKey });
  if (disposed) return;
  emit('accessLost');
}
watch(query.error, (reason) => {
  if (accessDenied(reason)) void loseDiscussion();
});
async function chooseSort(value: 'newest' | 'popular') {
  sort.value = value;
  sortOpen.value = false;
  await nextTick();
  sortButton.value?.focus();
}
async function toggleSort() {
  sortOpen.value = !sortOpen.value;
  await nextTick();
  if (sortOpen.value)
    menu.value?.querySelector<HTMLButtonElement>('button')?.focus();
}
async function closeSort() {
  sortOpen.value = false;
  await nextTick();
  sortButton.value?.focus();
}
function menuKeys(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    void closeSort();
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const buttons = [
      ...(menu.value?.querySelectorAll<HTMLButtonElement>('button') ?? []),
    ];
    buttons[
      (buttons.indexOf(document.activeElement as HTMLButtonElement) + 1) %
        buttons.length
    ]?.focus();
  }
}
async function submit() {
  if (
    pending.value ||
    closed.value ||
    session.loading ||
    session.changing ||
    session.error ||
    !session.account ||
    !query.data.value?.pages[0]?.can_comment
  )
    return;
  if (!valid.value) {
    error.value =
      count.value > 2000
        ? 'Не более 2000 символов.'
        : 'Введите непустой комментарий.';
    await nextTick();
    textarea.value?.focus();
    return;
  }
  const scope = session.scope,
    videoId = props.videoId,
    requestKey = ['comments', session.scope, props.videoId];
  pending.value = true;
  error.value = '';
  notice.value = '';
  try {
    unwrap(
      await createV1VideosVideoIdCommentsPost({
        path: { video_id: videoId },
        body: { text: text.value },
        signal: controller.signal,
      }),
    );
    if (
      !disposed &&
      !closed.value &&
      session.scope === scope &&
      props.videoId === videoId
    ) {
      text.value = '';
      composing.value = false;
      notice.value = 'Комментарий опубликован.';
      await client.resetQueries({ queryKey: requestKey });
    }
  } catch (reason) {
    if (
      !disposed &&
      !closed.value &&
      session.scope === scope &&
      props.videoId === videoId
    ) {
      error.value = apiMessage(reason);
      if (accessDenied(reason)) void loseDiscussion();
    }
  } finally {
    pending.value = false;
    if (!disposed && !closed.value && session.scope === scope && error.value) {
      await nextTick();
      textarea.value?.focus();
    }
  }
}
async function setLike(id: Identifier, liked: boolean) {
  if (
    disposed ||
    closed.value ||
    likeRequests.has(id) ||
    !session.account ||
    session.loading ||
    session.changing ||
    session.error
  )
    return;
  const scope = session.scope,
    videoId = props.videoId,
    requestKey = ['comments', scope, videoId],
    request = new AbortController();
  const current = () =>
    !disposed &&
    !closed.value &&
    session.scope === scope &&
    props.videoId === videoId;
  likeRequests.set(id, request);
  try {
    await client.cancelQueries({ queryKey: requestKey });
    if (!current()) return;
    const value = unwrap(
      await setStateV1VideosVideoIdCommentsCommentIdLikePut({
        path: { video_id: videoId, comment_id: id },
        body: { liked },
        signal: request.signal,
      }),
    );
    if (!current()) return;
    client.setQueriesData<InfiniteData<Page>>(
      { queryKey: requestKey },
      (previous) =>
        previous
          ? {
              ...previous,
              pages: previous.pages.map((page) => ({
                ...page,
                items: page.items.map((item) =>
                  item.id === id ? { ...item, ...value } : item,
                ),
              })),
            }
          : previous,
    );
  } catch (reason) {
    if (current() && accessDenied(reason)) void loseDiscussion();
    throw reason;
  } finally {
    // Enabling the list after the last command fetches the server order again.
    likeRequests.delete(id);
  }
}
onBeforeUnmount(() => {
  disposed = true;
  document.removeEventListener('pointerdown', outsideSort);
  controller.abort();
  for (const request of likeRequests.values()) request.abort();
  likeRequests.clear();
  text.value = '';
});
</script>
<template>
  <section class="discussion" aria-label="Комментарии">
    <div class="discussion__header">
      <h2>Комментарии</h2>
      <div class="discussion__sort" @keydown="menuKeys">
        <button
          ref="sortButton"
          type="button"
          aria-label="Упорядочить комментарии"
          :aria-expanded="sortOpen"
          @click="toggleSort"
        >
          <AppIcon name="filter" />Упорядочить
        </button>
        <div v-if="sortOpen" ref="menu" class="discussion__menu">
          <button
            type="button"
            :aria-pressed="sort === 'newest'"
            @click="chooseSort('newest')"
          >
            Сначала новые</button
          ><button
            type="button"
            :aria-pressed="sort === 'popular'"
            @click="chooseSort('popular')"
          >
            Сначала популярные
          </button>
        </div>
      </div>
    </div>
    <p v-if="unavailable" role="status">Комментарии недоступны.</p>
    <template v-else>
      <form
        v-if="session.account && query.data.value?.pages[0]?.can_comment"
        class="discussion__form"
        @submit.prevent="submit"
      >
        <label for="new-comment" class="discussion__label"
          >Введите комментарий</label
        >
        <textarea
          id="new-comment"
          ref="textarea"
          v-model="text"
          rows="1"
          placeholder="Введите комментарий"
          :disabled="pending"
          :aria-invalid="!!error"
          aria-describedby="comment-validation"
          @focus="composing = true"
        />
        <div v-if="composing || text || error" class="discussion__form-actions">
          <span :class="{ 'form-error': count > 2000 }">{{ count }}/2000</span
          ><button
            type="button"
            class="button discussion__cancel"
            :disabled="pending"
            @click="
              text = '';
              error = '';
              composing = false;
            "
          >
            Отмена</button
          ><button
            class="button button--primary"
            type="submit"
            :disabled="pending || !valid"
          >
            {{ pending ? 'Отправка…' : 'Оставить комментарий' }}
          </button>
        </div>
        <p id="comment-validation" class="form-error" role="alert">
          {{ error }}
        </p>
      </form>
      <RouterLink
        v-else-if="!session.loading && !session.account"
        class="discussion__guest"
        :to="{ path: '/login', query: { returnTo: route.fullPath } }"
        >Войдите, чтобы оставить комментарий</RouterLink
      >
      <p v-if="notice" class="discussion__notice" role="status">{{ notice }}</p>
      <p
        v-if="
          query.isFetching.value &&
          !query.isPending.value &&
          !query.isFetchingNextPage.value
        "
        role="status"
      >
        Обновление комментариев…
      </p>
      <p v-if="query.isPending.value" role="status">Загрузка комментариев…</p>
      <template
        v-else-if="!query.isError.value || query.isFetchNextPageError.value"
      >
        <CommentItem
          v-for="comment in items"
          :key="comment.id"
          :video-id="videoId"
          :comment="comment"
          :pending="likeRequests.has(comment.id)"
          :set-like="setLike"
        />
        <p v-if="!items.length" class="discussion__empty">
          Комментариев пока нет.
        </p>
      </template>
      <p v-if="query.isError.value" class="form-error" role="alert">
        {{ apiMessage(query.error.value) }}
      </p>
      <button
        v-if="query.hasNextPage.value || query.isError.value"
        class="text-link discussion__more"
        type="button"
        :disabled="query.isFetching.value || !!likeRequests.size"
        @click="continueList"
      >
        {{
          query.isFetching.value
            ? 'Загрузка…'
            : query.isError.value
              ? 'Повторить'
              : 'Показать ещё'
        }}
      </button>
      <div v-if="query.hasNextPage.value" ref="sentinel" aria-hidden="true" />
    </template>
  </section>
</template>
<style scoped lang="scss">
.discussion {
  margin-top: 24px;
  font-size: 14px;
  line-height: 20px;
  &__header {
    display: flex;
    align-items: center;
    gap: 32px;
    margin-bottom: 24px;
    h2 {
      font-size: 20px;
      line-height: 28px;
      margin: 0;
      font-weight: 700;
    }
  }
  &__sort {
    position: relative;
    > button {
      display: flex;
      align-items: center;
      gap: 8px;
      border: 0;
      background: transparent;
      padding: 0;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
    }
  }
  &__menu {
    position: absolute;
    top: 32px;
    left: 0;
    min-width: 210px;
    padding: 8px 0;
    background: var(--color-menu);
    border-radius: 12px;
    box-shadow: 0 4px 24px #0008;
    z-index: 5;
    button {
      display: block;
      width: 100%;
      background: transparent;
      border: 0;
      text-align: left;
      padding: 12px 16px;
      cursor: pointer;
      &:hover,
      &[aria-pressed='true'] {
        background: var(--color-hover);
      }
    }
  }
  &__form {
    margin-bottom: 24px;
    textarea {
      display: block;
      resize: vertical;
      width: 100%;
      min-height: 32px;
      max-height: 300px;
      border: 0;
      border-bottom: 1px solid var(--color-border);
      border-radius: 0;
      padding: 0 0 4px;
      background: transparent;
      font-size: 14px;
      line-height: 20px;
      &:focus {
        outline: 0;
        border-color: var(--color-text);
      }
      &[aria-invalid='true'] {
        border-color: var(--color-error);
      }
    }
    .form-error:empty {
      display: none;
    }
  }
  &__label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  &__form-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 8px;
    flex-wrap: wrap;
    > span {
      font-size: 12px;
      color: var(--color-muted);
      margin-right: auto;
    }
  }
  &__cancel {
    background: transparent;
  }
  &__guest {
    display: block;
    border-bottom: 1px solid var(--color-border);
    padding: 4px 0 8px;
    color: var(--color-muted);
  }
  &__empty {
    color: var(--color-muted);
    text-align: center;
    padding: 24px;
  }
  &__notice {
    color: var(--color-muted);
  }
  &__more {
    margin-top: 16px;
    padding: 8px 0;
  }
}
@media (max-width: 767px) {
  .discussion {
    margin-top: 16px;
    &__header {
      gap: 16px;
      flex-wrap: wrap;
      h2 {
        font-size: 18px;
        line-height: 24px;
      }
    }
    &__menu {
      left: auto;
      right: 0;
    }
    &__form-actions .button {
      font-size: 12px;
      padding: 8px 12px;
    }
  }
}
</style>
