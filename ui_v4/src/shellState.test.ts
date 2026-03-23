/**
 * ADR-0041 §5a — Unit tests for derivePdBadge().
 *
 * G1 invariant: Frontend uses backend's label (SSOT).
 * derivePdBadge() does NOT re-classify — only adds directional coloring.
 *
 * Покриття:
 *   aligned-green, aligned-red, amber (×2), neutral,
 *   label-based EQ/PREMIUM/DISCOUNT (backend SSOT),
 *   VH-F1 fallback (direction=null).
 *
 * Запуск: npx vitest run  (потребує npm install --save-dev vitest)
 */

import { describe, it, expect } from 'vitest';
import { derivePdBadge } from './stores/shellState';
import type { PdState } from './types';

// ── helpers ──────────────────────────────────────────────────────────────
/** Build PdState with explicit label — simulates backend SSOT classification. */
function pd(pd_percent: number, label?: PdState['label']): PdState {
    const l = label ?? (pd_percent < 45 ? 'DISCOUNT' : pd_percent > 55 ? 'PREMIUM' : 'EQ');
    return { pd_percent, label: l, range_high: 100, range_low: 0, equilibrium: 50 };
}

// ── aligned-green / aligned-red ──────────────────────────────────────────
describe('derivePdBadge — directional coloring', () => {
    it('TC-01 aligned-green: DISCOUNT + long → aligned-green', () => {
        const r = derivePdBadge(pd(38), 'long');
        expect(r?.colorVariant).toBe('aligned-green');
        expect(r?.label).toContain('DISCOUNT');
    });

    it('TC-02 aligned-red: PREMIUM + short → aligned-red', () => {
        const r = derivePdBadge(pd(71), 'short');
        expect(r?.colorVariant).toBe('aligned-red');
        expect(r?.label).toContain('PREMIUM');
    });

    // ── amber ────────────────────────────────────────────────────────────
    it('TC-03 amber-1: PREMIUM + long → amber (conflict)', () => {
        const r = derivePdBadge(pd(72), 'long');
        expect(r?.colorVariant).toBe('amber');
    });

    it('TC-04 amber-2: DISCOUNT + short → amber (conflict)', () => {
        const r = derivePdBadge(pd(30), 'short');
        expect(r?.colorVariant).toBe('amber');
    });

    // ── neutral (EQ band) ────────────────────────────────────────────────
    it('TC-05 neutral: EQ band (50%) → neutral regardless of direction', () => {
        const r = derivePdBadge(pd(50), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });
});

// ── G1: Frontend trusts backend label (SSOT) ─────────────────────────────
describe('derivePdBadge — G1 backend label SSOT', () => {
    it('TC-06 backend says DISCOUNT → frontend shows DISCOUNT (no re-classify)', () => {
        const r = derivePdBadge(pd(44.9, 'DISCOUNT'), 'long');
        expect(r?.colorVariant).toBe('aligned-green');
        expect(r?.label).toContain('DISCOUNT');
    });

    it('TC-07 backend says EQ → frontend shows EQ (trusts label)', () => {
        const r = derivePdBadge(pd(45.0, 'EQ'), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });

    it('TC-08 backend says EQ at 55% → still EQ', () => {
        const r = derivePdBadge(pd(55.0, 'EQ'), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });

    it('TC-09 backend says PREMIUM → frontend shows PREMIUM', () => {
        const r = derivePdBadge(pd(55.1, 'PREMIUM'), 'short');
        expect(r?.colorVariant).toBe('aligned-red');
        expect(r?.label).toContain('PREMIUM');
    });

    it('TC-11 backend label is authoritative even if percent seems different', () => {
        // Backend says EQ for 49% (within backend's eq_low..eq_high)
        // Frontend must trust this — no re-classification
        const r = derivePdBadge(pd(49, 'EQ'), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });
});

// ── VH-F1 fallback: direction=null ────────────────────────────────────────
describe('derivePdBadge — VH-F1 fallback (direction=null)', () => {
    it('TC-10a no amber for DISCOUNT + null → aligned-green fallback', () => {
        const r = derivePdBadge(pd(30), null);
        expect(r?.colorVariant).toBe('aligned-green');
        expect(r?.colorVariant).not.toBe('amber');
    });

    it('TC-10b no amber for PREMIUM + null → aligned-red fallback', () => {
        const r = derivePdBadge(pd(72), null);
        expect(r?.colorVariant).toBe('aligned-red');
        expect(r?.colorVariant).not.toBe('amber');
    });

    it('TC-10c null pdState → returns null', () => {
        expect(derivePdBadge(null, 'long')).toBeNull();
        expect(derivePdBadge(undefined, 'long')).toBeNull();
    });
});
