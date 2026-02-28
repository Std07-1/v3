# ADR-0023: D1 Live Derive from M1

- **Статус**: Implemented
- **Дата**: 2026-02-27
- **Автор**: user + AI agent
- **Initiative**: `d1_derive_from_m1`
- **Навігація**: [docs/adr/index.md](index.md)
- **Пов'язані ADR**: [ADR-0002](0002-derive-chain-from-m1.md) (DeriveChain M1→H4), [ADR-0005](0005-mid-session-gap-tolerance.md) (Mid-session Gap Tolerance)

---

## 0. Контекст і проблема

### AS-IS

D1 (86400) — єдиний TF, який не входить у DERIVE_CHAIN. Він фетчиться напряму з FXCM History API (engine_b, `broker_base_tfs_s: [86400]`). ADR-0002 завершив каскад M1→H4 і залишив D1 як виняток:

> *"FXCM History D1 → d1_fetcher → UDS (D1) (keep as-is)"* — ADR-0002 §1.4

Проблеми:

1. **Два джерела D1**: engine_b дає broker D1, а rebuild tool будує D1 з M1 — різні anchor можуть давати різний OHLCV.
2. **Залежність від FXCM D1 API**: окрема FXCM сесія (engine_b) тільки для D1 — надлишок ресурсів.
3. **Anchor розбіжність**: `day_anchor_offset_s_d1` = 75600 (21:00 UTC) в live, а rebuild використовує 79200 (22:00 UTC) — потенційний split-brain.
4. **Неповний ланцюг**: M1→H4 derive chain охоплює всі TF крім D1, що ускладнює mental model.

### TO-BE

D1 стає derived TF: **1440 × M1** з anchor **79200s (22:00 UTC)**. Calendar-aware: d1 бар = від session open до session close. engine_b більше не фетчить D1.

```
AS-IS:
  FXCM History D1 → engine_b → UDS (D1)     [broker]
  m1_poller → DeriveEngine → M3→…→H4         [derive chain]

TO-BE:
  m1_poller → DeriveEngine → M3→…→H4 + D1    [derive chain, unified]
  engine_b → broker_base_tfs_s: []            [D1 fetch disabled]
```

---

## 1. Scope та обмеження

### Single-symbol mode (ізольоване тестування)

Перший rollout — **тільки XAU/USD** (`config.json → "symbols": ["XAU/USD"]"`). Supervisor, m1_poller, connector, tick_publisher, tick_preview, ws_server — всі вже підтримують single element (ітерують по `symbols`). Код міняти не потрібно, тільки config.

### Non-goals

- Інші символи: поки single-symbol, roll-out після успішної верифікації.
- Зміна anchor H4 (82800 = 23:00 UTC): залишаємо як є.
- Зміна preview_tick_tfs_s: D1 preview через tick relay (`d1_live_tick_relay`), не tick_preview_worker.
- Зміна UI rendering: D1 вже показує тільки дату через `D1_OFFSET_MS` в engine.ts.
- Старі D1 бари (pre-2026): лишаються on disk, не перебудовуємо.

---

## 2. Розглянуті варіанти

### Варіант A: D1 from M1 derive chain (ОБРАНО)

D1 = 1440 × M1 в DERIVE_CHAIN. DeriveEngine будує D1 так само як H4, але з окремим anchor (79200 vs 82800).

**Плюси**:

- Єдине джерело: всі TF (M3→D1) будуються від M1.
- Consistency: live D1 ≡ rebuild D1 (однаковий anchor + алгоритм).
- Спрощення: engine_b може бути вимкнено повністю.
- Calendar-aware: DeriveEngine вже обробляє daily breaks.

**Мінуси**:

- M1 буфер повинен утримувати ~1440 барів (вже покрито: `_BUFFER_MAX_KEEP[60]=2000`).
- D1 anchor ≠ H4 anchor → потрібен окремий параметр в derive_triggers/derive_bar/DeriveEngine.

### Варіант B: Залишити D1 from broker

Нічого не міняти, engine_b продовжує фетчити D1.

**Плюси**: Zero risk.
**Мінуси**: Anchor split-brain, зайва FXCM сесія, неповний derive chain.

### Варіант C: D1 = 6 × H4

