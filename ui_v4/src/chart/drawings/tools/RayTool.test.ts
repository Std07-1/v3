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

    // 12.10.2026 (ADR-0111 §3.2): промінь з H4 на M5 / сильний zoom-in — p2 далеко
    // ліворуч від екрана; фіксовані 10 000 px продовження до видимої області не діставали.
    describe('далекий p2 (x = −50 000) — промінь однаково перетинає видиму область', () => {
        const FAR: Drawing = {
            id: 'ray-far',
            type: 'ray',
            points: [
                { t_ms: -600_000, price: 200 }, // x = −60 000
                { t_ms: -500_000, price: 200 }, // x = −50 000
            ],
        };

        it('render: AABB — уся ширина canvas, кінець штриха за правим краєм', () => {
            const rc = makeRc();
            expect(RayTool.render(FAR, rc)).toEqual({ minX: 0, maxX: 800, minY: 200, maxY: 200 });
            const [endX] = (rc.ctx.lineTo as ReturnType<typeof vi.fn>).mock.calls[0];
            expect(endX).toBeGreaterThan(800);
        });

        it('hitTest по видимій частині влучає, поза tolerance — ні', () => {
            expect(RayTool.hitTest(FAR, 400, 205, 10, toX, toY).hit).toBe(true);
            expect(RayTool.hitTest(FAR, 795, 200, 10, toX, toY).hit).toBe(true);
            expect(RayTool.hitTest(FAR, 400, 230, 10, toX, toY).hit).toBe(false);
        });

        it('deleteAnchor — середина видимого сегмента', () => {
            const a = RayTool.deleteAnchor!(FAR, toX, toY, 800, 400);
            expect(a).not.toBeNull();
            expect(a!.x).toBeCloseTo(400, 6);
            expect(a!.y).toBeCloseTo(200, 6);
        });

        it('діагональ: hitTest влучає у видимий відрізок (0,200)→(400,400)', () => {
            // нахил 0.5: на x=0 → y=200, виходить знизу (y=400) на x=400.
            const DIAG: Drawing = {
                ...FAR,
                points: [
                    { t_ms: -600_000, price: -29_800 },
                    { t_ms: -500_000, price: -24_800 },
                ],
            };
            expect(RayTool.hitTest(DIAG, 200, 300, 10, toX, toY).hit).toBe(true);
            const aabb = RayTool.render(DIAG, makeRc());
            expect(aabb!.minX).toBe(0);
            expect(aabb!.maxY).toBe(400);
        });
    });
});
