// src/chart/drawings/drawingDrag.ts
// Перетягування фігури (ручка або тіло) — чисті функції поза DrawingsRenderer.ts (X33: файл біля межі 1500 рядків;
// раніше ця математика жила в handleDragMove без тестів). Renderer дає простір координат (LWC + кеш часів барів),
// модуль повертає нову фігуру або причину пропустити крок (renderer пише її гучно — warnOnce, I5).

import type { Drawing, T_MS } from '../../types';
import { cloneDrawing } from './drawingDraft';

/** Перетворення координат, через які тягнеться фігура (renderer: LWC timeScale/series і кеш часів барів). */
export interface DragSpace {
  /** Цілий logical-індекс бару під x (LWC coordinateToLogical) або null. */
  logicalAt(x: number): number | null;
  fromX(x: number): T_MS | null;
  fromY(y: number): number | null;
  toY(price: number): number | null;
  /** Ціна з магнітом (якщо увімкнений) — для ручки. */
  snapPrice(x: number, y: number, rawPrice: number): number;
  /** Час (с), зсунутий на ціле число барів поточного TF зі збереженням відстані від відкриття (timeMap.shiftTimeByBars). */
  shiftTimeByBars(tSec: number, bars: number): number | null;
}

/** Звідки почалось перетягування. */
export interface DragOrigin {
  readonly startX: number;
  readonly startY: number;
  readonly startObj: Drawing;
  /** Ручка мінус палець у момент захоплення (лише дотик, grabOffsetFor); нема — ручка рівно під вказівником. */
  readonly grabOffset?: { readonly x: number; readonly y: number };
}

/** Відстань від пальця до ручки, коли її взяли (ADR-0112 D8): дотик тягне ручку, зберігаючи цю відстань, — палець
 *  закриває точку, а стрибок ручки під палець (до 20 px допуску) читався як «перескакує». Миша й перо — рівно під
 *  вказівником, як було (курсор — інструмент точності): undefined. */
export function grabOffsetFor(
  pointerType: string,
  handle: { readonly x: number | null; readonly y: number | null },
  x: number,
  y: number,
): { x: number; y: number } | undefined {
  if (pointerType !== 'touch' || handle.x === null || handle.y === null) return undefined;
  return { x: handle.x - x, y: handle.y - y };
}

export type DragStep =
  | { readonly kind: 'moved'; readonly next: Drawing }
  | { readonly kind: 'skip'; readonly warnKey: string; readonly detail: string };

/** Ручка: ціна — під вказівником (з магнітом), час — бар під вказівником; поки він у тому самому барі, що на
 *  початку, час якоря не чіпаємо (якір з іншого TF — M15 13:45 на H1 — не стає на бар цього TF, рецензія 12.10).
 *  Дотик — зі зсувом захоплення (origin.grabOffset). */
export function dragHandle(
  current: Drawing,
  origin: DragOrigin,
  handleIdx: number,
  x: number,
  y: number,
  space: DragSpace,
): DragStep {
  const offX = origin.grabOffset?.x ?? 0;
  const offY = origin.grabOffset?.y ?? 0;
  const hx = x + offX;
  const hy = y + offY;
  const sameBar = space.logicalAt(hx) === space.logicalAt(origin.startX + offX);
  const t_ms = sameBar ? origin.startObj.points[handleIdx].t_ms : space.fromX(hx);
  const rawPrice = space.fromY(hy);
  if (t_ms === null || rawPrice === null) {
    return { kind: 'skip', warnKey: 'drag_handle_null', detail: `drag handle: cannot map x=${hx},y=${hy}` };
  }
  const next = cloneDrawing(current);
  next.points[handleIdx] = { t_ms, price: space.snapPrice(hx, hy, rawPrice) };
  return { kind: 'moved', next };
}

/** Тіло: без магніту (щоб не тремтіло). Час — зсув на ціле число барів (різниця logical під курсором), не через x
 *  якоря: якір з іншого TF зберігає частку бару, а хибний x не переписує час (S0 12.10 — x=0 зсував обидва якорі до
 *  лівого краю). Ціна — той самий зсув у пікселях для кожної точки. Точка, яку не перевести, лишається на місці. */
export function dragBody(current: Drawing, origin: DragOrigin, x: number, y: number, space: DragSpace): DragStep {
  const fromLogical = space.logicalAt(origin.startX);
  const toLogical = space.logicalAt(x);
  if (fromLogical === null || toLogical === null) {
    return { kind: 'skip', warnKey: 'drag_body_logical_null', detail: `drag body: cannot map x=${x}` };
  }
  const bars = toLogical - fromLogical;
  const dy = y - origin.startY;
  const next = cloneDrawing(current);
  for (let i = 0; i < origin.startObj.points.length; i++) {
    const startPoint = origin.startObj.points[i];
    const oldY = space.toY(startPoint.price);
    if (oldY === null) return { kind: 'skip', warnKey: 'drag_body_old_null', detail: 'drag body: old point out of view' };
    const newTimeSec = space.shiftTimeByBars(startPoint.t_ms / 1000, bars);
    const newPrice = space.fromY(oldY + dy);
    next.points[i] = {
      t_ms: newTimeSec === null ? current.points[i].t_ms : (Math.round(newTimeSec * 1000) as T_MS),
      price: newPrice ?? current.points[i].price,
    };
  }
  return { kind: 'moved', next };
}
