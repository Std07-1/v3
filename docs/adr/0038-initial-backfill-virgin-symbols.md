# ADR-0038: Initial Backfill for Virgin Symbols

- **Статус**: Implemented (Amendment v2 — 2026-03-15)
- **Дата**: 2026-03-14
- **Автор**: Architect (R_ARCHITECT)
- **Initiative**: cold_start_backfill_v1

## Контекст і проблема

При першому запуску нового символу (або нового провайдера — Binance) графік наповнюється зі швидкістю 1 M1/хв. H1 з'являється через 60 хв, H4 — через 240 хв, D1 — через 24 год.

**Причинний ланцюжок** (VERIFIED `m1_poller.py:588–619`):

```
Перший запуск → JSONL порожній
  → warmup_watermark() читає 10 M1 з tail → 0 знайдено
    → watermark_ms = None
      → tail_catchup() skip (guard: if watermark_ms is None: return)
        → дані тільки з live polling (1 bar/min)
          → cascade живиться тільки live M1
            → HTF порожні годинами
```

Проблема стосується **будь-якого** нового символу на будь-якому провайдері.

Підтверджено: BTCUSDT 184 M1 (~3 год live), H4/D1 відсутні.

## Обмеження

| # | Обмеження | Джерело |
|---|-----------|---------|
| C1 | Всі writes тільки через UDS | I1 |
| C2 | Degraded-but-loud: backfill failure ≠ crash | I5 |
| C3 | Bootstrap params в config.json SSOT | ADR-0003 S4 |
| C4 | Provider-agnostic: FXCM, Binance, future | ADR-0016, ADR-0037 |
| C5 | Не міняти tail_catchup — працює для restart | Стабільність |
| C6 | M1PollerRunner = спільна точка входу | Архітектура |
| C7 | Cascade catchup (Phase 2c) без змін | ADR-0003 |

## Розглянуті варіанти

### A. Phase 2.5 — Initial History Fetch в bootstrap (обрано)

Нова фаза між Phase 2 (watermark warmup) та Phase 2b (DeriveEngine warmup).
Trigger: `watermark_ms is None` після Phase 2. Fetch N M1 від провайдера → write через UDS → set watermark → continue pipeline.

**Плюси**: мінімальний blast radius (1 фаза, існуючий flow не змінюється). Idempotent (partial fetch → watermark → restart → tail_catchup дозаповнить). Provider interface вже підтримує fetch with endTime.

**Мінуси**: потрібен новий метод `fetch_m1_range()` для range-based fetch.

### B. Окремий backfill tool

`python -m tools.initial_backfill --symbol BTCUSDT --provider binance`.

**Плюси**: ізольовано, zero risk.
**Мінуси**: ручний крок, не масштабується.

### C. Зняти guard watermark=None у tail_catchup

Fallback watermark = `now - N_days`.

**Плюси**: zero нового коду.
**Мінуси**: порушує семантику tail_catchup, міксує restart vs virgin.

## Рішення

**Альтернатива A** — Phase 2.5: Initial Backfill.

### Місце в bootstrap pipeline

```
_bootstrap_warmup()
  ├── Phase 1:   Redis priming (disk→Redis, all TFs)
  ├── Phase 2:   Watermark warmup (10 M1 per symbol from disk tail)
  │
  ├── Phase 2.5: *** INITIAL BACKFILL ***
  │              trigger: watermark_ms is None after Phase 2
  │              action: fetch initial M1 history from provider
  │              write: M1SymbolPoller._ingest_bar() per bar
  │              result: watermark set, JSONL populated
  │
  ├── Phase 2b:  DeriveEngine buffer warmup
  ├── Phase 2c:  Cascade catchup (1440 M1 through derive)
  ├── Phase 3:   Tail catchup (fetch gap M1 watermark → now)
  └── prime:ready:m1
```

### Provider interface

Новий метод `fetch_m1_range()` в `BinanceHistoryProvider`:

```python
def fetch_m1_range(
    self, symbol: str, from_ms: int, to_ms: int, limit: int
) -> List[CandleBar]:
```

