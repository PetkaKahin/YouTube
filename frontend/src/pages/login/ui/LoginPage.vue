<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { safeReturnTo, useSession } from '../../../entities/session';
import { apiMessage } from '../../../shared/api';
import { BrandLogo } from '../../../shared/ui';

const route = useRoute();
const router = useRouter();
const session = useSession();
const email = ref('');
const password = ref('');
const pending = ref(false);
const error = ref('');
const errorElement = ref<HTMLElement>();
const alive = ref(true);
let revision = 0;
watch(
  () => route.fullPath,
  () => {
    revision++;
    password.value = '';
    error.value = '';
    pending.value = false;
  },
);
onBeforeUnmount(() => {
  alive.value = false;
  revision++;
  password.value = '';
});

async function submit() {
  if (!alive.value || pending.value || session.changing) return;
  const version = revision;
  const destination = safeReturnTo(route.query.returnTo);
  const current = () => alive.value && version === revision;
  pending.value = true;
  error.value = '';
  try {
    await session.signIn(email.value.trim(), password.value);
    if (current()) {
      password.value = '';
      await router.replace(destination);
    }
  } catch (cause) {
    if (current()) error.value = apiMessage(cause);
  } finally {
    if (current()) {
      pending.value = false;
      await nextTick();
      if (current() && error.value) errorElement.value?.focus();
    }
  }
}
</script>
<template>
  <main class="auth-page">
    <RouterLink to="/" class="auth-page__brand" aria-label="На главную"
      ><BrandLogo
    /></RouterLink>
    <section class="auth-card" aria-labelledby="login-title">
      <h1 id="login-title">Вход</h1>
      <p class="auth-card__intro">Войдите в аккаунт YouTube</p>
      <p v-if="route.query.registered === '1'" class="notice" role="status">
        Аккаунт создан. Теперь войдите с вашей почтой и паролем.
      </p>
      <form :aria-busy="pending" @submit.prevent="submit">
        <label class="field" for="login-email"
          >Электронная почта<input
            id="login-email"
            v-model="email"
            type="email"
            autocomplete="username"
            required
            :aria-describedby="error ? 'login-error' : undefined"
            :disabled="pending"
        /></label>
        <label class="field" for="login-password"
          >Пароль<input
            id="login-password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            :aria-describedby="error ? 'login-error' : undefined"
            :disabled="pending"
        /></label>
        <p
          v-if="error"
          id="login-error"
          ref="errorElement"
          class="form-error"
          role="alert"
          tabindex="-1"
        >
          {{ error }}
        </p>
        <div class="auth-card__actions">
          <RouterLink
            :to="{
              path: '/register',
              query: { returnTo: safeReturnTo(route.query.returnTo) },
            }"
            class="text-link"
            >Создать аккаунт</RouterLink
          ><button
            class="button button--primary"
            type="submit"
            :disabled="pending || session.changing"
          >
            {{ pending ? 'Входим…' : 'Войти' }}
          </button>
        </div>
      </form>
    </section>
  </main>
</template>
