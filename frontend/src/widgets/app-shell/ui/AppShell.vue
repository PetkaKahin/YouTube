<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { safeReturnTo, useSession } from '../../../entities/session';
import SubscribedChannels from './SubscribedChannels.vue';
import { CreatePlaylist } from '../../../features/create-playlist';
import { apiMessage } from '../../../shared/api';
import { AppIcon, BrandLogo } from '../../../shared/ui';

defineProps<{ ambientColor?: string }>();
const session = useSession();
const route = useRoute();
const router = useRouter();
const watchLayout = computed(() => !!route.meta.watchLayout);
const collapsed = ref(false);
const drawer = ref(false);
const channelsOpen = ref(true);
const extendedNavigation = computed(
  () =>
    drawer.value ||
    (wideViewport.value && !collapsed.value && !watchLayout.value),
);
const accountOpen = ref(false);
const search = ref('');
const searchInput = ref<HTMLInputElement>();
const accountButton = ref<HTMLButtonElement>();
const searchButton = ref<HTMLButtonElement>();
const menuButton = ref<HTMLButtonElement>();
const drawerTrigger = ref<HTMLElement>();
const menu = ref<HTMLElement>();
const logoutError = ref('');
let disposed = false,
  logoutRevision = 0;
const wideMedia = window.matchMedia('(min-width: 1313px)');
const phoneMedia = window.matchMedia('(max-width: 767px)');
const wideViewport = ref(wideMedia.matches);
const phoneViewport = ref(phoneMedia.matches);
const searchOpen = computed(
  () => phoneViewport.value && route.hash === '#searching',
);
function viewportChanged() {
  wideViewport.value = wideMedia.matches;
  phoneViewport.value = phoneMedia.matches;
  if (wideMedia.matches || phoneMedia.matches) drawer.value = false;
  if (!phoneMedia.matches && route.hash === '#searching') {
    void router.replace({ path: route.path, query: route.query, hash: '' });
  }
}
wideMedia.addEventListener('change', viewportChanged);
phoneMedia.addEventListener('change', viewportChanged);
const loginTo = computed(() => ({
  path: '/login',
  query: { returnTo: safeReturnTo(route.fullPath) },
}));
const initial = computed(
  () => [...(session.account?.name || '')][0]?.toLocaleUpperCase('ru') || '',
);

watch(
  () => route.fullPath,
  (value, previous) => {
    logoutRevision++;
    if (value.split('#')[0] !== previous?.split('#')[0])
      search.value =
        typeof route.query.search_query === 'string'
          ? route.query.search_query
          : '';
    drawer.value = false;
    accountOpen.value = false;
  },
  { immediate: true },
);
watch(
  searchOpen,
  async (open, previous) => {
    await nextTick();
    if (open) searchInput.value?.focus();
    else if (previous && phoneViewport.value) searchButton.value?.focus();
  },
  { immediate: true },
);
watch(drawer, (value) => {
  document.body.classList.toggle('drawer-open', value);
});
onBeforeUnmount(() => {
  disposed = true;
  document.body.classList.remove('drawer-open');
  wideMedia.removeEventListener('change', viewportChanged);
  phoneMedia.removeEventListener('change', viewportChanged);
});