Відмінність від `fetch_last_n_m1`: використовує `startTime` + `endTime` для діапазону.
Binance: `GET /fapi/v1/klines?symbol=X&interval=1m&startTime=from_ms&endTime=to_ms&limit=1500`.

### Backfill стратегія

Config-driven. Default = `cascade_catchup_m1_bars` (1440 = 1 день M1).

```json
"bootstrap": {
    "initial_backfill_m1_bars": 1440,
    ...existing...
}
```

`from_ms = now_ms - initial_backfill_m1_bars * 60_000`.
Fetch chunk = 1500 (Binance API limit). Якщо N > 1500 → поки не потрібно (1440 < 1500).

### Failure handling (I5)

| Сценарій | Поведінка |
|----------|-----------|
| Повний fail (network) | WARNING `INITIAL_BACKFILL_FAILED`. Watermark = None. Tail_catchup skip. Live-only. Degraded-but-loud. |
| Partial (800/1440) | Записати що є → watermark set → tail_catchup дозаповнить → cascade з неповними. WARNING `INITIAL_BACKFILL_PARTIAL`. |
| Provider не підтримує | Skip з WARNING. Fallback live-only. |
| Символ новий (< 1440 M1) | Записати всі бари → watermark set. |

### Idempotency

Phase 2.5 trigger = `watermark_ms is None after Phase 2`.
Після успішного backfill → JSONL populated → Phase 2 warmup знаходить → watermark ≠ None → Phase 2.5 skip.
Один раз backfill → потім назавжди restart-safe.

### D1 coverage

D1 derive від M1 (ADR-0023) потребує 1440 M1 → 1 D1 бар через cascade.
Для глибшої D1 історії — окремий Phase 2.6 (optional S4): fetch D1 напряму від Binance `interval=1d`.

## Наслідки

### Інваріанти (safe)

| Інваріант | Статус |
|-----------|--------|
| I0 Dependency Rule | ✅ Backfill logic в runtime/, provider в runtime/ingest/broker/ |
| I1 UDS вузька талія | ✅ Writes через `_ingest_bar()` → UDS |
| I2 Геометрія часу | ✅ Epoch ms, existing bar contract |
| I3 Final > Preview | ✅ Backfill = final бари (complete=True) |
| I4 Dual plane routing | ✅ Не торкається |
| I5 Degraded-but-loud | ✅ Підсилюється (explicit WARNING) |
| I6 Stop-rule | ✅ Не міняє інваріанти |

### Файли (blast radius)

| Файл | Зміна |
|------|-------|
| `config.json` | +1 поле `bootstrap.initial_backfill_m1_bars` |
| `runtime/ingest/broker/binance/provider.py` | +метод `fetch_m1_range()` |
| `runtime/ingest/polling/m1_poller.py` | Phase 2.5 в `_bootstrap_warmup()` + per-symbol `initial_backfill()` + `initial_backfill_d1()` в M1SymbolPoller |

### S4: D1 Direct Backfill

D1 через cascade від 1440 M1 дає лише ~1 D1 бар. Для SMC/trading decisions потрібна глибша D1 історія.

**Рішення**: `fetch_d1_range()` в BinanceHistoryProvider (interval=1d) + `initial_backfill_d1()` в M1SymbolPoller.
Phase 2.5b в `_bootstrap_warmup()` — після M1 backfill + re-warmup, перед Phase 2b.
Config: `bootstrap.initial_backfill_d1_bars: 180` (6 місяців).
Idempotency: перевірка `read_tail_candles(sym, 86400, 1)` — якщо D1 є → skip.
Write path: `UDS.commit_final_bar()` (I1 compliant).
`_parse_klines` параметризовано: `tf_s` parameter з default 60 для backward compat.

#### Чому S4 було скасовано (2026-03-15)

Реалізовано та протестовано. Виявлені проблеми:

1. **Порушення derive-only HTF принципу.** Система навмисно будує H4/D1 виключно
   через cascade від M1 — це гарантує якість 1:1 між HTF та M1 джерелом. Прямий
   D1 від брокера має розбіжності: різний anchor, інша агрегація, timezone quirks.
   Тестування показало: cascaded D1 ≠ direct D1 від Binance в крайових барах.