D1 = aggregate(6 × H4 з anchor 82800). Мінімальні зміни в chain.

**Плюси**: Простіше aggregate (6 барів замість 1440).
**Мінуси**: H4 anchor (82800) ≠ D1 anchor (79200) → bucket misalignment; D1 не ділиться на 6 H4 з різними anchors; складніший alignment ніж M1→D1.

---

## 3. Рішення: D1 from M1 derive (Варіант A)

### 3.1 Config changes

| Файл | Ключ | AS-IS | TO-BE | Навіщо |
|------|------|-------|-------|--------|
| `config.json` | `symbols` | 13 елементів | `["XAU/USD"]` | Single-symbol mode |
| `config.json` | `day_anchor_offset_s_d1` | 75600 (21:00) | 79200 (22:00) | Alignment live = rebuild |
| `config.json` | `broker_base_tfs_s` | `[86400]` | `[]` | engine_b не фетчить D1 |

### 3.2 core/derive.py changes

#### DERIVE_CHAIN: додати D1

```python
# AS-IS
DERIVE_CHAIN = {
    60:   [(180, 3), (300, 5)],
    300:  [(900, 3)],
    ...
    3600: [(14400, 4)],
}

# TO-BE
DERIVE_CHAIN = {
    60:   [(180, 3), (300, 5), (86400, 1440)],  # + D1 = 1440 × M1
    300:  [(900, 3)],
    ...
    3600: [(14400, 4)],
}
```

#### DERIVE_ORDER: додати 86400

```python
# AS-IS
DERIVE_ORDER = [180, 300, 900, 1800, 3600, 14400]

# TO-BE
DERIVE_ORDER = [180, 300, 900, 1800, 3600, 14400, 86400]
```

Наслідки (автоматичні):

- `DERIVE_SOURCE[86400] = (60, 1440)` — auto-generated.
- `DEFAULT_COMMIT_TFS_S = set(DERIVE_ORDER)` → включає 86400 — auto.

#### derive_triggers(): anchor split для D1

AS-IS (`derive.py:509`):

```python
anchor_offset_ms = anchor_offset_s * 1000 if target_tf_s >= 14400 else 0
```

Це дає **однаковий anchor** (82800) для H4 і D1 — **неправильно** (D1 anchor = 79200).

TO-BE: додати параметр `d1_anchor_offset_s: int = 0`:

```python
def derive_triggers(
    source_bar: CandleBar,
    anchor_offset_s: int = 0,
    d1_anchor_offset_s: int = 0,         # NEW
    is_trading_fn=None,
) -> List[Tuple[int, int]]:
    ...
    for target_tf_s, _ in targets:
        if target_tf_s == 86400:
            anchor_offset_ms = d1_anchor_offset_s * 1000
        elif target_tf_s >= 14400:
            anchor_offset_ms = anchor_offset_s * 1000
        else:
            anchor_offset_ms = 0
```

#### derive_bar(): anchor resolution

derive_bar() отримує `anchor_offset_s` для `assert_invariants`. Caller (DeriveEngine) вже вирішує який anchor передати per target_tf_s — змін у derive_bar() **не потрібно** (caller responsibility).

### 3.3 runtime/ingest/derive_engine.py changes

#### DeriveEngine.**init**(): додати d1_anchor_offset_s

```python
def __init__(
    self,
    symbols,
    anchor_offset_s: int = 0,
    d1_anchor_offset_s: int = 0,      # NEW: D1 anchor (79200)
    calendars=None,
    ...
):
    self._anchor_offset_s = anchor_offset_s
    self._d1_anchor_offset_s = d1_anchor_offset_s   # NEW
```

#### _cascade(): anchor per target_tf_s

```python
# AS-IS
anchor = self._anchor_offset_s if target_tf_s >= 14400 else 0

# TO-BE
if target_tf_s == 86400:
    anchor = self._d1_anchor_offset_s
elif target_tf_s >= 14400:
    anchor = self._anchor_offset_s
else:
    anchor = 0
```

Аналогічно для:

- `derive_triggers()` виклику в `_cascade()` — передати `d1_anchor_offset_s=self._d1_anchor_offset_s`
- `_check_overdue_for_symbol()` — аналогічна anchor resolution

