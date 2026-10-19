// src/chart/drawings/tools/smcTools.test.ts — ADR-0111 §3.7 (прототипи): зона до першого дотику з лінією 50%,
// промінь рівня від точки, рамка нотатки.
import { describe, expect, it } from 'vitest';
import { zoneMidPrice, ZoneTool } from './ZoneTool';
import { HRayTool } from './HRayTool';
import { chipBox } from './TextTool';
import type { Drawing } from '../../../types';

describe('зона OB/FVG', () => {
    it('50% — посередині між межами', () => {
        expect(zoneMidPrice(98, 100)).toBe(99);
    });

    it('хіт — грань зони; інтер\'єр — лише через × активної (interiorIsBody)', () => {
        const zone: Drawing = { id: 'z', type: 'zone', points: [{ t_ms: 100, price: 300 }, { t_ms: 200, price: 200 }] };
        const id = (v: number) => v;
        expect(ZoneTool.hitTest(zone, 150, 202, 10, id, id)).toEqual({ hit: true, distance: 0 });
        expect(ZoneTool.hitTest(zone, 150, 250, 10, id, id)).toEqual({ hit: false });
        expect(ZoneTool.interiorIsBody).toBe(true);
    });
});

describe('промінь рівня', () => {
    const ray: Drawing = { id: 'r', type: 'hray', points: [{ t_ms: 100, price: 250 }] };
    const id = (v: number) => v;

    it('праворуч від початку — відстань по вертикалі; ліворуч — не хіт', () => {
        expect(HRayTool.hitTest(ray, 400, 254, 10, id, id)).toEqual({ hit: true, distance: 4 });
        expect(HRayTool.hitTest(ray, 60, 250, 10, id, id)).toEqual({ hit: false });
    });

    it('біля самого початку — відстань до точки', () => {
        const near = HRayTool.hitTest(ray, 94, 250, 10, id, id);
        expect(near.hit && near.distance).toBeCloseTo(6, 6);
    });
});

describe('нотатка', () => {
    it('якір — лівий нижній кут чіпа; ширина — текст плюс поля', () => {
        expect(chipBox(100, 300, 40)).toEqual({ minX: 100, maxX: 152, minY: 280, maxY: 300 });
    });
});
