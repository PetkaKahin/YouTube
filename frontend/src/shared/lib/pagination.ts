import { onBeforeUnmount, watch, type Ref } from 'vue';
export function useNextPage(
  target: Ref<HTMLElement | undefined>,
  load: () => void,
) {
  let observer: IntersectionObserver | undefined;
  watch(target, (value) => {
    observer?.disconnect();
    if (value) {
      observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) load();
        },
        { rootMargin: '300px' },
      );
      observer.observe(value);
    }
  });
  onBeforeUnmount(() => observer?.disconnect());
}
