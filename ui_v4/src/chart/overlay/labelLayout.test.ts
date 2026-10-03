// ADR-0107 S1: у скупченні текст лише в найновішої події структури, старші — штрих.
import { describe, it, expect } from 'vitest';
import { boxesOverlap, structureLabelsWithText, type StructureLabelCandidate } from './labelLayout';

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
