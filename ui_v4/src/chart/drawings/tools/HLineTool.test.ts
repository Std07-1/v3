// ADR-0074 T6 — HLineTool unit tests.
// Coverage: render() AABB calculation, hitTest() distance threshold,
// null-coord short-circuit.

import { describe, it, expect, vi } from 'vitest';
import { HLineTool } from './HLineTool';
import type { Drawing } from '../../../types';

// Mock CanvasRenderingContext2D — записує calls, не виконує реальний draw.
function makeMockCtx() {
    return {
        strokeStyle: '',
        lineWidth: 0,
        setLineDash: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
}

function makeRc(
    toX: (t: number) => number | null = () => 100,
    toY: (p: number) => number | null = (p) => p * 10,
) {
    return {
        ctx: makeMockCtx(),
        toX,
        toY,
        baseColor: '#fff',
        accentColor: '#d4a017',
        rectFill: 'rgba(0,0,0,0.1)',
        isDraft: false,
        isHovered: false,
        isSelected: false,
        cssW: 800,
        cssH: 400,
    };
}

const HLINE: Drawing = {
    id: 'h1',
    type: 'hline',
    points: [{ t_ms: 1_000, price: 50 }],
};

describe('HLineTool', () => {
    describe('metadata', () => {
        it('exposes correct id + hotkey + pointsRequired', () => {
            expect(HLineTool.id).toBe('hline');
            expect(HLineTool.pointsRequired).toBe(1);
            expect(HLineTool.hotkey).toBe('h');
            expect(HLineTool.label).toBeTruthy();
            expect(HLineTool.icon).toBeTruthy();
        });
    });

    describe('render', () => {
        it('returns full-width AABB at y=toY(price)', () => {
            const rc = makeRc();
            const aabb = HLineTool.render(HLINE, rc);
            expect(aabb).toEqual({ minX: 0, maxX: 800, minY: 500, maxY: 500 });
        });

        it('returns null коли toY() returns null (off-screen price)', () => {
            const rc = makeRc(undefined, () => null);
            const aabb = HLineTool.render(HLINE, rc);
            expect(aabb).toBeNull();
        });
    });

    describe('hitTest', () => {
        const toY = (price: number): number | null => price * 10; // price 50 → y=500
        const toX = (_t: number): number | null => 100;

        it('hits коли cursor на лінії (distance=0)', () => {
            const r = HLineTool.hitTest(HLINE, 400, 500, 10, toX, toY);
            expect(r.hit).toBe(true);
            if (r.hit) expect(r.distance).toBe(0);
        });

        it('hits коли cursor у tolerance window (5px above)', () => {
            const r = HLineTool.hitTest(HLINE, 400, 495, 10, toX, toY);
            expect(r.hit).toBe(true);
            if (r.hit) expect(r.distance).toBe(5);
        });

        it('no-hit коли cursor поза tolerance (20px away)', () => {
            const r = HLineTool.hitTest(HLINE, 400, 520, 10, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('no-hit коли toY() returns null', () => {
            const r = HLineTool.hitTest(HLINE, 400, 500, 10, toX, () => null);
            expect(r.hit).toBe(false);
        });

        it('x-coordinate ignored — hline тягнеться повністю по width', () => {
            const r1 = HLineTool.hitTest(HLINE, 0, 500, 10, toX, toY);
            const r2 = HLineTool.hitTest(HLINE, 800, 500, 10, toX, toY);
            expect(r1.hit).toBe(true);
            expect(r2.hit).toBe(true);
        });
    });
});
