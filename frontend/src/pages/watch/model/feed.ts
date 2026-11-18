import { computed } from 'vue';
import { useInfiniteQuery } from '@tanstack/vue-query';
import { useSession } from '../../../entities/session';
import { validateCards } from '../../../entities/video';
import {
  feedV1VideosFeedGet,
  unwrap,
  type Identifier,
} from '../../../shared/api';

export function useWatchFeed(
  id: () => Identifier | undefined,
  enabled: () => boolean,
) {
  const session = useSession();
  return useInfiniteQuery({
    queryKey: computed(() => ['watch-feed', session.scope, id()]),
    enabled: computed(enabled),
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
