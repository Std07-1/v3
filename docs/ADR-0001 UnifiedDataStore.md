
**ADR-скелет** під *v3* для рішення “**UnifiedDataStore (RAM↔Redis↔Disk) + API contracts**”, із акцентом на **TV-like стабільність**, швидкі перемикання **symbol/TF**, і **тупий UI** (рендер без рішень).

Я спеціально заклав у скелет **інваріанти**, які прямо б’ють по твоїй проблемі “refresh нормалізує / потім знову ламається”: це майже завжди **гонки запитів**, **часткові вікна**, **неузгоджені cursors/seq**, і **випадкові деградації** (silent partial). У поточному UI видно паралельні контури оновлення: `loadBarsFull()` → `/api/bars`, `pollLatest()` → `/api/latest`, `pollLive()` → `/api/live` із різними таймерами, без cancellation/epoch-гейту, що легко дає “смикання” при перемиканні symbol/TF і при масштабуванні.

---

## ADR-0001: UnifiedDataStore (RAM/Redis/Disk) + Contract-first MarketData API

**Status:** In progress (P2.2 wired, verify pending)
**Date:** 2026-02-09
**Owners:** (TBD)
**Related:** UI stability / live jitter, redis snapshots, derived TF, exit-gates (TBD)
**Updated:** 2026-02-09

### 1) Контекст і проблема

**Симптоми (prod/live):**

* графік “смикається”, інколи стає “битий”, refresh інколи лікує, інколи навпаки;
* 4h/1d часто “стабільніші”, TF5 + похідні — більш чутливі;
* при перемиканні symbol/TF та при zoom/scrollback — нестабільне підвантаження/узгодження історії.

**Факти поточного UI-патерну (v3 UI lite):**

* cold load бере `/api/bars?…&limit=20000`;
* паралельно живуть **два polling-контури**:

  * `/api/latest … after_open_ms=…` кожні 3s;
  * `/api/live` кожні 500ms (оновлення “останнього бару”).
* зміна symbol/TF викликає `loadBarsFull()`, але **не гарантує** “зупинити/анулювати” in-flight відповіді попереднього symbol/TF (нема request epoch / cancellation token у протоколі).

**Арх-проблема:** сьогодні “кеш/політика” фактично розмазана між UI↔server↔Redis↔disk, а відповідальність за **цілісність вікна** і **узгодженість інкрементів** не зафіксована контрактом.

### 2) Цілі (Goals)

1. **TV-like стабільність рендера**: жодних “дір”/стрибків від часткових відповідей або reorder.
2. **Швидкі перемикання symbol/TF**: predictable latency, без “гонок” між old/new запитами.
3. **UI = dumb renderer**: UI не вирішує, “звідки дані”, не міксує шарів, не має prefer_redis тощо.
4. **UnifiedDataStore = системний шар**: RAM↔Redis↔Disk як *єдине місце*, де вирішується: read path, write path, backpressure, TTL, coherency.
5. **Contract-first API**: чіткі схеми запит/відповідь + версії; partial/degraded — **тільки loud**.
6. **Масштабування**: керовані ліміти історії/вікон/хвостів; контроль Redis-size; прогнозована CPU/IO.

### 2.1) Поточний стан (станом на 2026-02-10)

**Реалізовано (факти з коду):**

* UDS реалізовано у runtime/store/uds.py з шарами RAM/Redis/Disk та arbitration: `force_disk` → Disk, `cold_load+prefer_redis` → Redis tail/snap, інакше RAM (якщо є) → Disk.
* /api/bars та /api/updates вже використовують UDS у ui_chart_v3/server.py; UDS створюється в `main()` і передається в handler.
* warn-only guard контрактів активний для /api/bars і /api/updates (логування без блокування).
* **Write-path обходить UDS**: PollingConnectorB напряму використовує JsonlAppender і RedisSnapshotWriter.
* **/api/updates читає disk tail** через UDS.read_updates (hot-path з диску).

**Висновок станом на 2026-02-10:** інваріант ADR “All writes go through UDS” **ще не виконано**, а updates залежить від disk tail.

**Ціль P2X (UDS write-center):** всі записи йдуть через UDS, а /api/updates читає RAM/Redis stream, disk лишається recovery.

**Фактичний API (наразі, без schema поля):**

* /api/bars: `bars[]` (LWC-формат), `meta` з `source`, `redis_hit`, `redis_error_code?`, `boot_id`.
* /api/updates: `events[]` (key/bar/complete/source/event_ts), `cursor_seq`, `disk_last_open_ms`, `bar_close_ms`, `ssot_write_ts_ms`, `api_seen_ts_ms`, `boot_id`.

**VERIFY (реальні відповіді):**

* tf_s=300: зафіксовано `open_time_ms` нестрого-зростаючий (non-increasing) на idx=399.
  * prev=1769126100000 (2026-01-22 23:55:00Z)
  * cur=1769040000000 (2026-01-22 00:00:00Z)
  * це “відкат” майже на добу всередині одного дня.
* tf_s=86400: зафіксовані зміни offset (ймовірно DST) — як warning.

**Наслідки:**

