// src/app/diagState.ts
// SSOT DiagState — єдине джерело правди діагностики UI v4.
// Без silent fallback. Усі стани мають бути явними.

import { writable, derived, get } from 'svelte/store';

// -------------------- Types --------------------

export interface WsCloseInfo {
    code: number;
    reason: string;
    wasClean: boolean;
    ts_ms: number;
}

export interface FeError {
    message: string;
    stack?: string;
    ts_ms: number;
}

export interface EdgeProbe {
    ok: boolean;             // true = 2xx
    status?: number;         // HTTP status code (absent on network error)
    cf_ray?: string;         // Cloudflare ray ID (CDN present)
    ts_ms: number;
    // SSOT classification flags — set by edgeProbe.ts, consumed by diagSelectors
    probe_unknown?: boolean;      // 404 — endpoint missing, but server is responding
    probe_blocked?: boolean;      // 403/429/1020 — CDN/WAF access block → EDGE_BLOCKED
    probe_origin_down?: boolean;  // 5xx/522/524 — origin unreachable or timed out → WS_UNAVAILABLE (detail)
    probe_network_err?: boolean;  // fetch threw (network error) — no connectivity → WS_UNAVAILABLE (detail)
}

export type WsState = 'idle' | 'connecting' | 'open' | 'closing' | 'closed';

export interface DiagStateData {
    // Frontend
    fe_ok: boolean;
    fe_last_error: FeError | null;

    // WS Transport
    ws_state: WsState;
    ws_last_open_ms: number | null;
    ws_last_msg_ms: number | null;
    ws_last_close: WsCloseInfo | null;
    ws_reconnect_attempt: number;
    ws_last_error: string | null;

    // Backend freshness
    last_frame_seq: number | null;
    last_frame_server_ts_ms: number | null;
    last_frame_received_ms: number | null;

    // Edge/Network
    net_offline: boolean;
    edge_probe: EdgeProbe | null;
}

// -------------------- Initial --------------------

const INITIAL: DiagStateData = {
    fe_ok: true,
    fe_last_error: null,
    ws_state: 'idle',
    ws_last_open_ms: null,
    ws_last_msg_ms: null,
    ws_last_close: null,
    ws_reconnect_attempt: 0,
    ws_last_error: null,
    last_frame_seq: null,
    last_frame_server_ts_ms: null,
    last_frame_received_ms: null,
    net_offline: typeof navigator !== 'undefined' ? !navigator.onLine : false,
    edge_probe: null,
};

// -------------------- Store --------------------

function createDiagStore() {
    const store = writable<DiagStateData>({ ...INITIAL });
    const { subscribe, update } = store;

    return {
        subscribe,

        // --- Frontend ---
        setFeError(err: FeError) {
            update(s => ({ ...s, fe_ok: false, fe_last_error: err }));
        },
        clearFeError() {
            update(s => ({ ...s, fe_ok: true, fe_last_error: null }));
        },

        // --- WS ---
        setWsState(state: WsState) {
            update(s => ({ ...s, ws_state: state }));
        },
        onWsOpen() {
            update(s => ({
                ...s,
                ws_state: 'open',
                ws_last_open_ms: Date.now(),
                ws_last_error: null,
            }));
        },
        onWsMessage() {
            update(s => ({ ...s, ws_last_msg_ms: Date.now() }));
        },
        onWsClose(info: WsCloseInfo) {
            update(s => ({
                ...s,
                ws_state: 'closed',
                ws_last_close: info,
                ws_reconnect_attempt: s.ws_reconnect_attempt + 1,
            }));
        },
        onWsError(msg: string) {
            update(s => ({ ...s, ws_last_error: msg }));
        },
        resetReconnectAttempt() {
            update(s => ({ ...s, ws_reconnect_attempt: 0 }));
        },

        // --- Frame freshness ---
        onValidFrame(seq: number, serverTs: number) {
            update(s => ({
                ...s,
                last_frame_seq: seq,
                last_frame_server_ts_ms: serverTs,
                last_frame_received_ms: Date.now(),
            }));
        },

        // --- Network ---
        setNetOffline(offline: boolean) {
            update(s => ({ ...s, net_offline: offline }));
        },
        setEdgeProbe(probe: EdgeProbe) {
            update(s => ({ ...s, edge_probe: probe }));
        },

        // --- Reset ---
        reset() {
            store.set({ ...INITIAL });
        },

        /** Snapshot для console/debug */
        snapshot(): DiagStateData {
            return get(store);
        },
    };
}

export const diagStore = createDiagStore();
