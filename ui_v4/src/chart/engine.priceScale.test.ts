// ADR-0073 rev 6 — ціна на price scale: лінія поточної ціни і ціна рівня курсора.
// Контракт опцій, з якими ChartEngine створює LWC-чарт і свічкову серію. Обидва — нативні
// елементи LWC; мітку crosshair можна задати лише в createChart (applyOptions після init її
// не вмикав, rev 5), тож регресію ловить тільки перевірка опцій створення.
// Колір лінії й мітки ціни: LWC бере `priceLineColor || колір тіла бару`, а в стилях gray/hollow
// тіло прозоре — тому engine задає priceLineColor явно, з непрозорого бордюру, за напрямком бару.

import { describe, it, expect, vi } from 'vitest';
import { LineStyle, PriceLineSource } from 'lightweight-charts';
import type { Candle } from '../types';

const created = vi.hoisted(() => ({
    chartOptions: undefined as any,
    seriesOptions: [] as any[],
    candleApplied: [] as any[],
}));

vi.mock('lightweight-charts', async (importOriginal) => {
    const lwc = await importOriginal<typeof import('lightweight-charts')>();
    // Інертний чарт: конструктору потрібні лише виклики API, не рендер; data() серій — порожня.
    const inert: any = new Proxy(function () {}, {
        get: (_target, key) => (key === 'then' ? undefined : key === 'data' ? () => [] : inert),
        apply: () => inert,
    });
    // Свічкова серія (перша addSeries) записує applyOptions — решта інертна.
    const candleSeries = new Proxy({}, {
        get: (_target, key) =>
            key === 'applyOptions'
                ? (options: unknown) => { created.candleApplied.push(options); }
                : inert[key as string],
    });
    const chart = new Proxy({}, {
        get: (_target, key) =>
            key === 'addSeries'
                ? (_type: unknown, options: unknown) => {
                      created.seriesOptions.push(options);
                      return created.seriesOptions.length === 1 ? candleSeries : inert;
                  }
                : inert,
    });
    return {
        ...lwc,
        createChart: (_container: unknown, options: unknown) => {
            created.chartOptions = options;
            return chart;
        },
    };
});

import { ChartEngine } from './engine';

const UP_BAR: Candle = { t_ms: 1_790_000_000_000, o: 100, h: 102, l: 99, c: 101 };
const DOWN_BAR: Candle = { t_ms: 1_790_000_000_000, o: 101, h: 102, l: 99, c: 100 };

/** Останній явно заданий колір лінії/мітки ціни. */
function lastPriceLineColor(): string | undefined {
    const withColor = created.candleApplied.filter((o) => o && 'priceLineColor' in o);
    return withColor.length ? withColor[withColor.length - 1].priceLineColor : undefined;
}

describe('ChartEngine — ціна на price scale (ADR-0073 rev 6)', () => {
    const engine = new ChartEngine({} as HTMLElement, () => {}, () => {});
    const [candleOptions] = created.seriesOptions;

    it('лінія поточної ціни — рідкі крапки по close останнього (формуючого) бару', () => {
        expect(candleOptions).toMatchObject({
            priceLineVisible: true,
            priceLineSource: PriceLineSource.LastBar,
            priceLineStyle: LineStyle.SparseDotted,
            lastValueVisible: true,
        });
    });

    it('crosshair показує ціну рівня курсора на шкалі', () => {
        expect(created.chartOptions.crosshair.horzLine.labelVisible).toBe(true);
    });

    it.each([
        // [стиль, бар, очікуваний колір — бордюр напрямку, не прозоре тіло]
        ['hollow', UP_BAR, '#26a69a'],   // тіло росту rgba(255,255,255,0)
        ['hollow', DOWN_BAR, '#ef5350'],
        ['gray', DOWN_BAR, '#5f6368'],   // тіло спаду rgba(255,255,255,0)
        ['gray', UP_BAR, '#9aa0a6'],
        ['classic', DOWN_BAR, '#ef5350'],
    ] as const)('стиль %s: колір лінії й мітки ціни — непрозорий бордюр напрямку бару', (style, bar, ink) => {
        engine.clearAll();
        engine.applyCandleStyle(style);
        engine.setData([bar]);
        expect(lastPriceLineColor()).toBe(ink);
    });

    it('зміна стилю перефарбовує лінію для поточного напрямку бару', () => {
        engine.applyCandleStyle('classic');
        engine.setData([DOWN_BAR]);
        engine.applyCandleStyle('gray');
        expect(lastPriceLineColor()).toBe('#5f6368');
    });
});