#### _OVERDUE_LOOKBACK: додати D1

```python
_OVERDUE_LOOKBACK = {
    ...
    14400: 3,
    86400: 1,    # NEW: D1 overdue lookback = 1 (лише попередній D1 bucket)
}
```

### 3.4 m1_poller build_m1_poller(): передати d1_anchor

```python
# AS-IS
derive_engine = DeriveEngine(
    symbols=symbols,
    anchor_offset_s=anchor_offset_s,
    calendars=calendars_for_engine,
)

# TO-BE
d1_anchor = int(cfg.get("day_anchor_offset_s_d1", 0))
derive_engine = DeriveEngine(
    symbols=symbols,
    anchor_offset_s=anchor_offset_s,
    d1_anchor_offset_s=d1_anchor,
    calendars=calendars_for_engine,
)
```

### 3.5 _PRIME_TFS: додати 86400

```python
# AS-IS (m1_poller.py:1169)
_PRIME_TFS = (60, 180, 300, 900, 1800, 3600, 14400)

# TO-BE
_PRIME_TFS = (60, 180, 300, 900, 1800, 3600, 14400, 86400)
```

### 3.6 engine_b: broker_base_tfs_s = []

Config-only зміна (`broker_base_tfs_s: []`). Код engine_b ітерує по `self._broker_base_tfs_s` — порожній список = нуль ітерацій. Cold start D1 з broker також не відбувається (порожній список). D1 при старті завантажується з диску (вже правильні бари після rebuild).

---

## 4. Інваріанти

| ID | Зберігається? | Коментар |
|----|---------------|----------|
| I0 | ✅ | pure logic залишається в core/derive.py |
| I1 | ✅ | D1 writes через UDS.commit_final_bar(src="derived") |
| I2 | ✅ | D1 геометрія: end-excl (CandleBar), end-incl (Redis) — конвертація на межі |
| I3 | ✅ | D1 tick relay (preview, complete=false) vs DeriveEngine (final, complete=true) — final wins |
| I4 | ✅ | UI read-only, /api/updates |
| I5 | ✅ | Boundary partial → degraded-but-loud (extensions.partial=true) |
| I6 | ✅ | Disk = bootstrap/warmup only |

---

## 5. Числа та бюджети

| Параметр | Значення | Обґрунтування |
|----------|----------|---------------|
| D1 source | M1 (60s) | DERIVE_CHAIN[60] += (86400, 1440) |
| D1 anchor | 79200s (22:00 UTC) | Відповідає rebuild, XAU/USD daily break 22:00-23:00 |
| H4 anchor | 82800s (23:00 UTC) | Без змін |
| M1 buffer | 2000 bars | Покриває 1440 з запасом (~33h) |
| D1 overdue lookback | 1 bucket | Достатньо для 1 D1 бакет |
| MAX_MID_SESSION_GAPS | 3 | Calendar breaks фільтруються is_trading_fn; 3 unexpected gaps = достатній budget для D1 |
| _PRIME_TFS | +86400 | Redis priming D1 |
| Tick relay D1 | Без змін | d1_live_tick_relay вже працює для preview |

---

## 6. Ризики та мітігації

| Ризик | Ймовірність | Вплив | Мітігація |
|-------|-------------|-------|-----------|
| M1 gap > MAX_MID_SESSION_GAPS → D1 не деривується | Низька (live_recover active) | D1 пропуск | Overdue check (60s) + live_recover fills gaps |
| D1 anchor mismatch з TradingView | Низька | OHLCV diff | 79200 (22:00 UTC) вже підтверджено rebuild vs TV |
| engine_b disabled → D1 cold start порожній | Середня | UI показує менше D1 | D1 on disk (rebuild дані) + m1_poller cascade catchup |
| D1 derive latency (1440 bars aggregate) | Дуже низька | aggregate_bars ~1ms | Pure Python aggregate — мінімальна вартість |
| Regression M3→H4 | Низька | Каскад зламано | Тести + verify щоб anchor split не порушив H4 |

---

## 7. Verification plan

### Pre-deploy

1. `python -m pytest tests/ -v` — регресія (M3-H4 не зламано).
2. Новий тест: D1 derive з ~1440 M1 барів, anchor 79200, calendar cfd_us_22_23.

