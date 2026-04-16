---
applyTo: "runtime/**"
---

# runtime/ — I/O Layer

**SSOT**: `.github/copilot-instructions.md` §C (Architecture).

## Жорсткі правила

### I0 — Dependency Rule
- Може імпортувати `core/`
- **НЕ** імпортує `tools/` (tools — ізольовано, одноразові утиліти)
- **НЕ** імпортує `ui*/` (UI говорить з runtime через HTTP/WS only)

### I1 — UDS = вузька талія
- Writes OHLCV → **тільки** через `runtime/store/uds.py`
- Прямі `redis.zadd(...)` / `redis.set(...)` для OHLCV = **заборонено**
- Reads з disk/Redis для OHLCV → через UDS API (`get_window()`, `get_tail()`)

### I2 — Time geometry (Redis boundary)
- На межі Redis write: `close_ms = open_ms + tf_s * 1000 - 1` (end-incl)
- CandleBar залишається end-excl. Конвертація тільки у `redis_layer.py`

### I3 — Final > Preview (NoMix)
- Один ключ `(symbol, tf, open_ms)` → **один** final source
- Mixing preview і final у одному місці = split-brain → ADR + patch

### I4 — Один update-потік
- UI оновлюється через `/api/updates` (Redis bus only)
- Паралельні endpoints "для preview" і "для final" заборонені

### I5 — Degraded-but-loud
- `except:` без явного error code + log = заборонено (X9)
- Tight loop з однаковим error без backoff = заборонено (X10)
- Будь-яка деградація → `degraded[]` / `warnings[]` / `errors[]` у status

## Підшари

### runtime/store/
- UDS SSOT. Всі правила I1 тут найжорсткіші.
- `layers/disk_layer.py`, `layers/ram_layer.py`, `layers/redis_layer.py` — тільки UDS їх дергає.

### runtime/ingest/
- `broker_sidecar.py` = `.venv37/` (Python 3.7, forexconnect SDK). НЕ імпортує нічого з головного `.venv/` (ADR-0016).
- `m1_ingestion_worker.py` = `.venv/` (Python 3.11+). Читає від sidecar через Redis IPC.
- `tick_publisher_fxcm.py` — **DEPRECATED**. FXCM ticks через broker_sidecar._TickRelay.
- `binance_ingest_worker.py` = Binance M1 (24/7, ADR-0037).

### runtime/smc/
- `smc_runner.py` — І/О wrapper над pure `core/smc/`. Публікує deltas у WS frames.
- Callback pattern — **не** окремий Redis pub/sub для SMC (shared memory через ws_server process).

### runtime/ws/
- `ws_server.py` port 8000. Same-origin з UI v4.
- SmcRunner wiring — у цьому процесі.

## Pattern reminders

- Watermark + drop_stale=true for all event streams (X10 enforcement)
- `req_id` + idempotency для кожної команди (I1 torgiv)
- Rate-limit logs для repeated WARN/ERROR (suppressed counter)