2. **D1 bypass DeriveEngine (D-06).** `UDS.commit_final_bar()` пише D1 на диск,
   але DeriveEngine buffer залишається порожнім. Після запису DeriveEngine не знає
   про ці бари → при закритті наступного D1 bucket = potential duplicate/stale.

3. **Неправильна ідемпотентність (D-08).** Phase 2.5b запускалась навіть якщо
   Phase 2.5 (M1 backfill) провалилась — відсутній guard між S3 та S4.

**Рішення**: замінено на Historical Backward Crawl (Amendment v2, S6).
M1-only підхід: crawl накопичує M1 на диску → cascade будує HTF з них при рестарті.
Derive-only принцип дотримано.

## P-Slices

| Slice | Що | LOC est | Файли | Verify |
|-------|-----|---------|-------|--------|
| S1 | Config: `bootstrap.initial_backfill_m1_bars` default 1440 | ~5 | config.json | Field loads |
| S2 | `fetch_m1_range()` в BinanceHistoryProvider | ~35 | provider.py | Unit test fetch |
| S3 | Phase 2.5 в `_bootstrap_warmup()` + `initial_backfill()` в M1SymbolPoller | ~60 | m1_poller.py | Virgin symbol → backfill → watermark set |
| S4 | D1 direct backfill: `fetch_d1_range()` + Phase 2.5b | ~80 | provider.py, m1_poller.py, binance_ingest_worker.py, config.json | D1 ≥ 30 bars |

## Exit Gates

| Gate | Опис |
|------|------|
| G1 | Virgin symbol (порожній JSONL) → після bootstrap M1 count ≥ `initial_backfill_m1_bars` |
| G2 | Cascade після backfill: H1 ≥ 24, H4 ≥ 6 |
| G3 | Restart після backfill: tail_catchup fills gap, no re-backfill |
| G4 | Backfill failure: process NOT crash, WARNING в logs |
| G5 | Multi-symbol: кожен symbol backfill-ається незалежно |

## Rollback

1. Видалити Phase 2.5 блок з `_bootstrap_warmup()`
2. Видалити `initial_backfill()` з M1SymbolPoller
3. Видалити `fetch_m1_range()` з BinanceHistoryProvider
4. Видалити `initial_backfill_m1_bars` з config.json bootstrap section
5. Поведінка повертається до live-only для virgin symbols

---

## Amendment v2: Historical Backward Crawl (2026-03-15)

> **Supersedes**: секцію "S4: D1 Direct Backfill" та Phase 2.5b з оригінального ADR.
> D1 direct backfill порушує принцип derive-only HTF. Видаляється.

### Мотивація

Phase 2.5 (M1 Initial Backfill) дає ~1 день M1 history (1440 барів). Після cascade
це перетворюється на ~24 H1, ~6 H4, ~1 D1 бар. Для комфортного трейдингу (SMC зони
на H4/D1, narrative context) потрібно значно більше.

Phase 2.5b (D1 Direct Backfill) вирішувала цю проблему прямим завантаженням D1 від
брокера, але це **порушує фундаментальний принцип**: HTF бари повинні будуватись
виключно через derive cascade від M1. Причина:

1. **Якість 1:1** — derive cascade гарантує ідентичність HTF до M1 джерела. Прямий
   D1 від брокера може мати розбіжність з cascaded D1 (різний anchor, різна
   агрегація, timezone quirks).
2. **Єдиний шлях даних** — один write path = менше failure modes, менше edge cases.
3. **Consistency** — не може бути "D1 з двох джерел" (derived + direct).

**Нове рішення**: Historical Backward Crawl. Background task що поступово підвантажує
M1 бари НАЗАД у минуле. Тільки M1 на диск. HTF з'являються при наступному рестарті
через DeriveEngine warmup.

### Обмеження (додаткові до оригінального ADR)