### Post-deploy

3. Запуск платформи з single symbol (XAU/USD).
4. Чекати daily close (~22:00 UTC) → перевірити що D1 бар коммітиться:

   ```
   curl http://127.0.0.1:8089/api/bars?symbol=XAU/USD&tf_s=86400&limit=5
   ```

   Expected: `src=derived`, anchor 79200-aligned.
5. Cross-validation: порівняти live D1 з rebuild даними (`data_v3/XAU_USD/tf_86400/`).
6. UI: D1 chart → тільки дата, без часу, вертикальна лінія = daily close.

### Rollback verification

7. Відкат: `broker_base_tfs_s: [86400]`, видалити D1 з DERIVE_CHAIN / DERIVE_ORDER.
8. `day_anchor_offset_s_d1: 75600` (або залишити 79200 якщо дані вже перебудовані).

---

## 8. P-slices (кожен ≤150 LOC)

### P1: Config changes (~5 lines JSON)

- `symbols: ["XAU/USD"]`
- `day_anchor_offset_s_d1: 79200`
- `broker_base_tfs_s: []`

### P2: core/derive.py (~15 lines)

- DERIVE_CHAIN[60] += (86400, 1440)
- DERIVE_ORDER += 86400
- derive_triggers() + d1_anchor_offset_s parameter

### P3: runtime/ingest/derive_engine.py (~30 lines)

- DeriveEngine.**init**() + d1_anchor_offset_s
- _cascade() anchor resolution
- _check_overdue_for_symbol() anchor resolution
- _OVERDUE_LOOKBACK[86400] = 1
- derive_triggers() call + d1_anchor_offset_s

### P4: m1_poller build_m1_poller() (~5 lines)

- Read d1_anchor from config
- Pass to DeriveEngine
- _PRIME_TFS += 86400

### P5: Tests (~50 lines)

- Новий тест D1 derive від 1440 M1 барів

---

## 9. Rollback

1. `config.json`: `broker_base_tfs_s: [86400]`, `symbols: [13 елементів]`, `day_anchor_offset_s_d1: 75600`
2. `core/derive.py`: видалити (86400, 1440) з DERIVE_CHAIN[60], видалити 86400 з DERIVE_ORDER, видалити d1_anchor_offset_s з derive_triggers()
3. `runtime/ingest/derive_engine.py`: видалити d1_anchor_offset_s, відкатити anchor resolution, видалити_OVERDUE_LOOKBACK[86400]
4. `m1_poller.py`: видалити d1_anchor pass-through,_PRIME_TFS без 86400
5. Видалити новий тест

---

## 10. Відкладені рішення

- **Multi-symbol rollout**: після верифікації XAU/USD → повернути всі 13 символів. Потрібно адаптувати D1 anchor per calendar group (різні символи можуть мати різний D1 anchor).
- **engine_b decommission**: з `broker_base_tfs_s: []` engine_b стає no-op для D1. Потенційно його можна вимкнути повністю або прибрати (окремий initiative).
- **D1 overdue lookback tuning**: якщо 1 недостатньо (weekend edge case), збільшити.
- **D1 for non-22:00 anchors**: символи з іншим daily break (GER30 21:00, HKG33 19:00) потребують per-symbol D1 anchor. Поточна архітектура — global d1_anchor_offset_s. Multi-symbol phase потребує per-group anchor mapping.

## Bug-Hunter Аналіз ADR-0023: D1 Live Derive from M1

## VERDICT: УМОВНО (з 2 блокерами, 4 значними)

S0 блокерів:  0
S1 критичних: 2
S2 значних:   4
S3 косметичних: 3

## Scorecard