* потрібна нормалізація/впорядкування для tf_s=300 на read-path (або корекція даних на disk).
* HTF offset має бути явно закріплений у SSOT календарі/конфігу та узгоджений з `bucket_start_ms()`.

### 2.2) Висновки з VERIFY і локалізація причини

**Ключовий факт:** /api/bars повернув non-increasing `open_time_ms` для tf_s=300. Для LWC це токсично, бо серія очікує монотонний час; такі “відкати” дають рваність/стриби/нестабільну нормалізацію після refresh.

**Найбільш імовірна причина:** append-only JSONL отримав “пізній” запис із раннім `open_time_ms` (backfill/repair/derived/перезапуск), який дописався в кінець файла. DiskLayer `_read_jsonl_tail_filtered()` повертає хвіст без сортування, тому out-of-order одразу виходить в API.

**tf_s=86400 offset/DST:** це не обов’язково дефект. Для D1 часто буває зміна якоря (FX/NY close) і DST. Це залишаємо як warning, але не FAIL у core.

**Локалізація джерела out-of-order (Disk vs Redis vs RAM):**

* Disk SSOT: `/api/bars?symbol=XAU/USD&tf_s=300&limit=2000&force_disk=1&prefer_redis=0`
* Redis: `/api/bars?symbol=XAU/USD&tf_s=300&limit=2000&prefer_redis=1`

Інтерпретація:

* якщо out-of-order є навіть при `force_disk=1` → проблема у JSONL (дублі/пізні вставки).
* якщо тільки в Redis → проблема у snapshot writer/packing.
* якщо тільки у “звичайному” режимі → проблема в RAM cache seed/override.

**Пошук дубля/пізнього append у JSONL (мінімальний скрипт):**

```
python - <<'PY'
import os, glob, json
DATA_ROOT = "./data_v3"
SYMBOL_DIR = "XAU_USD"
TF = 300
TARGET = 1769040000000

root = os.path.join(DATA_ROOT, SYMBOL_DIR, f"tf_{TF}")
for fn in sorted(glob.glob(os.path.join(root, "part-*.jsonl"))):
  with open(fn, "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
      line = line.strip()
      if not line:
        continue
      try:
        obj = json.loads(line)
      except Exception:
        continue
      if obj.get("open_time_ms") == TARGET:
        print("HIT", fn, "line", i, "src", obj.get("src"), "complete", obj.get("complete"))
PY
```

**Критерій:** 2+ входження одного `open_time_ms` = прямий доказ дубля/пізнього backfill append.

### 2.3) P2.2 hardening (must-have)

**P2.2.1 Geometry normalize на read-path (UDS):**

* додати `ensure_sorted_dedup(bars)` для виходу з Disk/Redis/RAM:
  * cheap-scan: якщо монотонність порушена → sort by `open_time_ms`.
  * dedup по `open_time_ms` із пріоритетом: `complete=True` над `complete=False`, FINAL sources над preview.
  * якщо були правки → `meta.degraded += ["geom_non_monotonic"]` і `meta.geom_fix={"sorted":true,"dedup_dropped":N}`.
  * лог `UDS_GEOM_FIX` з `source`, `tf_s`, `dropped`.

**P2.2.2 Заборонити cache override після disk:**

* джерело обирається до читання (UDS arbitration) і не підміняється після факту.

**P2.2.3 Cursor semantics (updates):**

* `cursor_seq` має бути детермінований (max seq або since_seq). Не перезаписувати cursor поза UDS.

**P2.3/P2.4 Rails (single-writer + watermark):**

* UDS у UI процесі має роль `reader` і кидає `UDS_WRITE_FORBIDDEN` при будь-якому записі.
* Write-path має **watermark per (symbol, tf_s)**; бар з `open_time_ms <= wm_open_ms` відкидається як `stale|duplicate` (loud лог).
* SSOT приймає лише final/complete бари з FINAL sources; preview/live іде лише в RAM/Redis tail.
* Інваріант: final/complete бар **незмінний** — дублікати того ж `open_time_ms` не перезаписуються.

### 3) Не-цілі (Non-goals)

* не міняємо доменний SMC compute (Stage/сценарії) — тільки рельси даних/вікон/узгодження;
* не робимо “історичний recovery” дір (це окремий ADR);
* не переписуємо UI в “важкий” клієнт (навпаки — спрощуємо).

### 4) Рішення (Decision)

#### 4.1 Ввести UnifiedDataStore як SSOT для marketdata

**UDS** — центральний модуль з чітким layering:

* **RAM (hot window)**: LRU/TTL, квоти по пам’яті/кількості hot symbols, швидкий доступ. (патерн RAM-евікшнів/TTL вже показаний у референс-імплементації)
* **Redis (warm/system cache)**:

  * мінімум: “останній бар/метадані/seq/cursor”, швидкий cross-process доступ;
  * опційно: window cache (chunked) для популярних TF (TBD).
* **Disk (durable snapshots)**:

  * write-behind (черга) з backpressure;
  * atomic replace + retry (IO стабільність).

**Write policy (стандарт):** write-through RAM→Redis + write-behind Disk.
**Read policy (стандарт):** завжди повертаємо **контрактне вікно**; якщо шар не здатен — fallback в нижчий шар, але без “тихої підміни”.
**Метрики:** hit ratios, flush backlog, latency per layer (RAM/Redis/Disk), errors per stage.

