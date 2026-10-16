// src/lib/alerts/alertUi.ts
// ADR-0114 S8: що показати людині — дія для лінії, підписи стану, текст спливного повідомлення. Чисті функції.
//
// Стан сповіщення бере з сервера (X28): тут лише назви для того, що повернув `/api/alerts`.

import type { Drawing } from '../../types';
import { alertLevelOf } from '../../chart/drawings/tools';
import { displayPriceDigits } from '../priceDigits';
import type { AlertWire } from './alertClient';
import type { AlertsView } from './alertsController';

export type AlertActionKind = 'enable' | 'disable' | 'rearm' | 'pair';

export interface AlertAction {
  kind: AlertActionKind;
  label: string;
  /** Сповіщення увімкнене зараз (дзвіночок «горить»). */
  on: boolean;
  /** Дія недоступна з цього місця — лише підказка (`pair`: прив'язка живе в ☰ → Сповіщення). */
  hintOnly: boolean;
}

/** Дія для фігури: null — фігура не може сповіщати або сервіс вимкнений / недоступний (рядка нема зовсім). */
export function alertActionFor(drawing: Drawing | null, view: AlertsView, symbol: string | null): AlertAction | null {
  if (drawing === null || symbol === null || alertLevelOf(drawing) === null) return null;
  if (view.status === 'unpaired') {
    return { kind: 'pair', label: "Сповіщати — під'єднайте Telegram: ☰ → Сповіщення", on: false, hintOnly: true };
  }
  if (view.status !== 'ready') return null;
  const alert = alertOfDrawing(view.alerts, symbol, drawing.id);
  if (alert === null) return { kind: 'enable', label: 'Сповіщати', on: false, hintOnly: false };
  if (alert.state === 'armed') return { kind: 'disable', label: 'Сповіщає — вимкнути', on: true, hintOnly: false };
  return { kind: 'rearm', label: 'Спрацювало — увімкнути знову', on: false, hintOnly: false };
}

export function alertOfDrawing(alerts: readonly AlertWire[], symbol: string, drawingId: string): AlertWire | null {
  return alerts.find((a) => a.symbol === symbol && a.drawing_id === drawingId) ?? null;
}

/** «XAU/USD 4380.00» — знаками символу з конфігу (як шкала), без них — за величиною. */
export function alertTitle(alert: AlertWire, configuredDigits: number | null): string {
  return `${alert.symbol} ${alert.level.toFixed(displayPriceDigits(configuredDigits, alert.level))}`;
}

const DIRECTION = { up: 'знизу вгору', down: 'згори вниз' } as const;

/** Підрядок стану для ☰-списку. */
export function alertStateText(alert: AlertWire, degraded: readonly string[]): string {
  if (alert.state === 'armed') return 'чекає перетину';
  const direction = alert.fired ? DIRECTION[alert.fired.direction] : '';
  if (alert.delivery === 'pending' && degraded.includes('telegram_unreachable')) {
    return `перетнуто ${direction} · Telegram не доставив`;
  }
  return `перетнуто ${direction}`;
}

const ERROR_TEXT: Record<string, string> = {
  no_price: 'немає ціни, щоб визначити бік — ринок закритий?',
  price_unavailable: 'сервер не прочитав ціну — спробуйте ще раз',
  limit: 'досягнуто ліміту активних сповіщень',
  not_paired: "під'єднайте Telegram: ☰ → Сповіщення",
  unavailable: 'сервіс сповіщень недоступний',
  rate_limited: 'забагато запитів — зачекайте хвилину',
  symbol_mismatch: 'ця лінія вже сповіщає на іншому символі',
  bot_unavailable: 'бот сигналів зараз недоступний',
  not_alertable: 'ця фігура не може сповіщати',
};

/** Причина відмови людською мовою (код сервера ADR-0114 §4.5–4.6); невідомий код — як є. */
export function alertErrorText(code: string): string {
  return `Сповіщення: ${ERROR_TEXT[code] ?? code}`;
}

/** Текст спливного повідомлення на відкритій сторінці. */
export function firedToastText(alert: AlertWire, configuredDigits: number | null): string {
  const direction = alert.fired ? ` — перетнуто ${DIRECTION[alert.fired.direction]}` : '';
  return `${alertTitle(alert, configuredDigits)}${direction}`;
}
