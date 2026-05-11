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

/** Mode resolution per ADR-0069 Slice 2.
 *  Override (set by user click) wins per ADR §126-135 with B.2 escalation
 *  reset rule applied by caller (this function is purely structural). */
export function modeOf(state: AgentState, override: Mode | null): Mode {
    if (override) return override;
    const t = tierOf(state);
    if (t === 1) return 'compact';
    if (t === 2) return 'banner'; // Slice 2: watching / bias_confirmed → banner
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
 *  Returns "" for states whose narrative.headline OR HUD shell-stage pill
 *  already carries the message — duplicate badge would be redundant noise.
 *  Returns '—' when state is null (unknown / not yet supplied). */
export function badgeLabel(state: AgentState | null): string {
    if (!state) return '—';
    switch (state) {
        case 'market_closed':
            return ''; // ChartHud shell-stage pill + headline already say this
        case 'awaiting_setup':
            return ''; // headline + body already carry the state
        case 'stay_out':
            return ''; // ChartHud shell-stage "STAY OUT" pill is canonical
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

// ─── Compact pill text synthesis ──────────────────────────────────────
// Per user direction: pill content is a "шпаргалка" — pulled from system
// narrative (scenarios, levels, thesis) action-first. When narrative has
// nothing actionable (awaiting_setup / market_closed) — fall back to
// archi_presence.status (Ukrainian) so trader sees what Архi is doing
// instead of a dead "—" or generic "no setup".
//
// Important: this helper does NOT touch the системний наратив body content.
// It only chooses what to render in the compact pill (новий кут scope).

// ═══════════════════════════════════════════════════════════════════════════
//  🔒 LOCKED: PUBLIC-FACING agent pill text — NO "Арчі" mention 🔒
// ═══════════════════════════════════════════════════════════════════════════
//  Owner-direction 2026-05-11: brand "Арчі" = brand-internal scope only
//  (owner + sponsors). Public users (free + paying) see neutral one-word
//  English status: Sleeping / Watching / Analyzing / Alert / Off / Idle.
//
//  Дозволені правки:
//    - додавати нові wake-engine status keys (sync з wake_types.py)
//    - перекладати на інші мови ЯКЩО додаємо locale система (зараз EN-only)
//    - tweak status labels (Watching→Standby, etc) з owner approval
//  Заборонені правки без owner approval:
//    - додати "Арчі" / "Archi" / "Архи" у будь-який return string
//    - експонувати internal bot details (model name, Claude, OpenAI, etc)
//    - показувати raw silence_h без прозорого UX контексту
// ═══════════════════════════════════════════════════════════════════════════
//
// Wake-engine semantic mapping (per wake_types.py:144 + wake_engine.py:319-320):
//   "watching"  = wake conditions all met, armed for trigger
//   "sleeping"  = wake conditions not all met, agent dozing
//   "analyzing" = bot actively running Sonnet analysis call
//   "alert"     = urgent/triggered signal state
// Important: status alone does NOT mean bot is alive — that's gated by
// thesis presence + freshness in compactPillText below.
const _ARCHI_STATUS_EN: Record<string, string> = {
    sleeping: 'Sleeping',
    watching: 'Watching',
    analyzing: 'Analyzing',
    alert: 'Alert',
    active: 'Active', // legacy (older PresenceStatus values)
};

/** Build the compact pill one-liner. PURE agent-surface scope: pill text
 *  comes ONLY from archi_thesis (when bot wrote one) or archi_presence
 *  (when bot is alive but hasn't written yet) or offline indicator.
 *
 *  Системний наратив content (bias_summary, scenarios, warnings,
 *  fvg_context, next_area) is INTENTIONALLY NOT consulted here — those
 *  belong on different surfaces, not in this corner.
 *
 *  Returns single-word English status by default (owner-direction:
 *  public-facing text must not mention "Арчі"). Thesis text from bot
 *  passes through verbatim (it's strategic narrative, brand-neutral). */
export function compactPillText(
    narrative: NarrativeBlock | null,
    _state: AgentState, // kept in signature for caller compat; unused here
): string {
    if (!narrative) return 'Idle';

    const presence = narrative.archi_presence;
    const thesis = narrative.archi_thesis;
    // Liveness gate per ADR-0049 + narrative_enricher freshness rule.
    // Bot writes thesis to Redis on every Sonnet call — stale freshness
    // OR missing thesis means bot has been silent for too long.
    const botAlive = !!thesis && thesis.freshness !== 'stale';

    // 1. Bot alive AND has fresh thesis → pass through bot's narrative.
    //    Thesis text is brand-neutral strategic content (no "Арчі" inside).
    if (botAlive && thesis) {
        return thesis.thesis;
    }

    // 2. Bot offline (no thesis OR stale) → single-word "Off" + silence.
    if (presence && !botAlive) {
        const silence =
            presence.silence_h > 0
                ? ` · ${presence.silence_h.toFixed(1)}h`
                : '';
        return `Off${silence}`;
    }

    // 3. Bot alive but hasn't written thesis yet → single-word status.
    if (presence) {
        const statusEn = _ARCHI_STATUS_EN[presence.status] ?? presence.status;
        const focus = presence.focus ? ` · ${presence.focus}` : '';
        return `${statusEn}${focus}`;
    }

    // 4. Nothing shipped at all — neutral fallback so pill stays visible
    //    (was: empty → panel rendered as just an arrow → owner-reported
    //    "інколи пропадає" 2026-05-11).
    return 'Idle';
}
