// ADR-0084 D1 — RayTool unit tests: напрямленість (за p2 — hit, перед p1 — ні),
// AABB кліп до canvas (delete-× лишається на екрані).

import { describe, it, expect, vi } from 'vitest';
import { RayTool } from './RayTool';
import type { Drawing } from '../../../types';

function makeMockCtx() {
    return {
        strokeStyle: '',
        lineWidth: 0,
        lineCap: 'butt',
        shadowColor: '',
        shadowBlur: 0,
        setLineDash: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
}

// Горизонтальний промінь: p1=(100,200) → p2=(300,200), t*10=x, price=y.
const toX = (t: number): number | null => t / 10;
const toY = (p: number): number | null => p;
const RAY: Drawing = {
    id: 'ray1',
    type: 'ray',
    points: [
        { t_ms: 1000, price: 200 },
        { t_ms: 3000, price: 200 },
    ],
};

function makeRc() {
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

describe('RayTool', () => {
    it('metadata: id/points/hotkey', () => {
        expect(RayTool.id).toBe('ray');
        expect(RayTool.pointsRequired).toBe(2);
        expect(RayTool.hotkey).toBe('y');
    });

    describe('render — AABB кліпнутий до canvas', () => {
        it('maxX = cssW (не 10000): центр AABB → delete-× на екрані', () => {
            const aabb = RayTool.render(RAY, makeRc());
            expect(aabb).not.toBeNull();
            expect(aabb!.maxX).toBe(800);
            expect(aabb!.minX).toBe(100);
            expect(aabb!.minY).toBe(200);
            expect(aabb!.maxY).toBe(200);
        });

        it('null коли якір поза координатами', () => {
            const rc = { ...makeRc(), toY: () => null };
            expect(RayTool.render(RAY, rc)).toBeNull();
        });
    });

    describe('hitTest — напрямленість променя', () => {
        it('hit ЗА p2 (промінь продовжується вправо)', () => {
            const r = RayTool.hitTest(RAY, 700, 200, 10, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('hit на сегменті p1→p2', () => {
            const r = RayTool.hitTest(RAY, 200, 205, 10, toX, toY);
            expect(r.hit).toBe(true);
        });

        it('NO hit ПЕРЕД p1 (проти напрямку променя)', () => {
            const r = RayTool.hitTest(RAY, 40, 200, 10, toX, toY);
            expect(r.hit).toBe(false);
        });

        it('NO hit поза tolerance перпендикулярно', () => {
            const r = RayTool.hitTest(RAY, 400, 230, 10, toX, toY);
            expect(r.hit).toBe(false);
        });
    });
});
