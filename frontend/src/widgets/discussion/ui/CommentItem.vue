<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useSession } from '../../../entities/session';
import {
  apiMessage,
  type CommentResponse,
  type Identifier,
} from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
const props = defineProps<{
  comment: CommentResponse;
  videoId: Identifier;
  pending: boolean;
  setLike: (id: Identifier, liked: boolean) => Promise<void>;
}>();
const session = useSession(),
  route = useRoute(),
  router = useRouter();
const error = ref(''),
  expanded = ref(false);
let disposed = false;
async function toggle() {
  if (props.pending || session.loading || session.changing || session.error)
    return;
  if (!session.account) {
    await router.push({ path: '/login', query: { returnTo: route.fullPath } });
    return;
  }
  const scope = session.scope,
    videoId = props.videoId,
    commentId = props.comment.id;
  error.value = '';
  try {
    await props.setLike(commentId, !props.comment.liked);
  } catch (reason) {
    if (
      !disposed &&
      session.scope === scope &&
      props.videoId === videoId &&
      props.comment.id === commentId
    ) {
      error.value = apiMessage(reason);
    }
  }
}
onBeforeUnmount(() => {
  disposed = true;
});
</script>
<template>
  <article class="comment" :data-comment-id="comment.id">
    <span class="comment__avatar" aria-hidden="true">{{
      [...comment.author_name][0]?.toLocaleUpperCase('ru')
    }}</span>
    <div class="comment__body">
      <div class="comment__heading">
        <strong>{{ comment.author_name }}</strong><time
          :datetime="comment.created_at"
          >{{
            new Intl.DateTimeFormat('ru-RU', {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
            }).format(new Date(comment.created_at))
          }}</time
        >
      </div>
      <p :class="{ 'comment__text--expanded': expanded }" class="comment__text">
        {{ comment.text }}
      </p>
      <button
        v-if="comment.text.length > 300 || comment.text.split('\n').length > 4"
        type="button"
        class="comment__expand"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ expanded ? 'Свернуть' : 'Читать дальше' }}
      </button>
      <div class="comment__actions">
        <button
          class="icon-button"
          type="button"
          :disabled="session.loading || session.changing"
          :aria-disabled="pending"
          :aria-busy="pending"
          :aria-label="
            comment.liked ? 'Снять лайк комментария' : 'Нравится комментарий'
          "
          :aria-pressed="comment.liked"
          @click="toggle"
        >
          <AppIcon name="like" /></button
        ><span>{{
          new Intl.NumberFormat('ru-RU').format(comment.like_count)
        }}</span>
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    </div>
  </article>
</template>
<style scoped lang="scss">
.comment {
  display: flex;
  gap: 16px;
  margin-top: 24px;
  min-width: 0;
  &__avatar {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--color-surface);
    display: grid;
    place-items: center;
    font-size: 20px;
  }
  &__body {
    min-width: 0;
    flex: 1;
  }
  &__heading {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 13px;
    line-height: 18px;
    overflow-wrap: anywhere;
    strong {
      font-weight: 500;
    }
    time {
      color: var(--color-muted);
      font-size: 12px;
      flex-shrink: 0;
    }
  }
  &__text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    margin: 4px 0;
    font-size: 14px;
    line-height: 20px;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 4;
    overflow: hidden;
    &--expanded {
      display: block;
    }
  }
  &__expand {
    color: var(--color-muted);
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
  }
  &__actions {
    display: flex;
    align-items: center;
    margin-left: -8px;
    font-size: 12px;
    color: var(--color-muted);
    .icon-button {
      height: 32px;
      width: 32px;
      color: var(--color-text);
      &[aria-disabled='true'] {
        opacity: 0.6;
        cursor: wait;
      }
      svg {
        width: 16px;
        height: 16px;
      }
    }
    [aria-pressed='true'] {
      background: var(--color-hover);
    }
  }
}
@media (max-width: 767px) {
  .comment {
    gap: 12px;
    &__avatar {
      width: 32px;
      height: 32px;
      font-size: 16px;
    }
    &__heading {
      flex-wrap: wrap;
      gap: 4px 8px;
    }
  }
}
</style>
