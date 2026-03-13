# ADR-0037: Binance Futures — Second Broker (BTC/ETH Live Ingest)

- **Статус**: Proposed
- **Дата**: 2026-03-13
- **Автор**: R_ARCHITECT
- **Initiative**: `binance_broker_v1`
- **Пов'язані ADR**: ADR-0001 (UDS), ADR-0002 (DeriveChain), ADR-0016 (dual-venv), ADR-0023 (D1 derive), ADR-0025 (multi-symbol quality)

---

## 1. Контекст і проблема

XAU/USD (FXCM) — єдиний активний символ. Коли ринок золота закритий
(щоденні 21:00–22:00 UTC перерви; вихідні п'ятниця 20:55 → неділя 22:00),
система не має live даних для роботи.

BTC та ETH торгуються **24/7/365** на Binance Futures — це дає безперервний
потік M1 барів і тиків навіть коли FX-ринок спить.

**Проблема**: як додати другий брокер (Binance Futures) із мінімальним blast radius,
зберігаючи всі платформенні інваріанти I0–I6?

**FACTS**:

- [VERIFIED runtime/ingest/derive_engine.py] DeriveEngine — symbol-agnostic. Приймає `symbols: List[str]`.
- [VERIFIED runtime/store/uds.py] UDS — symbol-agnostic. Per-symbol instances.
- [VERIFIED runtime/ingest/tick_agg.py] TickAggregator — symbol-agnostic. State keyed by `(symbol, tf_s)`.
- [VERIFIED runtime/store/redis_keys.py] `symbol_key("BTCUSDT")` → `"BTCUSDT"` (no transform, no slash in symbol).
- [VERIFIED runtime/ingest/broker/fxcm/provider.py] FxcmHistoryProvider — broker-specific, isolated in `broker/fxcm/`.
- [VERIFIED runtime/ingest/m1_ingestion_worker.py] build_ingestion_worker() — рекомпонується через BrokerRedisProxy; для Binance Redis IPC не потрібен (`.venv` напряму).
- [VERIFIED core/] — zero FXCM imports. Pure logic layer.
- [VERIFIED research/ws_worker (1).py] — робочий Binance WS kline_1m consumer: `wss://fstream.binance.com/stream?streams={s}@kline_1m`, `msg.data.k`, `k["x"]` = final.
- [VERIFIED research/raw_data (1).py] — робочий REST klines fetcher: `https://fapi.binance.com/fapi/v1/klines`.

---

## 2. Обмеження (Constraints)

| Constraint | Деталі |
|------------|--------|
| **I0 Dependency Rule** | `core/` не імпортує `runtime/`. Новий broker — тільки у `runtime/ingest/broker/binance/`. |
| **I1 UDS = вузька талія** | Binance M1 → UDS commit → derive cascade. Жодних writes поза UDS. |
| **I2 Геометрія часу** | CandleBar end-excl. Binance kline `t` = open_time_ms. `close_time_ms = open_time_ms + 60000`. |
| **I3 Final > Preview** | WS partial klines → `complete=false`, REST final → `complete=true`. Один source per key. |
| **I4 Один update-потік** | UI отримує Binance бари через той самий `/api/updates` + WS delta. No new channels. |
| **I5 Degraded-but-loud** | WS disconnect, REST failure → explicit log + degraded[]. No silent fallback. |
| **Calendar** | Crypto = 24/7. MarketCalendar(enabled=False). Жодних break checks. |
| **Anchor H4/D1** | Crypto має no FX session. `day_anchor_offset_s = 0` → H4 бакети: 00, 04, 08, 12, 16, 20 UTC. D1 = midnight-aligned. |
| **Бюджет** | ≤4 нові файли runtime. ≤1 новий файл config. 0 змін у `core/`. |
| **Зворотна сумісність** | Existing FXCM pipeline untouched. Binance = additive. |
| **Python** | `.venv` (3.14). `python-binance` works on 3.11+. No `.venv37` needed → no sidecar Redis IPC. |

---

## 3. Розглянуті альтернативи

### Альтернатива A: Direct Worker (BinanceHistoryProvider + own worker)

- **Суть**: Створити `BinanceHistoryProvider` з інтерфейсом `fetch_last_n_m1()`, власний `binance_ingest_worker.py` що використовує існуючий M1PollerRunner + DeriveEngine, і `binance_tick_publisher.py` для WS live stream.
- **Pros**: Простота (no IPC queue), запускається в `.venv` напряму, pattern match з FXCM tick_publisher/m1_poller.
- **Cons**: Дублювання `build_ingestion_worker` logic (calendar, UDS, derive wiring).
- **Blast radius**: 4 нових файли в `runtime/ingest/`, config.json, app/main.py.
- **LOC estimate**: ~350 total, per-slice ≤100.

### Альтернатива B: Sidecar Pattern (як FXCM ADR-0016)

- **Суть**: `binance_sidecar.py` в `.venv`, `m1_ingestion_worker.py` отримує дані через Redis queue (як BrokerRedisProxy для FXCM).
- **Pros**: Уніфікація: всі брокери через один Redis IPC contract.
- **Cons**: Зайва складність — Binance SDK не потребує Python 3.7, отже sidecar isolation безглузда. +latency від Redis queue hop. Складніший debug.
- **Blast radius**: 3 нових файли + зміни в m1_ingestion_worker (multi-broker routing).
- **LOC estimate**: ~400 total, складніший.

### Альтернатива C: Абстрактний BrokerProvider protocol

- **Суть**: Визначити Protocol у `core/` (`BrokerProvider`), FXCM і Binance реалізують його. Один generic ingestion worker.
- **Pros**: Максимальна уніфікація. Future-proof для 3+ брокерів.
- **Cons**: Over-engineering (YAGNI — лише 2 брокери). Рефакторинг FXCM provider (blast radius). Protocol в `core/` ризикує втягнути I/O types (I0 violation).
- **Blast radius**: 1 новий файл core/ + refactor fxcm/provider + refactor m1_ingestion_worker + 3 нових файли.
- **LOC estimate**: ~500+, піклуватись про зворотну сумісність FXCM.

### Вибір: Альтернатива A (Direct Worker)

**Обґрунтування**: Мінімальний blast radius. Binance не потребує Python 3.7 → sidecar pattern (Alt B) — зайва складність. Abstract protocol (Alt C) — YAGNI при 2 брокерах, плюс ризик I0 violation. Alt A дає ізольований, простий pipeline що повторює перевірений FXCM pattern без його legacy constraints.

---

## 4. Рішення (деталі)

### 4.1 Canonical Symbol Names

| Binance API | Internal canonical | `symbol_key()` | `data_v3/` folder |
|---------|-------------------|----------------|-------------------|
| `BTCUSDT` | `BTCUSDT` | `BTCUSDT` | `data_v3/BTCUSDT/` |
| `ETHUSDT` | `ETHUSDT` | `ETHUSDT` | `data_v3/ETHUSDT/` |

No slash in symbol → `symbol_key()` pass-through. No mapping needed.

### 4.2 Data Flow

```
Binance Futures WS          binance_tick_publisher
wss://fstream.binance.com  ──────────────────────►  Redis pub/sub
  btcusdt@kline_1m                                   channels.price_tick
  ethusdt@kline_1m                                   (same channel as FXCM)
                                                           │
                                                           ▼
                                                   tick_preview_worker
                                                   (existing, symbol-agnostic)
                                                           │
                                                           ▼
                                                   Preview bars → UDS → UI

Binance Futures REST        binance_ingest_worker
fapi.binance.com/fapi/v1/  ──────────────────────►  BinanceHistoryProvider
  klines?symbol=BTCUSDT        M1PollerRunner           .fetch_last_n_m1()
                                    │                         │
                                    ▼                         │
                               DeriveEngine                   ▼
                           M1→M3→M5→M15→M30→H1→H4         CandleBar list
                                    │
                                    ▼
                                UDS commit → Redis → UI
```

### 4.3 BinanceHistoryProvider (runtime/ingest/broker/binance/provider.py)

```python
class BinanceHistoryProvider:
    """Drop-in interface matching FxcmHistoryProvider for M1PollerRunner."""

    def __init__(self, api_key: str, api_secret: str) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *args) -> None: ...
    def fetch_last_n_m1(self, symbol: str, n: int,
                        date_to_utc: datetime | None = None) -> list[CandleBar]: ...
```

- REST: `GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=N`
- Response: `[[open_time, o, h, l, c, v, close_time, ...], ...]`
- Mapping: `open_time_ms` = `row[0]`, `o/h/l/c/v` = `row[1..5]`, `close_time_ms = open_time_ms + 60_000`
- Backoff retry: 3 attempts, 2s sleep, timeout 10s per request.
- Auth: HMAC-SHA256 signed (python-binance Client handles this).

### 4.4 binance_ingest_worker.py

Reuses `M1SymbolPoller` + `M1PollerRunner` from `m1_poller.py`:

- `BinanceHistoryProvider` as provider (drop-in for FXCM)
- Config: `config.json` → `binance.symbols`, `binance.enabled`
- Calendar: `MarketCalendar(enabled=False)` — 24/7
- DeriveEngine: `anchor_offset_s=0`, `d1_anchor_offset_s=0`
- UDS per symbol: `build_uds_from_config()`

### 4.5 binance_tick_publisher.py

WebSocket live stream for forming (preview) candles:

- URL: `wss://fstream.binance.com/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m`
- Message: `{"stream":"btcusdt@kline_1m","data":{"k":{...}}}`
- Fields: `k["s"]` = symbol (uppercase), `k["t"]` = open_time ms, `k["o/h/l/c/v"]` = OHLCV, `k["x"]` = is_final
- Publish to Redis: same `channels.price_tick` channel → tick_preview_worker picks up
- Tick format: `{"symbol":"BTCUSDT","bid":<price>,"ask":<price>,"ts":<epoch_ms>}` (matching existing tick contract)

### 4.6 Config (config.json)

```json
{
  "binance": {
    "enabled": false,
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "api_base_url": "https://fapi.binance.com",
    "ws_base_url": "wss://fstream.binance.com",
    "poll_interval_s": 8
  },
  "market_calendar_by_group": {
    "crypto_24x7": {
      "market_weekend_open_dow": 0,
      "market_weekend_open_hm": "00:00",
      "market_weekend_close_dow": 6,
      "market_weekend_close_hm": "23:59",
      "market_daily_break_start_hm": "00:00",
      "market_daily_break_end_hm": "00:00"
    }
  },
  "market_calendar_symbol_groups": {
    "BTCUSDT": "crypto_24x7",
    "ETHUSDT": "crypto_24x7"
  }
}
```

Note: `crypto_24x7` calendar has `daily_break_start == daily_break_end` →
`_is_in_break()` returns False → always trading. Weekend wrap covers full week.

### 4.7 Supervisor (app/main.py)

Two new modes: `binance_ingest_worker`, `binance_tick_publisher`.
**Not** in `BROKER_MODULES` (they run in `.venv`, not `.venv37`).
In `--mode all`: started only if `binance.enabled = true`.

### 4.8 Dependencies

`requirements.txt`: add `python-binance>=1.0.19`
`.env` / `.env.example`: add `BINANCE_API_KEY=`, `BINANCE_API_SECRET=`

---

## 5. P-Slices (план реалізації)

| Slice | Scope | Files | LOC | Інваріант | Verify | Rollback |
|-------|-------|-------|-----|-----------|--------|----------|
| **P0** | BinanceHistoryProvider + config + deps | `broker/binance/provider.py`, `broker/binance/__init__.py`, `config.json`, `requirements.txt`, `.env.example` | ~100 | I0 (pure runtime, no core imports backward) | `pytest tests/test_binance_provider.py` | `git checkout` |
| **P1** | binance_ingest_worker (M1 poll + derive) | `binance_ingest_worker.py` | ~100 | I1 (UDS writes), I2 (time geometry), I5 (degraded-loud) | `python -m runtime.ingest.binance_ingest_worker` → UDS commit logs | `git checkout` |
| **P2** | binance_tick_publisher (WS live) | `binance_tick_publisher.py` | ~100 | I4 (same update channel), I5 (reconnect-loud) | `python -m runtime.ingest.binance_tick_publisher` → Redis pub/sub logs | `git checkout` |
| **P3** | Supervisor wiring (app/main.py) | `app/main.py` | ~30 | I5 (degraded-loud if binance.enabled but no provider) | `python -m app.main --mode all` → binance workers started | `git checkout app/main.py` |

---

## 6. Наслідки

### Що змінюється

- `config.json`: new `binance` section + `crypto_24x7` calendar group + 2 symbol-group mappings
- `app/main.py`: 2 new modes in choices + spawn logic (~30 LOC)
- `requirements.txt`: +1 dependency
- `.env.example`: +2 keys
- `data_v3/`: new dirs `BTCUSDT/`, `ETHUSDT/` (auto-created by UDS on first write)
- Redis: new keys `v3_local:preview:curr:BTCUSDT:*`, `v3_local:preview:curr:ETHUSDT:*`

### Що НЕ змінюється

- `core/` — zero changes (pure, broker-agnostic)
- `runtime/store/uds.py` — already symbol-agnostic
- `runtime/ingest/derive_engine.py` — already symbol-agnostic
- `runtime/ingest/tick_preview_worker.py` — already symbol-agnostic
- `runtime/ingest/tick_agg.py` — already symbol-agnostic
- `runtime/ws/ws_server.py` — already symbol-agnostic
- `runtime/ingest/broker/fxcm/` — untouched
- FXCM pipeline (broker_sidecar, m1_ingestion_worker, tick_publisher_fxcm) — untouched

### Нові інваріанти

- Жодних нових платформенних інваріантів. Binance підпорядковується I0–I6.

### Performance / SLO

- binance_tick_publisher: steady ~2 WS messages/sec per symbol (Binance 1m kline updates ~every 2s)
- binance_ingest_worker: 1 REST call per symbol per minute ≈ 2 requests/min
- Redis impact: minimal (+2 symbols × 8 TFs = 16 additional key families)
- UDS disk: ~2 JSONL files per TF per symbol (standard)

---

## 7. Rollback

Per-slice rollback = `git checkout` відповідних файлів.

Full rollback:

1. `config.json`: set `binance.enabled: false` → workers won't start
2. `app/main.py`: remove `binance_*` modes (or just disable in config)
3. Delete `data_v3/BTCUSDT/`, `data_v3/ETHUSDT/` if needed
4. Redis: `redis-cli -n 1 KEYS "v3_local:*:BTCUSDT:*" | xargs redis-cli -n 1 DEL`
5. `pip uninstall python-binance` (optional)

---

## 8. Open Questions

| # | Питання | Хто перевіряє | Дедлайн |
|---|---------|---------------|---------|
| Q1 | Чи Binance Futures `fapi` REST klines потребує auth для public endpoints (klines)? python-binance Client дозволяє unauthenticated calls для market data. Якщо так — можна спростити до unauthenticated. | R_PATCH_MASTER при P0 | При реалізації P0 |
| Q2 | `python-binance` сумісність з Python 3.14 — протестувати `pip install` у `.venv`. | R_PATCH_MASTER при P0 | При реалізації P0 |
| Q3 | SMC overlay для BTC/ETH — окремий future initiative, не scope цього ADR. | R_SMC_CHIEF | Окремий ADR |