#### 4.2 “Вікно” як перший клас (Window Contract)

Вводимо поняття **Window** як канонічну відповідь `/api/bars`:

* сервер повертає **узгоджене** вікно (sorted+dedup, монотонні open_time_ms);
* відповідь має **meta**:

  * `window_start_ms`, `window_end_ms`, `count`,
  * `complete_coverage: true/false`,
  * `degraded[]` / `errors[]` (loud деградації),
  * `cursor_next` для scrollback,
  * `seq_head` (монотонний номер версії потоку по symbol/TF).

#### 4.3 Інкременти тільки через seq/cursor (Updates Contract)

Замість “UI сам думає, що вважати новим” — UDS дає стабільний **seq**:

* `/api/updates?symbol&tf_s&since_seq=` → повертає:

  * `bars_append[]` (повністю завершені нові бари),
  * `bar_last_patch` (патч/повний last bar для live),
  * `seq_head`,
  * `gap: true/false` (якщо since_seq занадто старий/втрата — тоді UI робить **реload window**, а не “латання”).
* UI **не міксує** `/api/live` і `/api/latest` без epoch-гейту; або:

  * A) лишаємо 2 endpoints, але обидва підпорядковані одному `seq_head`, або
  * B) зводимо live+latest в один `/api/updates` (рекомендовано).

#### 4.4 UI стає “тупий”: state machine + epoch gate

UI логіка міняється з “викликай що хочеш паралельно” на:

* `view_epoch++` на кожне перемикання symbol/TF;
* кожен fetch несе `epoch` (в query або header), а відповіді містять `epoch` → UI ігнорує “старі” відповіді;
* на час `load window` — poll/updates або зупиняється, або відповіді старого epoch відкидаються.

(Поточні обробники `change` викликають `loadBarsFull()`, але без вбудованого механізму відкидання in-flight відповідей → це прямий кандидат на “смикання”)

### 5) API Contracts (версійовані схеми)

**Розміщення:** `core/contracts/public/marketdata_v1/…` (TBD)
**Валідація:** server-side (debug/rail), і опційно в CI.

#### 5.1 GET /api/bars (Window)

**Request**

* `symbol: string`
* `tf_s: int`
* `limit: int`
* `to_open_ms?: int` (опційно, для scrollback)
* `epoch?: int` (опційно, для UI gate)

**Response**

* `schema: "marketdata.window.v1"`
* `symbol, tf_s`
* `bars: Bar[]`
* `meta: { seq_head, cursor_next, window_start_ms, window_end_ms, complete_coverage, degraded[], errors[] }`
* `epoch?: int`

#### 5.2 GET /api/updates (Incremental)

**Request**

* `symbol, tf_s`
* `since_seq: int`
* `epoch?: int`

**Response**

* `schema: "marketdata.updates.v1"`
* `seq_head`
* `bars_append: Bar[]`
* `bar_last: Bar | null` (або patch-формат)
* `gap: boolean`
* `degraded[], errors[]`
* `epoch?: int`

#### 5.3 GET /api/status (Health/telemetry)

* включає UDS metrics snapshot (hit ratios, backlog, bytes_in_ram…)

### 6) Data Model

**Bar (canonical)**

* `open_time_ms: int64`
* `close_time_ms: int64`
* `open, high, low, close: float`
* `volume: float`
* `complete: bool`
* `source: "stream"|"backfill"|"derived"|"disk_snapshot"` (TBD)
* `rev?: int` (опційно для patch/last bar)

(У референсі UDS бари тримають мінімальний набір OHLCV+complete і роблять merge/dedup/sort)

### 7) Інваріанти та гарантії

1. **No silent partial**: якщо вікно/інкремент неповний — тільки через `complete_coverage=false` + `degraded[]`.
2. **Monotonic time**: `open_time_ms` не зменшується в `bars_append`; last-bar patch має `open_time_ms >= last_seen`.
3. **Single-writer per (symbol,tf)** для seq (UDS/worker).
4. **Collapse-to-latest**: при backpressure (write-behind, derived rebuild) — latest wins, без розмноження черг. (патерн коалесингу flush_pending)
5. **Disk не на hot-path**: disk — durable + cold start + recovery, але не “кожен скрол” (окрім контрольованого scrollback режиму).

### 8) Варіанти (Alternatives considered)

A) **Redis-only** (disk лише для recovery)

* * швидко, просто для live
* − ризик “втрати історії” при eviction/рестарті; складніше робити стабільний scrollback; Redis memory pressure.

B) **Disk-first + cache as optimization**

* * простіше гарантувати повноту
* − latency/jitter при навантаженні, гірше UX.

C) **UDS layered (рекомендовано)**

* * контрольована продуктивність і повнота, чіткі контракти
* − складніша реалізація, треба дисципліна контрактів і метрик.

### 9) Ризики і “анти-патерни”

