// src/chart/drawings/drawingHitTest.ts
// Що під вказівником: ручка, × видалення чи тіло фігури — чиста функція поза DrawingsRenderer.ts (X33: файл на межі
// 1500 рядків; раніше це був performHitTest без тестів). ADR-0112 D8 (14.10): ручка — найточніший намір, тож вона
// бере гору над тілом і над інтер'єром прямокутника; × — лише коли вказівник ближче до нього, ніж до ручки; ручки
// фігури під вказівником доступні й без наведення (на дотику наведення нема).

import type { Drawing, T_MS } from '../../types';
import { distToPoint } from '../interaction/geometry';
import { getToolModule, type ScreenAabb } from './tools';

/** Клікабельний радіус × — трохи більший за знак, реагує ~1 мм раніше (ADR-0079). */
export const DELETE_BTN_RADIUS_PX = 13;
/** Ручку хапаємо трохи далі за допуск лінії (ADR-0079). */
export const HANDLE_REACT_MARGIN_PX = 4;

export interface DrawingHit {
  readonly id: string;
  /** Якір, який тягнути; null — тіло. */
  readonly handleIdx: number | null;
  /** Вказівник на × активної фігури: клік видаляє, наведення тримає афорданси (× стоїть над лінією). */
  readonly onDelete: boolean;
}

/** Стан renderer-а, потрібний для хіт-тесту (кадр уже намальовано: AABB і × — з нього). */
export interface HitScene {
  readonly drawings: readonly Drawing[];
  /** Активна фігура (наведена або виділена) — у неї видно ручки й ×. */
  readonly activeId: string | null;
  readonly deleteBtn: { readonly id: string; readonly x: number; readonly y: number } | null;
  readonly aabbById: ReadonlyMap<string, ScreenAabb>;
  /** Допуск лінії, CSS px (токен `--drawing-hit-tolerance-px`: 10 миша / 16 палець, ADR-0074 T2). */
  readonly tolerancePx: number;
  readonly toX: (t_ms: T_MS) => number | null;
  readonly toY: (price: number) => number | null;
}

/** Хіт у (x, y), CSS px панелі; null — порожньо. */
export function hitTestDrawings(scene: HitScene, x: number, y: number): DrawingHit | null {
  // 1) Активна фігура: ручка або × — що ближче (рівно — ручка: вона нічого не руйнує).
  const active = scene.activeId === null ? undefined : scene.drawings.find((d) => d.id === scene.activeId);
  const handle = active ? nearestHandle(scene, active, x, y) : null;
  const del = scene.deleteBtn;
  const delDist = del ? distToPoint(x, y, del.x, del.y) : Infinity;
  if (del && delDist <= DELETE_BTN_RADIUS_PX && (handle === null || delDist < handle.dist)) {
    return { id: del.id, handleIdx: null, onDelete: true };
  }
  if (active && handle) return { id: active.id, handleIdx: handle.idx, onDelete: false };

  // 2) Інтер'єр прямокутника з × — тіло (інакше × у центрі недосяжний наведенням, audit v2; тягнеться зсередини).
  if (del) {
    const target = scene.drawings.find((d) => d.id === del.id);
    const box = scene.aabbById.get(del.id);
    if (target && box && getToolModule(target.type)?.interiorIsBody && x >= box.minX && x <= box.maxX && y >= box.minY && y <= box.maxY) {
      return { id: del.id, handleIdx: null, onDelete: false };
    }
  }

  // 3) Тіло (остання фігура — зверху); ручка цієї ж фігури під вказівником — ручка.
  const body = topBodyAt(scene, x, y);
  if (!body) return null;
  const own = nearestHandle(scene, body, x, y);
  return { id: body.id, handleIdx: own ? own.idx : null, onDelete: false };
}

/** Найближчий якір фігури в межах допуску + запас реакції; null — жодного. */
function nearestHandle(scene: HitScene, d: Drawing, x: number, y: number): { idx: number; dist: number } | null {
  const reach = scene.tolerancePx + HANDLE_REACT_MARGIN_PX;
  let best: { idx: number; dist: number } | null = null;
  for (let i = 0; i < d.points.length; i++) {
    const hx = scene.toX(d.points[i].t_ms);
    const hy = scene.toY(d.points[i].price);
    if (hx === null || hy === null) continue;
    const dist = distToPoint(x, y, hx, hy);
    if (dist <= reach && (best === null || dist < best.dist)) best = { idx: i, dist };
  }
  return best;
}

/** Фігура, чия лінія/грань під вказівником (z-порядок: остання зверху; найближча з рівних за допуском). */
function topBodyAt(scene: HitScene, x: number, y: number): Drawing | null {
  const tol = scene.tolerancePx;
  let best: Drawing | null = null;
  let minDist = Infinity;
  for (let i = scene.drawings.length - 1; i >= 0; i--) {
    const d = scene.drawings[i];
    const box = scene.aabbById.get(d.id);
    if (box && (x < box.minX - tol || x > box.maxX + tol || y < box.minY - tol || y > box.maxY + tol)) continue;
    const tool = getToolModule(d.type);
    if (!tool) continue;
    const result = tool.hitTest(d, x, y, tol, scene.toX, scene.toY);
    // Рівна відстань — лишається верхня (раніше `<=` віддавала нижній фігурі під нею)
    if (!result.hit || result.distance > tol || result.distance >= minDist) continue;
    minDist = result.distance;
    best = d;
    if (result.distance === 0) break;
  }
  return best;
}