async function toggleNavigation(event?: MouseEvent) {
  wideViewport.value = wideMedia.matches;
  if (!wideViewport.value || watchLayout.value) {
    drawer.value = !drawer.value;
    if (drawer.value) {
      drawerTrigger.value = event?.currentTarget as HTMLElement | undefined;
      await nextTick();
      menu.value?.querySelector<HTMLElement>('a,button')?.focus();
    }
  } else collapsed.value = !collapsed.value;
}
async function openSearch() {
  await router.push({
    path: route.path,
    query: route.query,
    hash: '#searching',
  });
}
async function closeSearch() {
  const target = router.resolve({
    path: route.path,
    query: route.query,
    hash: '',
  }).fullPath;
  if (router.options.history.state.back === target) router.back();
  else await router.replace(target);
}
async function closeNavigation() {
  drawer.value = false;
  await nextTick();
  (drawerTrigger.value ?? menuButton.value)?.focus();
}
function submitSearch() {
  const query = search.value.trim();
  if (!query || [...query].length > 100) {
    searchInput.value?.setCustomValidity('Введите от 1 до 100 символов.');
    searchInput.value?.reportValidity();
    return;
  }
  searchInput.value?.setCustomValidity('');
  void router.push({ path: '/results', query: { search_query: query } });
}
async function signOut() {
  if (disposed || session.changing) return;
  const version = logoutRevision;
  const current = () => !disposed && version === logoutRevision;
  logoutError.value = '';
  try {
    await session.signOut();
    if (!current()) return;
    accountOpen.value = false;
    await nextTick();
    [...document.querySelectorAll<HTMLElement>('a[href^="/login"]')]
      .find((link) => link.getClientRects().length)
      ?.focus();
  } catch (cause) {
    await nextTick();
    if (!current()) return;
    logoutError.value = apiMessage(cause);
    accountOpen.value = !!session.account;
    await nextTick();
    if (current()) document.getElementById('logout-button')?.focus();
  }
}
async function toggleAccount() {
  accountOpen.value = !accountOpen.value;
  if (accountOpen.value) {
    await nextTick();
    document.getElementById('logout-button')?.focus();
  }
}
function escape() {
  if (accountOpen.value) {
    accountOpen.value = false;
    accountButton.value?.focus();
  } else if (drawer.value) {
    void closeNavigation();
  } else if (searchOpen.value) closeSearch();
}
function trapDrawer(event: KeyboardEvent) {
  if (!drawer.value || event.key !== 'Tab') return;
  const elements = [
    ...(menu.value?.querySelectorAll<HTMLElement>('a,button') ?? []),
  ];
  const visible = elements.filter(
    (element) =>
      element.getClientRects().length && !element.hasAttribute('disabled'),
  );
  const first = visible[0];
  const last = visible.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
</script>
<template>
  <div
    class="app-shell"
    :class="{
      'app-shell--collapsed': collapsed,
      'app-shell--drawer': drawer,
      'app-shell--search': searchOpen,
      'app-shell--watch': watchLayout,
    }"
    @keydown.esc="escape"
  >
    <a class="skip-link" href="#main-content">Перейти к содержимому</a>
    <header
      class="masthead"
      :class="{ 'masthead--ambient': !!ambientColor }"
      :style="{ '--header-ambient': ambientColor || '0 0 0' }"
      :inert="drawer || undefined"
    >
      <div class="masthead__start">
        <button
          ref="menuButton"
          type="button"
          class="icon-button masthead__menu"
          aria-label="Меню навигации"
          :aria-expanded="
            drawer || (wideViewport && !collapsed && !watchLayout)
          "
          aria-controls="main-navigation"
          @click="toggleNavigation"
        >
          <AppIcon name="menu" /></button
        ><RouterLink
          to="/"
          class="masthead__brand"
          aria-label="YouTube — главная"
          ><BrandLogo
        /></RouterLink>
      </div>
      <form class="search-form" role="search" @submit.prevent="submitSearch">
        <button
          type="button"
          class="icon-button search-form__back"
          aria-label="Закрыть поиск"
          @click="closeSearch"
        >
          <AppIcon name="back" />
        </button>
        <div class="search-form__field">
          <input
            ref="searchInput"
            v-model="search"
            aria-label="Поиск"
            name="search_query"
            placeholder="Поиск"
            autocomplete="off"
            @input="searchInput?.setCustomValidity('')"
          /><button
            v-if="search"
            type="button"
            class="icon-button search-form__clear"
            aria-label="Очистить поиск"
            @click="
              search = '';
              searchInput?.focus();
            "
          >
            <AppIcon name="close" />
          </button>
        </div>
        <button class="search-form__submit" aria-label="Найти" type="submit">
          <AppIcon name="search" />
        </button>
      </form>
      <div class="masthead__end">
        <button
          ref="searchButton"
          class="icon-button masthead__search"
          type="button"
          aria-label="Открыть поиск"
          @click="openSearch"
        >
          <AppIcon name="search" />
        </button>
        <span
          v-if="session.loading || session.changing"
          class="account-skeleton"
          role="status"
          aria-label="Проверяем аккаунт"
        />
        <div v-else-if="session.error" class="session-error">
          <button class="text-link" type="button" @click="session.refresh()">
            Повторить вход
          </button>
        </div>
        <template v-else-if="session.account">
          <CreatePlaylist navigate public-header />
          <button
            ref="accountButton"
            class="avatar-button"
            type="button"
            aria-label="Меню аккаунта"
            :aria-expanded="accountOpen"
            aria-controls="account-menu"
            @click="toggleAccount"
          >
            {{ initial }}
          </button>
        </template>
        <RouterLink v-else :to="loginTo" class="sign-in"
          ><AppIcon name="user" /><span>Войти</span></RouterLink
        >
      </div>
    </header>
    <button
      v-if="accountOpen"
      class="dismiss-layer"
      type="button"
      tabindex="-1"
      aria-label="Закрыть меню аккаунта"
      @click="
        accountOpen = false;
        accountButton?.focus();
      "
    />
    <section
      v-if="accountOpen && session.account"
      id="account-menu"
      class="account-menu"
      aria-label="Аккаунт"
    >
      <div class="account-menu__identity">
        <span class="avatar-button" aria-hidden="true">{{ initial }}</span>
        <div>
          <strong>{{ session.account.name }}</strong><span>{{
            session.account.email
          }}</span>
        </div>
      </div>
      <RouterLink to="/studio/content/videos" class="account-menu__studio">
        <AppIcon name="video" />YouTube Studio
      </RouterLink>
      <RouterLink
        v-if="['moderator', 'admin'].includes(session.account.role)"
        to="/moderation"
        class="account-menu__moderation"
        ><AppIcon name="video" />Модерация</RouterLink
      >
      <RouterLink
        v-if="session.account.role === 'admin'"
        to="/admin/jobs"
        class="account-menu__jobs"
        ><AppIcon name="video" />Ошибки заданий</RouterLink
      >
      <button
        id="logout-button"
        type="button"
        :disabled="session.changing"
        @click="signOut"
      >
        <AppIcon name="logout" />Выйти
      </button>
      <p v-if="logoutError" class="form-error" role="alert">
        {{ logoutError }}
      </p>
    </section>
    <button
      v-if="drawer"
      class="drawer-backdrop"
      tabindex="-1"
      type="button"
      aria-label="Закрыть навигацию"
      @click="closeNavigation"
    />
    <aside
      id="main-navigation"
      ref="menu"
      class="navigation"
      :class="{ 'navigation--open': drawer }"
      :role="drawer ? 'dialog' : undefined"
      :aria-modal="drawer || undefined"
      aria-label="Навигация"
      @keydown="trapDrawer"
    >
      <div class="navigation__header">
        <button
          class="icon-button"
          type="button"
          aria-label="Закрыть навигацию"
          @click="closeNavigation"
        >
          <AppIcon name="menu" /></button
        ><RouterLink to="/" aria-label="На главную"><BrandLogo /></RouterLink>
      </div>
      <nav>
        <RouterLink
          to="/"
          class="navigation__item"
          :class="{ 'navigation__item--active': route.path === '/' }"
          :aria-current="route.path === '/' ? 'page' : undefined"
          ><AppIcon name="home" /><span>Главная</span></RouterLink
        >
        <div
          class="navigation__subscriptions"
          :class="{
            'navigation__subscriptions--extended':
              extendedNavigation && session.account,
          }"
        >
          <RouterLink
            to="/feed/subscriptions"
            class="navigation__item"
            :class="{
              'navigation__item--active': route.path === '/feed/subscriptions',
            }"
            :aria-current="
              route.path === '/feed/subscriptions' ? 'page' : undefined
            "
            ><AppIcon name="subscriptions" /><span>Подписки</span></RouterLink
          >
          <button
            v-if="extendedNavigation && session.account"
            type="button"
            class="icon-button navigation__channels-toggle"
            :aria-expanded="channelsOpen"
            aria-controls="subscribed-channel-list"
            aria-label="Каналы подписок"
            @click="channelsOpen = !channelsOpen"
          >
            <AppIcon name="down" />
          </button>
        </div>
        <SubscribedChannels
          v-if="extendedNavigation && channelsOpen && session.account"
          id="subscribed-channel-list"
          :key="session.scope"
        />
        <div class="navigation__personal">
          <p v-if="extendedNavigation" class="navigation__heading">Вы</p>
          <RouterLink
            v-for="item in [
              { path: '/feed/history', label: 'История', icon: 'history' },
              { path: '/feed/playlists', label: 'Плейлисты', icon: 'playlist' },
              { path: '/feed/liked', label: 'Понравившиеся', icon: 'like' },
            ] as const"
            :key="item.path"
            :to="item.path"
            class="navigation__item"
            :class="{ 'navigation__item--active': route.path === item.path }"
            :aria-current="route.path === item.path ? 'page' : undefined"
            ><AppIcon :name="item.icon" /><span>{{
              item.label
            }}</span></RouterLink
          >
        </div>
      </nav>
      <div
        v-if="!session.account && !session.loading"
        class="navigation__login"
      >
        <p>
          Войдите, чтобы ставить отметки «Нравится», писать комментарии и
          подписываться на каналы.
        </p>
        <RouterLink :to="loginTo" class="sign-in"
          ><AppIcon name="user" />Войти</RouterLink
        >
      </div>
    </aside>
    <div class="app-shell__content" :inert="drawer || searchOpen || undefined">
      <slot />
    </div>
    <button
      v-if="searchOpen"
      class="mobile-search-backdrop"
      aria-label="Закрыть поиск"
      type="button"
      tabindex="-1"
      @click="closeSearch"
    />
    <nav
      class="mobile-navigation"
      aria-label="Основная навигация"
      :inert="drawer || searchOpen || undefined"
    >
      <RouterLink to="/" :aria-current="route.path === '/' ? 'page' : undefined"
        ><AppIcon name="home" /><span>Главная</span></RouterLink
      ><RouterLink
        to="/feed/subscriptions"
        :aria-current="
          route.path === '/feed/subscriptions' ? 'page' : undefined
        "
        ><AppIcon name="subscriptions" /><span>Подписки</span></RouterLink
      ><button
        type="button"
        :aria-expanded="drawer"
        aria-controls="main-navigation"
        @click="toggleNavigation"
      >
        <AppIcon name="user" /><span>Вы</span></button
      ><RouterLink v-if="!session.account && !session.loading" :to="loginTo"
        ><AppIcon name="user" /><span>Войти</span></RouterLink
      >
    </nav>
  </div>