* **Гонки запитів UI** без epoch/seq → “смикання”, reorder, applied-to-wrong-series. (поточний UI має паралельні контури)
* **Tail-only видача як cold-load** (не контрактне вікно) → “дірки” при zoom-out/scrollback.
* **Derived TF on read-path без бюджету** → спайки CPU/IO; потрібен throttle/worker policy. (у референсі є throttle refresh derived TF)
* **Redis size runaway** без TTL/retention/limit. (у референсі є tail limit + TTL per interval)

### 10) План впровадження (P-slices)

**P0 (Discovery / Audit)**

* зафіксувати current endpoints + payload sizes + jitter сценарії;
* додати діаг-лог: epoch, seq_head, count, window_start/end.

**P1 (Contracts-first)**

* додати JSON-схеми `marketdata.window.v1`, `marketdata.updates.v1`, `bar.v1`;
* мінімальні rails: “server відповідає тільки за схемою або loud error”.

**P2 (UDS core)**

* RAM LRU/TTL/quotas, Redis meta+lastbar+seq, Disk snapshots write-behind.

**P3 (Server інтеграція)**

* `/api/bars` → UDS.get_window()
* `/api/updates` → UDS.get_updates()
* заборонити “cache override” та silent partial.

**P4 (UI спрощення)**

* ввести epoch-gate (ігнор старих відповідей)
* звести polling до `/api/updates` (або синхронізувати live/latest через seq)

**P5 (Exit gates + Observability)**

* метрики: latency p95, hit ratios, degraded counts, seq gaps
* тест-кейси: switch storm (symbol/tf), zoom-out, reconnect, redis down (loud degrade).

### 10.1) Статус виконання (факт)

* P0: виконано (діаг-логи у /api/bars і /api/updates, epoch у запитах зафіксовано у журналі).
* P1: виконано (JSON Schema `marketdata_v1` + warn-only guard у server.py).
* P2: виконано (UDS + RAM/Redis/Disk шари у runtime/store).
* P3: виконано (інтеграція /api/bars і /api/updates через UDS у ui_chart_v3/server.py).
* P4: частково (epoch-гейт у UI зафіксовано в журналі; потребує повторної перевірки після P2.2).
* P5: не виконано (exit-gates і Observability без runner/manifest для UDS).
* **P2X (Write-center)**: не виконано (writer/connector обходить UDS; updates читає disk tail).

### 10.3) Апдейт 2026-02-10 (P2X: UDS як write-center)

**Інваріанти:**

1. **All writes go through UDS**: жодних прямих JsonlAppender/RedisSnapshotWriter у writer/connector.
2. **UI reads only from UDS**: /api/bars і /api/updates не читають disk/redis напряму поза UDS.
3. **Disk is recovery**: disk читається тільки для cold-start, scrollback за межами hot window, recovery/backfill.
4. **Updates hot-path = RAM/Redis**: /api/updates не сканує disk tail.

**Гейти (exit criteria):**

* Gate-1: у runtime/ingest немає імпортів JsonlAppender/RedisSnapshotWriter (тільки UDS API).
* Gate-2: UDS read_updates не має read_window_with_geom(use_tail=True).
* Gate-3: /api/updates повертає cursor_seq з UDS stream без disk_last_open_ms як джерела істини.

**Rollback:**

* Повернути writer/connector на JsonlAppender + RedisSnapshotWriter без UDS.
* Відновити UDS.read_updates через disk tail (current behavior) як деградований режим.

### 10.4) Діагностика (поточний стан vs ADR)

**Факти:**

* Writer/connector пише напряму в JSONL і Redis snapshots, оминаючи UDS.
* /api/updates читає disk tail (UDS read_updates використовує disk tail), тобто disk зараз hot-path.
* UDS використовується як read-only у UI, але не є центром запису.

**Висновок:** ADR-інваріанти “All writes go through UDS” і “Disk is recovery” порушені. Це головна причина split-brain і затримок між stream/SSOT/updates.

### 10.5) Мінімальний план P2X (без UDS-daemon)

**Принцип:** UDS залишається бібліотекою/фасадом, Redis = міжпроцесна спільна пам’ять, disk = SSOT + recovery. Один і той самий UDS код працює у writer (role=writer) і в UI (role=reader).

**Крок A: Writer goes through UDS (критичний мінімум)**

* Перенести JsonlAppender, RedisSnapshotWriter і watermark в UDS.
* У PollingConnectorB замінити прямі записи на:
  * `uds.commit_final_bar(...)` (complete=true → SSOT + Redis)
  * `uds.upsert_preview_bar(...)` (complete=false → лише Redis)

**Крок B: Updates тільки через Redis (hot-path)**

* Додати Redis updates stream (XADD/XREAD або простий журнал з INCR seq).
* UDS публікує update event на кожен upsert/commit.
* /api/updates читає лише Redis updates; disk використовується тільки при redis_down (degraded) або для recovery.

**Крок C: Disk = recovery/scrollback/warmup**

* /api/bars: hot window з Redis snapshots/tail, scrollback — disk range.
* Warmup/prime: заповнювати Redis з disk під бюджет; не використовувати disk у hot updates.

**Preview vs Final (NoMix):**

* preview ніколи не пишеться у SSOT як final;
* final перемагає preview за ключем (symbol, tf, open_ms);
* derived TF будуються тільки з final/complete бази.

