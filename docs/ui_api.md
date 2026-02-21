# UI API Reference (HTTP)

> **Останнє оновлення**: 2026-02-15  
> **Навігація**: [docs/index.md](index.md)  
> **Принцип**: UI = read-only renderer. Тіки напряму не бачить. Не має доменної логіки.

Усі endpoint-и обслуговуються одним процесом `ui_chart_v3/server.py` (same-origin, порт 8089).  
UDS ініціалізується з `role="reader"` — будь-яка спроба запису → `RuntimeError`.

---

## Зміст

1. [Таблиця endpoint-ів](#таблиця-endpoint-ів)
2. [Деталі кожного endpoint](#деталі-кожного-endpoint)
3. [Policy SSOT (/api/config)](#apiconfig)
4. [Guards та rails](#guards-та-rails)
5. [Кеш та TTL](#кеш-та-ttl)

---

## Таблиця endpoint-ів

| Endpoint | Метод | Призначення | Контракт | Джерело даних | Кеш |
|---|---|---|---|---|---|
| `/api/config` | GET | Policy SSOT для UI | — | `config.json` (mtime cache ~0.5s) | no-cache |
| `/api/status` | GET | Статус UDS/UI, prime_ready | — | `uds.snapshot_status()` | no-cache |
| `/api/bars` | GET | Вікно барів (cold-load / scrollback) | `window_v1.json` | UDS `read_window()` / `read_preview_window()` | no-cache |
| `/api/latest` | GET | Alias для `/api/bars` (legacy) | `window_v1.json` | UDS `read_window()` | no-cache |
| `/api/updates` | GET | Інкрементальні upsert events (polling) | `updates_v1.json` | UDS `read_updates()` / preview updates | no-cache |
| `/api/overlay` | GET | Ephemeral preview bar для TF≥M5 | — | UDS `read_preview_window()` | no-cache |
| `/api/symbols` | GET | Список доступних символів | — | `config.json → symbols` | no-cache |
| `/api/gaps` | GET | Gap report з tail integrity scanner | — | `reports/tail_audit/summary.json` | no-cache |
| `/` | GET | Статичний UI (index.html) | — | `ui_chart_v3/static/` | no-cache (dev) |
| `/app.js`, `/chart_adapter_lite.js` | GET | Статичні JS файли | — | `ui_chart_v3/static/` | no-cache (з cache-buster) |
| `/ui_config.json` | GET | Портативний UI конфіг | — | `ui_chart_v3/static/` | no-cache |

> **Усі API endpoint-и повертають `Content-Type: application/json; charset=utf-8`.**  
> **Статичні файли**: no-cache headers (`Cache-Control: no-store, no-cache`, `Pragma: no-cache`, `Expires: 0`).

---

## Деталі кожного endpoint

### /api/config

**Призначення**: видати policy SSOT для UI один раз на старті.

**Відповідь**:

```json
{
  "ok": true,
  "ui_debug": false,
  "policy_version": "20260215-001",
  "build_id": "20260215T120000Z",
  "window_policy": {
    "scrollback_chunk_by_tf_s": { "300": 1000, ... },
    "cold_start_by_tf_s": { "300": 5000, ... },
    "max_bars_by_tf_s": { "300": 20000, ... }
  },
  "tf_allowlist": [60, 180, 300, 900, 1800, 3600, 14400, 86400],
  "preview_tf_allowlist": [60, 180],
  "config_invalid": false,
  "warnings": []
}
```

**Rail**: серверний sanity-check (`warmup < cold_start` для show-immediately TF). Якщо порушено → `config_invalid=true`, `warnings[]` з деталями, лог `POLICY_CONFIG_INVALID`.

**UI поведінка при відмові**: fallback у hard-coded константи + loud лог `UI_POLICY_FALLBACK_ACTIVE`.

---

### /api/status

**Призначення**: моніторинг та діагностика стану UDS/UI.

**Параметри**: немає.

**Відповідь** (приклад):

```json
{
  "ok": true,
  "status": {
    "boot_id": "20260215T120000Z",
    "prime_ready": true,
    "prime_ready_payload": {
      "boot_id": "20260215T120000Z",
      "ready": true,
      "prime_tail_len_by_tf_s": { "300": 8000, "14400": 256 }
    },
    "redis_spec": { "host": "127.0.0.1", "port": 6379, "db": 1 },
    "disk_bootstrap_reads_total": 0,
    "ram_stats": { ... },
    "warnings": []
  }
}
```

---

### /api/bars

**Призначення**: вікно барів для cold-load та scrollback.

**Параметри**:

| Параметр | Тип | Required | Опис |
|---|---|---|---|
| `symbol` | string | ✓ | Символ (напр. `XAU/USD`) |
| `tf_s` | integer | ✓ | Timeframe у секундах |
| `limit` | integer | | Кількість барів (default 1000, clamp по TF: `_TF_CAP`) |
| `since_open_ms` | integer | | Бари з open_ms ≥ since_open_ms |
| `to_open_ms` | integer | | Бари з open_ms < to_open_ms (для scrollback) |
| `align` | string | | `fxcm` (default) або `tv` (лише H4, derived з H1 final-only) |
| `epoch` | string | | Epoch guard: UI передає для cancellation stale відповідей |
| `force_disk` | integer | | **ІГНОРУЄТЬСЯ** з warning (Правило 20.2) |
| `prefer_redis` | integer | | **ІГНОРУЄТЬСЯ** з warning (Правило 20.2) |

**Контракт відповіді**: `window_v1.json` (див. [contracts.md](contracts.md#window_v1--відповідь-apibars))

**Джерело даних**:

- Для **preview TF** (60, 180): `uds.read_preview_window()` (Redis preview keyspace)
- Для **final TF** (300+): `uds.read_window()` з `disk_policy="never"`, `prefer_redis=true` (Redis snap → RAM → degraded)
- Для **align=tv (H4)**: derived H4 з H1 final-only, anchor_remainder_ms=10800000, без запису в SSOT; `meta.source=derived_h1_final`, redis_* не повертаються у top-level. Неповний bucket перед calendar break повертається як partial (`complete=false`, `warnings+=derived_partial_bucket`, `extensions.partial_reason=calendar_break|calendar_break_no_m5`).

**Stitching**: якщо `ui_stitching_enabled=true` у config.json → `open[i]=close[i-1]` (display-only, SSOT не модифікується). За замовчуванням `false`.

**Clamp**: `limit` обмежується server-side через `_clamp_limit(raw, tf_s)`. При перевищенні → warning `limit_clamped_...`.

**No-data rail**: якщо `bars=[]` → відповідь містить `note="no_data"` + `warnings[]` (мінімум пояснення чому порожнє). Silent порожня відповідь заборонена (інваріант I5).

---

### /api/updates

**Призначення**: інкрементальні оновлення барів (upsert events). UI polling кожні 1–3 секунди.

**Параметри**:

| Параметр | Тип | Required | Опис |
|---|---|---|---|
| `symbol` | string | ✓ | Символ |
| `tf_s` | integer | ✓ | Timeframe |
| `since_seq` | integer | | Cursor: повернути events з seq > since_seq |
| `limit` | integer | | Max events (default 100) |
| `epoch` | string | | Epoch guard |
| `include_preview` | integer | | Для preview TF: включити preview events. Для non-preview TF → warning `include_preview_ignored` |
| `align` | string | | **Тільки `fxcm`**. `align=tv` → HTTP 400 `align_tv_updates_not_supported` |

**Контракт відповіді**: `updates_v1.json` (див. [contracts.md](contracts.md#updates_v1--відповідь-apiupdates))

**Джерело даних**: Redis updates bus (hot-path). Disk **не** використовується для updates (disk = лише recovery).

**Guard**: `align=tv` не підтримується для updates (derived view працює лише через snapshot `/api/bars`).

**Boot_id guard**: при зміні `boot_id` (рестарт сервера) → UI робить повний reload, а не інкрементальний update.

---

### /api/overlay

**Призначення**: ephemeral preview bar для формуючоїсвічки на TF ≥ M5.

**Параметри**:

| Параметр | Тип | Required | Опис |
|---|---|---|---|
| `symbol` | string | ✓ | Символ |
| `tf_s` | integer | ✓ | Target TF (H1, H4, D1 тощо) |
| `base_tf_s` | integer | | Базовий TF для агрегації (default: M3=180 для H4/D1, M1=60 для інших) |

**Відповідь**:

```json
{
  "ok": true,
  "bar": { ... },
  "bars": [ ... ],
  "warnings": [],
  "meta": { "source": "preview_curr", "redis_hit": false }
}
```

**Примітка**: Overlay — read-only. Створюється ефемерно з preview-tail, не пишеться у SSOT.

---

### /api/symbols

**Призначення**: список доступних символів.

**Відповідь**:

```json
{
  "ok": true,
  "symbols": ["XAU/USD", "EUSTX50", "GBP/CAD", ...]
}
```

### /api/gaps

**Призначення**: звіт про gaps з tail integrity scanner (для моніторингу).

**Джерело**: `reports/tail_audit/summary.json` (генерується `python -m tools.tail_integrity_scanner`).

**Відповідь**:

```json
{
  "ok": true,
  "ts": "2026-02-20T13:40:15Z",
  "overall": "FAIL",
  "pass": 31,
  "fail": 60,
  "elapsed_s": 1.3,
  "failed_items": [
    {"symbol": "XAU/USD", "tf": "M5", "gaps": 2, "issues": []},
    ...
  ]
}
```

Якщо scanner ще не запускався — `"overall": "NO_DATA"`.

---

## Guards та rails

| Guard | Файл:функція | Що перевіряє |
| --- | --- | ---|
| `_guard_bar_shape` | `server.py` | Validates bar contract (time, OHLCV, open_time_ms та ін.) |
| `_guard_event_shape` | `server.py` | Validates update event structure (key, bar, complete, source) |
| `_guard_meta_shape` | `server.py` | Validates meta object (source, redis_hit, boot_id) |
| `_clamp_limit` | `server.py` | Caps limit per TF; warning при перевищенні |
| `_normalize_bar_window_v1` | `server.py` | Нормалізує бар до public window_v1 формату |
| `_contract_guard_warn_window` | `server.py` | Loud лог при порушенні window_v1 контракту |
| `_contract_guard_warn_updates` | `server.py` | Loud лог при порушенні updates_v1 контракту |
| `prefer_redis`/`force_disk` ignore | `server.py` | Query params ігноруються з warning (Правило 20.2) |
| no_data rail | `server.py` | `bars=[]` → обов'язково `warnings[]` |

---

## Кеш та TTL

| Ресурс | Кеш-стратегія | TTL |
| --- | --- | --- |
| `/api/bars` (Redis snap) | Redis `ohlcv:tail`, warmup on boot | TTL per TF (M1=1800s, M5=3600s, H4=172800s, D1=604800s) |
| `/api/bars` (RAM) | LRU in-process | Eviction при пам'яті / кількості |
| `/api/bars` (Disk) | **НЕ hot-path** (`disk_policy="never"`) | Лише bootstrap (60s window) або scrollback (explicit) |
| `/api/updates` | Redis list + seq | Немає TTL; очищається при переповненні |
| Preview (curr) | Redis `preview:curr` | `preview_curr_ttl_s` (default 1800s) |
| Preview (tail) | Redis `preview:tail` | Ring buffer, без TTL |
| `/api/config` | mtime cache (~0.5s) | Перечитується при зміні config.json |
| Статичні файли | no-cache headers | Cache-buster у URL |

---

## Важливі обмеження

1. **UI read-only**: UI процес не має права писати в UDS (`role="reader"`). Будь-яка спроба → `RuntimeError`.
2. **Тіки не видимі UI напряму**: UI не бачить raw ticks з Redis PubSub. Він бачить лише preview bars через `/api/bars` (preview TF) та overlay через `/api/overlay`.
3. **Disk не hot-path**: `/api/bars` для interactive requests використовує `disk_policy="never"`. Disk доступний лише під час bootstrap (перші 60s) або для explicit scrollback (`to_open_ms`).
4. **No split-brain**: query params `prefer_redis`/`force_disk` ігноруються server-side. Джерело обирається сервером, не клієнтом.
5. **Same-origin**: UI та API працюють в одному процесі, один порт (8089).
