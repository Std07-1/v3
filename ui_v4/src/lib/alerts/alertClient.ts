// src/lib/alerts/alertClient.ts
// ADR-0114 S7: клієнт процесу smc-alerts — `/api/alerts/*` того самого сайту (nginx → 127.0.0.1:8020).
//
// Тонкий шар HTTP: жодних рішень про перетин (X28 — стан приходить із сервера). Cookie пристрою HttpOnly — браузер
// шле її сам (`credentials: 'same-origin'`), JS її не бачить. Зміни стану — із заголовком `X-Alerts: 1` (CSRF,
// ADR-0114 §4.5). Відмови сервера — `AlertsApiError` з кодом причини (`not_paired`, `no_price`, `limit`, …), а не
// «щось пішло не так». Відповідь не-JSON (сайт без nginx-маршруту віддає index.html) — `unavailable`.
// Rev 2: канал (`health.channel`) — пуш у браузер (ключ, підписка, «вимкнути на цьому пристрої») або Telegram (S7).

export type AlertState = 'armed' | 'fired';
/** `failed` — не доставлено й не буде (відмова push-сервісу, застаріло, пристрій вимкнено). */
export type AlertDelivery = 'pending' | 'delivered' | 'failed' | null;
export type AlertsChannel = 'push' | 'telegram';

/** Сповіщення так, як його віддає сервер (`runtime/alerts/app.py::_wire`). */
export interface AlertWire {
  alert_id: string;
  drawing_id: string;
  symbol: string;
  level: number;
  tf_label: string;
  active_from_ms: number;
  created_at_ms: number;
  version: number;
  state: AlertState;
  side: 'below' | 'above' | null;
  fired: {
    at_ms: number;
    price: number;
    via: 'tick' | 'm1';
    direction: 'up' | 'down';
    after_gap: boolean;
  } | null;
  delivery: AlertDelivery;
}

export interface AlertsHealth {
  enabled: boolean;
  channel: AlertsChannel;
  degraded: string[];
}

/** Пристрій цієї сторінки (пуш): `push_gone` — браузер скасував підписку, треба ввімкнути знову. */
export interface AlertsDevice {
  device_id: string;
  label: string;
  push_gone: boolean;
}

/** Підписка браузера як її віддає `PushSubscription.toJSON()`. */
export interface PushSubscriptionWire {
  endpoint: string;
  expirationTime?: number | null;
  keys: { p256dh: string; auth: string };
}

export interface PutAlertBody {
  symbol: string;
  level: number;
  tf_label: string;
  active_from_ms: number | null;
  rearm: boolean;
}

export type ConfirmStatus = 'bound' | 'wrong_code' | 'locked' | 'awaiting_bot' | 'no_pairing';

export interface ConfirmResult {
  status: ConfirmStatus;
  attempts_left?: number;
  device?: { device_id: string; label: string };
}

export type AlertEventName = 'fired' | 'delivered' | 'undelivered';

export class AlertsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(`alerts ${status} ${code}`);
    this.name = 'AlertsApiError';
  }
}

export interface AlertsClient {
  health(): Promise<AlertsHealth>;
  list(): Promise<{ alerts: AlertWire[]; degraded: string[]; device: AlertsDevice | null }>;
  put(drawingId: string, body: PutAlertBody): Promise<AlertWire>;
  remove(drawingId: string): Promise<boolean>;
  pair(): Promise<{ link: string; label: string; expires_in_s: number }>;
  confirm(code: string): Promise<ConfirmResult>;
  /** Публічний ключ VAPID для `pushManager.subscribe` (канал «пуш»). */
  pushKey(): Promise<string>;
  /** Підписка браузера → пристрій цієї сторінки (cookie ставить сервер; своя сторінка — оновлення). */
  pushSubscribe(subscription: PushSubscriptionWire): Promise<{ device_id: string; label: string }>;
  /** «Вимкнути на цьому пристрої»: сповіщення пристрою, пристрій і cookie — геть; → скільки сповіщень прибрано. */
  forgetDevice(): Promise<number>;
  /** Потік подій моїх сповіщень; повертає функцію закриття. `onOpen` — після (пере)підключення: перечитати список. */
  events(handlers: {
    onEvent: (name: AlertEventName, alert: AlertWire) => void;
    onOpen?: () => void;
    onError?: () => void;
  }): () => void;
}

