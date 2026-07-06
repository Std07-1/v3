// src/chart/drawings/lineStyles.ts
// ADR-0080 (surface-2, крок 4): стилі лінії малювань — SSOT.
//
// Один список тут ітерує flyout (палітра стилів), а dashPattern() юзають усі
// три tool.render (Trend/HLine/Rect) для ctx.setLineDash — dash-візерунок
// масштабується товщиною лінії, щоб пунктир/крапки лишались пропорційні.
// D15.2: єдине джерело → UI-палітра й canvas-рендер не розсинхронюються.

import type { DrawingLineStyle } from '../../types';

// Тип живе у types.ts (SSOT типів); re-export для co-located споживачів.
export type { DrawingLineStyle } from '../../types';

export interface LineStyleSpec {
  readonly style: DrawingLineStyle;
  readonly label: string;
}

/** Порядок = порядок chips у flyout (суцільна → пунктир → крапки). */
export const DRAWING_LINE_STYLES: readonly LineStyleSpec[] = [
  { style: 'solid', label: 'Суцільна' },
  { style: 'dashed', label: 'Пунктир' },
  { style: 'dotted', label: 'Крапки' },
];

/** Canvas dash-візерунок для стилю, масштабований товщиною (px). solid/undefined
 *  → [] (суцільна). dashed → штрих×пропуск. dotted → canonical точки: нульовий
 *  штрих `[0, gap]` з `lineCap='round'` рендериться круглою крапкою діаметром
 *  lineWidth (інакше `[w, w*2]` + round-cap зливається в суцільну на antialiasing). */
export function dashPattern(
  style: DrawingLineStyle | undefined,
  lineWidth: number,
): number[] {
  const w = Math.max(1, lineWidth);
  switch (style) {
    case 'dashed':
      return [w * 4, w * 3];
    case 'dotted':
      return [0, w * 2.4];
    default:
      return [];
  }
}
