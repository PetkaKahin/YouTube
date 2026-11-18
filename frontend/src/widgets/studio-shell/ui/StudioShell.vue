<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { useSession } from '../../../entities/session';
import { CreatePlaylist } from '../../../features/create-playlist';
import { apiMessage } from '../../../shared/api';
import { AppIcon } from '../../../shared/ui';
import logo from '../../../shared/assets/youtube-logo.svg';

const session = useSession();
const route = useRoute();
let disposed = false,
  logoutRevision = 0;
watch(
  () => route.fullPath,
  () => logoutRevision++,
);
onBeforeUnmount(() => {
  disposed = true;
});
const collapsed = ref(false),
  accountOpen = ref(false),
  error = ref('');
const accountButton = ref<HTMLButtonElement>();
const initial = computed(() =>
  [...(session.account?.name || '')][0]?.toLocaleUpperCase('ru'),
);
watch(
  () => session.scope,
  () => {
    accountOpen.value = false;
    error.value = '';
  },
);
async function toggleAccount() {
  accountOpen.value = !accountOpen.value;
  await nextTick();
  if (accountOpen.value) document.getElementById('studio-logout')?.focus();
}
function closeAccount() {
  accountOpen.value = false;
  accountButton.value?.focus();
}
async function logout() {
  if (disposed || session.changing) return;
  const version = logoutRevision;
  const current = () => !disposed && version === logoutRevision;
  error.value = '';
  try {
    await session.signOut();
    if (!current()) return;
    closeAccount();
    await nextTick();
    if (current())
      [...document.querySelectorAll<HTMLElement>('a[href^="/login"]')]
        .find((link) => link.getClientRects().length)
        ?.focus();
  } catch (cause) {
    await nextTick();
    if (!current()) return;
    error.value = apiMessage(cause);
    accountOpen.value = !!session.account;
    await nextTick();
    if (current()) document.getElementById('studio-logout')?.focus();
  }
}
</script>
<template>
  <div
    class="studio-shell"
    :class="{ 'studio-shell--collapsed': collapsed }"
    @keydown.esc="closeAccount"
  >
    <a class="skip-link" href="#main-content">Перейти к содержимому</a>
    <header class="studio-header">
      <div class="studio-header__brand">
        <button
          type="button"
          class="icon-button studio-navigation-toggle"
          aria-label="Свернуть навигацию Studio"
          :aria-expanded="!collapsed"
          aria-controls="studio-navigation"
          @click="collapsed = !collapsed"
        >
          <AppIcon name="menu" />
        </button>
        <RouterLink to="/studio" class="studio-logo" aria-label="YouTube Studio"
          ><span><img :src="logo" width="112" height="24" alt="" /></span
          ><strong>Studio</strong></RouterLink
        >
      </div>
      <div class="studio-header__actions">
        <slot v-if="session.account" name="actions"
          ><CreatePlaylist navigate
        /></slot>
        <button
          v-if="session.account"
          ref="accountButton"
          type="button"
          class="studio-avatar"
          aria-label="Меню аккаунта Studio"
          :aria-expanded="accountOpen"
          aria-controls="studio-account"
          @click="toggleAccount"
        >
          {{ initial }}
        </button>
      </div>
    </header>
    <aside
      v-if="session.account"
      id="studio-navigation"
      class="studio-navigation"
    >
      <div class="studio-navigation__identity">
        <span class="studio-avatar" aria-hidden="true">{{
          initial
        }}</span><strong>Ваш канал</strong
        ><span>{{ session.account.name }}</span>
      </div>
      <nav aria-label="Studio">
        <RouterLink
          to="/studio/content/videos"
          class="studio-navigation__item"
          :aria-current="$route.path.startsWith('/studio') ? 'page' : undefined"
          ><AppIcon name="video" /><span>Контент</span></RouterLink
        >
        <RouterLink
          v-if="['moderator', 'admin'].includes(session.account.role)"
          to="/moderation"
          class="studio-navigation__item"
          :aria-current="
            $route.path.startsWith('/moderation') ? 'page' : undefined
          "
          ><AppIcon name="video" /><span>Модерация</span></RouterLink
        >
        <RouterLink
          v-if="session.account.role === 'admin'"
          to="/admin/jobs"
          class="studio-navigation__item"
          :aria-current="$route.path === '/admin/jobs' ? 'page' : undefined"
          ><AppIcon name="video" /><span>Ошибки заданий</span></RouterLink
        >
      </nav>
      <RouterLink to="/" class="studio-navigation__exit"
        ><AppIcon name="home" /><span>Перейти на YouTube</span></RouterLink
      >
    </aside>
    <div class="studio-shell__content"><slot /></div>
    <button
      v-if="accountOpen"
      class="studio-dismiss"
      type="button"
      tabindex="-1"
      aria-label="Закрыть меню аккаунта"
      @click="closeAccount"
    />
    <section
      v-if="accountOpen && session.account"
      id="studio-account"
      class="studio-account"
      aria-label="Аккаунт"
    >
      <p>{{ session.account.name }}</p>
      <RouterLink to="/">Перейти на YouTube</RouterLink>
      <button
        id="studio-logout"
        type="button"
        :disabled="session.changing"
        @click="logout"
      >
        <AppIcon name="logout" />Выйти
      </button>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>