const BASE = '/api/alerts';
const CSRF = { 'X-Alerts': '1' } as const;
const CONFIRM_STATUSES: readonly ConfirmStatus[] = ['bound', 'wrong_code', 'locked', 'awaiting_bot', 'no_pairing'];

export function createAlertsClient(
  fetchImpl: typeof fetch = (...args) => fetch(...args),
  eventSourceFactory: (url: string) => EventSource = (url) => new EventSource(url, { withCredentials: true }),
): AlertsClient {
  async function call(method: string, path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = method === 'GET' ? {} : { ...CSRF };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    return fetchImpl(`${BASE}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  async function json(response: Response): Promise<Record<string, unknown>> {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new AlertsApiError(response.status, 'unavailable'); // HTML (SPA fallback) чи порожньо — сервісу нема
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      throw new AlertsApiError(response.status, 'unavailable');
    }
    return payload as Record<string, unknown>;
  }

  async function ok(response: Response): Promise<Record<string, unknown>> {
    const payload = await json(response);
    if (!response.ok) {
      const { error, ...details } = payload;
      throw new AlertsApiError(response.status, typeof error === 'string' ? error : 'http_error', details);
    }
    return payload;
  }

  return {
    async health() {
      const payload = await ok(await call('GET', '/health'));
      const channel: AlertsChannel = payload.channel === 'push' ? 'push' : 'telegram'; // старий сервер — Telegram
      return { enabled: payload.enabled === true, channel, degraded: asStrings(payload.degraded) };
    },
    async list() {
      const payload = await ok(await call('GET', ''));
      const device = (payload.device as AlertsDevice | undefined) ?? null;
      return { alerts: (payload.alerts as AlertWire[]) ?? [], degraded: asStrings(payload.degraded), device };
    },
    async put(drawingId, body) {
      return (await ok(await call('PUT', `/${encodeURIComponent(drawingId)}`, body))).alert as AlertWire;
    },
    async remove(drawingId) {
      return (await ok(await call('DELETE', `/${encodeURIComponent(drawingId)}`))).removed === true;
    },
    async pair() {
      const payload = await ok(await call('POST', '/pair'));
      return payload as unknown as { link: string; label: string; expires_in_s: number };
    },
    async confirm(code) {
      // Хибні цифри (400), «бот ще не дав» (409), анульовано (410), нема (404) — не збій, а відповідь сторінці
      const response = await call('POST', '/pair/confirm', { code });
      const payload = await json(response);
      const status = payload.status;
      if (typeof status !== 'string' || !(CONFIRM_STATUSES as readonly string[]).includes(status)) {
        const reason = typeof payload.error === 'string' ? payload.error : 'bad_confirm_response';
        throw new AlertsApiError(response.status, reason);
      }
      return payload as unknown as ConfirmResult;
    },
    async pushKey() {
      const key = (await ok(await call('GET', '/push/key'))).public_key;
      if (typeof key !== 'string' || !key) throw new AlertsApiError(200, 'bad_push_key');
      return key;
    },
    async pushSubscribe(subscription) {
      return (await ok(await call('POST', '/push/subscribe', subscription))).device as { device_id: string; label: string };
    },
    async forgetDevice() {
      const removed = (await ok(await call('DELETE', '/device'))).removed_alerts;
      return typeof removed === 'number' ? removed : 0;
    },
    events({ onEvent, onOpen, onError }) {
      const source = eventSourceFactory(`${BASE}/events`);
      const relay = (name: AlertEventName) => (event: MessageEvent) => {
        try {
          onEvent(name, JSON.parse(event.data) as AlertWire);
        } catch (err) {
          console.warn('[alerts] ALERTS_EVENT_BAD_PAYLOAD %s %s', name, String(err));
        }
      };
      source.addEventListener('fired', relay('fired') as EventListener);
      source.addEventListener('delivered', relay('delivered') as EventListener);
      source.addEventListener('undelivered', relay('undelivered') as EventListener);
      if (onOpen) source.addEventListener('open', () => onOpen());
      if (onError) source.addEventListener('error', () => onError());
      return () => source.close();
    },
  };
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}
