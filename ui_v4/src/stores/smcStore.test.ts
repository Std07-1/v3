/**
 * ADR-0042 P1 — applySmcDelta() field parity tests.
 *
 * Інваріант DF-1: applySmcDelta() зберігає всі 8 полів SmcData.
 * zone_grades та pd_state НЕ повинні зникати після delta-оновлення.
 *
 * Запуск: npx vitest run src/stores/smcStore.test.ts
 */

import { describe, it, expect } from 'vitest';
import { applySmcDelta, applySmcFull, EMPTY_SMC_DATA } from './smcStore';
import type { SmcData, SmcDeltaWire, SmcZone, ZoneGradeInfo, PdState } from '../types';

// ── fixtures ─────────────────────────────────────────────────

const ZONE_OB: SmcZone = {
    id: 'ob_bull_XAU_USD_900_1000',
    start_ms: 1000,
    end_ms: 2000,
    high: 2050,
    low: 2040,
    kind: 'ob_bull',
    status: 'active',
};

const ZONE_FVG: SmcZone = {
    id: 'fvg_bull_XAU_USD_900_3000',
    start_ms: 3000,
    end_ms: 4000,
    high: 2060,
    low: 2055,
    kind: 'fvg_bull',
    status: 'active',
};

const GRADES: Record<string, ZoneGradeInfo> = {
    [ZONE_OB.id]: { score: 8, grade: 'A+', factors: ['sweep +2', 'htf_align +2', 'session +2', 'fvg_nearby +2'] },
    [ZONE_FVG.id]: { score: 4, grade: 'B', factors: ['sweep +2', 'fvg_nearby +2'] },
};

const PD: PdState = { pd_percent: 35, label: 'DISCOUNT', range_high: 2100, range_low: 2000, equilibrium: 2050 };

const BIAS_MAP: Record<string, string> = { '900': 'bullish', '3600': 'bearish' };
const MOMENTUM_MAP: Record<string, { b: number; r: number }> = { '900': { b: 3, r: 1 } };

function makeFullState(): SmcData {
    return applySmcFull(
        [ZONE_OB, ZONE_FVG],
        [{ id: 'sw1', kind: 'hl', time_ms: 1500, price: 2045 }],
        [{ id: 'pdh', kind: 'pdh', price: 2090 }],
        'bullish',
        GRADES,
        BIAS_MAP,
        MOMENTUM_MAP,
        PD,
    );
}

function emptyDelta(): SmcDeltaWire {
    return {
        new_zones: [],
        mitigated_zone_ids: [],
        updated_zones: [],
        new_swings: [],
        new_levels: [],
        removed_level_ids: [],
        trend_bias: null,
    };
}

// ── DF-1: applySmcDelta preserves all 8 SmcData fields ──────

describe('ADR-0042 DF-1: applySmcDelta field parity', () => {
    it('TC-01: empty delta preserves zone_grades from current state', () => {
        const state = makeFullState();
        const result = applySmcDelta(state, emptyDelta());

        expect(result.zone_grades).toEqual(GRADES);
    });

    it('TC-02: empty delta preserves pd_state from current state', () => {
        const state = makeFullState();
        const result = applySmcDelta(state, emptyDelta());

        expect(result.pd_state).toEqual(PD);
    });

    it('TC-03: empty delta preserves bias_map from current state', () => {
        const state = makeFullState();
        const result = applySmcDelta(state, emptyDelta());

        expect(result.bias_map).toEqual(BIAS_MAP);
    });

    it('TC-04: empty delta preserves momentum_map from current state', () => {
        const state = makeFullState();
        const result = applySmcDelta(state, emptyDelta());

        expect(result.momentum_map).toEqual(MOMENTUM_MAP);
    });

    it('TC-05: delta with new_zones still preserves zone_grades', () => {
        const state = makeFullState();
        const newZone: SmcZone = { id: 'ob_bear_test', start_ms: 5000, high: 2070, low: 2065, kind: 'ob_bear', status: 'active' };
        const delta: SmcDeltaWire = { ...emptyDelta(), new_zones: [newZone] };

        const result = applySmcDelta(state, delta);

        expect(result.zones).toHaveLength(3);
        expect(result.zone_grades).toEqual(GRADES);
        expect(result.pd_state).toEqual(PD);
    });

    it('TC-06: delta with mitigated_zone_ids still preserves pd_state', () => {
        const state = makeFullState();
        const delta: SmcDeltaWire = { ...emptyDelta(), mitigated_zone_ids: [ZONE_OB.id] };

        const result = applySmcDelta(state, delta);

        expect(result.zones.find(z => z.id === ZONE_OB.id)?.status).toBe('mitigated');
        expect(result.pd_state).toEqual(PD);
        expect(result.zone_grades).toEqual(GRADES);
    });

    it('TC-07: result has exactly 8 SmcData keys', () => {
        const state = makeFullState();
        const result = applySmcDelta(state, emptyDelta());

        const keys = Object.keys(result).sort();
        expect(keys).toEqual([
            'bias_map', 'levels', 'momentum_map', 'pd_state',
            'swings', 'trend_bias', 'zone_grades', 'zones',
        ]);
    });

    it('TC-08: applySmcFull ↔ applySmcDelta field set is identical', () => {
        const full = makeFullState();
        const delta = applySmcDelta(full, emptyDelta());

        expect(Object.keys(full).sort()).toEqual(Object.keys(delta).sort());
    });
});