</template>
<style scoped lang="scss">
.masthead {
  height: var(--header-height);
  padding: 0 32px 0 16px;
  position: fixed;
  inset: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  background: var(--color-background);
  z-index: 30;
  &--ambient {
    background:
      linear-gradient(
        rgb(var(--header-ambient) / var(--watch-ambient-strength)),
        rgb(var(--header-ambient) / var(--watch-ambient-strength))
      ),
      var(--color-background);
  }
  &__start,
  &__end {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    gap: 16px;
  }
  &__start {
    min-width: 208px;
  }
  &__search {
    display: none;
  }
  &__brand {
    display: flex;
  }
}
.search-form {
  display: flex;
  height: 40px;
  position: absolute;
  left: 50%;
  transform: translateX(calc(-50% - 26px));
  width: min(600px, calc(100vw - 480px));
  &__field {
    border: 1px solid var(--color-border);
    border-radius: 24px 0 0 24px;
    background: var(--color-input);
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    padding-left: 16px;
    &:focus-within {
      border-color: var(--color-focus);
    }
  }
  input {
    color: var(--color-text);
    width: 100%;
    min-width: 0;
    background: transparent;
    border: 0;
    outline: 0;
    font: inherit;
    font-size: 16px;
    padding: 0;
  }
  &__submit {
    display: grid;
    place-items: center;
    width: 64px;
    flex-shrink: 0;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-left: 0;
    border-radius: 0 24px 24px 0;
    cursor: pointer;
  }
  &__back {
    display: none;
  }
  &__clear {
    flex-shrink: 0;
  }
}
.sign-in {
  border: 1px solid var(--color-border);
  border-radius: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--color-accent);
  height: 40px;
  padding: 0 12px;
  font-weight: 500;
  white-space: nowrap;
  &:hover {
    background: var(--color-accent-hover);
    border-color: transparent;
  }
}
.avatar-button,
.account-skeleton {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 0;
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  background: #7057a3;
  color: #fff;
  font-size: 18px;
}
.avatar-button {
  cursor: pointer;
}
.account-skeleton {
  background: var(--color-surface);
}
.navigation {
  position: fixed;
  top: var(--header-height);
  bottom: 0;
  left: 0;
  width: var(--sidebar-width);
  background: var(--color-background);
  padding: 12px 24px 12px 12px;
  overflow-y: auto;
  z-index: 25;
  &__header {
    display: none;
  }
  &__item {
    display: flex;
    align-items: center;
    height: 40px;
    gap: 24px;
    padding: 0 12px;
    border-radius: 10px;
    font-size: 14px;
    &--active {
      background: var(--color-surface);
      font-weight: 500;
    }
    &:hover {
      background: var(--color-hover);
    }
  }
  &__subscriptions {
    position: relative;
  }
  &__channels-toggle {
    position: absolute;
    right: 0;
    top: 0;
    width: 32px;
  }
  &__subscriptions--extended .navigation__item {
    padding-right: 32px;
  }
  &__heading {
    padding: 0 12px;
    font-size: 16px;
    font-weight: 500;
  }
  &__personal {
    border-top: 1px solid var(--color-border);
    margin-top: 12px;
    padding-top: 12px;
  }
  &__login {
    padding: 16px 32px;
    margin: 12px -14px 0 -12px;
    border-top: 1px solid var(--color-border);
    border-bottom: 1px solid var(--color-border);
    p {
      margin: 0 0 12px;
      font-size: 14px;
      line-height: 20px;
    }
  }
}
.app-shell__content {
  padding-top: var(--header-height);
  margin-left: var(--sidebar-width);
  min-height: 100dvh;
}
.app-shell--collapsed {
  .navigation {
    width: 72px;
    padding: 4px;
    &__item {
      height: 74px;
      flex-direction: column;
      gap: 6px;
      justify-content: center;
      padding: 0;
      font-size: 10px;
      background: transparent;
    }
    &__login {
      display: none;
    }
  }
  .app-shell__content {
    margin-left: 72px;
  }
}
.dismiss-layer,
.drawer-backdrop,
.mobile-search-backdrop {
  position: fixed;
  inset: 0;
  border: 0;
  background: transparent;
  z-index: 35;
}
.account-menu {
  position: fixed;
  top: 50px;
  right: 16px;
  width: 300px;
  max-width: calc(100vw - 32px);
  background: var(--color-menu);
  border-radius: 12px;
  box-shadow: 0 4px 32px #0006;
  z-index: 40;
  overflow: hidden;
  &__identity {
    display: flex;
    padding: 16px;
    gap: 16px;
    border-bottom: 1px solid var(--color-border);
    min-width: 0;
    div {
      min-width: 0;
    }
    strong,
    span:not(.avatar-button) {
      display: block;
      overflow-wrap: anywhere;
      font-size: 14px;
      line-height: 20px;
    }
  }
  > button,
  > a {
    border: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 16px;
    width: 100%;
    background: transparent;
    cursor: pointer;
    text-align: left;
    &:hover {
      background: var(--color-hover);
    }
  }
  .form-error {
    padding: 0 16px;
  }
}
.mobile-navigation {
  display: none;
}
@media (min-width: 768px) and (max-width: 1312px) {
  .navigation {
    width: 72px;
    padding: 4px;
    &__item {
      height: 74px;
      flex-direction: column;
      justify-content: center;
      gap: 6px;
      padding: 0;
      font-size: 10px;
      background: transparent;
    }
    &__login {
      display: none;
    }
  }
  .app-shell__content {
    margin-left: 72px;
  }
}
.navigation--open {
  inset: 0 auto 0 0;
  width: 240px !important;
  padding: 0 12px 12px !important;
  z-index: 55;
  display: block !important;
  .navigation__header {
    height: 56px;
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 12px;
  }
  .navigation__item {
    height: 40px;
    font-size: 14px;
    flex-direction: row;
    justify-content: flex-start;
    padding: 0 12px;
    gap: 24px;
  }
  .navigation__login {
    display: block;
  }
}
.drawer-backdrop {
  z-index: 50;
  background: #0008;
}
@media (max-width: 767px) {
  .masthead {
    padding: 0 12px;
    gap: 12px;
    &__start {
      min-width: 0;
    }
    &__menu {
      display: none;
    }
    &__search {
      display: grid;
    }
    &__end {
      gap: 8px;
    }
    .sign-in {
      display: none;
    }
  }
  .search-form {
    display: none;
  }
  .navigation {
    display: none;
  }
  .app-shell__content,
  .app-shell--collapsed .app-shell__content {
    margin-left: 0;
    padding-bottom: 56px;
  }
  .app-shell--search {
    .masthead__start,
    .masthead__end {
      display: none;
    }
    .search-form {
      display: flex;
      position: static;
      transform: none;
      width: 100%;
      flex: 1;
      gap: 0;
      height: 32px;
      &__back {
        display: grid;
        flex-shrink: 0;
        margin-right: 8px;
      }
      &__field {
        border: 0;
        background: var(--color-surface);
        border-radius: 20px 0 0 20px;
        padding-left: 12px;
      }
      &__submit {
        border: 0;
        width: 40px;
        border-radius: 0 20px 20px 0;
      }
    }
  }
  .mobile-search-backdrop {
    inset: var(--header-height) 0 0;
    z-index: 28;
    background: #0005;
  }
  .mobile-navigation {
    position: fixed;
    inset: auto 0 0;
    background: var(--color-background);
    border-top: 1px solid var(--color-border);
    z-index: 27;
    display: flex;
    justify-content: space-around;
    padding-bottom: env(safe-area-inset-bottom);
    a,
    button {
      border: 0;
      background: transparent;
      font: inherit;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      padding: 6px 12px;
      font-size: 10px;
      cursor: pointer;
    }
  }
}
.app-shell--watch {
  .navigation {
    display: none;
  }
  .navigation.navigation--open {
    display: block;
  }
  .app-shell__content {
    margin-left: 0;
    padding-bottom: 0;
  }
  .mobile-navigation {
    display: none;
  }
}
</style>
