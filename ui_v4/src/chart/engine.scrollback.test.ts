// Регресія 15–16.10.2026: «[ChartEngine] prependData setData crashed: Error: Assertion failed: data must be
// asc ordered by time, index=500, time=1789530300, prev time=1792103400» (відтворено наживо, ?tf=M15, лог кадрів).
// Гонка: запит історії пішов від одного вмісту графіка, а відповідь прийшла після повного кадру, що цей
// вміст замінив. Сервер відповідає вже в новому TF і ставить мітку нового TF — маршрутизатор її пропускає.
// Відповідь стосується лише того вмісту, від якого її просили; інакше — відкинути, не ламати графік.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Candle } from '../types';

const lwc = vi.hoisted(() => ({ rangeListeners: [] as Array<(range: unknown) => void> }));

vi.mock('lightweight-charts', async (importOriginal) => {
    const real = await importOriginal<typeof import('lightweight-charts')>();
    const inert: any = new Proxy(function () {}, {
        get: (_target, key) => (key === 'then' ? undefined : inert),
        apply: () => inert,
    });
    /** Серія з даними; setData перевіряє порядок так само, як LWC (той самий текст assert). */
    const dataSeries = () => {
        let rows: any[] = [];
        const api: Record<string, unknown> = {
            data: () => rows,
            setData: (next: any[]) => {
                for (let i = 1; i < next.length; i++) {
                    if (!(next[i - 1].time < next[i].time)) {
                        throw new Error(
                            `Assertion failed: data must be asc ordered by time, index=${i}, time=${next[i].time}, prev time=${next[i - 1].time}`,
                        );
                    }
                }
                rows = next;
            },
            update: (row: any) => {
                const last = rows[rows.length - 1];
                rows = last && last.time === row.time ? [...rows.slice(0, -1), row] : [...rows, row];
            },
            barsInLogicalRange: (range: { from: number }) => ({ barsBefore: Math.max(0, Math.floor(range.from)) }),
        };
        return new Proxy(api, { get: (target, key) => (key in target ? target[key as string] : inert) });
    };
    const timeScale = new Proxy({}, {
        get: (_target, key) =>
            key === 'subscribeVisibleLogicalRangeChange'
                ? (cb: (range: unknown) => void) => { lwc.rangeListeners.push(cb); }
                : inert,
    });
    const chart = new Proxy({}, {
        get: (_target, key) =>
            key === 'addSeries' ? () => dataSeries() : key === 'timeScale' ? () => timeScale : inert,
    });
    return { ...real, createChart: () => chart };
});

import { ChartEngine } from './engine';

const M15_MS = 900_000;
const H4_MS = 14_400_000;
const M30_DELTA_MS = 1_792_103_400_000; // 15.10 22:30 — дельта дефолтної M30 на порожньому графіку
const M15_FIRST_MS = 1_789_530_300_000; // 16.09 03:45 — перший бар повного кадру M15
const M15_LAST_MS = 1_792_104_300_000;  // 15.10 22:45

const bar = (t_ms: number): Candle => ({ t_ms, o: 100, h: 101, l: 99, c: 100.5 });
function bars(fromMs: number, toMs: number, stepMs: number): Candle[] {
    const out: Candle[] = [];
    for (let t = fromMs; t <= toMs; t += stepMs) out.push(bar(t));
    return out;
}
const chartTimes = (engine: ChartEngine) => engine.series.data().map((d) => d.time as number);
const secs = (candles: Candle[]) => candles.map((c) => c.t_ms / 1000);

/** Рейка scrollback бачить лівий край графіка (дебаунс 150 мс). */
function edgeReached(): void {
    for (const cb of lwc.rangeListeners) cb({ from: -3, to: 40 });
    vi.advanceTimersByTime(150);
}

