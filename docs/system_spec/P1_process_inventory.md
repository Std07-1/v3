# P1: Process Inventory — Повний реєстр процесів платформи v3

> **Документ**: code-first, кожен факт має evidence `(file:line)`.
> **Дата**: 2026-02-21
> **Scope**: усі OS-процеси, їх lifecycle, залежності, FXCM сесії, Redis використання, restart policy.

---

## Зміст

- [1. Архітектурна діаграма процесів](#1-архітектурна-діаграма-процесів)
- [2. Supervisor — `app/main.py`](#2-supervisor)
- [3. Connector (D1 Fetcher) — `app/main_connector.py`](#3-connector)
- [4. M1 Poller — `runtime/ingest/polling/m1_poller.py`](#4-m1-poller)
- [5. Tick Publisher — `runtime/ingest/tick_publisher_fxcm.py`](#5-tick-publisher)
- [6. Tick Preview Worker — `runtime/ingest/tick_preview_worker.py`](#6-tick-preview-worker)
- [7. UI Server — `ui_chart_v3/server.py`](#7-ui-server)
- [8. Зведена матриця процесів](#8-зведена-матриця-процесів)
- [9. FXCM сесії (зведення)](#9-fxcm-сесії)
- [10. Redis використання (зведення)](#10-redis-використання)
- [11. UDS інстанції (зведення)](#11-uds-інстанції)
- [12. Підтримуючі модулі](#12-підтримуючі-модулі)
- [13. Діаграма запуску та Bootstrap](#13-діаграма-запуску-та-bootstrap)

---

## 1. Архітектурна діаграма процесів

```
┌─────────────────────── SUPERVISOR (app/main.py) ───────────────────────┐
│  Режим: all|connector|ui|tick_preview|tick_publisher|m1_poller         │
│  PID management, restart with exponential backoff, AND-gate            │
│                                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────────┐  │
│  │  CONNECTOR   │  │  M1_POLLER   │  │TICK_PUBLISH│  │TICK_PREVIEW  │  │
│  │  (critical)  │  │  (critical)  │  │(non_crit)  │  │(non_crit)    │  │
│  │  D1 broker   │  │  M1 broker   │  │FXCM stream │  │tick→M1/M3    │  │
│  │  fetch       │  │  + cascade   │  │→Redis pub  │  │preview bars  │  │
│  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  └──────┬───────┘  │
│         │                 │                │                │          │
│         │   prime:ready   │ prime:ready:m1 │                │          │
│         └────────┬────────┘                │                │          │
│                  ▼                         │                │          │
│         ┌────────────────┐                 │                │          │
│         │  AND-gate wait │◄────────────────┘                │          │
│         └───────┬────────┘                                  │          │
│                 ▼                                           │          │
│         ┌──────────────┐                                    │          │
│         │  UI SERVER   │   читає /api/* через UDS (reader)  │          │
│         │  (essential) │                                    │          │
│         └──────────────┘                                    │          │
└────────────────────────────────────────────────────────────────────────┘

Потоки даних:
  FXCM History API ──► Connector ──► UDS (writer) ──► Disk JSONL + Redis Snap
  FXCM History API ──► M1 Poller ──► UDS (writer) ──► Disk + Redis + Updates Bus
  FXCM OFFERS table ──► Tick Publisher ──► Redis Pub/Sub channel
  Redis Pub/Sub ──► Tick Preview ──► UDS (writer) ──► Preview Ring + Updates Bus
  UDS (reader) ──► UI Server ──► HTTP API ──► Browser
```

---

## 2. Supervisor

**Файл**: [app/main.py](../app/main.py)
**Entry point**: `python -m app.main` (або `python app/main.py`)

### 2.1 Аргументи CLI

| Аргумент | Значення | Default | Evidence |
|----------|----------|---------|----------|
| `--mode` | `all\|connector\|ui\|tick_preview\|tick_publisher\|m1_poller` | `all` | [app/main.py](../app/main.py#L20-L30) |
| `--stdio` | `inherit\|pipe\|files\|null` | `inherit` | [app/main.py](../app/main.py#L30-L40) |

### 2.2 Категорії процесів

Визначено у `_PROCESS_CATEGORIES` ([app/main.py:L66-L72](../app/main.py#L66-L72)):

| Процес | Категорія | Backoff base | Backoff max | Max attempts |
|--------|-----------|-------------|-------------|-------------|
| `connector` | `critical` | 10s | 300s | 5 |
| `m1_poller` | `critical` | 10s | 300s | 5 |
| `tick_publisher` | `non_critical` | 5s | 120s | 10 |
| `tick_preview` | `non_critical` | 5s | 120s | 10 |
| `ui` | `essential` | 5s | 120s | 10 |

Конфігурація backoff: `_BACKOFF_CFG` ([app/main.py:L74-L78](../app/main.py#L74-L78)).
`_STABLE_RESET_S = 600` — лічильник перезапусків скидається після 10 хв стабільної роботи ([app/main.py:L79](../app/main.py#L79)).

### 2.3 Порядок запуску (mode=all)

`main()` ([app/main.py:L304](../app/main.py#L304)):

1. Запуск `connector` → subprocess
2. Запуск `tick_preview` → subprocess
3. Запуск `tick_publisher` → subprocess
4. Запуск `m1_poller` → subprocess
5. **AND-gate**: `_wait_for_prime_ready()` — чекає на обидва Redis ключі:
   - `prime:ready` (від connector) **ТА**
   - `prime:ready:m1` (від m1_poller)
6. Запуск `ui` → subprocess

Evidence: [app/main.py:L193-L280](../app/main.py#L193-L280) — `_wait_for_prime_ready()` AND-gate.
Evidence: [app/main.py:L304-L400](../app/main.py#L304-L400) — `main()` послідовний запуск.

### 2.4 Supervisor loop (restart policy)

([app/main.py:L400-L500](../app/main.py#L400-L500))

**Phase 1**: Виконує заплановані рестарти (scheduled restarts).
**Phase 2**: Перевіряє статус кожного дочірнього процесу:

- Якщо `critical` процес вичерпав спроби → зупиняє **ВСІ** процеси (fatal).
- Якщо `non_critical` процес вичерпав спроби → видаляє його з пулу (removed, інші працюють).
- `essential` — поведінка як `non_critical` при вичерпанні спроб.

**Start subprocess**: `_start_process()` ([app/main.py:L120-L195](../app/main.py#L120-L195)):

- Будує команду `[sys.executable, "-m", module_name]`
- Налаштовує stdio відповідно до `--stdio` режиму
- `files` режим → записує stdout/stderr у `logs/{process_name}_stdout.log` / `_stderr.log`

### 2.5 Graceful shutdown

`_terminate()` ([app/main.py:L250-L290](../app/main.py#L250-L290)):

- Надсилає `SIGINT` (або `terminate()` на Windows)
- 10s таймаут → `kill()`

### 2.6 Інваріанти

- **Supervisor не читає/пише ринкові дані**. Тільки менеджмент процесів.
- **AND-gate**: UI НЕ запускається поки обидва priming-джерела не готові.
- `critical` exhausted → повна зупинка (**safety rail**).

---

## 3. Connector (D1 Fetcher)

**Файл entry**: [app/main_connector.py](../app/main_connector.py)
**Composition**: [app/composition.py](../app/composition.py)
**Engine**: [runtime/ingest/polling/engine_b.py](../runtime/ingest/polling/engine_b.py) → `PollingConnectorB`, `MultiSymbolRunner`
**Module**: `python -m app.main_connector`

### 3.1 Що робить

Забирає **D1 (86400s) бари** з FXCM History API для всіх символів.
Після ADR-0002: **тільки D1**, всі M5-H4 тепер через M1 Poller → DeriveEngine.

Evidence: [engine_b.py:L684-L688](../runtime/ingest/polling/engine_b.py#L684-L688) — коментар:
> `M5 polling/derive/backfill/recover видалено (ADR-0002 Phase 5+cleanup)`
> `Весь M5 pipeline тепер через m1_poller + DeriveEngine.`

### 3.2 Lifecycle

```
main_connector.main()                          [main_connector.py:L172]
  │
  ├─ init_redis_snapshot()                     [main_connector.py:L180]
  ├─ _build_with_retry()                       [main_connector.py:L130]
  │     └─ build_connector(config_path)        [composition.py:L217]
  │           ├─ FxcmHistoryProvider            (1 FXCM session)
  │           ├─ per-symbol PollingConnectorB   [engine_b.py:L178]
  │           └─ MultiSymbolRunner              [engine_b.py:L691]
  │
  └─ run_with_shutdown(runner.run_forever)      [lifecycle.py:L8]
        └─ MultiSymbolRunner.run_forever()      [engine_b.py:L991]
              │
              ├─ per-engine bootstrap_and_warmup()
              │     ├─ Phase 1: disk bootstrap  [engine_b.py:L303]
              │     ├─ Phase 2: Redis priming   [engine_b.py:L366]
              │     ├─ Phase 3: prime_ready      [engine_b.py:L316]
              │     └─ Phase 4: cold-start D1   [engine_b.py:L476]
              │
              ├─ _log_bootstrap_summary()
              ├─ _set_prime_ready_global()       → Redis key "prime:ready"
              │
              └─ LOOP:
                    ├─ circuit breaker check
                    ├─ sleep_to_next_minute(safety_delay_s)
                    ├─ _log_calendar_closed_if_needed()
                    ├─ per-engine poll_iteration()
                    │     └─ _fetch_base_from_broker_on_close()
                    └─ _drain_history_errors()
```

### 3.3 FXCM сесія

**Одна** спільна `FxcmHistoryProvider` сесія на всі символи.
Створюється: [composition.py:L250-L280](../app/composition.py#L250-L280).
Credentials: ENV пріоритет → config.json fallback ([composition.py:L230-L250](../app/composition.py#L230-L250)).

### 3.4 UDS інстанція

Кожен `PollingConnectorB` створює свій `UDS(role="writer", writer_components=True)` у `__init__` ([engine_b.py:L260-L265](../runtime/ingest/polling/engine_b.py#L260-L265)).

> **Примітка**: UDS створюється всередині `PollingConnectorB.__init__()`, а не у `composition.py`. Composition layer не знає про UDS — він будує лише broker provider та per-symbol engines.
Writer components: `JsonlAppender` + `RedisSnapshotWriter`.

### 3.5 Redis використання

| Операція | Деталі |
|----------|--------|
| **Priming** (bootstrap) | Disk → Redis snapshot для D1 (tax tfs від config) |
| **prime:ready** | Публікація з payload v1 (TTL=21600s/6h) |
| **commit_final_bar** | Через UDS → Redis snapshot + Updates bus |
| **cache_state** | `set_cache_state()` — стан priming для status endpoint |

### 3.6 Calendar gate

Per-symbol `MarketCalendar` (створюється у `build_connector()`, [composition.py:L320-L400](../app/composition.py#L320-L400)).
Connector використовує calendar для:

- Визначення останньої торгової хвилини (`_last_trading_minute_open_ms`)
- Логування calendar state changes (open→closed, closed→open)
- Calendar-aware sleep при retry ([main_connector.py:L90-L120](../app/main_connector.py#L90-L120))

### 3.7 Fetch policy

`broker_base_fetch_on_close=True` — фетчить D1 бар коли bucket закривається:

- Обчислює `anchor_open_ms` через `last_trading_minute_open_ms()`
- Перевіряє деdup через `has_on_disk()`
- Фетчить 1 бар з `fetch_last_n_tf()`

Evidence: [engine_b.py:L620-L680](../runtime/ingest/polling/engine_b.py#L620-L680) — `_fetch_base_from_broker_on_close()`.

### 3.8 Circuit breaker

`MultiSymbolRunner` має circuit breaker для history помилок ([engine_b.py:L730-L850](../runtime/ingest/polling/engine_b.py#L730-L850)):

- `_classify_history_error()` — класифікує помилки (session_expired, auth_failed, rate_limited, network_timeout, etc.)
- `_history_circuit_fail_streak` (config) → якщо всі символи fail N раз → backoff
- Exponential backoff: `base_s * 2^(streak - threshold)`, обмежений `max_s`
- Error log throttling: summary кожні `history_still_failing_interval_s`

### 3.9 Config параметри (з composition.py)

| Параметр | Default | Джерело |
|----------|---------|---------|
| `broker_base_tfs_s` | `[86400]` | config.json |
| `broker_base_fetch_on_close` | `true` | config.json |
| `broker_base_max_tf_per_poll` | `0` (unlimited) | config.json |
| `broker_base_cold_start_enabled` | `true` | config.json |
| `broker_base_cold_start_counts` | `{86400: 30}` | config.json |
| `safety_delay_s` | `8` | config.json |
| `redis_priming_enabled` | `true` | config.json |
| `redis_priming_budget_s` | `30.0` | config.json |
| `day_anchor_offset_s` | `0` | config.json |

---

## 4. M1 Poller

**Файл**: [runtime/ingest/polling/m1_poller.py](../runtime/ingest/polling/m1_poller.py)
**Module**: `python -m runtime.ingest.polling.m1_poller`
**Build**: `build_m1_poller(config_path)` ([m1_poller.py:L1105](../runtime/ingest/polling/m1_poller.py#L1105))

### 4.1 Що робить

- Pollingує **M1 (60s)** бари з FXCM History API
- Derivує **M3 (180s)** через M1Buffer (legacy) або DeriveEngine
- Каскад через **DeriveEngine**: M1 → M3 → M5 → M15 → M30 → H1 → H4
- Live recover після downtime (ADR-0002)
- Stale detection (ADR-0002 P0.3)

### 4.2 Lifecycle

```
m1_poller main()                    [m1_poller.py:L1359]
  │
  ├─ pidfile guard (logs/m1_poller.pid)  [m1_poller.py:L1364]
  ├─ build_m1_poller(config_path)        [m1_poller.py:L1105]
  │     ├─ FxcmHistoryProvider           (окрема FXCM session!)
  │     ├─ UDS(role="writer", writer_components=True)
  │     ├─ DeriveEngine(symbols, anchor, calendars, commit_tfs_s=ALL)
  │     │     └─ register_symbol_uds(sym, shared_uds)
  │     └─ per-symbol M1SymbolPoller(uds, derive_engine, calendar, ...)
  │
  └─ run_with_shutdown(runner.run_forever)
        └─ M1PollerRunner.run_forever()   [m1_poller.py:L1035]
              │
              ├─ _bootstrap_warmup()       [m1_poller.py:L859]
              │     ├─ 1. Redis priming (M1→H4, all TFs)
              │     ├─ 2. M1Buffer warmup (last 10 M1 from disk)
              │     ├─ 2b. DeriveEngine buffer warmup (M1+M5+M15+M30+H1 from disk)
              │     ├─ 2c. Cascade catchup (M1 → cascade → derive gaps)
              │     └─ 3. Tail catchup (fetch from broker: watermark → expected)
              │
              ├─ _publish_prime_ready()    → Redis key "prime:ready:m1"
              ├─ _try_connect()            (FXCM session open)
              │
              └─ LOOP:
                    ├─ _sleep_to_next_minute() [+safety_delay_s]
                    ├─ per-poller poll_once()
                    │     ├─ calendar state check
                    │     ├─ expected_closed_m1_calendar()
                    │     ├─ adaptive fetch_n (2 якщо caught-up, gap+1 якщо gap)
                    │     ├─ provider.fetch_last_n_m1()
                    │     ├─ watermark pre-filter
                    │     ├─ _ingest_bar(bar) → UDS.commit_final_bar()
                    │     │     └─ DeriveEngine.on_bar() → cascade M3→H4
                    │     ├─ _live_recover_check()
                    │     └─ _stale_check()
                    │
                    ├─ DeriveEngine.check_overdue_buckets() (кожні 60s)
                    ├─ _maybe_log_stats() (кожні log_interval_s=300s)
                    └─ _maybe_reconnect() (якщо всі полери fail)
```

### 4.3 FXCM сесія

**Окрема** `FxcmHistoryProvider` сесія — не спільна з Connector!
Створюється: [m1_poller.py:L1115-L1130](../runtime/ingest/polling/m1_poller.py#L1115-L1130).
Session lifecycle управляється `M1PollerRunner._try_connect()` / `shutdown()` ([m1_poller.py:L822-L855](../runtime/ingest/polling/m1_poller.py#L822-L855)).
Reconnect: якщо всі символи мають помилку → `_maybe_reconnect()` з cooldown=120s.

### 4.4 UDS інстанція

Одна спільна UDS(role="writer", writer_components=True) для всіх символів ([m1_poller.py:L1140-L1150](../runtime/ingest/polling/m1_poller.py#L1140-L1150)).
DeriveEngine per-symbol посилається на цю ж UDS через `register_symbol_uds()`.

### 4.5 DeriveEngine

**Файл**: [runtime/ingest/derive_engine.py](../runtime/ingest/derive_engine.py)

Каскад: M1 → M3(3) → M5(5) → M15(3) → M30(2) → H1(2) → H4(4).
Evidence: [derive_engine.py:L1-L20](../runtime/ingest/derive_engine.py#L1-L20) — docstring.

**Commit TFs**: Phase 5 = ALL derived TFs: `{180, 300, 900, 1800, 3600, 14400}`.
Evidence: `DEFAULT_COMMIT_TFS_S = set(DERIVE_ORDER)` ([derive_engine.py:L62](../runtime/ingest/derive_engine.py#L62)).

**Buffer sizes** ([derive_engine.py:L52-L59](../runtime/ingest/derive_engine.py#L52-L59)):

| Source TF | Buffer size | Coverage |
|-----------|-------------|---------|
| M1 (60) | 2000 | ~33h trading |
| M5 (300) | 500 | ~41h |
| M15 (900) | 200 | ~50h |
| M30 (1800) | 100 | ~50h |
| H1 (3600) | 50 | ~50h |

**Thread-safety**: per-symbol `threading.Lock` ([derive_engine.py:L115](../runtime/ingest/derive_engine.py#L115)).

**Overdue buckets**: timer-based safety net ([derive_engine.py:L200-L300](../runtime/ingest/derive_engine.py#L200-L300)):

- Викликається з M1 poller loop кожні 60s
- Сканує N попередніх bucket-ів per TF (lookback: M3=3, M5=6, M15=4, M30=4, H1=3, H4=3)
- Каскадує: overdue M5 → може побудувати M15 → M30 → H1 → H4

### 4.6 M1SymbolPoller

**Клас**: [m1_poller.py:L195](../runtime/ingest/polling/m1_poller.py#L195)

Per-symbol poller з:

- **Calendar gate**: `_is_market_open()`, `_check_calendar_state()` — логує зміни стану
- **Watermark tracking**: `_watermark_ms` — останній committed M1 `open_ms`
- **Adaptive fetch**: `_compute_fetch_n()` — 2 якщо caught-up, `gap+1` якщо gap (max 300)
- **Bar ingest**: `_ingest_bar()` ([m1_poller.py:L320-L405](../runtime/ingest/polling/m1_poller.py#L320-L405)):
  - Calendar-aware flat bar classification:
    - Flat + trading → маркер `trading_flat`, приймається
    - Flat + pause → **skip** (шум від брокера)
    - Non-flat + pause → маркер `calendar_pause_nonflat_anomaly`, WARNING
  - Commit → UDS → DeriveEngine.on_bar() або legacy M3 derive fallback
- **Live recover** (ADR-0002 P0.2): [m1_poller.py:L470-L600](../runtime/ingest/polling/m1_poller.py#L470-L600)
  - Threshold →budgeted batch fetch → degraded-but-loud gap_state
  - Cooldown між fetches, max total bars budget
- **Stale detection** (ADR-0002 P0.3): [m1_poller.py:L610-L650](../runtime/ingest/polling/m1_poller.py#L610-L650)
  - Throttled WARNING якщо ринок відкритий і давно не було нового M1
- **Tail catchup** (P0.1): [m1_poller.py:L680-L770](../runtime/ingest/polling/m1_poller.py#L680-L770)
  - Перед main loop: заповнює M1 від watermark до expected з брокера
  - Truncated → loud warning + gap_state

### 4.7 M1Buffer

**Клас**: [m1_poller.py:L80-L140](../runtime/ingest/polling/m1_poller.py#L80-L140)

- Stores up to 500 closed M1 bars per symbol (for legacy M3 derivation)
- `_derive_m3()`: builds M3 from 3 consecutive M1 bars
- `_is_flat()`: `O==H==L==C` with `volume <= flat_bar_max_volume` (default 4)

### 4.8 Redis використання

| Операція | Деталі |
|----------|--------|
| **Priming** (bootstrap) | Disk → Redis для M1-H4 (_PRIME_TFS = 60,180,300,900,1800,3600,14400). **D1 (86400) НЕ входить** — D1 priming це відповідальність Connector. Evidence: [m1_poller.py:L1246](../runtime/ingest/polling/m1_poller.py#L1246) |
| **prime:ready:m1** | v1 payload з symbols/tfs, TTL=21600s |
| **commit_final_bar** | Через UDS → Redis snapshot + Updates bus (Redis pub/sub) |
| **gap_state** | `set_gap_state()` — стан live-recover gap для UI/status |

### 4.9 Pidfile guard

`logs/m1_poller.pid` — запобігає дублюванню інстанцій ([m1_poller.py:L1350](../runtime/ingest/polling/m1_poller.py#L1350)).

### 4.10 Config параметри (з build_m1_poller)

| Параметр | Default | Evidence |
|----------|---------|----------|
| `m1_poller.enabled` | `false` | [m1_poller.py:L1115](../runtime/ingest/polling/m1_poller.py#L1115) |
| `m1_poller.safety_delay_s` | `8` | [m1_poller.py:L1170](../runtime/ingest/polling/m1_poller.py#L1170) |
| `m1_poller.log_interval_s` | `300` | [m1_poller.py:L1175](../runtime/ingest/polling/m1_poller.py#L1175) |
| `m1_poller.flat_bar_max_volume` | `4` | [m1_poller.py:L1200](../runtime/ingest/polling/m1_poller.py#L1200) |
| `m1_poller.redis_tail_n_by_tf_s` | `{60:300,...}` | [m1_poller.py:L1160](../runtime/ingest/polling/m1_poller.py#L1160) |
| `m1_poller.live_recover_*` | various | [m1_poller.py:L1220](../runtime/ingest/polling/m1_poller.py#L1220) |
| `m1_poller.stale_s` | `600` | [m1_poller.py:L1230](../runtime/ingest/polling/m1_poller.py#L1230) |
| `bootstrap.derive_warmup_bars_by_tf` | `{60:300,...}` | [m1_poller.py:L1250](../runtime/ingest/polling/m1_poller.py#L1250) |
| `bootstrap.cascade_catchup_m1_bars` | `1440` (~24h) | [m1_poller.py:L1260](../runtime/ingest/polling/m1_poller.py#L1260) |

---

## 5. Tick Publisher

**Файл**: [runtime/ingest/tick_publisher_fxcm.py](../runtime/ingest/tick_publisher_fxcm.py)
**Module**: `python -m runtime.ingest.tick_publisher_fxcm`

### 5.1 Що робить

Підписується на FXCM OFFERS table (real-time price stream), публікує тіки через Redis Pub/Sub.

### 5.2 Lifecycle

```
tick_publisher.main()                  [tick_publisher_fxcm.py:L363]
  │
  ├─ load_env_secrets()
  ├─ _parse_stream_cfg(config_path)    [tick_publisher_fxcm.py:L50]
  │     └─ reads tick_stream_* config params
  ├─ check tick_stream_enabled
  ├─ Redis client connect
  │
  └─ FxcmTickPublisher.run_forever()   [tick_publisher_fxcm.py:L293]
        └─ LOOP (retry on error with 5s sleep):
              └─ _run_once()           [tick_publisher_fxcm.py:L315]
                    ├─ ForexConnect() login
                    ├─ table_manager → wait tables loaded (30s timeout)
                    ├─ Subscribe OFFERS table (UPDATE + INSERT)
                    │     └─ _OffersListener → _handle_row()
                    │           ├─ normalize_symbol()
                    │           ├─ _pick_price(mode, bid, ask, mid)
                    │           ├─ to_ms(tick timestamp)
                    │           ├─ out-of-order check → drop
                    │           ├─ min_interval_ms throttle → skip
                    │           └─ publish to Redis channel + set tick:last:{symbol}
                    └─ sleep(1.0) loop until stop_requested
```

### 5.3 FXCM сесія

**Окрема** `ForexConnect()` сесія — третя незалежна від Connector та M1 Poller.
Створюється в `_run_once()` ([tick_publisher_fxcm.py:L315-L360](../runtime/ingest/tick_publisher_fxcm.py#L315-L360)).
Підписується не на History API, а на OFFERS table listener (real-time quotes).

### 5.4 Tick payload (tick_v1)

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "bid": 2650.50,
  "ask": 2651.20,
  "mid": 2650.85,
  "tick_ts_ms": 1740100000000,
  "src": "fxcm",
  "seq": 42
}
```

Evidence: [tick_publisher_fxcm.py:L230-L250](../runtime/ingest/tick_publisher_fxcm.py#L230-L250).

Wallclock fallback: якщо FXCM не надає `tick_ts` → `int(time.time() * 1000)`, src="fxcm_wallclock".
Stats кожні 60s з wallclock ratio warning якщо >10%.

### 5.5 Redis використання

| Операція | Деталі |
|----------|--------|
| **Publish** | `redis.publish(channel, tick_json)` — Redis Pub/Sub |
| **tick:last:{symbol}** | `redis.setex(key, ttl, tick_json)` — last tick з TTL |

### 5.6 Guards / Safety

- Out-of-order тіки → drop + counter `ticks_dropped_out_of_order`
- `min_interval_ms` throttle → skip + counter `ticks_throttled_total`
- Symbol normalization через aliases → unknown → drop + counter `ticks_dropped_symbol`
- No price → drop + counter `ticks_dropped_price`

### 5.7 Config параметри

| Параметр | Default | Evidence |
|----------|---------|----------|
| `tick_stream_enabled` | `false` | [tick_publisher_fxcm.py:L50-L55](../runtime/ingest/tick_publisher_fxcm.py#L50-L55) |
| `tick_stream_symbols` | `[]` (all) | [tick_publisher_fxcm.py:L50-L55](../runtime/ingest/tick_publisher_fxcm.py#L50-L55) |
| `tick_stream_channel` | (config/ENV, без default) | resolved via `pick_tick_channel()` ([tick_common.py:L19](../runtime/ingest/tick_common.py#L19)): config.channels.price_tick → ENV FXCM_PRICE_TICK_CHANNEL → None |
| `tick_stream_min_interval_ms` | `200` | [tick_publisher_fxcm.py:L75](../runtime/ingest/tick_publisher_fxcm.py#L75) |
| `tick_stream_last_tick_ttl_s` | `30` | [tick_publisher_fxcm.py:L80](../runtime/ingest/tick_publisher_fxcm.py#L80) |
| `tick_stream_price_mode` | `mid` | [tick_publisher_fxcm.py:L85](../runtime/ingest/tick_publisher_fxcm.py#L85) |

### 5.8 Cleanup

`_cleanup()` ([tick_publisher_fxcm.py:L305-L320](../runtime/ingest/tick_publisher_fxcm.py#L305-L320)):

- Відписка від OFFERS listener
- FXCM logout

---

## 6. Tick Preview Worker

**Файл**: [runtime/ingest/tick_preview_worker.py](../runtime/ingest/tick_preview_worker.py)
**Module**: `python -m runtime.ingest.tick_preview_worker`

### 6.1 Що робить

Підписується на Redis Pub/Sub channel з тіками, агрегує їх у M1/M3 preview бари для UI.

### 6.2 Lifecycle

```
tick_preview_worker.main()              [tick_preview_worker.py:L442]
  │
  ├─ _parse_preview_cfg(config_path)    [tick_preview_worker.py:L60]
  ├─ check preview_tick_enabled
  ├─ build UDS(role="writer", writer_components=False)
  ├─ build MarketCalendar per symbol
  │
  └─ TickPreviewWorker(uds, calendars, ...).run_forever(redis_client)
        └─ LOOP (reconnect on pubsub error with 1s sleep):
              ├─ redis_client.pubsub().subscribe(channel)
              └─ for msg in pubsub.listen():
                    └─ on_tick(payload)
                          ├─ validate tick_v1 schema
                          ├─ normalize_symbol()
                          ├─ calendar gate (skip if market closed)
                          ├─ per (symbol, tf_s) TickAggregator.update()
                          │     ├─ promoted_bar? → UDS.publish_promoted_bar()
                          │     └─ preview_bar? → UDS.publish_preview_bar()
                          ├─ M1→M3 derivation (_M1toM3Buffer)
                          │     └─ promoted M3 → UDS.publish_promoted_bar()
                          └─ auto_promote_m1 → UDS.commit_final_bar (tick_promoted)
```

### 6.3 FXCM сесія

**Жодної**. Tick Preview Worker НЕ підключається до FXCM. Він читає тіки з Redis Pub/Sub channel, куди публікує Tick Publisher.

### 6.4 UDS інстанція

UDS(role="writer", writer_components=**False**) — без JsonlAppender та RedisSnapshotWriter.
Preview Worker пише тільки через preview API — `publish_preview_bar()`, `publish_promoted_bar()`.
**Не пише на диск** (JSONL) напряму.

Evidence: [tick_preview_worker.py:L487](../runtime/ingest/tick_preview_worker.py#L487) — `build_uds_from_config(..., writer_components=False)`.

> **Примітка**: `writer_components=False` означає UDS отримує writer role (може викликати publish_preview_bar тощо), але без disk/Redis write інфраструктури (без JsonlAppender, без RedisSnapshotWriter). Preview Worker пише тільки в RAM + Redis preview ring.

### 6.5 TickAggregator

**Файл**: [runtime/ingest/tick_agg.py](../runtime/ingest/tick_agg.py)

- Агрегує тіки у preview бари per (symbol, tf_s)
- `auto_promote=True`: при rollover (перший тік нового бакету) → завершений бар попереднього бакету як `complete=True, src="tick_promoted"`
- UI бачить "миттєвий final" до приходу справжнього History final від M1 Poller

Evidence: [tick_agg.py:L45-L55](../runtime/ingest/tick_agg.py#L45-L55).

### 6.6 _M1toM3Buffer

Derives M3 preview from accumulated M1 preview bars (keeps last 6 M1 per symbol).
Evidence: [tick_preview_worker.py:L120-L155](../runtime/ingest/tick_preview_worker.py#L120-L155).

### 6.7 Redis використання

| Операція | Деталі |
|----------|--------|
| **Subscribe** | `redis.pubsub().subscribe(channel)` — читає тіки |
| **Preview publish** | Через UDS → Redis preview ring (publish_preview_bar) |
| **Promoted publish** | Через UDS → Redis preview event (publish_promoted_bar) |

### 6.8 Config параметри

| Параметр | Default | Evidence |
|----------|---------|----------|
| `preview_tick_enabled` | `false` | [tick_preview_worker.py:L101-L150](../runtime/ingest/tick_preview_worker.py#L101-L150) |
| `preview_tick_tfs_s` | `[60, 180]` | [tick_preview_worker.py:L101-L150](../runtime/ingest/tick_preview_worker.py#L101-L150) |
| `preview_tick_publish_min_interval_ms` | `250` | [tick_preview_worker.py:L101-L150](../runtime/ingest/tick_preview_worker.py#L101-L150) |
| `preview_tick_curr_ttl_s` | `60` | [tick_preview_worker.py:L101-L150](../runtime/ingest/tick_preview_worker.py#L101-L150) |
| `tick_auto_promote_m1` | `false` | [tick_preview_worker.py:L101-L150](../runtime/ingest/tick_preview_worker.py#L101-L150) |
| `tick_stream_channel` | (same as publisher, config/ENV) | resolved via `pick_tick_channel()` — shared config path |

---

## 7. UI Server

**Файл entry**: [ui_chart_v3/**main**.py](../ui_chart_v3/__main__.py) → [ui_chart_v3/server.py](../ui_chart_v3/server.py)
**Module**: `python -m ui_chart_v3`

### 7.1 Що робить

HTTP API сервер для свічкового UI. Віддає статичні файли + JSON API endpoints.

### 7.2 Lifecycle

```
ui_chart_v3.__main__                    [__main__.py:L1]
  └─ server.main()                      [server.py:L1786]
        ├─ parse args (--data-root, --host, --port, --static-root, --config)
        ├─ UDS = build_uds_from_config(role="reader")
        ├─ _bootstrap_warmup()
        ├─ _policy_sanity_issues()
        │
        └─ ThreadingHTTPServer (127.0.0.1:8089)
              └─ Handler (per request)
                    ├─ /               → static files (index.html)
                    ├─ /api/config     → window policy, TF allowlist, debug
                    ├─ /api/status     → UDS snapshot status
                    ├─ /api/symbols    → list symbols (data_root dirs)
                    ├─ /api/gaps       → tail audit reports
                    ├─ /api/updates    → incremental bar events
                    ├─ /api/bars       → history bars (final + preview overlay)
                    └─ /api/latest     → same as bars (shortcut)
```

### 7.3 FXCM сесія

**Жодної**. UI Server НЕ підключається до FXCM.

### 7.4 UDS інстанція

UDS(role="**reader**") — без writer components.
Evidence: [server.py:L1810-L1820](../ui_chart_v3/server.py#L1810-L1820).

Читає:

- `read_window()` — історія барів (Redis snap → RAM → disk fallback)
- `read_updates()` — інкрементальні події з Updates bus
- `read_preview_window()` — preview бари для overlay
- `snapshot_status()` — стан для /api/status

RAM cache формула (reader): `max(symbols × TFs + 16, 128)` ≈ `max(13×8+16, 128) = 128` keys.
Writer отримує лише `ram_max_keys=8`.
Evidence: [uds.py:L2061-L2069](../runtime/store/uds.py#L2061-L2069).

### 7.5 API Endpoints

| Endpoint | Method | Params | Джерело даних | Evidence |
|----------|--------|--------|---------------|----------|
| `/api/config` | GET | — | config.json + UDS | [server.py:L1029](../ui_chart_v3/server.py#L1029) |
| `/api/status` | GET | — | UDS.snapshot_status() | [server.py:L1047](../ui_chart_v3/server.py#L1047) |
| `/api/symbols` | GET | — | data_root directory listing | [server.py:L1185](../ui_chart_v3/server.py#L1185) |
| `/api/gaps` | GET | — | reports/tail_audit/*.json | [server.py:L1190](../ui_chart_v3/server.py#L1190) |
| `/api/updates` | GET | symbol, tf_s, limit, since_seq, epoch, include_preview | UDS.read_updates() | [server.py:L1055](../ui_chart_v3/server.py#L1055) |
| `/api/bars` | GET | symbol, tf_s, limit, align, force_disk, prefer_redis, epoch, since_open_ms, to_open_ms | UDS.read_window() + preview overlay | [server.py:L1224](../ui_chart_v3/server.py#L1224) |
| `/api/latest` | GET | symbol, tf_s, limit, after_open_ms | = /api/bars shortcut | [server.py:L1224](../ui_chart_v3/server.py#L1224) |

### 7.6 Preview TF handling (/api/bars)

Для preview TF (M1=60, M3=180): [server.py:L1290-L1380](../ui_chart_v3/server.py#L1290-L1380):

1. **Історія**: `UDS.read_window(prefer_redis=True)` — Redis snap з фіналами від m1_poller
2. **Preview overlay**: `UDS.read_preview_window(include_current=True)` — поточний бар від тіків
3. **I3 final>preview guard**: якщо last_hist_open == curr_preview_open і `complete=True` → НЕ заміщує
4. **Stitching**: `_stitch_bars_previous_close()` якщо `ui_stitching_enabled=true` в config

### 7.7 Stitching

`_stitch_bars_previous_close()` ([server.py:L1230-L1270](../ui_chart_v3/server.py#L1230-L1270)):

- `bars[i].open = bars[i-1].close` (TV-like PREVIOUS_CLOSE)
- Коригує high/low якщо новий open вийшов за межі
- **Не змінює SSOT на диску**, тільки UI display
- Контролюється: `cfg["ui_stitching_enabled"]` (bool)

### 7.8 Contract guards

- `_normalize_bars_window_v1()` — валідує/нормалізує бари, drop невалідних
- `_normalize_update_events_window_v1()` — валідує events, drop невалідних
- `_contract_guard_warn_window()` / `_contract_guard_warn_updates()` — warnings for violations
- Evidence: server.py passim, called from /api/bars and /api/updates handlers

### 7.9 Server config

- Default host: `127.0.0.1`
- Default port: `8089`
- `ThreadingHTTPServer` — multi-threaded (thread per request)
- `server_version = "AiOne_v3_UI/0.1"`
- Client tracking: `aione_client_id` cookie

---

## 8. Зведена матриця процесів

| # | Процес | Module | Категорія | FXCM сесія | UDS role | Пише на диск | Redis |
|---|--------|--------|-----------|-----------|----------|-------------|-------|
| 1 | **Supervisor** | `app.main` | — | Ні | Ні | Ні | Читає prime:ready |
| 2 | **Connector** | `app.main_connector` | critical | Так (shared, 1 сесія) | writer (per-sym) | Так (JSONL) | Snap + Updates + Prime |
| 3 | **M1 Poller** | `runtime.ingest.polling.m1_poller` | critical | Так (окрема, 1 сесія) | writer (shared) | Так (JSONL) | Snap + Updates + Prime |
| 4 | **Tick Publisher** | `runtime.ingest.tick_publisher_fxcm` | non_critical | Так (окрема, OFFERS) | Ні | Ні | Pub/Sub + tick:last |
| 5 | **Tick Preview** | `runtime.ingest.tick_preview_worker` | non_critical | Ні | writer (no disk) | Ні | Subscribe + Preview ring |
| 6 | **UI Server** | `ui_chart_v3` | essential | Ні | reader | Ні | Reads snap + updates |

---

## 9. FXCM сесії (зведення)

**Всього 3 незалежні FXCM сесії** одночасно (mode=all):

| Процес | API | Спосіб підключення | Evidence |
|--------|-----|-------------------|----------|
| Connector | History API | `FxcmHistoryProvider.__enter__()` | [composition.py:L270](../app/composition.py#L270) |
| M1 Poller | History API (M1 only) | `FxcmHistoryProvider.__enter__()` | [m1_poller.py:L1115](../runtime/ingest/polling/m1_poller.py#L1115) |
| Tick Publisher | OFFERS table (real-time) | `ForexConnect().login()` | [tick_publisher_fxcm.py:L330](../runtime/ingest/tick_publisher_fxcm.py#L330) |

**Ризик**: 3 паралельні login-сесії на один Demo акаунт. FXCM Demo дозволяє кілька, але Production може мати обмеження.

---

## 10. Redis використання (зведення)

| Key pattern | Writer | Reader | TTL | Тип |
|-------------|--------|--------|-----|-----|
| `v3_local:prime:ready` | Connector | Supervisor, UI | 21600s | STRING (JSON) |
| `v3_local:prime:ready:m1` | M1 Poller | Supervisor | 21600s | STRING (JSON) |
| `v3_local:snap:{symbol}:{tf_s}` | Connector, M1 Poller (via UDS) | UI (via UDS) | — | LIST (bar JSONs) |
| `v3_local:updates:{symbol}:{tf_s}` | Connector, M1 Poller (via UDS) | UI (via UDS) | — | LIST (event JSONs) |
| `v3_local:preview:curr:{symbol}:{tf_s}` | Tick Preview (via UDS) | UI (via UDS) | preview_curr_ttl_s | STRING (bar JSON) |
| `v3_local:preview:tail:{symbol}:{tf_s}` | Tick Preview (via UDS) | UI (via UDS) | — | LIST (bar JSONs) |
| `v3_local:preview:updates:{symbol}:{tf_s}` | Tick Preview (via UDS) | UI (via UDS) | — | LIST (event JSONs) |
| `v3_local:tick:last:{symbol}` | Tick Publisher | (diagnostic) | last_tick_ttl_s (30s) | STRING (tick JSON) |
| `v3_local:ticks` (channel) | Tick Publisher | Tick Preview | — | PUB/SUB |
| `v3_local:cache_state` | Connector (via UDS) | UI/status | — | STRING (JSON) |
| `v3_local:gap_state` | M1 Poller (via UDS) | UI/status | — | STRING (JSON) |

---

## 11. UDS інстанції (зведення)

| Процес | Role | writer_components | Layers | Кількість |
|--------|------|------------------|--------|-----------|
| Connector | writer | True | Disk(JSONL)+Redis(snap)+RAM+Updates | per-symbol (13) |
| M1 Poller | writer | True | Disk(JSONL)+Redis(snap)+RAM+Updates | 1 shared |
| Tick Preview | writer | **False** | Redis(preview)+RAM | 1 |
| UI Server | **reader** | — | Redis(read)+RAM+Disk(fallback) | 1 |

**Ризик**: Connector створює 13 окремих UDS інстанцій (по одній на символ через `PollingConnectorB.__init__`), а M1 Poller — 1 спільну. Це різна архітектура. Можливий race між writers якщо обидва пишуть одночасно до одного символу+TF (на практиці: Connector пише тільки D1, M1 Poller пише M1-H4, тому конфлікту немає).

---

## 12. Підтримуючі модулі

### 12.1 Core layer (pure, без I/O)

| Модуль | Файл | Опис |
|--------|------|------|
| `CandleBar` | [core/model/bars.py](../core/model/bars.py) | Canonical bar dataclass |
| `bucket_start_ms` / `tf_to_ms` | [core/buckets.py](../core/buckets.py) | Bucket geometry (єдина правда) |
| `derive_bar` / `derive_triggers` / `GenericBuffer` | [core/derive.py](../core/derive.py) | Pure derivation logic |
| `DERIVE_CHAIN` / `DERIVE_ORDER` / `DERIVE_SOURCE` | [core/derive.py](../core/derive.py) | Cascade graph definition |
| `tf_allowlist_from_cfg` / `preview_tf_allowlist_from_cfg` | [core/config_loader.py](../core/config_loader.py) | Config parsing |
| `time_geom` | [core/time_geom.py](../core/time_geom.py) | Time geometry helpers |

### 12.2 Runtime store layer

| Модуль | Файл | Опис |
|--------|------|------|
| `UnifiedDataStore` | [runtime/store/uds.py](../runtime/store/uds.py) | Central data orchestrator (2294 LOC) |
| `DiskLayer` | [runtime/store/layers/disk_layer.py](../runtime/store/layers/disk_layer.py) | JSONL read |
| `RamLayer` | [runtime/store/layers/ram_layer.py](../runtime/store/layers/ram_layer.py) | In-memory LRU cache |
| `RedisLayer` | [runtime/store/layers/redis_layer.py](../runtime/store/layers/redis_layer.py) | Redis snapshot read |
| `JsonlAppender` | [runtime/store/ssot_jsonl.py](../runtime/store/ssot_jsonl.py) | SSOT disk write (append-only) |
| `RedisSnapshotWriter` | [runtime/store/redis_snapshot.py](../runtime/store/redis_snapshot.py) | Redis snapshot write (tail deque) |
| `redis_spec` | [runtime/store/redis_spec.py](../runtime/store/redis_spec.py) | Redis connection resolution |
| `redis_keys` | [runtime/store/redis_keys.py](../runtime/store/redis_keys.py) | Redis key naming |

### 12.3 Runtime ingest layer

| Модуль | Файл | Опис |
|--------|------|------|
| `DeriveEngine` | [runtime/ingest/derive_engine.py](../runtime/ingest/derive_engine.py) | Cascade derivation with I/O (434 LOC) |
| `PollingConnectorB` | [runtime/ingest/polling/engine_b.py](../runtime/ingest/polling/engine_b.py) | D1 polling engine (1043 LOC) |
| `MultiSymbolRunner` | [runtime/ingest/polling/engine_b.py](../runtime/ingest/polling/engine_b.py) | Multi-symbol orchestrator |
| `TickAggregator` | [runtime/ingest/tick_agg.py](../runtime/ingest/tick_agg.py) | Tick→bar aggregation (170 LOC) |
| `MarketCalendar` | [runtime/ingest/market_calendar.py](../runtime/ingest/market_calendar.py) | Trading hours/sessions |
| `FxcmHistoryProvider` | [runtime/ingest/broker/fxcm/provider.py](../runtime/ingest/broker/fxcm/provider.py) | FXCM History API wrapper |
| `fetch_policy` | [runtime/ingest/polling/fetch_policy.py](../runtime/ingest/polling/fetch_policy.py) | Expected bar calculations |
| `dedup` | [runtime/ingest/polling/dedup.py](../runtime/ingest/polling/dedup.py) | On-disk dedup helpers |

---

## 13. Діаграма запуску та Bootstrap

### 13.1 Повний запуск (mode=all)

```
t=0   Supervisor starts
      │
t=0   ├─ spawn Connector (critical)
      │    └─ init_redis_snapshot()
      │    └─ build_connector() → FXCM login
      │    └─ per-symbol bootstrap_and_warmup():
      │         ├─ disk bootstrap (load watermarks)
      │         ├─ Redis priming (disk → snap for D1)
      │         ├─ cold-start D1 from broker
      │         └─ set prime:ready (per-symbol + global)
      │
t≈0   ├─ spawn Tick Preview (non_critical)
      │    └─ subscribe Redis ticks channel
      │
t≈0   ├─ spawn Tick Publisher (non_critical)
      │    └─ FXCM login → subscribe OFFERS
      │
t≈0   ├─ spawn M1 Poller (critical)
      │    └─ pidfile guard
      │    └─ build_m1_poller() → FXCM login
      │    └─ bootstrap_warmup():
      │         ├─ Redis priming (disk → snap for M1-H4, all 7 TFs)
      │         ├─ M1Buffer warmup (last 10 M1 from disk)
      │         ├─ DeriveEngine buffer warmup (M1+M5+M15+M30+H1 from disk)
      │         ├─ Cascade catchup (1440 M1 → cascade → derive missing M3-H4)
      │         └─ Tail catchup (broker fetch: watermark → expected)
      │    └─ set prime:ready:m1
      │
t≈30s ├─ AND-gate: wait prime:ready AND prime:ready:m1
      │    (poll Redis every 2s, timeout configurable)
      │
t≈30s └─ spawn UI Server (essential)
           └─ build UDS(reader)
           └─ _bootstrap_warmup() (reader-side Redis/RAM fill)
           └─ HTTP server start on 127.0.0.1:8089
```

### 13.2 Bootstrap data flow

```
                    ┌──────────────┐
                    │  FXCM Broker │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐  ┌─────────┐  ┌──────────┐
         │Connect.│  │M1 Poller│  │Tick Pub. │
         │ cold   │  │ tail    │  │          │
         │ start  │  │ catchup │  │          │
         └───┬────┘  └────┬────┘  └──────────┘
             │            │
    ┌────────┴────────────┴─────────┐
    ▼                               ▼
┌────────┐                    ┌────────┐
│  Disk  │ ◄── priming ──►    │ Redis  │
│ (JSONL)│                    │ (Snap) │
└────────┘                    └───┬────┘
                                  │
                            ┌─────┴──────┐
                            │  UI Server │
                            │  (reader)  │
                            └────────────┘
```

### 13.3 Steady-state data flow

```
FXCM History ──► Connector ──poll D1──► UDS writer ──► Disk + Redis snap + Updates
FXCM History ──► M1 Poller ──poll M1──► UDS writer ──► Disk + Redis snap + Updates
                                   └──► DeriveEngine ──cascade──► M3-H4 ──► UDS writer
FXCM OFFERS ──► Tick Publisher ──► Redis PubSub ──► Tick Preview ──► UDS preview plane

UI Server ◄── /api/bars         ◄── UDS reader ◄── Redis snap + disk fallback
UI Server ◄── /api/updates      ◄── UDS reader ◄── Redis updates bus
UI Server ◄── /api/bars (M1/M3) ◄── Redis snap   + preview overlay
```

---

## Виявлені факти / примітки

1. **3 FXCM сесії** — потенційне обмеження для Production.
2. **Connector створює per-symbol UDS** (13 інстанцій), M1 Poller — 1 shared. Різний паттерн.
3. **DeriveEngine Phase 5 active** — всі derived TFs коммітяться (M3-H4). Engine_b M5 polling видалено.
4. **Tick Preview writer_components=False** — не пише на диск. Тільки preview ring через Redis.
5. **UI Server reader-only** — жодної бізнес-логіки, крім stitching (display-only transform). stitching не має впливати на реальні гепи, це потрібно перевірити.
6. **AND-gate critical** — UI не запускається без обох prime:ready сигналів.
7. **Pidfile guard** тільки у M1 Poller ([m1_poller.py:L1328](../runtime/ingest/polling/m1_poller.py#L1328) def, [L1364](../runtime/ingest/polling/m1_poller.py#L1364) call). Connector та інші — без pidfile.
8. **Bootstrap cascade catchup** (1440 M1 = 24h) — критичний для заповнення H4 після рестарту.
9. **Overdue buckets check** (кожні 60s) — safety net для DeriveEngine cascade misses.
10. **Домен-розділення TFs**: Connector пише **тільки D1**, M1 Poller пише **M1-H4**. Тому 13 per-symbol UDS у Connector і 1 shared UDS у M1 Poller не конфліктують (різні TF domains).
11. **`_PRIME_TFS` виключає D1** — m1_poller прогріває Redis тільки M1→H4. D1 Redis priming — відповідальність Connector.
12. **tick_stream_channel** не має hardcoded default — resolved через `pick_tick_channel()` з config.json або ENV. Якщо жодне не задано → `None` → process не запускається.
13. **Reader RAM** значно більше за Writer: reader = `max(n_sym × n_tf + 16, 128)`, writer = 8 keys.

> **Наступний документ**: P2: Data Flow — Потоки даних від брокера до UI.
