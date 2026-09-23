/**
 * publicSnapshot — єдине публічне джерело для трейдерської поверхні.
 *
 * Читає санітизований знімок з /api/public/snapshot (без токена — публічний контракт).
 * Це SSOT того, що дозволено бачити відвідувачу: presence (для кільця) + focus + армовані
 * рівні. Сирі /api/agent/state · /api/archi/directives сюди НЕ приходять (в проді вони
 * приватні; знімок будує платформа — окремий ADR). Frontend = dumb renderer (X28).
 */

export interface ArmedLevel {
    level: number;
    direction: string; // "above" | "below"
    role: string; // invalidation | entry | trigger | target | watch
    label: string; // людський підпис (звірений зі сценарієм на боці знімка)
}

export interface PublicFocus {
    symbol: string;
    direction?: "long" | "short" | string;
    thesis: string;
    session: string;
    grade: string;
    confidence_pct: number | null;
    bias: string;
    entry_zone: [number, number] | null;
    invalidation: number | null;
    targets: number[];
    status: string;
}

export interface PublicPresence {
    ts_ms?: number;
    mood: string;
    health: string;
    circuit_breaker: string;
    kill_switch_active: boolean;
    last_error: string;
    has_virtual_position: string;
    active_scenarios: number | string;
    has_active_scenario: boolean;
    next_wake_ms?: number;
    next_wake_reason: string;
    session: string;
}

export interface PublicSnapshot {
    generated_ms: number;
    presence: PublicPresence;
    focus: PublicFocus | null;
    watching: ArmedLevel[];
}

let snapshot = $state<PublicSnapshot | null>(null);
let lastSyncMs = $state(0);
let error = $state("");

let pollTimer: ReturnType<typeof setInterval> | null = null;
const POLL_INTERVAL_MS = 15_000;

export async function refreshSnapshot(): Promise<PublicSnapshot | null> {
    try {
        const res = await fetch("/api/public/snapshot", { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(`status ${res.status}`);
        snapshot = (await res.json()) as PublicSnapshot;
        lastSyncMs = Date.now();
        error = "";
        return snapshot;
    } catch (e) {
        error = e instanceof Error ? e.message : "snapshot fetch failed";
        return null;
    }
}

export function startSnapshotPolling(intervalMs = POLL_INTERVAL_MS): void {
    stopSnapshotPolling();
    refreshSnapshot();
    pollTimer = setInterval(refreshSnapshot, intervalMs);
}

export function stopSnapshotPolling(): void {
    if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

export function getSnapshot(): PublicSnapshot | null {
    return snapshot;
}

export function getSnapshotError(): string {
    return error;
}

export function getLastSnapshotSyncMs(): number {
    return lastSyncMs;
}