describe('ChartEngine — scrollback застосовується лише до вмісту, від якого його просили', () => {
    let requested: number[];
    let engine: ChartEngine;
    let consoleError: ReturnType<typeof vi.spyOn>;
    let consoleWarn: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
        vi.useFakeTimers();
        vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0));
        consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        lwc.rangeListeners.length = 0;
        requested = [];
        engine = new ChartEngine({} as HTMLElement, () => {}, (ms) => requested.push(ms));
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('відповідь, що накладається на новий повний кадр, відкинуто: графік цілий, «стіни» нема', () => {
        // Дефолтний кадр M30 не показано (чекаємо пару з адреси), а його дельта домалювала бар на порожній графік
        engine.setTfS(1800);
        engine.update(bar(M30_DELTA_MS));
        vi.advanceTimersByTime(16);
        edgeReached();
        expect(requested).toEqual([M30_DELTA_MS]);
        // Повний кадр M15 замінив вміст; відповідь прийшла вже в M15 — 500 барів до 15.10 22:30
        engine.setTfS(900);
        const full = bars(M15_FIRST_MS, M15_LAST_MS, M15_MS);
        engine.setData(full);
        engine.prependData(bars(M30_DELTA_MS - 499 * M15_MS, M30_DELTA_MS, M15_MS));

        expect(consoleError).not.toHaveBeenCalled();
        expect(chartTimes(engine)).toEqual(secs(full));
        expect(engine.scrollbackState).toBe('idle');
        expect(consoleWarn).toHaveBeenCalledTimes(1);
    });

    it('відповідь для старшого вмісту не лягає ліворуч з дірою', () => {
        // Запит пішов від лівого краю H4, поки перемикались на M15
        engine.setTfS(14400);
        const h4 = bars(M15_FIRST_MS - 600 * H4_MS, M15_FIRST_MS - 400 * H4_MS, H4_MS);
        engine.setData(h4);
        edgeReached();
        expect(requested).toEqual([h4[0].t_ms]);
        engine.setTfS(900);
        const full = bars(M15_FIRST_MS, M15_LAST_MS, M15_MS);
        engine.setData(full);
        engine.prependData(bars(h4[0].t_ms - 499 * M15_MS, h4[0].t_ms, M15_MS));

        expect(chartTimes(engine)).toEqual(secs(full));
        expect(consoleWarn).toHaveBeenCalledTimes(1);
    });

    it('порожня відповідь на застарілий запит не оголошує «стіну» новому графіку', () => {
        engine.setTfS(1800);
        engine.update(bar(M30_DELTA_MS));
        vi.advanceTimersByTime(16);
        edgeReached();
        engine.setTfS(900);
        engine.setData(bars(M15_FIRST_MS, M15_LAST_MS, M15_MS));
        engine.prependData([]);

        expect(engine.scrollbackState).toBe('idle');
    });

    it('звичайна догрузка: межа inclusive — спільний бар один раз, історія ліворуч', () => {
        engine.setTfS(900);
        const full = bars(M15_FIRST_MS, M15_LAST_MS, M15_MS);
        engine.setData(full);
        edgeReached();
        expect(requested).toEqual([M15_FIRST_MS]);
        const chunk = bars(M15_FIRST_MS - 499 * M15_MS, M15_FIRST_MS, M15_MS);
        engine.prependData(chunk);

        expect(chartTimes(engine)).toEqual(secs([...chunk.slice(0, -1), ...full]));
        expect(engine.scrollbackState).toBe('idle');
        expect(consoleError).not.toHaveBeenCalled();
        expect(consoleWarn).not.toHaveBeenCalled();
    });

    it('D1 (час — дата рядком): звичайна догрузка не відкидається', () => {
        const D1_MS = 86_400_000;
        const firstMs = Date.UTC(2026, 5, 1, 21); // 01.06 21:00 UTC — відкриття D1 влітку
        engine.setTfS(86400);
        engine.setData(bars(firstMs, firstMs + 99 * D1_MS, D1_MS));
        edgeReached();
        expect(requested).toEqual([firstMs]);
        engine.prependData(bars(firstMs - 49 * D1_MS, firstMs, D1_MS));

        expect(engine.series.data()).toHaveLength(149);
        expect(engine.series.data()[0].time).toBe('2026-04-14');
        expect(consoleWarn).not.toHaveBeenCalled();
    });
});
