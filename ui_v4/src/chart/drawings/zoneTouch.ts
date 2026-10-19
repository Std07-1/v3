// src/chart/drawings/zoneTouch.ts
// ADR-0111 §3.7: зона OB/FVG тягнеться вправо до першої свічки поточного TF, яка тінню зайшла в зону. Пошук — тут, а
// не в DrawingsRenderer.ts (X33: файл під межею 1500 рядків), з пам'яттю: знайдений дотик щокадру не шукається
// заново, незнайдений дошукується лише від останньої перевіреної свічки (жива свічка росте з тіками).
//
// Відлік — зі свічки після тієї, що містить правий край зони: правий край ставить трейдер («звідси зона живе»).
// На старшому TF, якщо дотик стався всередині свічки, де закінчується зона, видно його лише на наступній —
// порядок цін усередині свічки невідомий. Права межа старша за завантажену історію — пошук від першої свічки
// історії (дотик «не пізніше за»), прокрутка назад довантажує історію й перераховує.

import { timeToFractionalIndex } from './timeMap';

/** Діапазон ціни свічки: high/low — з даних серії (LWC), тож і поточна свічка рахується. */
export interface BarRange {
  readonly low: number;
  readonly high: number;
}

/** Що renderer дає пошуку: кеш часів відкриття барів (новий масив = нова історія) і діапазон бару за індексом. */
export interface TouchSeries {
  readonly barTimesSec: () => readonly number[];
  readonly barStepSec: () => number;
  readonly barAt: (idx: number) => BarRange | null;
}

/** Час (мс) першої свічки після `fromMs`, що торкнулась [lo, hi]; null — ще не торкалась. */
export type FirstTouchMs = (fromMs: number, lo: number, hi: number) => number | null;

/** Індекс першого бару від `fromIdx`, чий діапазон [low, high] перетинає зону [lo, hi]; null — ще не торкалась. */
export function firstTouchIndex(
  fromIdx: number,
  count: number,
  barAt: (i: number) => BarRange | null,
  lo: number,
  hi: number,
): number | null {
  for (let i = Math.max(0, fromIdx); i < count; i++) {
    const bar = barAt(i);
    if (bar && bar.low <= hi && bar.high >= lo) return i;
  }
  return null;
}

/** Пам'ять — на зону з її межами; перетягування зони щокадру дає нові межі, тож пам'ять обмежена. */
const MEMO_LIMIT = 256;

interface TouchMemo {
  /** Індекс дотику; null — до `scannedTo` дотику не було. */
  readonly idx: number | null;
  readonly scannedTo: number;
}

/** Пошук дотику з пам'яттю на поточну історію: нова історія (інший масив часів) — пам'ять з нуля. */
export function createFirstTouchLookup(series: TouchSeries): FirstTouchMs {
  let times: readonly number[] | null = null;
  const memo = new Map<string, TouchMemo>();

  return (fromMs, lo, hi) => {
    const barTimes = series.barTimesSec();
    if (barTimes !== times) {
      times = barTimes;
      memo.clear();
    }
    const from = timeToFractionalIndex(barTimes, fromMs / 1000, series.barStepSec());
    if (from === null) return null;
    const start = Math.floor(from) + 1;
    const key = `${fromMs}|${lo}|${hi}`;
    const known = memo.get(key);
    if (known?.idx != null) return barTimes[known.idx] * 1000;
    // Остання перевірена свічка могла бути живою — її перевіряємо ще раз.
    const resumeAt = known ? Math.max(start, known.scannedTo - 1) : start;
    const idx = firstTouchIndex(resumeAt, barTimes.length, series.barAt, lo, hi);
    if (memo.size >= MEMO_LIMIT) memo.clear();
    memo.set(key, { idx, scannedTo: barTimes.length });
    return idx === null ? null : barTimes[idx] * 1000;
  };
}
