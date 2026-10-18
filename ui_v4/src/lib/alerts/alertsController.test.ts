// src/lib/alerts/alertsController.test.ts — ADR-0114 S7: стан ліній-сповіщень сторінки (фейковий сервер у пам'яті).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { Drawing } from '../../types';
import { alertLevelOf } from '../../chart/drawings/tools';
import { drawingsKey } from '../../chart/drawings/drawingsStorage';
import {
  AlertsApiError,
  type AlertEventName,
  type AlertsChannel,
  type AlertsClient,
  type AlertWire,
  type PushSubscriptionWire,
  type PutAlertBody,
} from './alertClient';
import { createAlertsController, localStorageOwned, type OwnedStore } from './alertsController';
import type { PushEnv } from './browserPush';

const NOW = 1_792_086_371_000;
const SYMBOL = 'XAU/USD';

function hline(id: string, price: number): Drawing {
  return { id, type: 'hline', points: [{ t_ms: NOW as Drawing['points'][number]['t_ms'], price }] };
}

/** Сервер у пам'яті з тією самою семантикою PUT, що й app.py (рівень/rearm → нова версія). */
class FakeServer implements AlertsClient {
  alerts = new Map<string, AlertWire>();
  calls: string[] = [];
  enabled = true;
  paired = true;
  failRemove = false;
  handlers: { onEvent: (n: AlertEventName, a: AlertWire) => void; onOpen?: () => void } | null = null;
  gate: Promise<void> | null = null;
  channel: AlertsChannel = 'telegram';
  pushGone = false;
  subscribeError: AlertsApiError | null = null;
  subscriptions: PushSubscriptionWire[] = [];
  forgotten = 0;

  async health() {
    if (!this.enabled) return { enabled: false, channel: this.channel, degraded: [] };
    return { enabled: true, channel: this.channel, degraded: [] };
  }
  async list() {
    if (!this.paired) throw new AlertsApiError(401, 'not_paired');
    const device = this.channel === 'push' ? { device_id: 'w1', label: 'Android · Chrome', push_gone: this.pushGone } : null;
    return { alerts: [...this.alerts.values()], degraded: [], device };
  }
  async pushKey() {
    this.calls.push('GET key');
    return 'BKey';
  }
  async pushSubscribe(subscription: PushSubscriptionWire) {
    this.calls.push('POST subscribe');
    if (this.subscribeError) throw this.subscribeError;
    this.subscriptions.push(subscription);
    this.paired = true;
    this.pushGone = false;
    return { device_id: 'w1', label: 'Android · Chrome' };
  }
  async forgetDevice() {
    this.calls.push('DELETE device');
    this.forgotten += 1;
    this.paired = false;
    const removed = this.alerts.size;
    this.alerts.clear();
    return removed;
  }
  async put(drawingId: string, body: PutAlertBody) {
    this.calls.push(`PUT ${drawingId} ${body.level}${body.rearm ? ' rearm' : ''}`);
    if (this.gate) await this.gate;
    const prev = this.alerts.get(drawingId);
    const changed = !prev || prev.level !== body.level || body.rearm;
    const next: AlertWire = {
      alert_id: prev?.alert_id ?? `a-${drawingId}`,
      drawing_id: drawingId,
      symbol: body.symbol,
      level: body.level,
      tf_label: body.tf_label,
      active_from_ms: NOW,
      created_at_ms: prev?.created_at_ms ?? NOW,
      version: !prev ? 1 : changed ? prev.version + 1 : prev.version,
      state: changed ? 'armed' : prev!.state,
      side: 'below',
      fired: changed ? null : prev!.fired,
      delivery: changed ? null : prev!.delivery,
    };
    this.alerts.set(drawingId, next);
    return next;
  }
  async remove(drawingId: string) {
    this.calls.push(`DELETE ${drawingId}`);
    if (this.failRemove) throw new AlertsApiError(503, 'unavailable');
    return this.alerts.delete(drawingId);
  }
  async pair() {
    return { link: 'https://t.me/v3_bot?start=x', label: 'ПК', expires_in_s: 600 };
  }
  async confirm(code: string) {
    if (code !== '123456') return { status: 'wrong_code' as const, attempts_left: 4 };
    this.paired = true;
    return { status: 'bound' as const, device: { device_id: 'dev1', label: 'ПК' } };
  }
  events(handlers: { onEvent: (n: AlertEventName, a: AlertWire) => void; onOpen?: () => void }) {
    this.handlers = handlers;
    return () => {
      this.handlers = null;
    };
  }
}

