import { onBeforeUnmount } from 'vue';

// A failed preview can mean that a previously public video lost access.
export function useVideoCardVerification(
  refetch: () => Promise<unknown>,
  failed: () => boolean,
) {
  let disposed = false,
    checking = false,
    recheck = false;
  onBeforeUnmount(() => {
    disposed = true;
    recheck = false;
  });
  return async () => {
    if (disposed) return;
    recheck = true;
    if (checking) return;
    checking = true;
    try {
      do {
        recheck = false;
        await refetch();
      } while (recheck && !disposed && !failed());
    } finally {
      checking = false;
    }
  };
}
