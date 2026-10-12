// src/chart/drawings/timeMap.test.ts
// ADR-0082 D6: специфікація fractional time-мапінгу (cross-TF рендер якорів).

import { describe, it, expect } from 'vitest';
import { timeToFractionalIndex, fractionalIndexToTime, medianStep, fractionalLogicalToX, shiftTimeByBars } from './timeMap';

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

/** Шкала, як у LWC 5.1.0: координата лише для ЦІЛОГО індексу, дробовий → 0 (`_internal_indexToCoordinate`). */
const lwcLikeScale = (spacing: number, origin: number) => (index: number): number | null =>
  index % 1 === 0 ? origin + index * spacing : 0;

describe('fractionalLogicalToX (LWC рахує лише цілий індекс)', () => {
  const toX = lwcLikeScale(8, 100);

  it('сама шкала LWC на дробовому індексі дає 0 — саме це кидало фігури на лівий край', () => {
    expect(toX(10.25)).toBe(0);
  });

  it('дробовий індекс — інтерполяція між сусідніми цілими', () => {
    expect(fractionalLogicalToX(10.25, toX)).toBeCloseTo(100 + 10.25 * 8, 10);
    expect(fractionalLogicalToX(-2.5, toX)).toBeCloseTo(100 - 2.5 * 8, 10); // до першого бару
  });

  it('цілий індекс — та сама координата без другого виклику', () => {
    expect(fractionalLogicalToX(7, toX)).toBe(156);
  });

  it('порожня шкала → null; нема сусіда справа → координата лівого', () => {
    expect(fractionalLogicalToX(3.5, () => null)).toBeNull();
    expect(fractionalLogicalToX(3.5, (i) => (i === 3 ? 42 : null))).toBe(42);
  });

  it('якір з M15 (10:15) на H1-сітці стоїть між 10:00 і 11:00, а не на x=0', () => {
    const idx = timeToFractionalIndex(H1, 8100)!; // 1.25
    expect(fractionalLogicalToX(idx, toX)).toBeCloseTo(100 + 1.25 * 8, 10);
  });
});

describe('shiftTimeByBars (перетягування тіла фігури)', () => {
  it('зсув на +1 бар H1 зберігає частку бару: 10:15 → 11:15', () => {
    expect(shiftTimeByBars(H1, 8100, 1)).toBeCloseTo(11700, 6);
  });

  it('зсув на 0 — той самий час; на бар сітки — точно на бар', () => {
    expect(shiftTimeByBars(H1, 8100, 0)).toBeCloseTo(8100, 6);
    expect(shiftTimeByBars(H1, 7200, 2)).toBe(14400);
  });

  it('за останній бар — у майбутнє за кроком, без стрибка', () => {
    expect(shiftTimeByBars(H1, 14400, 3, 3600)).toBe(14400 + 3 * 3600);
  });

  it('порожня сітка → null (точку не рухати)', () => {
    expect(shiftTimeByBars([], 8100, 1)).toBeNull();
  });

  // Сітка H1 з вихідними: Пт 18:00, 19:00, 20:00 → Нд 22:00, 23:00 (години від умовного нуля)
  const WEEKEND = [18, 19, 20, 70, 71].map((h) => h * 3600);

  it('через вихідні — хвилини всередині бару, а не частка інтервалу з гепом', () => {
    // Пт 18:30 +3 бари → Нд 22:30 (бар 70 год), не субота
    expect(shiftTimeByBars(WEEKEND, 18.5 * 3600, 3, 3600)).toBe(70.5 * 3600);
    // Нд 22:30 −3 бари → назад рівно на Пт 18:30
    expect(shiftTimeByBars(WEEKEND, 70.5 * 3600, -3, 3600)).toBe(18.5 * 3600);
  });

  it('якір у самому гепі (Пт 20:45, після останнього бару) — відступ обмежено кроком, не 50 год', () => {
    expect(shiftTimeByBars(WEEKEND, 20.75 * 3600, -1, 3600)).toBe(19.75 * 3600);
  });

  it('0 барів — час без змін навіть на сітці з одного бару', () => {
    expect(shiftTimeByBars([3600], 5000, 0)).toBe(5000);
  });
});