function memoryOwned(): OwnedStore & { data: Map<string, string[]> } {
  const data = new Map<string, string[]>();
  return {
    data,
    load: (symbol) => new Set(data.get(symbol) ?? []),
    save: (symbol, ids) => void data.set(symbol, [...ids]),
  };
}

async function ready(server = new FakeServer(), owned = memoryOwned()) {
  const controller = createAlertsController({ client: server, levelOf: alertLevelOf, now: () => NOW, owned });
  await controller.init();
  return { controller, server, owned };
}

describe('alertsController', () => {
  afterEach(() => vi.restoreAllMocks());

  it('status follows the server: disabled, unavailable, unpaired, ready', async () => {
    const disabled = new FakeServer();
    disabled.enabled = false;
    expect(get((await ready(disabled)).controller.view).status).toBe('disabled');

    const broken = new FakeServer();
    broken.health = async () => Promise.reject(new AlertsApiError(200, 'unavailable'));
    vi.spyOn(console, 'info').mockImplementation(() => undefined);
    expect(get((await ready(broken)).controller.view).status).toBe('unavailable');

    const unpaired = new FakeServer();
    unpaired.paired = false;
    expect(get((await ready(unpaired)).controller.view).status).toBe('unpaired');

    const { controller, server } = await ready();
    expect(get(controller.view).status).toBe('ready');
    expect(server.handlers).not.toBeNull();
  });

  it('enable → owned; moving the line re-arms; deleting it removes the alert; foreign alerts stay', async () => {
    const prepared = new FakeServer();
    await prepared.put('phone', body(4400)); // сповіщення з іншого пристрою — є на сервері до відкриття сторінки
    prepared.calls = [];
    const { controller, server, owned } = await ready(prepared);
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    expect(owned.data.get(SYMBOL)).toEqual(['d1']);

    await controller.onPersisted(drawingsKey(SYMBOL), [hline('d1', 4385)]);
    await controller.onPersisted(drawingsKey(SYMBOL), []);
    expect(server.calls).toEqual(['PUT d1 4380', 'PUT d1 4385', 'DELETE d1']);
    expect(server.alerts.has('phone')).toBe(true);
    expect(get(controller.view).alerts.map((a) => a.drawing_id)).toEqual(['phone']);
    expect(owned.data.get(SYMBOL)).toEqual([]);
  });

  it('rapid saves are applied strictly in order (an older level never overtakes a newer one)', async () => {
    const { controller, server } = await ready();
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    let release!: () => void;
    server.gate = new Promise((resolve) => (release = resolve)); // перший PUT повільний
    const first = controller.onPersisted(drawingsKey(SYMBOL), [hline('d1', 4381)]);
    await Promise.resolve();
    server.gate = null; // другий був би швидким — без черги він фінішував би першим, і старий 4381 переписав би 4382
    const second = controller.onPersisted(drawingsKey(SYMBOL), [hline('d1', 4382)]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    release();
    await Promise.all([first, second]);
    expect(server.calls.slice(1)).toEqual(['PUT d1 4381', 'PUT d1 4382']);
    expect(server.alerts.get('d1')!.level).toBe(4382);
  });

  it('snapshot is taken at save time — later mutation of the live array does not leak in', async () => {
    const { controller, server } = await ready();
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    const live = [hline('d1', 4390)];
    const pending = controller.onPersisted(drawingsKey(SYMBOL), live);
    live[0].points[0].price = 9999;
    await pending;
    expect(server.alerts.get('d1')!.level).toBe(4390);
  });

  it('failed delete stays owned and is retried with the next save; the failure is loud', async () => {
    const { controller, server, owned } = await ready();
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    server.failRemove = true;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    await controller.onPersisted(drawingsKey(SYMBOL), []);
    expect(owned.data.get(SYMBOL)).toEqual(['d1']);
    expect(get(controller.view).lastError).toBe('unavailable');
    expect(warn).toHaveBeenCalled();
    server.failRemove = false;
    await controller.onPersisted(drawingsKey(SYMBOL), []);
    expect(server.alerts.has('d1')).toBe(false);
  });

  it('not ready or foreign storage key → nothing is sent', async () => {
    const unpaired = new FakeServer();
    unpaired.paired = false;
    const remembered = memoryOwned();
    remembered.save(SYMBOL, new Set(['d1']));
    const { controller } = await ready(unpaired, remembered);
    await controller.onPersisted(drawingsKey(SYMBOL), []);
    // поки сервіс недоступний, пам'ять «своїх» не стирається — інакше видалена тоді лінія лишила б сирітку
    expect(remembered.data.get(SYMBOL)).toEqual(['d1']);
    const { controller: other, server, owned } = await ready();
    await other.onPersisted('v4_show_hints', []);
    expect(unpaired.calls).toEqual([]);
    expect(server.calls).toEqual([]);
    expect(owned.data.size).toBe(0); // і пам'ять «своїх» не пишеться під вигаданим символом
  });

  it('rearm and disable go to the server; not-alertable shape is refused before any request', async () => {
    const { controller, server } = await ready();
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    await controller.rearm(hline('d1', 4380), SYMBOL, 'M15');
    await controller.disable('d1', SYMBOL);
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const trend: Drawing = { ...hline('t1', 4380), type: 'trend' };
    await expect(controller.enable(trend, SYMBOL, 'M15')).rejects.toMatchObject({ code: 'not_alertable' });
    expect(server.calls).toEqual(['PUT d1 4380', 'PUT d1 4380 rearm', 'DELETE d1']);
  });

  it('events update the view; an event about an older version does not roll it back', async () => {
    const { controller, server } = await ready();
    const armed = await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    const fired: AlertWire = { ...armed, state: 'fired', delivery: 'pending' };
    server.handlers!.onEvent('fired', fired);
    expect(get(controller.view).alerts[0].state).toBe('fired');
    const moved = await controller.enable(hline('d1', 4390), SYMBOL, 'M15'); // version 2
    server.handlers!.onEvent('delivered', { ...fired, delivery: 'delivered' }); // про version 1
    expect(get(controller.view).alerts[0]).toEqual(moved);
  });

  it('fired events reach listeners (toast/sound); delivered do not; unsubscribe works', async () => {
    const { controller, server } = await ready();
    const armed = await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    const heard: string[] = [];
    const stop = controller.onFired((alert) => heard.push(`${alert.drawing_id} ${alert.state}`));
    server.handlers!.onEvent('fired', { ...armed, state: 'fired', delivery: 'pending' });
    server.handlers!.onEvent('delivered', { ...armed, state: 'fired', delivery: 'delivered' });
    stop();
    server.handlers!.onEvent('fired', { ...armed, state: 'fired', delivery: 'pending' });
    expect(heard).toEqual(['d1 fired']);
  });

  it('rearmAlert re-arms from the list by the alert itself (line may live on another device)', async () => {
    const prepared = new FakeServer();
    await prepared.put('phone', body(4400));
    prepared.calls = [];
    const { controller, server } = await ready(prepared);
    const [phone] = get(controller.view).alerts;
    const rearmed = await controller.rearmAlert({ ...phone, state: 'fired' });
    expect(server.calls).toEqual(['PUT phone 4400 rearm']);
    expect(rearmed.version).toBe(2);
    expect(get(controller.view).alerts[0].version).toBe(2);
  });

  it('confirm with the right digits makes the page ready', async () => {
    const server = new FakeServer();
    server.paired = false;
    const { controller } = await ready(server);
    expect((await controller.confirm('000000')).status).toBe('wrong_code');
    expect(get(controller.view).status).toBe('unpaired');
    expect((await controller.confirm('123456')).status).toBe('bound');
    expect(get(controller.view).status).toBe('ready');
  });
});

describe('alertsController — push channel (ADR-0114 Rev 2)', () => {
  afterEach(() => vi.restoreAllMocks());

  function pushEnv(order: string[], permission: NotificationPermission = 'granted') {
    let current: NotificationPermission = 'default';
    const subscription = {
      options: { applicationServerKey: null },
      unsubscribe: vi.fn(async () => true),
      toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/fcm/send/x', keys: { p256dh: 'p', auth: 'a' } }),
    };
    const pushManager = {
      getSubscription: vi.fn(async () => (order.includes('subscribe') ? subscription : null)),
      subscribe: vi.fn(async () => {
        order.push('subscribe');
        return subscription;
      }),
    };
    const env: PushEnv = {
      hasPush: true,
      permission: () => current,
      requestPermission: async () => {
        order.push('permission');
        current = permission;
        return permission;
      },
      registration: async () => ({ pushManager }) as unknown as ServiceWorkerRegistration,
      isIos: false,
      isStandalone: false,
    };
    return { env, subscription, pushManager };
  }

  async function pushReady(server: FakeServer, env: PushEnv) {
    const controller = createAlertsController({
      client: server,
      levelOf: alertLevelOf,
      now: () => NOW,
      owned: memoryOwned(),
      push: env,
    });
    await controller.init();
    return controller;
  }

  function pushServer(paired = false): FakeServer {
    const server = new FakeServer();
    server.channel = 'push';
    server.paired = paired;
    return server;
  }

  it('start: key fetched in advance, channel and support known, unpaired device', async () => {
    const server = pushServer();
    const controller = await pushReady(server, pushEnv([]).env);
    expect(get(controller.view)).toMatchObject({ status: 'unpaired', channel: 'push', pushSupport: 'ok' });
    expect(server.calls).toEqual(['GET key']);
  });

  it('enablePush: permission first, then subscription sent; page becomes ready', async () => {
    const server = pushServer();
    const order: string[] = [];
    const controller = await pushReady(server, pushEnv(order).env);
    server.calls = [];
    const pushSubscribe = server.pushSubscribe.bind(server);
    server.pushSubscribe = async (sub) => {
      order.push('POST');
      return pushSubscribe(sub);
    };
    const seen: boolean[] = [];
    const stop = controller.view.subscribe((v) => seen.push(v.pushEnabling));
    await controller.enablePush();
    stop();
    expect(order).toEqual(['permission', 'subscribe', 'POST']);
    expect(seen.includes(true) && seen[seen.length - 1] === false).toBe(true); // «Вмикаю…» було й зникло
    expect(server.subscriptions[0].endpoint).toBe('https://fcm.googleapis.com/fcm/send/x');
    expect(get(controller.view)).toMatchObject({ status: 'ready', lastError: null });
  });

  it('refusals are loud with a reason: denied, server limit, unknown browser error', async () => {
    const denied = await pushReady(pushServer(), pushEnv([], 'denied').env);
    await expect(denied.enablePush()).rejects.toBeTruthy();
    expect(get(denied.view)).toMatchObject({ status: 'unpaired', lastError: 'push_denied', pushSupport: 'denied', pushEnabling: false });

    const limited = pushServer();
    limited.subscribeError = new AlertsApiError(429, 'devices_per_ip');
    const busy = await pushReady(limited, pushEnv([]).env);
    await expect(busy.enablePush()).rejects.toBeTruthy();
    expect(get(busy.view).lastError).toBe('devices_per_ip');

    const { env, pushManager } = pushEnv([]);
    pushManager.subscribe.mockRejectedValueOnce(new DOMException('Registration failed', 'AbortError'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const broken = await pushReady(pushServer(), env);
    await expect(broken.enablePush()).rejects.toBeTruthy();
    expect(get(broken.view).lastError).toBe('push_failed');
    expect(warn.mock.calls.some((c) => String(c[0]).includes('ALERTS_PUSH_SUBSCRIBE_FAILED'))).toBe(true);
  });

  it('gone subscription is visible to the page and cleared by enabling again', async () => {
    const server = pushServer(true);
    server.pushGone = true;
    const controller = await pushReady(server, pushEnv([]).env);
    expect(get(controller.view).pushGone).toBe(true);
    await controller.enablePush();
    expect(get(controller.view).pushGone).toBe(false);
  });

  it('forget device: server forgets, browser unsubscribes, page is back to «not enabled»', async () => {
    const server = pushServer(true);
    const { env, subscription } = pushEnv(['subscribe']);
    const controller = await pushReady(server, env);
    await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    await controller.forgetDevice();
    expect(server.forgotten).toBe(1);
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
    expect(get(controller.view)).toMatchObject({ status: 'unpaired', alerts: [], pushGone: false });
    expect(server.handlers).toBeNull();
  });

  it('undelivered event updates the alert like other events', async () => {
    const server = pushServer(true);
    const controller = await pushReady(server, pushEnv([]).env);
    const alert = await controller.enable(hline('d1', 4380), SYMBOL, 'M15');
    server.handlers!.onEvent('undelivered', { ...alert, version: 2, state: 'fired', delivery: 'failed' });
    expect(get(controller.view).alerts[0].delivery).toBe('failed');
  });
});

describe('localStorageOwned', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('round-trips per symbol and survives broken storage loudly', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    });
    const owned = localStorageOwned();
    owned.save(SYMBOL, new Set(['d1', 'd2']));
    expect([...owned.load(SYMBOL)]).toEqual(['d1', 'd2']);
    expect(owned.load('NAS100').size).toBe(0);
    store.set(`v4_alert_owned_${SYMBOL}`, '{broken');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(owned.load(SYMBOL).size).toBe(0);
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    });
    owned.save(SYMBOL, new Set(['d1']));
    expect(warn).toHaveBeenCalledTimes(2);
  });
});

function body(level: number): PutAlertBody {
  return { symbol: SYMBOL, level, tf_label: 'M15', active_from_ms: null, rearm: false };
}
