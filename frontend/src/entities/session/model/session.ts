import { computed, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { defineStore } from 'pinia';
import {
  ApiError,
  isIdentifier,
  meV1AuthMeGet,
  loginV1AuthLoginPost,
  logoutV1AuthLogoutPost,
  unwrap,
  type AccountBody,
} from '../../../shared/api';

export const useSession = defineStore('session-ui', () => {
  const client = useQueryClient();
  const revision = ref(0);
  const authenticating = ref(false);
  const expiring = ref(false);
  const changing = computed(() => authenticating.value || expiring.value);
  const query = useQuery({
    queryKey: ['session'],
    queryFn: async ({ signal }) => {
      const result = await meV1AuthMeGet({ signal });
      if (result.response?.status === 401) return null;
      const account = unwrap(result);
      if (!isIdentifier(account.id))
        throw new ApiError(500, 'invalid_identifier');
      return account;
    },
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  const account = computed(() =>
    changing.value || query.isError.value ? null : (query.data.value ?? null),
  );
  const scope = computed(
    () => `${revision.value}:${account.value?.id ?? 'guest'}`,
  );

  async function reset() {
    const stale = new Set(
      client
        .getQueryCache()
        .getAll()
        .filter((item) => item.queryKey[0] !== 'session')
        .map((item) => item.queryHash),
    );
    revision.value++;
    const filter = {
      predicate: (item: { queryHash: string }) => stale.has(item.queryHash),
    };
    await client.cancelQueries(filter);
    client.removeQueries(filter);
  }

  watch(
    () => query.data.value?.id,
    (value, previous) => {
      if (value !== previous) void reset();
    },
    { flush: 'sync' },
  );
  watch(
    () => query.isError.value,
    (failed) => {
      if (failed) void reset();
    },
  );

  async function accept(value: AccountBody | null) {
    await reset();
    await client.cancelQueries({ queryKey: ['session'] });
    client.setQueryData(['session'], value);
  }

  async function signIn(email: string, password: string) {
    if (changing.value) return;
    authenticating.value = true;
    try {
      await client.cancelQueries({ queryKey: ['session'] });
      const account = unwrap(
        await loginV1AuthLoginPost({ body: { email, password } }),
      );
      if (!isIdentifier(account.id))
        throw new ApiError(500, 'invalid_identifier');
      await accept(account);
    } finally {
      authenticating.value = false;
    }
  }

  async function signOut() {
    if (changing.value) return;
    authenticating.value = true;
    try {
      await client.cancelQueries({ queryKey: ['session'] });
      const result = await logoutV1AuthLogoutPost();
      unwrap(result);
      if (result.response?.status !== 204)
        throw new Error('Logout not confirmed');
      await accept(null);
    } catch (cause) {
      const current = await query.refetch({ cancelRefetch: true });
      if (!current.isError && current.data === null) {
        await accept(null);
        return;
      }
      throw cause;
    } finally {
      authenticating.value = false;
    }
  }

  async function expire() {
    if (expiring.value) return;
    expiring.value = true;
    try {
      await accept(null);
    } finally {
      expiring.value = false;
    }
  }

  function refresh() {
    return query.refetch({ cancelRefetch: false });
  }

  return {
    account,
    scope,
    changing,
    loading: query.isPending,
    error: query.error,
    refresh,
    signIn,
    signOut,
    expire,
  };
});
