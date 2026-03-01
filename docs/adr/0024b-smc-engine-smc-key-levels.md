# ADR-0024b: SMC Key Levels — Horizontal Anchors System

- **Статус**: Partially Implemented (L1 key levels: D1/H4/H1/M30/M15 prev/curr H/L + cross-TF injection + per-kind UI styling)
- **Дата**: 2026-03-01
- **Дата часткової реалізації**: 2026-03-01
- **Автор**: AI Agent (SMC Chief Strategist)
- **Initiative**: Потік C — SMC Engine (ADR-0024 sub-document)
- **Parent**: ADR-0024 (SMC Engine Architecture)
- **Sibling**: ADR-0024a (SMC Hardening)

---

## 1. Контекст і проблема

### 1.1 Поточний стан

`core/smc/liquidity.py` детектує **тільки** Equal Highs/Lows (EQH/EQL) через ATR-clustering свінгів.
Це 1 з 6 категорій рівнів, які трейдер використовує для орієнтації, цілей та інвалідації.

**Відсутні 5 категорій:**

| Категорія | Рівні | Статус |
|-----------|-------|--------|
| A. HTF Reference | PDH, PDL, PWH, PWL | ❌ Відсутні |
| B. Dynamic Extremes | DH, DL, WH, WL | ❌ Відсутні |
| C. Sessions | AS_H, AS_L, LON_H, LON_L, NY_H, NY_L | ❌ Відсутні |
| D. Opens | DO, WO | ❌ Відсутні |
| E. Liquidity | EQH, EQL | ✅ `liquidity.py` |
| F. Range | RNG_H, RNG_L, EQ | ❌ Відсутні |

### 1.2 Чому це критично

Без PDH/PDL/PWH/PWL трейдер не має **якорів** — точок, відносно яких оцінюється позиція ціни. Без session H/L — не розуміє внутрішньоденний контекст. Без range levels — не бачить premium/discount без заливки пів-графіка.

**Ці рівні = мінімальний viable SMC overlay.** Зони (OB/FVG) без якорів — шум. Якорі без зон — вже корисні.

### 1.3 Architectural constraint

`core/smc/` = pure, NO I/O (I0, S0). Для обчислення PDH/PDL/sessions потрібні D1/W1/M1 бари з UDS. Тому:

- **Pure compute** → `core/smc/key_levels.py`
- **I/O fetch** → `runtime/smc/smc_runner.py`

---

## 2. Розглянуті варіанти

### Варіант A: Все в `core/smc/engine.py`

Розширити `_compute_snapshot()` — додати PDH/PDL/sessions як частину snapshot.

**Мінуси:**
- engine.py вже 524 LOC (God-module trajectory, F3 з ADR-0024a)
- PDH/PDL потребують D1 бари, engine працює з поточним TF — ламає абстракцію
- Session definitions потребують config parsing — ускладнює engine

**Відхилено.**

### Варіант B: Окремий `core/smc/key_levels.py` + wiring в SmcRunner

Pure functions приймають CandleBar lists, повертають SmcLevel list.
SmcRunner (runtime) робить I/O fetch D1/M1, викликає pure functions, мержить з engine levels.

**Плюси:**
- I0/S0 дотримано: core=pure, runtime=I/O
- Тестується без UDS/Redis
- engine.py не росте
- Окремий budget enforcement (6 категорій × max per category)

**Обрано.**

### Варіант C: Окремий runtime-сервіс KeyLevelsService

Окремий клас у `runtime/smc/` з власним lifecycle.

**Мінуси:**
- Зайва абстракція для ~120 LOC pure logic
- Потребує окремий wiring/startup

**Відхилено (P5 — мінімальна складність).**

---

## 3. Рішення

### 3.1 Архітектура

