// src/lib/alerts/alertClient.test.ts — ADR-0114 S7: клієнт /api/alerts (fetch і EventSource — підставні).
import { describe, expect, it, vi } from 'vitest';
import { AlertsApiError, createAlertsClient, type AlertWire } from './alertClient';

type Call = { url: string; init: RequestInit };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function fakeFetch(responses: Response[]) {
  const calls: Call[] = [];
  const impl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = responses.shift();
    if (!next) throw new Error('no more responses');
    return next;
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

const WIRE = { alert_id: 'a1', drawing_id: 'd1', level: 4380, version: 1 } as unknown as AlertWire;

describe('createAlertsClient', () => {
  it('reads health and list; GET carries no CSRF header, cookie goes same-origin', async () => {
    const { impl, calls } = fakeFetch([
      jsonResponse(200, { enabled: true, degraded: ['ticks_unavailable', 7] }),
      jsonResponse(200, { alerts: [WIRE], degraded: [] }),
    ]);
    const client = createAlertsClient(impl);
    expect(await client.health()).toEqual({ enabled: true, channel: 'telegram', degraded: ['ticks_unavailable'] });
    expect((await client.list()).alerts).toEqual([WIRE]);
    expect(calls.map((c) => [c.url, c.init.method, c.init.credentials])).toEqual([
      ['/api/alerts/health', 'GET', 'same-origin'],
      ['/api/alerts', 'GET', 'same-origin'],
    ]);
    expect(calls[0].init.headers).toEqual({});
  });

  it('state changes send X-Alerts and JSON; drawing id is URL-encoded', async () => {
    const { impl, calls } = fakeFetch([jsonResponse(200, { alert: WIRE }), jsonResponse(200, { removed: true })]);
    const client = createAlertsClient(impl);
    const body = { symbol: 'XAU/USD', level: 4380, tf_label: 'M15', active_from_ms: null, rearm: false };
    expect(await client.put('d/1', body)).toEqual(WIRE);
    expect(await client.remove('d1')).toBe(true);
    expect(calls[0].url).toBe('/api/alerts/d%2F1');
    expect(calls[0].init.headers).toEqual({ 'X-Alerts': '1', 'Content-Type': 'application/json' });
    expect(JSON.parse(String(calls[0].init.body))).toEqual(body);
    expect(calls[1].init).toMatchObject({ method: 'DELETE', headers: { 'X-Alerts': '1' } });
  });

  it('server refusals become AlertsApiError with the reason code and details', async () => {
    const { impl } = fakeFetch([jsonResponse(409, { error: 'limit', max_active: 50 })]);
    const err = await createAlertsClient(impl)
      .put('d1', { symbol: 'XAU/USD', level: 1, tf_label: 'M15', active_from_ms: null, rearm: false })
      .catch((e) => e);
    expect(err).toBeInstanceOf(AlertsApiError);
    expect([err.status, err.code, err.details]).toEqual([409, 'limit', { max_active: 50 }]);
  });

  it('site without the alerts route answers HTML → unavailable, not a crash', async () => {
    const html = new Response('<!doctype html><html></html>', { status: 200, headers: { 'Content-Type': 'text/html' } });
    const { impl } = fakeFetch([html]);
    const err = await createAlertsClient(impl).health().catch((e) => e);
    expect(err).toBeInstanceOf(AlertsApiError);
    expect(err.code).toBe('unavailable');
  });

  it('confirm returns wrong digits / awaiting bot as answers, unknown body as error with real status', async () => {
    const { impl } = fakeFetch([
      jsonResponse(400, { status: 'wrong_code', attempts_left: 2 }),
      jsonResponse(409, { status: 'awaiting_bot' }),
      jsonResponse(401, { error: 'not_pairing' }),
    ]);
    const client = createAlertsClient(impl);
    expect(await client.confirm('000000')).toEqual({ status: 'wrong_code', attempts_left: 2 });
    expect((await client.confirm('111111')).status).toBe('awaiting_bot');
    const err = await client.confirm('111111').catch((e) => e);
    expect([err.status, err.code]).toEqual([401, 'not_pairing']);
  });

  it('push channel: health, device state, key, subscribe and forget device', async () => {
    const subscription = { endpoint: 'https://fcm.googleapis.com/fcm/send/x', keys: { p256dh: 'p', auth: 'a' } };
    const { impl, calls } = fakeFetch([
      jsonResponse(200, { enabled: true, channel: 'push', degraded: [] }),
      jsonResponse(200, { alerts: [], degraded: [], device: { device_id: 'w1', label: 'ПК', push_gone: true } }),
      jsonResponse(200, { public_key: 'BKey' }),
      jsonResponse(200, { device: { device_id: 'w1', label: 'ПК' } }),
      jsonResponse(200, { removed_alerts: 3 }),
      jsonResponse(200, { public_key: '' }),
    ]);
    const client = createAlertsClient(impl);
    expect((await client.health()).channel).toBe('push');
    expect((await client.list()).device).toEqual({ device_id: 'w1', label: 'ПК', push_gone: true });
    expect(await client.pushKey()).toBe('BKey');
    expect(await client.pushSubscribe(subscription)).toEqual({ device_id: 'w1', label: 'ПК' });
    expect(await client.forgetDevice()).toBe(3);
    expect((await client.pushKey().catch((e) => e)).code).toBe('bad_push_key');
    expect(calls.slice(2, 5).map((c) => [c.url, c.init.method])).toEqual([
      ['/api/alerts/push/key', 'GET'],
      ['/api/alerts/push/subscribe', 'POST'],
      ['/api/alerts/device', 'DELETE'],
    ]);
    expect(JSON.parse(String(calls[3].init.body))).toEqual(subscription);
    expect(calls[3].init.headers).toEqual({ 'X-Alerts': '1', 'Content-Type': 'application/json' });
    expect(calls[4].init.headers).toEqual({ 'X-Alerts': '1' });
  });

  it('events relay fired/delivered payloads, report open, survive a bad payload and close the source', () => {
    const listeners = new Map<string, (event: MessageEvent) => void>();
    const source = {
      url: '',
      addEventListener: (name: string, fn: (event: MessageEvent) => void) => listeners.set(name, fn),
      close: vi.fn(),
    };
    const factory = vi.fn((url: string) => {
      source.url = url;
      return source as unknown as EventSource;
    });
    const seen: Array<[string, AlertWire]> = [];
    const onOpen = vi.fn();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const close = createAlertsClient(fakeFetch([]).impl, factory).events({
      onEvent: (name, alert) => seen.push([name, alert]),
      onOpen,
    });
    listeners.get('open')!(new MessageEvent('open'));
    listeners.get('fired')!(new MessageEvent('fired', { data: JSON.stringify(WIRE) }));
    listeners.get('delivered')!(new MessageEvent('delivered', { data: '{oops' }));
    listeners.get('undelivered')!(new MessageEvent('undelivered', { data: JSON.stringify(WIRE) }));
    close();
    expect(source.url).toBe('/api/alerts/events');
    expect(seen).toEqual([['fired', WIRE], ['undelivered', WIRE]]);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalledOnce();
    expect(source.close).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});
