// src/lib/alerts/alertsStore.ts
// ADR-0114 S8: один контролер ліній-сповіщень на сторінку — його читають графік (дзвіночки, меню стилю), ☰-меню
// (прив'язка, список) і спливне повідомлення. Старт — один раз, з ChartPane.

import { alertLevelOf } from '../../chart/drawings/tools';
import { createAlertsClient } from './alertClient';
import { createAlertsController } from './alertsController';
import { browserPushEnv } from './browserPush';

export const alerts = createAlertsController({
  client: createAlertsClient(),
  levelOf: alertLevelOf,
  now: () => Date.now(),
  push: browserPushEnv(),
});

let started = false;

export function startAlerts(): void {
  if (started) return;
  started = true;
  void alerts.init();
}