Критерій	Оцінка	Коментар
Correctness	3	Correct happy-path, але anchor resolution має 4+ mutation sites — один пропуск = assert_invariants crash
SSOT	2	Anchor визначається в 2+ місцях: resolve_anchor_offset_ms (buckets.py) + inline if tf_s >= 14400 (derive_engine.py). Config має _alt ключі.
Ordering	3	Watermark + dedup через UDS. D1 overdue retry без dedup-буфера — шумно але безпечно
Concurrency	3	Per-symbol lock. D1 aggregate 1440 барів під lock — тримає lock ~2-5ms. Прийнятно для 60s poll cycle
Error semantics	2	DERIVE_SKIP logging фільтр ігнорує D1. Overdue retry без target-буфера → тихий шум
Observability	2	D1 derive failure непомітне (без логу). Overdue D1 retry нема метрики відфільтрування
Performance	4	1440 iterations при derive = ~1-2ms. Прийнятно
Contracts	3	CandleBar + assert_invariants. Anchor передається вручну, не через contract
SoC	3	Pure derive в core/, I/O в runtime/. Anchor routing дублюється між шарами
UX truth	2	Зараз D1 tick relay використовує anchor 75600, дані на диску — 79200. UI бреше

## Defect Ledger

D-01: Існуючий split-brain D1 anchor: config=75600, data=79200
Severity: S1
Class: C1 (SSOT роз'їзд)
MTBF: daily (кожен день о 21:00-22:00 UTC)

Симптом: D1 tick relay у WS server будує forming candle з anchor 75600 (21:00 UTC). Дані на диску вирівняні до 79200 (22:00 UTC). Між 21:00-22:00 UTC tick relay показує бар для іншого D1 bucket ніж той, що на диску → UI D1 bar стрибає на 1 годину при кожному UDS update.

Причина: config.json:161] day_anchor_offset_s_d1: 75600, [VERIFIED data] tf_86400 — open_time=22:00 UTC (anchor 79200). buckets.py:24] resolve_anchor_offset_ms читає day_anchor_offset_s_d1. ws_server.py:601] WS tick relay використовує resolve_anchor_offset_ms.

Доказ: Запущена перевірка в терміналі:

D1 bucket (anchor 75600/21:00): 2025-02-25 21:00:00+00:00D1 bucket (anchor 79200/22:00): 2025-02-25 22:00:00+00:00SPLIT-BRAIN during 21:00-22:00? True
Відтворення:

Запустити WS server з поточним config (day_anchor_offset_s_d1: 75600)
Підписатись на D1 XAU/USD через WS
Спостерігати tick relay forming bar o 21:30 UTC → open_ms computed з anchor 75600
UDS повертає D1 бар з open_time_ms вирівняний до 79200
Очікується: однаковий bucket. Фактично: різні bucket (1-hour зсув)
Фікс-мінімум: config.json → "day_anchor_offset_s_d1": 79200 (P1 з ADR-0023). ADR вже це покриває, але це не "майбутня зміна" — це існуючий баг прямо зараз.

Рейка: Exit-gate що перевіряє (latest_d1_bar.open_time_ms - config.d1_anchor * 1000) % 86400000 == 0

Метрика: d1_anchor_mismatch_count — лічильник випадків де bucket tick relay ≠ bucket UDS

