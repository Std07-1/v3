// src/chart/drawings/tools/PositionTool.test.ts — ADR-0111 §3.6: довга / коротка позиція (розміщення з кліку, R:R,
// підписи, спільний правий край стопу й цілі, рамка й хіт-тест).
import { describe, expect, it } from 'vitest';
import {
    DEFAULT_WIDTH_BARS,
    LongPositionTool,
    ShortPositionTool,
    legLabel,
    positionPlacement,
    positionStats,
    rrLabel,
} from './PositionTool';
import { firstClick, followCursor, newDraft } from '../drawingDraft';
import type { Drawing } from '../../../types';

const M15 = 900_000;
const ENV = { priceUnit: 0.2, barMs: M15 };
const AT = { t_ms: 1_000 * M15, price: 158.3 };

describe('positionPlacement — один клік ставить усі три якорі', () => {
    it('довга: стоп на 1 ATR нижче, ціль 2R вище, обидва на правому краї через 20 барів', () => {
        const [entry, stop, target] = positionPlacement('long', AT, ENV);
        expect(entry).toEqual(AT);
        expect(stop.t_ms).toBe(AT.t_ms + DEFAULT_WIDTH_BARS * M15);
        expect(target.t_ms).toBe(stop.t_ms);
        expect(stop.price).toBeCloseTo(158.1, 10);
        expect(target.price).toBeCloseTo(158.7, 10);
    });

    it('коротка: дзеркально — стоп вище, ціль нижче', () => {
        const [, stop, target] = positionPlacement('short', AT, ENV);
        expect(stop.price).toBeCloseTo(158.5, 10);
        expect(target.price).toBeCloseTo(157.9, 10);
    });

    it('через реєстр: перший клік одразу комітить позицію з трьома якорями', () => {
        const step = firstClick(LongPositionTool, 'p1', AT, undefined, ENV);
        expect(step.kind).toBe('commit');
        expect(step.kind === 'commit' && step.drawing.points).toHaveLength(3);
        expect(step.kind === 'commit' && step.drawing.type).toBe('long');
    });

    it('прев\'ю під курсором: рух переставляє всю позицію від курсора (стоп 1 ATR, ціль 2R), а не лише ціль', () => {
        const draft = newDraft(LongPositionTool, 'p3', AT, undefined, ENV);
        const moved = followCursor(LongPositionTool, draft, { t_ms: AT.t_ms + M15, price: 158.4 }, ENV);
        expect(moved.points[0]).toEqual({ t_ms: AT.t_ms + M15, price: 158.4 });
        expect(moved.points[1].price).toBeCloseTo(158.2, 10);
        expect(moved.points[2].price).toBeCloseTo(158.8, 10);
        expect(draft.points[0]).toEqual(AT); // чернетка не мутується
    });

    it('без масштабу (env) — як однокрокова: один якір (renderer завжди дає env, це лише контракт)', () => {
        const step = firstClick(ShortPositionTool, 'p2', AT, undefined);
        expect(step.kind === 'commit' && step.drawing.points).toEqual([AT]);
    });
});

describe('R:R і підписи', () => {
    const pos: Drawing = { id: 'p', type: 'long', points: positionPlacement('long', AT, ENV) };

    it('R:R = відстань до цілі / відстань до стопу', () => {
        const s = positionStats(pos.points);
        expect(s.risk).toBeCloseTo(0.2, 10);
        expect(s.reward).toBeCloseTo(0.4, 10);
        expect(s.rr).toBeCloseTo(2, 10);
        expect(rrLabel(s)).toBe('R:R 2.00');
    });

    it('стоп на вході — R:R не ділимо на нуль', () => {
        const flat = [AT, { t_ms: AT.t_ms, price: AT.price }, { t_ms: AT.t_ms, price: 158.5 }];
        expect(rrLabel(positionStats(flat))).toBe('R:R —');
    });

    it('нога: ціна, відстань від входу і відсоток — знаками символу (USD/JPY — 3)', () => {
        expect(legLabel('TP', 158.7, 158.3, 3)).toBe('TP 158.700 · 0.400 (0.25%)');
        expect(legLabel('SL', 158.1, 158.3, 3)).toBe('SL 158.100 · 0.200 (0.13%)');
    });
});

describe('ручки: стоп і ціль ділять правий край', () => {
    const pos: Drawing = { id: 'p', type: 'long', points: positionPlacement('long', AT, ENV) };

    it('потягнули стоп убік — ціль на тому самому краї, її ціна та сама', () => {
        const moved: Drawing = { ...pos, points: pos.points.map((p, i) => (i === 1 ? { t_ms: p.t_ms + 5 * M15, price: 158.05 } : p)) };
        const next = LongPositionTool.constrainHandle!(moved, 1);
        expect(next.points[2]).toEqual({ t_ms: pos.points[1].t_ms + 5 * M15, price: pos.points[2].price });
        expect(next.points[1].price).toBe(158.05);
    });

    it('вхід — незалежний (лівий край і ціна входу)', () => {
        const moved: Drawing = { ...pos, points: pos.points.map((p, i) => (i === 0 ? { t_ms: p.t_ms - M15, price: 158.32 } : p)) };
        expect(LongPositionTool.constrainHandle!(moved, 0)).toBe(moved);
    });
});

describe('рамка й хіт-тест', () => {
    // 1:1 простір: час (бари M15) → x, ціна → y = (160 − ціна) × 1000
    const toX = (t: number) => t / M15 - 1000;
    const toY = (p: number) => (160 - p) * 1000;
    const pos: Drawing = { id: 'p', type: 'long', points: positionPlacement('long', AT, ENV) };
    // вхід x 0, y 1700; стоп y 1900; ціль y 1300; правий край x 20

    it('усередині рамки — тіло (у межах допуску), поза рамкою далеко від ліній — ні', () => {
        expect(LongPositionTool.hitTest(pos, 10, 1500, 16, toX, toY)).toEqual({ hit: true, distance: 16 });
        const nearEntry = LongPositionTool.hitTest(pos, 10, 1705, 16, toX, toY);
        expect(nearEntry.hit && nearEntry.distance).toBeCloseTo(5, 6);
        expect(LongPositionTool.hitTest(pos, 60, 1500, 16, toX, toY)).toEqual({ hit: false });
    });

    it('якорі поза видом — не хіт і не рендер', () => {
        expect(LongPositionTool.hitTest(pos, 10, 1500, 16, () => null, toY)).toEqual({ hit: false });
    });
});
