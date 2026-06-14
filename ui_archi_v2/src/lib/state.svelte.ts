/**
 * Shared reactive state — single source of truth for directives + agentState.
 *
 * All views import from here instead of independently fetching api.directives() / api.agentState().
 * Uses Svelte 5 module-level $state runes for reactivity.
 */

import { api } from "./api";
import type { Directives, AgentState } from "./types";

// ── reactive atoms ──
let directives = $state<Directives | null>(null);
let agentState = $state<AgentState | null>(null);
let lastDirectivesSyncMs = $state(0);
let lastAgentStateSyncMs = $state(0);
let directivesError = $state("");
let agentStateError = $state("");

// ── polling internals ──
let pollTimer: ReturnType<typeof setInterval> | null = null;
const POLL_INTERVAL_MS = 30_000;

// ── refresh helpers ──

export async function refreshDirectives(brief = true): Promise<Directives | null> {
    try {
        const d = await api.directives(brief);
        directives = d;
        lastDirectivesSyncMs = Date.now();
        directivesError = "";
        return d;
    } catch {
        directivesError = "Directives fetch failed";
        return null;
    }
}

export async function refreshAgentState(): Promise<AgentState | null> {
    try {
        const s = await api.agentState();
        agentState = s;
        lastAgentStateSyncMs = Date.now();
        agentStateError = "";
        return s;
    } catch {
        agentStateError = "AgentState fetch failed";
        return null;
    }
}

export async function refreshAll(brief = true): Promise<void> {
    await Promise.all([refreshDirectives(brief), refreshAgentState()]);
}

// ── live apply (SSE push, no fetch) ──
// Фаза 3a (2026-06-13): the /api/archi/stream SSE pushes directives changes in
// real time, but the UI only polled (30s lag) + used SSE for background notifs.
// These let the live stream update state instantly so mood/думка/scenario stop
// lagging on the console while the owner is watching.

export function applyDirectives(d: Directives | null): void {
    if (!d) return;
    directives = d;
    lastDirectivesSyncMs = Date.now();
    directivesError = "";
}

export function applyAgentState(s: AgentState | null): void {
    if (!s) return;
    agentState = s;
    lastAgentStateSyncMs = Date.now();
    agentStateError = "";
}

// ── polling lifecycle ──

export function startPolling(intervalMs = POLL_INTERVAL_MS): void {
    stopPolling();
    refreshAll(false);
    pollTimer = setInterval(() => refreshAll(false), intervalMs);
}

export function stopPolling(): void {
    if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

// ── read-only exports ──

export function getDirectives(): Directives | null {
    return directives;
}

export function getAgentState(): AgentState | null {
    return agentState;
}

export function getLastDirectivesSyncMs(): number {
    return lastDirectivesSyncMs;
}

export function getLastAgentStateSyncMs(): number {
    return lastAgentStateSyncMs;
}

export function getDirectivesError(): string {
    return directivesError;
}

export function getAgentStateError(): string {
    return agentStateError;
}
