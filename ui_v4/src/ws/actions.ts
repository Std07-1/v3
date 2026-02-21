// src/ws/actions.ts
// SSOT: action creators для UI v4. Українські коментарі. Без silent fallback.

import type { WSConnection } from './connection';
import type { Drawing, T_MS, WsAction } from '../types';

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
      // Rail: символ/TF мають бути явними, без авто-нормалізації тут
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