<style scoped lang="scss">
.studio-shell {
  --studio-background: #161616;
  --studio-header-height: 64px;
  --studio-sidebar-width: 248px;
  --studio-dialog: #282828;
  --studio-input-border: #535353;
  --studio-dialog-radius: 24px;
  background: var(--studio-background);
  min-height: 100dvh;
  font-size: 14px;
  &__content {
    padding-top: var(--studio-header-height);
    margin-left: var(--studio-sidebar-width);
  }
  &--collapsed {
    --studio-sidebar-width: 72px;
  }
}
.studio-header {
  position: fixed;
  inset: 0 0 auto;
  height: var(--studio-header-height);
  background: var(--studio-background);
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px 0 16px;
  gap: 16px;
  &__brand,
  &__actions {
    display: flex;
    align-items: center;
    gap: 20px;
  }
}
.studio-logo {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  > span {
    width: 35px;
    height: 24px;
    overflow: hidden;
  }
  img {
    max-width: none;
    display: block;
  }
  strong {
    font-size: 26px;
    letter-spacing: -1px;
  }
}
.studio-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 0;
  background: #7057a3;
  color: #fff;
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  font-size: 18px;
}
button.studio-avatar {
  cursor: pointer;
}
.studio-navigation {
  position: fixed;
  inset: var(--studio-header-height) auto 0 0;
  width: var(--studio-sidebar-width);
  display: flex;
  flex-direction: column;
  padding: 24px 20px 16px 12px;
  &__identity {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 0 12px 28px;
    text-align: center;
    min-width: 0;
    .studio-avatar {
      width: 112px;
      height: 112px;
      font-size: 48px;
      margin-bottom: 8px;
    }
    strong {
      font-size: 12px;
    }
    > span:last-child {
      max-width: 100%;
      overflow-wrap: anywhere;
    }
  }
  &__item,
  &__exit {
    display: flex;
    align-items: center;
    gap: 24px;
    min-height: 40px;
    padding: 8px 12px;
    border-radius: 10px;
    svg {
      flex-shrink: 0;
    }
  }
  &__item {
    &:hover,
    &[aria-current='page'] {
      background: var(--color-surface);
    }
    &[aria-current='page'] {
      font-weight: 500;
    }
  }
  &__exit {
    margin-top: auto;
    &:hover {
      background: var(--color-surface);
    }
  }
}
.studio-shell--collapsed .studio-navigation {
  padding: 24px 8px;
  &__identity {
    display: none;
  }
  &__item,
  &__exit {
    justify-content: center;
    padding: 8px;
    span {
      display: none;
    }
  }
}
.studio-dismiss {
  position: fixed;
  inset: 0;
  border: 0;
  background: transparent;
  z-index: 35;
}
.studio-account {
  position: fixed;
  top: 56px;
  right: 16px;
  z-index: 40;
  width: 280px;
  max-width: calc(100vw - 32px);
  background: var(--color-menu);
  border-radius: 12px;
  box-shadow: 0 4px 32px #0006;
  overflow: hidden;
  p {
    padding: 0 16px;
    overflow-wrap: anywhere;
  }
  a,
  button {
    display: flex;
    gap: 16px;
    align-items: center;
    width: 100%;
    border: 0;
    background: transparent;
    padding: 12px 16px;
    cursor: pointer;
    &:hover {
      background: var(--color-hover);
    }
  }
}
@media (max-width: 767px) {
  .studio-shell {
    --studio-sidebar-width: 0px;
    --studio-header-height: 56px;
  }
  .studio-navigation,
  .studio-navigation-toggle {
    display: none;
  }
  .studio-header {
    padding: 0 16px;
    &__brand,
    &__actions {
      gap: 12px;
    }
  }
  .studio-logo strong {
    font-size: 22px;
  }
}
</style>