```
core/smc/key_levels.py          runtime/smc/smc_runner.py
┌─────────────────────┐         ┌──────────────────────────┐
│ compute_htf_levels() │◄────────┤ fetch D1 bars from UDS   │
│ compute_dynamic_hl() │◄────────┤ fetch M1 bars from UDS   │
│ compute_session_hl() │◄────────┤ current_time_ms           │
│ compute_opens()      │◄────────┤ period anchors from cfg   │
│ compute_range_levels()│◄───────┤ swings from SmcEngine     │
│ merge_and_budget()   │◄────────┤ budget from config        │
└─────────────────────┘         └──────────────────────────┘
                                          │
                                          ▼
                                SmcSnapshot.levels (merged)
                                          │
                                          ▼
                                   WS delta → UI
```

### 3.2 Категорії — детальна специфікація

#### A. HTF Reference (PDH/PDL/PWH/PWL)

```python
def compute_htf_levels(
    d1_bars: List[CandleBar],    # останні 2 completed D1
    w1_bars: List[CandleBar],    # останні 2 completed W1 (or derived from D1)
    symbol: str,
) -> List[SmcLevel]:
```

- **PDH** = previous completed D1 bar `.h`
- **PDL** = previous completed D1 bar `.low`
- **PWH** = previous completed W1 bar `.h`
- **PWL** = previous completed W1 bar `.low`
- D1 anchor = 79200s (22:00 UTC, ADR-0023)
- W1 = aggregate Mon 22:00 → Fri close. Pure: `_aggregate_d1_to_w1(d1_bars)`.

**Guard**: якщо `len(d1_bars) < 2` → empty list (no PDH/PDL). Не fake дані.

#### B. Dynamic Extremes (DH/DL/WH/WL)

```python
def compute_dynamic_levels(
    m1_bars: List[CandleBar],    # M1 бари поточного D1/W1 period
    d1_anchor_ms: int,           # start of current day
    w1_anchor_ms: int,           # start of current week
    symbol: str,
) -> List[SmcLevel]:
```

- **DH** = `max(bar.h for bar in today_m1_bars)`
- **DL** = `min(bar.low for bar in today_m1_bars)`
- **WH/WL** = same logic for current week window
- Refresh: per M1 bar (dynamic — змінюються протягом дня)

**Guard**: якщо no M1 bars in window → empty list.

#### C. Sessions (AS_H/AS_L/LON_H/LON_L/NY_H/NY_L)

```python
def compute_session_levels(
    m1_bars: List[CandleBar],
    session_defs: Dict[str, SessionDef],  # from config
    current_time_ms: int,
    symbol: str,
) -> List[SmcLevel]:
```

Session definitions (config.json):

```json
{
  "asia":   {"start_utc": "00:00", "end_utc": "09:00"},
  "london": {"start_utc": "07:00", "end_utc": "16:00"},
  "ny":     {"start_utc": "12:00", "end_utc": "21:00"}
}
```

- Для **активної** сесії: running H/L (оновлюється per M1)
- Для **минулої** сесії (найближча completed): final H/L
- Sessions overlap (London+NY 12:00-16:00) — обидві показуємо
- Budget: max 2 sessions × 2 levels = 4 levels

**Guard**: weekend → no active session → only previous session levels.

#### D. Opens (DO/WO)

```python
def compute_opens(
    m1_bars: List[CandleBar],
    d1_anchor_ms: int,
    w1_anchor_ms: int,
    symbol: str,
) -> List[SmcLevel]:
```

- **DO** = first M1 bar after d1_anchor → `.o`
- **WO** = first M1 bar after w1_anchor → `.o`
- Once set per period — not dynamic

**Guard**: якщо period just started and no M1 yet → empty.

#### E. Liquidity (EQH/EQL)

**Already implemented** in `core/smc/liquidity.py`. No changes needed.
Budget cap applied in `merge_and_budget()`.

#### F. Range / Dealing Range (RNG_H/RNG_L/EQ)

```python
def compute_range_levels(
    swings: List[SmcSwing],
    min_swing_count: int = 4,    # мінімум свінгів для valid range
    symbol: str = "",
    tf_s: int = 0,
) -> List[SmcLevel]:
```

- **RNG_H** = highest SH (swing high) у поточному active range
- **RNG_L** = lowest SL (swing low) у поточному active range
- **EQ** = `(RNG_H + RNG_L) / 2` — equilibrium (50% level)
- Range valid тільки якщо ≥ `min_swing_count` свінгів (configurable)
- Якщо range invalid → **не показувати** (це explicit — не fake рівні)

