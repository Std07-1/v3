/**
 * authStore — реактивний сигнал «токен протух» (post-review fix, ADR-0088).
 *
 * Без нього 401 (ротація/зміна серверного archi_token) лишав консоль у вічному
 * failing-poll: банер «😴 офлайн» брехав про причину, token-gate не повертався,
 * єдиний вихід був DevTools localStorage clear. Тепер api.ts на 401 чистить токен
 * і піднімає цей сигнал → App.svelte показує token-gate знову (I5 loud).
 */

let authExpired = $state(false);

export function markAuthExpired(): void {
    authExpired = true;
}

export function resetAuthExpired(): void {
    authExpired = false;
}

export function isAuthExpired(): boolean {
    return authExpired;
}
