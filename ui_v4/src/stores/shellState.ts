/**
 * ADR-0041 §5a (Variant H) — Shell state helpers.
 * derivePdBadge(): pure function, frontend-only amber logic (C-DUMB).
 * Zero backend changes. Uses pd_state.label + narrative direction.
 */

import type { PdState } from '../types';

// ── EQ hysteresis band (PD-5 invariant) ──
const EQ_LOW = 45;
const EQ_HIGH = 55;

export type PdColorVariant = 'aligned-green' | 'aligned-red' | 'amber' | 'neutral';

export interface PdBadgeResult {
    label: string;        // "DISCOUNT 38%" | "PREMIUM 71%" | "EQ"
    percent: number;      // 0–100
    colorVariant: PdColorVariant;
}

/**
 * Derive P/D chip color based on pd_state + trade direction.
 *
 * ADR-0041 §5a directional coloring:
 *   DISCOUNT + long  → aligned-green (P/D confirms bias)
 *   PREMIUM  + short → aligned-red   (P/D confirms bias)
 *   PREMIUM  + long  → amber         (CONFLICT)
 *   DISCOUNT + short → amber         (CONFLICT)
 *   EQ (45–55%)      → neutral       (equilibrium, bias irrelevant)
 *   direction=null    → default by label (green/red, no amber)
 *
 * @param pdState   Wire pd_state from backend (null → null return)
 * @param direction Primary scenario direction: 'long' | 'short' | null
 */
export function derivePdBadge(
    pdState: PdState | null | undefined,
    direction: 'long' | 'short' | null | undefined,
): PdBadgeResult | null {
    if (!pdState) return null;

    const pct = pdState.pd_percent;

    // EQ hysteresis band (PD-5): 45–55% → neutral regardless of direction
    if (pct >= EQ_LOW && pct <= EQ_HIGH) {
        return { label: 'EQ', percent: pct, colorVariant: 'neutral' };
    }

    const pdLabel = pct < EQ_LOW ? 'DISCOUNT' : 'PREMIUM';
    const displayLabel = `${pdLabel} ${Math.round(pct)}%`;

    // No direction available (WAIT stage, no scenarios) → fallback VH-F1
    if (!direction) {
        return {
            label: displayLabel,
            percent: pct,
            colorVariant: pdLabel === 'DISCOUNT' ? 'aligned-green' : 'aligned-red',
        };
    }

    // Directional coloring (PD-4 invariant: amber = frontend-only)
    if (pdLabel === 'DISCOUNT' && direction === 'long') return { label: displayLabel, percent: pct, colorVariant: 'aligned-green' };
    if (pdLabel === 'PREMIUM' && direction === 'short') return { label: displayLabel, percent: pct, colorVariant: 'aligned-red' };
    if (pdLabel === 'PREMIUM' && direction === 'long') return { label: displayLabel, percent: pct, colorVariant: 'amber' };
    if (pdLabel === 'DISCOUNT' && direction === 'short') return { label: displayLabel, percent: pct, colorVariant: 'amber' };

    // Should not reach — but safe fallback
    return { label: displayLabel, percent: pct, colorVariant: 'neutral' };
}