**Guard**: `if len(swings) < min_swing_count → []`

### 3.3 Budget enforcement

```python
LEVEL_BUDGET = {
    'htf':       4,   # PDH + PDL + PWH + PWL
    'dynamic':   4,   # DH + DL + WH + WL
    'session':   4,   # max 2 sessions × 2 (H+L)
    'opens':     2,   # DO + WO
    'liquidity': 4,   # 2 EQH + 2 EQL (nearest/strongest)
    'range':     3,   # RNG_H + RNG_L + EQ
}
# Theoretical max: 21 levels
# After proximity filter + display cap → 10-14 visible
```

`merge_and_budget()` — pure function:
1. Per-category cap (budget dict)
2. Proximity filter: `abs(price - current_price) < N × ATR` (configurable, default=`10.0` — wider than zones because levels = orientation anchors for the whole chart)
3. Global cap: `config.key_levels.max_total` (default=16)
4. Stable sort: `(category_priority, -strength, -abs(distance))` for determinism (S2)

### 3.4 Wire format

`SmcLevel` (existing type in `core/smc/types.py`) вже підходить:

```python
@dataclasses.dataclass(frozen=True)
class SmcLevel:
    id: str              # "{kind}_{symbol}_{tf_s}_{anchor_ms}"
    symbol: str
    tf_s: int
    kind: str            # pdh, pdl, pwh, pwl, dh, dl, wh, wl, do_, wo,
                         # as_h, as_l, lon_h, lon_l, ny_h, ny_l,
                         # eqh, eql, rng_h, rng_l, eq
    price: float
    strength: float      # 1.0 for fixed levels, 0.0-1.0 for EQH/EQL
    count: int           # touch count (EQH/EQL), 1 for fixed levels
    first_touch_ms: int
    last_touch_ms: int
```

**Нові поля** (додати до SmcLevel):

```python
    swept: bool = False          # liquidity swept (price pierced and returned)
    category: str = ""           # "htf" | "dynamic" | "session" | "opens" | "liquidity" | "range"
```

UI `SmcLevel` type in `types.ts` — mirror додаткових полів.

### 3.5 UI rendering — Style map per kind

**Backend не надсилає стиль.** Стиль = UI concern (S5: styles = presentation, not data).

```typescript
type LevelStyle = {
  color: string;
  dash: number[];
  width: number;
  labelColor: string;
};

const LEVEL_STYLES: Record<string, LevelStyle> = {
  // A. HTF Reference
  pdh:   { color: '#C0C0C0', dash: [3,3],  width: 1.0, labelColor: '#C0C0C0' },
  pdl:   { color: '#C0C0C0', dash: [3,3],  width: 1.0, labelColor: '#C0C0C0' },
  pwh:   { color: '#888888', dash: [8,4],  width: 1.0, labelColor: '#888888' },
  pwl:   { color: '#888888', dash: [8,4],  width: 1.0, labelColor: '#888888' },

  // B. Dynamic
  dh:    { color: '#26a69a', dash: [],      width: 0.5, labelColor: '#26a69a' },
  dl:    { color: '#ef5350', dash: [],      width: 0.5, labelColor: '#ef5350' },
  wh:    { color: '#26a69a', dash: [6,3],  width: 0.5, labelColor: '#26a69a' },
  wl:    { color: '#ef5350', dash: [6,3],  width: 0.5, labelColor: '#ef5350' },

  // C. Sessions
  as_h:  { color: '#7c4dff', dash: [2,2],  width: 0.7, labelColor: '#7c4dff' },
  as_l:  { color: '#7c4dff', dash: [2,2],  width: 0.7, labelColor: '#7c4dff' },
  lon_h: { color: '#ff6e40', dash: [2,2],  width: 0.7, labelColor: '#ff6e40' },
  lon_l: { color: '#ff6e40', dash: [2,2],  width: 0.7, labelColor: '#ff6e40' },
  ny_h:  { color: '#69f0ae', dash: [2,2],  width: 0.7, labelColor: '#69f0ae' },
  ny_l:  { color: '#69f0ae', dash: [2,2],  width: 0.7, labelColor: '#69f0ae' },

  // D. Opens
  do_:   { color: '#616161', dash: [4,4],  width: 0.5, labelColor: '#9e9e9e' },
  wo:    { color: '#616161', dash: [10,6], width: 0.5, labelColor: '#9e9e9e' },

  // E. Liquidity (BSL/SSL semantic: EQH=stop pool above, EQL=stop pool below)
  eqh:   { color: '#ef5350', dash: [],      width: 1.2, labelColor: '#ef5350' },
  eql:   { color: '#42a5f5', dash: [],      width: 1.2, labelColor: '#42a5f5' },

  // F. Range
  rng_h: { color: '#9575cd', dash: [6,3],  width: 0.8, labelColor: '#9575cd' },
  rng_l: { color: '#9575cd', dash: [6,3],  width: 0.8, labelColor: '#9575cd' },
  eq:    { color: '#9e9e9e', dash: [2,4],  width: 0.7, labelColor: '#9e9e9e' },
};
```

