// ADR-0074 T6 — RectTool unit tests.
// Coverage: render AABB normalization, hitTest на ребрах (hit) vs body
// interior (no-hit per distToRectEdge contract).

import { describe, it, expect, vi } from 'vitest';
import { RectTool } from './RectTool';
import type { Drawing } from '../../../types';

function makeMockCtx() {
    return {
        strokeStyle: '',
        fillStyle: '',
        lineWidth: 0,
        setLineDash: vi.fn(),
        fillRect: vi.fn(),
        strokeRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
}

function makeRc() {
    return {
        ctx: makeMockCtx(),
        toX: (t: number) => t / 10,
        toY: (p: number) => p * 10,
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

// Rect corners: (t=1000, price=10) → (100, 100), (t=3000, price=30) → (300, 300).
// → rect bounds [100, 100] – [300, 300] (200×200 square).
const RECT: Drawing = {
    id: 'r1',
    type: 'rect',
    points: [
        { t_ms: 1_000, price: 10 },
        { t_ms: 3_000, price: 30 },
    ],
};

describe('RectTool', () => {
    describe('metadata', () => {
        it('id=rect, pointsRequired=2, hotkey=r', () => {
            expect(RectTool.id).toBe('rect');
            expect(RectTool.pointsRequired).toBe(2);
            expect(RectTool.hotkey).toBe('r');
        });
    });

    describe('render', () => {
        it('AABB normalized to min/max corners', () => {
            const aabb = RectTool.render(RECT, makeRc());
            expect(aabb).toEqual({ minX: 100, minY: 100, maxX: 300, maxY: 300 });
        });

        it('AABB однаковий якщо corners переставлені (other diagonal)', () => {
            const flipped: Drawing = {
                ...RECT,
                points: [RECT.points[1], RECT.points[0]],
            };
            const aabb = RectTool.render(flipped, makeRc());
            expect(aabb).toEqual({ minX: 100, minY: 100, maxX: 300, maxY: 300 });
        });

        it('returns null при single-point malformed', () => {
            const d: Drawing = { id: 'x', type: 'rect', points: [{ t_ms: 1, price: 1 }] };
            expect(RectTool.render(d, makeRc())).toBeNull();
        });
    });

    describe('hitTest (edges hit, interior no-hit)', () => {
        const toX = (t: number): number | null => t / 10;
        const toY = (p: number): number | null => p * 10;
        const TOL = 6;

        it('hits на top edge (200, 100)', () => {
            const r = RectTool.hitTest(RECT, 200, 100, TOL, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('hits на bottom-right corner (300, 300)', () => {
            const r = RectTool.hitTest(RECT, 300, 300, TOL, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('hits within tolerance window outside edge (105, 95) — 5px above top edge', () => {
            const r = RectTool.hitTest(RECT, 105, 95, TOL, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('no-hit у body interior (200, 200) — middle of rect', () => {
            const r = RectTool.hitTest(RECT, 200, 200, TOL, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('no-hit поза expanded bbox (50, 50) — 50px above top-left', () => {
            const r = RectTool.hitTest(RECT, 50, 50, TOL, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('no-hit при null coord transform', () => {
            const r = RectTool.hitTest(RECT, 200, 100, TOL, () => null, toY);
            expect(r.hit).toBe(false);
        });
    });
});
