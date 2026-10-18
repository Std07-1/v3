// src/lib/alerts/browserPush.test.ts — ADR-0114 Rev 2 S12: підписка браузера на пуш (API браузера — підставні).
import { describe, expect, it, vi } from 'vitest';
import {
  PushError,
  pushSupport,
  subscribeBrowser,
  unsubscribeBrowser,
  urlBase64ToBytes,
  type PushEnv,
} from './browserPush';

const KEY = 'BAEC'; // base64url трьох байтів [4, 1, 2]
const KEY_BYTES = [4, 1, 2];

function fakeSubscription(keyBytes: number[], endpoint = 'https://fcm.googleapis.com/fcm/send/x') {
  return {
    options: { applicationServerKey: new Uint8Array(keyBytes).buffer },
    unsubscribe: vi.fn(async () => true),
    toJSON: () => ({ endpoint, keys: { p256dh: 'p', auth: 'a' } }),
  };
}

function env(overrides: Partial<PushEnv> & { existing?: ReturnType<typeof fakeSubscription> | null } = {}) {
  const order: string[] = [];
  const created = fakeSubscription(KEY_BYTES, 'https://fcm.googleapis.com/fcm/send/new');
  const pushManager = {
    getSubscription: vi.fn(async () => overrides.existing ?? null),
    subscribe: vi.fn(async () => {
      order.push('subscribe');
      return created;
    }),
  };
  const base: PushEnv = {
    hasPush: true,
    permission: () => 'default',
    requestPermission: vi.fn(async () => {
      order.push('permission');
      return 'granted' as NotificationPermission;
    }),
    registration: vi.fn(async () => {
      order.push('registration');
      return { pushManager } as unknown as ServiceWorkerRegistration;
    }),
    isIos: false,
    isStandalone: false,
  };
  return { env: { ...base, ...overrides } as PushEnv, order, pushManager, created };
}

describe('browserPush', () => {
  it('support: iPhone outside home screen, no API, denied, ok', () => {
    expect(pushSupport(env({ isIos: true, isStandalone: false }).env)).toBe('ios_home_screen');
    expect(pushSupport(env({ isIos: true, isStandalone: true }).env)).toBe('ok');
    expect(pushSupport(env({ hasPush: false }).env)).toBe('unsupported');
    expect(pushSupport(env({ permission: () => 'denied' }).env)).toBe('denied');
    expect(pushSupport(env().env)).toBe('ok');
  });

  it('asks permission FIRST (gesture), then the service worker, then subscribes with the server key', async () => {
    const { env: e, order, pushManager } = env();
    const json = await subscribeBrowser(e, KEY);
    expect(order).toEqual(['permission', 'registration', 'subscribe']);
    expect(json.endpoint).toBe('https://fcm.googleapis.com/fcm/send/new');
    const options = pushManager.subscribe.mock.calls[0] as unknown as [PushSubscriptionOptionsInit];
    expect(options[0].userVisibleOnly).toBe(true);
    expect([...(options[0].applicationServerKey as Uint8Array)]).toEqual(KEY_BYTES);
  });

  it('reuses a subscription made with the same key, replaces one made with another key', async () => {
    const same = fakeSubscription(KEY_BYTES, 'https://fcm.googleapis.com/fcm/send/same');
    const kept = env({ existing: same });
    expect((await subscribeBrowser(kept.env, KEY)).endpoint).toBe('https://fcm.googleapis.com/fcm/send/same');
    expect(kept.pushManager.subscribe).not.toHaveBeenCalled();

    const stale = fakeSubscription([9, 9, 9]);
    const replaced = env({ existing: stale });
    expect((await subscribeBrowser(replaced.env, KEY)).endpoint).toBe('https://fcm.googleapis.com/fcm/send/new');
    expect(stale.unsubscribe).toHaveBeenCalledOnce();
  });

  it('refusals carry a code: not supported, denied now, no service worker', async () => {
    const codeOf = (p: Promise<unknown>) => p.catch((e: PushError) => e.code);
    expect(await codeOf(subscribeBrowser(env({ isIos: true }).env, KEY))).toBe('push_ios_home_screen');
    expect(await codeOf(subscribeBrowser(env({ hasPush: false }).env, KEY))).toBe('push_unsupported');
    const denied = env({ requestPermission: async () => 'denied' as NotificationPermission });
    expect(await codeOf(subscribeBrowser(denied.env, KEY))).toBe('push_denied');
    expect(denied.pushManager.subscribe).not.toHaveBeenCalled();
    expect(await codeOf(subscribeBrowser(env({ registration: async () => undefined }).env, KEY))).toBe('no_service_worker');
  });

  it('unsubscribe drops the browser subscription; without push API does nothing', async () => {
    const existing = fakeSubscription(KEY_BYTES);
    await unsubscribeBrowser(env({ existing }).env);
    expect(existing.unsubscribe).toHaveBeenCalledOnce();
    const none = env({ hasPush: false });
    await unsubscribeBrowser(none.env);
    expect(none.env.registration).not.toHaveBeenCalled();
  });

  it('url-safe base64 key decodes with or without padding', () => {
    expect([...urlBase64ToBytes('BAEC')]).toEqual([4, 1, 2]);
    expect([...urlBase64ToBytes('-_8')]).toEqual([251, 255]);
  });
});
