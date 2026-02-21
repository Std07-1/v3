# P6: Config & Policy SSOT

> **Документ**: code-first, кожен факт має evidence `(file:line)`.
> **Дата**: 2026-02-21
> **Scope**: config.json SSOT, config_loader, Policy SSOT аналіз, config consumption, validation, caching/hot-reload, hardcoded policy violations, client policy bridge.
> **Залежності**: P1 (Process Inventory), P3 (UDS/Store), P4 (API Surface), P5 (Contracts & Guards).

---

## Зміст

- [1. Загальна картина](#1-загальна-картина)
- [2. config.json — SSOT конфігурації](#2-configjson)
- [3. Config loader — core/config_loader.py](#3-config-loader)
- [4. Config consumption map](#4-config-consumption)
- [5. Policy: TF Allowlists](#5-tf-allowlists)
- [6. Policy: Symbol Allowlist](#6-symbol-allowlist)
- [7. Policy: Polling Intervals](#7-polling-intervals)
- [8. Policy: Redis TTL та Tail](#8-redis-ttl-tail)
- [9. Policy: Preview plane](#9-preview-plane)
- [10. Policy: Calendar](#10-calendar)
- [11. Policy: Stitching](#11-stitching)
- [12. Policy: Limit clamping та Window](#12-limit-clamping)
- [13. Policy: Day Anchor Offsets](#13-anchor-offsets)
- [14. Config validation](#14-config-validation)
- [15. Config caching & hot-reload](#15-config-caching)
- [16. Client policy bridge (/api/config)](#16-client-policy-bridge)
- [17. Hardcoded policy audit](#17-hardcoded-audit)
- [18. Policy SSOT compliance matrix](#18-ssot-compliance)
- [19. Знахідки та ризики](#19-знахідки)

---

## 1. Загальна картина

```
┌─────────────────────────────────────────────────────────────────┐
│                Config & Policy Architecture                      │
│                                                                  │
│  ┌── config.json (278 lines) ─── SSOT ──────────────────────┐   │
│  │  Identity: 13 symbols, data_root                          │   │
│  │  Connector: retry, circuit breaker, safety delays         │   │
│  │  M1 Poller: tail, backfill, live_recover, stale           │   │
│  │  TF: tf_allowlist_s, preview_tick_tfs_s                   │   │
│  │  Preview/Tick: enabled, intervals, TTL, auto-promote      │   │
│  │  Calendar: 4 groups × per-symbol mapping                  │   │
│  │  Day Anchor: 5 offset variants                            │   │
│  │  Redis: host/port/db/ns, TTL per TF, tail_n per TF       │   │
│  │  Bootstrap: priming, warmup, cold_start bars per TF       │   │
│  │  Channels: 5 Redis pub/sub names                          │   │
│  │  Known outages: manual broker annotations                 │   │
│  └───────────────────────────────────────────────────────────┘   │
│               │                                                  │
│     ┌─────────┼──────────────────────────────────────────┐       │
│     ▼         ▼                                          ▼       │
│  core/        app/composition.py      ui_chart_v3/server.py      │
│  config_      (own load_config L20)   (_load_cfg_cached L469)    │
│  loader.py    (validates once L83)    (hot-reload 0.5s mtime)    │
│  (SSOT        ▼                                          ▼       │
│   loader)  connector                   /api/config → app.js      │
│     │      (once)                      (policy bridge)           │
│     ▼                                                            │
│  m1_poller, tick_publisher, tick_preview_worker                   │
│  (read once at startup, NO hot-reload)                           │
│                                                                  │
│  ┌── .env.example ──────────────────────────────────────────┐    │
│  │  FXCM_USERNAME, FXCM_PASSWORD, FXCM_CONNECTION,          │    │
│  │  FXCM_HOST_URL — тільки секрети (Rule №4)                │    │
│  └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Ключові факти**:

- SSOT конфіг: `config.json` (278 рядків, ~20 секцій).
- Config loader: `core/config_loader.py` (170 LOC, 7 функцій).
- ENV: тільки FXCM секрети + `AI_ONE_CONFIG_PATH` для override шляху.
- Hot-reload: **тільки UI server** (`_load_cfg_cached`, mtime check per 0.5s).
- Validation: **тільки в connector** (`_validate_config`, `composition.py:83`).
- Hardcoded policy violations: **14+ знайдено** (див. §17).
**Критична невідповідність**: `PREVIEW_CURR_TTL_S` — config=1800s vs uds.py=120s vs tick_preview_worker default=60s (**3-way mismatch**).

---

## 2. config.json — SSOT конфігурації

**Файл**: `config.json` (278 рядків).

### 2.1 Top-level секції

| Секція | Рядки | Ключі |
|--------|-------|-------|
| Identity | L1-L18 | `symbol`, `symbols[]` (13 інструментів), `data_root` |
| Connector retry | L19-L30 | `safety_delay_s`(2), `connector_retry_base_s`(10), `connector_retry_max_s`(3600), `connector_wake_ahead_s`(900) |
| History circuit | L23-L30 | `history_summary/still_failing/circuit_fail_streak/base_s/max_s/log_interval_s/symbols_sample_n/network_error_escalate_s` |
| M1 Poller | L31-45 | Nested `m1_poller{}` — enabled, tail_fetch_n(5), safety_delay_s(8), m3_derive, backfill, live_recover, stale_s(720) |
| Broker base | L46-54 | `broker_base_tfs_s`([86400]), `broker_base_fetch_on_close`(true), `broker_base_max_tf_per_poll`(0), cold_start |
| TF allowlists | L55-64 | `tf_allowlist_s` = [60,180,300,900,1800,3600,14400,86400] |
| Preview/ticket | L65-72 | `preview_tick_enabled`(true), `preview_tick_tfs_s`([60,180]), `preview_tick_publish_min_interval_ms`(250), `preview_curr_ttl_s`(1800), `tick_auto_promote_m1`(true) |
| Stitching | L73 | `ui_stitching_enabled`(true) |
| Tick stream | L74-L79 | `tick_stream_enabled`(true), `tick_stream_symbols`([]), `tick_stream_min_interval_ms`(200), `tick_stream_last_tick_ttl_s`(30), `tick_stream_price_mode`("bid") |
| Calendar | L80-L150 | `calendar_gate_enabled`(true), `market_calendar_by_group{}` (4 groups), `market_calendar_symbol_groups{}` (13 mappings), `market_ignore_minutes_utc[]`, `market_boundary_slip_minutes_per_day`(2) |
| Day anchor | L149-153 | 5 offset variants: 82800/79200/68400/75600/79200 |
| Debug | L154-155 | `ui_debug`(true), `group_logs_enabled`(true) |
| Redis priming | L156-168 | `redis_priming_enabled`(true), `redis_priming_budget_s`(15), `redis_priming_tfs_s/symbols` |
| Min coldload | L169-178 | `min_coldload_bars_by_tf_s{}` — per-TF мінімум (60:1440, 300:2016, ... 86400:365) |
| Redis | L179-206 | Nested `redis{}` — enabled, host/port/db/ns, `ttl_by_tf_s{}`, `tail_n_by_tf_s{}` |
| Channels | L207-214 | `channels{}` — ohlcv, price_tick, status, commands, heartbeat |
| Bootstrap | L215-245 | `bootstrap{}` — prime_ready_timeout_s(30), cascade_catchup_m1_bars, warmup/cold_start bars per TF |
| Known outages | L246-278 | `known_broker_outages[]` — manual annotations (5 entries) |

### 2.2 Типо в config.json

> Evidence: `config.json:L210`.
> `"price_tick": "fxcm_local:price_tik"` — **typo** в channel name ("tik" замість "tick").
> Це legacy, всі консьюмери читають цей ключ як є.

---

## 3. Config loader — core/config_loader.py

**Файл**: `core/config_loader.py` (~170 LOC).

### 3.1 Функції

| Функція | Line | Опис |
|---------|------|------|
| `resolve_config_path(raw_path)` | L18 | Resolve path relative to repo root |
| `pick_config_path()` | L36 | ENV `AI_ONE_CONFIG_PATH` → fallback `config.json` |
| `load_system_config(path)` | L48 | `json.load()` — кожен виклик читає файл з нуля |
| `env_str(key)` | L62 | Safe ENV read + strip |
| `tf_allowlist_from_cfg(cfg)` | L78 | Extract TF allowlist з fallback chain |
| `preview_tf_allowlist_from_cfg(cfg)` | L126 | Extract preview TF allowlist |
| `min_coldload_bars_from_cfg(cfg)` | L162 | Extract min coldload bars per TF |

### 3.2 Default константи

| Константа | Line | Значення |
|-----------|------|----------|
| `DEFAULT_TF_ALLOWLIST` | L73 | `{300, 900, 1800, 3600, 14400, 86400}` (без preview 60/180) |
| `DEFAULT_PREVIEW_TF_ALLOWLIST` | L74 | `{60, 180}` |
| `MAX_EVENTS_PER_RESPONSE` | L75 | `500` |

> Evidence: `core/config_loader.py:73-75`.

### 3.3 Fallback chain для TF allowlist

```python
def tf_allowlist_from_cfg(cfg) → set[int]:
    # 1. cfg["tf_allowlist_s"] (list) → set
    # 2. cfg["polling_intervals_s"] (legacy, dict keys) → set
    # 3. DEFAULT_TF_ALLOWLIST fallback
```

> Evidence: `core/config_loader.py:78-123`.

---

## 4. Config consumption map

### 4.1 Config loader usage по процесах

| Процес | Файл:line | Як читає | Hot-reload? |
|--------|----------|----------|-------------|
| **Connector** | `app/composition.py:21` | Власний `load_config()` (json.load) | ✗ (once) |
| **Orchestrator** | `app/main.py:16` | `pick_config_path()` | ✗ (once, для child processes) |
| **M1 Poller** | `runtime/ingest/polling/m1_poller.py:23` | `pick_config_path`, `load_system_config` | ✗ (once) |
| **Tick Publisher** | `runtime/ingest/tick_publisher_fxcm.py:10` | `pick_config_path`, `load_system_config`, `env_str` | ✗ (once) |
| **Tick Preview Worker** | `runtime/ingest/tick_preview_worker.py:10` | `pick_config_path`, `load_system_config` | ✗ (once) |
| **UI Server** | `ui_chart_v3/server.py:151` | `pick_config_path`, `tf_allowlist_from_cfg`, `preview_tf_allowlist_from_cfg` | **✓** (0.5s mtime) |
| **UDS** | `runtime/store/uds.py:15-16` | `tf_allowlist_from_cfg`, `preview_tf_allowlist_from_cfg`, `min_coldload_bars_from_cfg` | ✗ (once at build) |
| **aione_top** | `aione_top/collectors.py:136` | Власний `load_config()` | ✗ (once) |

### 4.2 ENV змінні

| ENV Key | Використання | Файл |
|---------|-------------|------|
| `AI_ONE_CONFIG_PATH` | Override config file path | `config_loader.py:41` |
| `FXCM_USERNAME` | FXCM credentials | `composition.py`, `m1_poller`, `tick_publisher`, tools |
| `FXCM_PASSWORD` | FXCM credentials | Same |
| `FXCM_CONNECTION` | Demo/Real mode | Same |
| `FXCM_HOST_URL` | FXCM server URL | Same |
| `FXCM_REDIS_HOST/PORT/DB/NS` | Redis override (gated) | `redis_spec.py:78-107` |
| `FXCM_PRICE_TICK_CHANNEL` | Tick channel override | `tick_common.py:27` |

> Evidence: `runtime/store/redis_spec.py` — Redis ENV overrides gated by `allow_env_override` flag (currently `false` у config.json).

### 4.3 Знахідки config consumption

**F1**: `app/composition.py:21` та `aione_top/collectors.py:136` мають **власні** `load_config()` функції (простий `json.load`), не використовують `core.config_loader.load_system_config()`. Legacy дублювання.

**F2**: UDS отримує config одноразово через `build_uds_from_config()` (`uds.py:~2036`). Зміни config.json після старту UDS — невидимі для store layer.

---

## 5. Policy: TF Allowlists

### 5.1 Три визначення TF allowlist (дублювання)

| Джерело | Значення | Файл:line |
|---------|----------|-----------|
| **config.json** (SSOT) | `[60,180,300,900,1800,3600,14400,86400]` | `config.json:55-64` |
| **core/buckets.py** (guard) | `{60,180,300,900,1800,3600,14400,86400}` | `core/buckets.py:10` |
| **core/config_loader.py** (fallback) | `{300,900,1800,3600,14400,86400}` (без 60/180) | `core/config_loader.py:73` |

> **⚠ Знахідка F3**: `TF_ALLOWLIST` у `core/buckets.py:10` — **hardcoded duplicate** config.json. Якщо додати новий TF в config — `tf_to_ms()` guard все одно відкине його, бо `buckets.py` не читає config.

### 5.2 Preview TF allowlist (дублювання)

| Джерело | Значення | Файл:line |
|---------|----------|-----------|
| config.json (SSOT) | `[60, 180]` | `config.json:67-69` |
| config_loader.py (fallback) | `{60, 180}` | `core/config_loader.py:74` |
| gate_preview_not_on_disk.py | `{60, 180}` | `tools/exit_gates/gates/gate_preview_not_on_disk.py:8` |

### 5.3 Потік TF allowlist

```
config.json:tf_allowlist_s
    ↓
core/config_loader.py:tf_allowlist_from_cfg(cfg) → set[int]
    ↓                                    ↓
UDS._tf_allowlist (build_uds)    server._load_cfg_cached → per-request
    ↓
m1_poller/connector — separately parse from cfg
```

---

## 6. Policy: Symbol Allowlist

**SSOT**: `config.json:3-17` → `symbols[]` (13 інструментів).

| Консьюмер | Файл:line | Як читає |
|-----------|----------|----------|
| Connector | `composition.py:262` | Direct cfg["symbols"] |
| M1 Poller | `m1_poller.py:1116` via `tick_common.py:43` (`symbols_from_cfg`) | cfg["symbols"] fallback cfg["symbol"] |
| Tick Publisher | `tick_publisher_fxcm.py:410` | stream_cfg.symbols ∪ symbols_from_cfg |
| Tick Preview | `tick_preview_worker.py:492` | preview_cfg.symbols ∪ symbols_from_cfg |
| UI Server | per-request from cfg | Direct cfg["symbols"] |

> `symbols_from_cfg()` у `tick_common.py:43` — fallback `cfg["symbol"]` (одиночний).

---

## 7. Policy: Polling Intervals

### 7.1 M1 Poller

| Параметр | config.json key | Значення | Line |
|----------|----------------|----------|------|
| Cycle delay | `m1_poller.safety_delay_s` | 8s | L34 |
| Bars per fetch | `m1_poller.tail_fetch_n` | 5 | L33 |
| Stale detection | `m1_poller.stale_s` | 720s (12 min) | L44 |
| Live recover cooldown | `m1_poller.live_recover_cooldown_s` | 5s | L41 |
| Backfill max bars | `m1_poller.backfill_max_bars` | 1440 | L37 |

### 7.2 D1 Connector

| Параметр | config.json key | Значення | Line |
|----------|----------------|----------|------|
| Safety delay | `safety_delay_s` | 2s | L19 |
| Base TFs | `broker_base_tfs_s` | [86400] | L46-48 |
| Retry base | `connector_retry_base_s` | 10s | L20 |
| Retry max | `connector_retry_max_s` | 3600s (1h) | L21 |
| Wake ahead | `connector_wake_ahead_s` | 900s (15 min) | L22 |

### 7.3 Tick Stream

| Параметр | config.json key | Значення | Line |
|----------|----------------|----------|------|
| Min interval | `tick_stream_min_interval_ms` | 200ms | L77 |
| Last tick TTL | `tick_stream_last_tick_ttl_s` | 30s | L78 |
| Preview publish interval | `preview_tick_publish_min_interval_ms` | 250ms | L70 |

---

## 8. Policy: Redis TTL та Tail

### 8.1 TTL per TF

**SSOT**: `config.json:186-195` → `redis.ttl_by_tf_s{}`.

| TF | Ключ | TTL | Human |
|----|------|-----|-------|
| M1 (60) | `"60"` | 86400 | 1 день |
| M3 (180) | `"180"` | 86400 | 1 день |
| M5 (300) | `"300"` | 259200 | 3 дні |
| M15 (900) | `"900"` | 259200 | 3 дні |
| M30 (1800) | `"1800"` | 259200 | 3 дні |
| H1 (3600) | `"3600"` | 259200 | 3 дні |
| H4 (14400) | `"14400"` | 604800 | 7 днів |
| D1 (86400) | `"86400"` | 604800 | 7 днів |

### 8.2 Tail_N per TF

**SSOT**: `config.json:196-206` → `redis.tail_n_by_tf_s{}`.

| TF | Tail bars |
|----|-----------|
| M1 (60) | 2880 (2 дні) |
| M3 (180) | 1440 (3 дні) |
| M5 (300) | 8000 (~28 днів) |
| M15 (900) | 4000 (~42 дні) |
| M30 (1800) | 2500 (~52 дні) |
| H1 (3600) | 2000 (~83 дні) |
| H4 (14400) | 256 (~107 днів) |
| D1 (86400) | 128 (~175 днів) |

### 8.3 Hardcoded Redis policy (NOT from config)

| Параметр | Файл:line | Значення | Конфігурується? |
|----------|----------|----------|----------------|
| `REDIS_SOCKET_TIMEOUT_S` | `uds.py:43` | 0.4s | ✗ |
| `PREVIEW_CURR_TTL_S` | `uds.py:44` | **120s** | ✗ (**MISMATCH**: config.json `preview_curr_ttl_s`=1800s) |
| `PREVIEW_TAIL_RETAIN` | `uds.py:45` | 2000 bars | ✗ |
| `PREVIEW_UPDATES_RETAIN` | `uds.py:46` | 2000 events | ✗ |
| `UPDATES_REDIS_RETAIN_DEFAULT` | `uds.py:94` | 2000 events | ✗ |

> **⚠ Знахідка F4**: `PREVIEW_CURR_TTL_S = 120` (uds.py:44) vs `preview_curr_ttl_s: 1800` (config.json:71) — **15x різниця**. UDS hardcode ігнорує config SSOT.

---

## 9. Policy: Preview plane

| Параметр | config.json key | Значення | Файл:line |
|----------|----------------|----------|-----------|
| Enabled | `preview_tick_enabled` | true | `config.json:66` |
| TFs | `preview_tick_tfs_s` | [60, 180] | `config.json:67-69` |
| Publish interval | `preview_tick_publish_min_interval_ms` | 250ms | `config.json:70` |
| Preview TTL (config) | `preview_curr_ttl_s` | 1800s | `config.json:71` |
| Auto-promote M1 | `tick_auto_promote_m1` | true | `config.json:72` |

**Consumption**: `tick_preview_worker.py` парсить ці ключі у `PreviewConfig` dataclass — `tick_preview_worker.py:~81-88`.

---

## 10. Policy: Calendar

### 10.1 Calendar groups

**SSOT**: `config.json:80-150` → 4 groups.

| Group | Weekend open | Weekend close | Daily break | Symbols |
|-------|-------------|---------------|-------------|---------|
| `fx_24x5_utc_winter` | Sun 22:00 | Fri 21:55 | Break: 21:55-22:30 | GBP/CAD, NZD/CAD, USD/CAD, USD/JPY |
| `cfd_us_22_23` | Sun 23:00 | Fri 21:45 | Break: 22:00-23:00 | XAU/USD, XAG/USD, NGAS, SPX500, NAS100, US30 |
| `cfd_eu_21_07` | Mon 07:00 | Fri 21:00 | Break: 21:00-07:00 | GER30, EUSTX50 |
| `cfd_hk_main` | Mon 01:15 | Fri 19:00 | Break: 19:00-01:15, 04:00-05:00, 08:30-09:15 | HKG33 |

### 10.2 Calendar consumers

| Консьюмер | Як створюється |
|-----------|---------------|
| M1 Poller | `m1_poller.py:~1184-1190` — `MarketCalendar` per symbol from group config |
| Tick Preview Worker | `tick_preview_worker.py:~485` — `MarketCalendar` per symbol |
| Derive Engine | Uses calendar from UDS/poller context |

### 10.3 Спеціальні ключі

- `market_ignore_minutes_utc[]` — manual ignore list (1 entry). Evidence: `config.json:L149`.
- `market_boundary_slip_minutes_per_day` — tolerance for boundary detection (2 min). Evidence: `config.json:L151`.

---

## 11. Policy: Stitching

| Параметр | SSOT | Значення | Evidence |
|----------|------|----------|----------|
| Server-side | `config.json:73` → `ui_stitching_enabled` | `true` | Consumed at `server.py:1343` (preview path), `server.py:1428` (final path) |
| Default fallback | `cfg.get("ui_stitching_enabled", False)` | `False` | Guard: якщо ключ відсутній → disabled |

Stitching перетворює `bar.open = previous_bar.close` для UI display як LightweightCharts-compat.

---

## 12. Policy: Limit clamping та Window

### 12.1 _TF_CAP (hardcoded in server.py)

**НЕ з config.json** — hardcoded у `server.py:294-303`.

| TF | Cap (bars) | Еквівалент |
|----|-----------|------------|
| M1 (60) | 10,080 | 7 днів |
| M3 (180) | 3,360 | 7 днів |
| M5 (300) | 20,000 | max |
| M15 (900) | 20,000 | max |
| M30 (1800) | 20,000 | max |
| H1 (3600) | 20,000 | max |
| H4 (14400) | 5,000 | ~208 днів |
| D1 (86400) | 3,650 | ~10 років |

`_MAX_BARS_CAP = 20,000` — absolute maximum. Evidence: `server.py:293`.

### 12.2 Warmup та Cold Start bars

Два набори per-TF барів:

| config.json key | Секція | Evidence |
|----------------|--------|----------|
| `bootstrap.ui_warmup_bars_by_tf` | Bars for warmup | `config.json:225-234` |
| `bootstrap.ui_cold_start_bars_by_tf` | Bars for cold start | `config.json:235-244` |

**Server.py hardcoded defaults** (overridden from config if present):

- `_WARMUP_BARS_BY_TF` at `server.py:159-168`.
- `_COLD_START_BARS_BY_TF` at `server.py:170-179`.

### 12.3 Scrollback chunk

**Hardcoded** у `server.py:367-372` — `scrollback_chunk_by_tf` (300-3600: 1000 bars). Exposed через `/api/config → window_policy.scrollback_chunk_by_tf`.

### 12.4 Min coldload bars

**SSOT**: `config.json:169-178` → `min_coldload_bars_by_tf_s{}`.

Consumed by: `core/config_loader.py:162` → `min_coldload_bars_from_cfg()` → UDS `build_uds_from_config()`.

---

## 13. Policy: Day Anchor Offsets

**SSOT**: `config.json:149-153`.

| Ключ | Значення | Секунди | Offset |
|------|----------|---------|--------|
| `day_anchor_offset_s` | 82800 | 23h | Primary (FX winter session) |
| `day_anchor_offset_s_alt` | 79200 | 22h | Alt (FX summer?) |
| `day_anchor_offset_s_alt2` | 68400 | 19h | Alt2 |
| `day_anchor_offset_s_d1` | 75600 | 21h | D1 primary |
| `day_anchor_offset_s_d1_alt` | 79200 | 22h | D1 alt |

**Consumers**:

- UDS `build_uds_from_config()` → передає anchor_offset до RedisSnapshot та SSOT JSONL.
- server.py HTF anchor validation: `_HTF_ALLOWED_REMAINDERS_MS` at `server.py:282` — **hardcoded** frozenset derived from anchor offsets.

---

## 14. Config validation

### 14.1 _validate_config()

**Файл**: `app/composition.py:83-216`.

| Категорія | Поля | Перевірка |
|-----------|------|-----------|
| Integer range (10 полів) | `connector_retry_base_s`, `connector_retry_max_s`, `connector_wake_ahead_s`, `history_*` (7 полів) | `require_int(key, min_val)` — raises ValueError |
| Int list | `tf_allowlist_s`, `broker_base_tfs_s` | Non-empty / allow-empty |
| Unique int list | `tf_allowlist_s`, `broker_base_tfs_s`, `redis_priming_tfs_s` | No duplicates |
| Unique str list | `symbols` | No duplicates |
| Calendar gate | `market_calendar_by_group`, `market_calendar_symbol_groups` | Dicts if enabled |
| Redis | host(str), port/db(int), ttl/tail dicts | If redis enabled |
| Cross-field | `connector_retry_max_s ≥ connector_retry_base_s` | Raises ConfigError |

### 14.2 Validation coverage gap

> **⚠ Знахідка F5**: `_validate_config()` виконується **тільки** в connector process (`build_connector()` path — `composition.py:219`).
>
> **Не валідуються** при старті:
>
> - M1 Poller
> - Tick Publisher
> - Tick Preview Worker
> - UI Server
>
> Це означає: якщо config.json має помилку лише в M1-специфічних полях — вона буде виявлена тільки після RuntimeError в m1_poller, а не при старті.

---

## 15. Config caching & hot-reload

### 15.1 UI Server — hot-reload через mtime

**Механізм**: `_load_cfg_cached()` at `server.py:469`.

```
_cfg_cache: {data: {}, mtime: None, next_check_ts: 0.0}
CFG_CACHE_CHECK_INTERVAL_S = 0.5

_load_cfg_cached():
  if now < next_check_ts → return cached
  next_check_ts = now + 0.5
  if os.path.getmtime(path) == cached_mtime → return cached
  else → re-read file, update cache
```

> Evidence: `server.py:269-270` (cache struct), `server.py:469-495` (function).

**Ефект**: UI server підхоплює зміни config.json протягом 0.5s без рестарту.

### 15.2 Інші процеси — read-once, no hot-reload

| Процес | Evidence |
|--------|----------|
| connector | `composition.py:219` — config read at `build_connector()`, потім використовується cached dict |
| m1_poller | `m1_poller.py:~1107` — `build_m1_poller()`, одноразове читання |
| tick_publisher | `tick_publisher_fxcm.py:~373` — read once at start |
| tick_preview_worker | `tick_preview_worker.py:~450` — read once at start |

### 15.3 config_loader — stateless

`load_system_config()` (`config_loader.py:48`) — **кожен виклик читає файл з нуля**. Жодного внутрішнього кешу. Це забезпечує простоту, але означає disk I/O на кожен виклик.

---

## 16. Client policy bridge (/api/config)

### 16.1 Server-side exposure

UI server повертає serverConfig через `/api/config`:

```
/api/config → {
  window_policy: {
    cold_start_bars_by_tf, warmup_bars_by_tf, max_bars_cap,
    tf_cap, scrollback_chunk_by_tf, redis_tail_by_tf
  },
  preview_tfs_s, tf_allowlist_s, symbols, ...
}
```

> Evidence: `server.py:~1000-1045` (config endpoint).

### 16.2 Client-side consumption (app.js)

app.js має **fallback** константи (`app.js:86-145`):

| Константа | app.js | /api/config overrides? |
|-----------|--------|------------------------|
| `COLD_START_BARS_BY_TF` | `{60:10080,...}` | **Так** — `getPolicyMap('cold_start_bars_by_tf')` |
| `MAX_BARS_CAP` | 20000 | **Так** — `getPolicyMaxBarsCap()` |
| `OVERLAY_POLL_INTERVAL_MS` | 1000 | **Ні** — pure client hardcode |
| `OVERLAY_MIN_TF_S` | 300 | **Ні** |
| `OVERLAY_MAX_TF_S` | 3600 | **Ні** |
| `OVERLAY_PREVIEW_TF_SET` | `{60, 180}` | **Ні** — duplicate preview_tf |
| `UPDATES_BASE_FINAL_MS` | 3000 | **Ні** |
| `UPDATES_BASE_PREVIEW_MS` | 1000 | **Ні** |
| `SCROLLBACK_TRIGGER_BARS_BASE` | 1000 | **Ні** |
| `SCROLLBACK_MIN_INTERVAL_MS` | 1200 | **Ні** |
| `FORWARD_GAP_MAX_BARS` | 3 | **Ні** |

> **⚠ Знахідка F6**: 7 client-side policy constants **не** керуються сервером. Зміна polling behavior вимагає правки app.js.

### 16.3 Policy fallback

app.js має `policyFallbackActive` flag — якщо `/api/config` не відповів, використовує hardcoded defaults. Evidence: `app.js:~90-120`.

---

## 17. Hardcoded policy audit

### 17.1 Server-side hardcodes (NOT from config)

| # | Файл:line | Що | Значення | Ризик |
|---|----------|-----|----------|-------|
| H1 | `server.py:293-305` | `_TF_CAP` per TF | dict 8 entries | MEDIUM — exposed via /api/config |
| H2 | `server.py:293` | `_MAX_BARS_CAP` | 20000 | LOW — exposed via /api/config |
| H3 | `server.py:159-172` | `_WARMUP_BARS_BY_TF` | Defaults, overridden from bootstrap config | LOW |
| H4 | `server.py:174-180` | `_COLD_START_BARS_BY_TF` | Defaults, overridden from bootstrap config | LOW |
| H5 | `server.py:181` | `_WARMUP_TF_PRIORITY` | `[300,3600,900,14400,86400,1800,60,180]` | LOW — order only |
| H6 | `server.py:182` | `_SHOW_IMMEDIATELY_TFS` | `[14400, 86400]` | LOW |
| H7 | `server.py:367-372` | `scrollback_chunk_by_tf` | `{300-3600: 1000}` | LOW — exposed via /api/config |
| H8 | `server.py:282` | `_HTF_ALLOWED_REMAINDERS_MS` | frozenset derived from anchor | MEDIUM — не оновиться при зміні anchor |
| H9 | `uds.py:43` | `REDIS_SOCKET_TIMEOUT_S` | 0.4s | LOW |
| H10 | `uds.py:44` | `PREVIEW_CURR_TTL_S` | **120s** | **HIGH** — config=1800s |
| H11 | `uds.py:45-46` | `PREVIEW_TAIL/UPDATES_RETAIN` | 2000 | LOW |
| H12 | `uds.py:94` | `UPDATES_REDIS_RETAIN_DEFAULT` | 2000 | LOW |
| H13 | `m1_poller.py:~1007` | `_PRIME_READY_TTL_S` | 21600 (6h) | LOW |
| H14 | `app/main.py:74-78` | `_BACKOFF_CFG` restart policies | `{"critical":(10,300,5), "non_critical":(5,120,10), "essential":(5,120,10)}` | LOW |

### 17.2 Client-side hardcodes

| # | Файл | Що | Значення |
|---|------|-----|----------|
| C1 | `app.js:~86` | `OVERLAY_POLL_INTERVAL_MS` | 1000 |
| C2 | `app.js:~87` | `OVERLAY_MIN_TF_S` | 300 |
| C3 | `app.js:~88` | `OVERLAY_MAX_TF_S` | 3600 |
| C4 | `app.js:~89` | `OVERLAY_PREVIEW_TF_SET` | {60, 180} |
| C5 | `app.js:~113` | `UPDATES_BASE_FINAL_MS` | 3000 |
| C6 | `app.js:~114` | `UPDATES_BASE_PREVIEW_MS` | 1000 |
| C7 | `app.js:~121` | `SCROLLBACK_TRIGGER_BARS_BASE` | 1000 |

---

## 18. Policy SSOT compliance matrix

### 18.1 Повна SSOT відповідність (config.json → runtime)

| Policy area | SSOT key | Де читається | Status |
|-------------|----------|-------------|--------|
| Symbols | `symbols[]` | All processes | ✅ SSOT |
| TF allowlist | `tf_allowlist_s[]` | UDS, server, ingest | ⚠ **Дублюється** в `buckets.py:10` |
| Preview TFs | `preview_tick_tfs_s[]` | UDS, tick_preview | ⚠ **Дублюється** в exit gate |
| Polling M1 | `m1_poller{}` | m1_poller | ✅ SSOT |
| Polling D1 | Root-level keys | connector | ✅ SSOT |
| Redis TTL | `redis.ttl_by_tf_s{}` | UDS → redis_snapshot | ✅ SSOT |
| Redis tail_n | `redis.tail_n_by_tf_s{}` | UDS → redis_snapshot | ✅ SSOT |
| Calendar | `market_calendar_*` | m1_poller, tick_preview | ✅ SSOT |
| Day anchor | `day_anchor_offset_s*` | UDS, server | ⚠ server `_HTF_ALLOWED_REMAINDERS_MS` hardcoded |
| Stitching | `ui_stitching_enabled` | server.py | ✅ SSOT |
| Preview TTL | `preview_curr_ttl_s` | tick_preview_worker + UDS (via hardcode 120s) | ❌ **3-way mismatch**: config=1800, UDS=120, worker_default=60 |
| Warmup bars | `bootstrap.ui_warmup_bars_by_tf` | server.py | ✅ SSOT (overrides hardcoded defaults) |
| Cold start | `bootstrap.ui_cold_start_bars_by_tf` | server.py | ✅ SSOT (overrides hardcoded defaults) |
| Limit cap | — | server.py `_TF_CAP` | ❌ **Not in config** (hardcoded) |
| Scrollback | — | server.py `scrollback_chunk_by_tf` | ❌ **Not in config** (hardcoded) |
| Client timing | — | app.js | ❌ **Not server-driven** |

### 18.2 Compliance score

- ✅ **SSOT compliant**: 8 / 15 policy areas
- ⚠ **Duplicated**: 3 areas (TF, preview TF, anchor)
- ❌ **Not in config / mismatch**: 4 areas (preview TTL, limit cap, scrollback, client timing)

---

## 19. Знахідки та ризики

### F1: Legacy config loaders (РИЗИК: низький)

**Факт**: `app/composition.py:21` та `aione_top/collectors.py:136` мають власні `load_config()` — не використовують `core.config_loader.load_system_config()`.

**Вплив**: Порушення SSOT loader pattern. Якщо `load_system_config()` додасть нормалізацію/валідацію — legacy loaders не отримають її.

**Рекомендація**: Міграція на `core.config_loader.load_system_config()`.

---

### F2: UDS config read-once (РИЗИК: низький)

**Факт**: UDS отримує config одноразово через `build_uds_from_config()` (`uds.py:~2036`). Немає hot-reload.

**Вплив**: Зміни в config.json (TTL, tail_n, TF allowlist) після bootstrap — невидимі для UDS layer. Потрібен рестарт процесу.

---

### F3: TF_ALLOWLIST hardcoded в buckets.py (РИЗИК: середній)

**Факт**: `core/buckets.py:10` містить `TF_ALLOWLIST = {60,180,300,...}` — **hardcoded duplicate** config.json `tf_allowlist_s`.

> Evidence: `core/buckets.py:10`.

**Вплив**: Якщо додати новий TF тільки в config.json — `tf_to_ms()` guard (`buckets.py:22`) відкине його з ValueError. Потрібно змінити обидва місця.

**Рекомендація**: `buckets.py` має читати allowlist з config або `TF_ALLOWLIST` має бути єдиною SSOT (і config.json посилається на неї).

---

### F4: PREVIEW_CURR_TTL_S mismatch (РИЗИК: високий)

**Факт**: **Тристороння невідповідність**:

| Джерело | Значення | Evidence |
|--------|----------|----------|
| config.json | **1800s** (30 min) | `config.json:71` |
| UDS hardcode | **120s** (2 min) | `uds.py:44` |
| tick_preview_worker default | **60s** (1 min) | `tick_preview_worker.py:114` (`cfg.get("preview_curr_ttl_s", 60)`) |

**Механіка**: `build_uds_from_config()` (`uds.py:~2008`) **не передає** `preview_curr_ttl_s` з config до UDS constructor — завжди використовується hardcode 120s. tick_preview_worker читає з config (1800s), але з default=60s як fallback.

**Вплив**: Preview bars у Redis мають TTL=120s (не 1800s як очікується за config). Якщо tick preview worker перестане публікувати >2 хвилини — preview зникне з Redis.

**Рекомендація**: UDS має читати `preview_curr_ttl_s` з config через `build_uds_from_config()`. tick_preview_worker default має бути вирівняний з config value (1800, не 60).

---

### F5: Validation coverage gap (РИЗИК: середній)

**Факт**: `_validate_config()` виконується тільки в connector process.

> Evidence: `app/composition.py:83` + `composition.py:219` (виклик).

**Вплив**: M1 Poller, Tick Publisher, Tick Preview Worker та UI Server стартують без validation. Помилка в M1-специфічних полях не ловиться до RuntimeError.

**Рекомендація**: Виокремити validation і виконувати в кожному процесі на старті.

---

### F6: Client policy not server-driven (РИЗИК: низький)

**Факт**: 7 policy constants у app.js не керуються сервером (overlay timing, updates polling, scrollback).

> Evidence: `app.js:86-145`.

**Вплив**: Зміна цих значень вимагає правки JavaScript файлу, а не config.json. Не відповідає Rule №4 (SSOT у файлах конфігу).

**Рекомендація**: Розширити `/api/config` response для передачі цих policy values.

---

### F7: broker_base_tfs_s fallback mismatch (РИЗИК: низький)

**Факт**: config.json `broker_base_tfs_s: [86400]`, але `composition.py:292` fallback = `[14400, 86400]` (includes H4).

> Evidence: `config.json:149-153` vs `composition.py:~292`.

**Вплив**: Якщо `broker_base_tfs_s` key відсутній у config — connector буде polling H4 з broker замість D1 only. Minor, оскільки key currently exists.

---

### Зведена таблиця знахідок

| ID | Опис | Ризик | Rule / Інваріант |
|----|------|-------|-----------------|
| F1 | Legacy config loaders | Низький | Rule №4 |
| F2 | UDS config read-once | Низький | — |
| F3 | TF_ALLOWLIST hardcoded в buckets.py | Середній | Rule №4 |
| F4 | PREVIEW_CURR_TTL_S 3-way mismatch (120/1800/60) | **Високий** | Rule №4, SSOT |
| F5 | Validation тільки в connector | Середній | Rule №5.1 |
| F6 | Client policy не server-driven | Низький | Rule №4 |
| F7 | broker_base_tfs_s fallback mismatch | Низький | Rule №4 |
