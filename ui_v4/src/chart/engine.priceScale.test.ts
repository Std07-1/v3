// ADR-0073 rev 6 — ціна на price scale: лінія поточної ціни і ціна рівня курсора.
// Контракт опцій, з якими ChartEngine створює LWC-чарт і свічкову серію. Обидва — нативні
// елементи LWC; мітку crosshair можна задати лише в createChart (applyOptions після init її
// не вмикав, rev 5), тож регресію ловить тільки перевірка опцій створення.

import { describe, it, expect, vi } from 'vitest';
import { LineStyle, PriceLineSource } from 'lightweight-charts';

const created = vi.hoisted(() => ({
    chartOptions: undefined as any,
    seriesOptions: [] as any[],
}));

vi.mock('lightweight-charts', async (importOriginal) => {
    const lwc = await importOriginal<typeof import('lightweight-charts')>();
    // Інертний чарт: конструктору потрібні лише виклики API, не рендер.
    const inert: any = new Proxy(function () {}, {
        get: (_target, key) => (key === 'then' ? undefined : inert),
        apply: () => inert,
    });
    const chart = new Proxy({}, {
        get: (_target, key) =>
            key === 'addSeries'
                ? (_type: unknown, options: unknown) => {
                      created.seriesOptions.push(options);
                      return inert;
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

describe('ChartEngine — ціна на price scale (ADR-0073 rev 6)', () => {
    new ChartEngine({} as HTMLElement, () => {}, () => {});
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
});
