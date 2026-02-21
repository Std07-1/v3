# P2: Data Flow — Потоки даних від брокера до UI

> **Документ**: code-first, кожен факт має evidence `(file:line)`.
> **Дата**: 2026-02-21
> **Scope**: повний маппінг усіх потоків OHLCV та тиків: Broker → Ingest → UDS → Storage → API → UI.
> **Залежності**: P1 (Process Inventory) — імена процесів і bootstrap sequence.

---

## Зміст

- [1. Зведена діаграма потоків даних](#1-зведена-діаграма-потоків-даних)
- [2. Flow A: D1 Final Bars (Connector → UDS → Disk + Redis)](#2-flow-a-d1-final-bars)
- [3. Flow B: M1 Final Bars (M1 Poller → UDS → Disk + Redis + Updates)](#3-flow-b-m1-final-bars)
- [4. Flow C: Каскадна деривація (DeriveEngine: M1 → M3..H4)](#4-flow-c-каскадна-деривація)
- [5. Flow D: Tick Stream (FXCM OFFERS → Redis Pub/Sub)](#5-flow-d-tick-stream)
- [6. Flow E: Tick Preview (Redis Pub/Sub → Preview Ring)](#6-flow-e-tick-preview)
- [7. Flow F: UI Cold Load (/api/bars → UDS → Redis/Disk)](#7-flow-f-ui-cold-load)
- [8. Flow G: UI Live Updates (/api/updates → Redis Updates Bus / Preview Ring)](#8-flow-g-ui-live-updates)
- [9. UDS Commit Pipeline (деталі)](#9-uds-commit-pipeline)
- [10. Redis Topology (ключі та TTL)](#10-redis-topology)
- [11. Preview Plane vs Final Plane](#11-preview-plane-vs-final-plane)
- [12. Watermark та Drop-Stale Policy](#12-watermark-та-drop-stale-policy)
- [13. Bootstrap Data Flow (priming)](#13-bootstrap-data-flow)
- [14. Stitching (UI-only transform)](#14-stitching)
- [15. Зведена матриця: TF × Source × Destination](#15-зведена-матриця)
- [16. Знахідки та потенційні проблеми](#16-знахідки)

---

## 1. Зведена діаграма потоків даних

```
                         ┌─────────────── FXCM Broker ────────────────┐
                         │                                            │
                ┌────────┤                                   ┌────────┤
                │History │                                   │OFFERS  │
                │API     │                                   │table   │
                │(D1)    │                                   │(ticks) │
                └───┬────┘                                   └───┬────┘
                    │                                            │
           ┌────────▼────────┐                          ┌────────▼────────┐
           │  CONNECTOR      │                          │  TICK PUBLISHER │
           │  (D1 fetcher)   │                          │  (FXCM stream)  │
           │                 │                          │                 │
           │ fetch_last_n_tf │                          │ _handle_row()   │
           └────────┬────────┘                          └────────┬────────┘
                    │ CandleBar                                  │ JSON tick_v1
                    │ (complete=True,                            │ via Redis
                    │  src="history")                            │ Pub/Sub
                    │                                            │
           ┌────────▼────────┐                                   │
           │  UDS (writer)   │                          ┌────────▼────────┐
           │  per-symbol     │                          │  Redis channel  │
           │  commit_final   │                          │  (tick pubsub)  │
           │  _bar()         │                          └────────┬────────┘
           └──┬──┬──┬────────┘                                   │
              │  │  │                                    ┌───────▼─────────┐
              │  │  └──► Redis Snap (put_bar)            │  TICK PREVIEW   │
              │  └─────► Disk JSONL (append)             │  WORKER         │
              └────────► Updates Bus (publish)           │  on_tick()      │
                         (для D1 — через updates bus)    └────────┬────────┘
                                                                  │
           ┌─────────────── FXCM Broker ──────────────┐           │ CandleBar
           │ History API (M1)                         │           │ (M1/M3 preview)
           └──────────┬───────────────────────────────┘           │
                      │                                  ┌────────▼────────┐
             ┌────────▼────────┐                         │  UDS (writer)   │
             │  M1 POLLER      │                         │  writer_compo-  │
             │  (M1 fetcher)   │                         │  nents=False    │
             │  poll_once()    │                         │  publish_       │
             └────────┬────────┘                         │  preview_bar()  │
                      │ CandleBar                        └────────┬────────┘
                      │ (complete=True,                           │
                      │  src="history")                           │ Preview Ring
                      │                                           │ (Redis)
             ┌────────▼────────┐                                  │
             │  UDS (writer)   │                                  │
             │  shared per     │                                  │
             │  M1PollerRunner │                                  │
             │  commit_final   │                                  │
             │  _bar()         │                                  │
             └──┬──┬──┬────────┘                                  │
                │  │  │                                           │
                │  │  └──► Redis Snap                             │
                │  └─────► Disk JSONL                             │
                └────────► Updates Bus                            │
                      │                                           │
             ┌────────▼────────┐                                  │
             │  DERIVE ENGINE  │                                  │
             │  on_bar(M1)     │                                  │
             │  cascade:       │                                  │
             │  M3→M5→M15→     │                                  │
             │  M30→H1→H4      │                                  │
             └──┬──────────────┘                                  │
                │ CandleBar per derived TF                        │
                │ commit_final_bar() per TF                       │
                └──► Disk + Redis + Updates Bus                   │
                                                                  │
             ┌─────────── UI SERVER ──────────────────────────────┤
             │                                                    │
             │  /api/bars ───► UDS.read_window()                  │
             │                   Redis snap → Disk fallback       │
             │                   + preview overlay (M1/M3)        │
             │                                                    │
             │  /api/updates ──► Final TFs: UpdatesBus.read()     │
             │                   Preview TFs: PreviewRing.read()◄─┘
             │
             │  /api/bars response → stitching (optional) → JSON
             └────────────────────────────────────────────────────
```

---

## 2. Flow A: D1 Final Bars

**Процес**: Connector (`runtime/ingest/polling/engine_b.py`)
**Джерело**: FXCM History API
**TF**: тільки D1 (86400s)

### 2.1 Потік

```
FXCM History API
    │ fetch_last_n_tf(symbol, tf_s=86400, n=1)
    ▼
PollingConnectorB._fetch_base_from_broker_on_close()    [engine_b.py:L613-L680]
    │ Перевірка: anchor bucket, last_trading_minute, has_on_disk
    ▼
PollingConnectorB._append_bar(bar)                      [engine_b.py:L456-L477]
    │ if _is_final_bar(bar) → UDS.commit_final_bar()
    │ if not final → UDS.publish_preview_bar()
    ▼
UDS.commit_final_bar(bar)                               [uds.py:L619-L675]
    ├── _append_to_disk()     → JSONL append  (data_v3/{sym}/tf_86400/part-*.jsonl)
    ├── _write_redis_snapshot() → Redis snap + tail
    └── _publish_update()      → Updates Bus (Redis list)
```

### 2.2 Scheduling

- **Poll trigger**: кожну хвилину (`_sleep_to_next_minute`) — [engine_b.py:L602-L603](../runtime/ingest/polling/engine_b.py#L602-L603)
- **Fetch guard**: бар фетчиться лише коли `last_trading_open == expected_last` (кінець D1 bucket) — [engine_b.py:L639-L640](../runtime/ingest/polling/engine_b.py#L639-L640)
- **Dedup**: `has_on_disk()` перевіряє `_day_index_cache` — фетчиться лише раз

### 2.3 Watermark

- Connector використовує `_last_saved_base[tf_s]` як soft watermark — [engine_b.py:L241](../runtime/ingest/polling/engine_b.py#L241)
- UDS watermark (`_wm_by_key[(symbol, tf_s)]`) — жорсткий, drop stale — [uds.py:L651-L660](../runtime/store/uds.py#L651-L660)
- Backfill quarantine: bari з `open_ms <= wm` логуються як `BACKFILL_QUARANTINE` (throttled) — [engine_b.py:L463-L474](../runtime/ingest/polling/engine_b.py#L463-L474)

---

## 3. Flow B: M1 Final Bars

**Процес**: M1 Poller (`runtime/ingest/polling/m1_poller.py`)
**Джерело**: FXCM History API
**TF**: M1 (60s)

### 3.1 Потік

```
FXCM History API
    │ fetch_last_n_m1(symbol, n=fetch_n, date_to_utc)
    ▼
M1SymbolPoller.poll_once()                              [m1_poller.py:L401-L466]
    │ Calendar gate: _check_calendar_state()
    │ Adaptive fetch: _compute_fetch_n()  [2 if caught-up, gap+1 if behind]
    │ Watermark pre-filter: bars > watermark && bars <= expected
    │ Sort: bars.sort(key=open_time_ms)
    ▼
M1SymbolPoller._ingest_bar(bar)                         [m1_poller.py:L320-L398]
    │ Calendar-aware flat bar classification:
    │   flat + market_open → accept with trading_flat extension
    │   flat + market_closed → DROP (broker noise)
    │   non-flat + market_closed → accept with calendar_pause_nonflat_anomaly
    ▼
UDS.commit_final_bar(bar)                               [uds.py:L619-L675]
    ├── _append_to_disk()      → JSONL     (data_v3/{sym}/tf_60/part-*.jsonl)
    ├── _write_redis_snapshot() → Redis snap + tail
    ├── _publish_update()       → Updates Bus
    └── _publish_final_to_preview_ring()  → якщо tf_s ∈ preview_tf_allowlist (M1/M3)
    ▼
DeriveEngine.on_bar(committed_M1)                       [m1_poller.py:L380-L391]
    └── cascade → Flow C
```

### 3.2 Adaptive Fetch

- **Caught-up** (`watermark >= expected`): `fetch_n = 2` — [m1_poller.py:L307](../runtime/ingest/polling/m1_poller.py#L307)
- **Gap detected**: `fetch_n = min(gap + 1, MAX_FETCH_N=120)` — [m1_poller.py:L317](../runtime/ingest/polling/m1_poller.py#L317)
- **Expected calc**: `_expected_closed_m1_calendar()` — calendar-aware floor — [m1_poller.py:L174-L189](../runtime/ingest/polling/m1_poller.py#L174-L189)

### 3.3 Watermark + Post-ingest

- `_watermark_ms` оновлюється після кожного successful commit — [m1_poller.py:L376](../runtime/ingest/polling/m1_poller.py#L376)
- M1Buffer: `upsert(bar)` для legacy M3 derive fallback — [m1_poller.py:L379](../runtime/ingest/polling/m1_poller.py#L379)
- після poll_once: `_live_recover_check()` (P0.2) + `_stale_check()` (P0.3) — [m1_poller.py:L465-L466](../runtime/ingest/polling/m1_poller.py#L465-L466)

### 3.4 Live Recover (P0.2 ADR-0002)

Якщо gap > `live_recover_threshold_bars` (default 3):

- вхід у recover mode — [m1_poller.py:L470-L487](../runtime/ingest/polling/m1_poller.py#L470-L487)
- batch fetch з cooldown + budget cap (`max_total_bars`) — [m1_poller.py:L504-L530](../runtime/ingest/polling/m1_poller.py#L504-L530)
- gap_state публікується в UDS: `degraded-but-loud` — [m1_poller.py:L486](../runtime/ingest/polling/m1_poller.py#L486)
- фініш: `_live_recover_finish(reason)` → clear gap_state — [m1_poller.py:L587-L599](../runtime/ingest/polling/m1_poller.py#L587-L599)

### 3.5 Stale Detection (P0.3)

- Якщо `silence_s > stale_s` (default 720) при market open → loud warning — [m1_poller.py:L609-L626](../runtime/ingest/polling/m1_poller.py#L609-L626)
- Throttled: перші 3 + кожен 60-й — [m1_poller.py:L622-L626](../runtime/ingest/polling/m1_poller.py#L622-L626)

---

## 4. Flow C: Каскадна деривація

**Модуль**: DeriveEngine (`runtime/ingest/derive_engine.py`)
**Trigger**: M1 committed bar від M1 Poller
**Output**: M3, M5, M15, M30, H1, H4 final bars

### 4.1 Cascade Chain

```
M1 (input)
 │ buffer(M1, max=2000)
 ├──► M3  (3 × M1)     commit → UDS
 ├──► M5  (5 × M1)     commit → UDS
 │         buffer(M5, max=500)
 │         ├──► M15 (3 × M5)  commit → UDS
 │         │    buffer(M15, max=200)
 │         │    ├──► M30 (2 × M15) commit → UDS
 │         │    │    buffer(M30, max=100)
 │         │    │    ├──► H1  (2 × M30) commit → UDS
 │         │    │    │    buffer(H1, max=50)
 │         │    │    │    └──► H4  (4 × H1)  commit → UDS
```

**Evidence**:

- Chain definition: `DERIVE_CHAIN` у `core/derive.py` (imported at [derive_engine.py:L30-L36](../runtime/ingest/derive_engine.py#L30-L36))
- Default commit TFs: `DEFAULT_COMMIT_TFS_S = set(DERIVE_ORDER) = {180,300,900,1800,3600,14400}` — [derive_engine.py:L60](../runtime/ingest/derive_engine.py#L60)
- Buffer sizes: `_BUFFER_MAX_KEEP` — [derive_engine.py:L49-L55](../runtime/ingest/derive_engine.py#L49-L55)

### 4.2 Cascade Logic

```
DeriveEngine.on_bar(M1)                                 [derive_engine.py:L144-L168]
    │ with per-symbol lock
    ▼
DeriveEngine._cascade(bar)                              [derive_engine.py:L327-L427]
    │ 1. Buffer upsert (source TF)
    │ 2. derive_triggers(bar, anchor_offset, is_trading_fn) → [(target_tf, bucket_open)]
    │ 3. For each trigger:
    │    a. derive_bar(source_buffer, bucket_open) → CandleBar or None
    │    b. if target_tf ∈ commit_tfs_s → UDS.commit_final_bar(derived)
    │    c. RECURSE: _cascade(derived)  ← derived bar triggers next level
```

### 4.3 Overdue Safety Net

- `check_overdue_buckets(now_ms)` — timer-based per [derive_engine.py:L197-L220](../runtime/ingest/derive_engine.py#L197-L220)
- Lookback per TF: `_OVERDUE_LOOKBACK` — [derive_engine.py:L225-L232](../runtime/ingest/derive_engine.py#L225-L232)
- Страховка від race/restart/out-of-order: якщо bucket_end < now але bar не committed → derive

### 4.4 UDS Integration

- DeriveEngine НЕ створює UDS — реєструє через `register_symbol_uds(symbol, uds)` — [derive_engine.py:L131](../runtime/ingest/derive_engine.py#L131)
- Shared UDS instance: M1 Poller створює UDS, реєструє в DeriveEngine — [m1_poller.py build](../runtime/ingest/polling/m1_poller.py)
- Commit: `uds.commit_final_bar(derived)` з `src="derived"` — [derive_engine.py:L408](../runtime/ingest/derive_engine.py#L408)

### 4.5 Calendar-Aware Derivation

- `derive_triggers()` отримує `is_trading_fn` — знаходить останній TRADING source slot у bucket
- `derive_bar()` з `filter_calendar_pause=True` — фільтрує flat бари з `calendar_pause_flat` extension
- M3 inline derive у m1_poller: `_derive_m3()` фільтрує `calendar_pause_flat` M1 бари — [m1_poller.py:L117](../runtime/ingest/polling/m1_poller.py#L117)

---

## 5. Flow D: Tick Stream

**Процес**: Tick Publisher (`runtime/ingest/tick_publisher_fxcm.py`)
**Джерело**: FXCM OFFERS table (real-time subscription)
**Output**: Redis Pub/Sub channel

### 5.1 Потік

```
FXCM ForexConnect
    │ OFFERS table subscription (UPDATE + INSERT events)
    │ _OffersListener callbacks
    ▼
FxcmTickPublisher._handle_row(row)                      [tick_publisher_fxcm.py:L202-L260]
    │ 1. Extract symbol: _row_value(row, ["Instrument",...])
    │ 2. Normalize: _normalize_symbol() via aliases
    │ 3. Extract bid/ask/mid prices
    │ 4. Compute price per mode (bid/ask/mid): _pick_price()
    │ 5. Extract tick_ts_ms (wallclock fallback якщо FXCM не надає)
    │ 6. Guards:
    │    - out-of-order: tick_ts_ms < last → DROP
    │    - throttle: min_interval_ms (default 200ms)
    ▼
Redis Pub/Sub
    │ channel = pick_tick_channel(cfg)  [config: channels.price_tick]
    │ payload = tick_v1 JSON:
    │   {v:1, symbol, bid, ask, mid, tick_ts_ms, src, seq}
    │
    │ Redis SET:  {ns}:tick:last:{symbol} (TTL = last_tick_ttl_s)
    │   → для quick tick lookup (не основний потік)
```

### 5.2 Schema: tick_v1

**Evidence**: payload formation at [tick_publisher_fxcm.py:L241-L250](../runtime/ingest/tick_publisher_fxcm.py#L241-L250)

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "bid": 2650.12,
  "ask": 2650.45,
  "mid": 2650.12,
  "tick_ts_ms": 1740150000000,
  "src": "fxcm",         // або "fxcm_wallclock"
  "seq": 42
}
```

> **Примітка**: поле `mid` — це результат `_pick_price(mode, bid, ask, mid)`, а **не** математичний mid.
> Поточна конфігурація `tick_stream_price_mode = "bid"` означає `mid == bid`.
> Preview worker читає саме `mid` як ціну для агрегації.

### 5.3 Guards

| Guard | Evidence | Дія |
|-------|----------|-----|
| Out-of-order | [tick_publisher_fxcm.py:L229-L231](../runtime/ingest/tick_publisher_fxcm.py#L229-L231) | DROP + `ticks_dropped_out_of_order` metric |
| Throttle | [tick_publisher_fxcm.py:L236-L238](../runtime/ingest/tick_publisher_fxcm.py#L236-L238) | DROP if `now - last_pub < min_interval_ms` |
| Symbol normalize | [tick_publisher_fxcm.py:L192-L200](../runtime/ingest/tick_publisher_fxcm.py#L192-L200) | alias mapping → canonical symbol |
| Wallclock fallback | [tick_publisher_fxcm.py:L224-L227](../runtime/ingest/tick_publisher_fxcm.py#L224-L227) | `src="fxcm_wallclock"` + `ticks_ts_fallback_wallclock` metric |
| No price | [tick_publisher_fxcm.py:L217-L219](../runtime/ingest/tick_publisher_fxcm.py#L217-L219) | DROP |

---

## 6. Flow E: Tick Preview

**Процес**: Tick Preview Worker (`runtime/ingest/tick_preview_worker.py`)
**Вхід**: Redis Pub/Sub (tick_v1 JSON)
**Output**: UDS preview ring (Redis)

### 6.1 Потік

```
Redis Pub/Sub (tick channel)
    │ pubsub.listen() loop
    ▼
TickPreviewWorker.on_tick(payload)                      [tick_preview_worker.py:L263-L325]
    │ 1. Schema guard: _validate_tick_schema() [tick_v1 contract]
    │ 2. Version check: v == 1
    │ 3. Symbol normalize + allowlist
    │ 4. Timestamp extraction + out-of-order drop
    │ 5. Price extraction
    │ 6. Calendar gate: drop ticks during market closed
    ▼
For each tf_s in configured TFs (M1=60, M3=180):
    │
    ├── M1 (tf_s=60):
    │   TickAggregator.update(symbol, 60, tick_ts_ms, price) [tick_agg.py]
    │   │ → (promoted_bar, current_bar)
    │   │
    │   │ promoted_bar (rollover): tick_promoted, complete=True
    │   │   → UDS.publish_promoted_bar()             [uds.py:L677-L720]
    │   │     → Redis preview ring event {complete:true, source:"tick_promoted"}
    │   │
    │   │ current_bar: preview, complete=False
    │   │   → UDS.publish_preview_bar()              [uds.py:L722-L800]
    │   │     → Redis preview ring curr + event
    │   │
    │   └── M1→M3 derive (if derive_m3 enabled):
    │       _M1toM3Buffer.update(symbol, m1_bar)     [tick_preview_worker.py:L29-L75]
    │       → M3 preview bar (if enough M1s in bucket)
    │       → UDS.publish_preview_bar(m3_bar)
    │
    └── M3 (tf_s=180): SKIPPED у TickAggregator якщо derive_m3=True
                        (M3 будується з M1, не з тиків напряму)
```

### 6.2 Forward-Gap Detection

- `_publish_bar()` перевіряє gap_bars = (new_open - last_open) / tf_ms — [tick_preview_worker.py:L353-L379](../runtime/ingest/tick_preview_worker.py#L353-L379)
- Якщо gap_bars > 1:
  - За market_closed → level=DEBUG
  - За market_open → level=WARNING (`PREVIEW_GAP`)
- Throttled: раз на 60s per (symbol, tf_s)

### 6.3 Promoted Bar (auto-promote)

- `tick_auto_promote_m1=true` у config.json — [config.json:L72](../config.json#L72)
- TickAggregator при rollover створює "mock final" бар: `src="tick_promoted"`, `complete=True`
- UDS: `publish_promoted_bar()` пише в preview ring з `source="tick_promoted"` — [uds.py:L677-L720](../runtime/store/uds.py#L677-L720)
- UI одразу бачить "псевдо-фінальний" M1, який потім замінюється справжнім History M1

### 6.4 UDS Instance

- `writer_components=False` → немає disk writer, немає Redis snap writer — [tick_preview_worker.py:L487](../runtime/ingest/tick_preview_worker.py#L487)
- Тільки preview ring (Redis) — preview curr + preview tail + preview events

---

## 7. Flow F: UI Cold Load

**Endpoint**: `/api/bars` або `/api/latest`
**Процес**: UI Server (`ui_chart_v3/server.py`)
**Два шляхи**: Preview TFs (M1,M3) vs Final TFs (M5+, D1)

### 7.1 Preview TFs (M1, M3)

```
Browser → GET /api/bars?symbol=XAU/USD&tf_s=60&limit=2000
    ▼
server.py Handler._handle_api()                         [server.py:L1296-L1378]
    │
    │ 1. Read history: UDS.read_window(prefer_redis=True)
    │    → Redis snap (tail bars від M1 Poller commits)
    │    → Disk fallback якщо Redis порожній
    │
    │ 2. Read preview_curr: UDS.read_preview_window(symbol, tf_s, 1)
    │    → Redis preview ring → поточний формуючий бар від тіків
    │
    │ 3. Overlay: I3 final>preview guard
    │    if hist_last.open == curr.open AND hist_last.complete → DO NOT replace
    │    if curr.open > hist_last.open → append
    │
    │ 4. Optional stitching: open[i] = close[i-1]
    │
    │ 5. Normalize: _normalize_bars_window_v1()
    │    → drop invalid bars + warnings
    │
    │ 6. JSON response: {ok, symbol, tf_s, bars, boot_id, meta}
```

**Key evidence**: Preview path at [server.py:L1296-L1380](../ui_chart_v3/server.py#L1296-L1380)

### 7.2 Final TFs (M5, M15, M30, H1, H4, D1)

```
Browser → GET /api/bars?symbol=XAU/USD&tf_s=300&limit=2000
    ▼
server.py Handler._handle_api()                         [server.py:L1395-L1458]
    │
    │ 1. Build WindowSpec + ReadPolicy(prefer_redis=cold_load)
    │ 2. UDS.read_window(spec, policy)
    │    Cold load (no since/to):
    │      → Redis tail first (prefer_redis=True)
    │      → Disk fallback якщо Redis порожній/insufficient
    │    Range query (since/to):
    │      → Disk direct (disk_policy="explicit")
    │ 3. Optional stitching
    │ 4. Normalize + JSON response
```

### 7.3 UDS Read Window Logic

```
UDS.read_window(spec, policy)                           [uds.py:L405-L490]
    │
    ├── Check RAM cache (_ram.get_window)
    │   → hit → return
    │
    ├── Cold load + prefer_redis + prime_ready:
    │   → _read_window_redis()                          [uds.py:L1482-L1570]
    │     → Redis tail (or snap) → bars
    │     → Check min_coldload: if bars < target → disk fallback
    │   → fall through to disk if Redis fails/insufficient
    │
    ├── Cold load + !prefer_redis:
    │   → _read_window_disk()                           [uds.py:L1340-L1375]
    │     → SsotJsonl read → sort + dedup → bars
    │
    └── Range query:
        → _disk_allowed(policy) guard → _read_window_disk()
```

---

## 8. Flow G: UI Live Updates

**Endpoint**: `/api/updates`
**Два канали**: Final TFs (UpdatesBus) vs Preview TFs (Preview Ring)

### 8.1 Final TFs (UpdatesBus)

```
Browser → GET /api/updates?symbol=XAU/USD&tf_s=300&since_seq=42
    ▼
server.py Handler._handle_api()                         [server.py:L1055-L1196]
    │
    │ UDS.read_updates(spec)                            [uds.py:L491-L600]
    │   → _RedisUpdatesBus.read_updates()               [uds.py:L1915-L1974]
    │     → Redis LRANGE {ns}:updates:list:{symbol}:{tf_s}
    │     → Filter: seq > since_seq
    │     → Return events + cursor_seq + gap info
    │
    │ Normalize: _normalize_update_events_window_v1()
    │ JSON: {ok, events, cursor_seq, boot_id, ...}
```

### 8.2 Preview TFs (Preview Ring)

```
Browser → GET /api/updates?symbol=XAU/USD&tf_s=60&since_seq=0
    ▼
server.py Handler._handle_api()                         [server.py:L1077-L1078]
    │ tf_s ∈ preview_allowlist → include_preview = True
    │
    │ UDS.read_updates(spec)                            [uds.py:L510-L525]
    │   → RedisLayer.read_preview_updates()
    │     → Redis LRANGE {ns}:preview:updates:{symbol}:{tf_s}:list
    │     → Filter: seq > since_seq
    │     → Return events + cursor_seq
```

### 8.3 Event Schema (updates_v1)

```json
{
  "key": {
    "symbol": "XAU/USD",
    "tf_s": 300,
    "open_ms": 1740150000000
  },
  "bar": {
    "open_time_ms": 1740150000000,
    "close_time_ms": 1740150300000,
    "o": 2650.12, "h": 2650.45, "low": 2649.80, "c": 2650.30,
    "v": 242.0,
    "complete": true,
    "src": "derived"
  },
  "complete": true,
  "source": "derived",
  "event_ts": 1740150300000,
  "seq": 43
}
```

**Evidence**: event creation at [uds.py:L1288-L1297](../runtime/store/uds.py#L1288-L1297)

---

## 9. UDS Commit Pipeline (деталі)

Ключова функція: `UDS.commit_final_bar()` — [uds.py:L619-L675](../runtime/store/uds.py#L619-L675)

### 9.1 Guards (fail-fast)

| Guard | Рядок | Дія |
|-------|-------|-----|
| CandleBar type | L627 | reject "invalid_bar" |
| complete=True | L630 | reject "not_complete" |
| src ∈ FINAL_SOURCES | L638 | reject "non_final_source" |
| Watermark (stale/dup) | L647-L660 | drop + log + metric |

### 9.2 Three-Way Write

Якщо всі guards passed:

1. **Disk**: `_append_to_disk(bar)` → `SsotJsonl.append(bar)` — append-only JSONL — [uds.py:L1226-L1251](../runtime/store/uds.py#L1226-L1251)
2. **Redis Snap**: `_write_redis_snapshot(bar)` → `RedisSnapshotWriter.put_bar(bar)` — snap + tail — [uds.py:L1254-L1274](../runtime/store/uds.py#L1254-L1274)
3. **Updates Bus**: `_publish_update(bar)` → `_RedisUpdatesBus.publish(event)` — list + seq — [uds.py:L1276-L1302](../runtime/store/uds.py#L1276-L1302)

### 9.3 Post-Commit

- Watermark update: `_wm_by_key[(symbol, tf_s)] = bar.open_time_ms` — [uds.py:L665](../runtime/store/uds.py#L665)
- RAM cache update: `_ram.upsert_bar(symbol, tf_s, bar.to_dict())` — [uds.py:L667](../runtime/store/uds.py#L667)
- Preview ring bridge: якщо `tf_s ∈ preview_tf_allowlist` → `_publish_final_to_preview_ring(bar)` — [uds.py:L669-L670](../runtime/store/uds.py#L669-L670)

---

## 10. Redis Topology (ключі та TTL)

**Namespace**: `v3_local` (config.json: `redis.namespace`)
**DB**: 1 (config.json: `redis.db`)

### 10.1 Snapshot Keys (writer: RedisSnapshotWriter)

| Key pattern | Тип | TTL | Writer | Reader |
|-------------|-----|-----|--------|--------|
| `{ns}:ohlcv:snap:{symbol}:{tf_s}` | STRING (JSON) | per TF | Connector / M1 Poller | UI Server |
| `{ns}:ohlcv:tail:{symbol}:{tf_s}` | STRING (JSON, bars[]) | per TF | Connector / M1 Poller | UI Server |
| `{ns}:status:snapshot` | STRING (JSON) | none | Connector / M1 Poller | UI Server (/api/status) |
| `{ns}:prime:ready` | STRING (JSON) | TTL config | Connector | Supervisor AND-gate |
| `{ns}:prime:ready:m1` | STRING (JSON) | TTL config | M1 Poller | Supervisor AND-gate |

### 10.2 Updates Bus Keys

| Key pattern | Тип | Retain | Writer | Reader |
|-------------|-----|--------|--------|--------|
| `{ns}:updates:seq:{symbol}:{tf_s}` | STRING (int counter) | - | UDS writer | UDS reader |
| `{ns}:updates:list:{symbol}:{tf_s}` | LIST (JSON events) | LTRIM retain | UDS writer | UDS reader |

### 10.3 Preview Ring Keys

| Key pattern | Тип | TTL | Writer | Reader |
|-------------|-----|-----|--------|--------|
| `{ns}:preview:curr:{symbol}:{tf_s}` | STRING (JSON) | `curr_ttl_s` | Tick Preview Worker / commit bridge | UI Server |
| `{ns}:preview:tail:{symbol}:{tf_s}` | STRING (JSON, bars[]) | varies | Tick Preview Worker / commit bridge | UI Server |
| `{ns}:preview:updates:{symbol}:{tf_s}:seq` | STRING (int) | - | UDS preview publish | UDS preview read |
| `{ns}:preview:updates:{symbol}:{tf_s}:list` | LIST (JSON events) | retain | UDS preview publish | UDS preview read |

### 10.4 Tick Keys

| Key pattern | Тип | TTL | Writer | Reader |
|-------------|-----|-----|--------|--------|
| `{ns}:tick:last:{symbol}` | STRING (JSON tick_v1) | `last_tick_ttl_s` (30s) | Tick Publisher | - |

### 10.5 Pub/Sub Channels

| Channel | Publisher | Subscriber |
|---------|-----------|------------|
| `{channels.price_tick}` = `fxcm_local:price_tik` | Tick Publisher | Tick Preview Worker |

**Evidence**: Redis key patterns at [redis_keys.py](../runtime/store/redis_keys.py), channel config at [config.json:L210](../config.json#L210)

> **Примітка про формат symbol у Redis-ключах**: UpdatesBus використовує **raw symbol** зі слешем (`XAU/USD`), а всі інші ключі (snap, tail, preview, tick) використовують `symbol_key()` який замінює `/` на `_` (`XAU_USD`). Це неконсистентність, але не критична (обидва writer і reader використовують однаковий формат для кожного типу ключа).

### 10.6 TTL Configuration (config.json)

| TF | TTL (snap) | Tail N |
|----|-----------|--------|
| M1 (60) | 86400s (1d) | 2880 |
| M3 (180) | 86400s (1d) | 1440 |
| M5 (300) | 259200s (3d) | 8000 |
| M15 (900) | 259200s (3d) | 4000 |
| M30 (1800) | 259200s (3d) | 2500 |
| H1 (3600) | 259200s (3d) | 2000 |
| H4 (14400) | 604800s (7d) | 256 |
| D1 (86400) | 604800s (7d) | 128 |

**Evidence**: [config.json:L186-L208](../config.json#L186-L208)

---

## 11. Preview Plane vs Final Plane

Система має **два data planes** для preview TFs (M1=60, M3=180):

### 11.1 Final Plane

- **Writer**: M1 Poller → UDS.commit_final_bar() → Disk + Redis snap + UpdatesBus
- **Bars**: `complete=True`, `src="history"` або `"derived"`
- **Latency**: ~60s від закриття M1 (один poll cycle)
- **Hot path**: Redis snap/tail, disk = recovery

### 11.2 Preview Plane

- **Writer**: Tick Preview Worker → UDS.publish_preview_bar() → Redis preview ring
- **Bars**: `complete=False`, `src="preview_tick"` або `"derived_m1"`
- **Promoted**: `complete=True`, `src="tick_promoted"` (auto-promote on rollover)
- **Latency**: ~200ms (throttled by `min_interval_ms`)
- **Storage**: Redis only (no disk)

### 11.3 Bridge: Final → Preview Ring

Коли M1 Poller комітить final M1/M3 бар:

- `commit_final_bar()` → `_publish_final_to_preview_ring(bar)` — [uds.py:L670-L671](../runtime/store/uds.py#L670-L671)
- UI preview updates отримує final event → applyUpdates() застосовує `final>preview`

### 11.4 I3 Enforcement: Final > Preview

**На стороні API (/api/bars preview path)**:

- Overlay guard: якщо `hist_last.complete == True && curr.open == hist_last.open` → НЕ заміщувати — [server.py:L1330-L1336](../ui_chart_v3/server.py#L1330-L1336)

> **Примітка**: UI overlay код порівнює поле `"time"` (LWC format), не `"open"` — `"time"` це `open_ms` у LWC-схемі UI.

**На стороні UI (applyUpdates)**:

- UI JavaScript: `complete=true` event завжди перемагає `complete=false` для того ж open_ms

---

## 12. Watermark та Drop-Stale Policy

### 12.1 UDS Watermark

- Per-key: `_wm_by_key[(symbol, tf_s)]` — [uds.py](../runtime/store/uds.py)
- Init: `_init_watermark_for_key()` — перший commit ініціалізує з поточного bar
- Policy: `_watermark_drop_reason(open_ms, wm)` → "stale" якщо `open_ms <= wm`
- Результат: `CommitResult(ok=False, reason="stale")` → loud log

### 12.2 M1 Poller Watermark

- `_watermark_ms` — per-symbol, tracks last committed M1 open_ms — [m1_poller.py:L261](../runtime/ingest/polling/m1_poller.py#L261)
- Pre-filter: `bars = [b for b in bars if b.open_time_ms > watermark]` — [m1_poller.py:L453](../runtime/ingest/polling/m1_poller.py#L453)
- Avoids stale spam in UDS

### 12.3 Tick Publisher OOO Guard

- Per-symbol: `_last_tick_ts_ms[symbol]` — [tick_publisher_fxcm.py:L229-L231](../runtime/ingest/tick_publisher_fxcm.py#L229-L231)
- Policy: drop if `tick_ts_ms < last_ts` → `ticks_dropped_out_of_order` metric

### 12.4 Tick Preview Worker OOO Guard

- Per-symbol: `_last_tick_ts_ms[symbol]` — [tick_preview_worker.py:L310-L313](../runtime/ingest/tick_preview_worker.py#L310-L313)
- Policy: same drop + metric

---

## 13. Bootstrap Data Flow (priming)

### 13.1 Connector Bootstrap (D1)

```
bootstrap_and_warmup()                                  [engine_b.py:L302-L370]
    │ Phase 1: Load watermark from disk (_bootstrap_from_disk)
    │ Phase 2: Redis priming from disk (_prime_redis_from_disk)
    │           → UDS.bootstrap_prime_from_disk() per TF
    │           → RedisSnapshotWriter.prime_from_bars()
    │ Phase 3: Set prime:ready signal
    │ Phase 4: Cold-start D1 from broker (_cold_start_base_from_broker)
```

### 13.2 M1 Poller Bootstrap (M1-H4)

```
_bootstrap_warmup() (5 phases)                          [m1_poller.py]
    │ Phase 1: Redis priming M1-H4 from disk
    │           → UDS.bootstrap_prime_from_disk() per TF per symbol
    │ Phase 2: M1Buffer warmup from disk tail
    │ Phase 2b: DeriveEngine buffer warmup
    │ Phase 2c: Cascade catchup (1440 M1)
    │ Phase 3: Tail catchup from broker (fill gap: watermark→expected)
    │
    │ Set prime:ready:m1 signal
```

### 13.3 AND-Gate → UI Start

```
Supervisor._wait_for_prime_ready()                      [app/main.py:L193-L280]
    │ Wait for Redis keys: prime:ready AND prime:ready:m1
    │ Both must have ready=True AND matching boot_id
    │ Timeout: configurable
    ▼
UI Server starts → has Redis data for all TFs
```

---

## 14. Stitching

**Feature**: PREVIOUS_CLOSE stitching для UI display
**Scope**: UI-only transform, не змінює SSOT на диску
**Config**: `ui_stitching_enabled: true` — [config.json:L73](../config.json#L73)

### 14.1 Logic

```python
_stitch_bars_previous_close(bars)                       [server.py:L1229-L1258]
    for i in range(1, len(bars)):
        if abs(bars[i].open - bars[i-1].close) > 0.0001:
            bars[i].open = bars[i-1].close
            # adjust high/low if open outside range
```

### 14.2 Where Applied

- `/api/bars` preview path: after history + preview overlay — [server.py:L1343-L1346](../ui_chart_v3/server.py#L1343-L1346)
- `/api/bars` final path: after UDS.read_window() — [server.py:L1428-L1432](../ui_chart_v3/server.py#L1428-L1432)
- `meta.extensions.stitching = true` injected when active

### 14.3 Мотивація

FXCM History API повертає `open` з FIRST_TICK для першого бару кожного batch.
Це створює цінові розриви між batch-ами. Stitching: `open[i] = close[i-1]` — як TradingView.

---

## 15. Зведена матриця: TF × Source × Destination

| TF | Source Process | Source Data | UDS Commit | Disk Path | Redis Snap | UpdatesBus | Preview Ring |
|----|---------------|-------------|------------|-----------|------------|------------|--------------|
| **M1 (60)** | M1 Poller | FXCM History M1 | `commit_final_bar` | `tf_60/` | ✅ snap+tail | ✅ | ✅ (bridge) |
| **M1 (60) preview** | Tick Preview | FXCM tick stream | `publish_preview_bar` | ❌ | ❌ | ❌ | ✅ curr+tail+events |
| **M3 (180)** | DeriveEngine | 3×M1 | `commit_final_bar` | `tf_180/` | ✅ snap+tail | ✅ | ✅ (bridge) |
| **M3 (180) preview** | Tick Preview | M1 preview → M3 | `publish_preview_bar` | ❌ | ❌ | ❌ | ✅ |
| **M5 (300)** | DeriveEngine | 5×M1 | `commit_final_bar` | `tf_300/` | ✅ | ✅ | ❌ |
| **M15 (900)** | DeriveEngine | 3×M5 | `commit_final_bar` | `tf_900/` | ✅ | ✅ | ❌ |
| **M30 (1800)** | DeriveEngine | 2×M15 | `commit_final_bar` | `tf_1800/` | ✅ | ✅ | ❌ |
| **H1 (3600)** | DeriveEngine | 2×M30 | `commit_final_bar` | `tf_3600/` | ✅ | ✅ | ❌ |
| **H4 (14400)** | DeriveEngine | 4×H1 | `commit_final_bar` | `tf_14400/` | ✅ | ✅ | ❌ |
| **D1 (86400)** | Connector | FXCM History D1 | `commit_final_bar` | `tf_86400/` | ✅ | ✅ | ❌ |

### Source → Destination Legend

- **Disk**: SSOT, append-only JSONL files at `data_v3/{symbol}/tf_{seconds}/part-{date}.jsonl`
- **Redis Snap**: `{ns}:ohlcv:snap:{sym}:{tf}` + `{ns}:ohlcv:tail:{sym}:{tf}` — hot path for reads
- **UpdatesBus**: `{ns}:updates:list:{sym}:{tf}` — event stream for UI live updates (final TFs)
- **Preview Ring**: `{ns}:preview:{curr|tail|updates}:{sym}:{tf}` — preview events for UI (M1/M3)

---

## 16. Знахідки та потенційні проблеми

### Знахідка 1: Два паралельні event streams (UpdatesBus vs Preview Ring)

Final TFs (M5-D1) використовують `_RedisUpdatesBus` (Redis list per symbol:tf), а Preview TFs (M1/M3) використовують Preview Ring (окремий набір Redis ключів з власним seq counter). UI має знати який endpoint/plane використовувати через `preview_tf_allowlist`. Bridge mechanism (`_publish_final_to_preview_ring`) забезпечує що final M1/M3 бари також потрапляють у preview ring.

**Ризик**: Якщо bridge failed/late → preview ring not aware of final bar → stale preview displayed дольше.

### Знахідка 2: Tick channel name має typo

Config: `"price_tick": "fxcm_local:price_tik"` — "tik" замість "tick". Функціонально не критично (publisher і subscriber читають той же config), але може збивати при дебагу.

**Evidence**: [config.json:L210](../config.json#L210)

### Знахідка 3: Preview bars не мають volume (v=0)

`_M1toM3Buffer` і `TickAggregator` не мають доступу до volume з тиків (FXCM stream не надає volume). Всі preview бари мають `v=0.0`, що розрізняє їх від final барів.

### Знахідка 4: DeriveEngine shared UDS — no file race guarantee

DeriveEngine використовує SHARED UDS instance з M1 Poller (не створює свій). Це правильно для уникнення file race. Але per-symbol lock в DeriveEngine НЕ синхронізований з M1 Poller poll_once() — вони в одному thread (M1 Poller calls DeriveEngine.on_bar() синхронно після commit).

**Conclusion**: Race неможливий через синхронний виклик (`_ingest_bar` → commit → `engine.on_bar()` — все в одному control flow).

### Знахідка 5: Redis snap vs UpdatesBus — independent write paths

`commit_final_bar()` робить 3 незалежні операції (disk, redis snap, updates bus). Якщо одна з них fails, інші все одно виконуються. `CommitResult` повертає `ssot_written`, `redis_written`, `updates_published` окремо, але `ok` визначається ЛИШЕ по `ssot_written`.

**Takeaway**: Redis failure не блокує disk writes (degraded-but-loud). Але: якщо Redis snap written а updates bus failed → UI бачить стару history (snap OK) але не отримує live event.

### Знахідка 6: Two M3 derive paths (inline fallback)

Якщо DeriveEngine connected → M3 derives through cascade (`on_bar`).
Якщо DeriveEngine NOT connected → inline fallback через `_derive_m3()` в m1_poller — [m1_poller.py:L384-L391](../runtime/ingest/polling/m1_poller.py#L384-L391).

**Current state**: DeriveEngine завжди connected (Phase 5), але fallback code залишається.

### Знахідка 7: No disk hot-path for /api/updates

`read_updates()` читає ЛИШЕ з Redis (UpdatesBus або Preview Ring). Disk не використовується для updates — це відповідає інваріанту I1: "disk = recovery, не hot-path для updates".

**Evidence**: [uds.py:L491-L600](../runtime/store/uds.py#L491-L600) — тільки Redis calls.

### Знахідка 8: RAM cache populated from updates read

В `read_updates()` для non-preview TFs, final events зберігаються в RAM cache: `_ram.upsert_bar()` — [uds.py:L577-L580](../runtime/store/uds.py#L577-L580). Це забезпечує що наступний `read_window()` може потрапити в RAM hit без Redis/disk.

### Знахідка 9: boot_id mismatch detection

Updates response включає `boot_id`. Якщо UI зберігає boot_id і отримує інший → має робити full reload. Supervisor AND-gate також перевіряє boot_id match — [app/main.py:L193-L280](../app/main.py#L193-L280).

### Знахідка 10: Stitching applied двічі для preview TFs

Preview path: stitching applied after `hist_bars + preview overlay` — [server.py:L1343-L1346](../ui_chart_v3/server.py#L1343-L1346).
Final path: stitching applied after `read_window()` — [server.py:L1428-L1432](../ui_chart_v3/server.py#L1428-L1432).

Обидва шляхи правильно застосовують stitching один раз, немає double-apply. Але: preview bars і history bars в overlay можуть мати "gap" на стику (preview open != last history close) — stitching це виправляє.

### Знахідка 11: Three independent Redis clients per process

- Connector: RedisSnapshotWriter (snap) + _RedisUpdatesBus (updates) = 2 clients
- M1 Poller: shared UDS з RedisSnapshotWriter + _RedisUpdatesBus + RedisLayer (preview) = 3 clients
- Tick Preview Worker: UDS з RedisLayer (preview) = 1 client
- Tick Publisher: direct redis client = 1 client
- UI Server: UDS reader з RedisLayer + _RedisUpdatesBus = 2 clients

Усі ці clients підключаються до одного Redis instance (127.0.0.1:6379 db=1).

### Знахідка 12: min_coldload_bars vs tail_n mismatch possibility

Config має `min_coldload_bars_by_tf_s` (скільки барів потрібно для cold load) і `redis.tail_n_by_tf_s` (скільки барів зберігати в tail). Для коректної роботи `tail_n >= min_coldload`. Поточні значення:

| TF | min_coldload | tail_n | OK? |
|----|-------------|--------|-----|
| M1 | 1440 | 2880 | ✅ |
| M3 | 480 | 1440 | ✅ |
| M5 | 2016 | 8000 | ✅ |
| M15 | 672 | 4000 | ✅ |
| M30 | 150 | 2500 | ✅ |
| H1 | 720 | 2000 | ✅ |
| H4 | 1080 | 256 | ⚠️ 1080 > 256 |
| D1 | 365 | 128 | ⚠️ 365 > 128 |

**H4 та D1**: `min_coldload > tail_n` — Redis tail не може задовольнити cold load → disk fallback завжди для великих limits. Не critical (disk fallback працює), але порушує принцип "disk не hot-path після bootstrap".

---

> **Наступний документ**: P3 (UDS/Store internals — RAM layer, disk layer, Redis integration, watermark model).
