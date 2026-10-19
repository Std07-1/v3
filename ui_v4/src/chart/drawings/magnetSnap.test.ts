// src/chart/drawings/magnetSnap.test.ts — ADR-0074 T4: магніт бере найближчий рівень свічки за пікселями.
import { describe, expect, it } from 'vitest';
import { nearestSnapLevel, snapCandidates } from './magnetSnap';

const BAR = { open: 102, high: 110, low: 95, close: 100 };
const priceToY = (p: number) => 1000 - p * 5; // вища ціна — вище на екрані

describe('snapCandidates', () => {
  it('режим — які рівні: OHLC, лише H/L, лише закриття', () => {
    expect(snapCandidates(BAR, 'ohlc')).toEqual([110, 95, 102, 100]);
    expect(snapCandidates(BAR, 'hl')).toEqual([110, 95]);
    expect(snapCandidates(BAR, 'close')).toEqual([100]);
  });
});

describe('nearestSnapLevel', () => {
  it('найближчий рівень за пікселями і відстань до нього', () => {
    expect(nearestSnapLevel(BAR, 'ohlc', priceToY(101.5), priceToY)).toEqual({ price: 102, distPx: 2.5 });
    expect(nearestSnapLevel(BAR, 'hl', priceToY(101.5), priceToY)).toEqual({ price: 95, distPx: 32.5 });
  });

  it('рівні без координати пропускаються; жодного — null', () => {
    const onlyHigh = (p: number) => (p === 110 ? 450 : null);
    expect(nearestSnapLevel(BAR, 'ohlc', 600, onlyHigh)).toEqual({ price: 110, distPx: 150 });
    expect(nearestSnapLevel(BAR, 'close', 600, () => null)).toBeNull();
  });
});
