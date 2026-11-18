import { onBeforeUnmount, watch } from 'vue';

// A write may finish after its component, account or resource has changed.
export function useOperations(identity: () => string) {
  const controllers = new Set<AbortController>();
  let disposed = false;
  function abort() {
    for (const controller of controllers) controller.abort();
    controllers.clear();
  }
  watch(identity, abort, { flush: 'sync' });
  onBeforeUnmount(() => {
    disposed = true;
    abort();
  });
  return (external?: AbortSignal) => {
    const controller = new AbortController(),
      key = identity();
    controllers.add(controller);
    const stop = () => controller.abort();
    external?.addEventListener('abort', stop, { once: true });
    if (external?.aborted || disposed) controller.abort();
    return {
      signal: controller.signal,
      current: () => !disposed && identity() === key,
      check: () => {
        if (disposed || controller.signal.aborted || identity() !== key)
          throw new DOMException('Aborted', 'AbortError');
      },
      finish: () => {
        external?.removeEventListener('abort', stop);
        controllers.delete(controller);
      },
    };
  };
}
