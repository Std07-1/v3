// src/chart/interaction/geometry.ts
// SSOT: вся математика в Screen Space (CSS px)

export const HIT_TOLERANCE_PX = 6;
export const HANDLE_RADIUS_PX = 3.5;
export const HANDLE_RADIUS_HOVER_PX = 5;

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