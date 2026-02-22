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
| `npm run dev` (Vite)          | 5173  | UI dev-сервер; проксює `/api/*` → 8089         |
| `app.main_connector`          | 8000  | WebSocket сервер (`/ws`); живі фрейми          |
| `ui_chart_v3` (HTTP)          | 8089  | REST API (`/api/*`); health: `GET /api/status` |

Скопіюй `.env.example` → `.env.local` (git-ignored) для локальних overrides.

## Env-змінні (Vite)

| Змінна                   | Default                      | Опис                                              |
|--------------------------|------------------------------|---------------------------------------------------|
| `VITE_WS_URL`            | `ws://localhost:8000/ws`     | WebSocket URL (app.main_connector)                |
| `VITE_API_PROXY_TARGET`  | `http://localhost:8089`      | Proxy target для `/api/*` (ui_chart_v3)           |
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
