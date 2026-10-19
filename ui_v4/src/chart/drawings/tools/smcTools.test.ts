// src/chart/drawings/tools/smcTools.test.ts — ADR-0111 §3.7 (прототипи): зона до першого дотику з лінією 50%,
// промінь рівня від точки, рамка нотатки.
import { describe, expect, it, vi } from 'vitest';
import { zoneMidPrice, ZoneTool } from './ZoneTool';
import { HRayTool } from './HRayTool';
import { chipBox } from './TextTool';
import type { Drawing } from '../../../types';
import type { RenderContext } from './types';

/** Полотно-заглушка: пам'ятає кольори заливок (fillRect). */
function recordingRc(firstTouchMs: RenderContext['firstTouchMs']) {
    const fills: string[] = [];
    const ctx = {
        fillStyle: '', strokeStyle: '', lineWidth: 0, globalAlpha: 1, shadowBlur: 0, shadowColor: '', lineCap: 'butt',
        setLineDash: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
        fillRect: vi.fn(function (this: { fillStyle: string }) { fills.push(this.fillStyle); }),
    };
    const rc: RenderContext = {
        ctx: ctx as unknown as CanvasRenderingContext2D,
        toX: (t) => t, toY: (p) => p, baseColor: '#26a69a', accentColor: '#d4a017', rectFill: 'rgba(0,0,0,0.1)',
        isDraft: false, isHovered: false, isSelected: false, cssW: 800, cssH: 400, firstTouchMs,
    };
    return { rc, fills };
}

describe('зона OB/FVG', () => {
    const zone: Drawing = { id: 'z', type: 'zone', points: [{ t_ms: 100, price: 300 }, { t_ms: 200, price: 200 }] };

    it('заливка — колір фігури (не нейтральна, як у прямокутника); продовження — тихіше', () => {
        const { rc, fills } = recordingRc(() => 500);
        expect(ZoneTool.render(zone, rc)).toEqual({ minX: 100, maxX: 500, minY: 200, maxY: 300 });
        expect(fills).toEqual(['rgba(38,166,154,0.12)', 'rgba(38,166,154,0.06)']);
    });

    it('не торкалась — до шкали; дотик за шкалою — теж до шкали', () => {
        expect(ZoneTool.render(zone, recordingRc(() => null).rc)?.maxX).toBe(800);
        expect(ZoneTool.render(zone, recordingRc(() => 5000).rc)?.maxX).toBe(800);
    });

    it('межа продовження клікається праворуч від зони; середина продовження — ні', () => {
        const id = (v: number) => v;
        expect(ZoneTool.hitTest(zone, 450, 297, 10, id, id)).toEqual({ hit: true, distance: 3 });
        expect(ZoneTool.hitTest(zone, 450, 250, 10, id, id)).toEqual({ hit: false });
    });

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