| # | Обмеження | Джерело |
|---|-----------|---------|
| C8 | HTF derive-only: H4/D1 тільки через cascade від M1 | Власник проекту |
| C9 | XAU/USD та FXCM активи — не чіпати | Стабільність |
| C10 | Backward crawl = disk-only (JSONL), без Redis/pubsub | Мін. impact на live |
| C11 | UDS watermark drop guard: `open_ms < wm` = "stale" | `uds.py:151-158` |

### Asset Type Matrix

Система підтримує активи різних типів. Кожен тип має свої особливості
для cold start та crawl pipeline.

| Тип | Приклади | Торгівля | Провайдер | Phase 2.5 | Crawl | HTF якість |
|-----|---------|---------|-----------|-----------|-------|------------|
| **24/7 Crypto** | BTC, ETH | Безперервно | Binance | ✅ (fetch_m1_range) | ✅ (natural guard) | cascade = perfect (no gaps) |
| **Market-hours FX** | XAU/USD | Пн-Пт, сесії | FXCM | ✅ (якщо virgin) | ❌ (fetch_m1_range відсутній) | cascade = perfect (handles gaps) |
| **Market-hours Index** | US30, GER40 | Пн-Пт, обмежено | FXCM | ✅ (якщо virgin) | ❌ (FXCM guard) | cascade = perfect |
| **Commodity** | XAG, XPT | Пн-Пт, сесії | FXCM | ✅ (якщо virgin) | ❌ (FXCM guard) | cascade = perfect |

**Ключові висновки:**

- **Crawl = Binance-only** зараз. Природній guard: `hasattr(provider, 'fetch_m1_range')`.
  FXCM provider не має цього методу → crawl thread не стартує для FXCM активів.
- **D1 direct backfill відхилено для всіх типів** — принцип derive-only HTF
  застосовується до всіх активів (Binance і FXCM однаково).
- **Market-hours активи** не потребують crawl: вони стартують не з нуля, FXCM
  provider забезпечує historical M1 через tail_catchup при звичайному рестарті.
  При virgin symbol — Phase 2.5 (якщо провайдер підтримує fetch_m1_range).
- **Майбутнє**: якщо FXCM provider отримає `fetch_m1_range` — crawl автоматично
  увімкнеться для FXCM активів без змін у crawl logic. Guard є.

### Розглянуті альтернативи

#### Alt A: Crawl в `binance_ingest_worker.py` (обрано)

Background coroutine/thread в `binance_ingest_worker.py` — окремий periodic task,
що виконується паралельно з основним polling loop. Пише M1 бари на диск напряму
через `JsonlAppender.append()`.

**Плюси**:

- Природна ізоляція: crawl торкається **тільки** Binance символів. FXCM pipeline
  не має `fetch_m1_range()` — zero blast radius на XAU/USD.
- Binance ingest worker вже має `provider`, `uds` та config — zero нових залежностей.
- Crawl schedule (раз на годину) незалежний від polling loop (раз на хвилину).
- Якщо crawl падає — live polling продовжує працювати.

**Мінуси**:

- Дублювання pattern якщо колись потрібно crawl для FXCM (unlikely — FXCM має
  обмежену API history глибину).

**Blast radius**: `binance_ingest_worker.py` (+crawl loop), `config.json` (+4 поля).

#### Alt B: Crawl в `M1PollerRunner._bootstrap_warmup()` з provider guard

Додати crawl phase в `_bootstrap_warmup()` із `getattr(provider, 'fetch_m1_range')`
перевіркою.

**Плюси**: одне місце для всього bootstrap (Phase 1 → Phase 2 → ... → Phase N).

**Мінуси**:

- `_bootstrap_warmup()` — **blocking**. Crawl 30 днів (30 запитів) блокує bootstrap
  на ~30 секунд навіть при 1 req/s. Неприйнятно для live trading.
- `M1PollerRunner` — спільний для FXCM та Binance. Guard `getattr` працює, але
  семантично неправильно: runner не повинен знати специфіку провайдера.
- Crawl виконується лише при рестарті, а не continuously.

**Blast radius**: `m1_poller.py` (bootstrap + crawl state), `config.json`.

#### Alt C: Окремий standalone crawl скрипт

