# UI API Reference (WebSocket + HTTP)

> **Останнє оновлення**: 2026-03-29  
> **Навігація**: [docs/index.md](index.md)  
> **SSOT код**: `runtime/ws/ws_server.py`, `runtime/ws/candle_map.py`  
> **SSOT клієнт**: `ui_v4/src/ws/`, `ui_v4/src/app/frameRouter.ts`  
> **Принцип**: UI = read-only renderer (I1, G1). Тіки напряму не бачить. Не має доменної логіки.

Архітектура — **WS-only**: всі дані (bars, SMC overlay, config, scrollback) передаються через
WebSocket `/ws` з протоколом `ui_v4_v2`. Єдиний HTTP endpoint — `/api/status` (health check).

Процес: `runtime/ws/ws_server.py` (aiohttp, порт 8000, same-origin).  
UDS ініціалізується з `role="reader"` — будь-яка спроба запису → `RuntimeError`.

---

## Зміст

1. [HTTP Endpoints](#1-http-endpoints)
2. [WebSocket протокол](#2-websocket-протокол)
3. [Client → Server (actions)](#3-client--server-actions)
4. [Server → Client (frames)](#4-server--client-frames)
5. [Delta Loop](#5-delta-loop)
6. [SMC інтеграція](#6-smc-інтеграція)
7. [Candle Mapping](#7-candle-mapping)
8. [Guards та rails](#8-guards-та-rails)
9. [Конфігурація](#9-конфігурація)
10. [Reconnect та відмовостійкість](#10-reconnect-та-відмовостійкість)

---

## 1. HTTP Endpoints

| Endpoint | Метод | Призначення | Кеш |
|---|---|---|---|
| `/api/status` | GET | Health check (UDS стан, boot_id, кількість WS клієнтів) | no-cache |
| `/` | GET | SPA fallback → `ui_v4/dist/index.html` | no-cache |
| `/assets/*` | GET | Статичні файли Vite build | hashed filenames |

> Інших HTTP endpoints немає. `/api/bars`, `/api/updates`, `/api/overlay`, `/api/config` — **не існують**.

### GET /api/status

```json
{
  "status": "ok",
  "boot_id": "a1b2c3d4e5f67890",
  "ws_clients": 2,
  "server_ts_ms": 1711929600000
}
```

| Поле | Тип | Опис |
|---|---|---|
| `status` | `"ok"` \| `"no_uds"` | `"no_uds"` якщо UDS не ініціалізовано |
| `boot_id` | string (hex16) | Унікальний ID процесу, змінюється при рестарті |
| `ws_clients` | int | Кількість активних WS підключень |
| `server_ts_ms` | int | Серверний час (epoch ms) |

---

## 2. WebSocket протокол

| Параметр | Значення |
|---|---|
| **Endpoint** | `GET /ws` → WebSocket upgrade |
| **Schema version** | `"ui_v4_v2"` (константа `SCHEMA_V`) |
| **Бібліотека (сервер)** | aiohttp `WebSocketResponse` |
| **Бібліотека (клієнт)** | native browser `WebSocket` |
| **Subprotocol** | немає |

### Connection flow

```
Client                          Server
  │                               │
  ├─── WS handshake /ws ────────►│
  │                               ├─── config frame (symbols, tfs, defaults)
  │                               ├─── full frame (default symbol+tf, bars + SMC)
  │                               │
  │◄── delta frames (кожні 1s) ──┤  (global delta loop)
  │◄── heartbeat (кожні 30s) ────┤
  │                               │
  ├─── {"action":"switch"} ──────►├─── full frame (new symbol+tf)
  ├─── {"action":"scrollback"} ──►├─── scrollback frame (older bars)
  │                               │
```

1. Клієнт відкриває `new WebSocket(url)` — без subprotocol header
2. Сервер створює `WsSession` з унікальним `client_id` (12-char hex UUID)
3. Сервер надсилає **config frame** (список символів, TF, defaults)
4. Якщо UDS доступний — сервер надсилає **full frame** для пари за замовчуванням (`symbols[0]`, `M30`)
5. Якщо UDS недоступний — heartbeat frame як hello
6. Сервер запускає per-connection heartbeat loop (кожні `heartbeat_interval_s`, default 30s)
7. Глобальний delta loop broadcastить deltas всім підписаним сесіям

---

## 3. Client → Server (actions)

Всі повідомлення — JSON з полем `"action"`. Визначені в `ui_v4/src/types.ts` як `WsAction` union type.

### 3.1 `switch` — переключити символ/TF

```json
{
  "action": "switch",
  "symbol": "XAU/USD",
  "tf": "M30"
}
```

| Поле | Тип | Опис |
|---|---|---|
| `symbol` | string | Символ (підтримує `_` → `/` нормалізацію, напр. `"XAU_USD"` → `"XAU/USD"`) |
| `tf` | string | TF label (`"M15"`, `"1h"`, `"H4"`) або seconds як рядок (`"900"`) |

**Поведінка**: валідує symbol проти `symbols_set`, tf проти `tf_allowlist`. При успіху:
оновлює сесію, скидає `last_update_seq` та scrollback counter, надсилає новий **full frame**.

**При помилці**: надсилає full frame з порожніми candles + warning `["unknown_symbol"]` або `["tf_not_allowed"]`.

**Client guard**: `actions.ts` блокує відправку якщо symbol/tf не в server allowlist.

### 3.2 `scrollback` — запит старших барів

```json
{
  "action": "scrollback",
  "to_ms": 1711929600000
}
```

| Поле | Тип | Опис |
|---|---|---|
| `to_ms` | int | Epoch ms — найстаріший `open_time_ms` для завантаження |

**Поведінка**: читає з UDS `read_window(to_open_ms=to_ms)`, надсилає **scrollback frame**.

**Rails**:
- Макс. 12 chunks per session per symbol+tf (`SCROLLBACK_MAX_STEPS`)
- Cooldown 0.5s між запитами
- Chunk limit: `min(cold_start_bars, 500)`
- При порушенні — порожній scrollback frame з warning

### 3.3 Client-only actions (сервер НЕ обробляє)

Ці actions визначені в клієнті, але сервер повертає `unknown_action` error frame:

| Action | Призначення | Чому client-only |
|---|---|---|
| `overlay_toggle` | Видимість overlay шарів | Стан UI, не потребує серверу |
| `drawing_add/update/remove` | Малювання на графіку | ADR-0007: drawings = client-side |
| `replay_*` (`seek/step/play/pause/exit`) | Client-side replay | ADR-0027: replay локальний |

---

## 4. Server → Client (frames)

Всі повідомлення мають envelope:

```json
{
  "type": "render_frame",
  "frame_type": "<тип>",
  "meta": {
    "schema_v": "ui_v4_v2",
    "seq": 42,
    "server_ts_ms": 1711929600000,
    "boot_id": "a1b2c3d4e5f67890",
    "warnings": []
  },
  ...
}
```

| Поле meta | Тип | Опис |
|---|---|---|
| `schema_v` | string | Версія протоколу (клієнт відхиляє якщо ≠ `"ui_v4_v2"`) |
| `seq` | int | Per-connection монотонний лічильник (клієнт drop якщо `seq ≤ lastSeq`) |
| `server_ts_ms` | int | Серверний час |
| `boot_id` | string | ID процесу (при зміні → клієнт робить повний reset) |
| `warnings` | string[] | Деградації, guards, rails |

### 4.1 `config` frame

**Тригер**: одразу при підключенні (перед full frame).

```json
{
  "type": "render_frame",
  "frame_type": "config",
  "config": {
    "symbols": ["XAU/USD", "XAG/USD", "BTCUSDT", "ETHUSDT"],
    "tfs": ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"],
    "default_symbol": "XAU/USD",
    "default_tf": "M30"
  },
  "meta": { ... }
}
```

**Client**: зберігає в `serverConfig` store. Не потрапляє в `currentFrame`.

### 4.2 `full` frame

**Тригер**: при підключенні (після config), при `switch` action.

```json
{
  "type": "render_frame",
  "frame_type": "full",
  "symbol": "XAU/USD",
  "tf": "M30",
  "candles": [
    { "t_ms": 1711929600000, "o": 3012.5, "h": 3015.0, "l": 3010.0, "c": 3013.2, "v": 120.0 }
  ],
  "zones": [
    { "id": "ob_bull_XAU/USD_1800_17119...", "start_ms": 1711929600000,
      "high": 3015.0, "low": 3010.0, "kind": "ob_bull", ... }
  ],
  "swings": [
    { "id": "...", "kind": "hh", "time_ms": 1711929600000, "price": 3015.0 }
  ],
  "levels": [
    { "id": "...", "kind": "pdh", "price": 3020.0 }
  ],
  "trend_bias": "bullish",
  "zone_grades": { "<zone_id>": { "score": 8, "grade": "A+", "factors": ["sweep +2", ...] } },
  "bias_map": { "900": "bullish", "3600": "bearish" },
  "momentum_map": { "900": { "b": 3, "r": 1 } },
  "pd_state": {
    "range_high": 3020.0, "range_low": 3000.0,
    "equilibrium": 3010.0, "pd_percent": 65.0, "label": "PREMIUM"
  },
  "narrative": { "mode": "trade", "sub_mode": "aligned", "headline": "..." },
  "shell": { "stage": "ready", "stage_label": "SHORT · READY" },
  "signals": [{ "signal_id": "...", "direction": "short", ... }],
  "signal_alerts": [{ "signal_id": "...", "alert_type": "..." }],
  "drawings": [],
  "meta": {
    "schema_v": "ui_v4_v2",
    "seq": 3,
    "server_ts_ms": 1711929600000,
    "boot_id": "a1b2c3d4e5f67890",
    "warnings": [],
    "config": {
      "symbols": ["XAU/USD", "XAG/USD", "BTCUSDT", "ETHUSDT"],
      "tfs": ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"]
    }
  }
}
```

**Примітки**:
- `meta.config` дублюється у full frame (крім окремого config frame)
- `zones`, `swings`, `levels`, `trend_bias` — SmcSnapshot (повний стан SMC overlay)
- `zone_grades`, `bias_map`, `momentum_map`, `pd_state`, `narrative`, `shell`, `signals`, `signal_alerts` — присутні лише коли SMC engine ввімкнений і є дані

### 4.3 `delta` frame

**Тригер**: global delta loop (кожні `delta_poll_interval_s`, default 1.0s), коли UDS має нові events.

```json
{
  "type": "render_frame",
  "frame_type": "delta",
  "symbol": "XAU/USD",
  "tf": "M30",
  "candles": [
    { "t_ms": 1711929600000, "o": 3012.5, "h": 3015.0, "l": 3010.0,
      "c": 3013.2, "v": 120.0, "complete": false, "src": "tick_relay" }
  ],
  "smc_delta": {
    "new_zones": [...],
    "mitigated_zone_ids": ["..."],
    "updated_zones": [...],
    "new_swings": [...],
    "new_levels": [...],
    "removed_level_ids": ["..."],
    "trend_bias": "bullish"
  },
  "zone_grades": { ... },
  "bias_map": { ... },
  "momentum_map": { ... },
  "pd_state": { ... },
  "narrative": { ... },
  "shell": { ... },
  "signals": [...],
  "signal_alerts": [...],
  "session_levels": [{ "id": "...", "kind": "lon_h", "price": 3015.0 }],
  "meta": { ... }
}
```

**Примітки**:
- `smc_delta` — інкрементальні зміни (нові/мітиговані/оновлені зони, нові свінги/рівні)
- `complete: false` + `src: "tick_relay"` — формуюча свічка з tick stream
- SMC метадані (`zone_grades`, `bias_map`, тощо) присутні лише при complete bars (ADR-0042 P2)
- `session_levels` — сесійні рівні (Asia/London/NY H/L) з кожним delta

### 4.4 `scrollback` frame

**Тригер**: відповідь на client `scrollback` action.

```json
{
  "type": "render_frame",
  "frame_type": "scrollback",
  "symbol": "XAU/USD",
  "tf": "M30",
  "candles": [{ "t_ms": ..., "o": ..., "h": ..., "l": ..., "c": ..., "v": ... }],
  "meta": { ... }
}
```

### 4.5 `heartbeat` frame

**Тригер**: кожні `heartbeat_interval_s` (default 30s) per connection.

```json
{
  "type": "render_frame",
  "frame_type": "heartbeat",
  "meta": {
    "schema_v": "ui_v4_v2",
    "seq": 42,
    "server_ts_ms": 1711929600000,
    "boot_id": "a1b2c3d4e5f67890"
  }
}
```

**Client**: оновлює DiagState. Не потрапляє в `currentFrame`.

### 4.6 `error` frame

**Тригер**: невалідний JSON, відсутній action, невідомий action, надто великий message.

```json
{
  "type": "render_frame",
  "frame_type": "error",
  "error": {
    "code": "unknown_action",
    "message": "Unknown action: overlay_toggle"
  },
  "meta": { ... }
}
```

| Код | Опис |
|---|---|
| `json_parse_error` | Невалідний JSON |
| `missing_action` | Відсутнє поле `action` |
| `unknown_action` | Невідомий action (напр. `overlay_toggle`, `drawing_*`, `replay_*`) |
| `message_too_large` | Перевищено 64 KB ліміт |

**Client**: конвертує в `UiWarning`. Не потрапляє в `currentFrame`.

---

## 5. Delta Loop

**Функція**: `_global_delta_loop()` — один asyncio task на весь сервер (не per-client).

### Потік за один тік:

1. Sleep `delta_poll_interval_s` (default 1.0s)
2. Публікує viewer count в Redis (`ws:viewer_count`, TTL 30s) — для tick_preview_worker O3-sleep оптимізації
3. Групує сесії по `(symbol, tf_s)` target
4. Для кожного target з підписниками:
   - Знайти `min_seq` серед всіх підписників
   - `UDS.read_updates(since_seq=min_seq, include_preview=tf_in_preview_set)`
   - Якщо є events: dedup по `open_ms` (final beats preview), map to candles, build delta frame
   - Якщо events немає, але D1 tick relay ввімкнено: read last tick → forming candle delta
   - Inject SMC delta на complete bars (`on_bar_dict()` → `last_delta()`)
   - На complete bars: inject `zone_grades`, `bias_map`, `momentum_map`, `pd_state`, `narrative`, `shell`, `signals`, `signal_alerts`
   - Broadcast frame з per-client `seq` injection
5. Purge forming targets без підписників
6. M1 feed loop: poll M1 updates для всіх активних символів → SmcRunner session H/L tracking
7. Per-recipient cursor adoption: щойно підключені сесії (після full frame) пропускають перший delta

### Background SMC feed loop

**Функція**: `_bg_smc_feed_loop()` — кожні `bg_smc_poll_interval_s` (default 10s).

Живить SmcRunner всіма `compute_tfs × symbols` для cross-TF аналізу (D1/H4/H1/M15 bars змінюються рідко).

---

## 6. SMC інтеграція

### Full frame (SmcSnapshot — повний стан)

| Метод SmcRunner | Поле у frame | Опис |
|---|---|---|
| `get_snapshot(symbol, tf_s)` | `zones`, `swings`, `levels`, `trend_bias` | Повний SMC overlay |
| `get_zone_grades(symbol, tf_s)` | `zone_grades` | Confluence scoring: 8 факторів, grade A+/A/B/C |
| `get_bias_map(symbol)` | `bias_map` | Per-TF bias (bullish/bearish) |
| `get_momentum_map(symbol)` | `momentum_map` | Displacement detection per TF |
| `get_pd_state(symbol, tf_s)` | `pd_state` | Premium/Discount state |
| `get_narrative(symbol, tf_s, ...)` | `narrative` | Context Flow narrative block |
| `get_signals(symbol, tf_s, ...)` | `signals`, `signal_alerts` | Signal engine output |
| `get_shell_payload(symbol, tf_s, ...)` | `shell` | Thesis bar shell state |

### Delta frame (SmcDelta — інкрементальні зміни)

На complete bars: `on_bar_dict(symbol, tf_s, bar)` → SmcEngine → `last_delta()`:

```json
{
  "new_zones": [...],
  "mitigated_zone_ids": ["zone_id_1", "zone_id_2"],
  "updated_zones": [...],
  "new_swings": [...],
  "new_levels": [...],
  "removed_level_ids": ["level_id_1"],
  "trend_bias": "bullish"
}
```

Після обробки: `clear_delta(symbol, tf_s)`.

---

## 7. Candle Mapping

**Файл**: `runtime/ws/candle_map.py`

### Wire формат (Candle)

```json
{ "t_ms": 1711929600000, "o": 3012.5, "h": 3015.0, "l": 3010.0, "c": 3013.2, "v": 120.0 }
```

Відповідає `ui_v4/src/types.ts:Candle`.

### Flat bar filtering (display-only, не впливає на SSOT)

| TF | Умова фільтрації |
|---|---|
| LTF (<H4) | `O == H == L == C` і `V ≤ 10` (або `calendar_pause_flat`) |
| HTF (≥H4) | Flat AND weekend open (Fri/Sat/Sun UTC) |

### Tail normalization

- `h = max(o, h, low, c)`, `low = min(o, h, low, c)` — гарантує `h ≥ l` на wire
- Volume: default 0, negative → 0

### TF label mapping

| Label | Секунди | Альтернативи |
|---|---|---|
| `M1` | 60 | `1m` |
| `M3` | 180 | `3m` |
| `M5` | 300 | `5m` |
| `M15` | 900 | `15m` |
| `M30` | 1800 | `30m` |
| `H1` | 3600 | `1h` |
| `H4` | 14400 | `4h` |
| `D1` | 86400 | `1d` |

Server→client завжди використовує uppercase canonical labels.

---

## 8. Guards та rails

### Output guards

`_guard_candles_output()` валідує кожну candle перед відправкою:
- Required: `t_ms` (>0), `o`/`h`/`l`/`c` (числа, не NaN), `h ≥ l`
- Невалідні candles = drop + degraded-but-loud warning
- Перевірка `t_ms` монотонності (no dups, sorted asc)

### Rate limits та ліміти

| Guard | Значення | Опис |
|---|---|---|
| Max WS message size | 64 KB | `_MAX_WS_MSG_BYTES` — перевищення → error frame |
| Scrollback max steps | 12 per session per symbol+tf | `SCROLLBACK_MAX_STEPS` |
| Scrollback cooldown | 0.5s | Між запитами |
| Scrollback chunk | `min(cold_start_bars, 500)` | Макс. барів за один запит |
| Broadcast send timeout | 1.0s per client | Slow client → eviction |
| Log sanitization | Control chars stripped, 120 char max | Від client messages |

### Slow-client eviction

`_safe_broadcast()`: якщо `send_str` timeout >1s → клієнт evicted: сесія видаляється, WS закривається.

### Client-side guards

| Guard | Поведінка |
|---|---|
| `schema_v` check | Reject frame якщо `≠ "ui_v4_v2"` |
| `seq` monotonicity | Drop frame якщо `seq ≤ lastSeq` |
| `boot_id` change | Reset seq baseline, clear view caches, запит нового full frame |
| Cross-TF split-brain | Drop delta/scrollback якщо `symbol:tf` не збігається з поточною парою |

---

## 9. Конфігурація

З `config.json`:

```json
{
  "ws_server": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8000,
    "heartbeat_interval_s": 30,
    "delta_poll_interval_s": 1.0,
    "bg_smc_poll_interval_s": 10.0,
    "cors_allowed_origins": []
  }
}
```

### Cold start bars (bootstrap.ui_cold_start_bars_by_tf)

| TF | Барів |
|---|---|
| M1 (60) | 10080 |
| M3 (180) | 3360 |
| M5 (300) | 2016 |
| M15 (900) | 2000 |
| M30 (1800) | 1000 |
| H1 (3600) | 2000 |
| H4 (14400) | 2000 |
| D1 (86400) | 1000 |

Default fallback: 300 барів (`DEFAULT_COLD_START_BARS`).

---

## 10. Reconnect та відмовостійкість

**Файл**: `ui_v4/src/ws/connection.ts`

| Параметр | Значення |
|---|---|
| Перший reconnect | 2s delay |
| Backoff | ×1.5, макс. 10s |
| Після 3 невдач | Quiet mode: 60s інтервал + wake on `visibilitychange` |
| На reconnect | `resetFrameRouter()`: clear seq baseline, boot_id, всі кешовані стани |

### boot_id change

Якщо `meta.boot_id` змінився (сервер перезапустився):
1. Client скидає seq baseline
2. Fires `_onBootIdChange` callback
3. Clears view caches
4. Чекає на новий full frame (автоматично приходить від сервера)
