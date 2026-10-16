// src/chart/alerts/alertBells.ts
// ADR-0114 §2 п.4 (рішення власника «а»): дзвіночок на правому кінці лінії-сповіщення біля шкали ціни; спрацювало —
// тьмяніє; не доставлено в Telegram — крапка попередження. Чиста розкладка: вхід — фігури, сповіщення і перетворення
// ціна → y; вихід — що й де намалювати DOM-шаром (на знімок графіка не потрапляє — особисте налаштування).
//
// Позиція — за ціною САМОЇ фігури (не сповіщення): поки лінію тягнуть, дзвіночок іде з нею, а сервер дізнається
// про новий рівень після збереження.

import type { Drawing } from '../../types';
import { alertLevelOf } from '../drawings/tools';
import { roleSpec } from '../drawings/colorRoles';
import type { AlertWire } from '../../lib/alerts/alertClient';

export type BellState = 'armed' | 'fired' | 'undelivered';

/** Піввисота дзвіночка: він 13 CSS-px і центрований на лінії (AlertBells.svelte). */
const BELL_HALF_PX = 7;

export interface Bell {
  drawingId: string;
  /** CSS-px від верху пани, ціле. */
  y: number;
  state: BellState;
  /** CSS-колір лінії: роль (`var(--token, fallback)`), legacy hex або нейтраль теми. */
  color: string;
}

export interface BellsInput {
  drawings: readonly Drawing[];
  alerts: readonly AlertWire[];
  symbol: string;
  degraded: readonly string[];
  priceToY: (price: number) => number | null;
  paneHeight: number;
  /** CSS-px згори, зайняті рядком кнопок (☰, ▶, годинник) у тій самій колонці: там дзвіночок не малюємо — лінію
   *  й так перекривають кнопки, стан видно в ☰ → «Сповіщення»; зсув нижче показав би чужу ціну. */
  topReserved: number;
}

export function layoutBells(input: BellsInput): Bell[] {
  const bySymbol = new Map(input.alerts.filter((a) => a.symbol === input.symbol).map((a) => [a.drawing_id, a]));
  const telegramDown = input.degraded.includes('telegram_unreachable');
  const bells: Bell[] = [];
  for (const drawing of input.drawings) {
    const alert = bySymbol.get(drawing.id);
    const spec = alert === undefined ? null : alertLevelOf(drawing);
    if (alert === undefined || spec === null) continue;
    const y = input.priceToY(spec.level);
    if (y === null || !Number.isFinite(y) || y > input.paneHeight) continue; // лінія поза екраном
    if (y - BELL_HALF_PX < Math.max(0, input.topReserved)) continue; // під рядком кнопок (або вище екрана)
    const state: BellState =
      alert.state === 'armed' ? 'armed' : alert.delivery === 'pending' && telegramDown ? 'undelivered' : 'fired';
    bells.push({ drawingId: drawing.id, y: Math.round(y), state, color: lineColor(drawing) });
  }
  return bells;
}

export function sameBells(a: readonly Bell[], b: readonly Bell[]): boolean {
  return (
    a.length === b.length &&
    a.every((bell, i) => {
      const other = b[i];
      return bell.drawingId === other.drawingId && bell.y === other.y && bell.state === other.state && bell.color === other.color;
    })
  );
}

function lineColor(drawing: Drawing): string {
  const role = drawing.meta?.colorRole;
  const spec = role ? roleSpec(role) : undefined;
  if (spec) return `var(${spec.cssVar}, ${spec.fallback})`;
  if (drawing.meta?.color) return drawing.meta.color;
  const neutral = roleSpec('neutral')!;
  return `var(${neutral.cssVar}, ${neutral.fallback})`;
}
