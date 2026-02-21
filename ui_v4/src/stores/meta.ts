// src/stores/meta.ts
import { writable } from 'svelte/store';
import type { UiWarning } from '../types';

type MetaState = {
  cursorPrice: number | null;
  uiWarnings: UiWarning[];
};

const initial: MetaState = {
  cursorPrice: null,
  uiWarnings: [],
};

function createMetaStore() {
  const { subscribe, update, set } = writable<MetaState>(initial);

  return {
    subscribe,

    setCursorPrice(price: number | null) {
      update((s) => ({ ...s, cursorPrice: price }));
    },

    addUiWarning(w: UiWarning) {
      update((s) => {
        // Rail: bounded list, без спаму
        const next = [w, ...s.uiWarnings].slice(0, 50);
        return { ...s, uiWarnings: next };
      });
    },

    resetUiWarnings() {
      update((s) => ({ ...s, uiWarnings: [] }));
    },

    resetAll() {
      set(initial);
    },
  };
}

export const metaStore = createMetaStore();