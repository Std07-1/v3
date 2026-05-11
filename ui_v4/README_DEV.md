# UI v4 — Developer Guide

## Стек

- **Svelte 5** (runes mode) + **Vite 6** + **TypeScript 5.7**
- **lightweight-charts 5.0.0** — LWC (свічковий чарт)
- **uuid** — клієнтські ID для drawings
- Transport: **WebSocket** (Transport B)

## Швидкий старт

```bash
cd ui_v4
npm install          # одноразово
npm run dev          # dev-сервер на http://localhost:5173
npm run typecheck    # перевірка типів (svelte-check)
npm run build        # production build → dist/
```

## Dev port map

| Процес                        | Порт  | Що робить                                      |
|-------------------------------|-------|------------------------------------------------|
| `npm run dev` (Vite)          | 5173  | UI dev-сервер; проксює `/api/*` → 8000         |
| `app.main --mode ws_server`   | 8000  | WS + HTTP API (`/ws`, `/api/*`)                |

Скопіюй `.env.example` → `.env.local` (git-ignored) для локальних overrides.

## Env-змінні (Vite)

| Змінна                   | Default                      | Опис                                              |
|--------------------------|------------------------------|---------------------------------------------------|
| `VITE_WS_URL`            | `ws://localhost:8000/ws`     | WebSocket URL (app.main_connector)                |
| `VITE_API_PROXY_TARGET`  | `http://localhost:8000`      | Proxy target для `/api/*` (ws_server)             |
| `VITE_EDGE_PROBE_URL`    | `/api/status`                | Health probe endpoint (відносний, через proxy)    |

Приклад:

```bash
VITE_WS_URL=ws://192.168.1.50:8000/ws npm run dev
```

## Архітектура

```
src/
├── types.ts              # SSOT типи (RenderFrame, WsAction, Candle, …)
├── main.ts               # Svelte mount
├── App.svelte            # Root wiring: WS + DiagState + UI shell
│
├── app/
│   ├── diagState.ts      # DiagState SSOT (WS, network, frame freshness)
│   ├── diagSelectors.ts  # Derived mainStatus (priority scale)
│   ├── frameRouter.ts    # WS frame handler (guards, stores)
│   └── edgeProbe.ts      # /healthz probe при WS disconnect
│
├── ws/
│   ├── connection.ts     # WSConnection (reconnect + DiagState hooks)
│   └── actions.ts        # WsAction creators (switchSymbolTf, scrollback, …)
│
├── stores/
│   └── meta.ts           # Cursor price + UI warnings
│
├── layout/
│   ├── ChartPane.svelte  # 3-layer composite (LWC + overlay + drawings)
│   ├── DrawingToolbar.svelte
│   ├── SymbolTfPicker.svelte
│   ├── StatusBar.svelte
│   └── StatusOverlay.svelte
│
└── chart/
    ├── lwc.ts            # Re-export ChartEngine
    ├── engine.ts         # LWC wrapper
    ├── overlay/          # SMC overlay renderer
    └── drawings/         # Drawings renderer + command stack
```

## DiagState → StatusBar → StatusOverlay

Потік діагностики:

1. `connection.ts` → `diagStore.onWsOpen/Close/Message/Error`
2. `frameRouter.ts` → `diagStore.onValidFrame(seq, serverTs)`
3. `window.online/offline` → `diagStore.setNetOffline()`
4. `edgeProbe.ts` → `diagStore.setEdgeProbe()` (коли WS down)
5. `diagSelectors.ts` → `mainStatus` derived store
6. `StatusBar.svelte` — показує статус внизу (dot + label + latency + warnings)
7. `StatusOverlay.svelte` — модальний overlay при critical states

Пріоритет статусів (згори вниз):
`FRONTEND_ERROR → OFFLINE → EDGE_BLOCKED → WS_UNAVAILABLE → STALLED → CONNECTING → HEALTHY`

## Keyboard shortcuts