D-02: DeriveEngine anchor resolution — 4 місця з >= 14400 hardcode, ADR не перелічує всі
Severity: S1
Class: C1 (SSOT роз'їзд) + C2 (відсутній інваріант)
MTBF: кожен D1 trigger (daily) → assert_invariants crash

Симптом: При запуску ADR-0023 коду, перший D1 derive trigger → aggregate_bars() → assert_invariants() → ValueError: bar_bucket_misaligned → D1 бар ніколи не коммітиться.

Причина: Код має 5 місць з паттерном anchor = self._anchor_offset_s if target_tf_s >= 14400 else 0. ADR перелічує зміни для_cascade, _check_overdue_for_symbol, derive_triggers — але не верифікує повний список:

#	Де	Рядок	Потрібна зміна

1	_cascade() → derive_triggers() виклик	derive_engine.py:361	✅ ADR §3.3 покриває
2_cascade() → derive_bar() виклик	derive_engine.py:383	✅ ADR §3.3 покриває
3	_check_overdue_for_symbol() → anchor_ms	derive_engine.py:274	✅ ADR §3.3 покриває
4	_check_overdue_for_symbol() → anchor	derive_engine.py:277	✅ ADR §3.3 покриває
5_check_overdue_for_symbol() → derive_bar(..., anchor_offset_s=anchor)	derive_engine.py:298	⚠️ Implicitly covered via #4
Доказ: Запущено в терміналі:

bucket_open_d1 = ((ts - 79200*1000) // 86400000) * 86400000 + 79200*1000check = (bucket_open_d1 - 82800*1000) % 86400000  # = 82800000 ≠ 0 → FAIL
Ризик: ADR покриває зміни текстуально, але не має guard/rail проти регресії. Якщо хтось додасть ще один anchor-site з >= 14400 → D1 зламається.

Фікс-мінімум: Не просто if/elif per site — а один метод _resolve_anchor(target_tf_s) -> int в DeriveEngine. Один SSOT для anchor routing.

Рейка: Тест що явно перевіряє assert_invariants для D1 (86400) з anchor 79200 і verifies fail з 82800.

Метрика: Лічильник derive_assert_error_total{tf=86400}

D-03: Overdue check для D1 — безкінечний retry без target buffer
Severity: S2
Class: C6 (hot-path inefficiency) + C7 (брехлива спостережуваність)
MTBF: після кожного successful D1 derive (daily)

Симптом: Після коміту D1 бару, _check_overdue_for_symbol кожні 60 секунд пробує деривувати вчорашній D1 знову. target_buf для D1 = None (86400 не в DERIVE_CHAIN keys → буфер не створюється). Звірка if target_buf is not None and prev_bucket in target_buf: continue — пропускається. derive_bar знову агрегує 1440 M1 → UDS відхиляє як stale/dup. Кожні 60 секунд, 24 години на добу.

Причина: derive_engine.py:289] target_buf = self._buffers.get((symbol, target_tf_s)) — для D1 = None. derive_engine.py:55] _BUFFER_MAX_KEEP не має 86400. ADR §3.3 додає_OVERDUE_LOOKBACK[86400]=1 але не враховує відсутність target buffer.

Вплив: ~1440 dict lookups × 1 call/60s = ~24 зайвих aggregate_bars/хвилину. UDS commit_drop_counts шумить stale=1 кожні 30 секунд. Не critical, але засмічує логи і витрачає CPU.

Фікс-мінімум (≤10 LOC):

#  derive_engine.py:_BUFFER_MAX_KEEP_BUFFER_MAX_KEEP[86400] = 5  # Track last 5 D1 bars for overdue dedup

І в_cascade step 1 — перевірити що D1-як-target теж буферизується (зараз тільки source TFs). Або: перевірити through UDS watermark замість buffer.

D-04: DERIVE_SKIP діагностичний лог ігнорує D1
Severity: S2
Class: C7 (спостережуваність)
MTBF: кожен D1 derive failure (potentially daily after restart)

Симптом: Коли D1 derive_bar повертає None (missing M1 bars після restart, gap тощо) — жодного логу. Інші TF (300, 900, 1800, 3600, 14400) мають діагностичний DERIVE_SKIP warning. D1 — мовчить.

Причина: derive_engine.py:396] if target_tf_s in (300, 900, 1800, 3600, 14400): — 86400 відсутній у tuple.

Фікс-мінімум (1 LOC): змінити на if target_tf_s in (300, 900, 1800, 3600, 14400, 86400): або if target_tf_s >= 300:.

Рейка: Static check / grep для hardcoded TF tuples.

D-05: Anchor resolution — SSOT violation (2 шляхи)
Severity: S2
Class: C1 (SSOT) + C9 (SoC)
MTBF: per-change (latent)

Симптом: Anchor для D1 вирішується двома незалежними механізмами:

core/buckets.py:resolve_anchor_offset_ms() — використовується WS server, rebuild tools
DeriveEngine._anchor_offset_s +_d1_anchor_offset_s (ADR-0023) — використовується derive cascade
Якщо конфіг day_anchor_offset_s_d1 зміниться, WS server підхопить автоматично, а DeriveEngine — тільки після рестарту (значення читається в build_m1_poller і зберігається як instance attribute).

Причина: buckets.py:24] resolve_anchor_offset_ms — централізована функція. derive_engine.py:93] self._anchor_offset_s = anchor_offset_s — snapshot значення при init.

Фікс: ADR §3.2 пропонує d1_anchor_offset_s параметр в derive_triggers. Альтернатива — DeriveEngine використовує resolve_anchor_offset_ms(target_tf_s, cfg) замість inline if/elif. Зменшує mutation sites до 1.