### 10.2) Slice mapping (узгодження нумерації)

Щоб уникнути плутанини під час рев’ю:

* **P2.1** = `20260209-024` (UDS scaffolding: RAM/Redis/Disk шари).
* **P3.0** = `20260209-025` (thin-API інтеграція через UDS; історично позначено як P2.2).

### 11) Тести / Exit Gates (Definition of Done)

* Unit: dedup/sort invariants, seq monotonic, gap detection.
* Integration: “switch storm 100x” без jitter/reorder; “scrollback до N днів” без partial.
* Runtime rails:

  * “відповідь /api/bars не може бути tail-only якщо limit>X без degraded[]”
  * “updates з gap=true → UI мусить reload window”.

### 12) Rollback / Feature flags

* `config/config.py` (SSOT): `UDS_ENABLED`, `UDS_UPDATES_ENABLED`, `UDS_REDIS_REQUIRED` (TBD)
* rollback: повернення до старого /api/latest|/api/live, але **degraded-but-loud**.

### 13) Відкриті питання (TBD)відповідь на яких нижче

* Який “цільовий window” по TF (bars/day, max_days_by_tf)?
* Чи робимо Redis window-chunks, чи тільки meta+lastbar+seq?
* Як узгоджуємо derived OHLCV TF5: worker на close чи best-effort throttle?

---
Нижче — конкретні **рекомендовані рішення** для v3 по трьох питаннях. Я даю значення так, щоб:

* UI завжди отримував **контрактне “вікно”** (contiguous window), без “tail-only сюрпризів”
* перемикання symbol/TF було **швидким** завдяки RAM/Redis, а не через UI-хитрощі
* derived TF збирався **детерміновано** (без “best-effort магії” на read-path), але без зайвого навантаження

---

## 1) “Цільовий window” по TF (bars/day, max_days_by_tf)

### Принцип

* **Кап по барах** важливіший за “роки”: UI/мережа/JS відчувають саме кількість барів.
* Робимо `default_window_days` і `max_window_days`, але **обидва повинні вкладатися в max_bars_per_request**.
* Для TV-like UX: **default window** має покривати нормальний zoom-out без довантажень; довантаження — тільки через `cursor` (scrollback), а не через запит “дай 200k барів”.

### Рекомендована таблиця для v3 (UI window)

> Ці значення зручні тим, що для коротких TF ми тримаємо під контролем кількість барів (і час відповіді), а для довших TF дозволяємо великі “days”, бо барів мало.

| TF  | bars/day | default_window_days | default bars | max_window_days | max bars |
| --- | -------: | ------------------: | -----------: | --------------: | -------: |
| 1m  |     1440 |                   7 |       10,080 |              21 |   30,240 |
| 5m  |      288 |                  60 |       17,280 |             180 |   51,840 |
| 15m |       96 |                 180 |       17,280 |             365 |   35,040 |
| 30m |       48 |                 365 |       17,520 |             730 |   35,040 |
| 1h  |       24 |                 730 |       17,520 |            1460 |   35,040 |
| 4h  |        6 |                1460 |        8,760 |            3650 |   21,900 |
| 1d  |        1 |                3650 |        3,650 |            9125 |    9,125 |

1) Cold‑start (bootstrap) — рекомендовані ліміти
Це саме “скільки потрібно, щоб стартувати стабільно” для всіх активів.

5m (основа для derived):

7 днів (≈ 2 016 барів) — стартова нормa.
Якщо дозволяє CPU/IO, можна 14 днів (≈ 4 032), але тільки якщо без gap‑обрізання.
Derived 15m/30m/1h (з 5m):

дорівнює тривалості 5m, бо береться з нього.
Тобто якщо 5m = 7d → derived теж максимум 7d на старті.
4h (з брокера):

180 днів (≈ 1 080 барів).
Дає достатню “глибину” без надмірного трафіку.
1d (з брокера):

365 днів (≈ 365 барів).
Достатньо для макро‑контексту без важкого history.
Це холодний старт: швидко, без рваності, без перегріву.

1) Таблиця з ADR — як ліміт накопичення, а не cold‑start
Твоя таблиця нормальна як довгострокова межа (retention/макс‑вікно).
Але для cold‑start її застосовувати не можна — інакше ти знову потрапиш у gaps.

Тобто:

Cold‑start = короткі вікна (вище).
Retention/max = як у таблиці (це “стеля”, не старт).
3) UI ліміти (що бачимо на старті)
Пропоную такі default‑вікна UI саме для cold‑start:

TF Cold‑start window
5m 7d (≈ 2 016)
15m 7d (≈ 672)
30m 7d (≈ 336)
1h 7d (≈ 168)
4h 180d (≈ 1 080)
1d 365d (≈ 365)
А max‑window UI залишаємо за таблицею ADR (це вже “накопичення/глибина”).

**Ключові наслідки:**

* Для **5m**: max 180 днів ≈ 51.8k барів — це верхня межа, де ще реально тримати стабільно і на сервері, і в UI.
* Для derived (15m/30m/1h): ти можеш давати “рік+” без проблем, бо барів значно менше.
* `max_bars_per_request` можна поставити **60,000** глобально (і перевіряти rail’ом).