// ── DF-1 edge: undefined zone_grades in current state ────────

describe('ADR-0042 DF-1 edge: undefined metadata passthrough', () => {
    it('TC-09: undefined zone_grades preserved as undefined (not lost)', () => {
        const state: SmcData = { ...EMPTY_SMC_DATA, zones: [ZONE_OB] };
        // zone_grades is undefined in EMPTY_SMC_DATA
        const result = applySmcDelta(state, emptyDelta());

        // Ключ має бути у результаті, нехай навіть undefined
        expect('zone_grades' in result).toBe(true);
    });

    it('TC-10: null pd_state preserved as null (not dropped)', () => {
        const state: SmcData = { ...EMPTY_SMC_DATA, pd_state: null };
        const result = applySmcDelta(state, emptyDelta());

        expect(result.pd_state).toBeNull();
    });
});

// ── ADR-0042 P2 / ADR-0043 P2: frame-level metadata merge (ChartPane pattern) ──
// zone_grades/bias_map/momentum_map/pd_state live on top-level RenderFrame,
// not in SmcDeltaWire. ChartPane merges them separately after applySmcDelta.
// This helper simulates that ChartPane thick-delta block.

describe('ADR-0042 P2 DF-2 / ADR-0043 P2: frame metadata merge (ChartPane pattern)', () => {
    // Simulates ChartPane thick-delta merge block (ADR-0043 D3 fix applied:
    // pd !== undefined allows null through — explicit clear)
    function mergeFrameMetadata(
        smcData: SmcData,
        frame: { zone_grades?: Record<string, ZoneGradeInfo>; bias_map?: Record<string, string>; momentum_map?: Record<string, { b: number; r: number }>; pd_state?: PdState | null },
    ): SmcData {
        const zg = frame.zone_grades;
        const bm = frame.bias_map;
        const mm = frame.momentum_map;
        const pd = frame.pd_state;
        if (
            (zg && Object.keys(zg).length > 0) ||
            (bm && Object.keys(bm).length > 0) ||
            (mm && Object.keys(mm).length > 0) ||
            pd !== undefined
        ) {
            return {
                ...smcData,
                ...(zg && Object.keys(zg).length > 0 ? { zone_grades: zg } : {}),
                ...(bm && Object.keys(bm).length > 0 ? { bias_map: bm } : {}),
                ...(mm && Object.keys(mm).length > 0 ? { momentum_map: mm } : {}),
                // ADR-0043 D3 fix: pd !== undefined (allows null → explicit clear)
                ...(pd !== undefined ? { pd_state: pd } : {}),
            };
        }
        return smcData;
    }

    it('TC-11: frame pd_state null → explicit clear (D3 fix: null guard removed)', () => {
        const state = makeFullState(); // pd_state = PD (non-null)
        const result = mergeFrameMetadata(state, { pd_state: null });
        expect(result.pd_state).toBeNull();
    });

    it('TC-12: frame zone_grades overwrites stale smcData grades', () => {
        const state = makeFullState();
        const afterDelta = applySmcDelta(state, emptyDelta());
        const newGrades: Record<string, ZoneGradeInfo> = {
            [ZONE_OB.id]: { score: 10, grade: 'A+', factors: ['sweep +2', 'htf_align +2', 'session +2', 'fvg_nearby +2', 'extremum +2'] },
        };
        const result = mergeFrameMetadata(afterDelta, { zone_grades: newGrades });
        expect(result.zone_grades).toEqual(newGrades);
        expect(result.pd_state).toEqual(PD); // unchanged
    });

    it('TC-13: frame bias_map overwrites stale bias', () => {
        const state = makeFullState();
        const afterDelta = applySmcDelta(state, emptyDelta());
        const newBias = { '900': 'bearish', '3600': 'bullish' };
        const result = mergeFrameMetadata(afterDelta, { bias_map: newBias });
        expect(result.bias_map).toEqual(newBias);
        expect(result.zone_grades).toEqual(GRADES); // unchanged
    });

    it('TC-14: empty frame metadata does NOT overwrite (guard works)', () => {
        const state = makeFullState();
        const result = mergeFrameMetadata(state, { zone_grades: {}, bias_map: {}, momentum_map: {} });
        expect(result.zone_grades).toEqual(GRADES);
        expect(result.bias_map).toEqual(BIAS_MAP);
        expect(result.momentum_map).toEqual(MOMENTUM_MAP);
    });

    it('TC-15: full cycle: applySmcFull → applySmcDelta → mergeFrameMetadata preserves all 8 fields', () => {
        const full = makeFullState();
        const afterDelta = applySmcDelta(full, {
            ...emptyDelta(),
            new_zones: [{ id: 'new_z', start_ms: 9000, high: 2080, low: 2075, kind: 'ob_bear', status: 'active' }],
        });
        const afterMerge = mergeFrameMetadata(afterDelta, { zone_grades: GRADES, bias_map: BIAS_MAP });

        expect(Object.keys(afterMerge).sort()).toEqual([
            'bias_map', 'levels', 'momentum_map', 'pd_state',
            'swings', 'trend_bias', 'zone_grades', 'zones',
        ]);
        expect(afterMerge.zones).toHaveLength(3);
        expect(afterMerge.zone_grades).toEqual(GRADES);
        expect(afterMerge.pd_state).toEqual(PD);
    });
});