D-06: MAX_MID_SESSION_GAPS=3 може бути замало для D1
Severity: S2
Class: C2 (відсутній інваріант)
MTBF: weekly (XAU/USD при low liquidity)

Симптом: D1 bucket потребує ~1380 M1 барів. Якщо 4+ M1 бари пропущені mid-session (FXCM data outage, low liquidity), _collect_boundary_tolerant повертає None → D1 не деривується цілий день.

Причина: derive.py:55] MAX_MID_SESSION_GAPS: int = 3 — global для всіх TF. Для H4 (240 M1 bars) 3 gaps = 1.25% tolerance. Для D1 (1380 M1 bars) 3 gaps = 0.22% tolerance — в 5.7 разів суворіше.

Доказ: config.json:268] known_broker_outages містить кілька FXCM outages по 5-10 хвилин. Для NGAS маємо 3+ outages в один день. XAU/USD — стабільніше, але 4 пропущених M1 за 23 години — реалістично.

Фікс-мінімум: Або per-TF MAX_MID_SESSION_GAPS (D1: ~10), або пропорційний до bars_needed (наприклад max(3, bars_needed * 0.01)). Потребує ADR amendment.

D-07: bootstrap derive_warmup_bars_by_tf не включає достатньо M1 для D1
Severity: S3
Class: C5 (silent degradation)
MTBF: кожен restart

Симптом: Config bootstrap.derive_warmup_bars_by_tf.60: 300 (5 годин M1). D1 потребує ~1380 M1. Warmup заповнює лише 300 M1 → cascade_catchup потім додає 1440 M1. Cascade catchup компенсує — але warmup section в config вводить в оману (виглядає як достатній, але фактично D1 залежить від cascade_catchup).

Фікс-мінімум: Документаційна зміна в ADR або збільшити 60: 1500 в config. Функціонально потоку не зламає (cascade_catchup рятує), але mental model порушений.

D-08: derived_tfs_s config key — stale metadata
Severity: S3
Class: C1 (SSOT)
MTBF: latent

Симптом: config.json:53] "derived_tfs_s": [14400] — не включає 86400. Це використовується лише для documentation/metadata, не для runtime routing (DeriveEngine читає DERIVE_CHAIN). Але якщо хтось покладається на цей ключ — буде думати D1 = broker-only.

Фікс-мінімум: "derived_tfs_s": [14400, 86400] після імплементації ADR-0023.

D-09: Config keys _alt,_alt2 — мертвий код у SSOT
Severity: S3
Class: C1 (SSOT)
MTBF: latent (confusion risk)

Симптом: config.json:159] day_anchor_offset_s_alt: 79200, day_anchor_offset_s_alt2: 68400, day_anchor_offset_s_d1_alt: 79200 — ці ключі не використовуються в runtime (тільки в tools/repair). Після зміни primary D1 anchor на 79200, ключ_d1_alt: 79200 стає ідентичний primary → плутанина.

Фікс-мінімум: Після імплементації ADR-0023 — видалити або задокументувати _alt ключі.

## Top-5 підступних (неочевидних, з найбільшим blast radius)

1. assert_invariants crash при D1 derive (D-02)
Чому невидимо: Unit тести для існуючих TFs (M3-H4) проходять з anchor 82800. D1 з anchor 79200 — нова комбінація, яка ніде не тестувалася. Якщо реалізатор пропустить одне з 5 місць anchor resolution — crash прихований до першого D1 trigger (~22:00 UTC). Тестове середовище вдень не тригерить D1.
MTBF: Перший day-close після deploy.
Blast radius: D1 бар не коммітиться → D1 chart пустий → всі символи (якщо multi-symbol).

2. Overdue repeated D1 rederive (D-03)
Чому невидимо: UDS відхиляє dup/stale тихо (throttled log). Aggregate 1440 bars = 2ms CPU, не помітно на dashboards. Але: 1440 × is_trading_fn calls кожну хвилину per symbol × 13 символів (при multi-symbol) = 18720 × 1440 = **27M calls/day**. Зараз — 1 символ, ОК. При 13 — помітне навантаження.
MTBF: Continuous (after first successful D1).
Blast radius: CPU waste + UDS log spam.

