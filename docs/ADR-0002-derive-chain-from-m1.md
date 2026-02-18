# ADR-0002: DeriveChain — каскадна деривація від M1

> **Дата**: 2026-02-18  
> **Статус**: DRAFT  
> **Initiative**: `derive_chain_m1`  
> **Навігація**: [docs/index.md](index.md)

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
|---|:---:|:---:|---|
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

### 1.4. Цільовий derivation chain (TO-BE)

```
FXCM History M1 → enhanced m1_poller → UDS (M1)
  └→ M3  derived (3×M1)
  └→ M5  derived (5×M1)      ← NEW
     └→ M15 derived (3×M5)   ← moved from engine_b
     └→ M30 derived (6×M5)   ← moved from engine_b
     └→ H1  derived (12×M5)  ← moved from engine_b
        └→ H4  derived (4×H1, calendar-aware, TV anchor)  ← moved from UI

FXCM History D1 → broker fetch → UDS (D1)  (keep as-is)
```

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
| P0.1 | **Tail catchup** на bootstrap | Після warmup: fetch M1 від watermark до expected (як engine_b `_tail_catchup_from_broker`) | Max 5000 bars |
| P0.2 | **Live recover** | Якщо gap > 3 M1: enter recovery mode, fetch з cooldown, до gap=0 (як engine_b `_live_recover_check`) | Threshold=3, max_per_cycle=120, cooldown=5s |
| P0.3 | **Stale detection** | Якщо > 720s без нового M1 при відкритому ринку → loud warning + stale counter | m1_stale_s=720 |
| P0.4 | **Gap state reporting** | `uds.set_gap_state()` при великому gap | Як engine_b |
| P0.5 | **Config SSOT** | Нові ключі в `m1_poller: { live_recover_*, stale_s, tail_catchup_max }` | |

**Exit gate**: 7 днів роботи, M1 completeness ≥ 99% (рахуємо: trading_minutes - M1_on_disk) для XAU/USD.

**Rollback**: видалити нові методи, повернути простий warmup (10 барів).

#### Phase 1: DeriveChain framework + M5 від M1

**Ціль**: M5 derived від M1 (паралельно з broker M5).

**Зміни**:

| # | Де | Що |
|---|---|---|
| P1.1 | `core/derive_chain.py` (NEW) | `GenericBuffer(tf_s, max_keep)` — pure in-memory buffer з upsert/range/GC. Заміна M1Buffer/M5Buffer. Параметризований tf_s. |
| P1.2 | `core/derive_chain.py` | `derive_from_lower(symbol, target_tf_s, source_buf, anchor_offset_s, is_trading_fn)` — pure aggregation (OHLCV merge). |
| P1.3 | `runtime/ingest/polling/m1_poller.py` | Після commit M1 → derive M3 (як зараз) + **derive M5** (5×M1) → commit M5 derived. |
| P1.4 | `config.json` | `m1_poller.derive_tfs_s: [180, 300]` (M3 + M5). При включенні M5 derived — engine_b не полює M5. |

**Порівняння**: Phase 2 порівнює M5(broker) vs M5(derived від M1). Поки Phase 1 — M5 derived записується паралельно з `src=derived_m1` (або окремий TF-тег).

**Exit gate**: M5(derived) vs M5(broker) diff < 0.01% на OHLCV за тиждень.

**Rollback**: видалити M5 derive з m1_poller, повернути `m1_poller.derive_tfs_s: [180]`.

#### Phase 2: Cascade derive M15/M30/H1 + H4 в runtime

**Ціль**: повний ланцюг M1 → ... → H4 в m1_poller (паралельно з engine_b).

**Зміни**:

| # | Де | Що |
|---|---|---|
| P2.1 | `m1_poller.py` | Cascade trigger: commit M5(derived) → try_derive M15/M30/H1 (аналог engine_b `_try_derive_from_m5`) |
| P2.2 | `m1_poller.py` | Derive H4 від H1 (calendar-aware, TV anchor) — перенос з server.py |
| P2.3 | `config.json` | `m1_poller.derive_tfs_s: [180, 300, 900, 1800, 3600, 14400]` |

**Паралельна робота**: engine_b продовжує працювати. Обидва пишуть derived бари. UDS watermark/dedup запобігає конфліктам (той самий open_ms → перший записаний виграє).

**Exit gate**: порівняння M15/M30/H1/H4 від двох джерел за тиждень.

**Rollback**: видалити cascade derive, повернути derive_tfs_s: [180, 300].

#### Phase 3: Відключення engine_b M5+ polling

**Ціль**: engine_b більше не поллить M5. Залишає тільки D1 broker + Redis priming + backfill tools.

**Зміни**:

| # | Де | Що |
|---|---|---|
| P3.1 | `config.json` | `derived_tfs_s: []` (engine_b не деривує нічого) |
| P3.2 | `config.json` | engine_b стає "D1 poller + Redis primer" |
| P3.3 | Необов'язково | Перенести D1 broker fetch у m1_poller (одна FXCM сесія) |

**Exit gate**: тижневе порівняння — M1-ланцюг покриває все без engine_b M5 polling.

**Rollback**: повернути derived_tfs_s: [900, 1800, 3600] в engine_b config.

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

### Phase 1 — додаємо M5 derive

```json
{
  "m1_poller": {
    "derive_tfs_s": [180, 300]
  }
}
```

### Phase 2 — повний cascade

```json
{
  "m1_poller": {
    "derive_tfs_s": [180, 300, 900, 1800, 3600, 14400]
  }
}
```

### Phase 3 — engine_b стає D1-only

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
- **D1 derive від H4**: потенційно Phase 4, але D1 від брокера має специфічну семантику (різні anchor, DST), тому поки broker_base
- **Одна FXCM сесія**: m1_poller + engine_b(D1) через одну сесію — Phase 3+ опція

---

## 8. Exit Criteria (весь initiative)

- [ ] Phase 0: M1 completeness ≥ 99% за тиждень (XAU/USD + 2 інші символи)
- [ ] Phase 1: M5(derived) vs M5(broker) OHLCV delta < 0.01% за тиждень
- [ ] Phase 2: All derived TF (M15/M30/H1/H4) from chain match engine_b output
- [ ] Phase 3: engine_b M5 polling disabled, UI shows correct data
- [ ] No regression in UI cold-load time (p95 < 200ms)
- [ ] No split-brain, no silent fallback
