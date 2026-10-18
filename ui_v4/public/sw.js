// ADR-0071 P3 — Service Worker shell-only V1.
// ────────────────────────────────────────────────────────────────────────
// Goal: enable PWA install (Chrome/Edge/Safari) + branded offline fallback.
// NEVER cache /api/* responses (trader contract — stale data forbidden).
// NEVER cache /ws/* (WebSocket upgrade not cacheable).
// Static assets: cache-first (Vite hashes ensure freshness on rebuild).
//
// Update flow: bump SW_VERSION → next page-load installs new SW → activate
// evicts old caches. NO skipWaiting() — user reload activates explicitly.
// ────────────────────────────────────────────────────────────────────────

// v2 (ADR-0082 hotfix): navigation fetch з cache:'no-cache' — SW's fetch()
// шанував евристичний HTTP-кеш браузера, тож «network-first» міг повернути
// ЗАСТАРІЛИЙ index.html зі старим hashed-bundle (мікс builds між вкладками).
// Bump версії виселяє shell-кеші, що тримали старі копії HTML.
// v3 (ADR-0066 rev 6): бренд «v3» замість «AI · ONE» — offline.html і manifest.json лежать у shell-кеші, без bump
// встановлений PWA показував би стару назву.
// v4 (ADR-0114 Rev 2): пуш лінії-сповіщення — обробники push і notificationclick нижче.
const SW_VERSION = 'v3-2026-10-18';
const SHELL_CACHE = `shell-${SW_VERSION}`;
const SHELL_PRECACHE = [
  '/',
  '/offline.html',
  '/manifest.json',
  '/brand/mark-v4.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon-180.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL_PRECACHE))
  );
  // v2: skipWaiting УВІМКНЕНО (перегляд V1-рішення). Без нього клієнти з
  // отруєним HTTP-кешем (stale index → старий bundle) сидять на v1 до
  // закриття УСІХ вкладок. Наш SW shell-only — активація v2 не міняє
  // відкриту сторінку (жодного mid-trade flicker), лише лагодить наступні
  // навігації через no-cache fetch.
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // ADR-0071 contract — API never cached. Stale trading data is forbidden.
  if (url.pathname.startsWith('/api/')) return; // passthrough

  // WebSocket upgrade — defensive skip (SW cannot intercept WS anyway).
  if (e.request.headers.get('upgrade') === 'websocket') return;

  // Navigation (HTML doc): network-first → cache → offline fallback.
  // cache:'no-cache' — оминаємо евристичний HTTP-кеш (ревалідація по ETag,
  // 304 = дешево); інакше «network-first» повертав застарілий index.html.
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request, { cache: 'no-cache' })
        .then((res) => {
          // Cache fresh navigation responses for offline fallback.
          // POST requests cannot be cached (Cache API limitation).
          if (res && res.ok && res.type === 'basic' && e.request.method === 'GET') {
            const clone = res.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() =>
          caches.match(e.request).then((r) => r || caches.match('/offline.html'))
        )
    );
    return;
  }

  // Same-origin static assets: cache-first (GET only — POST not cacheable).
  if (url.origin === self.location.origin && e.request.method === 'GET') {
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;
        return fetch(e.request).then((res) => {
          if (res && res.ok && res.type === 'basic') {
            const clone = res.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(e.request, clone));
          }
          return res;
        });
      })
    );
  }
});

// ── ADR-0114 Rev 2: пуш лінії-сповіщення ──────────────────────────────────
// Тіло пушу (зашифроване сервером smc-alerts): {title, body, url, tag}. tag = alert_id — повторна доставка того
// самого спрацювання не дублює показ. Вкладка сайту видима й у фокусі — вона вже показала спливне і звук (SSE),
// тож сповіщення ОС не дублюємо (Chrome не вимагає показу, коли сторінка сайту на передньому плані).
self.addEventListener('push', (e) => {
  let data = {};
  try {
    data = e.data ? e.data.json() : {};
  } catch (err) {
    data = { title: 'v3', body: e.data ? e.data.text() : '' };
  }
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      if (windows.some((w) => w.visibilityState === 'visible' && w.focused)) return undefined;
      return self.registration.showNotification(data.title || 'v3', {
        body: data.body || '',
        tag: data.tag,
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        data: { url: data.url || '/' },
      });
    })
  );
});

// Натиск на сповіщення — графік символу й TF лінії. Адресу беремо лише як шлях і запит на СВОЄМУ походженні:
// пуш не може відкрити чужий сайт. Відкрита вкладка сайту — фокус і перехід; нема — нова вкладка.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const wanted = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin);
  const target = new URL(wanted.pathname + wanted.search, self.location.origin).href;
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      const own = windows.find((w) => new URL(w.url).origin === self.location.origin);
      if (own) return own.focus().then((focused) => (focused.navigate ? focused.navigate(target) : focused));
      return self.clients.openWindow(target);
    })
  );
});
