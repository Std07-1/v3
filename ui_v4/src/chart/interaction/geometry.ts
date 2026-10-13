// src/chart/interaction/geometry.ts
// SSOT: вся математика в Screen Space (CSS px)
//
// ADR-0074 T2: HIT_TOLERANCE_PX / HANDLE_RADIUS_PX / HANDLE_RADIUS_HOVER_PX
// тепер служать ТІЛЬКИ fallback defaults коли CSS vars відсутні. Live values
// resolve-ляться через tokens.css `--drawing-*` + DrawingsRenderer cache.
// Bumped 6→10 / 3.5→5 / 5→8 щоб desktop fallbacks матчили WCAG 2.1 AA target.
// Mobile (@media pointer:coarse у tokens.css) overrides до 16/7/10.

export const HIT_TOLERANCE_PX = 10;
export const HANDLE_RADIUS_PX = 5;
export const HANDLE_RADIUS_HOVER_PX = 8;

/** ADR-0111 (поріг перетягування, 13.10): на скільки CSS px вказівник має відійти від точки натискання, щоб почалось
 *  перетягування фігури чи її ручки. Менше — це клік (вибір), фігура не змінюється: без порогу тремтіння руки на 1px
 *  переписувало ціну якоря на ціну під курсором, а тіло зсувалось на піксель. Палець і перо тремтять сильніше — як
 *  MOVE_THRESHOLD_PX (8) довгого дотику в chart/longPressLock.ts. */
export const DRAG_START_PX_MOUSE = 3;
export const DRAG_START_PX_TOUCH = 8;

/** Чи почалось перетягування: зсув від точки натискання досяг порогу для цього типу вказівника. */
export function dragStarted(dx: number, dy: number, pointerType: string): boolean {
  const threshold = pointerType === 'touch' || pointerType === 'pen' ? DRAG_START_PX_TOUCH : DRAG_START_PX_MOUSE;
  return Math.hypot(dx, dy) >= threshold;
}

export function distToHLine(cursorY: number, lineY: number): number {
  return Math.abs(cursorY - lineY);
}

export function distToPoint(px: number, py: number, x: number, y: number): number {
  return Math.hypot(px - x, py - y);
}

export function distToSegment(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number,
): number {
  const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
  if (l2 === 0) return Math.hypot(px - x1, py - y1);

  let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
  t = Math.max(0, Math.min(1, t));

  const projX = x1 + t * (x2 - x1);
  const projY = y1 + t * (y2 - y1);
  return Math.hypot(px - projX, py - projY);
}

/**
 * Влучання в ребро прямокутника з допуском tol.
 * Повертає 0 якщо курсор на ребрі (в межах tol), інакше Infinity.
 */
export function distToRectEdge(
  px: number, py: number,
  xMin: number, yMin: number,
  xMax: number, yMax: number,
  tol: number,
): number {
  const insideExpanded =
    px >= xMin - tol && px <= xMax + tol &&
    py >= yMin - tol && py <= yMax + tol;

  if (!insideExpanded) return Infinity;

  const insideInner =
    px > xMin + tol && px < xMax - tol &&
    py > yMin + tol && py < yMax - tol;

  return insideInner ? Infinity : 0;
}