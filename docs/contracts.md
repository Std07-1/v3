# Реєстр контрактів (SSOT)

> **Останнє оновлення**: 2026-03-01  
> **Навігація**: [docs/index.md](index.md)

Усі публічні JSON Schema контракти живуть у `core/contracts/public/marketdata_v1/`.  
Контракти **не дублюються** у `runtime/` чи `ui/` — вони є єдиним джерелом правди (SSOT) для формату payload-ів.

---

## Зміст

1. [Реєстр схем](#реєстр-схем)
2. [bar_v1 — Один OHLCV бар](#bar_v1--один-ohlcv-бар)
3. [window_v1 — Відповідь /api/bars](#window_v1--відповідь-apibars)
4. [updates_v1 — Відповідь /api/updates](#updates_v1--відповідь-apiupdates)
5. [tick_v1 — Тік](#tick_v1--тік)
6. [SMC Wire Format (WS frames)](#smc-wire-format-ws-frames)
7. [Redis snapshot (внутрішній)](#redis-snapshot-та-preview-внутрішній-формат)
8. [Правила еволюції схем](#правила-еволюції-схем)

---

## Реєстр схем

| Контракт | Файл (SSOT) | Продюсер | Консюмер | Версія |
|---|---|---|---|---|
| **bar_v1** | `core/contracts/public/marketdata_v1/bar_v1.json` | UDS (через server.py нормалізацію) | UI (app.js), exit-gates | v1 |
| **window_v1** | `core/contracts/public/marketdata_v1/window_v1.json` | `ui_chart_v3/server.py` → `/api/bars` | UI (app.js), тести | v1 |
| **updates_v1** | `core/contracts/public/marketdata_v1/updates_v1.json` | `ui_chart_v3/server.py` → `/api/updates` | UI (app.js), тести | v1 |
| **tick_v1** | `core/contracts/public/marketdata_v1/tick_v1.json` | `tick_publisher_fxcm.py` | `tick_preview_worker.py`, exit-gates | v1 |
| **Redis snap** (internal) | Документація: `docs/redis_snapshot_design.md` | `runtime/store/redis_snapshot.py` | UDS read layers | internal v1 |
| **smc_snapshot** (wire) | `core/smc/types.py` → `ui_v4/src/types.ts` | `SmcRunner` (ws_server) | `OverlayRenderer` (ui_v4) | v1 (ADR-0024) |
| **smc_delta** (wire) | `core/smc/types.py` → `ui_v4/src/types.ts` | `SmcRunner` (ws_server) | `smcStore` (ui_v4) | v1 (ADR-0024) |

---

## bar_v1 — Один OHLCV бар

**Файл**: `core/contracts/public/marketdata_v1/bar_v1.json`  
**Schema ID**: `marketdata.bar.v1`

### Обов'язкові поля

| Поле | Тип | Опис |
|---|---|---|
| `time` | integer | Epoch seconds (LWC-сумісність: `open_time_ms / 1000`) |
| `open` | number | Ціна відкриття |
| `high` | number | Найвища ціна |
| `low` | number | Найнижча ціна |
| `close` | number | Ціна закриття |
| `volume` | number | Обсяг |
| `open_time_ms` | integer | Canonical epoch milliseconds (основний ключ часу) |
| `close_time_ms` | integer\|null | End-excl: `open_time_ms + tf_s * 1000` (canonical) |
| `tf_s` | integer | Timeframe у секундах (60, 180, 300, ... 86400) |
| `src` | string | Джерело: `"history"`, `"derived"`, `"history_agg"`, `"preview_tick"`, `"tick_promoted"` |
| `complete` | boolean | `true` = bucket elapsed (нових барів для цього bucket не буде), `false` = preview (формується). Це **не** гарантія "100% N з N source bars" для derived випадків |

### Опціональні поля

| Поле | Тип | Опис |
|---|---|---|
| `event_ts` | integer | Timestamp створення event (лише для complete=true) |
| `last_price` | number | Остання ціна (для HUD overlay) |
| `last_tick_ts` | integer | Timestamp останнього тіка |

**additionalProperties**: `false` — невідомі поля заборонені.

### Приклад payload

```json
{
  "time": 1770302400,
  "open": 2045.50,
  "high": 2048.30,
  "low": 2044.10,
  "close": 2047.80,
  "volume": 142,
  "open_time_ms": 1770302400000,
  "close_time_ms": 1770302700000,
  "tf_s": 300,
  "src": "history",
  "complete": true,
  "event_ts": 1770302699999
}
```

### Інваріанти

- `close_time_ms = open_time_ms + tf_s * 1000` (end-excl canonical)
- Якщо `complete=true`: `src ∈ {"history", "derived", "history_agg"}`
- Якщо `complete=false`: `src ∈ {"preview_tick", "tick_promoted"}`
- `time = floor(open_time_ms / 1000)` (цілочисельне ділення)
- Для `src="derived"` можливий partial-final випадок: `complete=true` разом з `extensions.partial=true` (або `partial_calendar_pause` / `boundary_partial`).
- Strict-споживачі (screening/strategy) можуть відфільтрувати такі бари за `extensions.partial*` або застосувати soft-penalty замість hard reject.
- Рекомендований soft-penalty: `1 - source_count/expected_count` (коли лічильники доступні).

---

## window_v1 — Відповідь /api/bars

**Файл**: `core/contracts/public/marketdata_v1/window_v1.json`  
**Schema ID**: `marketdata.window.v1`

Використовує `oneOf`:

### Варіант 1: window_response (є бари)

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `ok` | const true | ✓ | |
| `symbol` | string | ✓ | Символ |
| `tf_s` | integer | ✓ | Timeframe |
| `bars` | bar_v1[] | ✓ | Масив барів (sorted, deduped, monotonic open_time_ms) |
| `boot_id` | string | ✓ | ID boot-сесії UDS |
| `meta` | object | ✓ | Див. нижче |
| `warnings` | string[] | | Попередження (degraded, clamp тощо) |

### Варіант 2: no_data_response (порожній)

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `ok` | const true | ✓ | |
| `bars` | bar_v1[] | ✓ | Порожній масив (maxItems=0) |
| `note` | const "no_data" | ✓ | |
| `boot_id` | string | ✓ | |
| `meta` | object | ✓ | |
| `warnings` | string[] | | **Обов'язково при bars=[]** (інваріант I5: no_data rail) |

### Об'єкт meta

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `source` | string | ✓ | Джерело даних: `"redis"`, `"ram"`, `"disk"`, `"degraded"`, `"preview_curr"` |
| `redis_hit` | boolean | ✓ | Чи дані прийшли з Redis |
| `boot_id` | string | ✓ | |
| `redis_error_code` | string | | Код помилки Redis |
| `redis_ttl_s_left` | integer | | Залишок TTL |
| `redis_payload_ts_ms` | integer | | Час запису payload у Redis |
| `redis_seq` | integer | | Seq з Redis snap |
| `redis_len` | integer | | Кількість барів у Redis tail |
| `extensions` | object | | Розширення: `partial`, `partial_calendar_pause`, `boundary_partial`, `partial_reasons[]`, `source_count`, `expected_count`, `geom_fix`, `expected/got` тощо |

---

## updates_v1 — Відповідь /api/updates

**Файл**: `core/contracts/public/marketdata_v1/updates_v1.json`  
**Schema ID**: `marketdata.updates.v1`

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `ok` | const true | ✓ | |
| `symbol` | string | ✓ | |
| `tf_s` | integer | ✓ | |
| `events` | event[] | ✓ | Масив upsert events |
| `cursor_seq` | integer ≥0 | ✓ | Монотонний cursor для наступного запиту |
| `boot_id` | string | ✓ | Для epoch guard: при зміні boot_id → UI reload |
| `disk_last_open_ms` | integer | | Останній open_ms на диску |
| `bar_close_ms` | integer | | |
| `ssot_write_ts_ms` | integer | | Час запису SSOT |
| `api_seen_ts_ms` | integer | | Час обробки API |
| `warnings` | string[] | | Попередження |

### Об'єкт event

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `key` | object | ✓ | `{symbol, tf_s, open_ms}` — унікальний ключ бару |
| `bar` | bar_v1 | ✓ | Повний payload бару |
| `complete` | boolean | ✓ | Final (true) або preview (false) |
| `source` | string | ✓ | Джерело |
| `event_ts` | integer\|null | | Timestamp event (лише для complete=true) |

### Інваріанти

- `cursor_seq` монотонно зростає
- При зміні `boot_id` → UI робить повний reload (не інкрементальний)
- Final>Preview: для одного `key`, complete=true витісняє complete=false

---

## tick_v1 — Тік

**Файл**: `core/contracts/public/marketdata_v1/tick_v1.json`  
**Schema ID**: `marketdata.tick.v1`

| Поле | Тип | Required | Опис |
|---|---|---|---|
| `v` | integer | ✓ | Версія формату (1) |
| `symbol` | string | ✓ | Символ (canonical: `"XAU/USD"`) |
| `tick_ts_ms` | integer | ✓ | Timestamp тіка (epoch ms) |
| `src` | string | ✓ | Джерело: `"fxcm_offer"`, `"fxcm_wallclock"`, `"sim"` тощо |
| `seq` | integer | ✓ | Монотонний sequence number |
| `bid` | number\|null | | Ціна bid |
| `ask` | number\|null | | Ціна ask |
| `mid` | number\|null | | Ціна mid |

**additionalProperties**: `false`

### Приклад payload

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "tick_ts_ms": 1770302401234,
  "src": "fxcm_offer",
  "seq": 42,
  "bid": 2047.80,
  "ask": 2048.10,
  "mid": null
}
```

---

## SMC Wire Format (WS frames)

> **ADR**: [0024-smc-engine.md](adr/0024-smc-engine.md)  
> **Python SSOT**: `core/smc/types.py` (SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta)  
> **TypeScript SSOT**: `ui_v4/src/types.ts` (SmcZone, SmcSwing, SmcLevel, SmcData, SmcDeltaWire)  
> **Продюсер**: `runtime/smc/smc_runner.py` → `runtime/ws/ws_server.py`  
> **Консюмер**: `ui_v4/src/stores/smcStore.ts` → `ui_v4/src/smc/OverlayRenderer.ts`

SMC — ephemeral read-only overlay. **Не зберігається** на диску. Відновлюється при warmup з UDS bars.

### SmcZone (WS wire)

| Поле | Тип | Опис |
|---|---|---|
| `id` | string | Deterministic: `{kind}_{symbol}_{tf_s}_{anchor_ms}` |
| `start_ms` | integer | Epoch ms — коли зона створена |
| `end_ms` | integer\|null | Epoch ms коли mitigated, null якщо active |
| `high` | number | Верхня межа зони |
| `low` | number | Нижня межа зони |
| `kind` | string | `"ob_bull"`, `"ob_bear"`, `"fvg_bull"`, `"fvg_bear"`, `"premium"`, `"discount"`, `"inducement_bull"`, `"inducement_bear"` |
| `status` | string | `"active"`, `"mitigated"`, `"breaker"` |
| `strength` | number | 0.0–1.0, використовується для opacity у UI |

### SmcSwing (WS wire)

| Поле | Тип | Опис |
|---|---|---|
| `id` | string | `{kind}_{symbol}_{tf_s}_{time_ms}` |
| `a` | `{t: int, p: number}` | Точка A (час і ціна) |
| `b` | `{t: int, p: number}` | Точка B (час і ціна) |
| `label` | string | `"BOS BULL"`, `"BOS BEAR"`, `"CHOCH BULL"`, `"CHOCH BEAR"` |

### SmcLevel (WS wire)

| Поле | Тип | Опис |
|---|---|---|
| `id` | string | `{kind}_{symbol}_{tf_s}_{price_int}` |
| `price` | number | Рівень ціни |
| `t_ms` | integer | Epoch ms першого торкання |
| `kind` | string | `"eq_highs"`, `"eq_lows"` |
| `touches` | integer | Кількість торкань |

### SmcData (full frame payload)

У WS `full` та `replay` frame-ах:

```json
{
  "data": {
    "candles": [...],
    "zones": [SmcZone, ...],
    "swings": [SmcSwing, ...],
    "levels": [SmcLevel, ...]
  }
}
```

### SmcDeltaWire (delta frame payload)

У WS `delta` frame-ах (опціональне поле `smc_delta`):

```json
{
  "data": {
    "upsert": [...],
    "smc_delta": {
      "new_zones": [SmcZone, ...],
      "mitigated_zone_ids": ["ob_bear_XAU/USD_900_1770288000000"],
      "updated_zones": [SmcZone, ...],
      "new_swings": [SmcSwing, ...],
      "new_levels": [SmcLevel, ...],
      "removed_level_ids": [],
      "trend_bias": "bullish"
    }
  }
}
```

### Інваріанти SMC wire

- **S3**: Zone ID deterministic — same input → same ID
- **S6**: Python `to_wire()` output === TypeScript interface fields
- SMC не змінює OHLCV payload — це окремі поля в тому ж WS frame
- `smc_delta` присутній тільки якщо `SmcDelta.has_changes == true`
- UI обробляє через `smcStore.applySmcFull()` (full frame) / `smcStore.applySmcDelta()` (delta frame)

---

## Redis snapshot та preview (внутрішній формат)

> Детальний дизайн: [redis_snapshot_design.md](redis_snapshot_design.md)

Це **внутрішній** формат (не публічний контракт). Ключові відмінності від публічного bar_v1:

| Відмінність | Redis (ohlcv + preview) | Public bar_v1 (CandleBar) |
|---|---|---|
| `close_ms` семантика | **end-incl** (`open_ms + tf_ms - 1`) | **end-excl** (`open_time_ms + tf_s*1000`) |
| Додаткові поля | `payload_ts_ms`, `seq`, `v` | `time`, `event_ts`, `last_price` |

### Інваріант: Redis close_ms = end-inclusive (SSOT)

Усі Redis ключі (`ohlcv:*`, `preview:curr:*`, `preview:tail:*`) зберігають
`close_ms = open_ms + tf_s * 1000 - 1` (end-incl).  
CandleBar/SSOT JSONL внутрішньо використовують end-excl (`close_time_ms = open_ms + tf_s * 1000`).  
Конвертація відбувається **тільки на межі Redis write** (`redis_snapshot._bar_to_cache_bar`,
`redis_snapshot.put_bar`, `uds.publish_preview_bar`).  
UDS нормалізує Redis snap → public bar_v1 при читанні (`_redis_payload_bar_to_canonical`).

---

## Правила еволюції схем

1. **Schema-first**: будь-який новий payload/endpoint → спочатку JSON Schema у `core/contracts/`, потім код.
2. **Canonical representation**: час = epoch ms int; поля стабільні; невідомі поля → `additionalProperties: false`.
3. **Сумісність**: legacy поля підтримуються при читанні, але канон визначає одне ім'я.
4. **Версіонування**: `v1` → `v2` лише через окремий initiative + міграція + rollback-план.
5. **JSONL append-only**: формат файлів на диску (CandleBar) не змінюється без ADR.
6. **Guard на вході**: кожен payload проходить guard у `runtime/` (fail-fast). В `ui_chart_v3/server.py`: `_guard_bar_shape`, `_guard_event_shape`, `_guard_meta_shape`.
7. **Бюджети payload**: `_MAX_BARS_CAP` та `_TF_CAP` у server.py обмежують розмір відповідей. Перевищення → loud warning + clamp.
