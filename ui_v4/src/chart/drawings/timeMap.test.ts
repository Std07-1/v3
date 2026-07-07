// src/chart/drawings/timeMap.test.ts
// ADR-0082 D6: специфікація fractional time-мапінгу (cross-TF рендер якорів).

import { describe, it, expect } from 'vitest';
import { timeToFractionalIndex, fractionalIndexToTime, medianStep } from './timeMap';

// H1-сітка: бари щогодини (сек)
const H1 = [3600, 7200, 10800, 14400];

describe('timeToFractionalIndex', () => {
  it('точний збіг часу бару → цілий індекс', () => {
    expect(timeToFractionalIndex(H1, 7200)).toBe(1);
  });

  it('час МІЖ барами (якір з M15 на H1-сітці) → дробовий індекс', () => {
    // 10:15 між 10:00(idx1=7200) і 11:00(idx2=10800) → 1.25
    expect(timeToFractionalIndex(H1, 8100)).toBeCloseTo(1.25, 10);
  });

  it('час ПІСЛЯ останнього бару → екстраполяція за кроком (draw-into-future)', () => {
    expect(timeToFractionalIndex(H1, 18000)).toBeCloseTo(4, 10); // 14400+3600
  });

  it('час ПЕРЕД першим баром → від\'ємна екстраполяція', () => {
    expect(timeToFractionalIndex(H1, 0)).toBeCloseTo(-1, 10);
  });

  it('порожньо → null; один бар → 0', () => {
    expect(timeToFractionalIndex([], 100)).toBeNull();
    expect(timeToFractionalIndex([500], 999)).toBe(0);
  });

  it('нерівномірні бари (session gap) — інтерполяція в межах фактичної пари', () => {
    const gapped = [100, 200, 1000]; // gap 200→1000
    expect(timeToFractionalIndex(gapped, 600)).toBeCloseTo(1.5, 10);
  });
});

describe('fractionalIndexToTime (інверсія)', () => {
  it('roundtrip: t → idx → t для внутрішніх значень', () => {
    for (const t of [3600, 5400, 8100, 12345]) {
      const idx = timeToFractionalIndex(H1, t)!;
      expect(fractionalIndexToTime(H1, idx)).toBeCloseTo(t, 6);
    }
  });

  it('roundtrip за краями (екстраполяція обома напрямками)', () => {
    for (const t of [0, 18000, 21600]) {
      const idx = timeToFractionalIndex(H1, t)!;
      expect(fractionalIndexToTime(H1, idx)).toBeCloseTo(t, 6);
    }
  });

  it('порожньо → null; один бар → його час', () => {
    expect(fractionalIndexToTime([], 1)).toBeNull();
    expect(fractionalIndexToTime([500], 3)).toBe(500);
  });
});

describe('medianStep (ADR-0083 — gap-стійка екстраполяція)', () => {
  it('рівномірні бари → крок', () => {
    expect(medianStep(H1)).toBe(3600);
  });

  it('weekend-gap на КРАЮ не спотворює крок (медіана відкидає)', () => {
    // Пт 20:00, 21:00 ... + Нд-gap 48h останньою парою
    const withGap = [3600, 7200, 10800, 14400, 14400 + 48 * 3600];
    expect(medianStep(withGap)).toBe(3600);
  });

  it('<2 барів → 0 (нема кроку)', () => {
    expect(medianStep([])).toBe(0);
    expect(medianStep([500])).toBe(0);
  });
});

describe('екстраполяція зі stepSec (draw-into-future, ADR-0083)', () => {
  const gapped = [3600, 7200, 10800, 14400, 14400 + 48 * 3600];
  const step = medianStep(gapped); // 3600, не 48h

  it('future-якір мапиться медіанним кроком, не gap-парою', () => {
    const lastIdx = gapped.length - 1;
    const t = gapped[lastIdx] + 2 * 3600; // +2 «типові» бари за останнім
    expect(timeToFractionalIndex(gapped, t, step)).toBeCloseTo(lastIdx + 2, 10);
  });

  it('roundtrip у майбутньому точний за спільним stepSec', () => {
    for (const t of [gapped[4] + 3600, gapped[4] + 10 * 3600]) {
      const idx = timeToFractionalIndex(gapped, t, step)!;
      expect(fractionalIndexToTime(gapped, idx, step)).toBeCloseTo(t, 6);
    }
  });

  it('stepSec=0 → fallback до кроку крайньої пари (сумісність)', () => {
    expect(timeToFractionalIndex(H1, 18000, 0)).toBeCloseTo(4, 10);
  });

  it('внутрішні значення НЕ залежать від stepSec (інтерполяція фактичною парою)', () => {
    expect(timeToFractionalIndex(H1, 8100, 999)).toBeCloseTo(1.25, 10);
  });
});
