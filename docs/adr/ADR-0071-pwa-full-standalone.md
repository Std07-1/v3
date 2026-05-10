# ADR-0071: PWA Full Standalone — Manifest + Service Worker (Shell-only V1)

## Metadata

| Field          | Value                                                                |
| -------------- | -------------------------------------------------------------------- |
| ID             | ADR-0071                                                              |
| Status         | PROPOSED                                                              |
| Date           | 2026-05-11                                                            |
| Authors        | Станіслав                                                             |
| Supersedes     | —                                                                     |
| Builds on      | ADR-0066 rev 5 (visual identity, mark-v3.svg source); ADR-0070 (TR corner — `theme-color` + `apple-mobile-web-app-capable` already shipped as PWA prep meta tags) |
| Affects layers | `ui_v4/public/manifest.json` (NEW), `ui_v4/public/sw.js` (NEW), `ui_v4/public/offline.html` (NEW), `ui_v4/public/icons/*.png` (NEW, generated), `ui_v4/scripts/gen-icons.mjs` (NEW), `ui_v4/index.html` (MODIFY — `<link rel="manifest">` + apple-touch-icon), `ui_v4/src/main.ts` (MODIFY — SW register), `ui_v4/src/layout/InstallButton.svelte` (NEW) |

---

## Quality Axes

- **Ambition target**: R3 — concrete UX gain (full-screen "feels native"), defined V1 scope (shell-only), explicit V2 forward path; not over-engineering for hypothetical offline trading
- **Maturity impact**: M3 → M4 — installable artifact with deterministic build, versioned cache, explicit update flow, no opaque `vite-plugin-pwa` magic that future agents would have to reverse-engineer

---

## Context

After ADR-0070 we shipped `theme-color`, `apple-mobile-web-app-capable`,
`viewport-fit=cover`, and safe-area insets in `index.html`. These are
PWA *primers* — they make the browser address bar paint our dark bg
("visual blend") but do **not** install the app or hide the bar.

Trader feedback summary:

> "верх де ссилка з'їдає площу + чарт виглядає як сайт у браузері, не як native app"

Full PWA standalone resolves this by making the platform launchable from
the home screen with **no browser chrome at all**. Bonus: branded splash,
offline shell when connection drops, app icon in OS programs list.

The mark source `ui_v4/public/brand/mark-v3.svg` (1346 bytes,
ADR-0066 Tier 3) is the canonical icon — V dominant + 3 supplementary,
gold `#D4A017` on dark `#0D1117`. It must be rasterized to several
fixed-size PNGs because:

- iOS Safari `apple-touch-icon` requires PNG (no SVG support there)
- Android adaptive icons require a maskable PNG with 60% safe area
- Chrome Lighthouse PWA audit demands at least 192px and 512px PNG icons in manifest

Trading-specific constraint: a trader **must not act on stale data**.
When connection drops, showing a cached frame from 30 minutes ago risks
misleading position sizing or entry calls. V1 must therefore show
"offline" explicitly, not silently serve cached frames.

---

## Alternatives considered

### A. `vite-plugin-pwa` (workbox-based, automatic)

**Pros**: one-line config, auto-generates SW + manifest + injects icon
plugin (pwa-assets-generator), opinionated cache strategies for static
assets, version-controlled invalidation built in.

**Cons**: ~200KB of dependencies (workbox + companion packages), magic
behavior (precache manifest auto-injected at build time, hard to grep
later), couples future maintenance to the workbox release cadence,
discourages reading what the SW actually does. Also default workbox
cache strategies are oriented toward classic SPAs, not WS-driven
real-time apps — would need overrides anyway.

**Verdict**: rejected. The "magic" violates the craftsmanship-first F9
spirit (six months from now no one remembers what files workbox
precaches, only that it does). For a 100-LOC service worker, hand-rolled
is more maintainable.

### B. Hand-written manifest + SW + manual icon-gen script (CHOSEN)

**Pros**: every line of cache logic is grep-able and readable. No
runtime dependencies (manifest.json is JSON, sw.js is plain ES modules,
icons are committed PNGs). Update path is explicit (bump SW version
constant → SW evicts old cache on activate). Sharp dev dep is used
**once** at icon-gen time, not at every build.

