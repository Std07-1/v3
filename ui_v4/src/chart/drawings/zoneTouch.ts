// src/chart/drawings/zoneTouch.ts
// ADR-0111 §3.7: зона OB/FVG тягнеться вправо до першої свічки поточного TF, яка тінню зайшла в зону. Пошук — тут, а
// не в DrawingsRenderer.ts (X33: файл під межею 1500 рядків), з пам'яттю: знайдений дотик щокадру не шукається
// заново, незнайдений дошукується лише від останньої перевіреної свічки (жива свічка росте з тіками).
//
// Відлік — зі свічки після тієї, що містить правий край зони: правий край ставить трейдер («звідси зона живе»).
// На старшому TF, якщо дотик стався всередині свічки, де закінчується зона, видно його лише на наступній —
// порядок цін усередині свічки невідомий. Права межа старша за завантажену історію — пошук від першої свічки
// історії (дотик «не пізніше за»), прокрутка назад довантажує історію й перераховує.
//
// Кожен бар звіряється з кешем часів: на мить перемикання TF чи довантаження історії бар LWC за індексом (логічний
// індекс шкали часу) і кеш часів renderer-а розходяться — тоді нічого не запам'ятовується, наступний кадр рахує знову
// (стенд 19.10: без звірки хибний «дотик» з такої миті лишався в пам'яті й обривав живу зону на H1).

import { timeToFractionalIndex } from './timeMap';

/** Бар для пошуку: час відкриття (с) і діапазон ціни — з даних серії (LWC), тож і поточна свічка рахується. */
export interface TouchBar {
  readonly timeSec: number;
  readonly low: number;
  readonly high: number;
}

/** Що renderer дає пошуку: кеш часів відкриття барів (новий масив = нова історія) і бар за індексом. */
export interface TouchSeries {
  readonly barTimesSec: () => readonly number[];
  readonly barStepSec: () => number;
  /** null — бару нема або він без ціни (whitespace). */
  readonly barAt: (idx: number) => TouchBar | null;
  /** Бар і кеш часів розійшлися (дотик цього кадру не відомий) — гучно, не тихо (I5). */
  readonly onMisaligned?: () => void;
}

/** Час (мс) першої свічки після `fromMs`, що торкнулась [lo, hi]; null — ще не торкалась (або не відомо цього кадру). */
export type FirstTouchMs = (fromMs: number, lo: number, hi: number) => number | null;

/** Бар серії LWC → бар для пошуку; без high/low (whitespace) — null. */
export function toTouchBar<T>(
  item: { readonly time: T; readonly high?: number; readonly low?: number } | null | undefined,
  timeToSec: (time: T) => number,
): TouchBar | null {
  if (!item || typeof item.high !== 'number' || typeof item.low !== 'number') return null;
  return { timeSec: timeToSec(item.time), low: item.low, high: item.high };
}

/** Результат перегляду барів: індекс дотику (null — не було до `scannedTo`) або «бари й кеш часів розійшлися». */
type Scan = { readonly idx: number | null; readonly scannedTo: number } | 'misaligned';

/** Перший бар від `fromIdx`, чий діапазон перетинає [lo, hi]. */
export function scanForTouch(
  barTimes: readonly number[],
  barAt: (idx: number) => TouchBar | null,
  fromIdx: number,
  lo: number,
  hi: number,
): Scan {
  for (let i = Math.max(0, fromIdx); i < barTimes.length; i++) {
    const bar = barAt(i);
    if (bar === null) continue;
    if (bar.timeSec !== barTimes[i]) return 'misaligned';
    if (bar.low <= hi && bar.high >= lo) return { idx: i, scannedTo: barTimes.length };
  }
  return { idx: null, scannedTo: barTimes.length };
}

/** Пам'ять — на зону з її межами; перетягування зони щокадру дає нові межі, тож пам'ять обмежена. */
const MEMO_LIMIT = 256;

/** Пошук дотику з пам'яттю на поточну історію: нова історія (інший масив часів) — пам'ять з нуля. */
export function createFirstTouchLookup(series: TouchSeries): FirstTouchMs {
  let times: readonly number[] | null = null;
  const memo = new Map<string, { readonly idx: number | null; readonly scannedTo: number }>();

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
    const scan = scanForTouch(barTimes, series.barAt, resumeAt, lo, hi);
    if (scan === 'misaligned') {
      series.onMisaligned?.();
      return null;
    }
    if (memo.size >= MEMO_LIMIT) memo.clear();
    memo.set(key, scan);
    return scan.idx === null ? null : barTimes[scan.idx] * 1000;
  };
}
