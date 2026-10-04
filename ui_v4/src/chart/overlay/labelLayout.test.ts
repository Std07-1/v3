// ADR-0107: S1 — у скупченні текст лише в найновішої події структури, старші — штрих;
// S2 — у стиснутому вигляді підпис FVG лишається лише в найважливішої зони.
import { describe, it, expect } from 'vitest';
import {
    boxesOverlap,
    isCompactView,
    newestStructureLabel,
    structureLabelsWithText,
    zoneLabelsWithText,
    type StructureLabelCandidate,
    type ZoneLabelCandidate,
} from './labelLayout';

const box = (x: number, y: number, w = 30, h = 12) => ({ x, y, w, h });
const cand = (id: string, timeMs: number, x: number, y: number, isChoch = true): StructureLabelCandidate =>
    ({ id, timeMs, isChoch, box: box(x, y) });

describe('boxesOverlap', () => {
    it('плашки, що перетинаються, — скупчення', () => {
        expect(boxesOverlap(box(0, 0), box(20, 5))).toBe(true);
    });

    it('просвіт менший за 2px — теж скупчення, більший — ні', () => {
        expect(boxesOverlap(box(0, 0), box(31, 0))).toBe(true);
        expect(boxesOverlap(box(0, 0), box(33, 0))).toBe(false);
    });

    it('поруч по X, але на різній висоті — не скупчення', () => {
        expect(boxesOverlap(box(0, 0), box(10, 40))).toBe(false);
    });
});

describe('structureLabelsWithText', () => {
    it('розтягнутий графік (плашки не перетинаються) — текст у всіх', () => {
        const kept = structureLabelsWithText([cand('a', 1, 0, 0), cand('b', 2, 100, 0), cand('c', 3, 200, 0)]);
        expect([...kept].sort()).toEqual(['a', 'b', 'c']);
    });

    it('у скупченні текст отримує найновіша подія, старші — штрих', () => {
        const kept = structureLabelsWithText([cand('old', 1, 0, 0), cand('mid', 2, 10, 2), cand('new', 3, 20, 4)]);
        expect([...kept]).toEqual(['new']);
    });

    it('старша подія поза скупченням лишається з текстом, навіть коли сусідня поступилась', () => {
        // new і mid налазять; old далеко від new — текст у new і old, mid — штрих
        const kept = structureLabelsWithText([cand('old', 1, 60, 0), cand('mid', 2, 25, 0), cand('new', 3, 0, 0)]);
        expect([...kept].sort()).toEqual(['new', 'old']);
    });

    it('на рівний час CHoCH важливіший за BOS', () => {
        const kept = structureLabelsWithText([cand('bos', 5, 0, 0, false), cand('choch', 5, 5, 0, true)]);
        expect([...kept]).toEqual(['choch']);
    });

    it('той самий вхід у будь-якому порядку — та сама розкладка', () => {
        const items = [cand('x', 7, 0, 0), cand('y', 7, 4, 0), cand('z', 3, 8, 0)];
        const forward = [...structureLabelsWithText(items)];
        const backward = [...structureLabelsWithText([...items].reverse())];
        expect(forward).toEqual(backward);
        expect(forward).toEqual(['x']);
    });
});

describe('isCompactView (LB7)', () => {
    it('типова ширина свічки — не стиснутий, навіть із похибкою ділення', () => {
        expect(isCompactView(8, 8)).toBe(false);
        expect(isCompactView(7.9999999, 8)).toBe(false);
    });

    it('свічка вужча за типову — стиснутий', () => {
        expect(isCompactView(6.4, 8)).toBe(true);
    });

    it('розтягнутий графік — не стиснутий', () => {
        expect(isCompactView(14, 8)).toBe(false);
    });

    it('діапазону ще нема (0) — не стиснутий', () => {
        expect(isCompactView(0, 8)).toBe(false);
    });
});

const zone = (id: string, tfS: number, distance: number, x: number, y: number): ZoneLabelCandidate =>
    ({ id, tfS, distance, box: box(x, y, 50, 11) });

describe('zoneLabelsWithText (LB8–LB10)', () => {
    it('підписи FVG не налазять — текст у всіх', () => {
        const kept = zoneLabelsWithText([zone('a', 900, 5, 0, 0), zone('b', 900, 9, 0, 40)], []);
        expect([...kept].sort()).toEqual(['a', 'b']);
    });

    it('у скупченні текст отримує старший TF, навіть якщо молодший ближчий до ціни', () => {
        const kept = zoneLabelsWithText([zone('m15', 900, 1, 0, 0), zone('h1', 3600, 30, 10, 4)], []);
        expect([...kept]).toEqual(['h1']);
    });

    it('на рівний TF — ближча до ціни', () => {
        const kept = zoneLabelsWithText([zone('far', 900, 40, 0, 0), zone('near', 900, 2, 10, 4)], []);
        expect([...kept]).toEqual(['near']);
    });

    it('FVG поступається вже поставленому тексту структури чи OB', () => {
        const kept = zoneLabelsWithText([zone('fvg', 3600, 0, 0, 0)], [box(20, 3, 30, 12)]);
        expect(kept.size).toBe(0);
    });

    it('той самий вхід у будь-якому порядку — та сама розкладка', () => {
        const items = [zone('x', 900, 5, 0, 0), zone('y', 900, 5, 10, 2), zone('z', 900, 5, 20, 4)];
        expect([...zoneLabelsWithText(items, [])]).toEqual([...zoneLabelsWithText([...items].reverse(), [])]);
    });
});

describe('newestStructureLabel (LB13)', () => {
    it('текст лише в найновішої події, навіть коли решта стоять окремо', () => {
        const kept = newestStructureLabel([cand('old', 1, 0, 0), cand('mid', 2, 200, 0), cand('new', 3, 400, 0)]);
        expect([...kept]).toEqual(['new']);
    });

    it('на рівний час — CHoCH', () => {
        expect([...newestStructureLabel([cand('bos', 5, 0, 0, false), cand('choch', 5, 300, 0, true)])]).toEqual(['choch']);
    });

    it('нема подій — нема тексту', () => {
        expect(newestStructureLabel([]).size).toBe(0);
    });
});
