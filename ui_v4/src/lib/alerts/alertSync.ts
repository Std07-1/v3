// src/lib/alerts/alertSync.ts
// ADR-0114 §4.6: узгодження фігур цього браузера зі сповіщеннями на сервері — чиста функція, без мережі.
//
// Сервер — джерело «сповіщення є і в якому стані»; фігура — джерело ціни лінії. Після кожного збереження фігур
// (`DrawingsRenderer.onPersisted`) план каже, що відправити:
//   - лінію пересунули (ціна чи якір променя) → PUT з новим рівнем (сервер взведе знову);
//   - лінію видалили → DELETE її сповіщення;
// і лише для «своїх» фігур: тих, що є в цьому браузері, або чиї сповіщення цей браузер ставив (`owned` — пам'ятає
// видалені). Сповіщення фігур з іншого пристрою (фігур між пристроями ще не видно, ADR-B) не чіпаються — вони видні
// у ☰-списку і `/alerts` бота. Втрачений `owned` (почищене сховище) самовідновлюється: сповіщення, чия фігура є
// тут, — своє за UUID фігури.
//
// Лінію, чиє сповіщення зникло на сервері (вимкнули з іншого місця), план НЕ відновлює: прихованого «оживлення»
// нема — увімкнути знову може лише людина.

import type { Drawing } from '../../types';
import type { AlertLevelSpec } from '../../chart/drawings/tools/types';
import type { AlertWire, PutAlertBody } from './alertClient';

export interface AlertSyncInput {
  symbol: string;
  drawings: readonly Drawing[];
  /** Сповіщення підписника з сервера (будь-яких символів — план бере свій). */
  alerts: readonly AlertWire[];
  /** drawing_id, чиї сповіщення ставив цей браузер (символ `symbol`). */
  owned: ReadonlySet<string>;
  levelOf: (d: Drawing) => AlertLevelSpec | null;
  nowMs: number;
}

export interface AlertSyncPlan {
  puts: Array<{ drawingId: string; body: PutAlertBody }>;
  deletes: string[];
  /** Новий `owned` після плану (успіх кожної дії оновлює стан окремо — план не знає мережі). */
  owned: Set<string>;
}

export function planAlertSync(input: AlertSyncInput): AlertSyncPlan {
  const local = new Map(input.drawings.map((d) => [d.id, d]));
  const server = new Map(input.alerts.filter((a) => a.symbol === input.symbol).map((a) => [a.drawing_id, a]));
  const managed = new Set([...input.owned, ...[...server.keys()].filter((id) => local.has(id))]);
  const plan: AlertSyncPlan = { puts: [], deletes: [], owned: new Set() };

  for (const drawingId of managed) {
    const drawing = local.get(drawingId);
    const alert = server.get(drawingId);
    if (alert === undefined) continue; // сповіщення вже нема (вимкнули деінде) — не відновлюємо, забуваємо
    const spec = drawing === undefined ? null : input.levelOf(drawing);
    if (spec === null) {
      plan.deletes.push(drawingId); // фігуру видалено (або тип більше не може сповіщати)
      continue;
    }
    plan.owned.add(drawingId);
    if (needsPut(alert, spec, input.nowMs)) {
      plan.puts.push({
        drawingId,
        body: {
          symbol: input.symbol,
          level: spec.level,
          tf_label: alert.tf_label,
          active_from_ms: spec.anchorMs,
          rearm: false,
        },
      });
    }
  }
  return plan;
}

/** Та сама умова, що в сервера (`app.py::_needs_arming`): нова ціна або якір променя, перенесений у майбутнє. */
function needsPut(alert: AlertWire, spec: AlertLevelSpec, nowMs: number): boolean {
  if (spec.level !== alert.level) return true;
  return spec.anchorMs !== null && spec.anchorMs > nowMs && spec.anchorMs !== alert.active_from_ms;
}
