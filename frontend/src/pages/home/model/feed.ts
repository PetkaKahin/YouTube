import { useInfiniteQuery } from '@tanstack/vue-query';
import { computed } from 'vue';
import { useSession } from '../../../entities/session';
import { validateCards } from '../../../entities/video';
import { feedV1VideosFeedGet, unwrap } from '../../../shared/api';
export function useFeed() {
  const session = useSession();
  return useInfiniteQuery({
    queryKey: computed(() => ['feed', session.scope]),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      validateCards(
        unwrap(
          await feedV1VideosFeedGet({ query: { cursor: pageParam }, signal }),
        ),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: false,
    gcTime: 0,
  });
}