### 3.6 Label rendering spec

Кожен рівень:
- **Горизонтальна лінія** через весь видимий chart area (стиль per kind)
- **Label**: короткий text right-aligned, 30px лінія вправо від останнього видимого бару, текст **над** лінією
- **Label format**: `kind.toUpperCase()` + `price` (наприклад: `PDH 5312.50`)
- **Swept levels**: opacity × 0.4 (dimmed but visible — shows "liquidity taken")
- **Font**: `9px monospace`, color = `labelColor` from style map

### 3.7 W1 derivation (no new TF)

Weekly bars не існують як окремий TF у системі. Замість нового TF:

```python
def _aggregate_d1_to_w1(d1_bars: List[CandleBar], d1_anchor_s: int = 79200) -> List[CandleBar]:
    """Aggregate D1 bars into W1 bars. Pure function.

    Week boundary: Monday d1_anchor (22:00 UTC Sunday → 22:00 UTC Friday).
    Groups consecutive D1 bars by ISO week number.
    Returns: list of synthetic W1 CandleBars.
    """
```

Це pure, живе в `core/smc/key_levels.py`, не створює нового TF в derive chain.

---

## 4. Config (SSOT)

Нова секція `config.json:smc.key_levels`:

```json
{
  "smc": {
    "key_levels": {
      "enabled": true,
      "sessions": {
        "asia":   { "start_utc": "00:00", "end_utc": "09:00" },
        "london": { "start_utc": "07:00", "end_utc": "16:00" },
        "ny":     { "start_utc": "12:00", "end_utc": "21:00" }
      },
      "budget": {
        "htf": 4,
        "dynamic": 4,
        "session": 4,
        "opens": 2,
        "liquidity": 4,
        "range": 3
      },
      "max_total": 16,
      "proximity_atr_mult": 10.0,
      "range_min_swings": 4,
      "categories_enabled": {
        "htf": true,
        "dynamic": true,
        "session": true,
        "opens": true,
        "liquidity": true,
        "range": true
      }
    }
  }
}
```

---

## 5. Execution Plan (P-slices)

| Slice | Scope | LOC | Gate |
|-------|-------|-----|------|
| **L1** | `core/smc/key_levels.py`: 6 pure functions + `_aggregate_d1_to_w1` + `merge_and_budget` | ~140 | `pytest test_smc_key_levels.py` green |
| **L2** | `core/smc/config.py` + `config.json`: `SmcKeyLevelsConfig`, `SessionDef` dataclasses, SSOT config | ~40 | Config parses without error |
| **L3** | `runtime/smc/smc_runner.py`: I/O wiring — fetch D1/M1, call pure functions, merge into snapshot | ~50 | `pytest test_smc_runner.py` green + existing 147 pass |
| **L4** | `core/smc/types.py`: add `swept`, `category` to SmcLevel + `to_wire()` | ~10 | S6 wire format matches |
| **L5** | `ui_v4/src/chart/overlay/OverlayRenderer.ts`: LEVEL_STYLES map + label rendering | ~60 | `npm run build` clean |
| **L6** | `ui_v4/src/types.ts`: mirror `swept`, `category` fields | ~5 | TypeScript build clean |
| **L7** | `tests/test_smc_key_levels.py`: 15+ tests + changelog | ~170 | All green |

