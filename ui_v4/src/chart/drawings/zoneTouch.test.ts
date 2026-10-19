// src/chart/drawings/zoneTouch.test.ts — ADR-0111 §3.7: зона тягнеться до першої свічки, що тінню зайшла в зону.
import { describe, expect, it } from 'vitest';
import { createFirstTouchLookup, firstTouchIndex, type BarRange } from './zoneTouch';

// Бари по хвилині від 0 с: 0 — сама зона, 1 — вище, 2 — тінню зайшла в [98, 100], 3 — нижче.
const ZONE_BARS: BarRange[] = [
  { low: 98, high: 101 },
  { low: 103, high: 108 },
  { low: 99.5, high: 104 },
  { low: 90, high: 95 },
];

function series(bars: BarRange[], times = bars.map((_, i) => i * 60)) {
  const reads: number[] = [];
  return {
    reads,
    times,
    lookup: createFirstTouchLookup({
      barTimesSec: () => times,
      barStepSec: () => 60,
      barAt: (i) => {
        reads.push(i);
        return bars[i] ?? null;
      },
    }),
  };
}

describe('firstTouchIndex', () => {
  const barAt = (i: number) => ZONE_BARS[i] ?? null;

  it('перша свічка, чий діапазон перетинає зону', () => {
    expect(firstTouchIndex(1, ZONE_BARS.length, barAt, 98, 100)).toBe(2);
  });

  it('не торкалась — null; бари без даних (whitespace) пропускаються', () => {
    expect(firstTouchIndex(1, ZONE_BARS.length, barAt, 80, 85)).toBeNull();
    expect(firstTouchIndex(3, 6, (i) => (i < 4 ? barAt(i) : null), 98, 100)).toBeNull();
  });
});

describe('createFirstTouchLookup', () => {
  it('відлік — зі свічки після правого краю: свічка самої зони не рахується', () => {
    const { lookup } = series(ZONE_BARS);
    expect(lookup(0, 98, 100)).toBe(120_000);
  });

  it('правий край всередині свічки (якір з молодшого TF) — від наступної свічки', () => {
    const { lookup } = series(ZONE_BARS);
    expect(lookup(30_000, 98, 100)).toBe(120_000);
    expect(lookup(150_000, 98, 100)).toBeNull();
  });

  it('правий край у майбутньому — дотику ще нема', () => {
    const { lookup } = series(ZONE_BARS);
    expect(lookup(600_000, 98, 100)).toBeNull();
  });

  it('знайдений дотик щокадру не шукається заново', () => {
    const s = series(ZONE_BARS);
    s.lookup(0, 98, 100);
    const readsAfterFirst = s.reads.length;
    expect(s.lookup(0, 98, 100)).toBe(120_000);
    expect(s.reads.length).toBe(readsAfterFirst);
  });

  it('незнайдений — дошукується з останньої свічки: жива свічка, що доросла до зони, ловиться', () => {
    const bars = ZONE_BARS.map((b) => ({ ...b }));
    const s = series(bars);
    expect(s.lookup(120_000, 80, 85)).toBeNull();
    s.reads.length = 0;
    bars[3] = { low: 84, high: 95 }; // тік опустив low живої свічки в зону
    expect(s.lookup(120_000, 80, 85)).toBe(180_000);
    expect(s.reads).toEqual([3]);
  });

  it('нова історія (інший масив часів) — пам\'ять з нуля', () => {
    let times = [0, 60, 120, 180];
    let bars = ZONE_BARS;
    const lookup = createFirstTouchLookup({ barTimesSec: () => times, barStepSec: () => 60, barAt: (i) => bars[i] ?? null });
    expect(lookup(0, 98, 100)).toBe(120_000);
    // Довантажили старішу історію: ті самі часи зсунулись на 2 індекси
    times = [-120, -60, 0, 60, 120, 180];
    bars = [{ low: 0, high: 1 }, { low: 0, high: 1 }, ...ZONE_BARS];
    expect(lookup(0, 98, 100)).toBe(120_000);
  });
});
