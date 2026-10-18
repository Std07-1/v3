// src/lib/alerts/browserPush.ts
// ADR-0114 Rev 2 §2a: підписка браузера на пуш — дозвіл, service worker сайту (`public/sw.js`), `pushManager`.
//
// Порядок у кліку важливий: дозвіл просимо ПЕРШИМ. Safari пускає запит дозволу лише з жесту, а будь-який await
// мережі перед ним жест «губить» — тому ключ VAPID сторінка бере заздалегідь (контролер — на старті).
// Середовище (`PushEnv`) підмінне: у тестах браузерних API нема.

import type { PushSubscriptionWire } from './alertClient';

/** Чи може ця сторінка взагалі отримувати пуш (до запиту дозволу). */
export type PushSupport = 'ok' | 'unsupported' | 'ios_home_screen' | 'denied';

export interface PushEnv {
  /** Є service worker, PushManager і Notification. */
  hasPush: boolean;
  permission(): NotificationPermission;
  requestPermission(): Promise<NotificationPermission>;
  /** Реєстрація SW сайту; у dev-режимі Vite SW не реєструється — undefined. */
  registration(): Promise<ServiceWorkerRegistration | undefined>;
  isIos: boolean;
  /** Відкрито як додаток з початкового екрана (iOS дає пуш лише так, 16.4+). */
  isStandalone: boolean;
}

/** Причина, з якої підписки не буде; `code` — для `alertErrorText`. */
export class PushError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'PushError';
  }
}

export function browserPushEnv(): PushEnv {
  const nav = navigator as Navigator & { standalone?: boolean };
  const hasPush = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  return {
    hasPush,
    permission: () => (hasPush ? Notification.permission : 'denied'),
    requestPermission: () => Notification.requestPermission(),
    registration: () => navigator.serviceWorker.getRegistration('/'),
    isIos: /iPad|iPhone|iPod/.test(nav.userAgent) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1),
    isStandalone: window.matchMedia?.('(display-mode: standalone)').matches === true || nav.standalone === true,
  };
}

export function pushSupport(env: PushEnv): PushSupport {
  if (env.isIos && !env.isStandalone) return 'ios_home_screen';
  if (!env.hasPush) return 'unsupported';
  if (env.permission() === 'denied') return 'denied';
  return 'ok';
}

/** Дозвіл → підписка (стара з іншим ключем сервера — знімається) → JSON для `/api/alerts/push/subscribe`. */
export async function subscribeBrowser(env: PushEnv, publicKey: string): Promise<PushSubscriptionWire> {
  const support = pushSupport(env);
  if (support !== 'ok') throw new PushError(`push_${support}`);
  const permission = await env.requestPermission(); // першим — поки жест кліку ще живий
  if (permission !== 'granted') throw new PushError('push_denied');
  const registration = await env.registration();
  if (!registration) throw new PushError('no_service_worker');
  const key = urlBase64ToBytes(publicKey);
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !sameBytes(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe(); // ключ сервера змінився — стара підписка вже не дійде
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  return subscription.toJSON() as PushSubscriptionWire;
}

/** «Вимкнути на цьому пристрої»: підписку браузера теж знімаємо (сервер свою вже забув). */
export async function unsubscribeBrowser(env: PushEnv): Promise<void> {
  if (!env.hasPush) return;
  const registration = await env.registration();
  const subscription = await registration?.pushManager.getSubscription();
  await subscription?.unsubscribe();
}

export function urlBase64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const base64 = (value + '='.repeat((4 - (value.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}

function sameBytes(current: ArrayBuffer | null, expected: Uint8Array): boolean {
  if (current === null) return false;
  const bytes = new Uint8Array(current);
  return bytes.length === expected.length && bytes.every((byte, i) => byte === expected[i]);
}
