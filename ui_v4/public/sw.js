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

const SW_VERSION = 'v1-2026-05-11b';
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
  // No skipWaiting() — explicit reload activates new SW (no mid-trade flicker).
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
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request)
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
