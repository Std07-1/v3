// src/app/edgeProbe.ts
// Edge probe: classify HTTP layer when WS disconnected.
// Strategy: probe ONCE on start, classify, then STOP.
// Re-probe only on: tab focus (visibilitychange), or explicit probeNow().
// Це мінімізує browser-native "GET 500" спам у консолі.
//
// SSOT класифікація відповідей:
//   2xx           → ok=true
//   404           → ok=false, probe_unknown=true
//   403/429/1020  → ok=false, probe_blocked=true
//   5xx/522/524   → ok=false, probe_origin_down=true
//   fetch threw   → ok=false, probe_network_err=true

import { diagStore } from './diagState';
import type { EdgeProbe } from './diagState';

// --- Config ---
const PROBE_URL: string = import.meta.env.VITE_EDGE_PROBE_URL ?? '/api/status';

const BLOCKED_STATUSES = new Set([403, 429, 1020]);
const ORIGIN_DOWN_STATUSES = new Set([500, 502, 503, 504, 522, 524]);

// --- State ---
let inflight = false;
let abortCtrl: AbortController | null = null;
let active = false;
let hasResult = false; // труе після першого успішного classify

// --- Probe ---
async function runProbe(): Promise<void> {
    if (!active || inflight) return;
    inflight = true;
    abortCtrl = new AbortController();
    try {
        const resp = await fetch(PROBE_URL, {
            cache: 'no-store',
            signal: abortCtrl.signal,
        });
        const cfRay = resp.headers.get('cf-ray') ?? undefined;
        const status = resp.status;
        const probe: EdgeProbe = {
            ok: resp.ok,
            status,
            cf_ray: cfRay,
            ts_ms: Date.now(),
        };
        if (status === 404) {
            probe.probe_unknown = true;
        } else if (BLOCKED_STATUSES.has(status)) {
            probe.probe_blocked = true;
        } else if (ORIGIN_DOWN_STATUSES.has(status)) {
            probe.probe_origin_down = true;
        }
        diagStore.setEdgeProbe(probe);
        if (!hasResult) {
            const tag = resp.ok ? 'OK' : `${status}`;
            console.log(`[EdgeProbe] ${PROBE_URL} → ${tag}`);
        }
        hasResult = true;
    } catch (err) {
        if ((err as Error).name === 'AbortError') {
            inflight = false;
            return;
        }
        if (!hasResult) {
            console.warn(`[EdgeProbe] ${PROBE_URL} → network error`);
        }
        hasResult = true;
        diagStore.setEdgeProbe({
            ok: false,
            probe_network_err: true,
            ts_ms: Date.now(),
        });
    } finally {
        inflight = false;
        abortCtrl = null;
        // Не плануємо наступний — probe-once strategy.
        // Re-probe тільки через visibilitychange або probeNow().
    }
}

// --- Visibility API ---
function onVisibility(): void {
    if (!active) return;
    if (!document.hidden && !inflight) {
        runProbe();
    }
}

// --- Public API ---
export function startEdgeProbe(): void {
    if (active) return;
    active = true;
    hasResult = false;
    if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', onVisibility);
    }
    runProbe();
}

export function stopEdgeProbe(): void {
    active = false;
    if (abortCtrl) {
        abortCtrl.abort();
        abortCtrl = null;
    }
    inflight = false;
    if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility);
    }
}

/** Пробований probe за запитом (кнопка "Перепідключити") */
export function probeNow(): void {
    if (active && !inflight) {
        hasResult = false; // дозволити 1 лог
        runProbe();
    }
}
