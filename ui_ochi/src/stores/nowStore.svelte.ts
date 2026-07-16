/**
 * nowStore — реактивний знімок «стан зараз» (/api/archi/now).
 *
 * Полінг кожні 10s (Svelte 5 module-level $state runes, як state.svelte.ts у
 * ui_archi_v2). Degraded-but-loud (I5): fetch fail не тихне — піднімає `offline`,
 * PresenceHeader показує банер «😴 Арчі спить / офлайн». offline = (немає даних
 * АБО fetch впав АБО now.stale прийшов true). stale домен рахує сервер (X28).
 */

import { api } from '../lib/api';
import type { NowResponse } from '../lib/types';

const POLL_INTERVAL_MS = 10_000;

let now = $state<NowResponse | null>(null);
let lastSyncMs = $state(0);
let fetchFailed = $state(false);
let errorCode = $state('');

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refreshNow(): Promise<void> {
    try {
        now = await api.now();
        lastSyncMs = Date.now();
        fetchFailed = false;
        errorCode = '';
    } catch (err) {
        // I5: не ковтаємо — піднімаємо offline + код для банера/діагностики.
        fetchFailed = true;
        errorCode = err instanceof Error ? err.message : 'now_fetch_failed';
    }
}

export function startNowPolling(intervalMs = POLL_INTERVAL_MS): void {
    stopNowPolling();
    void refreshNow();
    pollTimer = setInterval(() => void refreshNow(), intervalMs);
}

export function stopNowPolling(): void {
    if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

export function getNow(): NowResponse | null {
    return now;
}

export function getLastNowSyncMs(): number {
    return lastSyncMs;
}

export function getNowErrorCode(): string {
    return errorCode;
}

/** offline = немає знімка, АБО останній fetch впав, АБО сервер позначив stale. */
export function isOffline(): boolean {
    if (fetchFailed) return true;
    if (!now) return true;
    return now.stale === true;
}