## 2) Redis: window-chunks чи тільки meta+lastbar+seq?

### Рішення: **двоступенево (але з чіткою ціллю)**

#### Phase A (P0/P1) — **тільки meta + lastbar + seq (+ опційно невеликий tail)**

Це обов’язково, навіть якщо потім підемо в chunks.

**Зберігаємо в Redis:**

* `seq_head` по (symbol, tf)
* `watermark_ms` (останній гарантовано finalized bar)
* `last_bar_final` (останній complete бар)
* `last_bar_live` (поточний incomplete, якщо потрібен)
* **опційно** `tail_small` (наприклад 500–2000 барів) — лише щоб UI не показував “порожньо” на cold start

**Чому так:**

* ти одразу вирізаєш головний клас багів: *UI не має гадати*, чи можна “дошивати” і що вважати новим.
* маленькі значення в Redis → менше шансів на latency spikes/переповнення/великий трафік.

#### Phase B (P2+) — **Redis window-chunks для hot TF (мінімум: 5m і 15m)**

Якщо хочеш **максимально швидкі перемикання TF/символів** (як ТВ), тоді chunks дадуть приріст: UI window можна віддати **без диску**.

**Рекомендація по chunks:**

* **chunk granularity = “1 день”** (для FX це природно):

  * 5m: до 288 барів на день
  * 15m: до 96 барів на день
* ключі:

  * `md:{symbol}:{tf}:chunk:{YYYYMMDD}` → bytes (msgpack/zstd)
  * `md:{symbol}:{tf}:index` → відсортований список днів/діапазонів
  * `md:{symbol}:{tf}:meta` → seq_head, watermark, last_bar_final/live, coverage flags
* TTL/retention для chunk-ключів **окремо від meta** (meta тримаємо довше)

**Чому chunk-by-day краще за “один великий blob”:**

* не переписуєш 20–50k барів при кожному апдейті (це дуже дорога операція)
* eviction/TTL працює по днях
* scrollback робиться природно (додаємо день за днем)

**Мій висновок:**

* якщо головна мета зараз — **стабільність**: починай з **Phase A**.
* якщо додатково потрібна **TV-like швидкість перемикань без диску**: додавай **Phase B** тільки для 5m/15m і тільки після того, як контракти/seq стабільні.

---

## 3) Узгодження derived OHLCV від TF5: worker “on close” чи best-effort throttle?

### Рішення: **event-driven “on close”, але з boundary gating + coalescing**

Best-effort throttle на read-path (як у старому `UnifiedDataStore` з `DERIVED_TF_REFRESH_THROTTLE_MS`) — це зручно, але воно **плодить недетермінізм**: UI запросив — derived “підтягнулось”; не запросив — не підтягнулось; інколи “підтягнулось частково”.

Для v3 (твоя ціль “як ТВ”) краще:

#### 3.1. Канонічний pipeline

* Базовий finalized TF (для цього питання) = **5m complete bars**.
* Derived OHLCV будується **інкрементально**:

  * 15m з 3×5m
  * 30m з 6×5m
  * 1h з 12×5m
  * 4h з 48×5m
  * 1d — через session/day anchor (окремо, але теж event-driven)

#### 3.2. Boundary gating (важливо)

На кожному новому 5m close:

* перевіряємо, чи цей close **закриває bucket** для target TF.

  * якщо **не закриває** → derived finalized **не оновлюємо**
  * якщо **закриває** → рахуємо рівно **1 новий derived bar** і пушимо його в UDS (RAM/Redis/Disk), піднімаємо `seq_head` для target TF

Це дає:

* **детермінізм** (derived з’являється рівно тоді, коли має)
* **низьку CPU** (не “перерахунок хвоста”, а одна свічка)

#### 3.3. Coalescing/backpressure (best-effort тільки тут)

Best-effort потрібен не для derived-логіки, а для **захисту**:

* якщо прийшов burst (backfill, reconnection) — робимо `collapse-to-latest-final`:

  * один inflight per (symbol, target_tf)
  * якщо під час обчислення прийшли ще 10 M5 барів — після завершення добудовуємо до останнього закритого boundary, але **не запускаємо 10 паралельних задач**

#### 3.4. Live (incomplete) derived bar — опційно

Якщо тобі треба “як ТВ” і для 15m/1h показувати “рух” всередині свічки:

* можна публікувати `last_bar_live (complete=false)` для derived TF, але:

  * він не впливає на `seq_head` finalized потоку
  * UI чітко розрізняє `final append` vs `live patch`

### Мій вибір

* **on close** для finalized derived — must-have.
* throttle/coalescing — тільки як механізм backpressure, не як “логіка правди”.
* best-effort refresh “по запиту UI” — не рекомендую для v3, бо це саме те, що дає “refresh лікує/ламає”.

---

## Підсумок рішень (коротко)

1. Window:

* 5m: default 60d (~17k), max 180d (~52k)
* derived: дозволяємо 1–4 роки в днях, але caps по барах ~35k

1. Redis:

* почати з **meta+lastbar+seq** (опційно маленький tail)
* chunks додати **після стабілізації контрактів**, і лише для **5m/15m** (day-chunks)

