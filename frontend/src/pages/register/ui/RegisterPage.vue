<script setup lang="ts">
import { nextTick, onBeforeUnmount, reactive, ref } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import { safeReturnTo } from '../../../entities/session';
import {
  ApiError,
  apiMessage,
  registerV1AuthRegisterPost,
  unwrap,
} from '../../../shared/api';
import { BrandLogo } from '../../../shared/ui';

const router = useRouter();
const route = useRoute();
const form = reactive({
  name: '',
  email: '',
  tag: '',
  password: '',
  password_confirmation: '',
});
const pending = ref(false);
const error = ref('');
const fields = ref<Record<string, string>>({});
const alive = ref(true);
const inputs = [
  {
    name: 'name',
    label: 'Имя',
    type: 'text',
    autocomplete: 'name',
    hint: 'От 2 до 50 символов: кириллица, латиница и пробелы.',
  },
  {
    name: 'email',
    label: 'Электронная почта',
    type: 'email',
    autocomplete: 'email',
    hint: '',
  },
  {
    name: 'tag',
    label: 'Тег канала',
    type: 'text',
    autocomplete: 'off',
    hint: '3–30 символов: латиница, цифры, точка, дефис и подчёркивание.',
  },
  {
    name: 'password',
    label: 'Пароль',
    type: 'password',
    autocomplete: 'new-password',
    hint: 'От 6 до 128 символов. Пробелы разрешены.',
  },
  {
    name: 'password_confirmation',
    label: 'Повтор пароля',
    type: 'password',
    autocomplete: 'new-password',
    hint: '',
  },
] as const;

function fieldMessage(code: string) {
  if (code === 'already_taken') return 'Это значение уже используется.';
  if (code === 'password_mismatch') return 'Пароли не совпадают.';
  if (code === 'invalid_length') return 'Проверьте длину значения.';
  return 'Проверьте формат значения.';
}

onBeforeUnmount(() => {
  alive.value = false;
  form.password = '';
  form.password_confirmation = '';
});
async function submit() {
  if (pending.value) return;
  error.value = '';
  fields.value = {};
  const name = form.name.trim();
  if ([...name].length < 2 || [...name].length > 50)
    fields.value.name = 'Введите от 2 до 50 символов.';
  else if (
    [...name].some(
      (character) =>
        character !== ' ' &&
        !/[A-Za-z]/.test(character) &&
        !(
          character >= '\u0400' &&
          character <= '\u052f' &&
          /\p{L}/u.test(character)
        ),
    )
  )
    fields.value.name = 'Используйте кириллицу, латиницу и пробелы.';
  if (!/^[A-Za-z0-9_.-]{3,30}$/.test(form.tag.replace(/^@/, '')))
    fields.value.tag = 'Проверьте формат тега: 3–30 символов.';
  if ([...form.password].length < 6 || [...form.password].length > 128)
    fields.value.password = 'Введите от 6 до 128 символов.';
  if (form.password !== form.password_confirmation) {
    fields.value.password_confirmation = 'Пароли не совпадают.';
  }
  if (Object.keys(fields.value).length) {
    await nextTick();
    document
      .getElementById(
        `register-${inputs.find((input) => fields.value[input.name])?.name}`,
      )
      ?.focus();
    return;
  }
  pending.value = true;
  try {
    unwrap(
      await registerV1AuthRegisterPost({
        body: {
          ...form,
          name: form.name.trim(),
          email: form.email.trim(),
          tag: form.tag,
        },
      }),
    );
    form.password = '';
    form.password_confirmation = '';
    if (alive.value)
      await router.replace({
        path: '/login',
        query: {
          registered: '1',
          returnTo: safeReturnTo(route.query.returnTo),
        },
      });
  } catch (cause) {
    if (!alive.value) return;
    if (cause instanceof ApiError) {
      for (const field of cause.fields) {
        const name = field.location.at(-1);
        if (
          typeof name === 'string' &&
          inputs.some((input) => input.name === name)
        )
          fields.value[name] = fieldMessage(field.code);
      }
    }
    error.value = apiMessage(cause);
  } finally {
    pending.value = false;
    if (alive.value && Object.keys(fields.value).length) {
      await nextTick();
      document
        .getElementById(
          `register-${inputs.find((input) => fields.value[input.name])?.name}`,
        )
        ?.focus();
    }
  }
}
</script>
<template>
  <main class="auth-page">
    <RouterLink to="/" class="auth-page__brand" aria-label="На главную"
      ><BrandLogo
    /></RouterLink>
    <section class="auth-card" aria-labelledby="register-title">
      <h1 id="register-title">Создание аккаунта</h1>
      <p class="auth-card__intro">Ваш канал будет создан автоматически</p>
      <form :aria-busy="pending" @submit.prevent="submit">
        <label
          v-for="input in inputs"
          :key="input.name"
          class="field"
          :for="`register-${input.name}`"
          >{{ input.label }}
          <input
            :id="`register-${input.name}`"
            v-model="form[input.name]"
            :name="input.name"
            :type="input.type"
            :autocomplete="input.autocomplete"
            required
            :disabled="pending"
            :aria-invalid="!!fields[input.name]"
            :aria-describedby="`hint-${input.name}`"
          />
          <span
            :id="`hint-${input.name}`"
            :class="fields[input.name] ? 'field__error' : 'field__hint'"
            >{{ fields[input.name] || input.hint }}</span
          >
        </label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="auth-card__actions">
          <RouterLink
            :to="{
              path: '/login',
              query: { returnTo: safeReturnTo(route.query.returnTo) },
            }"
            class="text-link"
            >Уже есть аккаунт</RouterLink
          ><button
            class="button button--primary"
            type="submit"
            :disabled="pending"
          >
            {{ pending ? 'Создаём…' : 'Создать аккаунт' }}
          </button>
        </div>
      </form>
    </section>
  </main>
</template>