**Cons**: ~150 LOC manual code (manifest 30 + SW 80 + offline.html 30
+ install button 40 + gen-icons script 50). Manual cache invalidation
discipline required: bump `SW_VERSION` whenever shipping breaking
changes to cached assets.

**Verdict**: accepted. The LOC budget is small enough that legibility
wins. Versioned cache is simpler than workbox's precache-manifest.

### C. Defer PWA, ship Add-to-Home-Screen workaround only

Status quo after ADR-0070: meta tags in place, on iOS Safari "Add to
Home Screen" launches in standalone mode (because we have
`apple-mobile-web-app-capable: yes`), but on Android Chrome there is no
install prompt without a manifest, and there is no offline behavior
anywhere.

**Verdict**: rejected. Half-measures — Android users get nothing, no
offline behavior, no programmatic install button. Trader perception of
"not native" persists.

---

## Decision

Implement **option B** in 6 sequential P-slices. V1 ships a
shell-only PWA: cached app shell + offline page + branded install
button. **No live data caching.** When offline, the user sees an
explicit "Підключення втрачено" screen, not stale chart data.

### Architecture diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ Browser                                                         │
│                                                                 │
│  ┌────────────────────┐   register   ┌───────────────────────┐  │
│  │ index.html         │ ───────────► │ /sw.js                │  │
│  │ <link manifest>    │              │ const SW_VERSION=...  │  │
│  │ <link apple-touch> │              │ install: precache     │  │
│  │ <script main.ts>   │              │ activate: evict old   │  │
│  └────────────────────┘              │ fetch:                │  │
│         │                            │  ├─ navigate → cache  │  │
│         │ navigate                   │  │  fallback offline  │  │
│         ▼                            │  ├─ /api/* → network  │  │
│  ┌────────────────────┐              │  │  (no cache)        │  │
│  │ /manifest.json     │              │  ├─ /ws/*  → network  │  │
│  │ name AI·ONE v3     │              │  │  (passthrough)     │  │
│  │ display standalone │              │  └─ static → cache 1st│  │
│  │ icons[] PNGs       │              └───────────────────────┘  │
│  │ start_url /        │                                         │
│  │ theme #0D1117      │                                         │
│  │ background #0D1117 │              ┌───────────────────────┐  │
│  │ scope /            │              │ /offline.html         │  │
│  └────────────────────┘              │ branded splash +      │  │
│                                      │ "Підключення втрачено"│  │
│  ┌────────────────────┐              │ + retry button        │  │
│  │ InstallButton      │              └───────────────────────┘  │
│  │ captures           │                                         │
│  │ beforeinstallprompt│                                         │
│  └────────────────────┘                                         │
└─────────────────────────────────────────────────────────────────┘
```

### Cache strategy table (V1)

| Request kind | Strategy | Reason |
| ------------ | -------- | ------ |
| Navigation (HTML doc) | network-first → cache fallback → `/offline.html` | Latest HTML wins when online; offline page when both fail |
| Static asset (`/assets/*.js`, `/assets/*.css`, `/brand/*`, `/icons/*`) | cache-first | Hash-fingerprinted by Vite, immutable per build |
| `/api/*` HTTP | **network-only, no cache** | Trader contract: never serve stale domain data |
| `/ws/*` WebSocket | **passthrough, SW does not intercept** | WS upgrade requests are not cacheable anyway |
| `/manifest.json`, `/sw.js` | network-only | Update mechanism — must always reflect server state |

### SW versioning + update flow

`SW_VERSION` is a constant at the top of `sw.js`, bumped manually when
breaking cache layout. On `activate`, SW iterates `caches.keys()` and
deletes anything not matching the current version. Clients pick up the
new SW on next reload (we will NOT auto-skipWaiting in V1 — explicit
reload is safer; user sees the new build only after they intend to).

### V2 forward path (NOT in this ADR)

When trader UX matures and we want optional offline last-frame:

1. Add IndexedDB writer in `runtime/ws/ws_server.py`-paired client code
   that snapshots last `render_frame.full` per (symbol, tf).
2. Add `<StaleDataBanner>` component that shows "ДАНІ ЗАСТАРІЛІ
   {age_min}хв" with explicit dismiss + acknowledgement.
3. Trader must tap "I acknowledge stale data" before any chart
   interaction is enabled in offline mode.
4. New ADR with separate accept/reject decision.

V2 is explicitly NOT in scope here. V1 ships honest "no live = nothing".

---

## Implementation slices

| # | Slice | Files | LOC | Dependencies |
| --- | ----- | ----- | --- | ------------ |
| P1 | Manifest + meta tag wiring | `ui_v4/public/manifest.json` (NEW), `ui_v4/index.html` (MOD: `<link rel="manifest">`, apple-touch-icon hint, manifest charset) | ~40 | none |
| P2 | Icon generation script + outputs | `ui_v4/scripts/gen-icons.mjs` (NEW), `ui_v4/public/icons/*.png` (7 NEW: 192, 512, maskable-512, apple-touch-180, favicon-32, favicon-16, og-1200x630), `ui_v4/package.json` (+devDep `sharp`, +script `gen-icons`) | ~80 + assets | sharp |
| P3 | Service Worker (shell-only V1) | `ui_v4/public/sw.js` (NEW) | ~100 | — |
| P4 | Offline page | `ui_v4/public/offline.html` (NEW) | ~60 | — |
| P5 | SW registration + version sync | `ui_v4/src/main.ts` (MOD: register sw.js, hook update detection), `ui_v4/vite.config.ts` (MOD: define `__SW_VERSION__` from package.json) | ~30 | — |
| P6 | InstallButton component + InfoModal entry | `ui_v4/src/layout/InstallButton.svelte` (NEW), `ui_v4/src/layout/InfoModal.svelte` (MOD: add "Встановити на пристрій" row in About tab) | ~80 | — |

**Sequential order**: P1 → P2 → P3 → P4 → P5 → P6. Each slice is
deployable on its own and harmless without the next (P1 alone gives
manifest discoverability for Lighthouse; P3 without P5 is dead code,
but no regression).

**Parallelism opportunity**: P2 (icon gen, mostly asset generation
work) can run in parallel with P3+P4 (SW + offline page) because they
touch disjoint paths.

---

## Manifest spec (P1 lock)

```json
{
  "name": "AI·ONE v3",
  "short_name": "AI·ONE",
  "description": "Smart Money Concepts trading platform · agent-led real-time chart",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#0D1117",
  "theme_color": "#0D1117",
  "lang": "uk",
  "dir": "ltr",
  "categories": ["finance", "productivity"],
  "icons": [
    {
      "src": "/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/icon-maskable-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ]
}
```

**Locked values** (do not change without ADR amendment):

- `name: "AI·ONE v3"` — owner-confirmed (`AI·ONE` is brand short form;
  `v3` distinguishes from any future major rev)
- `short_name: "AI·ONE"` — appears under home-screen icon (12-char limit)
- `display: "standalone"` — no browser chrome at all (vs `minimal-ui`
  which keeps a thin URL strip)
- `background_color` + `theme_color: "#0D1117"` — owner-confirmed; matches
  ADR-0066 dark canonical bg + `theme-color` meta tag from ADR-0070
- `start_url: "/"` — install always opens app root (last symbol/tf
  restoration handled by existing `saveLastPair` localStorage logic)
- `scope: "/"` — entire origin is in-scope (vs scoping to `/app/*`
  which would force a redesign of routes)

---

## Icon generation contract (P2 lock)

`scripts/gen-icons.mjs` reads `public/brand/mark-v3.svg` and writes
**exactly** these 7 PNG outputs to `public/icons/`:

| File | Size | Purpose | Composition |
| ---- | ---- | ------- | ----------- |
| `icon-192.png` | 192×192 | manifest standard | full SVG fit (the SVG already has a 64×64 viewBox with built-in dark plate; sharp renders it edge-to-edge) |
| `icon-512.png` | 512×512 | manifest standard, splash on Android | same |
| `icon-maskable-512.png` | 512×512 | Android adaptive icon | mark scaled to **60% canvas (307×307)** centered on solid `#0D1117` background, leaving 20% safe-area margin per Android maskable spec |
| `apple-touch-icon-180.png` | 180×180 | iOS home screen | full SVG fit, no safe-area (iOS auto-rounds corners) |
| `favicon-32.png` | 32×32 | tab icon HiDPI | full SVG fit |
| `favicon-16.png` | 16×16 | tab icon LDPI | full SVG fit |
| `og-1200x630.png` | 1200×630 | OG share preview (Telegram, Twitter, Facebook unfurl) | mark centered (300×300) on `#0D1117`, "AI·ONE v3" wordmark below in `#E6EDF3` Inter 800, gold `·` separator in `#D4A017` |

`og-1200x630` is generated alongside icons because the script already
has sharp loaded — bonus asset, used by `<meta property="og:image">`
which we'll add in P1.

**Re-run policy**: `npm run gen-icons` is invoked **only** when
`public/brand/mark-v3.svg` changes. Outputs are committed PNGs (not
regenerated per build) so production builds are deterministic and don't
require sharp at deploy time.

---

## Service Worker spec (P3 lock)

```js
// /sw.js — shell-only V1, hand-written, no workbox.
const SW_VERSION = 'v1-2026-05-11';   // bump on cache-layout breaking change
const SHELL_CACHE = `shell-${SW_VERSION}`;
const SHELL_PRECACHE = [
  '/',
  '/offline.html',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/manifest.json',
];
// Vite-hashed assets (/assets/*) caught by runtime cache-first below.

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL_PRECACHE)));
  // Do NOT skipWaiting — explicit reload by user is the update trigger.
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== SHELL_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API: never cache, always network. Stale data is forbidden.
  if (url.pathname.startsWith('/api/')) return; // passthrough

  // WebSocket upgrade: SW cannot intercept WS, but defensive skip anyway.
  if (e.request.headers.get('upgrade') === 'websocket') return;

  // Navigation: network-first → cache → offline.html
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request)
        .then(res => { caches.open(SHELL_CACHE).then(c => c.put(e.request, res.clone())); return res; })
        .catch(() => caches.match(e.request).then(r => r || caches.match('/offline.html')))
    );
    return;
  }

  // Static assets: cache-first (Vite hashes ensure freshness on rebuild).
  if (url.origin === self.location.origin) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        return cached || fetch(e.request).then(res => {
          if (res.ok && res.type === 'basic') {
            caches.open(SHELL_CACHE).then(c => c.put(e.request, res.clone()));
          }
          return res;
        });
      })
    );
  }
});
```

**Forbidden in SW** (no future drift):

- Caching `/api/*` responses, ever. Stale domain data is the worst sin.
- Caching WebSocket frames or any subset of WS traffic.
- Caching responses with `?` query params unless the params are part of
  the canonical resource (Vite hashes are in pathname, not query).
- `skipWaiting()` without an explicit user-driven update flow (V1: no).

---

## Update flow

1. Developer ships new build → new `index.html` + new hashed `/assets/*.js`.
2. Browser fetches `/sw.js` on next page load, sees identical bytes →
   no SW update unless `SW_VERSION` constant changed.
3. **If `SW_VERSION` bumped**: browser installs new SW in `waiting`
   state. Old SW stays active until all tabs of the app close.
4. On next launch: new SW activates, evicts old caches, claims clients.

**Manual override** (user trapped in old SW): Settings → Site Settings →
Clear data, OR uninstall + reinstall PWA.

V2 future enhancement: add a "ОНОВЛЕННЯ ДОСТУПНЕ" toast in InfoModal
when `navigator.serviceWorker.controller` differs from latest registered.
Out of scope V1.

---

## Install UX

`InstallButton.svelte` lives in `InfoModal.svelte` "About" tab as a
single row:

```
┌──────────────────────────────────────────────┐
│  📲 Встановити на пристрій                   │
│  Запускати як native app, без браузера       │
│  [        Встановити        ]                │
└──────────────────────────────────────────────┘
```

Behavior:

- **Chrome/Edge desktop + Android**: capture `beforeinstallprompt`
  event on app load, store the deferred prompt, button calls
  `deferredPrompt.prompt()` on click.
- **iOS Safari**: no `beforeinstallprompt` (Apple does not implement).
  Button text changes to "Інструкція для iPhone" → opens hint:
  "Натисни ⎋ Поділитися → Додати на головний екран".
- **Already installed (matchMedia '(display-mode: standalone)')**: row
  hidden entirely.
- **User dismissed**: `localStorage.setItem('install-dismissed', '1')` →
  row hidden until manually re-opened from InfoModal in the next session.

---

## Consequences

### Visible delta after V1 rollout

Mobile (Android Chrome) trader workflow:
1. Visits `aione-smc.com` → top-right `⋮` Chrome menu shows "Встановити
   AI·ONE" OR our InfoModal install button is available.
2. Taps install → home-screen icon appears with our gold mark on dark
   plate.
3. Taps icon → app launches **fullscreen, no address bar, no browser
   chrome** — feels like a native trading app.
4. Network drops → sees branded offline page (not browser default
   "Network error" page), tap "Спробувати знову" → reload.

iOS Safari: same outcome via Share → Add to Home Screen, but no
in-app install button (Apple platform limit).

### Lighthouse PWA audit

V1 should pass all categories: installable (manifest + icons + SW
register), apple-touch-icon present, theme-color set, viewport
configured, content sized correctly. Anticipated score 100.

### Bandwidth + cache footprint

Shell precache: ~360 KB (current `index.html` 4.32 KB + `index-*.js`
358 KB + small icons). Per-user, persists across sessions until cache
eviction by browser quota.

### Risks

| Risk | Mitigation |
| ---- | ---------- |
| User installs PWA, then we ship breaking SW change without `SW_VERSION` bump → caches diverge → user gets broken app | Code review checklist: any change to `SHELL_PRECACHE` array OR routing logic requires `SW_VERSION` bump in same commit. |
| User installed PWA on top of cached old shell → no update visible until tab close | V1 documented behavior. V2 toast is the upgrade path. |
| Sharp install fails on contributor machine (native binary) | gen-icons script is opt-in (run on owner machine, outputs committed). New contributor never needs to run sharp. Document in script header. |
| iOS users do not see install button | Documented as platform limit. Apple's "Add to Home Screen" route is mentioned in InstallButton iOS path. |
| Service worker traps user in old version after VPS rollback | Rollback recipe includes `SW_VERSION` bump. Bumping forward (even in rollback) ensures clients evict. |

---

## Rollback

V1 reversal per-slice in reverse order:

1. **P6**: `git revert` InstallButton + InfoModal mod. Removes button only;
   PWA still installable via browser native menu.
2. **P5**: revert main.ts + vite.config.ts. SW de-registers on next page
   load (browsers honor missing `register()` only after manual cache
   clear; document this in revert commit message — recommend bumping
   `SW_VERSION` to force eviction).
3. **P4**: revert offline.html. Browser shows native error page on
   offline navigation.
4. **P3**: revert sw.js. SW becomes 404 — browsers eventually unregister.
   Combined with P5 revert is the clean removal.
5. **P2**: revert icons + script + sharp dep. Manifest icons 404 (manifest
   degrades gracefully, browser falls back to favicon).
6. **P1**: revert manifest.json + index.html link tags. Returns to
   ADR-0070 state (theme-color blend only, no install).

**Full PWA disable in production without revert** (emergency):

- Replace `/opt/smc-v3/ui_v4/dist/sw.js` with a body that calls
  `self.registration.unregister()` for all clients, deploy. Existing
  installs detect SW gone on next load, app reverts to plain web.
- Bump `SW_VERSION` constant if shipping any subsequent PWA fix.

---

## Notes

### Why not skipWaiting?

`self.skipWaiting()` causes the new SW to activate immediately, which
sounds nice but mid-trade can replace the active client with a partially
loaded new build (race between client.claim and asset cache rebuilds).
Trader is mid-tap → screen flickers / partial state.

V1 takes the conservative path: explicit user reload activates new SW.
V2 may reconsider with a "soft reload" button in toast.

### Why not workbox precache manifest?

vite-plugin-pwa would inject a generated precache manifest into the SW
at build time. Means every build, the SW changes (new file hashes)
even when shipping non-cache-related code. Triggers SW update for every
deploy, fights skipWaiting policy. Manual `SW_VERSION` is opt-in:
deployer chooses when to invalidate caches.

### Cross-ref to ADR-0070

ADR-0070 §"Backend dependency" amendment is unchanged — `frame.atr`,
`frame.rv` continue to ship via WS. PWA does not cache WS data.
ADR-0070 meta tags in `index.html` (`theme-color`, `apple-mobile-web-app-capable`,
`viewport-fit`, safe-area) are **prerequisites** for this ADR — they
are kept and supplemented by `<link rel="manifest">` + `<link rel="apple-touch-icon">`.

### Future: ADR-0072 NarrativeSheet (mobile Архі-surface)

Mentioned forward-reference in ADR-0070 §Boundary. Not affected by
ADR-0071 — they touch disjoint surfaces (PWA = browser chrome / install,
NarrativeSheet = in-app mobile component).
