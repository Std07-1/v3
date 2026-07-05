// Shared UI setting: "Підказки" — whether hover hints are shown.
// Governs the drawing-tool labels (always-on when true, polite when false)
// AND the ~35 control tooltips (native `title`) across the chart chrome.
// The menu toggle lives in CommandRailOverflow (☰). Persisted; default OFF.
//
// Usage in markup: `import { hintsOn } from "../stores/uiHints";` then
// `title={$hintsOn ? "…" : undefined}` — `undefined` removes the attribute.
import { writable } from "svelte/store";

const KEY = "v4_show_hints";

function load(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

const store = writable<boolean>(load());

/** Read-only subscribable — use `$hintsOn` in markup. */
export const hintsOn = { subscribe: store.subscribe };

/** Flip the setting and persist. */
export function toggleHints(): void {
  store.update((v) => {
    const next = !v;
    try {
      localStorage.setItem(KEY, next ? "1" : "0");
    } catch {
      /* quota / private mode — silent */
    }
    return next;
  });
}
