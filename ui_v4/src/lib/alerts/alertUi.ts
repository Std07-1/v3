// src/lib/alerts/alertUi.ts
// ADR-0114 S8: що показати людині — дія для лінії, підписи стану, текст спливного повідомлення. Чисті функції.
//
// Стан сповіщення бере з сервера (X28): тут лише назви для того, що повернув `/api/alerts`.

import type { Drawing } from '../../types';
import { alertLevelOf } from '../../chart/drawings/tools';
import { displayPriceDigits } from '../priceDigits';
import type { AlertWire } from './alertClient';
import type { AlertsView } from './alertsController';
import type { PushSupport } from './browserPush';
import { msg, t } from '../i18n/i18n.svelte';

/** `resubscribe` — пуш: браузер скасував підписку, клік вмикає сповіщення знову (і більше нічого). */
export type AlertActionKind = 'enable' | 'disable' | 'rearm' | 'pair' | 'resubscribe';

export interface AlertAction {
  kind: AlertActionKind;
  label: string;
  /** Сповіщення увімкнене зараз (дзвіночок «горить»). */
  on: boolean;
  /** Дія недоступна з цього місця — лише підказка (`pair`: прив'язка живе в ☰ → Сповіщення; пуш неможливий). */
  hintOnly: boolean;
}

// Тексти — ключі мовного шару (ADR-0116): msg() позначає їх для тесту паритету, t() перекладає в мить показу
const PUSH_HINT: Record<Exclude<PushSupport, 'ok'>, string> = {
  denied: msg('Сповіщати — дозвольте сповіщення сайту в налаштуваннях браузера'),
  ios_home_screen: msg('Сповіщати — на iPhone додайте сайт «На початковий екран» і відкрийте звідти'),
  unsupported: msg('Сповіщати — цей браузер не вміє сповіщень'),
};

/** Дія для фігури: null — фігура не може сповіщати або сервіс вимкнений / недоступний (рядка нема зовсім). */
export function alertActionFor(drawing: Drawing | null, view: AlertsView, symbol: string | null): AlertAction | null {
  if (drawing === null || symbol === null || alertLevelOf(drawing) === null) return null;
  if (view.status === 'unpaired' && view.channel === 'push') {
    // Пуш: той самий клік просить дозвіл, підписує і вмикає лінію (chartAlerts.act)
    const support = view.pushSupport ?? 'unsupported';
    if (support !== 'ok') return { kind: 'enable', label: t(PUSH_HINT[support]), on: false, hintOnly: true };
    return { kind: 'enable', label: t('Сповіщати'), on: false, hintOnly: false };
  }
  if (view.status === 'unpaired') {
    return { kind: 'pair', label: t("Сповіщати — під'єднайте Telegram: ☰ → Сповіщення"), on: false, hintOnly: true };
  }
  if (view.status !== 'ready') return null;
  if (view.pushGone) {
    return { kind: 'resubscribe', label: t('Сповіщення вимкнено в браузері — увімкнути знову'), on: false, hintOnly: false };
  }
  const alert = alertOfDrawing(view.alerts, symbol, drawing.id);
  if (alert === null) return { kind: 'enable', label: t('Сповіщати'), on: false, hintOnly: false };
  if (alert.state === 'armed') return { kind: 'disable', label: t('Сповіщає — вимкнути'), on: true, hintOnly: false };
  return { kind: 'rearm', label: t('Спрацювало — увімкнути знову'), on: false, hintOnly: false };
}

export function alertOfDrawing(alerts: readonly AlertWire[], symbol: string, drawingId: string): AlertWire | null {
  return alerts.find((a) => a.symbol === symbol && a.drawing_id === drawingId) ?? null;
}

/** «XAU/USD 4380.00» — знаками символу з конфігу (як шкала), без них — за величиною. */
export function alertTitle(alert: AlertWire, configuredDigits: number | null): string {
  return `${alert.symbol} ${alert.level.toFixed(displayPriceDigits(configuredDigits, alert.level))}`;
}

const DIRECTION = { up: msg('знизу вгору'), down: msg('згори вниз') } as const;

/** Підрядок стану для ☰-списку. `pushGone` — браузер скасував підписку цього пристрою. */
export function alertStateText(alert: AlertWire, degraded: readonly string[], pushGone = false): string {
  if (alert.state === 'armed') return t('чекає перетину');
  const direction = alert.fired ? t(DIRECTION[alert.fired.direction]) : '';
  if (alert.delivery === 'failed') return t('перетнуто {direction} · не доставлено', { direction });
  if (alert.delivery === 'pending' && pushGone) {
    return t('перетнуто {direction} · чекає, доки ввімкнуть сповіщення', { direction });
  }
  if (alert.delivery === 'pending' && degraded.includes('telegram_unreachable')) {
    return t('перетнуто {direction} · Telegram не доставив', { direction });
  }
  if (alert.delivery === 'pending' && degraded.includes('push_unreachable')) {
    return t('перетнуто {direction} · ще не доставлено', { direction });
  }
  return t('перетнуто {direction}', { direction });
}

const ERROR_TEXT: Record<string, string> = {
  no_price: msg('немає ціни, щоб визначити бік — ринок закритий?'),
  price_unavailable: msg('сервер не прочитав ціну — спробуйте ще раз'),
  limit: msg('досягнуто ліміту активних сповіщень'),
  not_paired: msg('увімкніть сповіщення: ☰ → Сповіщення'),
  unavailable: msg('сервіс сповіщень недоступний'),
  rate_limited: msg('забагато запитів — зачекайте хвилину'),
  symbol_mismatch: msg('ця лінія вже сповіщає на іншому символі'),
  bot_unavailable: msg('бот сигналів зараз недоступний'),
  not_alertable: msg('ця фігура не може сповіщати'),
  // Rev 2, пуш у браузер
  push_denied: msg('браузер не дав дозволу — дозвольте сповіщення сайту в налаштуваннях'),
  push_unsupported: msg('цей браузер не вміє сповіщень'),
  push_ios_home_screen: msg('на iPhone сповіщення працюють, коли сайт додано «На початковий екран»'),
  no_service_worker: msg('сторінка ще не готова до сповіщень — оновіть її'),
  push_failed: msg('браузер не зміг підписатися — спробуйте ще раз'),
  push_unavailable: msg('сервіс не дав ключа сповіщень — оновіть сторінку'),
  bad_subscription: msg('браузер дав непридатну підписку'),
  devices_per_ip: msg('забагато нових пристроїв з вашої мережі — спробуйте за годину'),
  devices_full: msg('сервіс сповіщень переповнений — спробуйте пізніше'),
};

/** Причина відмови людською мовою (код сервера ADR-0114 §4.5–4.6); невідомий код — як є. */
export function alertErrorText(code: string): string {
  return t('Сповіщення: {reason}', { reason: Object.hasOwn(ERROR_TEXT, code) ? t(ERROR_TEXT[code]) : code });
}

/** Текст спливного повідомлення на відкритій сторінці. */
export function firedToastText(alert: AlertWire, configuredDigits: number | null): string {
  const title = alertTitle(alert, configuredDigits);
  if (!alert.fired) return title;
  return t('{title} — перетнуто {direction}', { title, direction: t(DIRECTION[alert.fired.direction]) });
}