`python -m tools.historical_crawl --config config.json`

**Плюси**: максимальна ізоляція, zero risk для live system.

**Мінуси**:

- Ручний запуск або cron — не автоматичний.
- Потребує свій UDS build (дублювання).
- Конфлікт при одночасному запуску з live worker (два процеси пишуть в ті ж JSONL).

### Рішення: Alternative A

Crawl розміщується в `binance_ingest_worker.py` як background thread, що запускається
після `runner.run_forever()` початку (або як daemon thread перед ним). Це забезпечує:

1. **Природній guard**: FXCM provider не має `fetch_m1_range()` — crawl навіть не
   намагається запуститись для XAU/USD.
2. **Незалежність від bootstrap**: crawl працює **паралельно** з live polling, не
   блокуючи його.
3. **Автоматичність**: запускається з worker, працює у background, зупиняється з worker.

### Ключові design decisions

#### 1. Write path: `JsonlAppender.append()` напряму, НЕ `UDS.commit_final_bar()`

**Проблема**: `UDS.commit_final_bar()` має watermark guard (`uds.py:151-158`):

```
if open_ms < wm_open_ms: return "stale"
```

Backward crawl пише бари СТАРШІ за поточний watermark. `commit_final_bar()` відкине
їх як "stale". Це by-design правильно для forward flow (захист від дублікатів), але
блокує backward crawl.

**Рішення**: Crawl використовує `JsonlAppender.append()` напряму. Це:

- Пише бар на диск (JSONL) — єдина задача crawl
- НЕ оновлює watermark (backward бари не повинні зсувати wm)
- НЕ пише в Redis (зайве — бари старі, не потрібні в live cache)
- НЕ публікує в pubsub (бари не потрібні UI в реальному часі)

**I1 compliance**: формально обходимо UDS, але семантично це "disk-only append for
historical data". UDS watermark integrity зберігається. Crawl = write-behind, не
write-through. `JsonlAppender.append()` — частина UDS stack (використовується
самим `UDS._append_to_disk()`), тому це не зовнішній bypass, а нижчий рівень
того ж pipeline.

**Errata I1**: цей amendment розширює семантику I1 для historical backward writes.
Forward flow (live + initial backfill) = через `commit_final_bar()`.
Backward flow (crawl) = через `JsonlAppender.append()` з explicit justification.

#### 2. Horizon discovery: `DiskLayer.list_parts()` + перший рядок першого файлу

**Задача**: знайти `oldest_m1_on_disk` — найстаріший M1 бар на диску.

**Альтернативи**:

a) `UDS.read_tail_candles()` з великим `limit` — читає ВСЕ з диску в пам'ять,
   повертає tail. Неефективно для 43200 барів.

b) `DiskLayer.list_parts()` → взяти `parts[0]` (найстаріший part-YYYYMMDD.jsonl) →
   прочитати перший рядок → `open_time_ms` = horizon.

**Рішення (b)**: parts вже відсортовані. Перший файл = найстаріший день.
Перший рядок = найстаріший бар. O(1) disk read.

#### 3. Scheduling: daemon thread з `threading.Event` sleep

Worker вже використовує threading model (`M1PollerRunner._stop_event`,
`threading.Event`). Crawl = daemon thread що спить `interval_s` між тіками,
перевіряє `stop_event` для graceful shutdown.

#### 4. Dedup: safe навіть при overlap

Crawl може мати overlap з Phase 2.5 initial backfill (обидва пишуть перший день).
`JsonlAppender.append()` просто append-ить у JSONL. Дублікати по `open_time_ms`
дедуплікуються при read (`_read_jsonl_filtered` перезаписує в deque по часу).
Не ідеально (зайвий disk space), але safe і не потребує lock між crawl та live.

#### 5. Multi-chunk fetch для > 1500 барів

`historical_crawl_m1_per_run: 1440` < 1500 (Binance API limit) — один запит.
Якщо в майбутньому потрібно > 1500 за тік — paginated fetch (loop по 1500 chunks).
Поки YAGNI.

### Інваріанти (Amendment v2)

