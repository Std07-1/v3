# Повний посібник: отримання свічок (Candle Acquisition)

> **Останнє оновлення**: 2026-02-19 (ADR-0002 cleanup complete)  
> **Навігація**: [docs/index.md](index.md)  
> **Попередній гайд (спрощений)**: [docs/guide_candles.md](guide_candles.md)

Цей документ — вичерпний опис **як система отримує, обробляє і доставляє OHLCV свічки** від брокера до UI. Враховує всі нюанси: FXCM API, календар, flat-бари, watermark, tail catchup, live recover, M3 деривацію, preview-plane.

---

## Зміст

1. [Архітектура отримання даних (A→C→B)](#1-архітектура-отримання-даних-acb)
2. [FXCM History API — як працює fetch](#2-fxcm-history-api--як-працює-fetch)
3. [M1 Poller — повний цикл](#3-m1-poller--повний-цикл)
4. [M5+ Pipeline (engine_b)](#4-m5-pipeline-engine_b)
5. [Календар і daily breaks](#5-календар-і-daily-breaks)
6. [Flat-бари і calendar-pause фільтрація](#6-flat-бари-і-calendar-pause-фільтрація)
7. [Watermark і dedup](#7-watermark-і-dedup)
8. [Tail catchup (bootstrap)](#8-tail-catchup-bootstrap)
9. [Live recover (gap auto-fill)](#9-live-recover-gap-auto-fill)
10. [Stale detection](#10-stale-detection)
11. [M3 деривація з M1](#11-m3-деривація-з-m1)
12. [Derived TF (M15/M30/H1 з M5)](#12-derived-tf-m15m30h1-з-m5)
13. [H4/D1 broker fetch](#13-h4d1-broker-fetch)
14. [Preview-plane (tick stream)](#14-preview-plane-tick-stream)
15. [Від UDS до UI: /api/bars і /api/updates](#15-від-uds-до-ui-apibars-і-apiupdates)
16. [PREVIOUS_CLOSE stitching](#16-previous_close-stitching)
17. [Типові пастки і edge cases](#17-типові-пастки-і-edge-cases)
18. [Конфігурація (config.json SSOT)](#18-конфігурація-configjson-ssot)
19. [Діагностика і перевірка](#19-діагностика-і-перевірка)

---

## 1. Архітектура отримання даних (A→C→B)

> **ADR-0002 завершено** — M5 polling вимкнено, derive chain M1→H4 через DeriveEngine.

```text
A: Broker (FXCM)                    C: UDS (вузька талія)           B: UI (read-only)
┌──────────────────┐               ┌──────────────┐                 ┌──────────────┐
│ History API      │──m1_poller───→│ commit_final │──disk/redis──→  │ /api/bars    │
│ (M1)             │  +DeriveEngine│ _bar()       │──updates bus─→  │ /api/updates │
│                  │               │              │                 │              │
│ History API      │──engine_b────→│ commit_final │──disk/redis──→  │              │
│ (D1)             │  (D1-only)    │ _bar()       │                 │              │
│                  │               │              │                 │              │
│ Tick Stream      │──tick_pub───→ │ publish_     │──preview────→   │ /api/overlay │
│ (ForexConnect WS)│──tick_prev──→ │ preview_bar()│  ring           │              │
└──────────────────┘               └──────────────┘                 └──────────────┘
```

**Дві SSOT-площини (після ADR-0002):**

| Площина | TF | Джерело | Процес |
| --- | --- | --- | --- |
| SSOT-1 | M1 → M3, M5, M15, M30, H1, H4, D1 | FXCM M1 History + DeriveEngine | `m1_poller` + `derive_engine` |
| SSOT-2 | D1 (legacy) | FXCM D1 History | `engine_b` (D1 fetch OFF, ADR-0023: broker_base_tfs_s=[]) |

**Preview-площина** (tick stream): M1, M3 — через `tick_publisher` + `tick_preview_worker`.

---

## 2. FXCM History API — як працює fetch

### 2.1 Виклик API

```python
# runtime/ingest/broker/fxcm/provider.py
arr = self._fx.get_history(symbol, timeframe, date_from, date_to, count)
```

Параметри:

- `symbol`: FXCM назва інструменту (напр. `"XAU/USD"`, `"EUR/USD"`)
- `timeframe`: `"m1"`, `"m5"`, `"H4"`, `"D1"` тощо
- `date_from`: `None` (не обмежуємо початок)
- `date_to`: UTC datetime або `None` (до "зараз")
- `count`: кількість барів (N останніх перед `date_to`)

### 2.2 Price Mode: FIRST_TICK vs PREVIOUS_CLOSE

**FIRST_TICK (наш default):**

- Open кожного бару = перший реальний BID тик в цьому періоді
- Між barами можуть бути "гепи" (open ≠ prev_close) — **це нормально**
- Дані чисті, без артефактів

**PREVIOUS_CLOSE (НЕ використовуємо):**

- Open = Close попереднього бару (штучне stitching від брокера)
- Створює артефакти: чергування volume (high/low), рвані переходи через breaks
- Ми робимо stitching самі в UI (`open[i] = close[i-1]`), а не через брокера

### 2.3 Нормалізація відповіді

`normalize_history_to_bars()` ([provider.py](../runtime/ingest/broker/fxcm/provider.py)):

1. Парсить numpy structured array від ForexConnect
2. Витягує час: `Date` → `open_time_ms` (UTC epoch ms)
3. Обчислює: `close_time_ms = open_time_ms + tf_s * 1000` (end-excl канон)
4. Витягує OHLCV: пріоритет `BidOpen/BidHigh/BidLow/BidClose` → `Open/High/Low/Close`
5. Volume: `Volume` поле (tick count)
6. Створює `CandleBar(complete=True, src="history")`
7. Перевіряє `assert_invariants()` (alignment, geometry)

### 2.4 Особливості FXCM

- **Порядок барів**: FXCM може повернути бари у **зворотному порядку** — завжди сортуємо
- **Sparse дані**: деякі інструменти (HKG33, NGAS) мають мало барів/хвилину — можуть бути "мікро-гепи"
- **Затримка**: M1 бар стає доступним через ~5-8с після закриття хвилини; safety_delay компенсує
- **Максимум барів за запит**: ~300-500 для M1 (залежить від глибини)

---

## 3. M1 Poller — повний цикл

**Файл**: [runtime/ingest/polling/m1_poller.py](../runtime/ingest/polling/m1_poller.py) (1096 рядків)

### 3.1 Startup (bootstrap)

```text
M1PollerRunner._bootstrap_warmup():
  1) Redis priming: читає останні N барів M1/M3 з диску → пише в Redis snap
  2) Watermark warmup: завантажує 10 M1 з диску для встановлення watermark
  3) Tail catchup: від watermark до expected (FXCM fetch, макс 5000 барів)
     → Інваріант: main loop не починається поки tail gap не закрито
```

### 3.2 Main loop — один цикл (кожні ~8 секунд після початку хвилини)

```text
M1SymbolPoller.poll_once():
  1) Calendar state logging (БЕЗ блокування poll)
     → Інваріант: НЕ має calendar gate! Поллить завжди, щоб
       отримати ОСТАННІЙ бар перед market break

  2) Calendar-aware expected: _expected_closed_m1_calendar()
     → Якщо ринок відкритий: остання закрита хвилина (floor)
     → Якщо ринок закритий: остання ТОРГОВА хвилина (сканує назад до 7 днів)
     → Якщо expected ≤ 0: skip (довгий weekend)

  3) Caught-up check: watermark >= expected → skip (ефективність)

  4) Adaptive fetch count:
     → gap=0: не фетчимо (caught up)
     → gap=1-2: fetch 2 (дефолт)
     → gap≥3: fetch gap+1 (наздоганяємо), max MAX_FETCH_N=120

  5) FXCM fetch_last_n_m1(symbol, n=fetch_n, date_to=expected+1M1)
     → date_to обмежує fetch — не дає FXCM повертати бари "з майбутнього"

  6) Фільтрація:
     → bars = [b for b if b.open_time_ms <= expected]     # не новіші за cutoff
     → bars = [b for b if b.open_time_ms > watermark]     # watermark pre-filter
     → bars.sort(key=lambda b: b.open_time_ms)            # asc порядок

  7) Ingest кожен бар: _ingest_bar(bar)
     → Calendar-aware flat bar classification (див. §6)
     → UDS.commit_final_bar(bar) → disk + Redis + updates bus
     → Оновлення watermark
     → DeriveEngine.on_bar(bar) → каскадна деривація M3→H4

  8) Live recover check: _live_recover_check() (див. §9)
  9) Stale detection: _stale_check() (див. §10)
```

### 3.3 Чому НЕ має calendar gate

**Проблема (баг знайдений 2026-02-18):** раніше `poll_once()` мав `if not market_open: return` на початку. Це означало, що коли ринок закривався (напр. XAU/USD о 22:00 UTC), **останній бар 21:59 ніколи не фетчився**, бо вже в наступну секунду ринок вже "closed".

**Рішення:** видалили calendar gate. Замість цього:

- Calendar-aware `expected`: знає "остання торгова хвилина" навіть коли ринок закритий
- Caught-up check: якщо watermark вже = expected → skip (не спамить зайвими fetch)
- engine_b (M5) ніколи не мав calendar gate — це була reference модель

### 3.4 Reconnect

Якщо всі символи в циклі мають помилку → reconnect FXCM сесії (cooldown 120s).

---

## 4. D1 Pipeline (engine_b) — тільки D1

**Файл**: [runtime/ingest/polling/engine_b.py](engine_b.py) (~996 рядків)

> **Після ADR-0002**: engine_b **більше не поллить M5** і не деривує M15/M30/H1.
> M5→H4 derive chain повністю перейшов до `m1_poller` + `DeriveEngine`.
> **Після ADR-0023**: engine_b D1 fetch вимкнено (`broker_base_tfs_s: []`).
> D1 тепер derived TF (1440×M1, anchor 79200) через DeriveEngine.

Основний цикл:

- Bootstrap: `_prime_redis_from_disk()` для D1 + `_bootstrap_from_disk()` + `_cold_start_base_from_broker()`
- Poll: `_fetch_base_from_broker_on_close()` — фетчить D1 бар при закритті bucket
- Calendar-aware: anchor offset для D1 (day_anchor_offset_s_d1 / _d1_alt)
- Запис тільки через UDS: `commit_final_bar()`

---

## 5. Календар і daily breaks

**Файл**: [runtime/ingest/market_calendar.py](../runtime/ingest/market_calendar.py)

### 5.1 Модель

Кожен символ належить до **calendar group** (з `config.json → market_calendar_symbol_groups`).
Кожна група має:

- **Weekend**: закриття/відкриття (dow + HH:MM UTC)
- **Daily breaks**: один або декілька інтервалів [start, end) UTC

### 5.2 Calendar groups (config.json)

| Група | Символи | Daily break(s) |
| --- | --- | --- |
| `fx_24x5_utc_winter` | GBP/CAD, NZD/CAD, USD/CAD, USD/JPY | 21:55-22:30 UTC |
| `cfd_us_22_23` | XAU/USD, XAG/USD, NAS100, US30, SPX500, NGAS | 22:00-23:00 UTC |
| `cfd_eu_21_07` | GER30, EUSTX50 | 21:00-07:00 UTC |
| `cfd_hk_main` | HKG33 | 19:00-01:15, 04:00-05:00, 08:30-09:15 UTC |

### 5.3 Визначення "торговельна хвилина"

`MarketCalendar.is_trading_minute(now_ms)`:

1. Перевіряє всі daily break інтервали: якщо `cur_min ∈ [start, end)` → **closed**
2. Підтримує wrap через **північ** (start > end, напр. 19:00→01:15)
3. Перевіряє weekend: якщо `cur_week_min ∈ [close, open)` → **closed**
4. Інакше → **trading**

### 5.4 Calendar-aware expected

`_expected_closed_m1_calendar(calendar, now_ms)`:

- Якщо остання хвилина — торгова → стандартний floor (як без календаря)
- Якщо остання хвилина — НЕ торгова → сканує назад (до 7 днів) до першої торгової
- Це гарантує що при break/weekend expected вказує на **останній реальний бар**

---

## 6. Flat-бари і calendar-pause фільтрація

### 6.1 Що таке flat bar

Flat bar: `O == H == L == C` і `volume ≤ flat_bar_max_volume` (SSOT: `config.json → flat_bar_max_volume`, default 4).

Це "шум" від брокера — він генерує бари навіть під час пауз (зазвичай з однаковими OHLC і мінімальним volume).

### 6.2 Класифікація (calendar-aware)

`_ingest_bar()` класифікує кожен бар:

| Стан ринку | Flat? | Дія |
| --- | --- | --- |
| Trading | No | ✅ Приймаємо як є |
| Trading | Yes | ✅ Приймаємо + `extensions.trading_flat=true` (grid completeness) |
| Closed (break) | Yes | ❌ **Скіпаємо** (шум від брокера) |
| Closed (break) | No | ⚠️ Приймаємо + `extensions.calendar_pause_nonflat_anomaly=true` + WARNING лог |

### 6.3 Вплив на M3 деривацію

DeriveEngine (GenericBuffer) фільтрує calendar_pause_flat бари при побудові M3:

```python
trading = [b for b in bars if not b.extensions.get("calendar_pause_flat")]
```

Якщо всі 3 M1 — calendar_pause_flat → M3 не будується.
Якщо частково — M3 будується з `extensions.partial_calendar_pause=true`.

> **Note (S17):** Inline `_derive_m3()` видалено (changelog 20260221-028/029).
> M3 деривація виконується виключно через DeriveEngine.

---

## 7. Watermark і dedup

### 7.1 Watermark (per-symbol)

Кожен `M1SymbolPoller` тримає `_watermark_ms` — `open_time_ms` останнього committed M1 бару.

**Інваріант**: бари з `open_time_ms ≤ watermark` **не надсилаються** до UDS (pre-filter).

### 7.2 Watermark pre-filter

```python
# В poll_once():
if self._watermark_ms is not None:
    bars = [b for b in bars if b.open_time_ms > self._watermark_ms]
```

Це **критично** для запобігання:

- Stale/duplicate WARNING spam від UDS watermark guard
- Зайвих UDS операцій при кожному циклі

### 7.3 UDS watermark guard

`UDS.commit_final_bar()` має свій watermark. Бари з `open_time_ms ≤ uds_watermark` → reject з `reason="stale"`. Це другий рубіж захисту.

---

## 8. Tail catchup (bootstrap)

### 8.1 Проблема

Після рестарту `m1_poller` може мати гепи: диск має дані до моменту зупинки, але з того часу минуло N хвилин/годин.

### 8.2 Рішення (ADR-0002 P0.1)

`tail_catchup()` — викликається в `_bootstrap_warmup()` **ПЕРЕД** main loop:

1. Обчислює `missing = expected - watermark` (кількість пропущених M1)
2. Якщо `missing > tail_catchup_max_bars` (default 5000) → truncate + loud WARNING + `gap_state`
3. Fetch від FXCM: `fetch_last_n_m1(n=missing, date_to=cutoff+1M1)`
4. Фільтр: тільки `watermark < open_ms ≤ cutoff`
5. Ingest всі бари (з calendar-aware flat filter, M3 derive)
6. Після catchup: `gap_state = 0` якщо все заповнено

### 8.3 Конфігурація

```json
"m1_poller": {
    "tail_catchup_max_bars": 5000
}
```

5000 M1 = ~83 години = ~3.5 дні. Після довшого простою → gap_state degraded + live_recover подбере.

---

## 9. Live recover (gap auto-fill)

### 9.1 Проблема

Під час роботи можуть виникнути гепи (FXCM timeout, мережа, downtime). Бар може бути пропущений.

### 9.2 Рішення (ADR-0002 P0.2)

`_live_recover_check()` — викликається **після кожного poll_once()**:

1. Обчислює gap: `(expected - watermark) / M1_MS`
2. Якщо gap > `live_recover_threshold_bars` (default 3) → **enter recover mode**
3. В recover mode:
   - Fetch з cooldown (`live_recover_cooldown_s=5`)
   - Batch size = `min(gap, max_bars_per_cycle=120)`
   - Budget limit: `live_recover_max_total_bars=5000`
   - Degraded-but-loud: `uds.set_gap_state(policy="m1_live_recover_active")`
   - Фазовий лог кожні `log_interval_s=60`
4. Вихід з recover: `caught_up` або `max_total_reached` або `budget_exhausted`
5. Після виходу: `gap_state = 0`

### 9.3 Конфігурація

```json
"m1_poller": {
    "live_recover_threshold_bars": 3,
    "live_recover_max_bars_per_cycle": 120,
    "live_recover_cooldown_s": 5,
    "live_recover_max_total_bars": 5000,
    "live_recover_log_interval_s": 60
}
```

---

## 10. Stale detection

### 10.1 Проблема

M1 poller може "тихо зупинитись" (FXCM сесія мертва, але не кидає exception).

### 10.2 Рішення (ADR-0002 P0.3)

`_stale_check(now_ms)`:

1. Якщо ринок закритий → stale нерелевантний (skip)
2. Якщо `time.time() - last_new_bar_ts > stale_s` (default 720 = 12 хв) → loud WARNING
3. Throttle: перші 3 + кожне 60-е (Правило §9.1: анти-спам)

```json
"m1_poller": {
    "stale_s": 720
}
```

---

## 11. Derive Chain: M1 → M3, M5, M15, M30, H1, H4 (DeriveEngine)

> **ADR-0002**: Повний derive chain реалізовано через `DeriveEngine` (runtime/ingest/derive_engine.py).
> Чиста логіка агрегації — в `core/derive.py` (pure, без I/O).

### 11.1 Derive Chain

```text
M1 → M3 (×3)   + M5 (×5)
       M5 → M15 (×3)
              M15 → M30 (×2)
                     M30 → H1 (×2)
                            H1 → H4 (×4)
```

**DERIVE_ORDER**: `[180, 300, 900, 1800, 3600, 14400]` — порядок деривації.

**DERIVE_CHAIN** (core/derive.py):

```python
{60:   [(180, 3), (300, 5)],    # M1 → M3(3), M5(5)
 300:  [(900, 3)],               # M5 → M15(3)
 900:  [(1800, 2)],              # M15 → M30(2)
 1800: [(3600, 2)],              # M30 → H1(2)
 3600: [(14400, 4)]}             # H1 → H4(4)
```

### 11.2 DeriveEngine (runtime/ingest/derive_engine.py)

- **Cascade**: `on_bar(symbol, bar)` — отримує M1, каскадно деривує всі TF
- **Buffers**: `GenericBuffer` per (symbol, source_tf) з configurable max_keep
- **Commit**: Всі 6 derived TFs коммітяться в UDS (disk + Redis + updates bus)
- **Calendar-aware**: flat M1 під час break → calendar_pause_flat → виключається з агрегації
- **Thread-safe**: per-symbol lock для cascade integrity

### 11.3 Інтеграція з m1_poller

```python
# m1_poller.py → poll_once() → _ingest_bar(bar)
if bar.tf_s == 60:
    derive_engine.on_bar(symbol, bar)  # → cascade M3→M5→M15→M30→H1→H4
```

### 11.4 aggregate_bars (core/derive.py — pure)

1. Збирає N source барів з буфера, фільтрує `calendar_pause_flat`
2. Агрегація: `O=first.o, H=max(h), L=min(low), C=last.c, V=sum(v)`
3. `CandleBar(src="derived", complete=True)`
4. `assert_invariants()` → commit_final_bar()

---

## 12. [Видалено] Derived TF з M5 (engine_b)

> **Цю секцію видалено** — M5 polling і M15/M30/H1 деривація з M5 вимкнені (ADR-0002).
> Всі derived TFs тепер йдуть через DeriveEngine (§11).

---

## 13. D1 broker fetch (engine_b, D1-only)

> **Після ADR-0002**: engine_b фетчить тільки D1. H4 деривується з M1→DeriveEngine.

**D1**: поллиться з FXCM History API (`fetch_last_n_tf(tf_s=86400)`) при закритті D1 бакета.
Calendar-aware anchor: `day_anchor_offset_s_d1` / `day_anchor_offset_s_d1_alt`.
Запис через UDS: `commit_final_bar()`.

Основні методи:

- `_fetch_base_from_broker_on_close()` — перевіряє закриття bucket і фетчить
- `_cold_start_base_from_broker()` — bootstrap gap fill для D1
- `_prime_redis_from_disk()` — warmup Redis з disk при старті

---

## 14. Preview-plane (tick stream)

### 14.1 Потік

```text
FXCM ForexConnect → tick_publisher (BID mode) → Redis PubSub
  → tick_preview_worker → TickAggregator (M1/M3)
    → publish_preview_bar (complete=false) → Redis preview ring
    → publish_promoted_bar (complete=true, src=tick_promoted) → preview tail
```

### 14.2 Final→Preview bridge

Коли `m1_poller` commit-ить final M1/M3 бар → UDS автоматично публікує його у preview ring. **Final > Preview**: final завжди перемагає preview для одного ключа `(symbol, tf_s, open_ms)`.

### 14.3 Ізоляція

Preview-plane живе виключно в Redis (`{NS}:preview:*`). Не на диску. Не є SSOT.

---

## 15. Від UDS до UI: /api/bars і /api/updates

### 15.1 /api/bars (cold-load)

1. UI запитує: `GET /api/bars?symbol=XAU/USD&tf_s=60&limit=5000`
2. Server → `UDS.read_window()` → Redis snap (prefer) → RAM → Disk (тільки scrollback)
3. Конвертація: `open_time_ms → time` (epoch_s, LWC format)
4. Stitching: `open[i] = close[i-1]` (якщо увімкнено)
5. Відповідь: `{ bars: [...], meta: {...}, warnings: [...] }`

### 15.2 /api/updates (live)

1. UI поллить: `GET /api/updates?symbol=XAU/USD&tf_s=60&since_seq=123`
2. Server → `UDS.read_updates()` → Redis updates bus
3. Відповідь: `{ events: [...], cursor_seq: 456, boot_id: "..." }`
4. UI → `applyUpdates(events)`: sort by seq, final>preview, NoMix, upsert

---

## 16. PREVIOUS_CLOSE stitching

### 16.1 Що це

"TV-like smooth candles": `open[i] = close[i-1]`. Візуально свічки не мають гепів між собою.

### 16.2 Де застосовується

**Тільки в `/api/bars`** (display layer). SSOT на диску **НЕ модифікується**.

### 16.3 Чому не через FXCM

FXCM має свій PREVIOUS_CLOSE mode, але він створює артефакти (чергування volume, рвані переходи через breaks). Ми використовуємо FIRST_TICK від FXCM + власне stitching в UI layer.

---

## 17. Типові пастки і edge cases

### 17.1 Останній бар перед daily break

**Пастка**: якщо поллер має calendar gate (`if not market_open: return`), останній бар (напр. XAU/USD 21:59) ніколи не фетчиться.

**Рішення**: m1_poller НЕ має calendar gate. Calendar-aware expected знаходить останню торгову хвилину. Caught-up check запобігає зайвим fetch під час break.

### 17.2 FXCM зворотний порядок

**Пастка**: FXCM може повернути бари від новіших до старіших.

**Рішення**: `bars.sort(key=lambda b: b.open_time_ms)` перед ingest.

### 17.3 MID vs BID стрибки

**Пастка**: preview (tick stream) на MID, history на BID → видимі стрибки при promoted→final.

**Рішення**: `tick_stream_price_mode="bid"` (config.json).

### 17.4 Sparse дані (HKG33, NGAS)

Деякі інструменти мають мало торгових хвилин або sparse FXCM дані:

- **HKG33**: торгує обмежені години (cfd_hk_main), мало барів/день
- **NGAS**: може мати мікро-гепи (2-3 хвилини) через sparse FXCM дані

Це **не баг** — derived rebuilds працюють коректно, бо будуються тільки з наявних M1.

### 17.5 Duplicate m1_poller processes

**Пастка**: якщо запустити два m1_poller → duplicate-drop WARNING spam від UDS watermark.

**Рішення**: перед запуском перевіряти `Get-Process python | Where ...`. Supervisor (`app.main --mode all`) керує цим автоматично.

### 17.6 Warmup < Cold-start (RAM miss)

Для деяких TF warmup барів < кількості, яку UI запитує при cold-start → можливий empty chart.

**Мітігація**: Redis tail retention має бути достатнім; partial+loud rail (`insufficient_warmup`).

---

## 18. Конфігурація (config.json SSOT)

### 18.1 M1 Poller

```json
"m1_poller": {
    "enabled": true,
    "tail_fetch_n": 5,
    "safety_delay_s": 8,
    "m3_derive_enabled": true,
    "backfill_enabled": true,
    "backfill_max_bars": 1440,
    "tail_catchup_max_bars": 5000,
    "live_recover_threshold_bars": 3,
    "live_recover_max_bars_per_cycle": 120,
    "live_recover_cooldown_s": 5,
    "live_recover_max_total_bars": 5000,
    "live_recover_log_interval_s": 60,
    "stale_s": 720
}
```

| Ключ | Опис | Default |
| --- | --- | --- |
| `enabled` | Увімкнути M1 poller | `false` |
| `tail_fetch_n` | Кількість барів при звичайному fetch | `5` |
| `safety_delay_s` | Затримка після початку хвилини (FXCM latency) | `8` |
| `m3_derive_enabled` | Деривація M3 з M1 | `true` |
| `tail_catchup_max_bars` | Макс барів для tail catchup на bootstrap | `5000` |
| `live_recover_threshold_bars` | Поріг гепу для входу в live recover | `3` |
| `live_recover_max_bars_per_cycle` | Макс барів за один recover fetch | `120` |
| `live_recover_cooldown_s` | Cooldown між recover fetch | `5` |
| `live_recover_max_total_bars` | Бюджет recover (всього барів) | `5000` |
| `live_recover_log_interval_s` | Інтервал фазового логу в recover | `60` |
| `stale_s` | Поріг stale detection (секунди без нового бару) | `720` |

### 18.2 D1 Pipeline (engine_b)

```json
"broker_base_tfs_s": [86400],
"broker_base_fetch_on_close": true,
"broker_base_cold_start_enabled": true,
"broker_base_cold_start_counts": {"86400": 180}
```

### 18.3 Market calendar

```json
"market_calendar_symbol_groups": {
    "XAU/USD": "cfd_us_22_23",
    "GER30": "cfd_eu_21_07",
    "HKG33": "cfd_hk_main",
    ...
},
"market_calendar_by_group": {
    "cfd_us_22_23": {
        "enabled": true,
        "weekend_close_dow": 4,
        "weekend_close_hm": "21:55",
        "weekend_open_dow": 6,
        "weekend_open_hm": "23:05",
        "daily_break_start_hm": "22:00",
        "daily_break_end_hm": "23:00",
        "daily_break_enabled": true
    },
    ...
}
```

---

## 19. Діагностика і перевірка

### 19.1 Логи (ключові патерни)

| Лог | Значення |
| --- | --- |
| `M1_POLLER_STATS symbols=13 m1=75 m3=24 err=0 cal_skip=0 gaps=6 caught_up=0 recovering=0 stale=0` | Агрегована статистика (кожні 5 хв) |
| `M1_CALENDAR_STATE symbol=XAU/USD state=closed` | Зміна calendar state |
| `M1_GAP_DETECTED symbol=XAU/USD gap_bars=5` | Виявлено gap |
| `M1_LIVE_RECOVER_START symbol=XAU/USD gap_bars=120` | Вхід в recover mode |
| `M1_LIVE_RECOVER_DONE symbol=XAU/USD reason=caught_up` | Вихід з recover |
| `M1_STALE symbol=XAU/USD silence_s=800` | Stale detection спрацювало |
| `M1_TAIL_CATCHUP symbol=XAU/USD missing=350 written=348` | Tail catchup результат |
| `M1_NONFLAT_IN_PAUSE symbol=XAU/USD` | Аномалія: non-flat під час break |

### 19.2 HTTP endpoints для перевірки

```bash
# Свіжість M1 барів
curl "http://localhost:8089/api/updates?symbol=XAU/USD&tf_s=60&limit=5"

# Останні бари M1
curl "http://localhost:8089/api/bars?symbol=XAU/USD&tf_s=60&limit=10"

# Стан системи
curl "http://localhost:8089/api/status"
```

### 19.3 Діагностичні інструменти

```bash
# Перевірка гепів
python -m tools.diag.check_gaps

# Перевірка свіжості Redis
python -m tools.diag.check_freshness

# Exit gates
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

---

## Додаток A: Схема повного data flow (M1 → H4)

```text
FXCM History API (M1)
    ↓
fetch_last_n_m1(symbol, n, date_to)          ← provider.py
    ↓
normalize_history_to_bars()                   ← CandleBar(complete=True, src="history")
    ↓
[watermark pre-filter + cutoff filter + sort]  ← poll_once()
    ↓
_ingest_bar(bar)                               ← calendar-aware flat classification
    ↓
UDS.commit_final_bar(M1)                       ← disk SSOT + Redis snap + updates bus
    ↓                                              + bridge → preview ring
DeriveEngine.on_bar(symbol, M1)                ← cascade derive
    ├→ buffer M1 → derive M3 (×3) → commit_final_bar(M3)
    ├→ buffer M1 → derive M5 (×5) → commit_final_bar(M5)
    │    └→ buffer M5 → derive M15 (×3) → commit_final_bar(M15)
    │         └→ buffer M15 → derive M30 (×2) → commit_final_bar(M30)
    │              └→ buffer M30 → derive H1 (×2) → commit_final_bar(H1)
    │                   └→ buffer H1 → derive H4 (×4) → commit_final_bar(H4)
    ↓
/api/updates → UI applyUpdates()               ← live оновлення всіх TF
```

## Додаток B: Файли модуля (після ADR-0002 cleanup)

```text
runtime/ingest/polling/
├── __init__.py
├── README.md              # цей документ
├── m1_poller.py           # M1 fetch + DeriveEngine integration (1160 LOC)
├── engine_b.py            # D1-only broker fetch (~996 LOC)
├── dedup.py               # has_on_disk / mark_on_disk
└── fetch_policy.py        # expected_last_closed_m5_open_ms, last_trading_minute_open_ms

runtime/ingest/
├── derive_engine.py       # DeriveEngine — cascade M1→H4 з I/O (295 LOC)
├── market_calendar.py     # MarketCalendar, is_market_open
└── broker/fxcm/provider.py # FxcmHistoryProvider

core/
├── derive.py              # DERIVE_CHAIN, GenericBuffer, aggregate_bars (pure, 398 LOC)
├── buckets.py             # bucket_start_ms, tf_to_ms, resolve_anchor_offset_ms
└── model/bars.py          # CandleBar, assert_invariants
```

**Видалені файли (ADR-0002 cleanup)**:

- `runtime/ingest/polling/derive.py` — M5Buffer + derive_from_m5_for_anchor (replaced by core/derive.py + DeriveEngine)
- `runtime/ingest/polling/flat_filter.py` — is_flat_bar (moved to core/derive.py calendar_pause_flat)
- `runtime/ingest/polling/time_buckets.py` — floor_bucket_start_ms (consolidated into core/buckets.py)
    ↓
/api/updates → UI applyUpdates()               ← live оновлення графіку
/api/bars → UI setBars()                       ← cold-load (з stitching)

```
