# Реєстр контрактів (SSOT)

> **Останнє оновлення**: 2026-02-15  
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
6. [Redis snapshot (внутрішній)](#redis-snapshot-внутрішній-формат)
7. [Правила еволюції схем](#правила-еволюції-схем)

---

## Реєстр схем

| Контракт | Файл (SSOT) | Продюсер | Консюмер | Версія |
|---|---|---|---|---|
| **bar_v1** | `core/contracts/public/marketdata_v1/bar_v1.json` | UDS (через server.py нормалізацію) | UI (app.js), exit-gates | v1 |
| **window_v1** | `core/contracts/public/marketdata_v1/window_v1.json` | `ui_chart_v3/server.py` → `/api/bars` | UI (app.js), тести | v1 |
| **updates_v1** | `core/contracts/public/marketdata_v1/updates_v1.json` | `ui_chart_v3/server.py` → `/api/updates` | UI (app.js), тести | v1 |
| **tick_v1** | `core/contracts/public/marketdata_v1/tick_v1.json` | `tick_publisher_fxcm.py` | `tick_preview_worker.py`, exit-gates | v1 |
| **Redis snap** (internal) | Документація: `docs/redis_snapshot_design.md` | `runtime/store/redis_snapshot.py` | UDS read layers | internal v1 |

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
| `complete` | boolean | `true` = final (закритий), `false` = preview (формується) |

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
| `extensions` | object | | Розширення: `partial`, `geom_fix`, `expected/got` тощо |

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

## Redis snapshot (внутрішній формат)

> Детальний дизайн: [redis_snapshot_design.md](redis_snapshot_design.md)

Це **внутрішній** формат (не публічний контракт). Ключові відмінності від публічного bar_v1:

| Відмінність | Redis snap (internal) | Public bar_v1 |
|---|---|---|
| `close_ms` семантика | **end-incl** (`close_time_ms_excl - 1`) | **end-excl** (`open_time_ms + tf_s*1000`) |
| Додаткові поля | `payload_ts_ms`, `seq`, `v` | `time`, `event_ts`, `last_price` |

UDS нормалізує Redis snap → public bar_v1 при читанні.

---

## Правила еволюції схем

1. **Schema-first**: будь-який новий payload/endpoint → спочатку JSON Schema у `core/contracts/`, потім код.
2. **Canonical representation**: час = epoch ms int; поля стабільні; невідомі поля → `additionalProperties: false`.
3. **Сумісність**: legacy поля підтримуються при читанні, але канон визначає одне ім'я.
4. **Версіонування**: `v1` → `v2` лише через окремий initiative + міграція + rollback-план.
5. **JSONL append-only**: формат файлів на диску (CandleBar) не змінюється без ADR.
6. **Guard на вході**: кожен payload проходить guard у `runtime/` (fail-fast). В `ui_chart_v3/server.py`: `_guard_bar_shape`, `_guard_event_shape`, `_guard_meta_shape`.
7. **Бюджети payload**: `_MAX_BARS_CAP` та `_TF_CAP` у server.py обмежують розмір відповідей. Перевищення → loud warning + clamp.
