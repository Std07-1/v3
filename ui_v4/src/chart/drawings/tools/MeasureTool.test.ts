// ADR-0084 D2 — MeasureTool unit tests: ephemeral контракт (нема AABB/hit),
// лейбл-текст (Δ/%/тривалість) рендериться напрямним кольором.

import { describe, it, expect, vi } from 'vitest';
import { MeasureTool } from './MeasureTool';
import type { Drawing } from '../../../types';

function makeMockCtx() {
    return {
        strokeStyle: '',
        fillStyle: '',
        lineWidth: 0,
        lineCap: 'butt',
        shadowColor: '',
        shadowBlur: 0,
        font: '',
        setLineDash: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        fillRect: vi.fn(),
        fillText: vi.fn(),
        measureText: vi.fn(() => ({ width: 120 })),
    } as unknown as CanvasRenderingContext2D;
}

const H1_MS = 3_600_000;
const UP: Drawing = {
    id: 'm1',
    type: 'measure',
    points: [
        { t_ms: 0, price: 4000 },
        { t_ms: 5.5 * H1_MS, price: 4040 }, // +40 (+1.00%) · 5г 30м
    ],
};

function makeRc(ctx = makeMockCtx()) {
    return {
        ctx,
        toX: (t: number) => t / 100000,
        toY: (p: number) => 4400 - p,
        baseColor: '#fff',
        accentColor: '#d4a017',
        rectFill: 'rgba(0,0,0,0.1)',
        bullColor: '#22CC8F',
        bearColor: '#ED4554',
        isDraft: true,
        isHovered: false,
        isSelected: false,
        cssW: 800,
        cssH: 400,
    };
}

describe('MeasureTool (ephemeral)', () => {
    it('metadata: id/points/hotkey', () => {
        expect(MeasureTool.id).toBe('measure');
        expect(MeasureTool.pointsRequired).toBe(2);
        expect(MeasureTool.hotkey).toBe('m');
    });

    it('render повертає null AABB (не selectable за визначенням)', () => {
        expect(MeasureTool.render(UP, makeRc())).toBeNull();
    });

    it('hitTest завжди {hit:false} (ephemeral контракт)', () => {
        const r = MeasureTool.hitTest(UP, 100, 100, 10, () => 100, () => 100);
        expect(r.hit).toBe(false);
    });

    it('лейбл: Δціна + % + тривалість у людському форматі', () => {
        const ctx = makeMockCtx();
        MeasureTool.render(UP, makeRc(ctx));
        const label = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
        expect(label).toContain('+40.00');
        expect(label).toContain('+1.00%');
        expect(label).toContain('5г 30м');
    });

    it('напрямний колір: up → bull, down → bear', () => {
        const up = makeMockCtx();
        MeasureTool.render(UP, makeRc(up));
        expect(up.fillStyle).toBe('#22CC8F'); // останній fillStyle = текст лейбла

        const DOWN: Drawing = { ...UP, points: [UP.points[0], { t_ms: H1_MS, price: 3960 }] };
        const down = makeMockCtx();
        MeasureTool.render(DOWN, makeRc(down));
        expect(down.fillStyle).toBe('#ED4554');
    });
});
