// ADR-0111 §3.4 (1) — FibTool: рівні відкату, підписи, рамка до шкали, влучання по лініях і діагоналі.
import { describe, expect, it, vi } from 'vitest';
import { FIB_LEVELS, FibTool, fibLevelLabel, fibLevelPrice } from './FibTool';
import type { Drawing } from '../../../types';

function makeMockCtx() {
    return {
        strokeStyle: '',
        fillStyle: '',
        lineWidth: 0,
        lineCap: 'butt',
        shadowColor: '',
        shadowBlur: 0,
        globalAlpha: 1,
        font: '',
        setLineDash: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        fillRect: vi.fn(),
        fillText: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
}

// Бичачий імпульс: початок 4100 (t=0), кінець 4200 (t=10 год). toX: 1 год = 10 px; toY: 4400 − ціна.
const H1_MS = 3_600_000;
const IMPULSE: Drawing = {
    id: 'f1',
    type: 'fib',
    points: [
        { t_ms: 2 * H1_MS, price: 4100 },
        { t_ms: 12 * H1_MS, price: 4200 },
    ],
};
const toX = (t: number) => (t / H1_MS) * 10;
const toY = (p: number) => 4400 - p;

function makeRc(ctx = makeMockCtx(), isDraft = false) {
    return {
        ctx,
        toX,
        toY,
        baseColor: '#c8cdd6',
        accentColor: '#d4a017',
        rectFill: 'rgba(0,0,0,0.1)',
        priceDigits: 2,
        isDraft,
        isHovered: false,
        isSelected: false,
        cssW: 800,
        cssH: 500,
    };
}

describe('FibTool — рівні', () => {
    it('0 — кінець імпульсу, 1 — початок; OTE 0.62–0.79 — глибокий відкат', () => {
        expect(fibLevelPrice(4100, 4200, 0)).toBe(4200);
        expect(fibLevelPrice(4100, 4200, 1)).toBe(4100);
        expect(fibLevelPrice(4100, 4200, 0.5)).toBe(4150);
        expect(fibLevelPrice(4100, 4200, 0.62)).toBeCloseTo(4138, 9);
        expect(fibLevelPrice(4100, 4200, 0.705)).toBeCloseTo(4129.5, 9);
        expect(fibLevelPrice(4100, 4200, 0.79)).toBeCloseTo(4121, 9);
        // ведмежий імпульс — дзеркально
        expect(fibLevelPrice(4200, 4100, 0.705)).toBeCloseTo(4170.5, 9);
    });

    it('набір рівнів — як в ADR-0111 §3.4', () => {
        expect(FIB_LEVELS).toEqual([0, 0.5, 0.62, 0.705, 0.79, 1]);
    });

    it('підпис — число рівня і ціна знаками символу', () => {
        expect(fibLevelLabel(0.705, 4129.5, 2)).toBe('0.705 · 4129.50');
        expect(fibLevelLabel(0.62, 158.302, 3)).toBe('0.62 · 158.302');
        expect(fibLevelLabel(1, 4100, null)).toBe('1 · 4100.00');
    });
});

describe('FibTool — render', () => {
    it('рамка — від лівого якоря до шкали (лінії тягнуться вправо), по висоті — між якорями', () => {
        expect(FibTool.render(IMPULSE, makeRc())).toEqual({ minX: 20, maxX: 800, minY: 200, maxY: 300 });
    });

    it('шість ліній рівнів від лівого якоря до шкали і підпис над кожною', () => {
        const ctx = makeMockCtx();
        FibTool.render(IMPULSE, makeRc(ctx));
        const lineTo = (ctx.lineTo as ReturnType<typeof vi.fn>).mock.calls;
        // 6 рівнів + діагональ
        expect(lineTo).toHaveLength(FIB_LEVELS.length + 1);
        expect(lineTo.slice(0, 6).every(([x]) => x === 800)).toBe(true);
        const labels = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls.map(([text]) => text);
        expect(labels).toEqual([
            '0 · 4200.00',
            '0.5 · 4150.00',
            '0.62 · 4138.00',
            '0.705 · 4129.50',
            '0.79 · 4121.00',
            '1 · 4100.00',
        ]);
    });

    it('смуга OTE — між 0.62 і 0.79, у кольорі фігури з ледь помітною альфою', () => {
        const ctx = makeMockCtx();
        FibTool.render(IMPULSE, makeRc(ctx));
        const [x, y, w, h] = (ctx.fillRect as ReturnType<typeof vi.fn>).mock.calls[0];
        expect([x, w]).toEqual([20, 780]);
        expect(y).toBeCloseTo(4400 - 4138, 9);
        expect(h).toBeCloseTo(17, 9);
    });

    it('якір поза видимою областю → нічого не малює', () => {
        const rc = { ...makeRc(), toX: () => null };
        expect(FibTool.render(IMPULSE, rc)).toBeNull();
    });
});

describe('FibTool — влучання і ×', () => {
    it('влучає по лінії рівня праворуч від кінця імпульсу (лінії тягнуться до шкали)', () => {
        const r = FibTool.hitTest(IMPULSE, 600, 4400 - 4129.5 + 2, 6, toX, toY);
        expect(r).toEqual({ hit: true, distance: 2 });
    });

    it('влучає по діагоналі; повз лінії й ліворуч від якоря — ні', () => {
        expect(FibTool.hitTest(IMPULSE, 70, 250, 6, toX, toY).hit).toBe(true); // середина діагоналі
        expect(FibTool.hitTest(IMPULSE, 600, 4400 - 4160, 6, toX, toY).hit).toBe(false); // між 0.5 і 0
        expect(FibTool.hitTest(IMPULSE, 5, 4400 - 4150, 6, toX, toY).hit).toBe(false); // ліворуч від якоря
    });

    it('× — посередині діагоналі', () => {
        expect(FibTool.deleteAnchor!(IMPULSE, toX, toY, 800, 500)).toEqual({ x: 70, y: 250 });
    });
});
