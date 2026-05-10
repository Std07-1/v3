// src/lib/agentState.ts
// ADR-0069 — Agent state derivation + tier mapping.
//
// Pure helpers (no side effects, no DOM, no I/O). Imported by
// NarrativePanel.svelte and any future consumer that needs to map
// narrative + shell state to a discrete agent_state / mode tier.
//
// Resolution path per ADR-0069 §A: Hybrid Option 2 → Option 1.
//   - Frontend derives agent_state from existing wire fields TODAY.
//   - Backend MAY later add explicit `frame.smc.narrative.agent_state`.
//   - Consumers prefer explicit when present, fall back to derived.
//
// Tier mapping per ADR-0069 §107-120 (state→mode table) + §211 Slice 1
// note: Banner-tier states (watching/bias_confirmed) route to Expanded
// in Slice 1 until Banner mode lands in Slice 2.

import type { NarrativeBlock, ShellPayload, AgentState } from '../types';

/** Display mode tier — drives NarrativePanel render shape.
 *  1 = compact pill (~28px) · 2 = banner (~36px) · 3 = expanded (~180px) */
export type Tier = 1 | 2 | 3;

/** Display mode — Slice 2 will introduce 'banner'; Slice 1 only renders
 *  'compact' or 'expanded' (banner-tier states fall through to expanded). */
export type Mode = 'compact' | 'banner' | 'expanded';

/** Pure derivation per ADR-0069 §A resolution table.
 *  Reads ONLY narrative.{mode,sub_mode,bias_summary,scenarios[].trigger}
 *  and shell.stage. Returns 'awaiting_setup' as default fallback when
 *  no rule matches (covers the empty-narrative cold-load case). */
export function deriveAgentState(
    narrative: NarrativeBlock | null,
    shell: ShellPayload | null,
): AgentState {
    // 1. Hard signals from shell first (most authoritative).
    if (shell?.stage === 'triggered') return 'triggered';
    if (shell?.stage === 'ready') return 'ready';
    if (shell?.stage === 'prepare') return 'prepare';
    if (shell?.stage === 'stayout') return 'stay_out';

    // 2. Market closed marker from narrative.
    if (narrative?.sub_mode === 'market_closed') return 'market_closed';

    // 3. Wait-mode disambiguation by scenarios + bias presence.
    if (narrative?.mode === 'wait') {
        const scenarios = narrative.scenarios ?? [];
        const hasBias = !!(narrative.bias_summary && narrative.bias_summary.trim());
        const firstTrigger = scenarios[0]?.trigger;

        // bias_confirmed: explicit bias + first scenario approaching entry.
        if (hasBias && firstTrigger === 'approaching') return 'bias_confirmed';
        // watching: scenarios exist but no bias_summary committed yet.
        if (scenarios.length > 0 && !hasBias) return 'watching';
        // awaiting_setup: no scenarios at all.
        if (scenarios.length === 0) return 'awaiting_setup';
    }

    // 4. Default fallback (covers null narrative + unmatched trade-mode).
    return 'awaiting_setup';
}

/** Tier mapping per ADR-0069 §107-120 (state → default mode). */
export function tierOf(state: AgentState): Tier {
    switch (state) {
        case 'market_closed':
        case 'awaiting_setup':
        case 'stay_out':
            return 1; // Compact
        case 'watching':
        case 'bias_confirmed':
            return 2; // Banner
        case 'prepare':
        case 'ready':
        case 'triggered':
            return 3; // Expanded
    }
}

/** Mode resolution. Slice 1: tier 1 → compact, tier 2+3 → expanded
 *  (banner-tier states fall through until Slice 2 ships Banner render).
 *  Override (set by user click) wins per ADR §126-135 with B.2 escalation
 *  reset rule applied by caller (this function is purely structural). */
export function modeOf(state: AgentState, override: Mode | null): Mode {
    if (override) return override;
    const t = tierOf(state);
    if (t === 1) return 'compact';
    // Slice 1 fallback: tier 2 banner → expanded until Slice 2 lands.
    return 'expanded';
}

/** Numeric tier of a mode — used by override-vs-escalation logic
 *  (ADR §B.2: clear override when state's natural tier > lastSeenTier). */
export function tierOfMode(mode: Mode): Tier {
    if (mode === 'compact') return 1;
    if (mode === 'banner') return 2;
    return 3;
}

/** Short-display label for the agent_state badge (T4 mono caps).
 *  Returns empty string for `awaiting_setup` — narrative.headline already
 *  carries the message in Ukrainian; English badge was redundant noise.
 *  Returns '—' when state is null (unknown / not yet supplied). */
export function badgeLabel(state: AgentState | null): string {
    if (!state) return '—';
    switch (state) {
        case 'market_closed':
            return 'CLOSED';
        case 'awaiting_setup':
            return '';
        case 'stay_out':
            return 'STAY OUT';
        case 'watching':
            return 'WATCHING';
        case 'bias_confirmed':
            return 'BIAS';
        case 'prepare':
            return 'PREPARE';
        case 'ready':
            return 'READY';
        case 'triggered':
            return 'TRIGGERED';
    }
}

/** Resolve explicit agent_state on the wire if backend supplies it
 *  (ADR §A Option 1 forward path). Falls back to derivation otherwise. */
export function resolveAgentState(
    narrative: NarrativeBlock | null,
    shell: ShellPayload | null,
): AgentState {
    if (narrative?.agent_state) return narrative.agent_state;
    return deriveAgentState(narrative, shell);
}