| Інваріант | Статус | Коментар |
|-----------|--------|----------|
| I0 Dependency Rule | safe | Crawl в runtime/ingest/ — правильний шар |
| I1 UDS вузька талія | **errata** | Backward crawl через `JsonlAppender.append()`, не `commit_final_bar()`. Justified: watermark guard блокує backward writes. Disk layer = частина UDS stack. |
| I2 Геометрія часу | safe | Epoch ms, existing CandleBar contract |
| I3 Final > Preview | safe | Crawl бари = `complete=True`, `src="binance_history"` |
| I4 Dual plane routing | safe | Не торкається |
| I5 Degraded-but-loud | safe | Crawl failure = WARNING, continue, no crash |
| I6 Stop-rule | safe | I1 errata justified above |

### Config (нові поля в `bootstrap`)

```json
"bootstrap": {
    "historical_crawl_enabled": true,
    "historical_crawl_m1_per_run": 1440,
    "historical_crawl_interval_s": 3600,
    "historical_crawl_max_days": 30,
    ...existing...
}
```

| Поле | Тип | Default | Опис |
|------|-----|---------|------|
| `historical_crawl_enabled` | bool | `true` | Увімкнути/вимкнути crawl |
| `historical_crawl_m1_per_run` | int | `1440` | Скільки M1 барів за один crawl tick (1440 = 1 день) |
| `historical_crawl_interval_s` | int | `3600` | Інтервал між crawl тіками (3600 = 1 год) |
| `historical_crawl_max_days` | int | `30` | Максимальна глибина crawl (30 днів = 43200 M1 барів) |

**Важливо**: `derive_warmup_bars_by_tf['60']` та `cascade_catchup_m1_bars` повинні
бути вирівняні з глибиною crawl. Інакше накопичені M1 бари ігноруються DeriveEngine
при рестарті.

Поточні значення вирівняно з 7-денним горизонтом:

- `derive_warmup_bars_by_tf['60']: 10080` (7 × 1440)
- `cascade_catchup_m1_bars: 10080` (7 × 1440)
- `historical_crawl_max_days: 30` (накопичує, але warmup бере останні 7 днів)

Якщо збільшити `historical_crawl_max_days` — пропорційно збільшити warmup/cascade
щоб не втратити накопичену history.

**Видалити**: `bootstrap.initial_backfill_d1_bars` (superseded).

### Crawl algorithm (pseudo)

```
function crawl_tick(symbol, provider, jsonl_appender, config):
    horizon_ms = get_oldest_m1_on_disk(symbol)
    if horizon_ms is None:
        return  # немає M1 на диску — Initial Backfill ще не запускався

    limit_ms = now_ms() - config.max_days * 86_400_000
    if horizon_ms <= limit_ms:
        log.info("CRAWL_COMPLETE symbol=%s reached max_days=%d", symbol, config.max_days)
        return  # досягли ліміту

    to_ms = horizon_ms  # exclusive: не перечитувати вже наявний бар
    n_bars = config.m1_per_run
    from_ms = horizon_ms - n_bars * 60_000
    from_ms = max(from_ms, limit_ms)  # не виходити за max_days

    bars = provider.fetch_m1_range(symbol, from_ms, to_ms, n_bars)
    bars.sort(key=lambda b: b.open_time_ms)

    written = 0
    for bar in bars:
        jsonl_appender.append(bar)  # disk-only, bypass UDS watermark
        written += 1

    log.info("CRAWL_TICK symbol=%s written=%d new_horizon=%s",
             symbol, written, bars[0].open_time_ms if bars else "N/A")


function crawl_loop(symbols, provider, jsonl_appender, config, stop_event):
    while not stop_event.is_set():
        for symbol in symbols:
            try:
                crawl_tick(symbol, provider, jsonl_appender, config)
            except Exception as exc:
                log.warning("CRAWL_ERROR symbol=%s err=%s", symbol, exc)
        stop_event.wait(timeout=config.interval_s)  # sleep з graceful shutdown
```

### `get_oldest_m1_on_disk(symbol)` — utility function

