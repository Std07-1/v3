// ADR-0074 T6 — TrendTool unit tests.
// Coverage: render AABB на 2 endpoints, hitTest на endpoints / midpoint /
// off-line, null-coord short-circuit, single-point Drawing reject.

import { describe, it, expect, vi } from 'vitest';
import { TrendTool } from './TrendTool';
import type { Drawing } from '../../../types';

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
    toX: (t: number) => number | null = (t) => t / 10,
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

// 2 points: (t=1000, price=10) → (x=100, y=100); (t=5000, price=50) → (x=500, y=500).
// Horizontal segment довжиною 400px, diagonal — 565px (Pythagorean).
const TREND: Drawing = {
    id: 'tr1',
    type: 'trend',
    points: [
        { t_ms: 1_000, price: 10 },
        { t_ms: 5_000, price: 50 },
    ],
};

describe('TrendTool', () => {
    describe('metadata', () => {
        it('id=trend, pointsRequired=2, hotkey=\\\\', () => {
            expect(TrendTool.id).toBe('trend');
            expect(TrendTool.pointsRequired).toBe(2);
            expect(TrendTool.hotkey).toBe('\\');
        });
    });

    describe('render', () => {
        it('returns AABB bounding 2 endpoints', () => {
            const aabb = TrendTool.render(TREND, makeRc());
            expect(aabb).toEqual({ minX: 100, maxX: 500, minY: 100, maxY: 500 });
        });

        it('returns null коли single-point drawing (invalid)', () => {
            const d: Drawing = { id: 'x', type: 'trend', points: [{ t_ms: 1, price: 1 }] };
            expect(TrendTool.render(d, makeRc())).toBeNull();
        });

        it('returns null коли any endpoint coord null (off-screen)', () => {
            const rc = makeRc((t) => (t === 5_000 ? null : 100));
            expect(TrendTool.render(TREND, rc)).toBeNull();
        });
    });

    describe('hitTest', () => {
        const toX = (t: number): number | null => t / 10;
        const toY = (p: number): number | null => p * 10;

        it('hits на point[0] endpoint (100, 100)', () => {
            const r = TrendTool.hitTest(TREND, 100, 100, 10, toX, toY);
            expect(r.hit).toBe(true);
            if (r.hit) expect(r.distance).toBeLessThanOrEqual(0.01);
        });

        it('hits на point[1] endpoint (500, 500)', () => {
            const r = TrendTool.hitTest(TREND, 500, 500, 10, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('hits на midpoint (300, 300) — точка на лінії y=x', () => {
            const r = TrendTool.hitTest(TREND, 300, 300, 10, toX, toY);
            expect(r.hit).toBe(true);
            if (r.hit) expect(r.distance).toBeLessThanOrEqual(0.01);
        });

        it('no-hit на 50px поза лінією (perpendicular distance > tol)', () => {
            // (300, 300) на лінії; (350, 300) = ~35px perpendicular off.
            const r = TrendTool.hitTest(TREND, 350, 300, 10, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('no-hit при single-point malformed Drawing', () => {
            const d: Drawing = { id: 'x', type: 'trend', points: [{ t_ms: 1, price: 1 }] };
            const r = TrendTool.hitTest(d, 100, 100, 10, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('no-hit коли coord transform повертає null', () => {
            const r = TrendTool.hitTest(TREND, 100, 100, 10, () => null, toY);
            expect(r.hit).toBe(false);
        });
    });
});
