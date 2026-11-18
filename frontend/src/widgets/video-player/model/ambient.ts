import { onBeforeUnmount, watch, type Ref } from 'vue';

// Sample the existing decoder at most twice a second; never create another stream.
export function useAmbientLight(
  video: Ref<HTMLVideoElement | undefined>,
  enabled: () => boolean,
  changed: (color: string | undefined) => void,
) {
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let release: (() => void) | undefined;
  let canvas: HTMLCanvasElement | undefined;
  let visible = false;
  let blocked = false;
  let disposed = false;

  function cancel() {
    clearTimeout(timer);
    timer = undefined;
  }
  function clear() {
    cancel();
    changed(undefined);
  }
  function permitted() {
    return (
      !disposed && enabled() && !motion.matches && !document.hidden && visible
    );
  }
  function sample() {
    timer = undefined;
    const element = video.value;
    if (!permitted() || !element || blocked) return;
    if (
      element.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
      element.videoWidth > 0 &&
      element.videoHeight > 0
    ) {
      try {
        canvas ??= document.createElement('canvas');
        if (canvas.width !== 32 || canvas.height !== 18) {
          canvas.width = 32;
          canvas.height = 18;
        }
        const context = canvas.getContext('2d', { willReadFrequently: true });
        if (!context) {
          blocked = true;
          clear();
          return;
        }
        context.drawImage(element, 0, 0, 32, 18);
        const pixels = context.getImageData(0, 0, 32, 18).data;
        let red = 0,
          green = 0,
          blue = 0;
        for (let index = 0; index < pixels.length; index += 4) {
          red += pixels[index]!;
          green += pixels[index + 1]!;
          blue += pixels[index + 2]!;
        }
        const count = pixels.length / 4;
        changed(
          `${Math.round(red / count)} ${Math.round(green / count)} ${Math.round(blue / count)}`,
        );
        context.clearRect(0, 0, 32, 18);
      } catch {
        // Canvas decoding/security restrictions must never break playback.
        blocked = true;
        if (canvas) {
          canvas.width = 0;
          canvas.height = 0;
        }
        clear();
        return;
      }
    }
    if (!element.paused && !element.ended) timer = setTimeout(sample, 500);
  }
  function refresh() {
    cancel();
    if (!permitted()) {
      changed(undefined);
      return;
    }
    if (!blocked) timer = setTimeout(sample, 0);
  }
  function emptied() {
    blocked = false;
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
    clear();
  }
  const stop = watch(
    [video, enabled],
    ([element, active]) => {
      release?.();
      release = undefined;
      visible = false;
      blocked = false;
      clear();
      if (!element || !active || disposed) return;
      const events = [
        'loadeddata',
        'playing',
        'pause',
        'seeked',
        'ended',
      ] as const;
      for (const event of events) element.addEventListener(event, refresh);
      element.addEventListener('emptied', emptied);
      element.addEventListener('error', clear);
      const observer = new IntersectionObserver((entries) => {
        visible = entries.some((entry) => entry.isIntersecting);
        refresh();
      });
      observer.observe(element);
      release = () => {
        observer.disconnect();
        for (const event of events) element.removeEventListener(event, refresh);
        element.removeEventListener('emptied', emptied);
        element.removeEventListener('error', clear);
        clear();
      };
    },
    { flush: 'post', immediate: true },
  );
  document.addEventListener('visibilitychange', refresh);
  motion.addEventListener('change', refresh);
  onBeforeUnmount(() => {
    disposed = true;
    stop();
    release?.();
    document.removeEventListener('visibilitychange', refresh);
    motion.removeEventListener('change', refresh);
    clear();
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
      canvas = undefined;
    }
  });
}