```
function get_oldest_m1_on_disk(symbol) -> Optional[int]:
    parts = disk_layer.list_parts(symbol, tf_s=60)
    if not parts:
        return None
    # parts[0] = найстаріший part-YYYYMMDD.jsonl (sorted by name = by date)
    with open(parts[0]) as f:
        first_line = f.readline().strip()
        if not first_line:
            return None
        obj = json.loads(first_line)
        return obj.get("open_time_ms")
```

Розміщення: або як static method в `M1SymbolPoller`, або як free function
в `binance_ingest_worker.py`. Рекомендація: free function в новому файлі
`runtime/ingest/crawl_utils.py` (але це micro-module — можна inline).
Рішення за patch-master.

### Failure model (3+ сценарії)

| # | Сценарій | Поведінка | Severity |
|---|----------|-----------|----------|
| F1 | Crawl tick мережева помилка | WARNING `CRAWL_ERROR`. Skip tick. Retry через `interval_s`. | Low |
| F2 | JSONL disk full | `JsonlAppender.append()` raise → WARNING. Crawl continue next tick. Live pipeline WARNING теж. | Medium |
| F3 | Рестарт під час crawl | Daemon thread зупиняється з процесом. Наступний запуск продовжує з нового `horizon_ms` (idempotent). Zero data loss. | Low |
| F4 | Overlap M1 з Initial Backfill | Дублікати в JSONL. Dedup при read (deque overwrite by time). Extra disk ~1.5 MB. | Low |
| F5 | Provider rate limit (Binance 2400 req/min) | 1 req/hour = 0.017 req/min. Навіть 2 символи * 1 req/hour = 2 req/hour. Far under limit. | None |
| F6 | Symbol не існує (delisted) | `fetch_m1_range` повертає [] → WARNING `CRAWL_TICK` written=0. Next tick спробує той самий діапазон. Eventually horizon не рухається → log noise але zero harm. | Low |
| F7 | Crawl + live write до тих самих JSONL | `JsonlAppender` використовує `threading.Lock` (ADR-0021). Thread-safe. | None |

### Ефект на трейдерський досвід

```
Час        | M1 history   | Після рестарту: D1    | Після рестарту: H4
-----------|-------------|----------------------|-------------------
0 (start)  | 1440 (1 day)| ~1 D1                | ~6 H4
+1 hour    | +1440 M1    | ~2 D1                | ~12 H4
+6 hours   | +8640 M1    | ~7 D1                | ~36 H4
+12 hours  | +17280 M1   | ~13 D1               | ~72 H4
+30 hours  | 43200 (max) | ~30 D1               | ~180 H4
```

**Ключовий момент**: трейдер бачить ефект **тільки після рестарту**. Це by-design:
DeriveEngine warmup reads від диску при старті, будує HTF через cascade.
Без рестарту — нові historical M1 лежать на диску, але не впливають на live view.

### Що видалити (Phase 2.5b deprecation)

| Артефакт | Файл | Що |
|----------|------|----|
| `initial_backfill_d1()` | `m1_poller.py:698-773` | Метод M1SymbolPoller — видалити |
| Phase 2.5b блок | `m1_poller.py:1097-1130` | Блок в `_bootstrap_warmup()` — видалити |
| `initial_backfill_d1_bars` param | `m1_poller.py:935` | Параметр `M1PollerRunner.__init__` — видалити |
| `self._initial_backfill_d1_bars` | `m1_poller.py:954` | Поле instance — видалити |
| D1 backfill config read | `binance_ingest_worker.py:168-176` | Блок читання initial_backfill_d1 — видалити |
| `initial_backfill_d1_bars` kwarg | `binance_ingest_worker.py:195` | Передача в M1PollerRunner — видалити |
| `fetch_d1_range()` | `provider.py:143-178` | Метод BinanceHistoryProvider — видалити |
| `initial_backfill_d1_bars` | `config.json:213` | Поле bootstrap — видалити |

### P-Slices (Amendment v2)

