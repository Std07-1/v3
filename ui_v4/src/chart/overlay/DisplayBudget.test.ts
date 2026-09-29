/**
 * ADR-0104 §3.7: Focus для рівнів — найближчі ціни на бік серед увімкнених у меню; Research — усі увімкнені.
 * Ранг близькості — з сервера (proximity: +1 найближча вище, −1 нижче; однакові ціни ділять ранг).
 * Запуск: npx vitest run src/chart/overlay/DisplayBudget.test.ts
 */

import { describe, it, expect } from 'vitest';
import type { SmcLevel } from '../../types';
import { applyBudget, nearestLevelsPerSide, parseDisplayBudget, DEFAULT_BUDGET } from './DisplayBudget';

const lvl = (id: string, proximity?: number): SmcLevel => ({ id, kind: 'pdh', price: 1, proximity });

describe('nearestLevelsPerSide', () => {
    it('бере найближчі 3 ціни вище і 3 нижче', () => {
        const levels = [1, 2, 3, 4, -1, -2, -3, -4].map((r) => lvl(`r${r}`, r));
        expect(nearestLevelsPerSide(levels, 3).map((l) => l.proximity)).toEqual([1, 2, 3, -1, -2, -3]);
    });

    it('рівні на одній ціні займають одне місце', () => {
        const levels = [lvl('lon', 1), lvl('ny', 1), lvl('h4', 1), lvl('a', 2), lvl('b', 3), lvl('c', 4)];
        expect(nearestLevelsPerSide(levels, 3).map((l) => l.id)).toEqual(['lon', 'ny', 'h4', 'a', 'b']);
    });

    it('після вимкнення рядка в меню місце займає наступний увімкнений (ранги з пропусками)', () => {
        const enabled = [lvl('r2', 2), lvl('r5', 5), lvl('r7', 7), lvl('r9', 9)];
        expect(nearestLevelsPerSide(enabled, 3).map((l) => l.id)).toEqual(['r2', 'r5', 'r7']);
    });

    it('рівень без рангу (сервер без ціни) показується', () => {
        expect(nearestLevelsPerSide([lvl('x'), lvl('far', 9)], 1).map((l) => l.id)).toEqual(['x', 'far']);
    });
});

describe('applyBudget — Focus і Research для рівнів', () => {
    const levels = [1, 2, 3, 4, 5, -1, -2, -3, -4].map((r) => lvl(`r${r}`, r));

    it('Focus обрізає рівні до найближчих на бік, Research показує всі', () => {
        expect(applyBudget([], levels, [], 'focus', DEFAULT_BUDGET).levels).toHaveLength(6);
        expect(applyBudget([], levels, [], 'research', DEFAULT_BUDGET).levels).toHaveLength(9);
    });
});

describe('parseDisplayBudget — бюджет Focus з кадру конфігу сервера (ADR-0028 v2 §3.4)', () => {
    it('читає поля config.json:smc.display у BudgetConfig', () => {
        expect(parseDisplayBudget({ zones_per_side: 2, structure_max: 5, levels_per_side: 4 }))
            .toEqual({ perSide: 2, structureMax: 5, levelsPerSide: 4 });
    });

    it('нуль дозволений — Focus без цього шару', () => {
        expect(parseDisplayBudget({ zones_per_side: 3, structure_max: 0, levels_per_side: 3 })?.structureMax).toBe(0);
    });

    it('не той вигляд — null (бракує поля, дробове, від’ємне, рядок)', () => {
        expect(parseDisplayBudget({ zones_per_side: 3, structure_max: 4 })).toBeNull();
        expect(parseDisplayBudget({ zones_per_side: 2.5, structure_max: 4, levels_per_side: 3 })).toBeNull();
        expect(parseDisplayBudget({ zones_per_side: -1, structure_max: 4, levels_per_side: 3 })).toBeNull();
        expect(parseDisplayBudget({ zones_per_side: '3', structure_max: 4, levels_per_side: 3 })).toBeNull();
        expect(parseDisplayBudget(null)).toBeNull();
    });
});
