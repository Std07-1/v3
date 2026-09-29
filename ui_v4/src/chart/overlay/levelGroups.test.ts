/**
 * ADR-0104 §3.7 (рішення 29.09): меню «Рівні» — рядки груп, вибір трейдера окремо на TF поверх типового сервера.
 * Запуск: npx vitest run src/chart/overlay/levelGroups.test.ts
 */

import { describe, it, expect } from 'vitest';
import type { SmcLevel } from '../../types';
import {
    buildMenuRows, hasOverrides, isLevelVisible, loadOverrides, saveOverrides, toggleGroup, visibleLevels,
    LEVEL_OVERRIDES_STORAGE_KEY,
} from './levelGroups';

const PDH: SmcLevel = { id: 'pdh_1', kind: 'pdh', price: 4315.68, group: 'day', auto: true };
const PDL: SmcLevel = { id: 'pdl_1', kind: 'pdl', price: 4254.4, group: 'day', auto: true };
const PREV_H4: SmcLevel = { id: 'p_h4_h_1', kind: 'p_h4_h', price: 4171.21, group: 'h4', auto: false };
const OLD_SERVER: SmcLevel = { id: 'eq_1', kind: 'eq_highs', price: 4293.75 }; // без group/auto
const FRAME = [PDH, PDL, PREV_H4];

describe('видимість рівня', () => {
    it('без вибору трейдера — типове сервера', () => {
        expect(visibleLevels(FRAME, {})).toEqual([PDH, PDL]);
    });
    it('вибір трейдера перемагає типове в обидва боки', () => {
        expect(visibleLevels(FRAME, { day: false, h4: true })).toEqual([PREV_H4]);
    });
    it('рівень від сервера без груп показується, як до меню', () => {
        expect(isLevelVisible(OLD_SERVER, { liquidity: false })).toBe(true);
    });
});

describe('рядки меню', () => {
    it('рахують рівні групи в кадрі; група без рівнів — 0 і вимкнена', () => {
        const rows = buildMenuRows(FRAME, {});
        expect(rows.find((r) => r.group === 'day')).toMatchObject({ count: 2, on: true, label: 'День' });
        expect(rows.find((r) => r.group === 'h4')).toMatchObject({ count: 1, on: false });
        expect(rows.find((r) => r.group === 'sessions_prev')).toMatchObject({ count: 0, on: false });
        expect(rows.map((r) => r.group)).toEqual(
            ['day', 'week', 'open', 'open_week', 'asia', 'london', 'newyork', 'sessions_prev', 'h4', 'h1', 'liquidity']);
    });
    it('перемикання назад до типового прибирає запис — «Скинути» знову неактивне', () => {
        const off = toggleGroup(FRAME, {}, 'day');
        expect(off).toEqual({ day: false });
        expect(hasOverrides(off)).toBe(true);
        expect(toggleGroup(FRAME, off, 'day')).toEqual({});
    });
    it('рядок без рівнів можна увімкнути наперед — вибір зберігається', () => {
        expect(toggleGroup(FRAME, {}, 'sessions_prev')).toEqual({ sessions_prev: true });
    });
});

describe('збереження вибору', () => {
    const memory = () => {
        const store = new Map<string, string>();
        return { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    };
    it('окремо для кожного TF, туди й назад', () => {
        const s = memory();
        saveOverrides(s, { M5: { h1: true }, H4: { day: false } });
        expect(loadOverrides(s)).toEqual({ M5: { h1: true }, H4: { day: false } });
    });
    it('невідомі групи й не-булеві значення ігноруються; пошкоджений JSON — типові', () => {
        const s = memory();
        s.setItem(LEVEL_OVERRIDES_STORAGE_KEY, JSON.stringify({ M5: { day: false, weekly: true, h4: 'yes' } }));
        expect(loadOverrides(s)).toEqual({ M5: { day: false } });
        s.setItem(LEVEL_OVERRIDES_STORAGE_KEY, '{oops');
        expect(loadOverrides(s)).toEqual({});
    });
    it('сховище недоступне — не падає', () => {
        const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('quota'); } };
        expect(loadOverrides(broken)).toEqual({});
        expect(() => saveOverrides(broken, { M5: { day: false } })).not.toThrow();
    });
});
