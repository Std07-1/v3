// src/chart/drawings/zoneTouch.test.ts — ADR-0111 §3.7: зона тягнеться до першої свічки, що тінню зайшла в зону.
import { describe, expect, it } from 'vitest';
import { createFirstTouchLookup, scanForTouch, toTouchBar, type TouchBar } from './zoneTouch';

// Бари по хвилині від 0 с: 0 — сама зона, 1 — вище, 2 — тінню зайшла в [98, 100], 3 — нижче.
const RANGES = [
  { low: 98, high: 101 },
  { low: 103, high: 108 },
  { low: 99.5, high: 104 },
  { low: 90, high: 95 },
];
const barsAt = (ranges: { low: number; high: number }[], times: number[]): TouchBar[] =>
  ranges.map((r, i) => ({ timeSec: times[i], ...r }));

function series(ranges = RANGES, times = ranges.map((_, i) => i * 60)) {
  const bars = barsAt(ranges, times);
  const reads: number[] = [];
  let misaligned = 0;
  return {
    reads,
    bars,
    misaligned: () => misaligned,
    lookup: createFirstTouchLookup({
      barTimesSec: () => times,
      barStepSec: () => 60,
      barAt: (i) => {
        reads.push(i);
        return bars[i] ?? null;
      },
      onMisaligned: () => misaligned++,
    }),
  };
}

describe('scanForTouch', () => {
  const times = [0, 60, 120, 180];
  const bars = barsAt(RANGES, times);

  it('перший бар, чий діапазон перетинає зону; не торкалась — null', () => {
    expect(scanForTouch(times, (i) => bars[i], 1, 98, 100)).toEqual({ idx: 2, scannedTo: 4 });
    expect(scanForTouch(times, (i) => bars[i], 1, 80, 85)).toEqual({ idx: null, scannedTo: 4 });
  });

  it('LWC бару ще не віддає (мить перемикання) — «розійшлися», а не «не торкалась до кінця»', () => {
    expect(scanForTouch(times, (i) => (i === 2 ? null : bars[i]), 1, 98, 100)).toBe('misaligned');
  });

  it('бар не з того часу, що кеш (мить перемикання TF) — «розійшлися», а не хибний дотик', () => {
    const shifted = (i: number) => ({ ...bars[i], timeSec: bars[i].timeSec + 3600 });
    expect(scanForTouch(times, shifted, 1, 98, 100)).toBe('misaligned');
  });
});

describe('toTouchBar', () => {
  it('свічка LWC — час і діапазон; whitespace без high/low — null', () => {
    expect(toTouchBar({ time: 120, high: 104, low: 99.5 }, (t) => t)).toEqual({ timeSec: 120, low: 99.5, high: 104 });
    expect(toTouchBar({ time: 120 }, (t) => t)).toBeNull();
    expect(toTouchBar(null, (t: number) => t)).toBeNull();
  });
});

describe('createFirstTouchLookup', () => {
  it('відлік — зі свічки після правого краю: свічка самої зони не рахується', () => {
    expect(series().lookup(0, 98, 100)).toBe(120_000);
  });

  it('правий край всередині свічки (якір з молодшого TF) — від наступної свічки; у майбутньому — дотику нема', () => {
    const { lookup } = series();
    expect(lookup(30_000, 98, 100)).toBe(120_000);
    expect(lookup(150_000, 98, 100)).toBeNull();
    expect(lookup(600_000, 98, 100)).toBeNull();
  });

  it('знайдений дотик щокадру не шукається заново', () => {
    const s = series();
    s.lookup(0, 98, 100);
    const readsAfterFirst = s.reads.length;
    expect(s.lookup(0, 98, 100)).toBe(120_000);
    expect(s.reads.length).toBe(readsAfterFirst);
  });

  it('незнайдений — дошукується з останньої свічки: жива свічка, що доросла до зони, ловиться', () => {
    const s = series();
    expect(s.lookup(120_000, 80, 85)).toBeNull();
    s.reads.length = 0;
    s.bars[3] = { timeSec: 180, low: 84, high: 95 }; // тік опустив low живої свічки в зону
    expect(s.lookup(120_000, 80, 85)).toBe(180_000);
    expect(s.reads).toEqual([3]);
  });

  it('бари й кеш розійшлись — дотик цього кадру не відомий, не запам’ятовується, сигнал; зійшлись — правильний', () => {
    const s = series();
    const good = [...s.bars];
    // Бари LWC уже з іншого TF (зсунуті на годину), а кеш часів ще старий
    s.bars.splice(0, 4, ...good.map((bar) => ({ ...bar, timeSec: bar.timeSec + 3600 })));
    expect(s.lookup(0, 98, 100)).toBeNull();
    expect(s.misaligned()).toBe(1);
    s.bars.splice(0, 4, ...good);
    expect(s.lookup(0, 98, 100)).toBe(120_000);
  });

  it('повна заміна даних (reset) — ті самі часи, нові ціни: дотик шукається заново', () => {
    const times = [0, 60, 120, 180];
    let bars = barsAt(RANGES, times);
    const lookup = createFirstTouchLookup({ barTimesSec: () => times, barStepSec: () => 60, barAt: (i) => bars[i] ?? null });
    expect(lookup(0, 80, 85)).toBeNull();
    bars = barsAt([...RANGES.slice(0, 2), { low: 84, high: 90 }, RANGES[3]], times);
    expect(lookup(0, 80, 85)).toBeNull(); // без reset: бар 2 уже перевірено — пам'ять «не торкалась»
    lookup.reset();
    expect(lookup(0, 80, 85)).toBe(120_000);
  });

  it('нова історія (інший масив часів) — пам’ять з нуля', () => {
    let times = [0, 60, 120, 180];
    let bars = barsAt(RANGES, times);
    const lookup = createFirstTouchLookup({ barTimesSec: () => times, barStepSec: () => 60, barAt: (i) => bars[i] ?? null });
    expect(lookup(0, 98, 100)).toBe(120_000);
    // Довантажили старішу історію: ті самі часи зсунулись на 2 індекси
    times = [-120, -60, 0, 60, 120, 180];
    bars = barsAt([{ low: 0, high: 1 }, { low: 0, high: 1 }, ...RANGES], times);
    expect(lookup(0, 98, 100)).toBe(120_000);
  });
});
