import { defineStore } from 'pinia';
import { onScopeDispose, ref } from 'vue';
import type { Identifier } from '../../../shared/api';

const prefix = 'youtube.guest-progress.v1:';
export const useGuestWatchProgress = defineStore('guest-watch-progress', () => {
  const revision = ref(0);
  let writtenId: Identifier | undefined,
    writtenAt = -Infinity;
  function position(id: Identifier) {
    void revision.value;
    try {
      const raw = localStorage.getItem(prefix + id);
      if (!raw || raw.length > 64) return 0;
      const value: unknown = JSON.parse(raw);
      return typeof value === 'number' && Number.isFinite(value) && value >= 0
        ? value
        : 0;
    } catch {
      return 0;
    }
  }
  function record(
    id: Identifier,
    value: number,
    duration: number,
    force = false,
  ) {
    if (
      !Number.isFinite(value) ||
      !Number.isFinite(duration) ||
      duration <= 0 ||
      value < 0
    )
      return;
    const now = performance.now();
    if (!force && id === writtenId && now - writtenAt < 5000) return;
    try {
      localStorage.setItem(
        prefix + id,
        JSON.stringify(Math.min(value, duration)),
      );
      writtenId = id;
      writtenAt = now;
      revision.value++;
    } catch {
      /* Playback remains available when browser storage is unavailable. */
    }
  }
  function remove(id: Identifier) {
    try {
      localStorage.removeItem(prefix + id);
    } catch {
      /* Storage may be disabled. */
    }
    if (writtenId === id) {
      writtenId = undefined;
      writtenAt = -Infinity;
    }
    revision.value++;
  }
  function changed(event: StorageEvent) {
    if (event.key === null || event.key.startsWith(prefix)) revision.value++;
  }
  window.addEventListener('storage', changed);
  onScopeDispose(() => window.removeEventListener('storage', changed));
  return { position, record, remove };
});
