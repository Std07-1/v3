# P5: Контракти, Guards, Типи та Exit Gates

> **Документ**: code-first, кожен факт має evidence `(file:line)`.
> **Дата**: 2026-02-21
> **Scope**: JSON Schema контракти, canonical types (dataclass), SSOT константи, guards (109: G1–G109), exit gates (24), enforcement інваріантів.
> **Залежності**: P1 (Process Inventory), P2 (Data Flow), P3 (UDS/Store), P4 (API Surface).

---

## Зміст

- [1. Загальна картина](#1-загальна-картина)
- [2. JSON Schema контракти (4)](#2-json-schema-контракти)
- [3. Canonical types (dataclass)](#3-canonical-types)
- [4. SSOT константи та allowlists](#4-ssot-константи)
- [5. Guard taxonomy — core/](#5-guards-core)
- [6. Guard taxonomy — runtime/store/](#6-guards-runtimestore)
- [7. Guard taxonomy — runtime/ingest/](#7-guards-runtimeingest)
- [8. Guard taxonomy — app/](#8-guards-app)
- [9. Guard taxonomy — ui_chart_v3/](#9-guards-ui)
- [10. Enforcement інваріантів (I0–I6)](#10-enforcement-інваріантів)
- [11. Exit Gates framework](#11-exit-gates-framework)
- [12. Exit Gates inventory (24)](#12-exit-gates-inventory)
- [13. Loud vs Silent аналіз](#13-loud-vs-silent)
- [14. Знахідки та потенційні проблеми](#14-знахідки)

---

## 1. Загальна картина

```
┌───────────────────────────────────────────────────────────────┐
│                   Contract & Guard Architecture               │
│                                                               │
│  ┌─── core/contracts/public/marketdata_v1/ ───────────────┐   │
│  │  bar_v1.json    tick_v1.json                           │   │
│  │  window_v1.json updates_v1.json                        │   │
│  │  (JSON Schema 2020-12, additionalProperties: false)    │   │
│  └────────────────────────────────────────────────────────┘   │
│           │                                                   │
│  ┌─── core/model/ ───────────────┐                            │
│  │  CandleBar (frozen dataclass) │ ← canonical internal type  │
│  │  assert_invariants()          │ ← geometry guard           │
│  └───────────────────────────────┘                            │
│           │                                                   │
│  ┌─── runtime/store/ ─────────────────────────────────────┐   │
│  │  UDS: 17 guards (writer role, watermark, NoMix, etc.)  │   │
│  │  SSOT JSONL: 3 guards (finality, invariants, selftest) │   │
│  │  Redis Snapshot: 4 guards (geometry, throttled error)  │   │
│  │  Layers (Disk/RAM/Redis): 12 guards (filter/merge)     │   │
│  └────────────────────────────────────────────────────────┘   │
│           │                                                   │
│  ┌─── runtime/ingest/ ────────────────────────────────────┐   │
│  │  Tick Agg: 5 guards    Tick Preview: 13 guards         │   │
│  │  Derive Engine: 6 guards   M1 Poller: 10 guards        │   │
│  │  Polling Engine B: 6 guards  Calendar: 2 guards        │   │
│  └────────────────────────────────────────────────────────┘   │
│           │                                                   │
│  ┌─── ui_chart_v3/ ───────────────────────────────────────┐   │
│  │  Contract Guards: 5 (bar/event/meta/window/updates)    │   │
│  │  Policy Guards: 6 (TF/align/limit/overlay/anchor)      │   │
│  │  Normalization: 4 (bar/batch/events/flat filter)       │   │
│  └────────────────────────────────────────────────────────┘   │
│           │                                                   │
│  ┌─── tools/exit_gates/ ──────────────────────────────────┐   │
│  │  24 gates: Contract/Dependency/NoMix/Calendar/SLO      │   │
│  │  Runner: tools/run_exit_gates.py + manifest.json       │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

**Загалом**: 4 JSON Schema, 16 dataclass types, 109 runtime guards (G1–G109), 24 exit gates, 11 SSOT констант.

---

## 2. JSON Schema контракти

Усі 4 контракти — JSON Schema Draft 2020-12 з `additionalProperties: false`.

### 2.1 bar_v1

**Файл**: `core/contracts/public/marketdata_v1/bar_v1.json`
**Schema ID**: `marketdata.bar.v1`

| Поле | Тип | Required | Опис |
|------|-----|----------|------|
| `time` | integer | ✓ | Epoch seconds (LWC-compat: `open_time_ms // 1000`) |
| `open` | number | ✓ | Ціна відкриття |
| `high` | number | ✓ | Найвища ціна |
| `low` | number | ✓ | Найнижча ціна |
| `close` | number | ✓ | Ціна закриття |
| `volume` | number | ✓ | Обсяг |
| `open_time_ms` | integer | ✓ | Canonical epoch ms (основний ключ часу) |
| `close_time_ms` | integer∣null | ✓ | `open_time_ms + tf_s * 1000` (canonical) |
| `tf_s` | integer | ✓ | Timeframe у секундах |
| `src` | string | ✓ | `"history"`, `"derived"`, `"history_agg"`, `"preview_tick"`, `"tick_promoted"` |
| `complete` | boolean | ✓ | `true` = final, `false` = preview |
| `event_ts` | integer | — | Timestamp event створення |
| `last_price` | number | — | Остання ціна (HUD) |
| `last_tick_ts` | integer | — | Timestamp останнього тіка |

**Enforcers**:

- `server.py:561` → `_guard_bar_shape(bar)` — runtime guard для кожного bar у відповіді.
- `tools/exit_gates/gate_api_bars_meta.py` — exit gate.

### 2.2 tick_v1

**Файл**: `core/contracts/public/marketdata_v1/tick_v1.json`
**Schema ID**: `marketdata.tick.v1`

| Поле | Тип | Required | Опис |
|------|-----|----------|------|
| `v` | integer | ✓ | Версія (const 1) |
| `symbol` | string | ✓ | Символ |
| `bid` | number∣null | — | Ціна bid |
| `ask` | number∣null | — | Ціна ask |
| `mid` | number∣null | — | Середня ціна |
| `tick_ts_ms` | integer | ✓ | Timestamp тіка (epoch ms) |
| `src` | string | ✓ | Джерело (`"fxcm"`) |
| `seq` | integer | ✓ | Послідовний номер |

**Enforcers**:

- `tick_preview_worker.py:~242` → `_validate_tick_schema(payload)` — runtime guard на вході.
- `tools/exit_gates/gate_tick_preview_calendar.py` — exit gate.

### 2.3 updates_v1

**Файл**: `core/contracts/public/marketdata_v1/updates_v1.json`
**Schema ID**: `marketdata.updates.v1`

**Top-level** required: `ok` (const true), `symbol`, `tf_s`, `events[]`, `cursor_seq` (int≥0), `boot_id`.

**Event sub-schema** required: `key{symbol, tf_s, open_ms}`, `bar` ($ref bar_v1), `complete`, `source`; optional `event_ts`.

**Extension fields** (у JSON Schema): `disk_last_open_ms`, `bar_close_ms`, `ssot_write_ts_ms`, `api_seen_ts_ms`, `warnings[]`.

> Evidence: `updates_v1.json:1-115`.

**Enforcers**:

- `server.py:609` → `_guard_event_shape(ev)` — runtime guard.
- `server.py:895` → `_contract_guard_warn_updates()` — comprehensive contract check.
- `tools/exit_gates/gate_api_updates_contract.py` — exit gate.

### 2.4 window_v1

**Файл**: `core/contracts/public/marketdata_v1/window_v1.json`
**Schema ID**: `marketdata.window.v1`

**Два варіанти** (oneOf):

1. **window_response**: `ok`, `symbol`, `tf_s`, `bars[]` ($ref bar_v1), `boot_id`, `meta`, optional `warnings[]`.
2. **no_data_response**: `ok`, `bars[]` (maxItems=0), `note="no_data"`, `boot_id`, `meta`, optional `warnings[]`.

**Meta sub-schema**: required `source` (string), `redis_hit` (bool), `boot_id`; optional `redis_error_code`, `redis_ttl_s_left`, `redis_payload_ts_ms`, `redis_seq`, `redis_len`, `extensions`.

> Evidence: `window_v1.json:1-137`.

**Enforcers**:

- `server.py:650` → `_guard_meta_shape(meta)` — runtime guard.
- `server.py:861` → `_contract_guard_warn_window()` — comprehensive contract check.
- `tools/exit_gates/gate_api_bars_meta.py` — exit gate.

---

## 3. Canonical types (dataclass)

### 3.1 Core layer

| # | Файл:line | Клас | frozen | Ключові поля |
|---|----------|------|--------|-------------|
| T1 | `core/model/bars.py:9` | `CandleBar` | ✓ | symbol, tf_s, open_time_ms, close_time_ms, o, h, low, c, v, complete, src, extensions |

**Методи CandleBar**: `to_dict()` → canonical dict (без `close`/`open`; поля `o`, `h`, `low`, `c`, `v`); `key()` → `(symbol, tf_s, open_time_ms)`.

> Evidence: `core/model/bars.py:9-51`.

### 3.2 Runtime/store layer

| # | Файл:line | Клас | frozen | Ключові поля |
|---|----------|------|--------|-------------|
| T2 | `runtime/store/uds.py:217` | `WindowSpec` | ✓ | symbol, tf_s, limit, since_open_ms, to_open_ms, cold_load |
| T3 | `runtime/store/uds.py:227` | `ReadPolicy` | ✓ | force_disk, prefer_redis, disk_policy |
| T4 | `runtime/store/uds.py:241` | `WindowResult` | ✗ | bars_lwc, meta, warnings |
| T5 | `runtime/store/uds.py:249` | `UpdatesSpec` | ✓ | symbol, tf_s, since_seq, limit, include_preview |
| T6 | `runtime/store/uds.py:257` | `UpdatesResult` | ✗ | events, cursor_seq, disk_last_open_ms, bar_close_ms, ssot_write_ts_ms, api_seen_ts_ms, meta, warnings |
| T7 | `runtime/store/uds.py:269` | `CommitResult` | ✗ | ok, reason, ssot_written, redis_written, updates_published, warnings |
| T8 | `runtime/store/redis_spec.py:12` | `RedisSpec` | ✓ | host, port, db, namespace, source, cfg_*, mismatch, mismatch_fields |

### 3.3 Runtime/ingest layer

| # | Файл:line | Клас | frozen | Ключові поля |
|---|----------|------|--------|-------------|
| T9 | `runtime/ingest/tick_agg.py:17` | `_BucketState` | ✗ | open_ms, o, h, l, c, v, open_tick_ts_ms, last_tick_ts_ms |
| T10 | `runtime/ingest/tick_preview_worker.py:80` | `PreviewConfig` | ✓ | enabled, tfs, publish_min_interval_ms, curr_ttl_s, symbols, channel |
| T11 | `runtime/ingest/tick_publisher_fxcm.py:32` | `TickStreamConfig` | ✓ | symbol aliases, channel, price_mode |
| T12 | `runtime/ingest/market_calendar.py:35` | `MarketCalendar` | ✓ | enabled, weekend_close/open, daily_break, daily_breaks(tuple) |
| T13 | `runtime/ingest/polling/m1_poller.py:44` | `M1Buffer` | ✗ | max_keep, by_open_ms dict, sorted_keys list |

### 3.4 App layer

| # | Файл:line | Клас | frozen | Ключові поля |
|---|----------|------|--------|-------------|
| T14 | `env_profile.py:22` | `EnvLoadReport` | ✓ | path, loaded, keys_count |
| T15 | `app/main.py:54` | `ChildProcess` | ✗ | label, module, proc, stdout_handle, stderr_handle |
| T16 | `app/composition.py:14` | `ConfigError(Exception)` | — | stage attribute |

---

## 4. SSOT константи та allowlists

| # | Файл:line | Константа | Значення | SSOT для |
|---|----------|-----------|----------|----------|
| K1 | `core/buckets.py:10` | `TF_ALLOWLIST` | `{60, 180, 300, 900, 1800, 3600, 14400, 86400}` | Всі допустимі TF (включно preview) |
| K2 | `core/config_loader.py:82` | `DEFAULT_TF_ALLOWLIST` | `{300, 900, 1800, 3600, 14400, 86400}` | Final TFs (без preview 60/180) |
| K3 | `core/config_loader.py:83` | `DEFAULT_PREVIEW_TF_ALLOWLIST` | `{60, 180}` | Preview TFs |
| K4 | `core/config_loader.py:84` | `MAX_EVENTS_PER_RESPONSE` | `500` | Ліміт events в /api/updates |
| K5 | `core/derive.py:36` | `DERIVE_CHAIN` | `{60:[(180,3),(300,5)], 300:[(900,3)], ...}` | Каскад деривації TF |
| K6 | `core/derive.py:47` | `DERIVE_ORDER` | `[180, 300, 900, 1800, 3600, 14400]` | Порядок деривації |
| K7 | `core/derive.py:43` | `DERIVE_SOURCE` | `{180:(60,3), 300:(60,5), ...}` | target→(source, N) |
| K8 | `runtime/store/uds.py:41` | `SOURCE_ALLOWLIST` | `{"history","derived","history_agg",""}` | Допустимі джерела барів |
| K9 | `runtime/store/uds.py:42` | `FINAL_SOURCES` | `{"history","derived","history_agg"}` | Фінальні джерела (I3) |
| K10 | `runtime/store/layers/disk_layer.py:10` | `FINAL_SOURCES` | `{"history","derived","history_agg"}` | Дублікат K9 для disk layer |
| K11 | `runtime/store/ssot_jsonl.py:12` | `FINAL_SOURCES` | `{"history","derived","history_agg"}` | Дублікат K9 для SSOT JSONL |

**Знахідка**: K9, K10 і K11 — тройне дублювання `FINAL_SOURCES` між uds.py, disk_layer.py і ssot_jsonl.py. См. F1.

---

## 5. Guards — core/

| # | Файл:line | Функція | Що валідує | Реакція |
|---|----------|---------|------------|---------|
| G1 | `core/model/bars.py:55` | `assert_invariants(bar, anchor_offset_s)` | bucket alignment: `(open_ms - anchor*1000) % tf_ms == 0`; close_time == open + tf_ms; заборона `derived` для tf_s=60 | **LOUD** (raises `ValueError`) |
| G2 | `core/buckets.py:22` | `tf_to_ms(tf_s)` | `tf_s ∈ TF_ALLOWLIST` | **LOUD** (raises `ValueError`) |
| G3 | `core/derive.py:118` | `GenericBuffer.upsert(bar)` | `bar.tf_s == self._tf_s` | **LOUD** (raises `ValueError`) |

**Coverage**: G1 викликається з `ssot_jsonl.append()`, `core/derive.aggregate_bars()`, `m1_poller._derive_m3()`.

---

## 6. Guards — runtime/store/

### 6.1 UDS guards (`runtime/store/uds.py`)

| # | Line | Метод/Функція | Що валідує | Реакція |
|---|------|---------------|------------|---------|
| G4 | 1199 | `_ensure_writer_role(action)` | `role == "writer"` | **LOUD** (raises `RuntimeError("UDS_WRITE_FORBIDDEN")`) |
| G5 | 97 | `_watermark_drop_reason(open_ms, wm)` | `open_ms > watermark` | **LOUD** (log warning + OBS counter) |
| G6 | 627 | `commit_final_bar` — isinstance | `bar isinstance CandleBar` | **LOUD** (returns `CommitResult(False,"invalid_bar")`) |
| G7 | 630 | `commit_final_bar` — complete | `bar.complete == True` | **LOUD** (returns `CommitResult(False,"not_complete")`) |
| G8 | 638 | `commit_final_bar` — source | `bar.src ∈ FINAL_SOURCES` | **LOUD** (returns `CommitResult(False,"non_final_source")`) |
| G9 | 649 | `commit_final_bar` — watermark | `open_ms > watermark` | **LOUD** (returns `CommitResult(False,"duplicate"/"stale")` + log) |
| G10 | 722 | `publish_preview_bar` — complete | `bar.complete == False` | **LOUD** (`_set_preview_nomix_violation`) |
| G11 | 736 | `publish_preview_bar` — source | `bar.src ∉ FINAL_SOURCES` | **LOUD** (`_set_preview_nomix_violation`) |
| G12 | ~728 | `publish_preview_bar` — TF | `tf_s ∈ preview_tf_allowlist` | **LOUD** (skip + warning) |
| G13 | ~730 | `publish_preview_bar` — Redis | Redis layer exists | **LOUD** (skip + warning) |
| G14 | ~686 | `publish_promoted_bar` | src=="tick_promoted", complete, tf_s ∈ preview_allowlist | **LOUD** (skip + warning) |
| G15 | 579 | `read_updates` — MAX_EVENTS | `len(events) ≤ MAX_EVENTS_PER_RESPONSE` (500) | **LOUD** (`warnings.append("max_events_trimmed")`, L581) |
| G16 | ~N/A | `_ensure_sorted_dedup(bars)` | Sorted + dedup by open_ms; `_choose_better_bar` priority | **SILENT** (auto-fix) |
| G17 | ~N/A | `_mark_*()` family | degraded/redis_mismatch/prime_pending/broken/history_short/ram_short | **LOUD** (warnings + extensions в meta) |
| G18 | 1264 | `_disk_allowed(policy, reason)` | disk_policy enforcement: "never"→block, "bootstrap"→window, "explicit"→allow | **LOUD** (rate-limited warning) |
| G19 | ~N/A | `read_window` — TF check | `tf_s ∈ tf_allowlist` | **LOUD** (warning "tf_not_allowed") |
| G20 | ~N/A | `_set_preview_nomix_violation(reason)` | NoMix enforcement (I3) | **LOUD** (warning + permanent flag) |

**Selftest guards**:

- `selftest_writer_api()` (~L2100): перевірка commit_final_bar + publish_preview_bar contract. **LOUD** (raises RuntimeError).
- `selftest_disk_policy()` (~L2200): перевірка disk_policy="never"/"bootstrap"/expired. **LOUD** (raises RuntimeError).

### 6.2 SSOT JSONL guards (`runtime/store/ssot_jsonl.py`)

| # | Line | Функція | Що валідує | Реакція |
|---|------|---------|------------|---------|
| G21 | ~113 | `JsonlAppender.append(bar)` — finality | `bar.complete and bar.src in FINAL_SOURCES` | **LOUD** (error log "SSOT_DROP_NON_FINAL" + counter) |
| G22 | ~130 | `JsonlAppender.append(bar)` — invariants | `assert_invariants(bar, anchor_offset_s)` | **LOUD** (raises ValueError) |
| G23 | ~N/A | `_selftest_ssot_guard()` | Preview bars rejected; final bars accepted | **LOUD** (raises RuntimeError) |

### 6.3 Redis Snapshot guards (`runtime/store/redis_snapshot.py`)

| # | Line | Функція | Що валідує | Реакція |
|---|------|---------|------------|---------|
| G24 | ~270 | `put_bar(bar)` — close_le_open | `close_ms_excl > open_ms` | **LOUD** (log "REDIS_SNAP_SKIP_INVALID_BAR") |
| G25 | ~278 | `put_bar(bar)` — close_mismatch | `complete → close_ms_excl == open_ms + tf_ms` | **LOUD** (log "REDIS_SNAP_SKIP_INVALID_BAR") |
| G26 | ~120 | `_bar_to_cache_bar(bar)` | Geometry checks | Returns None (skip) |
| G27 | ~100 | `_log_error_throttled()` | Rate-limited Redis error logging | **LOUD** (throttled) |

### 6.4 Redis Spec guards (`runtime/store/redis_spec.py`)

| # | Line | Функція | Що валідує | Реакція |
|---|------|---------|------------|---------|
| G28 | ~51 | `resolve_redis_spec()` — forbidden key | `"ns" not in raw` | **LOUD** (raises RuntimeError) |
| G29 | ~55 | `resolve_redis_spec()` — namespace | namespace exists | **LOUD** (raises RuntimeError) |
| G30 | ~N/A | `resolve_redis_spec()` — ENV mismatch | ENV override tracking | **LOUD** (mismatch logged) |

### 6.5 Layer guards (Disk/RAM/Redis)

| # | Layer | Функція | Що валідує | Реакція |
|---|-------|---------|------------|---------|
| G31 | Disk | `_bar_is_complete(bar)` | bar["complete"] truthy | Helper (bool) |
| G32 | Disk | `_bar_is_final_source(bar)` | bar["src"] ∈ FINAL_SOURCES | Helper (bool) |
| G33 | Disk | `_bar_has_canonical_ohlc(bar)` | o/h/l/c exist and float-parseable | Helper (bool) |
| G34 | Disk | `_bar_passes_filters(bar)` | Composite: final_only + skip_preview + canonical OHLC | **SILENT** (filter) |
| G35 | Disk | `_choose_better_bar(existing, incoming)` | Final > preview; final_source > non-final; newer event_ts | **SILENT** (merge policy) |
| G36 | Disk | `_dedup_open_ms(bars)` | Dedup by open_ms using_choose_better_bar | **SILENT** (returns dropped count) |
| G37 | Disk | `_finalize_tail_with_geom(out)` | Sort + dedup, geom metadata | **SILENT** (auto-fix + metadata) |
| G38 | RAM | `upsert_bar()` — open_ms check | `isinstance(open_ms, int)` | **SILENT** (skip if not int) |
| G39 | RAM | `_evict_if_needed()` | LRU eviction: max_keys limit | **SILENT** (auto-evict) |
| G40 | Redis | `_get_json(key)` | JSON parse + TTL check | Returns error tuple |
| G41 | Redis | `publish_preview_event()` | Atomic seq + list trim | **SILENT** (auto-trim) |

---

## 7. Guards — runtime/ingest/

### 7.1 Tick Aggregator (`runtime/ingest/tick_agg.py`)

| # | Що валідує | Реакція | Метрика |
|---|------------|---------|---------|
| G42 | tf_s not in tf_allowlist | **SILENT** (counter only) | `ticks_rejected_tf` |
| G43 | open_ms < state.open_ms (rollback) | **SILENT** (counter) | `ticks_dropped_late_bucket` |
| G44 | tick_ts_ms < state.open_tick_ts_ms | **SILENT** (counter) | `ticks_dropped_before_open` |
| G45 | tick_ts_ms < state.last_tick_ts_ms | **SILENT** (counter) | `ticks_dropped_out_of_order` |
| G46 | Auto-promote on rollover | Creates "tick_promoted" bar (complete=True) | `promoted_total` |

> **⚠ FINDING F2**: G42-G45 are SILENT (counter only, no log) — порушення Rule №9.

### 7.2 Tick Preview Worker (`runtime/ingest/tick_preview_worker.py`)

| # | Що валідує | Реакція | Метрика |
|---|------------|---------|---------|
| G47 | `_validate_tick_schema(payload)` — tick_v1 fields | **SILENT** (counter) | `ticks_dropped_schema` |
| G48 | version != 1 | **SILENT** (counter) | `ticks_dropped_version` |
| G49 | symbol normalization failure | **SILENT** (counter) | `ticks_dropped_symbol` |
| G50 | symbol not in allowlist | **SILENT** (counter) | `ticks_dropped_symbol` |
| G51 | tick_ts_ms resolution failure | **SILENT** (counter) | `ticks_dropped_ts` |
| G52 | out-of-order tick | **SILENT** (counter) | `ticks_dropped_out_of_order` |
| G53 | price extraction failure | **SILENT** (counter) | `ticks_dropped_price` |
| G54 | Calendar gate: `!is_trading_minute()` | **LOUD** (throttled warning/60s) | `ticks_dropped_calendar` |
| G55 | Forward-gap >1 bar | **LOUD** (WARNING if trading, DEBUG if closed) | `preview_gap_total` |
| G56 | Publish throttling (min interval) | **SILENT** (counter) | `preview_publish_throttled` |
| G57 | `_TICK_REQUIRED` fields check | Part of G47 | tick_v1 required |
| G58 | `_TICK_ALLOWED` extra fields | Part of G47 | `additionalProperties: false` |
| G59 | Zero-ticks silence >120s | **LOUD** (warning) | One-shot |

### 7.3 Tick Publisher FXCM (`runtime/ingest/tick_publisher_fxcm.py`)

| # | Що валідує | Реакція |
|---|------------|---------|
| G60 | `_normalize_symbol()` — alias resolve | **SILENT** (returns None) |
| G61 | `_pick_price()` — price mode | **SILENT** (returns None) |
| G62 | Wallclock fallback ratio >10% | **LOUD** (warning) |

### 7.4 Derive Engine (`runtime/ingest/derive_engine.py`)

| # | Що валідує | Реакція | Інваріант |
|---|------------|---------|-----------|
| G63 | `on_bar()` — symbol in symbols | **SILENT** (returns []) | — |
| G64 | `on_bar()` — bar.complete | **SILENT** (returns []) | — |
| G65 | Per-symbol threading lock | Concurrency guard | — |
| G66 | `_cascade()` — derive returns None (important TF) | **LOUD** (warning "DERIVE_SKIP") | — |
| G67 | UDS commit rejected (reason ≠ stale/dup) | **LOUD** (warning "DERIVE_REJECT") | I5 |
| G68 | `check_overdue_buckets()` — overdue scan | **LOUD** (info "OVERDUE_DERIVE_OK") | Страховка |

### 7.5 M1 Poller (`runtime/ingest/polling/m1_poller.py`)

| # | Що валідує | Реакція |
|---|------------|---------|
| G69 | `M1Buffer.upsert()` — tf_s == 60 | **LOUD** (raises ValueError) |
| G70 | `_is_flat(bar)` — o==h==l==c, v ≤ flat_bar_max_volume | Helper (bool) |
| G71 | `_ingest_bar()` — isinstance CandleBar + tf_s==60 + complete | **SILENT** (returns False) |
| G72 | Calendar-aware flat bar: flat + !trading → skip | **SILENT** (returns False) |
| G73 | Non-flat in pause → anomaly | **LOUD** (warning "M1_NONFLAT_IN_PAUSE") |
| G74 | UDS commit rejected (reason ≠ stale/dup) | **LOUD** (warning "M1_COMMIT_REJECT") |
| G75 | Gap ≥ GAP_WARN_THRESHOLD(3) | **LOUD** (info "M1_GAP_DETECTED") |
| G76 | Live recover — gap > threshold | **LOUD** (warning "M1_LIVE_RECOVER_START") |
| G77 | Live recover — budget exhausted | **LOUD** (info "M1_LIVE_RECOVER_DONE") |
| G78 | Stale detection: no new M1 > stale_s(720) | **LOUD** (warning) |

### 7.6 Polling Engine B (`runtime/ingest/polling/engine_b.py`)

| # | Що валідує | Реакція |
|---|------------|---------|
| G79 | `_is_final_bar(bar)` — complete + src ∈ FINAL_SOURCES | Helper (bool) |
| G80 | Watermark pre-filter (backfill quarantine) | **LOUD** (throttled warning) |
| G81 | UDS commit rejected | **SILENT** (debug log) |
| G82 | Bootstrap degraded phases | **LOUD** (warning per phase) |
| G83 | Cache prime partial (budget exceeded) | **LOUD** (warning) |
| G84 | `_warn_throttled(key, msg, every_s)` | Rate-limited warning | Rule №9.1 |

### 7.7 Market Calendar (`runtime/ingest/market_calendar.py`)

| # | Що валідує | Реакція |
|---|------------|---------|
| G85 | `is_trading_minute(now_ms)` — daily break (wrap-midnight) | Returns bool |
| G86 | `is_trading_minute(now_ms)` — weekend | Returns bool |

### 7.8 Dedup (`runtime/ingest/polling/dedup.py`)

| # | Що валідує | Реакція |
|---|------------|---------|
| G87 | `has_on_disk(cache, uds, symbol, tf_s, open_ms)` | Returns bool |
| G88 | `mark_on_disk(cache, uds, symbol, tf_s, open_ms)` | Marks written |

---

## 8. Guards — app/

### 8.1 Config validation (`app/composition.py`)

| # | Line | Що валідує | Реакція |
|---|------|------------|---------|
| G89 | ~89 | Integer fields (10 полів з min_val) | **LOUD** (raises ValueError) |
| G90 | ~110 | Int list uniqueness (tf_allowlist, broker_base_tfs, redis_priming) | **LOUD** |
| G91 | ~120 | Symbols: exists, unique str list | **LOUD** |
| G92 | ~130 | Calendar gate: required dicts present | **LOUD** |
| G93 | ~140 | Redis config: host=str, port/db=int, ttl_by_tf_s, tail_n_by_tf_s | **LOUD** |
| G94 | ~150 | Cross-validation: connector_retry_max_s ≥ base_s | **LOUD** |

---

## 9. Guards — ui_chart_v3/

### 9.1 Contract guards (`ui_chart_v3/server.py`)

| # | Line | Функція | Що валідує | Контракт |
|---|------|---------|------------|----------|
| G95 | 561 | `_guard_bar_shape(bar)` | 11 required + 3 optional; type checks; no extra fields | bar_v1 |
| G96 | 609 | `_guard_event_shape(ev)` | 4 required + 2 optional; key sub-object recursion | updates_v1 event |
| G97 | 650 | `_guard_meta_shape(meta)` | 3 required + 6 optional meta fields | window_v1 meta |
| G98 | 861 | `_contract_guard_warn_window(payload, bars, warnings)` | Comprehensive window_v1 check | window_v1 |
| G99 | 895 | `_contract_guard_warn_updates(payload, events, warnings)` | Updates: ok, required fields, cursor_seq type | updates_v1 |

### 9.2 Policy guards

| # | Що валідує | Реакція |
|---|------------|---------|
| G100 | `_clamp_limit(raw_limit, tf_s)` — `_TF_CAP` per TF | **LOUD** (rate-limited warning/30s) |
| G101 | `_policy_sanity_issues()` — warmup/cold_start consistency | **LOUD** (warning at boot) |
| G102 | TF allowlist: tf_s not in either allowlist | **LOUD** (400 "tf_not_allowed") |
| G103 | Align validation: align ∉ {fxcm, tv} | **LOUD** (400 "align_not_supported") |
| G104 | HTF anchor offset: overlay remainder | **LOUD** (warning) |
| G105 | I3 final>preview in /api/bars preview path | **SILENT** (merge policy) |

### 9.3 Normalization guards

| # | Функція | Що валідує | Реакція |
|---|---------|------------|---------|
| G106 | `_normalize_bar_window_v1(raw)` | Raw → window_v1 format | **SILENT** (returns None → dropped) |
| G107 | `_normalize_bars_window_v1(bars)` | Batch normalization | **SILENT** (counted `dropped_bars`) |
| G108 | `_log_public_api_contract_guard()` | Aggregate logging of drops | **LOUD** (periodic warning) |
| G109 | `_is_flat_preview_bar()` | Flat preview filter | **SILENT** (filter) |

---

## 10. Enforcement інваріантів (I0–I6)

### I0: Dependency Rule

| Шар | Enforcement | Guards |
|-----|------------|--------|
| core/ ¬→ runtime/ | **Немає автоматичного gate!** | — (див. зауваження) |
| runtime/ ¬→ tools/ | Немає автоматичного gate | — |
| ui/ ¬→ redis | Exit gate | EG8 (`gate_ui_no_direct_redis`) |
| ui/ ¬→ domain | Exit gate | EG9 (`gate_ui_thin_handlers`) |
| ui/ ¬→ writers | Exit gate | EG10 (`gate_ui_single_writer`) |
| ingest/ ¬→ direct writers | Exit gate | EG11 (`gate_ingest_no_direct_writers`) |

> **❗ Зауваження**: Всі існуючі exit gates перевіряють **ui/** та **ingest/** шари. Для основного правила `core/ ¬→ runtime/` та `runtime/ ¬→ tools/` **немає автоматизованого enforcement**. Це прогалина (gap).

### I1: UDS = єдина точка запису

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| Writer role check | G4 (`_ensure_writer_role`) | `uds.py:1199` |
| SSOT finality guard | G21-G23 | `ssot_jsonl.py:~113-130` |
| Exit gates | EG10, EG11 | Static analysis |

### I2: Єдина геометрія часу

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| `assert_invariants()` | G1 | `core/model/bars.py:55` |
| Redis geometry | G24, G25 | `redis_snapshot.py:~270-278` |
| SSOT invariants | G22 | `ssot_jsonl.py:~130` |

### I3: Final > Preview / NoMix

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| UDS commit guards | G7, G8 (source check) | `uds.py:630-638` |
| UDS preview guards | G10, G11, G20 (NoMix violation) | `uds.py:722-736` |
| Disk layer merge | G35 (`_choose_better_bar`) | `disk_layer.py:~180` |
| Server I3 guard | G105 (preview path) | `server.py:~1335-1340` |
| Client I3 guard | `app.js:1723-1735` | Two mechanisms: unconditional skip + NoMix source check |
| Exit gates | EG12, EG13, EG14 | Preview not on disk, not in final Redis |

### I4: Dual Plane Update Routing (один endpoint, два backend planes)

Є один endpoint `/api/updates`. Routing по TF:

- `tf_s ∈ preview_tf_allowlist` (M1/M3) → Preview Ring (`redis_layer.read_preview_updates`).
- Інакше (M5-D1) → UpdatesBus (`_RedisUpdatesBus.read_updates`).
- Bridge `_publish_final_to_preview_ring` — best-effort final→preview crossover.

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| Server contract guards | G98, G99 | `server.py:870-924` |
| Exit gate | EG15 (`gate_ui_live_candle_plane`) | Static analysis |

### I5: Degraded-but-loud

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| UDS _mark_* family | G17 | Multiple locations in `uds.py` |
| Derive reject | G67 | `derive_engine.py:~410` |
| M1 Poller reject | G74 | `m1_poller.py:~370` |
| Engine B bootstrap | G82, G83 | `engine_b.py:~300-390` |
| Server no-data rail | `_build_no_data_payload` | `server.py:384-407` |

### I6: Watermark + drop-stale

| Enforcement | Guards | Evidence |
|------------|--------|----------|
| UDS watermark | G5, G9 | `uds.py:97, 649` |
| Tick OOO | G45 | `tick_agg.py` (counter only — **SILENT**) |
| Preview OOO | G52 | `tick_preview_worker.py` (counter only — **SILENT**) |
| Backfill quarantine | G80 | `engine_b.py:~480` |

---

## 11. Exit Gates framework

### 11.1 Runner

**Файл**: `tools/run_exit_gates.py`

**Механіка**:

1. Читає `tools/exit_gates/manifest.json` → список gates з inputs/categories.
2. Для кожного gate: `importlib.import_module(gate_module).run_gate(inputs)`.
3. HTTP gates: якщо URL недосяжний → graceful skip (не fail).
4. Output: `reports/exit_gates/{run_id}/report.json`.
5. Exit code: 0 (all pass) або 1 (any fail).

**Запуск**: `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json`.

### 11.2 Gate result format

```python
{
    "ok": bool,          # pass/fail
    "details": str,      # human-readable
    "metrics": dict,     # optional counters
}
```

---

## 12. Exit Gates inventory (24)

| # | Gate name | Файл | Категорія | Що перевіряє |
|---|-----------|------|-----------|-------------|
| EG1 | redis_snapshots_basic | redis_snapshots_basic.py | Infra | Redis connectivity + snapshot exists |
| EG2 | gate_redis_snapshot_schema | gate_redis_snapshot_schema.py | Contract | Redis snapshot schema validation |
| EG3 | gate_api_bars_meta | gate_api_bars_meta.py | Contract | /api/bars response meta validation |
| EG4 | gate_api_updates_contract (M5) | gate_api_updates_contract.py | Contract | /api/updates M5 contract |
| EG5 | gate_api_updates_contract (M1) | gate_api_updates_contract.py | Contract | /api/updates M1 contract |
| EG6 | gate_api_updates_cursor | gate_api_updates_cursor.py | Contract | cursor_seq sanity |
| EG7 | gate_payload_budgets | gate_payload_budgets.py | SLO | Payload limits: status 16KB, updates 256KB, bars 1MB |
| EG8 | gate_ui_no_direct_redis | gate_ui_no_direct_redis.py | Dependency (I0) | UI server doesn't import redis |
| EG9 | gate_ui_thin_handlers | gate_ui_thin_handlers.py | Dependency (I0) | No `_read_jsonl_`, `_redis_`, `_cache_` methods |
| EG10 | gate_ui_single_writer | gate_ui_single_writer.py | Dependency (I1) | UI doesn't import ssot_jsonl, redis_snapshot |
| EG11 | gate_ingest_no_direct_writers | gate_ingest_no_direct_writers.py | Dependency (I1) | Ingest doesn't import write modules directly |
| EG12 | gate_preview_not_on_disk | gate_preview_not_on_disk.py | I3/NoMix | Preview bars not written to SSOT disk |
| EG13 | gate_no_preview_in_final_redis | gate_no_preview_in_final_redis.py | I3/NoMix | No preview bars in final Redis snapshots |
| EG14 | gate_preview_plane | gate_preview_plane.py | I3 | Preview plane separation |
| EG15 | gate_ui_live_candle_plane | gate_ui_live_candle_plane.py | I4 | UI live candle plane correctness |
| EG16 | gate_htf_available | gate_htf_available.py | Derive | HTF (H4/D1) availability |
| EG17 | gate_config_singleton | gate_config_singleton.py | SSOT | Config singleton pattern |
| EG18 | gate_coldstart_multisymbol | gate_coldstart_multisymbol.py | Bootstrap | Coldstart multi-symbol support |
| EG19 | gate_calendar_break_wrap | gate_calendar_break_wrap.py | Calendar | Calendar break wrap-through-midnight |
| EG20 | gate_unexpected_gap_budget | gate_unexpected_gap_budget.py | SLO | Gap limits: total≤100, per_sym≤50 / 14d |
| EG21 | gate_calendar_multi_break | gate_calendar_multi_break.py | Calendar | Multiple daily breaks support |
| EG22 | gate_live_recover_policy | gate_live_recover_policy.py | Recovery | Live recovery policy correctness |
| EG23 | gate_derived_partial | gate_derived_partial.py | Derive | Derived partial handling |
| EG24 | gate_tick_preview_calendar | gate_tick_preview_calendar.py | Calendar | Tick preview calendar integration |

**Покриття по інваріантах**:

- I0 (Dependency): EG8, EG9, EG10, EG11
- I1 (UDS SSOT): EG10, EG11
- I3 (Final>Preview): EG12, EG13, EG14
- I4 (Single updates): EG15
- SLO: EG7, EG20

---

## 13. Loud vs Silent аналіз

### LOUD guards (правильна поведінка за Rule №9)

| Шар | Guards | Механізм |
|-----|--------|----------|
| core/ | G1-G3 (3) | raises ValueError |
| runtime/store/ UDS | G4-G15, G17-G20 (17) | CommitResult(False), RuntimeError, warning |
| runtime/store/ SSOT | G21-G23 (3) | error log + counter, RuntimeError |
| runtime/store/ Redis | G24-G30 (7) | warning, RuntimeError |
| runtime/ingest/ | G54-G55, G59, G62, G66-G69, G73-G78, G80, G82-G84 (17) | warning/info |
| app/ | G89-G94 (6) | raises ValueError |
| ui/ | G95-G104, G108 (11) | warning, 400 response |
| **Total** | **64** | |

### SILENT guards (потенційні порушення Rule №9)

| Шар | Guards | Механізм | Ризик |
|-----|--------|----------|-------|
| tick_agg.py | G42-G45 (4) | Counter only, no log | **HIGH** — dropped ticks invisible |
| tick_preview_worker.py | G47-G53, G56 (8) | Counter only | **MEDIUM** — counters emitted per 60s |
| derive_engine.py | G63-G64 (2) | Returns [] | LOW — expected |
| runtime/store/ layers | G34-G41 (8) | Filter / auto-fix | LOW — internal |
| ui/ | G105-G107, G109 (4) | Normalization | LOW — normalization expected |
| polling/ | G71-G72, G79, G81, G87-G88 (6) | Debug/returns bool | LOW — internal |
| UDS | G16 (1) | Trim/auto-fix | **LOW** — internal dedup |
| **Total** | **33** | |

---

## 14. Знахідки та потенційні проблеми

### F1: FINAL_SOURCES дублювання (РИЗИК: середній)

**Факт**: `FINAL_SOURCES` визначено в **трьох** місцях:

- `runtime/store/uds.py:42` → `{"history", "derived", "history_agg"}`
- `runtime/store/layers/disk_layer.py:10` → `{"history", "derived", "history_agg"}`
- `runtime/store/ssot_jsonl.py:12` → `{"history", "derived", "history_agg"}`

> Evidence: `uds.py:42`, `disk_layer.py:10`, `ssot_jsonl.py:12`.

**Вплив**: При додаванні нового final source потрібно змінити три місця. Якщо забути одне — split-brain (напр., UDS прийме, але SSOT JSONL або disk відкине).

**Рекомендація**: Винести в `core/` як SSOT, і всі три файли імпортують звідти.

---

### F2: Tick Aggregator drops — SILENT (РИЗИК: високий)

**Факт**: Guards G42-G45 у `tick_agg.py` лише інкрементують counter. Жодного log warning при drop тіка.

> Evidence: `runtime/ingest/tick_agg.py` — counter-only для `ticks_rejected_tf`, `ticks_dropped_late_bucket`, `ticks_dropped_before_open`, `ticks_dropped_out_of_order`.

**Вплив**: Якщо тіки масово відкидаються (наприклад, через clock drift) — це невидимо без окремого stats emit циклу.

**Рекомендація**: Додати rate-limited warning (per 60s) при drop rate > threshold. Інваріант I5 (degraded-but-loud) формально порушено.

---

### F3: MAX_EVENTS_PER_RESPONSE trimming — LOUD (РИЗИК: низький)

**Факт**: `uds.py:579-581` — якщо events > 500, обрізає до останніх 500 **з explicit warning**: `warnings.append("max_events_trimmed")` (L581).

> Evidence: `runtime/store/uds.py:579-581`.

**Вплив**: Формально відповідає Rule №9 (degraded-but-loud). UI клієнт може виявити trimming через `warnings[]` поле.

**Зауваження**: Можна покращити, додавши кількість триммед events: `"events_trimmed:{total}"` замість просто `"max_events_trimmed"`.

---

### F4: `_ensure_sorted_dedup` auto-fix — SILENT (РИЗИК: низький)

**Факт**: UDS `_ensure_sorted_dedup()` автоматично вирівнює та дедуплікує бари без explicit degraded flag.

**Вплив**: Якщо є проблема з джерелом (duplicate bars) — це "лікується" тихо. Не critical для працюючої системи, але ускладнює діагностику.

**Рекомендація**: При fix > 0 bars — додати `meta.extensions.geom_fix = {dedup: N, reorder: M}`.

---

### F5: `_bar_passes_filters` — SILENT filter для disk reads (РИЗИК: низький)

**Факт**: Disk layer фільтрує non-final, incomplete, non-canonical бари тихо.

> Evidence: `runtime/store/layers/disk_layer.py:~148`.

**Вплив**: Очікувана поведінка для disk layer (SSOT protection). Але якщо з'являться некоректні бари — dropped count не видимий.

**Рекомендація**: Log dropped count при read > 0 (info level, periodic).

---

### F6: Selftest coverage — bootstrap only (РИЗИК: низький)

**Факт**: Selftests (`selftest_writer_api`, `selftest_disk_policy`, `_selftest_ssot_guard`) виконуються лише при boot.

**Вплив**: Після bootstrap зміна конфігу/стану може порушити контракт без повторної перевірки.

**Рекомендація**: Додати periodic selftest (раз на N хвилин) або включити до exit gates.

---

### F7: Default `complete=True` у server.py нормалізації (РИЗИК: середній)

**Факт**: `_normalize_bar_window_v1` (`server.py:744`): якщо raw bar не має поля `complete` → default `True`.

> Evidence: `server.py:744`.

**Вплив**: Якщо preview бар без explicit `complete=False` потрапить у нормалізацію — стане `complete=True`. Це може порушити I3. Поточне використання safe (preview завжди має `complete`), але fragile.

**Рекомендація**: Додати warning якщо `complete` field відсутній (замість silent default).

---

### Зведена таблиця знахідок

| ID | Опис | Ризик | Інваріанти |
|----|------|-------|------------|
| F1 | FINAL_SOURCES дублювання | Середній | I3 |
| F2 | Tick agg drops — SILENT | Високий | I5, I6 |
| F3 | MAX_EVENTS trimming — LOUD | Низький | I5 (відповідає) |
| F4 | _ensure_sorted_dedup auto-fix — SILENT | Низький | I5 |
| F5 | Disk filter drops — SILENT | Низький | I5 |
| F6 | Selftest only at boot | Низький | — |
| F7 | Default complete=True у нормалізації | Середній | I3 |