1. Derived:

* finalized derived = **worker on close** + boundary gating + coalescing
* live patches — окремо, не змішувати з finalized seq

---

Нижче — **повний P2X-план** (максимально приземлений, з конкретними артефактами/рейками), і **перші 1–2 кроки** як P-slices для мінімального дифу.

---

## Цільова модель (канон, без «магії»)

### Інваріанти (те, що будемо “enforce”, не просто декларувати)

## I1. All writes go through UDS

* Конектор/SMC/будь-який writer **не має**:

  * відкривати JSONL сам
  * будувати Redis ключі сам
  * писати snapshots/tail сам
* Вони **лише** викликають UDS-API (умовні назви):

  * `uds.commit_final_bar(...)` (final/complete, immutable)
  * `uds.publish_preview_bar(...)` (preview/tick/1m, TTL, не змішується з final)
  * `uds.publish_update_event(...)` (інкременти для UI)

## I2. UI reads only from UDS

* UI server викликає тільки:

  * `uds.read_window(...)`
  * `uds.read_updates(...)`
* Жодних “виборів шару” з UI (prefer_redis/force_disk) у робочому режимі. Максимум — **debug-параметр**, який видно в логах і додає `warnings[]`.

## I3. Disk is recovery

* Диск читаємо тільки для:

  * cold-start/warmup (прайм Redis хвоста)
  * scrollback глибше за cache window
  * recovery/backfill tools
* `/api/updates` **не читає disk tail** у штатному режимі. Якщо Redis down — **degraded-but-loud** (warnings/degraded), а не “тихий” disk-fallback.

## I4. NoMix preview/final

* preview **ніколи** не мутує SSOT (JSONL) і не підміняє final-бар.
* final/complete **immutable**: після `commit_final_bar` — ніяких “перезаписів у минуле”.

---

## Дані/потоки (як має виглядати після P2X)

**Write path (конектор):**

```
PollingConnectorB → UDS(role=writer)
  ├─ commit_final_bar() → (1) Redis tail/snap  (2) UpdatesBus (Redis)  (3) Disk (JSONL)
  └─ publish_preview_bar() → Redis preview (TTL) + UpdatesBus (опц.)
```

**Read path (UI):**

```
/api/bars    → uds.read_window()   (Redis hot window; disk тільки якщо треба)
/api/updates → uds.read_updates()  (Redis UpdatesBus; disk НЕ hot-path)
```

---

## P2X план (детально по слайсах)

### P2X.0 — “Freeze facts” (вже майже зроблено)

**Мета:** зафіксувати, що зараз write обходить UDS і updates читає disk tail.
**Артефакт:** запис у `changelog.jsonl` + короткий блок у ADR.

> Це у тебе вже є як discovery.

---

### P2X.1 — UDS Writer API (канонічні методи + внутрішні writer-компоненти)

**Що робимо:**

1. Додаємо/фіксуємо у `runtime/store/uds.py` (або поруч) **чіткі writer-методи**:

* `commit_final_bar(bar: Bar, *, symbol, tf_s, source, ssot_write_ts_ms, ...) -> CommitResult`
* `publish_preview_bar(bar: Bar, *, symbol, tf_s, ttl_s, ...) -> None`
* (всередині) `_append_to_disk(...)`, `_write_redis_snapshot(...)`, `_publish_update(...)`

1. UDS як writer **інкапсулює**:

* `JsonlAppender` (SSOT JSONL)
* `RedisSnapshotWriter` (snap/tail)
* `UpdatesBus` (новий компонент, див. P2X.2)

1. **Watermark** переносимо в UDS writer:

* watermark key = `(symbol, tf_s)`
* ініціалізація з диска (tail) при старті writer
* drop stale/duplicate **до** запису (loud лог + лічильник)

**Рейка (runtime rail):**

* `UDS(role=reader)` кидає `UDS_WRITE_FORBIDDEN` на будь-який write-метод (у тебе це вже було — це правильний напрямок).

**Тести (без Redis/диска як сервісів):**

* unit-тести на watermark-drop функцію (stale/duplicate/ok)
* unit-тести на “final immutable”: при `open_ms <= watermark` → drop з reason
* unit-тести на “NoMix”: preview не викликає disk write

**Verify (локально):**

* `py_compile` модуля UDS
* короткий “selftest” скрипт, який викликає writer-API на фейкових шарах/стабах

**Rollback:**

* UDS writer-API залишається, але конектор ще не переведено — це безпечний крок.

---

### P2X.2 — UpdatesBus у Redis (гарячий інкрементальний канал)

**Ціль:** `/api/updates` більше **не** читає disk tail.

**Мінімальний дизайн (простий і детермінований):**

* Redis key: `"{NS}:updates:{symbol}:{tf_s}:seq"` (INCR → int seq)
* Redis key: `"{NS}:updates:{symbol}:{tf_s}:list"` (RPUSH JSON, LTRIM до N)
* N (retain) — SSOT у `config.json`

**UDS.publish_update(...) робить:**

* `seq = INCR`
* `RPUSH(list, json(event))`
* `LTRIM(list, -N, -1)`
* (опц.) `SET last_seq`

