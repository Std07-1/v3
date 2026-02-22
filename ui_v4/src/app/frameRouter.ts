// src/app/frameRouter.ts
// SSOT handler WS кадрів. Єдиний entry-point для всіх RenderFrame з WS.
// Guards: schema_v, seq monotonicity. Splits: serverWarnings vs uiWarnings.
// Без silent fallback — кожен drop/guard видимий.

import { writable, get } from 'svelte/store';
import type { RenderFrame, UiWarning } from '../types';
import { diagStore } from './diagState';

const SUPPORTED_SCHEMA = 'ui_v4_v2';

// -------------------- Stores --------------------

export const currentFrame = writable<RenderFrame | null>(null);
export const serverWarnings = writable<string[]>([]);
export const uiWarnings = writable<UiWarning[]>([]);

/** P2: SSOT конфіг символів/TF з сервера. Заповнюється при full frame. */
export interface ServerConfig {
  symbols: string[];
  tfs: string[];
}
export const serverConfig = writable<ServerConfig>({ symbols: [], tfs: [] });

// -------------------- State --------------------

let lastSeq = -1;
let knownBootId: string | null = null;

/** Exported for actions.ts guard (P2). */
export function addUiWarning(code: UiWarning['code'], kind: UiWarning['kind'], details: string): void {
  const w: UiWarning = {
    code,
    kind,
    id: `${code}_${Date.now()}`,
    details,
  };
  uiWarnings.update(arr => [w, ...arr].slice(0, 50));
}

// -------------------- Main handler --------------------

export function handleWSFrame(raw: unknown): void {
  // Parse guard
  if (raw == null || typeof raw !== 'object') {
    addUiWarning('schema_mismatch', 'router', 'frame is not an object');
    return;
  }
  const frame = raw as RenderFrame;

  // 1. schema_v guard
  if (!frame.meta || frame.meta.schema_v !== SUPPORTED_SCHEMA) {
    const got = (frame as any)?.meta?.schema_v ?? 'missing';
    addUiWarning('schema_mismatch', 'router', `unsupported schema_v: ${got}, expected ${SUPPORTED_SCHEMA}`);
    return;
  }

  // 2. seq monotonic guard
  if (frame.meta.seq <= lastSeq) {
    addUiWarning('stale_frame', 'router', `stale seq ${frame.meta.seq} <= ${lastSeq}`);
    return;
  }
  lastSeq = frame.meta.seq;

  // 3. Оновити DiagState (frame freshness)
  diagStore.onValidFrame(frame.meta.seq, frame.meta.server_ts_ms);

  // 4. Warning split
  serverWarnings.set(frame.meta.warnings ?? []);

  // 5. Очищення uiWarnings при boot/switch (full frame)
  if (frame.frame_type === 'full') {
    uiWarnings.set([]);
    lastSeq = frame.meta.seq; // reset baseline

    // P2: populate serverConfig SSOT
    const cfg = (frame.meta as any).config;
    if (cfg && Array.isArray(cfg.symbols) && Array.isArray(cfg.tfs)) {
      serverConfig.set({ symbols: cfg.symbols, tfs: cfg.tfs });
    }
  }

  // 5b. boot_id guard — server restart detection (I4/P1)
  const frameBootId = (frame.meta as any).boot_id as string | undefined;
  if (frameBootId) {
    if (knownBootId === null) {
      knownBootId = frameBootId;
    } else if (frameBootId !== knownBootId) {
      // Сервер перезапустився — потрібен повний reload
      addUiWarning('schema_mismatch', 'router',
        `boot_id changed: ${knownBootId} → ${frameBootId}, server restarted`);
      knownBootId = frameBootId;
      // full frame автоматично прийде — але попередній стан може бути stale
    }
  }

  // 5c. Config frame — policy bridge (T8/S24)
  if (frame.frame_type === 'config') {
    const ccfg = (frame as any).config;
    if (ccfg && Array.isArray(ccfg.symbols) && Array.isArray(ccfg.tfs)) {
      serverConfig.set({ symbols: ccfg.symbols, tfs: ccfg.tfs });
    }
    return; // config frame не потрапляє в currentFrame
  }

  // 6. Dispatch (тільки валідні frame_type)
  const validTypes = new Set(['full', 'delta', 'scrollback', 'drawing_ack', 'replay', 'warming', 'heartbeat', 'config']);
  if (validTypes.has(frame.frame_type)) {
    // heartbeat не оновлює currentFrame — тільки DiagState
    if (frame.frame_type !== 'heartbeat') {
      currentFrame.set(frame);
    }
  } else {
    // P1: unknown frame_type → degraded-but-loud (не silent drop)
    addUiWarning('schema_mismatch', 'router', `unknown frame_type: ${frame.frame_type}`);
  }
}

/** Reset при reconnect / symbol switch */
export function resetFrameRouter(): void {
  lastSeq = -1;
  knownBootId = null;
  currentFrame.set(null);
  serverWarnings.set([]);
  uiWarnings.set([]);
  serverConfig.set({ symbols: [], tfs: [] });
}