| Key | Дія |
|-----|-----|
| T   | Toggle trend line tool |
| H   | Toggle horizontal line tool |
| R   | Toggle rectangle tool |
| E   | Toggle eraser |
| Esc | Cancel current tool / draft |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z / Ctrl+Y | Redo |

## Контракти

- `RenderFrame` / `WsAction` — визначені в `types.ts` (SSOT)
- `schema_v = 'ui_v4_v2'` — guard у `frameRouter.ts`
- Зміна контрактів потребує **ADR** (заборонено змінювати без узгодження)

## Surface contracts (locked ADRs — ЧИТАТИ перед UI змінами)

| Surface | ADR | Що locked |
|---------|-----|-----------|
| Desktop top-right corner (CommandRail + NarrativePanel pill + ☰) | [ADR-0070](../docs/adr/ADR-0070-tr-corner-canonical.md) | ATR/RV з `frame.atr`/`frame.rv` (X28), NP scope = pure Архі-surface, click-outside-collapse, wording (`Арчі`), liveness via `archi_thesis.freshness` |
| **Mobile** (portrait `<640px` + landscape phone `max-height:500px`) | [ADR-0072](../docs/adr/ADR-0072-mobile-canonical-layout.md) | ☰ position empirical (right:44, top:2, transparent — **NO backdrop "пятно"**), portrait hide-list, landscape hide-list (different — keeps status row), vertical row 1 alignment, `(orientation: landscape) and (max-height: 500px)` detection |
| CommandRail layout (CR-2.5) | [ADR-0065 rev 2](../docs/adr/ADR-0065-rev2-command-rail-final.md) | Slot composition, ☰ overflow contract, mobile breakpoint baseline |
| NarrativePanel state machine | [ADR-0069](../docs/adr/ADR-0069-narrative-panel-state-aware.md) | 3-mode (compact/banner/expanded), agent_state derivation, sessionStorage override, escalation reset |
| BrandWatermark slot | [ADR-0068](../docs/adr/ADR-0068-brand-surface-info-hub.md) | Bottom-left LOCKED (do NOT move): desktop 36/12, mobile 30/6 |
| Visual identity (tokens, themes) | [ADR-0066 rev 5](../docs/adr/ADR-0066-visual-identity-system.md) | Color tokens, T1-T8 typography, candle style matrix |
| PWA Full Standalone (manifest + SW) | [ADR-0071](../docs/adr/ADR-0071-pwa-full-standalone.md) | **PROPOSED** — manual SW shell-only V1, never cache `/api/*` or WS data |

### Перед будь-якою mobile UI зміною

1. Прочитай **ADR-0072 §Forbidden patterns** — список того що **гарантовано викине rollback** (backdrop на `☰`, calc() з `minimumWidth`, NP pill на mobile, тощо).
2. Виміри геометрії — **empirical, not theoretical**. Live screenshot, count pixels.
   ADR-0072 §Empirical measurements має re-measurement protocol.
3. Якщо потрібен новий surface на mobile — створи новий ADR-NNNN, не дописуй
   у ADR-0072 (його scope locked).
4. Localhost dev (`npm run dev`) НЕ симулює mobile WebView точно — тестуй через
   справжній телефон + Chrome DevTools mobile emulation як baseline.

### Mobile breakpoints — quick reference

| Regime | Detection | Where applies |
|--------|-----------|---------------|
| Portrait phone | `@media (max-width: 640px)` | App.svelte chrome |
| Portrait phone (HUD) | `@media (max-width: 768px)` | ChartHud (historical, ADR-0072 §Decision A) |
| Landscape phone | `@media (orientation: landscape) and (max-height: 500px)` | App.svelte + ChartHud (ADR-0072 §Decision B) |
| Tablet | none of the above | Desktop layout applies |
| Desktop | none of the above | Default |

Не використовувати `(max-width: 900px)` для landscape phone — захопить tablets теж.
Канонічно: `orientation + max-height` per ADR-0072 §"Why this query".
