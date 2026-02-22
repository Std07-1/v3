// src/app/diagSelectors.ts
// Derived status selectors для DiagState SSOT.
// Шкала пріоритетів:
//   FRONTEND_ERROR → OFFLINE → EDGE_BLOCKED → WS_UNAVAILABLE → CONNECTING → STALLED → HEALTHY
// WS_UNAVAILABLE = primary коли ws down. Edge probe = secondary detail.

import { derived } from 'svelte/store';
import { diagStore } from './diagState';
import type { DiagStateData } from './diagState';

export const STALE_MS = 15_000;

export type MainStatus =
    | 'FRONTEND_ERROR'
    | 'OFFLINE'
    | 'EDGE_BLOCKED'
    | 'WS_UNAVAILABLE'
    | 'STALLED'
    | 'CONNECTING'
    | 'HEALTHY';

export interface StatusInfo {
    status: MainStatus;
    detail: string;
    critical: boolean; // overlay-worthy
}

/** Обчислити frame_age_ms з поточного часу */
export function computeFrameAge(d: DiagStateData, now: number): number | null {
    if (d.last_frame_received_ms == null) return null;
    return now - d.last_frame_received_ms;
}

/** Format edge probe info as secondary detail */
function probeDetail(d: DiagStateData): string {
    const ep = d.edge_probe;
    if (!ep) return 'HTTP probe: ще не було';
    if (ep.probe_network_err) return 'HTTP probe: network error';
    if (ep.probe_origin_down) return `HTTP probe: ${ep.status} (origin down)`;
    if (ep.probe_unknown) return `HTTP probe: ${ep.status} (endpoint missing)`;
    if (ep.probe_blocked) return `HTTP probe: ${ep.status} (blocked)`;
    if (ep.ok) return `HTTP probe: ${ep.status} (OK)`;
    return `HTTP probe: ${ep.status ?? '?'}`;
}

export function resolveStatus(d: DiagStateData, now: number): StatusInfo {
    // 1. Frontend error
    if (!d.fe_ok) {
        return {
            status: 'FRONTEND_ERROR',
            detail: d.fe_last_error?.message ?? 'unknown fe error',
            critical: true,
        };
    }

    // 2. Network offline
    if (d.net_offline) {
        return {
            status: 'OFFLINE',
            detail: 'navigator.onLine = false',
            critical: true,
        };
    }

    // 3-5: WS not open
    if (d.ws_state !== 'open') {
        const ep = d.edge_probe;

        // 3. CDN/WAF block — specific root cause
        if (ep?.probe_blocked) {
            return {
                status: 'EDGE_BLOCKED',
                detail: `HTTP ${ep.status}` + (ep.cf_ray ? ` cf_ray=${ep.cf_ray}` : ''),
                critical: true,
            };
        }

        // 4. WS_UNAVAILABLE — primary status, edge probe as secondary detail
        if (d.ws_state === 'closed' || d.ws_state === 'idle') {
            const lastClose = d.ws_last_close;
            const wsInfo = lastClose
                ? `ws close=${lastClose.code} attempt=${d.ws_reconnect_attempt}`
                : `ws_state=${d.ws_state} attempt=${d.ws_reconnect_attempt}`;
            return {
                status: 'WS_UNAVAILABLE',
                detail: `${wsInfo} | ${probeDetail(d)}`,
                critical: true,
            };
        }

        // 5. Connecting (transitional)
        // У reconnect loop (attempt>0) — все ще critical, щоб overlay не миготів
        return {
            status: 'CONNECTING',
            detail: `attempt=${d.ws_reconnect_attempt}`,
            critical: d.ws_reconnect_attempt > 0,
        };
    }

    // 6. Stalled (ws open but no frames)
    const age = computeFrameAge(d, now);
    if (age != null && age > STALE_MS) {
        return {
            status: 'STALLED',
            detail: `frame_age=${Math.round(age / 1000)}s > ${STALE_MS / 1000}s`,
            critical: true,
        };
    }

    // 7. Healthy
    return {
        status: 'HEALTHY',
        detail: age != null ? `frame_age=${Math.round(age / 1000)}s` : 'awaiting first frame',
        critical: false,
    };
}

/**
 * Derived store — оновлюється при зміні diagStore.
 * NOTE: frame_age потребує periodic tick для stale detection.
 * Компонент StatusBar має запускати setInterval(1s) для примусового re-derive.
 */
export const mainStatus = derived(diagStore, ($d) => resolveStatus($d, Date.now()));
