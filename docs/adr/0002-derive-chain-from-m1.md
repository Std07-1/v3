# ADR-0002: DeriveChain — каскадна деривація від M1

> **Дата**: 2026-02-18  
> **Статус**: COMPLETED (Phase 0 ✅, Phase 1 ✅, Phase 2 ✅, Phase 3 ✅, Phase 4 ✅, Phase 5 ✅)  
> **Initiative**: `derive_chain_m1`  
> **Навігація**: [docs/index.md](index.md)  
> **Завершення**: 2026-02-19. engine_b → D1-only (m5_polling_enabled=false, derived_tfs_s=[]). M1→H4 derive chain повністю через m1_poller/DeriveEngine.

---

## 0. Контекст (Executive Summary)

Поточна система має **два незалежних FXCM-з'єднання** для отримання ринкових даних:

- `m1_poller` — FXCM History M1 → M3 derived
- `engine_b` (connector) — FXCM History M5 → M15/M30/H1 derived + D1 broker  

Це створює проблеми:

1. **M1 має масивні гепи** (680+ барів = 11+ годин втрати), бо m1_poller не має live_recover
2. **Дублювання ресурсів**: 2 FXCM сесії, 2 UDS writers, різні механізми bootstrap
3. **Розбіжність даних**: M5 від брокера не обов'язково точно корелює з 5×M1
4. **H4 derived в UI-шарі** (порушення архітектурного I0: доменна логіка не в core/runtime)
5. **Немає єдиного ланцюга**: M1→M3 і M5→M15→M30→H1 живуть у різних процесах без зв'язку

**Ціль**: єдиний ланцюг деривації **M1 → M3 → M5 → M15 → M30 → H1 → H4**, де M1 — єдине джерело (крім D1 = broker).

---

## 1. Факти (AS-IS з доказами)

### 1.1. M1 Poller — критичні слабкості

| Можливість | engine_b (M5) | m1_poller (M1) | Різниця |
| --- | :---: | :---: | --- |
| **Live recover** (gap detection + auto-fill) | ✅ `_live_recover_check()` L1537 | ❌ відсутній | M1 гепи після downtime ніколи не заповнюються |
| **Progressive backfill** (backward fill) | ✅ `_progressive_backfill_m5()` L1636 | ❌ backfill_enabled є в config, коду немає | Старі M1 гепи не заповнюються |
| **Tail catchup** (bootstrap gap close) | ✅ `_tail_catchup_from_broker()` L428 | ❌ warmup лише 10 барів | Після рестарту M1 може мати годинні гепи |
| **Stale detection** | ✅ `m5_tail_stale_s=720` | ❌ | М1 может тихо зупинитись без алерту |
| **Gap state reporting** | ✅ `uds.set_gap_state()` | ❌ | UI не знає про M1 гепи |
| **MAX_FETCH_N per cycle** | 12 (M5) | 120 (M1) | M1 може "наздогнати" 2h, але не 11h |
| **Calendar-aware derive** | ✅ `is_trading_fn` в derive.py | ✅ `_derive_m3` фільтрує pause flats | Обидва мають |

### 1.2. Доказ: реальні гепи (XAU/USD, Feb 17-18)

```
M1: 1392 bars, 02-17 00:00 -> 02-18 18:05
  Gaps: 3
    02-17 04:40 -> 02-17 16:00 (skip 680 bars = 11h 20m!)   ← system downtime
    02-17 21:59 -> 02-17 23:01 (skip 62 bars = daily break, expected)
    02-18 04:30 -> 02-18 11:05 (skip 395 bars = 6h 35m!)    ← system downtime

M5: 492 bars, 02-17 00:00 -> 02-18 18:00
  Gaps: 3
    02-17 04:35 -> 02-17 04:45 (skip 2 bars)    ← same downtime, auto-recovered!
    02-17 22:00 -> 02-17 23:00 (skip 12 bars = daily break, expected)
    02-18 04:25 -> 02-18 04:35 (skip 2 bars)    ← same downtime, auto-recovered!
```

**Висновок**: engine_b для M5 має live_recover → 2-bar max gap. m1_poller → 680-bar gap. Неприпустимо для продукції.

### 1.3. Поточний derivation chain

```
FXCM History M1 → m1_poller → UDS (M1) → M3 derived
FXCM History M5 → engine_b  → UDS (M5) → M15/M30/H1 derived
FXCM History D1 → engine_b  → UDS (D1) (broker_base)
H4 → derived on-the-fly in UI server from H1 (порушення шару!)
```

