# P3: UDS / Store — Архітектура UnifiedDataStore та шарів зберігання

> **initiative**: `system_docs`  
> **scope**: `runtime/store/` — UDS, RAM Layer, Redis Layer, Disk Layer, SSOT JSONL, Redis Snapshot, UpdatesBus  
> **метод**: reverse-engineering з коду, кожен факт має `file:line` evidence  
> **залежності**: P1 (Process Inventory), P2 (Data Flow)

---

## Зміст

1. [Зведена діаграма шарів](#1-зведена-діаграма-шарів)
2. [UnifiedDataStore — центральний оркестратор](#2-unifieddatastore--центральний-оркестратор)
3. [Dataclass-и та специфікації](#3-dataclass-и-та-специфікації)
4. [Ролі: Writer vs Reader](#4-ролі-writer-vs-reader)
5. [RAM Layer](#5-ram-layer)
6. [Redis Snapshot Writer](#6-redis-snapshot-writer)
7. [Redis Layer (Reader)](#7-redis-layer-reader)
8. [Disk Layer (JSONL Reader)](#8-disk-layer-jsonl-reader)
9. [SSOT JSONL Writer (JsonlAppender)](#9-ssot-jsonl-writer-jsonlappender)
10. [UpdatesBus (_RedisUpdatesBus)](#10-updatesbus-_redisupdatesbus)
11. [Preview Subsystem](#11-preview-subsystem)
12. [Watermark Model](#12-watermark-model)
13. [Disk Policy](#13-disk-policy)
14. [read_window — пріоритетний ланцюг](#14-read_window--пріоритетний-ланцюг)
15. [read_updates — dual path](#15-read_updates--dual-path)
16. [commit_final_bar — 3-way pipeline](#16-commit_final_bar--3-way-pipeline)
17. [Bootstrap (prime_redis_from_disk)](#17-bootstrap-prime_redis_from_disk)
18. [build_uds_from_config — Factory](#18-build_uds_from_config--factory)
19. [RedisSpec — резолвер підключення](#19-redisspec--резолвер-підключення)
20. [Redis Key Patterns](#20-redis-key-patterns)
21. [Константи та бюджети](#21-константи-та-бюджети)
22. [Guards та інваріанти](#22-guards-та-інваріанти)
23. [Знахідки та ризики](#23-знахідки-та-ризики)

---

## 1. Зведена діаграма шарів

```
                   ┌──────────────────────────────────────────────────┐
                   │                UnifiedDataStore                  │
                   │           runtime/store/uds.py:278               │
                   │          role = "writer" | "reader"              │
                   │                                                  │
                   │  ┌─────────┐  ┌────────────┐  ┌──────────────┐   │
                   │  │ RAM     │  │ Redis      │  │ Disk         │   │
                   │  │ Layer   │  │ Layers     │  │ Layers       │   │
                   │  │         │  │            │  │              │   │
                   │  │ LRU     │  │ Snap+Tail  │  │ SSOT JSONL   │   │
                   │  │ cache   │  │ Preview    │  │ append-only  │   │
                   │  │         │  │ UpdatesBus │  │              │   │
                   │  └─────────┘  └────────────┘  └──────────────┘   │
                   └──────────────────────────────────────────────────┘
                               ▲            ▲            ▲
                               │            │            │
                   ┌───────────┴──┐  ┌──────┴─────┐  ┌───┴──────────┐
                   │ ram_layer.py │  │ redis_     │  │ disk_layer.py│
                   │   (82 LOC)   │  │ layer.py   │  │  (422 LOC)   │
                   │ LRU          │  │ (258 LOC)  │  │ JSONL reader │
                   │ OrderedDict  │  │ tail/snap  │  │ tail/window  │
                   └──────────────┘  │ preview    │  └──────────────┘
                                     └────────────┘        │
                   ┌──────────────┐  ┌────────────┐  ┌─────┴────────┐
                   │ redis_       │  │ redis_     │  │ ssot_jsonl.py│
                   │ snapshot.py  │  │ keys.py    │  │  (378 LOC)   │
                   │ (506 LOC)    │  │ (25 LOC)   │  │ JsonlAppender│
                   │ Snap+Tail    │  │ key format │  │ day rotation │
                   │ writer       │  │ helpers    │  │ append-only  │
                   └──────────────┘  └────────────┘  └──────────────┘
```

**Ownership:**

- **Writer (Connector/M1Poller)**: RAM(write+read), RedisSnapshotWriter(write), JsonlAppender(write), _RedisUpdatesBus(publish). Disk read лише bootstrap.
- **Reader (UI Server)**: RAM(read), RedisLayer(read), DiskLayer(read), _RedisUpdatesBus(read). Ні snap writer, ні JSONL appender.

---

## 2. UnifiedDataStore — центральний оркестратор

**Файл**: `runtime/store/uds.py` (2294 LOC)  
**Клас**: `UnifiedDataStore` (L278)

UDS — єдина точка входу для запису та читання marketdata у всій системі. Усі процеси (Connector, M1 Poller, Tick Preview Worker, UI Server) працюють з даними виключно через UDS API.

### 2.1 Публічний API (Writer)

| Метод | Рядок | Призначення |
|-------|-------|-------------|
| `commit_final_bar(bar)` | L619 | Запис final бару: disk + redis snap + updates bus |
| `publish_promoted_bar(bar)` | L675 | Запис tick-promoted preview бару в Redis ring |
| `publish_preview_bar(bar)` | L723 | Запис preview preview бару: curr + tail + throttled event |
| `bootstrap_prime_from_disk(symbol, tf_s, limit)` | L1053 | Bootstrap: disk → Redis snap/tail |
| `set_cache_state(symbol, tf_s, payload)` | L1133 | Запис cache/status в Redis |
| `set_gap_state(symbol, tf_s, payload)` | L1159 | Запис gap-маркера в Redis |
| `set_prime_ready(symbol, tf_s, payload)` | L1178 | Запис prime:ready маркера |
| `snapshot_status()` | L1008 | Snapshot UDS стану для /api/status |

### 2.2 Публічний API (Reader)

| Метод | Рядок | Призначення |
|-------|-------|-------------|
| `read_window(spec, policy)` | L369 | Читання серії: RAM → Redis → Disk (priority chain) |
| `read_updates(spec)` | L491 | Читання оновлень: preview або final path |
| `read_preview_window(symbol, tf_s, limit)` | L838 | Читання preview серії: tail + curr |
| `read_tail_candles(symbol, tf_s, n)` | L1421 | Швидкий хвіст з disk (DiskLayer) |
| `head_first_open_ms(symbol, tf_s)` | L1455 | Перший open_ms на диску |
| `load_day_open_times(symbol, tf_s, day)` | L1465 | Множина open_ms за день (для dedup) |

---

## 3. Dataclass-и та специфікації

### WindowSpec (L216)

```python
@dataclass(frozen=True)
class WindowSpec:
    symbol: str
    tf_s: int
    limit: int
    since_open_ms: Optional[int] = None
    to_open_ms: Optional[int] = None
    cold_load: bool = False
```

> Evidence: `uds.py:216-225`

**Примітка**: `cold_load` контролює режим bootstrap (tail read з disk). `frozen=True` — immutable.

### ReadPolicy (L227)

```python
@dataclass(frozen=True)
class ReadPolicy:
    force_disk: bool = False
    prefer_redis: bool = False
    disk_policy: str = "never"   # "never" | "bootstrap" | "explicit"
```

> Evidence: `uds.py:227-230`

**Примітка**: `disk_policy` — єдиний спосіб контролю доступу до диску. Немає окремих `min_coldload`/`disk_allowed` полів.

### WindowResult (L243)

```python
@dataclass
class WindowResult:
    bars_lwc: list[dict[str, Any]]   # LWC формат
    meta: dict[str, Any]             # degraded[], source, count тощо
    warnings: list[str]              # окремий список warnings
```

> Evidence: `uds.py:243-246`

**Примітка**: `source` знаходиться всередині `meta`, а не як окреме поле. `warnings` — окреме поле (не всередині meta).

### UpdatesSpec (L248)

```python
@dataclass(frozen=True)
class UpdatesSpec:
    symbol: str
    tf_s: int
    since_seq: Optional[int]
    limit: int
    include_preview: bool = False
```

> Evidence: `uds.py:248-255`

**Примітка**: `include_preview` контролює маршрутизацію dual-path (preview vs final). `since_seq` і `limit` — обов'язкові параметри (без defaults).

### UpdatesResult (L256)

```python
@dataclass
class UpdatesResult:
    events: list[dict[str, Any]]
    cursor_seq: int
    disk_last_open_ms: Optional[int]
    bar_close_ms: Optional[int]
    ssot_write_ts_ms: Optional[int]
    api_seen_ts_ms: int
    meta: dict[str, Any]
    warnings: list[str]
```

> Evidence: `uds.py:256-267`

**Примітка**: 8 полів (не 3). `cursor_seq` — `int` (не Optional). Timestamp поля (`disk_last_open_ms`, `bar_close_ms`, `ssot_write_ts_ms`, `api_seen_ts_ms`) критичні для UI синхронізації.

### CommitResult (L268)

```python
@dataclass
class CommitResult:
    ok: bool
    reason: Optional[str]          # None якщо ok, інакше причина
    ssot_written: bool
    redis_written: bool
    updates_published: bool
    warnings: list[str]
```

> Evidence: `uds.py:268-276`

**Примітка**: `reason` — друге поле (обов'язкове, без default). `warnings` — окреме поле. Порядок полів важливий для позиційних конструкторів.

---

## 4. Ролі: Writer vs Reader

UDS створюється з явною роллю — `"writer"` або `"reader"` (`uds.py:288`).

### Writer

- Створюється для: Connector (D1), M1 Poller (M1+M3)
- Отримує: `JsonlAppender` (disk write), `RedisSnapshotWriter` (snap+tail write), `_RedisUpdatesBus` (publish)
- RAM LRU: `max_keys=8` (за замовчуванням, `ram_layer.py:10`)
- Guard: `_ensure_writer_role()` (L1199) — усі write-методи перевіряють роль

### Reader

- Створюється для: UI Server
- Отримує: `DiskLayer` (disk read), `RedisLayer` (Redis read), `_RedisUpdatesBus` (subscribe)
- RAM LRU: `max_keys = n_symbols × n_tfs + 16` (мінімум 128, `uds.py:2067-2070`)
- **Не має**: JsonlAppender, RedisSnapshotWriter
- Усі write-операції → RuntimeError через role guard

### Як визначається роль (Factory)

```python
# uds.py:2036 — writer
uds_writer = UnifiedDataStore(role="writer", ...)
# uds.py:2059 — reader
uds_reader = UnifiedDataStore(role="reader", ...)
```

> Evidence: `uds.py:2008-2093` (`build_uds_from_config`)

---

## 5. RAM Layer

**Файл**: `runtime/store/layers/ram_layer.py` (82 LOC)

### 5.1 Структура

```python
class RamLayer:
    def __init__(self, max_keys=8, max_bars=60000):
        self._windows: OrderedDict = OrderedDict()
        self._max_keys = max(1, int(max_keys))
        self._max_bars = max(1, int(max_bars))
```

> Evidence: `ram_layer.py:7-13`

### 5.2 Ключ кешу

`(symbol: str, tf_s: int)` — точний кортеж, без нормалізації символу.
> Evidence: `ram_layer.py:24` — `key = (symbol, tf_s)`

### 5.3 Операції

| Метод | Рядок | Поведінка |
|-------|-------|-----------|
| `get_window(symbol, tf_s, limit)` | L19 | LRU touch (move_to_end), повернення `bars[-limit:]` |
| `set_window(symbol, tf_s, bars)` | L34 | Trim до max_bars, LRU evict якщо > max_keys |
| `upsert_bar(symbol, tf_s, bar)` | L52 | Заміна по open_time_ms або append+sort, LRU touch |
| `clear()` | L76 | Повне очищення кешу |

### 5.4 LRU eviction

При `set_window`: якщо кількість ключів > `max_keys`, видаляється **oldest** ключ (FIFO з OrderedDict).

```python
def _evict_if_needed(self):
    while len(self._windows) > self._max_keys:
        self._windows.popitem(last=False)
```

> Evidence: `ram_layer.py:20-22`

### 5.5 upsert_bar logic

1. Шукає бар з тим же `open_time_ms` → замінює на місці
2. Якщо не знайдено → `append` + `sort(key=open_time_ms)`
3. Після insert — LRU touch (`move_to_end`)

> Evidence: `ram_layer.py:52-74`

### 5.6 Бюджети

| Параметр | Writer | Reader |
|----------|--------|--------|
| `max_keys` | 8 | n_symbols × n_tfs + 16 (мін 128) |
| `max_bars` | 60000 | 60000 |

> Evidence: `ram_layer.py:10`, `uds.py:2067-2070`

---

## 6. Redis Snapshot Writer

**Файл**: `runtime/store/redis_snapshot.py` (506 LOC)  
**Клас**: `RedisSnapshotWriter` (L20)

### 6.1 Призначення

Пише **snap** (точковий snapshot останнього бару) та **tail** (останні N барів) у Redis. Використовується тільки Writer-процесами.

### 6.2 Конструктор

```python
def __init__(self, client, ns, ttl_by_tf_s, tail_n_by_tf_s, boot_id):
```

> Evidence: `redis_snapshot.py:21-28`

- `client`: Redis connection
- `ns`: namespace, наприклад `"v3_local"`
- `ttl_by_tf_s`: `Dict[int, int]` — TF → TTL для snap/tail ключів (єдиный TTL на обидва)
- `tail_n_by_tf_s`: `Dict[int, int]` — TF → кількість барів у tail
- `boot_id`: унікальний ID запуску (UUID)

### 6.3 put_bar (L254)

Записує один final бар:

1. Конвертує `CandleBar` → cache dict (`_bar_to_cache_bar`, L114)
2. Записує snap: `{ns}:ohlcv:snap:{symbol_key}:{tf_s}` — JSON з TTL
3. Оновлює tail: читає поточний tail → append бар → trim до `tail_sizes[tf_s]` → write з TTL
4. Публікує `_write_status()` з акумульованим timestamp

> Evidence: `redis_snapshot.py:254-324`

### 6.4 prime_from_bars (L171)

Bootstrap: записує масив барів у snap (=останній) + tail (=останні N барів):

1. Якщо немає барів — skip
2. Snap = останній бар
3. Tail = останні `tail_sizes[tf_s]` барів
4. **Не використовує** put_bar для кожного бару — одна bulk-операція

> Evidence: `redis_snapshot.py:171-240`

### 6.5 set_cache_state / set_gap_state / set_prime_ready (L133-L252)

Допоміжні методи для запису meta/status ключів у Redis:

- `set_cache_state` (L133): Запис payload у `{ns}:cache:state:{sk}:{tf_s}`
- `set_gap_state` (L151): Запис payload у `{ns}:gap:state:{sk}:{tf_s}`
- `set_prime_ready` (L242): Запис payload у `{ns}:prime:ready`

### 6.6 _write_status (L326)

Записує агрегований статус snapshot у Redis:

- Ключ: `{ns}:status:snapshot`
- Payload: `{updated_ms, bars_written, errors, last_symbols: [...]}`

### 6.7 Формат cache bar

```python
{
    "open_ms": int,           # epoch ms (canonical open)
    "close_ms": int,          # end-inclusive (close_ms_excl - 1)
    "o": float, "h": float, "l": float, "c": float, "v": float
}
```

**Примітка**: Формат **не містить** `complete`, `src`, `symbol`, `tf_s` — тільки OHLCV + time. `close_ms` = end-incl (`close_ms_excl - 1`).
> Evidence: `redis_snapshot.py:114-131`

---

## 7. Redis Layer (Reader)

**Файл**: `runtime/store/layers/redis_layer.py` (258 LOC)  
**Клас**: `RedisLayer` (L15)

### 7.1 Призначення

Читання snap/tail/preview даних з Redis. Використовується тільки Reader (UI Server).

### 7.2 Конструктор

```python
def __init__(self, client, namespace: str):
    self._r = client
    self._ns = namespace
```

> Evidence: `redis_layer.py:14-16`

### 7.3 read_tail_or_snap (L18)

```python
def read_tail_or_snap(self, symbol, tf_s) -> tuple[payload, ttl, source, err]:
```

Стратегія: **tail first, snap fallback**:

1. Спроба прочитати tail: `{ns}:ohlcv:tail:{sk}:{tf_s}` → JSON array
2. Якщо tail є → return `(payload, ttl, "tail", None)`
3. Якщо tail немає → читає snap: `{ns}:ohlcv:snap:{sk}:{tf_s}` → JSON object
4. Якщо snap є → return `([snap_bar], ttl, "snap", None)`
5. Якщо нічого → return `(None, None, "empty", "no_data")`

> Evidence: `redis_layer.py:18-72`

### 7.4 Preview операції

| Метод | Рядок | Призначення |
|-------|-------|-------------|
| `read_preview_curr(sym, tf_s)` | L74 | Читає поточний preview бар |
| `read_preview_tail(sym, tf_s)` | L88 | Читає preview tail (масив) |
| `write_preview_curr(sym, tf_s, bar, ttl)` | L102 | Пише поточний preview бар |
| `write_preview_tail(sym, tf_s, bars)` | L112 | Пише preview tail масив |
| `publish_preview_event(sym, tf_s, event, retain)` | L122 | INCR seq → RPUSH event → LTRIM |
| `read_preview_updates(sym, tf_s, since_seq, limit, retain)` | L138 | Читає preview events з ring |
| `get_prime_ready_payload(sym, tf_s)` | L194 | Читає prime:ready маркер |

### 7.5 Preview Updates — Gap Detection та Fast-Forward

`read_preview_updates` (L138) реалізує:

1. **since_seq=None** (перший poll) → **adopt-tail**: читає поточний max seq, повертає `events=[], cursor_seq=max_seq`. UI починає з порожнього стану і на наступному poll отримає нові events.
2. **since_seq < oldest_in_ring** → **cursor_gap**: ring переповнився, старі events втрачено. Повертає `events=[], cursor_seq=max_seq, gap={reason: "cursor_gap", ...}`. UI має зробити reload.
3. **since_seq в межах ring** → нормальний інкрементальний poll: фільтрує events з `seq > since_seq`, повертає `cursor_seq=max(event_seqs)`.

> Evidence: `redis_layer.py:138-192`

---

## 8. Disk Layer (JSONL Reader)

**Файл**: `runtime/store/layers/disk_layer.py` (422 LOC)  
**Клас**: `DiskLayer` (L321)

### 8.1 Призначення

Читання барів з JSONL файлів на диску. Read-only — ніколи не пише.

### 8.2 Конструктор

```python
class DiskLayer:
    def __init__(self, data_root: str) -> None:
        self._data_root = data_root
```

> Evidence: `disk_layer.py:321-325`

### 8.3 Файлова структура

```
data_v3/
  {symbol_key}/          # напр. XAU_USD (/ → _)
    tf_{tf_s}/           # напр. tf_60, tf_86400
      part-YYYYMMDD.jsonl
```

> Evidence: `disk_layer.py:327-337` (`list_parts`)

### 8.4 list_parts (L327)

Повертає відсортований список файлів `part-*.jsonl` для (symbol, tf_s).

```python
def list_parts(self, symbol, tf_s) -> list[str]:
    d = os.path.join(self._data_root, symbol.replace("/", "_"), f"tf_{tf_s}")
    parts = [os.path.join(d, x) for x in os.listdir(d)
             if x.startswith("part-") and x.endswith(".jsonl")]
    parts.sort()
    return parts
```

> Evidence: `disk_layer.py:327-337`

### 8.5 read_window_with_geom (L360)

Основний метод читання з геометричними перевірками:

```python
def read_window_with_geom(self, symbol, tf_s, limit, *,
    since_open_ms=None, to_open_ms=None, use_tail=False,
    final_only=False, skip_preview=False, final_sources=None
) -> tuple[list[dict], Optional[dict]]:
```

Два режими:

- **use_tail=True**: використовує `_read_jsonl_tail_filtered_with_geom` — зворотне читання файлів (від кінця), ефективне для "останніх N барів"
- **use_tail=False**: використовує `_read_jsonl_filtered` — пряме читання з фільтрами

> Evidence: `disk_layer.py:360-401`

### 8.6 Зворотне читання (_iter_lines_reverse)

```python
def _iter_lines_reverse(path: str):
    # Читає файл від кінця блоками по 8192 байт
    # yield рядки у зворотному порядку
```

> Evidence: `disk_layer.py:15-46`

### 8.7 Фільтрація барів

| Функція | Рядок | Призначення |
|---------|-------|-------------|
| `_bar_passes_filters(obj, final_only, skip_preview, final_sources)` | L122 | Pass/fail за критеріями |
| `_choose_better_bar(existing, incoming)` | L143 | complete > incomplete, final > non-final, newer > older |
| `_dedup_open_ms(bars)` | L200 | Dedup по open_time_ms з _choose_better_bar |
| `_finalize_tail_with_geom(out)` | L265 | Sort + dedup + geom metadata |

> Evidence: `disk_layer.py:122-284`

### 8.8 Geom metadata

Якщо дані потребували сортування або dedup, повертається:

```python
geom = {"sorted": True, "dedup_dropped": int}
```

Це потім потрапляє в `meta.extensions.geom_fix` у WindowResult.

### 8.9 last_open_ms (L403)

Читає останній `open_time_ms` з останнього part-файлу. Використовується для ініціалізації watermark.
> Evidence: `disk_layer.py:403-413`

---

## 9. SSOT JSONL Writer (JsonlAppender)

**Файл**: `runtime/store/ssot_jsonl.py` (378 LOC)  
**Клас**: `JsonlAppender` (L62)

### 9.1 Призначення

Append-only запис canonical final барів у JSONL файли. SSOT для historical даних.

### 9.2 Конструктор

```python
class JsonlAppender:
    def __init__(self, root, day_anchor_offset_s=0,
                 day_anchor_offset_s_d1=None,
                 day_anchor_offset_s_d1_alt=None,
                 day_anchor_offset_s_alt=None,
                 day_anchor_offset_s_alt2=None):
        self._root = root
        self._open_files: dict = {}       # відкриті file handles
        self._drop_preview_total = 0      # лічильник відкинутих non-final
```

> Evidence: `ssot_jsonl.py:80-100`

### 9.3 append(bar: CandleBar) (L110)

Алгоритм:

1. **FINAL_SOURCES guard**: якщо `not bar.complete or bar.src not in FINAL_SOURCES` → `logging.error()` + інкремент `_drop_preview_total` + return
2. **path resolution**: `_path_for(symbol, tf_s, open_time_ms)` — визначає файл за день + anchor offset
3. **file rotation**: якщо path != поточний відкритий handle → закрити попередній, відкрити новий
4. **JSON серіалізація**: `json.dumps(bar_dict)` + `\n`
5. **flush**: `f.flush()` (fsync не використовується для performance)

> Evidence: `ssot_jsonl.py:110-155`

### 9.4 FINAL_SOURCES guard

```python
FINAL_SOURCES = {"history", "derived", "history_agg"}
```

Бари з `not bar.complete or bar.src not in FINAL_SOURCES` відкидаються з **`logging.error()`** (на кожен drop, не one-shot). Лічильник `_drop_preview_total` інкрементується.

> Evidence: `ssot_jsonl.py:110-123`, визначення в `uds.py:42`

### 9.5 Файлова структура

```
data_v3/{symbol_key}/tf_{tf_s}/part-{YYYYMMDD}.jsonl
```

Де `YYYYMMDD` визначається за:

- `open_time_ms` бару → UTC datetime
- **Anchor offset** для H4 (14400) та D1 (86400): конфігурується через параметри конструктора:
  - `day_anchor_offset_s` — основний offset (75600 = 21:00 UTC зима)
  - `day_anchor_offset_s_d1` / `day_anchor_offset_s_d1_alt` — D1-специфічні offsets (для DST)
  - `_path_for()` (L102): визначає day з `ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")`

> Evidence: `ssot_jsonl.py:80-110` (конструктор + `_path_for`)

### 9.6 Формат рядка JSONL

```json
{"symbol":"XAU/USD","tf_s":60,"open_time_ms":1700000000000,"close_time_ms":1700000060000,"o":2000.0,"h":2001.0,"low":1999.0,"c":2000.5,"v":100.0,"complete":true,"src":"history"}
```

### 9.7 close()

Закриває всі відкриті file handles (`_open_files`). Викликається при shutdown UDS.
> Evidence: `ssot_jsonl.py` (method в класі JsonlAppender)

---

## 10. UpdatesBus (_RedisUpdatesBus)

**Файл**: `runtime/store/uds.py` (L1896-L1977)  
**Клас**: `_RedisUpdatesBus` (L1896)

### 10.1 Призначення

Event stream для live-оновлень UI. Кожен committed final бар публікується як event. UI poll-ить `/api/updates` який читає з цього bus.

### 10.2 Структура зберігання

- **Seq counter**: `{ns}:updates:seq:{symbol}:{tf_s}` (Redis string, INCR)
- **Events ring**: `{ns}:updates:list:{symbol}:{tf_s}` (Redis list, RPUSH + LTRIM)

**УВАГА**: Порядок ключів: `ns:updates:seq:symbol:tf_s` (seq/list **перед** symbol/tf_s), формується через `_key("updates", "seq", symbol, tf_s)`.

UpdatesBus використовує **raw symbol** (з `/`), на відміну від snap/tail які використовують `symbol_key()` (з `_`).

> Evidence: `uds.py:1901-1907` — `_key` helper та використання у `publish()`

### 10.3 publish (L1905)

```python
def publish(self, event: dict[str, Any]) -> Optional[int]:
    seq_key = self._key("updates", "seq", str(event["key"]["symbol"]), str(event["key"]["tf_s"]))
    list_key = self._key("updates", "list", str(event["key"]["symbol"]), str(event["key"]["tf_s"]))
    seq = int(self._client.incr(seq_key))    # atomic increment
    event["seq"] = seq
    self._client.rpush(list_key, json.dumps(event))
    self._client.ltrim(list_key, -self._retain, -1)  # keep last N
    return seq
```

**Примітка**: `publish` приймає один параметр `event` (не `symbol, tf_s, event`). Symbol і tf_s витягуються з `event["key"]`.
> Evidence: `uds.py:1905-1915`

### 10.4 read_updates (L1917)

```python
def read_updates(self, symbol, tf_s, since_seq, limit) -> tuple[events, cursor_seq, gap_info, err]:
```

1. LRANGE list_key з `-retain` до `-1` → parse JSON
2. Трекінг min_seq/max_seq по всіх events
3. Якщо `since_seq is not None` → фільтр `seq > since_seq`
4. Trim до limit
5. `cursor_seq` = max(event seqs) або `since_seq` якщо немає events
6. Gap detection: якщо `min_seq > since_seq + 1` і `since_seq > 0` → gap

> Evidence: `uds.py:1917-1977`

### 10.5 Retain бюджет

`UPDATES_REDIS_RETAIN_DEFAULT = 2000` (L94)
> Evidence: `uds.py:94`

---

## 11. Preview Subsystem

Preview — це підсистема для real-time відображення поточних (незавершених) барів у UI. Працює **паралельно** з final pipeline.

### 11.1 Три рівні зберігання Preview

| Рівень | Redis Key Pattern | Призначення | TTL/Retain |
|--------|-------------------|-------------|------------|
| **curr** | `{ns}:preview:curr:{sk}:{tf_s}` | Поточний preview бар | 120s TTL |
| **tail** | `{ns}:preview:tail:{sk}:{tf_s}` | Масив останніх preview барів | no TTL, max 2000 |
| **events** | `{ns}:preview:updates:{sk}:{tf_s}:list` | Event ring для poll | max 2000 |

> Evidence: `redis_keys.py:9-22` (ключі), `uds.py:44-46` (константи)

### 11.2 Preview TFs

Тільки TF зі списку `preview_tf_allowlist` (з config): зараз `[60, 180]` (M1, M3).
> Evidence: `uds.py:324` — `self._preview_tf_allowlist = set(preview_tf_allowlist or DEFAULT_PREVIEW_TF_ALLOWLIST)`

### 11.3 publish_preview_bar flow (L723)

1. **Guards** (L728-L756):
   - `bar.complete is True` → reject (preview = incomplete only)
   - `bar.src in FINAL_SOURCES` → reject (preview ≠ final)
   - `tf_s not in preview_tfs` → reject
   - `redis_layer is None` → reject
2. **Write curr** (L771-L783): JSON SET з TTL (120s default)
3. **Read existing tail** (L787-L790): JSON GET
4. **Merge** (L793-L811): curr + tail → dedup по open_time_ms → sort → trim до `preview_tail_retain`
5. **Write tail** (L812-L815): JSON SET (без TTL)
6. **Throttled event** (L817-L835): виклик `_publish_preview_update`

> Evidence: `uds.py:723-835`

### 11.4 _publish_preview_update — Throttle (L972)

```python
def _publish_preview_update(self, symbol, tf_s, bar, *, force=False):
    now = time.monotonic()
    elapsed = now - self._preview_last_publish.get(key, 0)
    if not force and elapsed < 0.5:  # 500ms throttle
        return
```

**Виняток**: `force=True` при rollover (новий open_time_ms > попередній curr) — щоб UI одразу побачив новий бар.
> Evidence: `uds.py:972-1004`

### 11.5 Bridge: Final → Preview Ring (L935)

Коли commit_final_bar записує final бар для preview TF, він також пушить його в preview ring:

```python
def _publish_final_to_preview_ring(self, bar):
    # Публікує final бар як preview event, щоб UI preview-потік
    # бачив і final бари (I3: final > preview)
```

> Evidence: `uds.py:935-970`

### 11.6 read_preview_window (L838)

Читає повну preview серію для UI:

1. Читає tail з Redis (масив)
2. Читає curr з Redis (один бар)
3. Merge tail + curr → sorted → dedup по open_time_ms
4. Повертає `(bars, meta)`

> Evidence: `uds.py:838-910`

---

## 12. Watermark Model

### 12.1 Призначення

Watermark — це **per-key монотонний маркер** `(symbol, tf_s) → last_open_ms`. Гарантує, що stale/out-of-order бари не записуються в SSOT.

### 12.2 Зберігання

```python
self._wm_by_key: dict[tuple[str, int], int] = {}  # writer only
```

> Evidence: `uds.py:320`

### 12.3 Ініціалізація (_init_watermark_for_key, L1208)

При першому `commit_final_bar` для нового ключа (symbol, tf_s):

1. Читає `DiskLayer.last_open_ms(symbol, tf_s)` — останній open_ms на диску
2. Записує в `_wm_by_key[(symbol, tf_s)] = last_open_ms` або 0

> Evidence: `uds.py:1208-1222`

### 12.4 Guard при commit (L647-L660)

```python
wm_key = (bar.symbol, bar.tf_s)
if wm_key not in self._wm_by_key:
    self._init_watermark_for_key(bar.symbol, bar.tf_s)
wm = self._wm_by_key.get(wm_key, 0)
if bar.open_time_ms <= wm:
    return CommitResult(ok=False, reason="watermark_stale", ...)
```

> Evidence: `uds.py:647-660`

### 12.5 Оновлення після commit (L665)

```python
self._wm_by_key[wm_key] = bar.open_time_ms
```

> Evidence: `uds.py:665`

### 12.6 Семантика

- **drop_stale**: Бар з `open_time_ms <= watermark` → rejected, `reason="watermark_stale"`
- **Монотонність**: watermark тільки зростає (ніколи не зменшується)
- **Per-key**: окремий watermark для кожної пари (symbol, tf_s)
- **Loud**: rejected бар логується (`_log_warn`), але не спричиняє crash

---

## 13. Disk Policy

### 13.1 Призначення

Контролює, чи дозволено читання з диску у reader-процесі (UI Server). Три режими:

### 13.2 Режими

| Режим | Поведінка |
|-------|-----------|
| `"never"` | Диск заборонено взагалі |
| `"bootstrap"` | Диск дозволено протягом `BOOTSTRAP_WINDOW_S` (60с) після boot |
| `"explicit"` | Диск дозволено завжди (для scrollback/recovery) |

### 13.3 Реалізація (_disk_allowed, L1304)

```python
def _disk_allowed(self, policy: ReadPolicy, reason: str) -> bool:
    dp = policy.disk_policy                    # "never" | "bootstrap" | "explicit"
    if dp == DISK_POLICY_EXPLICIT:
        return True
    if dp == DISK_POLICY_BOOTSTRAP:
        elapsed = time.time() - self._boot_ts  # time.time(), не monotonic
        if elapsed <= BOOTSTRAP_WINDOW_S:       # 60s window
            self._disk_bootstrap_reads += 1
            return True
        # bootstrap вікно вичерпано
    # dp == "never" або bootstrap expired
    self._disk_hotpath_blocked += 1
    # rate-limited warning log (5s interval)
    return False
```

> Evidence: `uds.py:1304-1335`

**Примітка**: Сигнатура `(policy: ReadPolicy, reason: str)` — disk_policy читається з `policy`, не з instance. Лічильники `_disk_hotpath_blocked` та `_disk_bootstrap_reads` — телеметрія. Використовує `time.time()` (не `time.monotonic()`).

### 13.4 Де використовується

- `read_window()` (L369): якщо Redis не має даних або їх мало — `_disk_allowed(policy, reason)` перед fallback на disk
- `_read_window_disk()` (L1337): безпосереднє читання

### 13.5 Константи

```python
BOOTSTRAP_WINDOW_S = 60  # uds.py:237
```

---

## 14. read_window — пріоритетний ланцюг

**Метод**: `UnifiedDataStore.read_window(spec, policy)` (L369)

### 14.1 Алгоритм (повний)

```
read_window(spec, policy)
│
├─ 1. force_disk=True? ────────────────────────── → _read_window_disk()
│                                                    return (bars, "disk")
├─ 2. prefer_redis=True?
│   ├─ 2a. _read_window_redis(...)
│   │   ├─ RedisLayer.read_tail_or_snap()         # tail first, snap fallback
│   │   ├─ parse → bars
│   │   ├─ len(bars) < min_coldload?
│   │   │   ├─ YES → _disk_allowed()?
│   │   │   │   ├─ YES → _read_window_disk()      # disk enrich
│   │   │   │   └─ NO → return bars as-is + degraded
│   │   │   └─ NO → return bars ("redis")
│   │   └─ bars → RAM set_window (cache)
│   └─ return (bars_lwc, "redis"|"disk")
│
├─ 3. RAM check: ram.get_window(...)
│   ├─ HIT → return (bars, "ram")
│   └─ MISS → continue
│
├─ 4. _disk_allowed()?
│   ├─ YES → _read_window_disk()
│   │   └─ bars → RAM set_window → return (bars, "disk")
│   └─ NO → return ([], "empty") + degraded
│
└─ return WindowResult
```

### 14.2 _read_window_redis (L1475)

1. `RedisLayer.read_tail_or_snap(symbol, tf_s)` → `(payload, ttl, source, err)`
2. Parse Redis JSON → canonical bar dicts (`_redis_payload_to_bars`)
3. **min_coldload check** (L1530-L1560): якщо `len(bars) < policy.min_coldload`:
   - `_disk_allowed()` → якщо YES: `_read_window_disk()` замість Redis
   - Якщо NO: return Redis bars + `warnings["history_short"]`
4. Конвертація в LWC format (`_bars_to_lwc`)
5. Запис у RAM cache (`ram.set_window`)

> Evidence: `uds.py:1475-1575`

### 14.3 _read_window_disk (L1337)

1. `DiskLayer.read_window_with_geom(...)` → `(bars, geom)`
2. Якщо `geom is not None` → `_mark_geom_fix(meta, geom)` (degraded but loud)
3. `_ensure_sorted_dedup(bars)` (L1769) — додатковий sort+dedup guard
4. Конвертація `_bars_to_lwc(bars)`
5. Запис у RAM cache

> Evidence: `uds.py:1337-1375`

### 14.4 _bars_to_lwc (L1577)

Конвертація canonical bar dict → LWC (Lightweight Chart) format:

```python
{
    "time": open_time_ms // 1000,   # UNIX seconds (не ms!)
    "open": float, "high": float, "low": float, "close": float,
    "volume": float,
    # meta поля збережені
}
```

> Evidence: `uds.py:1577-1625`

---

## 15. read_updates — dual path

**Метод**: `UnifiedDataStore.read_updates(spec)` (L491)

### 15.1 Preview path (для preview TFs)

Якщо `spec.tf_s in self._preview_tf_allowlist`:

```python
events, cursor_seq, gap, err = self._redis_layer.read_preview_updates(
    spec.symbol, spec.tf_s, spec.since_seq,
    spec.limit, self._preview_updates_retain
)
```

> Evidence: `uds.py:523-536`

### 15.2 Final path (для не-preview TFs)

```python
events, cursor_seq, gap_info, err = self._updates_bus.read_updates(
    spec.symbol, spec.tf_s, spec.since_seq, spec.limit
)
```

> Evidence: `uds.py:541-570`

### 15.3 RAM cache population (L587)

Після отримання final events, UDS upsert-ить бари в RAM кеш:

```python
for ev in events:
    if ev.get("complete") is True and ev.get("source") in FINAL_SOURCES:
        self._ram_layer.upsert_bar(symbol, tf_s, bar_from_event(ev))
```

Це дозволяє наступному `read_window` з RAM отримати актуальні дані без Redis/disk.
> Evidence: `uds.py:580-600`

---

## 16. commit_final_bar — 3-way pipeline

**Метод**: `UnifiedDataStore.commit_final_bar(bar)` (L619)

### 16.1 Повний pipeline

```
commit_final_bar(bar: CandleBar)
│
├── GUARD 1: isinstance(bar, CandleBar)?        L627
├── GUARD 2: bar.complete is True?               L630
├── GUARD 3: bar.src in FINAL_SOURCES?           L638
├── GUARD 4: _ensure_writer_role()               L643
│
├── WATERMARK CHECK                              L647-L660
│   ├── _init_watermark_for_key() if needed
│   └── bar.open_time_ms <= wm? → reject "watermark_stale"
│
├── 3-WAY WRITE:
│   ├── 1) _append_to_disk(bar)                  L661
│   │     └── JsonlAppender.append(bar)
│   ├── 2) _write_redis_snapshot(bar)            L662
│   │     └── RedisSnapshotWriter.put_bar(bar)
│   └── 3) _publish_update(bar)                  L663
│         └── _RedisUpdatesBus.publish(event)
│
├── POST-COMMIT:
│   ├── watermark update                         L665
│   ├── RAM upsert                               L667
│   └── preview bridge (if preview TF)           L669-L670
│         └── _publish_final_to_preview_ring(bar)
│
└── return CommitResult(ok=True, ssot_written=True)  L672
```

### 16.2 Guards (детально)

| # | Guard | Рядок | Поведінка при fail |
|---|-------|-------|-------------------|
| 1 | `_ensure_writer_role()` | L625 | `RuntimeError` (перша перевірка) |
| 2 | `isinstance(bar, CandleBar)` | L627 | `ok=False, reason="invalid_bar"` |
| 3 | `bar.complete is True` | L630 | `ok=False, reason="not_complete"` |
| 4 | `bar.src in FINAL_SOURCES` | L638 | `ok=False, reason="non_final_source"` |
| 5 | `bar.open_time_ms <= watermark` | L650 | `ok=False, reason=drop_reason` |

### 16.3 _publish_update — Event Formation (L1276)

```python
def _publish_update(self, bar, warnings):
    bar_payload = bar.to_dict()  # повний CandleBar dict
    event = {
        "key": {"symbol": bar.symbol, "tf_s": int(bar.tf_s), "open_ms": int(bar.open_time_ms)},
        "bar": bar_payload,
        "complete": bool(bar.complete),
        "source": str(bar.src),
        "event_ts": int(bar_payload.get("close_time_ms")) if bar.complete else None,
    }
    self._updates_bus.publish(event)
```

> Evidence: `uds.py:1276-1302`

**Примітка**: `bar` серіалізується через `bar.to_dict()` (не вручну). Event містить `event_ts` (close_time_ms для complete барів). `updates_bus.publish(event)` приймає один аргумент.

---

## 17. Bootstrap (prime_redis_from_disk)

**Метод**: `UnifiedDataStore.bootstrap_prime_from_disk(symbol, tf_s, limit)` (L1053)

### 17.1 Алгоритм

1. **Role guard**: `_ensure_writer_role()`
2. **Disk read**: `DiskLayer.read_window(symbol, tf_s, limit, use_tail=True, final_only=True, final_sources=FINAL_SOURCES)` — читає останні `limit` final барів
3. **Конвертація**: кожен `dict → CandleBar` (`_disk_bar_to_candle`, L48)
4. **Redis prime**: `RedisSnapshotWriter.prime_from_bars(symbol, tf_s, bars)` — одна bulk-операція: snap = останній, tail = останні N
5. **Watermark init**: `_init_watermark_for_key(symbol, tf_s)` — з останнього бару

> Evidence: `uds.py:1053-1117`

### 17.2 Коли викликається

- З `composition.py` на старті Connector та M1 Poller
- Для кожного (symbol, tf_s) з конфігу
- **До початку polling** — гарантує що Redis має дані для UI cold load

### 17.3 _disk_bar_to_candle (L48)

```python
def _disk_bar_to_candle(obj: dict, *, symbol=None, tf_s=None) -> Optional[CandleBar]:
    # Парсить dict → CandleBar з fallback полів:
    # "low" -> "l" (legacy compatibility)
    # symbol/tf_s з obj або з параметрів
```

> Evidence: `uds.py:48-92`

---

## 18. build_uds_from_config — Factory

**Функція**: `build_uds_from_config(config_path, role, ...)` (L2008)

### 18.1 Створення Writer

```python
# L2020-2055 (спрощено):
redis_spec = resolve_redis_spec(cfg, role="writer")
redis_client = redis.Redis(host=spec.host, port=spec.port, db=spec.db)
jsonl_appender = JsonlAppender(data_root, anchor_offsets=offsets)
redis_writer = RedisSnapshotWriter(redis_client, ns, tail_sizes, snap_ttl, tail_ttl)
disk_layer = DiskLayer(data_root)
ram = RamLayer(max_keys=8)
updates_bus = _RedisUpdatesBus(redis_client, ns, retain)
redis_layer = _redis_layer_from_cfg(cfg)

uds = UnifiedDataStore(
    role="writer",
    ram_layer=ram,
    disk_layer=disk_layer,
    redis_layer=redis_layer,
    ssot_writer=jsonl_appender,
    redis_writer=redis_writer,
    updates_bus=updates_bus,
    preview_tfs=preview_tfs,
    ...
)
```

> Evidence: `uds.py:2008-2055`

### 18.2 Створення Reader

```python
# L2059-2090 (спрощено):
redis_spec = resolve_redis_spec(cfg, role="reader")
redis_client = redis.Redis(...)
disk_layer = DiskLayer(data_root)
n_keys = max(128, n_symbols * n_tfs + 16)
ram = RamLayer(max_keys=n_keys)
updates_bus = _RedisUpdatesBus(redis_client, ns, retain)
redis_layer = RedisLayer(redis_client, ns)

uds = UnifiedDataStore(
    role="reader",
    ram_layer=ram,
    disk_layer=disk_layer,
    redis_layer=redis_layer,
    ssot_writer=None,           # reader не пише SSOT
    redis_writer=None,          # reader не пише snap/tail
    updates_bus=updates_bus,
    preview_tfs=preview_tfs,
    ...
)
```

> Evidence: `uds.py:2059-2093`

### 18.3 Redis Layer створення

`_redis_layer_from_cfg(cfg)` (L1886): створює `RedisLayer(client, namespace)` з окремого виклику `resolve_redis_spec`.
> Evidence: `uds.py:1886-1894`

---

## 19. RedisSpec — резолвер підключення

**Файл**: `runtime/store/redis_spec.py` (153 LOC)  
**Функція**: `resolve_redis_spec(cfg, role, log)` (L46)

### 19.1 Призначення

Резолвить Redis connection параметри з config.json + можливий ENV override.

### 19.2 Алгоритм

1. Читає `cfg["redis"]` — host, port, db, namespace
2. Перевіряє `enabled=True` (інакше → None)
3. Перевіряє: `"ns"` key заборонено (→ RuntimeError), `"namespace"` обов'язковий
4. Якщо `allow_env_override=True` → перевіряє ENV змінні `FXCM_REDIS_{HOST|PORT|DB|NS}`
5. Якщо ENV !== config → mismatch warning (loud)
6. Повертає `RedisSpec` dataclass

> Evidence: `redis_spec.py:46-153`

### 19.3 Guards

- `"ns" in raw` → `RuntimeError("redis_ns_key_forbidden")` — захист від legacy key
- `namespace is None` → `RuntimeError("redis_namespace_missing")`
- Mismatch ENV vs config → `UDS_REDIS_SPEC_MISMATCH` warning (log once)
- ENV override без `allow_env_override=True` → `UDS_REDIS_ENV_OVERRIDE_IGNORED` warning

### 19.4 RedisSpec dataclass (L12)

```python
@dataclass(frozen=True)
class RedisSpec:
    host: str
    port: int
    db: int
    namespace: str
    source: str              # "config" | "env_override"
    cfg_host: str            # оригінал з json
    cfg_port: int
    cfg_db: int
    cfg_namespace: str
    mismatch: bool
    mismatch_fields: list[str]
```

> Evidence: `redis_spec.py:12-25`

---

## 20. Redis Key Patterns

**Файл**: `runtime/store/redis_keys.py` (25 LOC)

### 20.1 symbol_key (L5)

```python
def symbol_key(symbol: str) -> str:
    return str(symbol).strip().replace("/", "_")
```

Усі Redis ключі (крім UpdatesBus) використовують нормалізований символ: `XAU/USD → XAU_USD`.

### 20.2 Повна таблиця ключів

| Категорія | Pattern | Модуль | symbol format |
|-----------|---------|--------|---------------|
| Snap | `{ns}:ohlcv:snap:{sk}:{tf_s}` | redis_snapshot.py | `symbol_key()` |
| Tail | `{ns}:ohlcv:tail:{sk}:{tf_s}` | redis_snapshot.py | `symbol_key()` |
| Status | `{ns}:status:snapshot` | redis_snapshot.py | — |
| Cache state | `{ns}:cache:state:{sk}:{tf_s}` | redis_snapshot.py | `symbol_key()` |
| Gap state | `{ns}:gap:state:{sk}:{tf_s}` | redis_snapshot.py | `symbol_key()` |
| Prime ready | `{ns}:prime:ready` | redis_snapshot.py | — |
| Preview curr | `{ns}:preview:curr:{sk}:{tf_s}` | redis_keys.py | `symbol_key()` |
| Preview tail | `{ns}:preview:tail:{sk}:{tf_s}` | redis_keys.py | `symbol_key()` |
| Preview seq | `{ns}:preview:updates:{sk}:{tf_s}:seq` | redis_keys.py | `symbol_key()` |
| Preview list | `{ns}:preview:updates:{sk}:{tf_s}:list` | redis_keys.py | `symbol_key()` |
| Updates seq | `{ns}:updates:seq:{symbol}:{tf_s}` | uds.py:1906 | **raw** (`/`) |
| Updates list | `{ns}:updates:list:{symbol}:{tf_s}` | uds.py:1907 | **raw** (`/`) |

> **ЗНАХІДКА F1**: UpdatesBus використовує raw symbol (`XAU/USD`), а всі інші ключі — `symbol_key()` (`XAU_USD`). Це працює, але означає два різних формати ключів у одному Redis db. Потенційний ризик при міграції/скриптах.

---

## 21. Константи та бюджети

### 21.1 UDS константи (uds.py)

| Константа | Значення | Рядок | Призначення |
|-----------|----------|-------|-------------|
| `FINAL_SOURCES` | `{"history","derived","history_agg"}` | L42 | Дозволені src для final барів |
| `PREVIEW_CURR_TTL_S` | `120` | L44 | TTL для preview curr key |
| `PREVIEW_TAIL_RETAIN` | `2000` | L45 | Макс барів у preview tail |
| `PREVIEW_UPDATES_RETAIN` | `2000` | L46 | Макс events у preview ring |
| `UPDATES_REDIS_RETAIN_DEFAULT` | `2000` | L94 | Макс events у final updates ring |
| `BOOTSTRAP_WINDOW_S` | `60` | L237 | Час (сек) після boot коли disk дозволено |

### 21.2 RAM бюджети

| Параметр | Writer | Reader | Рядок |
|----------|--------|--------|-------|
| `max_keys` | 8 | n_sym×n_tf+16 (мін 128) | ram_layer.py:10, uds.py:2067 |
| `max_bars` | 60000 | 60000 | ram_layer.py:15 |

### 21.3 Redis TTL (з config.json)

| TF | snap TTL | tail TTL | tail size |
|----|----------|----------|-----------|
| 60 (M1) | config | config | config |
| 180 (M3) | config | config | config |
| 300 (M5) | config | config | config |
| ... | ... | ... | ... |

TTL та tail_sizes конфігуруються у `config.json` → `redis.snap_ttl`, `redis.tail_ttl`, `redis.tail_sizes`.

> Evidence: `redis_snapshot.py:21-47` (конструктор), `uds.py:2030-2040` (config parsing)

### 21.4 Preview throttle

- **Interval**: 500ms (`uds.py:992` — `elapsed < 0.5`)
- **Force**: при rollover (новий open_ms > попередній) → throttle bypassed

---

## 22. Guards та інваріанти

### 22.1 Таблиця guards

| Guard | Розташування | Інваріант | Поведінка |
|-------|-------------|-----------|-----------|
| Writer role guard (_ensure_writer_role) | `uds.py:625` | I1 | RuntimeError |
| CandleBar type check | `uds.py:627` | I1 | CommitResult(ok=False, reason="invalid_bar") |
| complete=True check | `uds.py:630` | I3 | CommitResult(ok=False, reason="invalid_bar") |
| FINAL_SOURCES check | `uds.py:638` | I3/NoMix | CommitResult(ok=False, reason="non_final_source") |
| Watermark drop-stale | `uds.py:647-660` | I1 | CommitResult(ok=False, reason="stale") |
| Preview: no complete | `uds.py:728` | I3 | early return |
| Preview: no FINAL_SOURCES | `uds.py:736` | I3 | early return |
| Preview: TF allowlist | `uds.py:740` | config | early return |
| SSOT JSONL: FINAL_SOURCES | `ssot_jsonl.py:95-110` | I3 | `logging.error()` on every drop |
| Redis ns_key_forbidden | `redis_spec.py:57` | config | RuntimeError |
| Redis namespace_missing | `redis_spec.py:59` | config | RuntimeError |
| Disk policy | `uds.py:1304-1335` | I1 | (False, reason) |
| sorted_dedup | `uds.py:1769` | I2/geom | sort + dedup + loud |

### 22.2 Інваріанти UDS

| # | Інваріант | Де enforced |
|---|-----------|-------------|
| I1 | Всі writes через UDS | Role guard, UDS API |
| I2 | Canonical time: epoch_ms int | _bars_to_lwc, event formation |
| I3 | Final > Preview | FINAL_SOURCES guard, complete check, bridge |
| I4 | Один update потік | _RedisUpdatesBus для final, preview ring для preview |
| I5 | Degraded-but-loud | _mark_degraded, _mark_geom_fix, warnings[] |
| WM | Watermark monotonic | commit_final_bar guard |

---

## 23. Знахідки та ризики

### F1: UpdatesBus raw symbol vs symbol_key (РИЗИК: низький)

**Факт**: UpdatesBus ключі використовують raw symbol (`XAU/USD`) — `uds.py:1908-1910`. Всі інші Redis ключі (snap, tail, preview) використовують `symbol_key()` (`XAU_USD`) — `redis_keys.py:5-6`.

**Вплив**: Працює коректно, бо writer і reader використовують одну і ту ж конвенцію. Але при ручному debug/скриптах Redis keys виглядають неконсистентно.

**Рекомендація**: Документувати як відому поведінку. Уніфікація — окремий initiative.

---

### F2: SSOT JSONL drop non-final з logging.error (РИЗИК: низький)

**Факт**: `JsonlAppender.append()` відкидає бари з `src not in FINAL_SOURCES` з `logging.error()` **на кожен drop** (не one-shot) — `ssot_jsonl.py:95-110`. Лічильник `_drop_preview_total` інкрементується.

**Вплив**: Це правильна поведінка (preview не має потрапляти на disk). Але `logging.error()` на кожен drop може спамити логи під навантаженням.

**Рекомендація**: Замінити на rate-limited warning (Правило 9.1) або one-shot з лічильником. Додати метрику `ssot_dropped_non_final_total`.

---

### F3: RAM LRU max_keys=8 для writer (РИЗИК: середній)

**Факт**: Writer RAM LRU має `max_keys=8` (default), при тому що існує 13 символів × 8 TF = 104 можливих комбінацій.

**Вплив**: Writer використовує RAM тільки для upsert після commit (L667) і зрідка для read. LRU eviction відбувається часто, але це не критично — writer не покладається на RAM для продуктивності.

**Рекомендація**: Поточне значення прийнятне. Якщо додати writer-side cache для hot keys — збільшити.

---

### F4: Disk policy "bootstrap" — 60s вікно (РИЗИК: середній)

**Факт**: `BOOTSTRAP_WINDOW_S = 60` — `uds.py:237`. Після 60с boot reader не може звертатись до disk для fallback (окрім `force_disk`).

**Вплив**: Якщо Redis prime не завершився за 60с (повільний disk, багато символів) — UI отримає порожні дані без fallback, тільки degraded warning.

**Рекомендація**: Моніторити час bootstrap. Якщо >60с для будь-якого (symbol, tf_s) — збільшити вікно або переглянути кількість prime-ованих барів.

---

### F5: Preview tail — необмежений TTL (РИЗИК: низький)

**Факт**: Preview tail (`{ns}:preview:tail:{sk}:{tf_s}`) не має TTL — `redis_layer.py:112`. Тільки trim до 2000 при кожному write.

**Вплив**: Якщо tick stream зупиниться — стара preview tail залишається в Redis нескінченно. UI може показати stale preview дані.

**Рекомендація**: Розглянути TTL для preview tail (наприклад, 300с). Або додати stale detection на UI стороні.

---

### F6: Два шляхи dedup (РИЗИК: низький)

**Факт**: Dedup відбувається в двох місцях:

1. `DiskLayer._dedup_open_ms()` — `disk_layer.py:200`
2. `UDS._ensure_sorted_dedup()` — `uds.py:1769`

**Вплив**: Подвійний захист — правильно. Але `_ensure_sorted_dedup` в UDS не використовує `_choose_better_bar` з DiskLayer напряму, а має свою реалізацію.

**Рекомендація**: Переконатися що обидві реалізації мають однакову семантику "better bar" (complete > incomplete, final > non-final).

---

### F7: _disk_bar_to_candle legacy "l" fallback (РИЗІК: низький)

**Факт**: `_disk_bar_to_candle` (L48) підтримує legacy поле `"l"` як альтернативу `"low"`:

```python
low = obj.get("low", obj.get("l"))
```

**Вплив**: Backward compatibility з legacy JSONL файлами. Канон → `"low"`, але старі файли мають `"l"`.

**Рекомендація**: Зберігати fallback. При міграції — окремий initiative.

---

### F8: Preview bridge only for preview_tfs (РИЗИК: інформативно)

**Факт**: `_publish_final_to_preview_ring(bar)` (L935) публікується тільки для TF ∈ `preview_tfs` (зараз M1, M3).

**Вплив**: Final бари для M5+ не з'являються у preview ring. UI для M5+ бачить final бари тільки через `_RedisUpdatesBus`.

**Рекомендація**: Це правильна поведінка. Документувати: preview subsystem = тільки M1/M3.

---

### F9: No fsync in JsonlAppender (РИЗИК: середній)

**Факт**: `JsonlAppender.append()` робить `flush()` але не `os.fsync()` — `ssot_jsonl.py:140-145`.

**Вплив**: При crash OS може втратити останні бари з OS buffer. JSONL буде truncated.

**Рекомендація**: Для production — розглянути `os.fsync()` (з performance penalty) або WAL. Для поточного масштабу (10-50 трейдерів) — прийнятно.

---

### F10: Selftest functions in production code (РИЗИК: інформативно)

**Факт**: UDS містить вбудовані тести: `selftest_writer_api()` (L2097), `selftest_disk_policy()` (L2133) — `uds.py:2097-2294`. RedisLayer має selftest inline (L200-258).

**Вплив**: ~200 LOC тестового коду живе у production файлах. Не викликається автоматично.

**Рекомендація**: Розглянути перенесення в `tests/` або `tools/` при рефакторі.

---

### Зведена таблиця знахідок

| ID | Опис | Ризик | Інваріанти |
|----|------|-------|------------|
| F1 | UpdatesBus raw symbol vs symbol_key | Низький | — |
| F2 | SSOT logging.error on every non-final drop | Низький | I5/Правило 9.1 |
| F3 | RAM max_keys=8 для writer | Середній | — |
| F4 | Disk bootstrap 60s window | Середній | I1 |
| F5 | Preview tail без TTL | Низький | — |
| F6 | Два шляхи dedup | Низький | I2 |
| F7 | Legacy "l" fallback | Низький | — |
| F8 | Preview bridge тільки M1/M3 | Інформативно | — |
| F9 | No fsync в JSONL | Середній | — |
| F10 | Selftests у production | Інформативно | — |
