// src/lib/alerts/alertsController.ts
// ADR-0114 S7: стан ліній-сповіщень для сторінки — без вигляду (вигляд — S8, за «так» власника).
//
// Склеює клієнт (HTTP/SSE), план узгодження (alertSync) і пам'ять «своїх» фігур. Дії (узгодження, увімкнути,
// вимкнути, знову взвести) виконуються строго по черзі: PUT зі старим рівнем не обжене PUT з новим, коли лінію
// тягнуть кілька разів поспіль. Відмови не тихі (I5): код причини в `view.lastError` + `console.warn`.
// Сервер недоступний / вимкнений — `status` каже це, і контролер нічого не шле.
// Rev 2 (канал «пуш»): ключ VAPID береться на старті (у кліку дозвіл має йти першим — browserPush.ts);
// `enablePush` — дозвіл → підписка браузера → пристрій на сервері; `pushGone` — браузер скасував підписку.

import { get, writable, type Readable } from 'svelte/store';
import type { Drawing } from '../../types';
import type { AlertLevelSpec } from '../../chart/drawings/tools/types';
import { symbolOfDrawingsKey } from '../../chart/drawings/drawingsStorage';
import {
  AlertsApiError,
  type AlertsChannel,
  type AlertsClient,
  type AlertWire,
  type ConfirmResult,
} from './alertClient';
import { planAlertSync } from './alertSync';
import { PushError, pushSupport, subscribeBrowser, unsubscribeBrowser, type PushEnv, type PushSupport } from './browserPush';

export type AlertsStatus = 'unknown' | 'unavailable' | 'disabled' | 'unpaired' | 'ready';

export interface AlertsView {
  status: AlertsStatus;
  /** Канал сервера (null — ще не відомо): пуш у браузер або Telegram. */
  channel: AlertsChannel | null;
  /** Пуш: чи може ця сторінка отримувати сповіщення (iPhone поза початковим екраном, заборона, нема API). */
  pushSupport: PushSupport | null;
  /** Пуш: браузер скасував підписку цього пристрою — сповіщення чекають, доки її не ввімкнуть знову. */
  pushGone: boolean;
  /** Пуш: триває дозвіл і підписка (перша реєстрація браузера у FCM буває довгою — сторінка показує, що працює). */
  pushEnabling: boolean;
  alerts: AlertWire[];
  degraded: string[];
  /** Код останньої відмови дії (`no_price`, `limit`, …) — сторінка показує причину. */
  lastError: string | null;
}

/** «Свої» drawing_id на символ: ставив сповіщення цей браузер (щоб видалена лінія зняла своє сповіщення). */
export interface OwnedStore {
  load(symbol: string): Set<string>;
  save(symbol: string, ids: ReadonlySet<string>): void;
}

export interface AlertsControllerDeps {
  client: AlertsClient;
  levelOf: (d: Drawing) => AlertLevelSpec | null;
  now: () => number;
  owned?: OwnedStore;
  /** Браузерні API пушу (тести підміняють); без них канал «пуш» — «не підтримується». */
  push?: PushEnv;
}

export interface AlertsController {
  readonly view: Readable<AlertsView>;
  init(): Promise<void>;
  onPersisted(storageKey: string, drawings: readonly Drawing[]): Promise<void>;
  enable(drawing: Drawing, symbol: string, tfLabel: string): Promise<AlertWire>;
  rearm(drawing: Drawing, symbol: string, tfLabel: string): Promise<AlertWire>;
  /** Знову взвести на рівні самого сповіщення (☰-список: лінії може не бути на цьому пристрої). */
  rearmAlert(alert: AlertWire): Promise<AlertWire>;
  disable(drawingId: string, symbol: string): Promise<void>;
  /** Спрацювання з потоку подій (спливне повідомлення, звук); повертає відписку. */
  onFired(listener: (alert: AlertWire) => void): () => void;
  pair(): Promise<{ link: string; label: string; expires_in_s: number }>;
  confirm(code: string): Promise<ConfirmResult>;
  /** Пуш: дозвіл → підписка → пристрій. Викликати прямо з кліку (дозвіл просить першим). */
  enablePush(): Promise<void>;
  /** Пуш: «Вимкнути на цьому пристрої» — сповіщення пристрою, пристрій і підписка браузера — геть. */
  forgetDevice(): Promise<void>;
  dispose(): void;
}

