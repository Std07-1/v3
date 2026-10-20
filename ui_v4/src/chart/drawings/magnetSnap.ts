// src/chart/drawings/magnetSnap.ts
// ADR-0074 T4 (магніт): найближчий до курсора рівень свічки за відстанню в пікселях. Чиста функція поза
// DrawingsRenderer.ts (X33: файл під межею 1500 рядків); радіус прилипання (миша 120 px, палець 16 px, ADR-0112 D7)
// і позначку магніту лишає renderer.

/** Які рівні свічки бере магніт. */
export type SnapMode = 'ohlc' | 'hl' | 'close';

export interface OhlcBar {
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
}

/** Рівні свічки для режиму магніту. */
export function snapCandidates(bar: OhlcBar, mode: SnapMode): readonly number[] {
  if (mode === 'close') return [bar.close];
  if (mode === 'hl') return [bar.high, bar.low];
  return [bar.high, bar.low, bar.open, bar.close];
}

/** Найближчий до `y` рівень свічки і відстань до нього (CSS px); null — жоден рівень не має координати. */
export function nearestSnapLevel(
  bar: OhlcBar,
  mode: SnapMode,
  y: number,
  priceToY: (price: number) => number | null,
): { price: number; distPx: number } | null {
  let best: { price: number; distPx: number } | null = null;
  for (const price of snapCandidates(bar, mode)) {
    const py = priceToY(price);
    if (py === null) continue;
    const distPx = Math.abs(y - py);
    if (best === null || distPx < best.distPx) best = { price, distPx };
  }
  return best;
}
