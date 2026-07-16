/**
 * API-клієнт «Очі Арчі». Bearer-токен з localStorage під тим самим ключем, що
 * ui_archi_v2 (`archi_token`) — обидві консолі шерять один токен (той самий
 * серверний auth-контур _archi_auth). Ендпоінти read-only: /api/archi/now,
 * /api/archi/wakes. Помилки — loud (кидаємо ApiError), стори вирішують degraded.
 */

import { markAuthExpired } from '../stores/authStore.svelte';

const TOKEN_KEY = 'archi_token';

export function getToken(): string {
    return localStorage.getItem(TOKEN_KEY) ?? '';
}

export function setToken(token: string): void {
    localStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearToken(): void {
    localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
    constructor(public status: number, public code: string) {
        super(`API error ${status}: ${code}`);
    }
}

async function apiFetch<T>(
    path: string,
    params?: Record<string, string | number>,
): Promise<T> {
    const token = getToken();
    const url = new URL(path, window.location.origin);
    if (params) {
        for (const [key, value] of Object.entries(params)) {
            url.searchParams.set(key, String(value));
        }
    }
    const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
    const res = await fetch(url.toString(), { headers });
    if (res.status === 401) {
        // Токен протух/ротований: чистимо і піднімаємо сигнал — App показує
        // token-gate знову, замість вічного failing-poll під брехливим «офлайн».
        clearToken();
        markAuthExpired();
        throw new ApiError(401, 'unauthorized');
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, (body as { error?: string }).error ?? 'request_failed');
    }
    return res.json();
}

export const api = {
    /** Presence «стан зараз»: price/stale/state/directives/thesis/armed/degraded. */
    now: (symbol = 'XAU/USD') =>
        apiFetch<import('./types').NowResponse>('/api/archi/now', { symbol }),

    /**
     * Кіноплівка пробуджень (keyset newest-first). before_ts → сторінка старіших
     * за курсор карток. limit сервер clamp'ить у 1..100.
     */
    wakes: (limit = 30, beforeTs?: number) =>
        apiFetch<import('./types').WakesResponse>(
            '/api/archi/wakes',
            beforeTs != null ? { limit, before_ts: beforeTs } : { limit },
        ),
};