**UDS.read_updates(since_seq, limit) робить:**

* зчитує хвіст list (LRANGE останні N)
* парсить, фільтрує `seq > since_seq`, обрізає до limit
* `cursor_seq = max(seq in returned) або since_seq/last_seq`
* якщо `since_seq` “занадто старий” (випав з retain) → `warnings=["cursor_gap"]` і `meta.extensions.gap={first_seq_available,...}`

**Degraded-but-loud:**

* якщо Redis недоступний → `warnings=["redis_down"]`, `meta.extensions.degraded=["redis_down"]`, `events=[]`
* і **без** автоматичного disk-fallback у штатному режимі

**Тести:**

* чистий unit: подати список подій (як ніби LRANGE) → перевірити cursor_seq/фільтр/limit/gap
* окремо: курсор-саніті (cursor_seq == max(seq) або since_seq)

---

### P2X.3 — Перевести конектор на UDS writer (закрити I1 фактично)

**Що міняємо в конекторі:**

* прибираємо пряме створення `JsonlAppender` і `RedisSnapshotWriter`
* замінюємо `_append_bar` на `uds.commit_final_bar(...)` (і `uds.publish_preview_bar` коли дійдемо)

**Exit-gates (обов’язково):**

* gate: заборона імпорту/використання `JsonlAppender` і `RedisSnapshotWriter` у `runtime/ingest/*`
* gate: single-writer (UI не імпортує writer-модулі)
* gate: `UDS.read_updates` не може містити disk tail читання

**Verify:**

* smoke: конектор пише 1 бар → в Redis з’являється updates-event → UI бачить його через /api/updates

---

### P2X.4 — Warmup/Prime Redis hot window (щоб UI не “гриз” диск на старті)

* на старті writer-процесу: для allowlist TF праймимо Redis tail з диска (останні N final барів)
* UI cold-start: `read_window` майже завжди з Redis

---

### P2X.5 — Backpressure + write-behind (опційно, але правильно)

* на першому етапі **не ускладнюй**: final commit → синхронно в диск (щоб не втратити дані)
* коли стабільно: write-behind queue + flush thread + ліміти черги + лічильники dropped/queue_len

---

### P2X.6 — Preview (tick/1m) + повернення 1m/3m у UI

* preview йде окремим Redis-ключем (TTL), окремим event-типом
* final 5m/15m/… як і раніше immutable
* UI малює preview як overlay, але не змішує в історію final

---

# Перші 1–2 кроки (конкретно, що робити зараз)

## Крок 1 (P2X.1) — “UDS Writer API + інкапсуляція writer-компонентів”

**Scope (мінімальний):**

* `runtime/store/uds.py` (додати/зафіксувати writer-API)
* `runtime/store/writer_components/*` або прямо в UDS (якщо хочеш мінімум файлів)
* **жодних** змін у UI і конекторі поки

**Deliverables:**

* `commit_final_bar()`:

  * приймає бар, робить watermark check
  * викликає (поки що) існуючі низькорівневі writer-штуки (JsonlAppender/RedisSnapshotWriter) **але вони створюються всередині UDS**
* `UDS(role=reader)` відмовляє на write (rail)
* unit-тести на watermark/drop та “final immutable”
* короткий selftest-скрипт

**Exit gate для кроку 1 (простий):**

* заборонити створення JsonlAppender/RedisSnapshotWriter у `engine_b.py` (поки warn-only або allowlist), щоб наступний крок був “легальним”.

---

## Крок 2 (P2X.2) — “UpdatesBus у Redis + UDS.read_updates без disk tail”

**Scope:**

* `runtime/store/uds.py`: реалізувати `publish_update()` і `read_updates()` через Redis list+seq
* `ui_chart_v3/server.py`: `/api/updates` викликає тільки `uds.read_updates()` (без disk tail)
* `config.json`: додати параметри updates retain/limits (SSOT)
* tests: unit на cursor/gap/limit

**Критерій успіху (перевіряється швидко):**

* `/api/updates` працює з Redis навіть при “порожньому диску”
* при вимкненому Redis → повертає `warnings=["redis_down"]` і **не** лізе в disk tail (це важливо)

---

## Коментар щодо твоєї “UDS-daemon” і RAM

Твоє уявлення логічне, але **не стрибай у daemon зараз**.

На першому етапі:

* “спільність між процесами” = **Redis + disk** (це вже достатньо)
* RAM-кеші залишаються per-process
* UDS-daemon має сенс пізніше, коли захочеш:

  * міжпроцесний backpressure/черги
  * один централізований writer-арбітр
  * мінімізувати disk I/O на UI-вузлі

Зараз найважливіше — **замкнути write/read інваріанти** і прибрати disk з hot-path updates.

---

## Важливе уточнення по “затримкам”

Навіть ідеальний UDS **не прибере** лаг “close→видно в UI”, якщо:

* 5m бар формується через history polling (а не live stream)

UDS прибирає інше:

* split-brain
* рваність від partial windows / out-of-order
* залежність UI updates від диска

А питання “4–5 хв lag” — це окремо: або live-stream, або інша політика polling/close-детекції.

---
