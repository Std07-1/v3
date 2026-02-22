// src/ws/actions.ts
// SSOT: action creators для UI v4. Українські коментарі. Без silent fallback.

import { get } from 'svelte/store';
import type { WSConnection } from './connection';
import type { Drawing, T_MS, WsAction } from '../types';
import { serverConfig, addUiWarning } from '../app/frameRouter';

function toInt(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return n < 0 ? Math.ceil(n) : Math.floor(n);
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

export type Actions = ReturnType<typeof createActions>;

export function createActions(ws: WSConnection) {
  const send = (a: WsAction) => ws.sendAction(a);

  return {
    // ----- Core -----
    switchSymbolTf(symbol: string, tf: string) {
      // P2: guard — не надсилати switch з невалідним symbol/tf (degraded-but-loud)
      const cfg = get(serverConfig);
      if (cfg.symbols.length > 0 && !cfg.symbols.includes(symbol)) {
        addUiWarning('schema_mismatch', 'action',
          `switch blocked: symbol "${symbol}" not in server allowlist`);
        return;
      }
      if (cfg.tfs.length > 0 && !cfg.tfs.includes(tf)) {
        addUiWarning('schema_mismatch', 'action',
          `switch blocked: tf "${tf}" not in server allowlist`);
        return;
      }
      send({ action: 'switch', symbol, tf });
    },

    scrollback(to_ms: T_MS) {
      send({ action: 'scrollback', to_ms });
    },

    // ----- Overlays -----
    overlayToggle(layer: string, visible: boolean) {
      send({ action: 'overlay_toggle', layer, visible });
    },

    // ----- Drawings -----
    drawingAdd(drawing: Drawing) {
      send({ action: 'drawing_add', drawing });
    },

    drawingUpdate(drawing: Drawing) {
      send({ action: 'drawing_update', drawing });
    },

    drawingRemove(id: string) {
      send({ action: 'drawing_remove', id });
    },

    // ----- Replay -----
    replaySeek(to_ms: T_MS) {
      send({ action: 'replay_seek', to_ms });
    },

    replayStep(delta_bars: number) {
      // Rail: тільки цілі кроки, з адекватним clamp
      const step = clamp(toInt(delta_bars), -5000, 5000);
      if (step === 0) return;
      send({ action: 'replay_step', delta_bars: step });
    },

    replayPlay() {
      send({ action: 'replay_play' });
    },

    replayPause() {
      send({ action: 'replay_pause' });
    },

    replayExit() {
      send({ action: 'replay_exit' });
    },
  };
}