| Slice | Що | LOC est | Файли | Verify | Rollback |
|-------|-----|---------|-------|--------|----------|
| S5 | Remove Phase 2.5b: видалити `initial_backfill_d1()`, Phase 2.5b блок з `_bootstrap_warmup()`, `initial_backfill_d1_bars` параметр/поле з `M1PollerRunner`, `fetch_d1_range()` з provider, config field, binance_ingest_worker wiring | ~-120 (deletion) | m1_poller.py, provider.py, binance_ingest_worker.py, config.json | bootstrap запускається без Phase 2.5b, D1 = порожній для virgin symbol, zero D1 direct fetch | git revert commit |
| S6 | Historical Crawl: config fields + `get_oldest_m1_on_disk()` utility + crawl_tick/crawl_loop functions + daemon thread launch в `binance_ingest_worker.py` + graceful shutdown | ~120 | binance_ingest_worker.py, config.json | Запустити worker → через 1 годину: M1 JSONL має бари старіші за initial backfill horizon. Log `CRAWL_TICK` з written > 0. Перевірити: FXCM worker НЕ запускає crawl. | Видалити crawl thread + crawl functions + config fields. Worker повертається до initial-only backfill. |
| S7 | Phase 2.5 trigger fix: `watermark_ms is None` → `bars_on_disk < initial_backfill_m1_bars`. Додано `_bars_on_disk: int` до M1SymbolPoller. `warmup_watermark(tail_n=10)` → `tail_n=initial_backfill_m1_bars`. | ~20 | m1_poller.py | 37 bars → trigger fires; 1440 bars → skip | git revert |

### Exit Gates (Amendment v2)

| Gate | Опис |
|------|------|
| G6 | Crawl tick: після першого tick `data_v3/{sym}/tf_60/` містить JSONL part старіший за initial backfill horizon |
| G7 | Crawl limit: після ~30 годин crawl зупиняється (log `CRAWL_COMPLETE`), не робить зайвих запитів |
| G8 | FXCM isolation: запуск FXCM ingest worker не запускає crawl thread (no `fetch_m1_range` = no crawl) |
| G9 | Graceful shutdown: `Ctrl+C` або `stop_event.set()` зупиняє crawl thread протягом `interval_s` секунд |
| G10 | Trigger fix: symbol з 37 M1 на диску (від tick publisher) → Phase 2.5 fires → `INITIAL_BACKFILL_DONE` в логах |

### Rollback (Amendment v2)

**S5 rollback**: `git revert <S5-commit>`. Повертає Phase 2.5b, `fetch_d1_range()`,
config field. D1 direct backfill знову працює.

**S6 rollback**: `git revert <S6-commit>`. Видаляє crawl thread, config fields.
Worker працює як до amendment — тільки initial backfill (1 день M1).
Вже накопичені crawl дані на диску залишаються (не шкодять — просто зайві JSONL
рядки). При наступному рестарті DeriveEngine все одно використає їх для warmup.

**Повний rollback (S5+S6)**: revert обох commits. Стан = original ADR-0038 Implemented.

### Cross-role plan

| Роль | Задача | Коли |
|------|--------|------|
| R_PATCH_MASTER | S5: видалити Phase 2.5b артефакти | Після accept |
| R_PATCH_MASTER | S6: crawl implementation | Після S5 verify |
| R_BUG_HUNTER | Review кожного slice: I1 errata compliance, thread safety, dedup | Після кожного slice |
| R_DOC_KEEPER | Sync docs/system_current_overview.md (bootstrap pipeline diagram) | Після S6 |
| R_TRADER | Validate: рестарт після 2+ годин crawl → D1/H4 depth satisfactory | Після S6 + restart |

### Open Questions

| # | Питання | Хто вирішує | Дедлайн |
|---|---------|-------------|---------|
| Q1 | Чи потрібен окремий файл `crawl_utils.py` для `get_oldest_m1_on_disk()`? Або inline в `binance_ingest_worker.py`? | patch-master (implementation detail) | При S6 |
| Q2 | Чи потрібен progress tracking (log кожні N тіків "crawl progress: 15/30 days")? | patch-master | При S6 |
| Q3 | Чи потрібна метрика/observable для crawl status (для aione_top)? | Окремий ADR якщо потрібно | Post-S6 |