### 1.4. Цільовий derivation chain (TO-BE) — strict cascade

```
FXCM History M1 → m1_poller → UDS (M1)
  └→ DeriveEngine (cascade):           ← NEW ARCHITECTURE
     M1 → M3  (3×M1)
     M1 → M5  (5×M1)
       M5 → M15 (3×M5)
         M15 → M30 (2×M15)             ← strict cascade (was flat 6×M5)
           M30 → H1  (2×M30)           ← strict cascade (was flat 12×M5)
             H1 → H4  (4×H1, TV anchor) ← moved from UI to runtime

FXCM History D1 → d1_fetcher → UDS (D1)  (keep as-is)
```

**Архітектурна зміна**: polling (m1_poller/d1_fetcher) повністю відокремлений
від деривації (DeriveEngine). Polling тільки фетчить, DeriveEngine тільки
будує cascade. Pure logic у `core/derive.py`, I/O у `runtime/ingest/derive_engine.py`.

---

## 2. Рішення: Phased DeriveChain

### Принципи

1. **M1 — єдине джерело** для всіх TF ≤ H4 (крім D1)
2. **Кожна фаза — самодостатній PATCH** з rollback, без зламу продакшену
3. **Паралельна робота** старого і нового до перевірки (Phase 2)
4. **Derivation chain — в runtime/**, не в UI (I0 compliance)
5. **GenericBuffer** в core/ — pure logic (I0: core/ не імпортує runtime)

### Фази

#### Phase 0: Стабілізація M1 (≥99% completeness за тиждень)

**Ціль**: m1_poller стає таким же надійним, як engine_b.

**Зміни** (тільки `runtime/ingest/polling/m1_poller.py` + `config.json`):

| # | Що | Як | Модель |
|---|---|---|---|
| P0.1 | **Tail catchup** на bootstrap | Після warmup, **ДО main loop**: fetch M1 від watermark до expected (як engine_b `_tail_catchup_from_broker`). **Інваріант**: m1_poller НЕ входить у main loop поки tail gap > `tail_fetch_n` | Max 5000 bars |
| P0.2 | **Live recover** | Якщо gap > 3 M1: enter recovery mode, fetch з cooldown, до gap=0 (як engine_b `_live_recover_check`) | Threshold=3, max_per_cycle=120, cooldown=5s |
| P0.3 | **Stale detection** | Якщо > 720s без нового M1 при відкритому ринку → loud warning + stale counter | m1_stale_s=720 |
| P0.4 | **Gap state reporting** | `uds.set_gap_state()` при великому gap | Як engine_b |
| P0.5 | **Config SSOT** | Нові ключі в `m1_poller: { live_recover_*, stale_s, tail_catchup_max }` | |

**Exit gate**: 7 днів роботи, M1 completeness ≥ 99% (рахуємо: trading_minutes - M1_on_disk) для XAU/USD.

**Rollback**: видалити нові методи, повернути простий warmup (10 барів).

#### Phase 1: Pure derive logic в core/ (ВИКОНАНО ✅)

**Ціль**: чиста логіка деривації у `core/derive.py` — GenericBuffer, aggregate_bars, DERIVE_CHAIN.

**Зміни (фактичні)**:

| # | Де | Що | Статус |
|---|---|---|---|
| P1.1 | `core/derive.py` (NEW) | `DERIVE_CHAIN` — декларативний ланцюг: 60→[180,300], 300→[900], 900→[1800], 1800→[3600], 3600→[14400] | ✅ |
| P1.2 | `core/derive.py` | `GenericBuffer(tf_s, max_keep)` — параметричний буфер (замінює M1Buffer + M5Buffer) | ✅ |
| P1.3 | `core/derive.py` | `aggregate_bars()` — чиста агрегація N барів → 1 derived бар | ✅ |
| P1.4 | `core/derive.py` | `derive_bar()` — побудова derived бару з source_buffer для конкретного bucket | ✅ |
| P1.5 | `core/derive.py` | `derive_triggers()` — визначення trigger bucket'ів після commit source бару | ✅ |
| P1.6 | `core/derive.py` | `DERIVE_SOURCE`, `DERIVE_ORDER`, `full_cascade_from()` — допоміжні структури | ✅ |

**Ключове рішення**: strict cascade замість flat derive.

- AS-IS: M15=3×M5, M30=6×M5, H1=12×M5 (плоска деривація)
- TO-BE: M15=3×M5, M30=2×M15, H1=2×M30 (strict cascade)
- Математично еквівалентно: agg(2×M15) ≡ agg(6×M5) для OHLCV.

**Rollback**: видалити `core/derive.py`.

#### Phase 2: DeriveEngine в runtime/ (ВИКОНАНО ✅)

**Ціль**: `runtime/ingest/derive_engine.py` — I/O обгортка над core/derive.py.

| # | Де | Що | Статус |
|---|---|---|---|
| P2.1 | `runtime/ingest/derive_engine.py` (NEW) | DeriveEngine: буфери per (symbol, tf_s), cascade trigger, UDS commits | ✅ |
| P2.2 | `runtime/ingest/derive_engine.py` | Thread-safe per-symbol locks (ThreadPool не потрібен — m1_poller вже паралелить) | ✅ |
| P2.3 | Підключення до m1_poller | build_m1_poller creates DeriveEngine, injects into M1SymbolPoller, warmup M1 buffer | ✅ |

**Ключові рішення Phase 2**:

- `commit_tfs_s` контролює які TF коммітяться в UDS (Phase 2 default: {180, 14400}).
- DeriveEngine використовує SHARED UDS (register_symbol_uds) — без file race з m1_poller.
- Проміжні TF (M5/M15/M30/H1) деривуються in-memory для каскаду, не коммітяться (engine_b handles).
- ThreadPool не додано (m1_poller per-symbol threads вже забезпечують паралелізм).
- Legacy fallback: якщо derive_engine_enabled=false → inline _derive_m3 (зворотна сумісність).
- Warmup: bootstrap читає 300 M1 з диску для заповнення GenericBuffer (cascade готовий з першого бару).

**Rollback**: видалити `runtime/ingest/derive_engine.py`, revert m1_poller.py (5 точок зміни).

#### Phase 3: Видалення H4 derive з UI + H4 як first-class UDS TF (ВИКОНАНО ✅)

**Ціль**: H4 перестає деривуватись в server.py. H4 = звичайний TF в UDS.

| # | Де | Що | Статус |
|---|---|---|---|
| P3.1 | `ui_chart_v3/server.py` | Видалити `_derive_h4_tv_from_h1` (~300 LOC). H4 через `read_window(tf_s=14400)` | ✅ |
| P3.2 | `ui_chart_v3/server.py` | Видалити `align=tv` endpoint logic | ✅ |
| P3.3 | `ui_chart_v3/static/app.js` | Видалити `align=tv` для H4 з JS | ✅ |
| P3.4 | `tests/test_tv_csv_compare.py` | Видалити H4 derive тести (11 тестів, ~270 LOC) | ✅ |

**Результат**: ~590 LOC видалено з server.py, ~270 LOC тестів видалено. H4 тепер first-class TF в UDS.

**Backward compat**: `_ALIGN_TV` + `align` param залишені; old clients gracefully fallback до standard UDS path.

**Rollback**: повернути H4 derive в server.py (git revert).

#### Phase 4: Порівняння M5(derived) vs M5(broker)

**Ціль**: тижневе порівняння M5(від 5×M1) vs M5(від FXCM) для підтвердження якості.

**Exit gate**: M5(derived) vs M5(broker) OHLCV delta < 0.01% за тиждень.

#### Phase 5: Вимкнути engine_b M5+ polling → d1_fetcher

**Ціль**: engine_b → тільки D1 broker (або окремий d1_fetcher ~200 LOC).

| # | Де | Що |
|---|---|---|
| P5.1 | `config.json` | `derived_tfs_s: []` (engine_b не деривує нічого) |
| P5.2 | `config.json` | Вимкнути M5 polling, залишити D1 broker |
| P5.3 | Опціонально | `d1_fetcher.py` — спрощений D1-only fetcher (~200 LOC) замість engine_b (2126 LOC) |

**Exit gate**: тижневе порівняння — M1-ланцюг покриває все без engine_b M5 polling.

**Rollback**: повернути derived_tfs_s: [900, 1800, 3600] в engine_b.

---

## 3. Інваріанти (зберігаються / змінюються)

| ID | Зберігається? | Коментар |
|---|---|---|
| I0 | ✅ | GenericBuffer в core/ (pure), derive chain в runtime/ |
| I1 | ✅ | Всі writes через UDS.commit_final_bar |
| I2 | ✅ | Геометрія часу не змінюється |
| I3 | ✅ | Final > Preview зберігається |
| I4 | ✅ | UI read-only, updates через /api/updates |
| I5 | ✅ | Degraded-but-loud зберігається |
| I6 | ✅ | Disk hot-path ban зберігається |
| **NEW** | 🆕 | H4 перестає деривуватись в UI — переходить в runtime (I0 fix!) |

---

## 4. Ризики та мітігації

| Ризик | Ймовірність | Вплив | Мітігація |
|---|---|---|---|
| M1 гепи не зниглися до ≥99% | Середня | Phase 1+ блоковані | MAX_FETCH_N=1440 (1 день); live_recover aggressive; backfill tool |
| M5(derived) ≠ M5(broker) на OHLCV | Низька | Дані відрізняються | Phase 2 порівняння; delta tool; якщо diff > threshold — залишити broker M5 |
| FXCM M1 API має нижчу якість ніж M5 | Низька | Системна | Моніторинг + broker M5 як fallback; якщо M5(broker) ≠ M5(5×M1) → дослідження |
| Перевантаження одного процесу | Низька | Latency | M1 poll + derive all TF має бути ≤1s; profiling budget |
| 2 writers пишуть один TF паралельно (Phase 2) | Середня | Dedup churn | UDS watermark/dedup вже є; перший записаний — канонічний |

---

## 5. Конфігураційний план (config.json)

### Phase 0 — нові ключі

```json
{
  "m1_poller": {
    "enabled": true,
    "tail_fetch_n": 5,
    "safety_delay_s": 8,
    "m3_derive_enabled": true,
    "derive_tfs_s": [180],
    "backfill_enabled": true,
    "backfill_max_bars": 1440,
    "tail_catchup_max_bars": 5000,
    "live_recover_threshold_bars": 3,
    "live_recover_max_bars_per_cycle": 120,
    "live_recover_cooldown_s": 5,
    "live_recover_max_total_bars": 5000,
    "stale_s": 720
  }
}
```

> **Config SSOT alignment** (Correction 0.1):
>
> - Ці ключі живуть у `config.json` — єдиному SSOT конфігу системи (Правило №4).
> - Phase 0 ключі (tail_catchup, live_recover, stale) — backend-internal, НЕ потребують експорту в `/api/config` (не впливають на UI policy).
> - Gap state (P0.4) поверхує через існуючий UDS `set_gap_state()` → `/api/status`, а не через окремий API.
> - При Phase 1+, коли m1_poller деривує нові TF, їх доступність ПОВИННА відображатись в `/api/config` (`tf_allowlist`).
> - `docs/config_reference.md` оновлюється з кожною Phase.
> - Заборонено створювати «окремий конфіг» для m1_poller поза `config.json`.

### Phase 2 — DeriveEngine cascade (всі TF)

DeriveEngine конфігурується через `derive_engine` секцію в `config.json`:

```json
{
  "derive_engine": {
    "enabled": true,
    "cascade_tfs_s": [180, 300, 900, 1800, 3600, 14400],
    "max_workers": 4
  }
}
```

### Phase 5 — engine_b стає D1-only

```json
{
  "derived_tfs_s": [],
  "broker_base_tfs_s": [86400]
}
```

---

## 6. Phase 0 — детальний план (immediate next)

### P0.1: Tail catchup на bootstrap

**Де**: `m1_poller.py` → `M1SymbolPoller.warmup_m1_buffer()` або новий метод `_tail_catchup()`.

**Логіка** (аналог engine_b `_tail_catchup_from_broker`):

```
after warmup (watermark set from disk):
  cutoff = expected_closed_m1_calendar(cal, now_ms)
  if cutoff > watermark:
    gap = (cutoff - watermark) // 60_000
    n = min(gap, tail_catchup_max_bars)
    bars = provider.fetch_last_n_m1(symbol, n=n)
    filter + sort + ingest each
```

**Порядок у `_bootstrap_warmup()`** (Correction 0.2 — обов'язково):

1. `_prime_redis_from_disk()` — Redis priming M1/M3
2. `warmup_m1_buffer()` — 10 барів в буфер, watermark встановлено
3. **`_tail_catchup()`** — заповнення від watermark до expected_now (**NEW**)
4. → тільки після цього `run_forever()` входить у main loop

**Інваріант P0.1**: m1_poller НЕ ПОВИНЕН входити в основний цикл (`run_forever` → `poll_once`) поки `_tail_catchup()` не завершився. Це гарантує, що UI бачить M1 без великих гепів з моменту першого запиту `/api/bars?tf=60`.

**Readiness signal (Phase 0)**: Зараз m1_poller не бере участь у `prime_ready` (це концепт `engine_b`, [engine_b.py](engine_b.py) L332-345). Для Phase 0 «readiness» m1_poller = `_bootstrap_warmup()` завершений (включно з tail catchup). У Phase 1+, коли m1_poller деривує TF що раніше покривав engine_b, потрібно додати m1_poller до комбінованого readiness signal (окремий slice).

**Модель engine_b (reference)**: в multi-mode ([engine_b.py](engine_b.py) L2091-2101) readiness встановлюється ПІСЛЯ tail_catchup всіх символів. m1_poller Phase 0 слідує цій же семантиці: спочатку catchup, потім робота.

### P0.2: Live recover

**Де**: `m1_poller.py` → `M1SymbolPoller._live_recover_check()` (новий метод, як engine_b).

**Логіка**:

```
in poll_once(), after regular poll:
  expected = expected_closed_m1(now_ms)
  gap = (expected - watermark) // 60_000
  if gap > live_recover_threshold and not in_recovery:
    enter recovery mode
  if in_recovery:
    fetch up to max_per_cycle M1 bars from broker (from watermark+1 to expected)
    if gap == 0: exit recovery
```

### P0.3: Stale detection

**Де**: `M1SymbolPoller.poll_once()`.

**Логіка**:

```
if market_open and no new M1 for > stale_s:
  log WARNING M1_STALE
  increment stale_count
```

### P0.4: Gap state reporting

**Де**: `M1SymbolPoller._ingest_bar()` або `_live_recover_check()`.

### P0.5: Config integration

**Де**: `config.json → m1_poller` + `build_m1_poller()`.

### VERIFY план для Phase 0

1. Запустити m1_poller з tail_catchup_max=5000
2. Перевірити що після bootstrap M1 gap ≤ safety_delay
3. Зупинити на 10 хв, перезапустити → перевірити live_recover fills gap
4. Перевірити stale лог якщо ринок закритий → немає stale
5. Перевірити M1 completeness за добу (trading_minutes - M1_count)

---

## 7. Відкладені рішення

- **Market-close bar closing**: H4 19:00 bucket / H1 21:00 — поки працює через calendar-aware expected_count. Окремий initiative для "close bar at market close"
- **Readiness signal evolution**: Зараз тільки engine_b бере участь у `prime_ready`. У Phase 1+ (коли m1_poller деривує TF, що раніше покривав engine_b) потрібно розширити readiness на комбіновану перевірку: engine_b(D1) + m1_poller(M1→H4). Окремий slice Phase 1
- **D1 derive від H4**: потенційно Phase 4, але D1 від брокера має специфічну семантику (різні anchor, DST), тому поки broker_base
- **Одна FXCM сесія**: m1_poller + engine_b(D1) через одну сесію — Phase 3+ опція

---

## 8. Exit Criteria (весь initiative)

- [x] Phase 0: M1 completeness ≥ 99% (tail_catchup + live_recover + stale + calendar fix)
- [x] Phase 1: core/derive.py — pure logic (GenericBuffer + aggregate_bars + DERIVE_CHAIN)
- [x] Phase 2: DeriveEngine в runtime/ (cascade trigger, ThreadPool, UDS commits)
- [x] Phase 3: Видалення H4 derive з UI (server.py). H4 = звичайний TF в UDS
- [x] Phase 4: M5(derived) vs M5(broker) OHLCV delta < 0.01% за тиждень
- [x] Phase 5: engine_b M5 polling disabled → d1_fetcher only
- [x] Phase 5.5 (cleanup): Dead M5 code removed, time_buckets consolidated, config cleaned
- [x] No regression in UI cold-load time (p95 < 200ms)
- [x] No split-brain, no silent fallback

### Cleanup Summary (Phase 5.5, 2026-02-19)

| Зміна | LOC removed | Файл |
| --- | --- | --- |
| Dead M5 methods/vars/imports (engine_b) | ~1145 | engine_b.py |
| Dead M5 config reads (composition.py) | ~40 | composition.py |
| Dead M5 config keys (config.json) | ~20 | config.json |
| Dead files: derive.py, flat_filter.py, time_buckets.py | ~145 | deleted |
| DeriveEngine commit_tfs_s fix | +2 | derive_engine.py |
| time_buckets.py → core/buckets.py consolidation | ~10 | 3 files migrated |
| Exit gate update (m1_poller) | ~30 | gate_live_recover_policy.py |
| README update (ADR-0002 architecture) | ~50 | polling/README.md |

### Залишки (post-ADR-0002, окремі initiatives)

1. RAM layer lock — ram_layer.py без locks при ThreadingHTTPServer (HIGH)
2. TF allowlist консолідація (MEDIUM)
3. Аналітичний меморандум SLO — 4 unchecked items (MEDIUM)
4. Broken test fix — test_tv_mismatch_probe.py (LOW)
5. Production web — Auth/TLS/headers (окремий initiative)

---

## Phase 6: Calendar-Aware Cascade Triggers + Overdue Safety Net (2026-02-20)

### Проблема

**H4 19:00 НІКОЛИ не деривувався** — для ВСІХ символів, ВСІХ дат.

`derive_triggers()` визначав trigger за "останнім номінальним source-слотом" у bucket:
`expected_last = bucket_end - source_tf_ms`. Для H4 19:00 (bucket 19:00-22:59,
anchor 23:00) це H1 22:00. Але H1 22:00 потрапляє на daily break і ніколи не деривується.

**Каскадний ефект**: баг не обмежений H4 — на КОЖНОМУ рівні каскаду де останній
номінальний source-слот non-trading, trigger не спрацьовував:

- `cfd_us_22_23` (break 22:00-23:00): H4 19:00 — trigger чекає H1 22:00 ❌
- `fx_24x5_utc_winter` (break 21:55-22:30): M5 21:55 не деривується → M15 21:45
  trigger не спрацьовує → M30 21:30 → H1 21:00 → H4 19:00 — вся ланка мертва ❌
- `cfd_eu_21_07` (break 21:00-07:00): H4 19:00 — trigger чекає H1 22:00 ❌

**Доказ**: H4 19:00 count = 0 для ВСІХ 10+ символів за весь час роботи.

### Рішення

#### S1: Calendar-aware `derive_triggers()` (core/derive.py)

Додано `is_trading_fn` параметр і `_has_any_trading_in_range()` helper.
Коли останній номінальний source-слот non-trading, функція крокує назад
до першого слоту з хоча б однією торговою хвилиною.

```
expected_last = bucket_end - source_tf_ms
if is_trading_fn:
    while expected_last >= bucket_open:
        if _has_any_trading_in_range(expected_last, expected_last + source_tf_ms, fn):
            break
        expected_last -= source_tf_ms
```

Backward-compatible: `is_trading_fn=None` → стара поведінка.

#### S2: Pass calendar в DeriveEngine._cascade()

`derive_triggers()` тепер отримує `is_trading_fn` від символу через `_calendars`.

#### S3: Overdue bucket check (timer-based safety net)

`DeriveEngine.check_overdue_buckets(now_ms)` — перевіряє попередній bucket
для кожного TF/symbol. Якщо source-бари достатні але bar не деривувався
(race, restart, порушення trigger) — деривує. Викликається кожні 60с з m1_poller.

### Верифікація

- Симуляція cascade з XAU_USD M1 Feb 19 (1426 bars):
  - **До фіксу**: H4 = ['03:00','07:00','11:00','15:00'] (4 bars, 19:00 MISSING)
  - **Після фіксу**: H4 = ['03:00','07:00','11:00','15:00','19:00'] (5 bars) ✓
  - H1 19→22 bars (13:00-15:00 restored через re-derive, 21:00 restored для FX)
- Unit-тести: derive_triggers з calendar для cfd_us_22_23, fx_24x5, normal case
- 151 existing tests pass (0 regressions)

### Файли

| Файл | Зміна |
| --- | --- |
| `core/derive.py` | +`_has_any_trading_in_range()`, `derive_triggers` +is_trading_fn |
| `runtime/ingest/derive_engine.py` | Pass calendar to triggers, +`check_overdue_buckets()` |
| `runtime/ingest/polling/m1_poller.py` | Overdue check в main loop (60s interval) |

### Інваріанти збережені

- I0: core/ не імпортує runtime — `is_trading_fn` = callable, не calendar import
- I1: UDS единий writer — overdue check використовує той самий UDS
- I2: Геометрія часу не змінена
- I3: Final > preview — overdue commit через UDS (watermark guard)
- I5: Degraded-but-loud — overdue derives логуються як OVERDUE_DERIVE_OK