export function createAlertsController(deps: AlertsControllerDeps): AlertsController {
  const owned = deps.owned ?? localStorageOwned();
  const view = writable<AlertsView>({
    status: 'unknown',
    channel: null,
    pushSupport: null,
    pushGone: false,
    pushEnabling: false,
    alerts: [],
    degraded: [],
    lastError: null,
  });
  let pushKey: string | null = null; // ключ VAPID сервера — заздалегідь, щоб у кліку дозвіл ішов першим
  let queue: Promise<unknown> = Promise.resolve();
  let closeEvents: (() => void) | null = null;
  const firedListeners = new Set<(alert: AlertWire) => void>();

  function enqueue<T>(job: () => Promise<T>): Promise<T> {
    const run = queue.then(job, job);
    queue = run.catch(() => undefined);
    return run;
  }

  function upsert(alert: AlertWire): void {
    view.update((v) => {
      const current = v.alerts.find((a) => a.alert_id === alert.alert_id);
      if (current !== undefined && current.version > alert.version) return v; // подія про стару версію
      return { ...v, alerts: [...v.alerts.filter((a) => a.alert_id !== alert.alert_id), alert] };
    });
  }

  function dropDrawing(drawingId: string, symbol: string): void {
    view.update((v) => ({
      ...v,
      alerts: v.alerts.filter((a) => !(a.drawing_id === drawingId && a.symbol === symbol)),
    }));
  }

  function fail(action: string, err: unknown): string {
    const code = err instanceof AlertsApiError || err instanceof PushError ? err.code : String(err);
    console.warn('[alerts] ALERTS_ACTION_FAILED %s %s', action, code);
    view.update((v) => ({ ...v, lastError: code }));
    return code;
  }

  async function refresh(): Promise<void> {
    const { alerts, degraded, device } = await deps.client.list();
    view.update((v) => ({ ...v, status: 'ready', alerts, degraded, pushGone: device?.push_gone === true }));
  }

  async function becomeReady(): Promise<void> {
    await refresh();
    closeEvents?.();
    closeEvents = deps.client.events({
      onEvent: (name, alert) => {
        upsert(alert);
        if (name === 'fired') firedListeners.forEach((listener) => listener(alert));
      },
      onOpen: () => void enqueue(refresh).catch((err) => fail('refresh', err)), // пропущене за обрив
    });
  }

  function putBody(drawing: Drawing, symbol: string, tfLabel: string, rearm: boolean) {
    const spec = deps.levelOf(drawing);
    if (spec === null) throw new AlertsApiError(0, 'not_alertable');
    return { symbol, level: spec.level, tf_label: tfLabel, active_from_ms: spec.anchorMs, rearm };
  }

  async function arm(drawing: Drawing, symbol: string, tfLabel: string, rearm: boolean): Promise<AlertWire> {
    try {
      const alert = await deps.client.put(drawing.id, putBody(drawing, symbol, tfLabel, rearm));
      upsert(alert);
      const ids = owned.load(symbol);
      ids.add(drawing.id);
      owned.save(symbol, ids);
      view.update((v) => ({ ...v, lastError: null }));
      return alert;
    } catch (err) {
      fail(rearm ? 'rearm' : 'enable', err);
      throw err;
    }
  }

  async function sync(symbol: string, drawings: readonly Drawing[]): Promise<void> {
    const plan = planAlertSync({
      symbol,
      drawings,
      alerts: get(view).alerts,
      owned: owned.load(symbol),
      levelOf: deps.levelOf,
      nowMs: deps.now(),
    });
    const keep = plan.owned;
    for (const { drawingId, body } of plan.puts) {
      try {
        upsert(await deps.client.put(drawingId, body));
      } catch (err) {
        fail('sync_put', err);
      }
    }
    for (const drawingId of plan.deletes) {
      try {
        await deps.client.remove(drawingId);
        dropDrawing(drawingId, symbol);
      } catch (err) {
        fail('sync_delete', err);
        keep.add(drawingId); // не вдалося — спробуємо з наступним збереженням
      }
    }
    owned.save(symbol, keep);
  }

  return {
    view: { subscribe: view.subscribe },

    async init() {
      try {
        const health = await deps.client.health();
        if (!health.enabled) {
          view.update((v) => ({ ...v, status: 'disabled' }));
          return;
        }
        view.update((v) => ({ ...v, channel: health.channel }));
        if (health.channel === 'push') {
          view.update((v) => ({ ...v, pushSupport: deps.push ? pushSupport(deps.push) : 'unsupported' }));
          pushKey = await deps.client.pushKey();
        }
        await becomeReady();
      } catch (err) {
        if (err instanceof AlertsApiError && err.code === 'not_paired') {
          view.update((v) => ({ ...v, status: 'unpaired' }));
          return;
        }
        console.info('[alerts] ALERTS_UNAVAILABLE %s', err instanceof AlertsApiError ? err.code : String(err));
        view.update((v) => ({ ...v, status: 'unavailable' }));
      }
    },

    onPersisted(storageKey, drawings) {
      const symbol = symbolOfDrawingsKey(storageKey);
      if (symbol === null || get(view).status !== 'ready') return Promise.resolve();
      const snapshot = drawings.map((d) => ({ ...d, points: d.points.map((p) => ({ ...p })) })); // масив живий
      return enqueue(() => sync(symbol, snapshot));
    },

    enable: (drawing, symbol, tfLabel) => enqueue(() => arm(drawing, symbol, tfLabel, false)),
    rearm: (drawing, symbol, tfLabel) => enqueue(() => arm(drawing, symbol, tfLabel, true)),

    rearmAlert(alert) {
      return enqueue(async () => {
        const body = {
          symbol: alert.symbol,
          level: alert.level,
          tf_label: alert.tf_label,
          active_from_ms: null,
          rearm: true,
        };
        try {
          const rearmed = await deps.client.put(alert.drawing_id, body);
          upsert(rearmed);
          view.update((v) => ({ ...v, lastError: null }));
          return rearmed;
        } catch (err) {
          fail('rearm', err);
          throw err;
        }
      });
    },

    disable(drawingId, symbol) {
      return enqueue(async () => {
        try {
          await deps.client.remove(drawingId);
        } catch (err) {
          fail('disable', err);
          throw err;
        }
        dropDrawing(drawingId, symbol);
        const ids = owned.load(symbol);
        ids.delete(drawingId);
        owned.save(symbol, ids);
      });
    },

    onFired(listener) {
      firedListeners.add(listener);
      return () => firedListeners.delete(listener);
    },

    pair: () => deps.client.pair(),

    async confirm(code) {
      const result = await deps.client.confirm(code);
      if (result.status === 'bound') await enqueue(becomeReady);
      return result;
    },

    async enablePush() {
      const env = deps.push;
      view.update((v) => ({ ...v, pushEnabling: true })); // синхронно — до першого await (жест кліку живий)
      try {
        if (!env || pushKey === null) throw new PushError(env ? 'push_unavailable' : 'push_unsupported');
        const subscription = await subscribeBrowser(env, pushKey); // дозвіл — першим await цього кліку
        await deps.client.pushSubscribe(subscription);
      } catch (err) {
        const known = err instanceof PushError || err instanceof AlertsApiError;
        if (!known) console.warn('[alerts] ALERTS_PUSH_SUBSCRIBE_FAILED %s', String(err)); // DOMException браузера
        fail('push', known ? err : new PushError('push_failed'));
        if (env) view.update((v) => ({ ...v, pushSupport: pushSupport(env) })); // напр. щойно заборонили
        throw err;
      } finally {
        view.update((v) => ({ ...v, pushEnabling: false }));
      }
      view.update((v) => ({ ...v, lastError: null }));
      await enqueue(becomeReady);
    },

    forgetDevice() {
      return enqueue(async () => {
        try {
          await deps.client.forgetDevice();
          if (deps.push) await unsubscribeBrowser(deps.push);
        } catch (err) {
          fail('forget_device', err);
          throw err;
        }
        closeEvents?.();
        closeEvents = null;
        view.update((v) => ({ ...v, status: 'unpaired', alerts: [], pushGone: false, lastError: null }));
      });
    },

    dispose() {
      closeEvents?.();
      closeEvents = null;
    },
  };
}

const OWNED_KEY_PREFIX = 'v4_alert_owned_';

/** Пам'ять «своїх» у localStorage (на символ). Збій сховища — попередження, а не виняток: у найгіршому разі
 *  сповіщення видаленої лінії лишиться і буде видне в ☰-списку й `/alerts`, де його можна зняти. */
export function localStorageOwned(): OwnedStore {
  return {
    load(symbol) {
      try {
        const raw = localStorage.getItem(`${OWNED_KEY_PREFIX}${symbol}`);
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []);
      } catch (err) {
        console.warn('[alerts] ALERTS_OWNED_READ_FAILED %s %s', symbol, String(err));
        return new Set();
      }
    },
    save(symbol, ids) {
      try {
        localStorage.setItem(`${OWNED_KEY_PREFIX}${symbol}`, JSON.stringify([...ids]));
      } catch (err) {
        console.warn('[alerts] ALERTS_OWNED_SAVE_FAILED %s %s', symbol, String(err));
      }
    },
  };
}