### Порядок

```
L1 + L2 (pure logic + config) → L4 (types) → L7 (tests) → L3 (I/O wiring) → L5 + L6 (UI) → verify all
```

---

## 6. Інваріанти

| ID | Check | Status |
|----|-------|--------|
| I0 | `core/smc/key_levels.py` = pure, no I/O | ✅ By design |
| S0 | Same as I0 | ✅ |
| S1 | SmcRunner read-only (UDS.read_window only) | ✅ No new writes |
| S2 | Same D1+M1 bars → same levels | ✅ Deterministic functions |
| S3 | Level ID = `{kind}_{symbol}_{tf_s}_{anchor_ms}` | ✅ Existing scheme |
| S5 | All params from config.json:smc.key_levels | ✅ No hardcoded thresholds |
| S6 | Wire format = SmcLevel.to_wire() → types.ts SmcLevel | ✅ 2 new fields |

---

## 7. Наслідки

### Що змінюється
- Нові файли: `core/smc/key_levels.py`, `tests/test_smc_key_levels.py`
- Модифіковані: `config.py`, `config.json`, `types.py`, `types.ts`, `OverlayRenderer.ts`, `smc_runner.py`, `__init__.py`
- Wire format: SmcLevel отримує 2 optional поля (`swept`, `category`)
- UI: `renderLevels()` повністю переписується (style map замість hardcoded yellow)

### Performance
- PDH/PDL/PWH/PWL: обчислюються раз на D1 close (~0ms)
- DH/DL/sessions: per M1 bar, O(N) scan де N ≤ 1440 (one day M1) — ~0.1ms
- Range: from existing swings, O(N) де N ≤ 20 — ~0ms
- Total per-bar overhead: **< 0.5ms** (negligible vs existing 3.8ms)

### Ризики
- **W1 aggregation accuracy**: ISO week boundaries + FX calendar (Mon-Fri). Mitigation: тести з конкретними датами
- **Session overlap**: London+NY показуються одночасно → 4 lines замість 2. Mitigation: budget cap = 4 total for sessions
- **M1 availability**: якщо UDS немає M1 для поточного дня → DH/DL/sessions порожні. Mitigation: graceful empty, not fake data

---

## 8. Rollback

1. `config.json:smc.key_levels.enabled = false` → key levels не обчислюються
2. Окремі категорії: `categories_enabled.session = false` → sessions off
3. Code rollback: revert `key_levels.py` + SmcRunner changes, SmcLevel fields backward-compatible (optional fields с defaults)

---

## 9. Gap Analysis vs ADR-0024

| ADR-0024 §4.5 | Що було | Що стає |
|----------------|---------|---------|
| Liquidity Levels | EQH/EQL clustering only | + PDH/PDL/PWH/PWL + DH/DL/WH/WL + Sessions + Opens + Range |
| Budget | `max_levels: 12` flat | Per-category budget (21 theoretical, 16 cap, ~10-14 after proximity) |
| Wire format | `SmcLevel` without swept/category | + `swept: bool`, `category: str` |
| UI rendering | All yellow dashed | Per-kind style map (7 distinct visual styles) |

---

## 10. Відкриті питання

| # | Питання | Пропозиція | Status |
|---|---------|------------|--------|
| Q1 | Sub-toggles per category в UI? | Phase 2: single LVL toggle → per-category | Deferred |
| Q2 | Hover tooltip з distance/swept/origin? | Phase 2: requires tooltip infrastructure | Deferred |
| Q3 | Quarter levels (0.25/0.75) для Range? | Optional: add if EQ alone insufficient | Deferred |
| Q4 | BSL/SSL semantic labels на UI замість EQH/EQL? | Config option `liquidity_label_mode: "eq" | "bsl"` | Deferred |