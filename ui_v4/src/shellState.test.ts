/**
 * ADR-0041 §5a — Unit tests for derivePdBadge().
 *
 * Покриття:
 *   aligned-green, aligned-red, amber (×2), neutral,
 *   EQ boundaries (44.9 / 45.0 / 55.0 / 55.1),
 *   VH-F1 fallback (direction=null).
 *
 * Запуск: npx vitest run  (потребує npm install --save-dev vitest)
 */

import { describe, it, expect } from 'vitest';
import { derivePdBadge } from './stores/shellState';
import type { PdState } from './types';

// ── helpers ──────────────────────────────────────────────────────────────
function pd(pd_percent: number): PdState {
    const label: PdState['label'] = pd_percent < 45 ? 'DISCOUNT' : pd_percent > 55 ? 'PREMIUM' : 'EQ';
    return { pd_percent, label, range_high: 100, range_low: 0, equilibrium: 50 };
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

// ── EQ boundaries (PD-5 invariant: 45–55 inclusive) ──────────────────────
describe('derivePdBadge — EQ boundary (PD-5)', () => {
    it('TC-06 EQ boundary low-side: 44.9 → DISCOUNT (не EQ)', () => {
        const r = derivePdBadge(pd(44.9), 'long');
        expect(r?.colorVariant).not.toBe('neutral');
        expect(r?.label).toContain('DISCOUNT');
    });

    it('TC-07 EQ boundary low-edge: 45.0 → EQ', () => {
        const r = derivePdBadge(pd(45.0), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });

    it('TC-08 EQ boundary high-edge: 55.0 → EQ', () => {
        const r = derivePdBadge(pd(55.0), 'long');
        expect(r?.colorVariant).toBe('neutral');
        expect(r?.label).toBe('EQ');
    });

    it('TC-09 EQ boundary high-side: 55.1 → PREMIUM (не EQ)', () => {
        const r = derivePdBadge(pd(55.1), 'short');
        expect(r?.colorVariant).not.toBe('neutral');
        expect(r?.label).toContain('PREMIUM');
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
