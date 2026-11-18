import type { InjectionKey } from 'vue';

export const watchAppearance: InjectionKey<{
  set: (owner: symbol, color: string | undefined) => void;
  clear: (owner: symbol) => void;
}> = Symbol('watch-appearance');