3. D1 tick relay anchor split-brain (D-01)
Чому невидимо: UI показує "forming" D1 bar nonstop, перезаписується рідко (раз на день при commit). Різниця в bucket — видна лише в 1-годинному вікні 21:00-22:00 UTC. Трейдер не помітить, бо D1 показує тільки дату.
MTBF: Daily (21:00-22:00 UTC window).
Blast radius: Один символ (XAU/USD), D1 тільки.

4. MAX_MID_SESSION_GAPS=3 для D1 (D-06)
Чому невидимо: XAU/USD = ліквідний, gaps рідкісні. Перша "стрілка" — при FXCM multi-outage (3+ gaps за 23h). D1 бар не деривується → D1 chart gap → жодного алерту (D-04).
MTBF: Monthly при outages, weekly для неліквідних (NGAS).
Blast radius: Пропуск D1 бару на 1+ день.

5. Відсутність _resolve_anchor centralized method (D-05)
Чому невидимо: Зараз працює, бо тільки 2 anchors (H4=82800, D1=79200). При додаванні 3-го TF з anchor (наприклад Weekly), кожне if/elif в 5 місцях потребує правки. Один пропуск = silent wrong bucket без crash (якщо anchor кратний).
MTBF: Per-future-change (latent).
Blast radius: Весь cascade для нового TF.

## Kill Criteria (ADR-0023 НЕ МОЖНА імплементувати без цього)

D-01: Змінити day_anchor_offset_s_d1: 79200 ПЕРШИМ кроком (P1) — збігається з ADR plan. Але прибрати _alt ключі щоб не було зворотної плутанини.

D-02: Реалізувати _resolve_anchor(target_tf_s) -> int як єдиний SSOT в DeriveEngine, а не inline if/elif в 5 місцях. Одна точка правки. Тест з assert_invariants(d1_bar, anchor_offset_s=79200).

D-04: Додати 86400 в DERIVE_SKIP tuple. Без діагностики D1 — сліпа зона в production.

## Guardrails Map (рекомендовані рейки)

Де → Що перевіряє → Що при спрацюванні
───────────────────────────────────────────────────────────────
DeriveEngine._resolve_anchor()    → SSOT anchor routing         → єдине місце зміни при новому anchor
aggregate_bars()→assert_invariants → bucket alignment для D1     → ValueError + loud log
Exit-gate: D1 anchor consistency  → config anchor = data anchor  → FAIL gate (no deploy)
_check_overdue: target_buf check  → D1 buffer exists for dedup  → skip re-derive (save CPU)
DERIVE_SKIP tuple                 → includes 86400               → diagnostic log on D1 failure
config.json: derived_tfs_s        → includes 86400 post-deploy  → documentation truth
Test: D1 derive E2E               → 1440 M1 → 1 D1 bar         → regression prevention

## Рекомендації по ADR-0023 — що дожати

###	Дія	Пріоритет

R1	Додати §3.7 "Anchor resolution centralization" — _resolve_anchor(tf_s) метод	P2 (при реалізації)
R2	Додати_BUFFER_MAX_KEEP[86400] = 5 і відповідний upsert в _cascade для D1 target	P3
R3	Прописати DERIVE_SKIP tuple update → (300, 900, 1800, 3600, 14400, 86400)	P2
R4	Прописати derived_tfs_s: [14400, 86400] в config changes table	P1
R5	Додати note про MAX_MID_SESSION_GAPS for D1 — рекомендація per-TF або proportional budget	§10 (deferred)
R6	P1 має включити cleanup_alt ключів або додати коментар що вони для tools/repair only	P1
R7	Додати exit-gate D1 anchor alignment: config ↔ data ↔ resolve_anchor_offset_ms	P5 (tests)
Загальна оцінка ADR: Архітектурно — вірний підхід. Data lineage стає чистішим (єдине джерело M1→D1). Основний ризик — не в задумі, а в кількості mutation sites для anchor routing. ADR добре описує ЩО змінити, але не пропонує централізації anchor resolution — а це ключовий guardrail проти регресії. Без_resolve_anchor() — наступний розробник, який додасть Weekly TF, гарантовано забуде одне з 5 місць.
