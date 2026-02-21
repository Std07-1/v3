# P4: API Surface — HTTP endpoints, контракти, guards, клієнтська інтеграція

> **Документ**: code-first, кожен факт має evidence `(file:line)`.
> **Дата**: 2026-02-21
> **Scope**: усі HTTP endpoints `ui_chart_v3/server.py`, нормалізація відповідей, contract guards, клієнтське використання (app.js).
> **Залежності**: P1 (Process Inventory), P2 (Data Flow), P3 (UDS/Store).

---

## Зміст

- [1. Архітектура HTTP шару](#1-архітектура-http-шару)
- [2. Таблиця endpoint-ів](#2-таблиця-endpoint-ів)
- [3. /api/config — Policy SSOT](#3-apiconfig)
- [4. /api/status — UDS діагностика](#4-apistatus)
- [5. /api/symbols — Перелік символів](#5-apisymbols)
- [6. /api/bars — Вікно барів (cold-load, scrollback)](#6-apibars)
- [7. /api/latest — Alias /api/bars](#7-apilatest)
- [8. /api/updates — Інкрементальні upsert events](#8-apiupdates)
- [9. /api/overlay — Ephemeral preview bar](#9-apioverlay)
- [10. /api/gaps — Gap report](#10-apigaps)
- [11. Нормалізаційний pipeline (window_v1)](#11-нормалізаційний-pipeline)
- [12. Contract guards](#12-contract-guards)
- [13. Symbol canonicalization](#13-symbol-canonicalization)
- [14. Limit clamping та TF caps](#14-limit-clamping)
- [15. Stitching (PREVIOUS_CLOSE)](#15-stitching)
- [16. Warmup та bootstrap](#16-warmup-та-bootstrap)
- [17. Config caching та rate-limiting](#17-config-caching)
- [18. Клієнтська інтеграція (app.js)](#18-клієнтська-інтеграція)
- [19. Polling та backoff strategy](#19-polling-та-backoff)
- [20. Scrollback (lazy history loading)](#20-scrollback)
- [21. Overlay polling (client)](#21-overlay-polling)
- [22. Зведена матриця: endpoint × guard × source × client function](#22-зведена-матриця)
- [23. Знахідки та потенційні проблеми](#23-знахідки)

---

## 1. Архітектура HTTP шару

```
┌──────────── UI Server (ui_chart_v3/server.py) ──────────────┐
│                                                             │
│   ThreadingHTTPServer (host:port, default 127.0.0.1:8089)   │
│          │                                                  │
│          ├─ Handler (SimpleHTTPRequestHandler)              │
│          │     ├─ do_GET() → static files (/, .html, .js)   │
│          │     └─ _handle_api() → /api/* routing            │
│          │            ├─ /api/config                        │
│          │            ├─ /api/status                        │
│          │            ├─ /api/symbols                       │
│          │            ├─ /api/bars + /api/latest            │
│          │            ├─ /api/updates                       │
│          │            ├─ /api/overlay                       │
│          │            ├─ /api/gaps                          │
│          │            └─ unknown → 400 "unknown_endpoint"   │
│          │                                                  │
│          ├─ UDS (role="reader") → RuntimeError on write     │
│          ├─ _cfg_cache (config.json mtime cache)            │
│          └─ _boot_id (uuid4, per process)                   │
└─────────────────────────────────────────────────────────────┘
```

**Ключові інваріанти**:

- **Same-origin**: UI + API = один процес, один порт. Evidence: `server.py:1834-1838` (`ThreadingHTTPServer`).
- **Read-only**: UDS ініціалізується з `role="reader"`. Evidence: `server.py:1842` (`build_uds_from_config(..., role="reader")`).
- **No-store**: всі API відповіді мають `Cache-Control: no-store`. Evidence: `server.py:1000`.
- **Cookie**: `aione_client_id` встановлюється для трекінгу. Evidence: `server.py:935-945`.

---

## 2. Таблиця endpoint-ів

| # | Endpoint | Метод | Routing line | Призначення | Контракт | Джерело даних |
|---|----------|-------|-------------|-------------|----------|---------------|
| 1 | `/api/config` | GET | `server.py:1029` | Policy SSOT для UI | — | `config.json` + `uds.snapshot_status()` |
| 2 | `/api/status` | GET | `server.py:1047` | Діагностика UDS | — | `uds.snapshot_status()` |
| 3 | `/api/updates` | GET | `server.py:1055` | Інкрементальні events | `updates_v1` | `uds.read_updates()` |
| 4 | `/api/symbols` | GET | `server.py:1178` | Список символів | — | `data_root` directory listing |
| 5 | `/api/gaps` | GET | `server.py:1182` | Gap report | — | `reports/tail_audit/*.json` |
| 6 | `/api/bars` | GET | `server.py:1224` | Вікно барів (холодне завантаження) | `window_v1` | UDS `read_window()` / `read_preview_window()` |
| 7 | `/api/latest` | GET | `server.py:1224` | Alias /api/bars + `after_open_ms` | `window_v1` | UDS `read_window()` |
| 8 | `/api/overlay` | GET | `server.py:1487` | Ephemeral preview bar | — | UDS `read_preview_window()` |

> **Routing**: `_handle_api()` стартує на `server.py:1011`. Всі endpoint-и — послідовні `if path ==` перевірки.

---

## 3. /api/config — Policy SSOT

### 3.1 Призначення

UI завантажує policy один раз на старті: ліміти, TF allowlist, preview allowlist, cold-start бюджети.

### 3.2 Параметри

Немає query параметрів.

### 3.3 Response body

```python
{
    "ok": True,
    "ui_debug": bool,                    # config.json → ui_debug
    "policy_version": str,               # _POLICY_VERSION = "20260214-201"
    "build_id": str,                     # _BUILD_ID = "20260214-201"
    "window_policy": {
        "cold_start_bars_by_tf": dict,   # TF→int, _COLD_START_BARS_BY_TF
        "warmup_bars_by_tf": dict,       # TF→int, _WARMUP_BARS_BY_TF
        "redis_tail_by_tf": dict,        # TF→int, від snapshot_status()
        "max_bars_cap": int,             # _MAX_BARS_CAP = 20000
        "scrollback_chunk_by_tf": dict,  # {300:1000, 900:1000, ...}
        "tf_cap": dict,                  # TF→int, _TF_CAP
    },
    "tf_allowlist": list[int],           # sorted
    "preview_tf_allowlist": list[int],   # sorted (60, 180)
    "config_invalid": bool,              # True якщо policy sanity порушено
    "warnings": list[str],               # опціонально
}
```

> Evidence: `server.py:337-380` (`_build_window_policy_payload`), `server.py:1029-1044`.

### 3.4 Sanity check

`_policy_sanity_issues()` перевіряє (`server.py:325-335`):

- Для `_SHOW_IMMEDIATELY_TFS` (14400, 86400): `warmup < cold_start` → warning.
- `_MAX_BARS_CAP < max(_TF_CAP.values())` → warning.

Якщо є issues → `config_invalid=True`, `warnings[]` містить деталі, лог `POLICY_CONFIG_INVALID`.

### 3.5 Клієнтська поведінка

`loadUiConfig()` (`app.js:513`): завантажує config, зберігає `uiPolicy`. Якщо `/api/config` недоступний або `window_policy` відсутній → `policyFallbackActive=true`, UI використовує hard-coded константи (`COLD_START_BARS_BY_TF`, `MAX_BARS_CAP`).

---

## 4. /api/status — UDS діагностика

### 4.1 Призначення

Моніторинг стану UDS, Redis, bootstrap readiness.

### 4.2 Response body

```python
{
    "ok": True,
    "status": uds.snapshot_status(),  # dict з boot_id, prime_ready, ram_stats, тощо
}
```

> Evidence: `server.py:1047-1052`.

---

## 5. /api/symbols — Перелік символів

### 5.1 Механіка

`_list_symbols(data_root)` (`server.py:531-541`): сканує `data_root` директорію, для кожної піддиректорії заміняє `_` на `/`.

### 5.2 Response

```python
{
    "ok": True,
    "symbols": ["EUSTX50", "GBP/CAD", "GER30", ...]  # sorted
}
```

> Evidence: `server.py:1178-1179`.

### 5.3 Клієнтська поведінка

`loadSymbols()` (`app.js:984`): заповнює `<select>` символами. Fallback `XAU/USD` якщо список порожній.

---

## 6. /api/bars — Вікно барів (cold-load, scrollback)

### 6.1 Parametri

| Параметр | Тип | Required | Default | Опис | Evidence |
|----------|-----|----------|---------|------|----------|
| `symbol` | str | ✓ | — | Символ (XAU/USD або XAU_USD) | `server.py:1253` |
| `tf_s` | int | ✓ | 60 | Timeframe у секундах | `server.py:1255` |
| `limit` | int | — | 2000 | Кількість барів | `server.py:1256` |
| `align` | str | — | "fxcm" | Ігнорується (ADR-0002 Phase 3) | `server.py:1261` |
| `force_disk` | 0/1 | — | 0 | **ІГНОРУЄТЬСЯ** з warning | `server.py:1274` |
| `prefer_redis` | 0/1 | — | 0 | **ІГНОРУЄТЬСЯ** з warning | `server.py:1275` |
| `epoch` | int | — | — | Epoch guard для cancellation | `server.py:1277` |
| `since_open_ms` | int | — | — | Бари з open_ms ≥ value | `server.py:1387` |
| `to_open_ms` | int | — | — | Бари з open_ms < value (scrollback) | `server.py:1388` |

### 6.2 Два code path-и

**Path A: Preview TF** (tf_s ∈ preview_allowlist — 60, 180):

1. Читає history з Redis snap через `uds.read_window()` з `prefer_redis=True, disk_policy="explicit"`.
2. Читає overlay preview_curr через `uds.read_preview_window(symbol, tf_s, 1, include_current=True)`.
3. Merge: якщо preview open == last_hist open і last_hist !complete → заміна; якщо preview open > last_hist open → append.
4. **I3 guard**: якщо `last_hist.complete == True` → preview бар **не** заміщує final.
5. `meta.extensions.plane = "preview+history"`.

> Evidence: `server.py:1296-1377`.

**Path B: Final TF** (tf_s ∉ preview_allowlist — 300, 900, ..., 86400):

1. Будує `WindowSpec` з `cold_load=True` (якщо без since/to).
2. `ReadPolicy(force_disk=False, prefer_redis=cold_load, disk_policy="explicit")`.
3. Якщо bars порожній і немає since/to → `_build_no_data_payload()` з mandatory warnings.
4. Нормалізація через `_normalize_bars_window_v1()`.

> Evidence: `server.py:1379-1486`.

### 6.3 Stitching

Якщо `cfg.ui_stitching_enabled == True`:

- `_stitch_bars_previous_close(bars)` → `bars[i].open = bars[i-1].close`.
- Коригує high/low якщо stitched open виходить за межі.
- `meta.extensions.stitching = True`.

> Evidence: `server.py:1229-1251` (function), `server.py:1343-1347` (preview path), `server.py:1428-1432` (final path).

### 6.4 Response body (window_v1)

```python
{
    "ok": True,
    "symbol": str,
    "tf_s": int,
    "bars": [                          # normalized bar objects
        {
            "time": int,               # open_time_ms // 1000 (LWC compat)
            "open": float,
            "high": float,
            "low": float,
            "close": float,
            "volume": float,
            "open_time_ms": int,
            "close_time_ms": int,      # None якщо невідомий
            "tf_s": int,
            "src": str,
            "complete": bool,
            "event_ts": int,           # optional, лише якщо complete
            "last_price": float,       # optional
            "last_tick_ts": int,       # optional
        },
        ...
    ],
    "boot_id": str,
    "meta": {
        "source": str,
        "redis_hit": bool,
        "boot_id": str,
        "extensions": {...},
    },
    "warnings": list[str],            # optional
}
```

> Evidence: `server.py:693-776` (`_normalize_bar_window_v1`).

### 6.5 No-data rail

Якщо `bars == []` і немає range params:

- `note: "no_data"` + mandatory `warnings[]`.
- Якщо warnings порожні → автоматично додається `"no_data_unexplained"`.

> Evidence: `server.py:384-407` (`_build_no_data_payload`).

### 6.6 Query param ігнорування

`prefer_redis` та `force_disk` ігноруються server-side (Правило 20.2):

- Warning: `"query_param_ignored:prefer_redis"` / `"query_param_ignored:force_disk"`.

> Evidence: `server.py:1392-1399`.

---

## 7. /api/latest — Alias /api/bars

Ідентичний `/api/bars`, але:

- Параметр `after_open_ms` → `since_open_ms` (shortcut для incremental fetch).
- Без `to_open_ms`.

> Evidence: `server.py:1383-1390` (if path == "/api/latest").

Використовується клієнтом у `fetchNewerBarsFromDisk()` (`app.js:1475`): коли `/api/updates` показує `disk_last_open_ms > lastOpenMs`, UI завантажує нові бари через `/api/bars?since_open_ms=...`.

---

## 8. /api/updates — Інкрементальні upsert events

### 8.1 Параметри

| Параметр | Тип | Required | Default | Опис | Evidence |
|----------|-----|----------|---------|------|----------|
| `symbol` | str | ✓ | — | Символ | `server.py:1056` |
| `tf_s` | int | ✓ | 300 | Timeframe | `server.py:1058` |
| `limit` | int | — | 500 | Max events | `server.py:1059` |
| `align` | str | — | "fxcm" | Тільки "fxcm" або "tv" | `server.py:1060` |
| `since_seq` | int | — | None | Cursor: events з seq > value | `server.py:1061` |
| `epoch` | int | — | None | Epoch guard | `server.py:1063` |
| `include_preview` | bool | — | False | Включити preview events | `server.py:1065` |

### 8.2 Логіка

1. Для preview TF (60, 180) → `include_preview = True` автоматично. Evidence: `server.py:1073`.
2. Створює `UpdatesSpec`, викликає `uds.read_updates(spec)`.
3. Events нормалізуються через `_normalize_update_events_window_v1()`.
4. Contract guard: `_contract_guard_warn_updates()`.
5. Dropped events → warning `"event_dropped_contract_violation"` + meta.extensions.

### 8.3 Response body (updates_v1)

```python
{
    "ok": True,
    "symbol": str,
    "tf_s": int,
    "events": [
        {
            "key": {"symbol": str, "tf_s": int, "open_ms": int},
            "bar": {...},              # normalized window_v1 bar
            "complete": bool,
            "source": str,
            "event_ts": int,           # optional
            "seq": int,                # optional
        },
        ...
    ],
    "cursor_seq": int,
    "boot_id": str,
    "disk_last_open_ms": int,          # extra field для disk catch-up
    "bar_close_ms": int,               # extra field
    "ssot_write_ts_ms": int,           # extra field
    "api_seen_ts_ms": int,             # extra field
    "meta": {...},                     # optional
    "warnings": list[str],             # optional
}
```

> Evidence: `server.py:1094-1120`.

### 8.4 Додаткові поля (за межами updates_v1)

- `disk_last_open_ms`: з `res.disk_last_open_ms` — дозволяє UI знати, що на диску є нові бари.
- `bar_close_ms`, `ssot_write_ts_ms`, `api_seen_ts_ms`: для latency діагностики.

> Evidence: `server.py:1119-1122`.

### 8.5 Rate-limited логування

Updates логуються aggregate (раз на 30s або при нових events):

- `UI_UPDATES symbol=... tf_s=... count=... cursor_seq=... event_rate_per_s=...`
- Thread-safe через `_updates_log_lock`.

> Evidence: `server.py:1131-1175`.

---

## 9. /api/overlay — Ephemeral preview bar

### 9.1 Параметри

| Параметр | Тип | Required | Default | Опис | Evidence |
|----------|-----|----------|---------|------|----------|
| `symbol` | str | ✓ | — | Символ | `server.py:1492` |
| `tf_s` | int | ✓ | 300 | Target TF | `server.py:1494` |
| `base_tf_s` | int | — | 60 | Базовий preview TF | `server.py:1495` |

### 9.2 Guard railings

1. **Preview TF guard**: якщо `tf_s ∈ preview_allowlist` → `"overlay_not_applicable_for_preview_tf"`, bar=None. Evidence: `server.py:1504-1511`.
2. **HTF guard**: якщо `tf_s >= 14400` → `"overlay_not_applicable_for_htf"`, bar=None. Evidence: `server.py:1514-1522`.
3. **TF too small**: якщо `tf_s < 300` → warning `"overlay_tf_too_small"`. Evidence: `server.py:1524`.
4. **Base TF guard**: якщо `base_tf_s ∉ preview_allowlist` → bar=None, warnings. Evidence: `server.py:1530-1539`.

### 9.3 Алгоритм агрегації

1. Визначає поточний bucket (`b0`) та попередній (`b_prev`) через `bucket_start_ms()`.
2. Читає preview window з `uds.read_preview_window()` — `bars_per_bucket * 2 + 4` барів.
3. `_aggregate_bucket()` — агрегує preview бари в один overlay bar:
   - Фільтрує flat бари (`_is_flat_preview_bar()` — calendar_pause_flat або o==h==l==c з v≤4.0).
   - OHLCV: open=first.open, high=max(highs), low=min(lows), close=last.close, volume=0.
4. **Prev bar logic** (`server.py:1664-1709`):
   - Якщо є final bar для `b_prev` → показує final (accurate OHLCV + volume).
   - Якщо final ще не прийшов → показує тиковий preview.
   - Якщо перевірка final впала → degraded-but-visible з warning.

### 9.4 Response body

```python
{
    "ok": True,
    "bar": dict | None,                # backward compat (curr_bar only)
    "bars": list[dict],                # 0-2 бари: [prev_bar?, curr_bar?]
    "warnings": list[str],
    "meta": {
        "extensions": {
            "plane": "overlay",
            "base_tf_s": int,
            "bucket_open_ms": int,
            "prev_bucket_open_ms": int,
            "has_prev_bar": bool,
            "has_curr_bar": bool,
            "anchor_offset_ms": int,
            "observed_remainder_ms": int,
        },
        "boot_id": str,
    },
}
```

> Evidence: `server.py:1737-1755`.

### 9.5 HTF anchor offset

Overlay перевіряє alignment last_final_open_ms з очікуваним bucket_start:

- Для HTF (≥14400): допустимі remainders `{0, 7200000, 75600000, 79200000}` (broker convention).
- Mismatch → warning `"overlay_anchor_offset"`, rate-limited per (symbol, tf_s).

> Evidence: `server.py:282` (`_HTF_ALLOWED_REMAINDERS_MS`), `server.py:1560-1580`.

### 9.6 Overlay observability

Rate-limited aggregate лог (раз на 60s):

- `UI_OVERLAY_OBS req_total=... prev_held_total=... prev_wait_ms_last=...`

> Evidence: `server.py:1726-1737`.

---

## 10. /api/gaps — Gap report

### 10.1 Механіка

Читає статичний файл `reports/tail_audit/summary.json` або `reports/tail_audit/latest.json`.

### 10.2 Response

```python
# Якщо summary.json існує:
{"ok": True, **summary}

# Якщо тільки latest.json:
{"ok": True, "ts": str, "overall": str, "gaps": [...]}

# Якщо нічого немає:
{"ok": True, "ts": None, "overall": "NO_DATA", "message": "Scanner has not been run yet."}
```

> Evidence: `server.py:1182-1222`.

---

## 11. Нормалізаційний pipeline (window_v1)

### 11.1 `_normalize_bar_window_v1()` — один бар

Приймає raw bar (UDS/LWC/disk формат), повертає canonical window_v1 dict або `None`.

**Field resolution** (fallback chain):

| Вихідне поле | Primary | Fallback | Evidence |
|-------------|---------|----------|----------|
| `open_time_ms` | `raw.open_time_ms` | `raw.open_ms`, `raw.time * 1000` | `server.py:703-710` |
| `close_time_ms` | `raw.close_time_ms` | `raw.close_ms`, `open + tf_s * 1000` | `server.py:712-720` |
| `open` | `raw.open` | `raw.o` | `server.py:729` |
| `high` | `raw.high` | `raw.h` | `server.py:730` |
| `low` | `raw.low` | `raw.l` | `server.py:731` |
| `close` | `raw.close` | `raw.c` | `server.py:732` |
| `volume` | `raw.volume` | `raw.v` | `server.py:733` |
| `src` | `raw.src` | `raw.source`, `""` | `server.py:737-739` |
| `complete` | `raw.complete` | `True` | `server.py:741` |

**Output fields**: `time, open, high, low, close, volume, open_time_ms, close_time_ms, tf_s, src, complete` + optional `event_ts, last_price, last_tick_ts`.

**event_ts enrichment**: якщо `complete=True` і `event_ts` відсутній → `event_ts = close_time_ms`.
> Evidence: `server.py:756-760`.

### 11.2 `_normalize_bars_window_v1()` — batch

Приймає `list[Any]`, повертає `(normalized: list[dict], dropped: int, examples: list[str])`.

- Dropped бари логуються через `_drop_example_text()` (до 3 прикладів).

> Evidence: `server.py:800-815`.

### 11.3 `_normalize_update_events_window_v1()` — events

Те саме, але для events: нормалізує `ev.bar` замість top-level.
> Evidence: `server.py:818-843`.

---

## 12. Contract guards

### 12.1 `_guard_bar_shape(bar)` — bar contract

**Required fields**: `time, open, high, low, close, volume, open_time_ms, close_time_ms, tf_s, src, complete`.
**Optional fields**: `event_ts, last_price, last_tick_ts`.
**Type checks**: time/open_time_ms/close_time_ms/tf_s → int; OHLCV → number; src → str; complete → bool.
**Extra fields**: not allowed (produces `"bar_extra:..."` issue).
> Evidence: `server.py:561-607`.

### 12.2 `_guard_event_shape(ev)` — event contract

**Required fields**: `key, bar, complete, source`.
**Optional fields**: `event_ts, seq`.
**Key structure**: `{symbol: str, tf_s: int, open_ms: int}` — перевіряється recursively.
**Extra fields**: заборонені як на top-level, так і в key.
> Evidence: `server.py:609-648`.

### 12.3 `_guard_meta_shape(meta)` — meta contract

**Required fields**: `source, redis_hit, boot_id`.
**Optional fields**: `redis_error_code, redis_ttl_s_left, redis_payload_ts_ms, redis_seq, redis_len, extensions`.
> Evidence: `server.py:650-679`.

### 12.4 Enforcement pipeline

Guards викликаються після нормалізації:

1. `_contract_guard_warn_window()` — для /api/bars відповідей. Evidence: `server.py:873-894`.
2. `_contract_guard_warn_updates()` — для /api/updates відповідей. Evidence: `server.py:897-924`.

**Поведінка при порушенні**:

- Warning `"contract_violation"` додається у payload.
- `logging.warning("CONTRACT_VIOLATION schema=... issues=...")`.
- До 10 issues логуються.

### 12.5 Aggregate guard logging

`_log_public_api_contract_guard()` — rate-limited (30s window):

- Акумулює `dropped_bars` / `dropped_events`.
- `logging.warning("PUBLIC_API_CONTRACT_GUARD dropped_bars=... dropped_events=...")`.

> Evidence: `server.py:845-870`.

### 12.6 Selftest

`_selftest_contract_guard()` — перевіряє що пустий payload без symbol/tf_s тригерить contract_violation.
> Evidence: `server.py:927-931`.

---

## 13. Symbol canonicalization

### 13.1 Механіка

`_canonicalize_symbol(raw, cfg)` (`server.py:443-465`):

1. Якщо `raw` ∈ `config.symbols` → `(raw, None)`.
2. Якщо `raw` містить `_` і `raw.replace("_", "/")` ∈ `config.symbols` → `(canon, raw)`.
3. Інакше → passthrough `(raw, None)`.

### 13.2 Inject у meta

Якщо відбулась канонікалізація (`_sym_input is not None`):

- `meta.extensions.symbol_input = "XAU_USD"`, `meta.extensions.symbol_canon = "XAU/USD"`.

> Evidence: `server.py:1289-1294` (`_inject_sym_canon`).

### 13.3 Використовується у

Всі endpoint-и через `_canonicalize_symbol()`:

- `/api/bars`: `server.py:1253`
- `/api/updates`: `server.py:1056`
- `/api/overlay`: `server.py:1492`

---

## 14. Limit clamping та TF caps

### 14.1 Server-side caps

```python
# server.py:296-308
_MAX_BARS_CAP = 20_000
_TF_CAP = {
    60:    10_080,   # 1m: 7d
    180:    3_360,   # 3m: 7d
    300:   20_000,   # 5m: max
    900:   20_000,   # 15m: max
    1800:  20_000,   # 30m: max
    3600:  20_000,   # 1h: max
    14400:  5_000,   # 4h
    86400:  3_650,   # 1d
}
```

### 14.2 `_clamp_limit(raw_limit, tf_s)` → (clamped, was_clamped)

- `cap = min(_TF_CAP[tf_s], _MAX_BARS_CAP)`
- `clamped = min(max(raw, 1), cap)`

> Evidence: `server.py:409-413`.

### 14.3 Warning при clamp

Rate-limited (30s):

- `logging.warning("API_BARS_LIMIT_CLAMP tf_s=... raw=... clamped=... count_30s=...")`
- Response: `warnings.append("limit_clamped:{raw}->{clamped}")`

> Evidence: `server.py:1263-1273`.

### 14.4 Client-side caps

```javascript
// app.js:126-131
const COLD_START_BARS_BY_TF = {60:10080, 180:3360, 300:2016, 900:672, 1800:336, 3600:168, 14400:1080, 86400:365};
const COLD_START_BARS_FALLBACK = 2000;
const MAX_BARS_CAP = 20000;
```

UI читає серверний policy через `/api/config` → `uiPolicy.windowPolicy`, fallback на hard-coded. Evidence: `app.js:572-594`.

---

## 15. Stitching (PREVIOUS_CLOSE)

### 15.1 Функція

`_stitch_bars_previous_close(bars)` (`server.py:1229-1251`):

- `bars[i].open = bars[i-1].close` для кожного бару (якщо різниця > 0.0001).
- Коригує high/low.
- Підтримує обидва формати: full (open/close) і LWC (o/c).

### 15.2 Активація

- `config.json → ui_stitching_enabled` (поточне значення: `true`, `config.json:73`).
- `meta.extensions.stitching = True` при активації.
- **Display-only**: SSOT не модифікується.

> Evidence: `server.py:1343-1347` (preview), `server.py:1428-1432` (final).

### 15.3 Коли це стає проблемою

1. FXCM History повертає `Open = FIRST_TICK` для першого бару batch → ціновий розрив між batch-ами.
2. Stitching приховує цей розрив, але artificial high/low коригування може спотворити H/L якщо gap великий.
3. Guardrail: `meta.extensions.stitching = True` → UI/logs бачать що stitching активний.

> **⚠ Обмеження (P8-Q7)**: Stitching замикає **ВСІ** gaps, включаючи weekend/session breaks/news gaps — немає calendar-awareness guard. Для gap-аналізу (SMC FVG) потрібно використовувати raw SSOT дані (`ui_stitching_enabled=false` або direct disk read).

---

## 16. Warmup та bootstrap

### 16.1 `_bootstrap_warmup(uds, config_path)`

На старті UI сервера прогріває RAM кеш UDS з диску.
> Evidence: `server.py:222-265`.

**Послідовність**:

1. Читає `config.json → symbols`.
2. Застосовує bootstrap config overrides (`_apply_bootstrap_config()`).
3. Для кожного `symbol × _WARMUP_TF_PRIORITY`:
   - `WindowSpec(limit=warmup_bars, cold_load=True)`
   - `ReadPolicy(disk_policy="bootstrap")`
   - `uds.read_window(spec, policy)` — заповнює RAM.
4. Лог: `WARMUP: завершено symbols=... tfs=... bars_total=... elapsed=...`.

### 16.2 Priority TFs

```python
# server.py:181-182
_WARMUP_TF_PRIORITY = [300, 3600, 900, 14400, 86400, 1800, 60, 180]
_SHOW_IMMEDIATELY_TFS = [14400, 86400]
```

### 16.3 Config overrides

`_apply_bootstrap_config(cfg)` (`server.py:187-218`): зчитує `config.json → bootstrap → ui_warmup_bars_by_tf`, `ui_cold_start_bars_by_tf` і перезаписує глобальні `_WARMUP_BARS_BY_TF`, `_COLD_START_BARS_BY_TF`.

---

## 17. Config caching та rate-limiting

### 17.1 Config cache

`_load_cfg_cached(config_path)` (`server.py:469-499`):

- Перевіряє mtime кожні `CFG_CACHE_CHECK_INTERVAL_S = 0.5s`.
- Якщо mtime не змінився → повертає кешований dict.
- Якщо змінився → перечитує JSON.
- Помилки → повертає `{}`.

### 17.2 Rate-limited warnings

| Warning type | Rate limit | Evidence |
|-------------|-----------|---------|
| Overlay anchor offset | 60s per (symbol, tf_s) | `server.py:501-509` |
| HTF anchor obs | 300s per (symbol, tf_s) | `server.py:511-519` |
| Overlay obs aggregate | 60s global | `server.py:522-528` |
| Contract guard aggregate | 30s global | `server.py:845-870` |
| Limit clamp aggregate | 30s global | `server.py:1263-1273` |
| Updates log aggregate | 30s per (symbol, tf_s) | `server.py:1131-1175` |

---

## 18. Клієнтська інтеграція (app.js)

### 18.1 API caller

`apiGet(url, opts)` (`app.js:506-511`):

- `fetch(API_BASE + url, {cache: 'no-store', signal: opts.signal})`.
- Throws on non-ok HTTP status.

### 18.2 Lifecycle (init)

`init()` (`app.js:2067`):

1. `loadUiConfig()` → /api/config
2. `loadSymbols()` → /api/symbols
3. `loadBarsFull()` → /api/bars
4. `resetPolling()` → starts updates loop
5. `resetOverlayPolling()` → starts overlay loop

### 18.3 Symbol/TF switch

На зміну symbol/TF:

- `stopAllPolling()` → abort in-flight requests, clear timers.
- `uiEpoch++` → stale responses dropped.
- `loadBarsFull()` → fresh /api/bars.
- `resetPolling()` + `resetOverlayPolling()`.

> Evidence: `app.js:180-200`, debounced `SWITCH_DEBOUNCE_MS = 120ms`.

### 18.4 `applyUpdates(events)` — upsert engine

`app.js:1687`:

- Сортує events по `seq`.
- **Cursor guard**: `ev.seq <= updatesSeqCursor` → skip.
- **Stale guard**: `bar.open_time_ms < lastOpenMs - tfMs` → skip.
- **Forward gap guard**: `bar.open_time_ms > lastOpenMs + tfMs * 3` → trigger reload.
- **I3 enforcement (final>preview)**: якщо existing bar `complete=True` і new event `complete=False` → **безумовний skip** (continue). Evidence: `app.js:1723-1725`.
- **NoMix guard (final→final source)**: якщо обидва `complete=True` і `source` різний:
  - Для preview TF (M1/M3): дозволений upgrade через `_isAllowedSourceUpgrade(prev, next)` (tick_promoted→history тощо).
  - Для інших TF або недозволений upgrade → `nomix_violation` warning + skip.
  - Evidence: `app.js:1726-1735`.

> Evidence: `app.js:1687-1782`.

### 18.5 Boot_id guard

При зміні `boot_id` (серверний рестарт):

- UI робить повний `loadBarsFull()` + `resetPolling()` + `resetOverlayPolling()`.
- `updatesSeqCursor = null` (скидає cursor).

> Evidence: `app.js:1843-1850`.

---

## 19. Polling та backoff strategy

### 19.1 Updates polling

```javascript
// app.js:105-110
const UPDATES_BASE_FINAL_MS = 3000;     // final TF (≥300): 3s base
const UPDATES_BASE_PREVIEW_MS = 1000;    // preview TF (60, 180): 1s base
const UPDATES_BACKOFF_PREVIEW_MS = [1000, 1000, 1000];
const UPDATES_BACKOFF_FINAL_MS = [3000, 5000, 8000];
```

**Backoff**: при послідовних пустих відповідях → збільшується delay з масиву.
**Timeout**: 15s `AbortController` timeout на кожен fetch. Evidence: `app.js:1795-1797`.

### 19.2 Cursor gap handling

Якщо warnings містить `"cursor_gap"`:

- **Informational only** — не робить reload (P3 fix).
- Fast-forward до `data.cursor_seq`.
- Debounce 5s (`CURSOR_GAP_RECOVERY_DEBOUNCE_MS`).

> Evidence: `app.js:1875-1890`.

### 19.3 Disk catch-up

Якщо events порожні, але `data.disk_last_open_ms > lastOpenMs`:

- Виклик `fetchNewerBarsFromDisk()` → /api/bars?since_open_ms=...
- Заповнює gap між updates і disk.

> Evidence: `app.js:1901-1906`.

---

## 20. Scrollback (lazy history loading)

### 20.1 Тригер

Scrollback тригериться при скролі вліво, коли видимий range наближається до початку даних.

### 20.2 Параметри

```javascript
// app.js:136-152
const SCROLLBACK_TRIGGER_BARS_BASE = 1000;
const SCROLLBACK_MIN_INTERVAL_MS = 1200;
const SCROLLBACK_CHUNK_MAX_BASE = 2000;
const SCROLLBACK_CHUNK_MIN_BASE = 500;
const SCROLLBACK_MAX_STEPS = 6;
const SCROLLBACK_CHUNK_BY_TF = {300:1000, 900:1000, 1800:1000, 3600:1000};
```

### 20.3 Алгоритм

`loadScrollbackChunk(range, step)` (`app.js:1421`):

1. Перевіряє: `!scrollbackInFlight && !scrollbackReachedStart && bars < maxBars`.
2. Rate-limits: `SCROLLBACK_MIN_INTERVAL_MS = 1200ms`.
3. URL: `/api/bars?symbol=...&tf_s=...&limit=...&to_open_ms={firstOpenMs - 1}`.
4. Response bars merged вліво через `mergeOlderBars()`.
5. Рекурсивно планує наступний chunk (до `SCROLLBACK_MAX_STEPS`).

---

## 21. Overlay polling (client)

### 21.1 Коли активний

- `tf ∈ [300, 3600]` (M5..H1) — `OVERLAY_MIN_TF_S=300, OVERLAY_MAX_TF_S=3600`.
- Не для preview TF (60, 180) — у них вже є live бари.
- Не для HTF (14400, 86400) — тільки broker final.

> Evidence: `app.js:87-92`.

### 21.2 Polling interval

```javascript
// app.js:90-91
const OVERLAY_POLL_FAST_MS = 1000;   // коли prev_bar тримається
const OVERLAY_POLL_SLOW_MS = 2000;   // інакше
```

### 21.3 `pollOverlay()`

`app.js:1978`:

1. `baseTf = tf >= 14400 ? 180 : 60` — HTF агрегує з M3, решта з M1.
2. URL: `/api/overlay?symbol=...&tf_s=...&base_tf_s=...`.
3. `controller.updateOverlayBar(data.bar, data.bars)`.
4. Оновлює HUD price з last overlay bar.
5. Помилки overlay — не фатальні (тихо ігноруються).

---

## 22. Зведена матриця: endpoint × guard × source × client function

| Endpoint | Server guards | UDS source | Client function | Polling |
|----------|--------------|-----------|----------------|---------|
| `/api/config` | `_policy_sanity_issues` | `snapshot_status()` + config | `loadUiConfig()` | One-shot |
| `/api/status` | — | `snapshot_status()` | — | — |
| `/api/symbols` | — | `_list_symbols()` (dir) | `loadSymbols()` | One-shot |
| `/api/bars` (final) | `_clamp_limit`, `_normalize_bars_window_v1`, `_contract_guard_warn_window`, `_guard_bar_shape` | `uds.read_window()` | `loadBarsFull()`, `loadScrollbackChunk()` | One-shot + scrollback |
| `/api/bars` (preview) | same + I3 final>preview | `read_window()` + `read_preview_window()` | `loadBarsFull()` | One-shot |
| `/api/latest` | same as /api/bars | `uds.read_window()` | `fetchNewerBarsFromDisk()` | On-demand |
| `/api/updates` | `_normalize_update_events_window_v1`, `_contract_guard_warn_updates`, `_guard_event_shape` | `uds.read_updates()` | `pollUpdates()` | 1-8s |
| `/api/overlay` | 4 guard rails, `_is_flat_preview_bar` | `read_preview_window()` + `read_window()` (final check) | `pollOverlay()` | 1-2s |
| `/api/gaps` | — | File read | — | — |

---

## 23. Знахідки та потенційні проблеми

### F1: Overlay volume=0 (РИЗИК: низький)

**Факт**: `_aggregate_bucket()` завжди встановлює `volume=0.0` для overlay барів.
> Evidence: `server.py:1645`.

**Вплив**: Overlay бари завжди показують нульовий volume. Preview тики не передають volume, але навіть якщо б передавали — він ігнорується.

**Рекомендація**: Документувати як відому поведінку.

---

### F2: Extra response fields поза updates_v1 контрактом (РИЗИК: низький)

**Факт**: `/api/updates` повертає `disk_last_open_ms`, `bar_close_ms`, `ssot_write_ts_ms`, `api_seen_ts_ms` — ці поля не входять у формальний `updates_v1` контракт.
> Evidence: `server.py:1119-1122`.

**Вплив**: Клієнт використовує `disk_last_open_ms` для disk catch-up (`app.js:1901-1906`). Формально це розширення контракту без документації.

**Рекомендація**: Додати ці поля у формальний `updates_v1` контракт як optional extensions, або перенести в `meta.extensions`.

---

### F3: `_normalize_bar_window_v1` fallback complete=True (РИЗИК: середній)

**Факт**: Якщо raw bar не має поля `complete` → default `True` (`server.py:741`).

**Вплив**: Cache bars (Redis snapshot format) не містять `complete` поле (P3: Section 6.7). При нормалізації вони автоматично стають `complete=True`. Це правильно для final bars, але потенційно проблемно якщо preview бар потрапить у цей pipeline без `complete` поля.

**Рекомендація**: Перевірити, що preview бари завжди мають explicit `complete=False`.

---

### F4: Query param `align` парситься але ігнорується (РИЗИК: низький)

**Факт**: `align` параметр перевіряється (`server.py:1222, 1244`) і відхиляється якщо не в (`fxcm`, `tv`), але функціонально ігнорується (ADR-0002 Phase 3: H4 тепер first-class UDS TF).

**Вплив**: Зайвий параметр у API. Backward compat підтримується через "accept but ignore".

**Рекомендація**: Поступова deprecation: додати warning якщо align != default.

---

### F5: Overlay error silently ignored on client (РИЗИК: низький)

**Факт**: `pollOverlay()` catch блок (`app.js:2032-2034`): тихо ігнорує всі помилки overlay.

**Вплив**: Якщо overlay перестає працювати (мережевий збій, server error), UI не показує жодного сигналу.

**Рекомендація**: Додати counter помилок overlay у HUD/diag. Після N послідовних помилок → degrade overlay polling або показати banner.

---

### F6: Config cache перевіряється кожні 0.5s у request thread (РИЗИК: низький)

**Факт**: `_load_cfg_cached()` (`server.py:469-499`) виконує `os.path.getmtime()` та потенційно `json.load()` всередині request handler thread.

**Вплив**: При великій кількості concurrent requests кожен перевіряє mtime (I/O). `ThreadingHTTPServer` використовує thread per request, тому contention на файловій системі.

**Рекомендація**: Для 10-50 трейдерів це не критично (check interval 0.5s обмежує). Для масштабування — lock або окремий config reload thread.

---

### F7: Public API guard dropped_bars atomic counter (РИЗИК: низький)

**Факт**: `_public_api_guard_dropped_bars` та `_public_api_guard_dropped_events` захищені `_public_api_guard_lock` (`server.py:310`).

**Вплив**: Thread-safe, але lock contention при високому throughput. Для 10-50 трейдерів це не проблема.

---

### F8: Updates payload log — dynamic global state (РИЗИК: низький)

**Факт**: `_updates_log_state` та `_updates_log_lock` створюються лазано через `globals()` (`server.py:1134-1139`).

**Вплив**: Функціонально працює, але антипатерн "lazy global init" може бути джерелом race у теорії (dunder globals assignment не атомарний у CPython, хоча GIL це маскує).

**Рекомендація**: Ініціалізувати при import-time як module-level.

---

### Зведена таблиця знахідок

| ID | Опис | Ризик | Інваріанти |
|----|------|-------|------------|
| F1 | Overlay volume=0 завжди | Низький | — |
| F2 | Extra fields у /api/updates поза контрактом | Низький | I5 |
| F3 | Fallback complete=True у нормалізації | Середній | I3 |
| F4 | align param parsed but ignored | Низький | — |
| F5 | Overlay errors silently ignored on client | Низький | I5 |
| F6 | Config cache mtime check in request thread | Низький | — |
| F7 | Public API guard lock contention | Низький | — |
| F8 | Lazy global init for updates log | Низький | — |
