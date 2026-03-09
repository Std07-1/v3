# ADR-0035: Sessions & Killzones — Trading Session Awareness Module

- **Статус**: Implemented
- **Дата**: 2026-03-08
- **Автор**: R_ARCHITECT (cross-role: R_TRADER, R_SMC_CHIEF, R_BUG_HUNTER, R_CHART_UX)
- **Initiative**: `smc_sessions_v1`
- **Залежності**: ADR-0024 (SMC Engine), ADR-0024b (Key Levels), ADR-0029 (Confluence), ADR-0033 (Narrative)

---

## §1. Контекст і проблема

### 1.1 Що відсутнє

Платформа v3 має повний SMC pipeline (8 детекторів, confluence scoring, narrative engine), але **не знає** про торгові сесії. Трейдер SMC використовує три основні сесії як структурні якорі:

- **Asia** (Tokyo): H/L = ліквідність для London sweep
- **London**: Перша хвиля доставки ціни, найвища ймовірність сетапу  
- **New York**: Друга хвиля, підтвердження або розворот

Без session awareness:

1. `narrative.py` не може сказати "чекай London open" — тільки "approaching zone"
2. `key_levels.py` не генерує Asia H/L, London H/L — трейдер не бачить session extremes
3. `confluence.py` не знає чи sweep був session-level (Asia H swept → +confluence)
4. Трейдер не отримує killzone warning (entry поза London 07–10 або NY 12–15 = нижча ймовірність)

### 1.2 Джерела вимог

| Джерело | Що каже |
|---------|---------|
| ADR-0024b §1.2 | "Session category: [MISSING]" — явно відкладено |
| ADR-0033 T-7 | "Session levels not in priority chain for v1. Deferred to ADR for session module" |
| ADR-0034 Q6 | "Session module deferred to ADR" |
| TDA Research | "Годинний TF за сесійний наратив" — H1 = session-aware |
| TDA Research | "ціна знімає азійський хайп" — session sweep = confluence |
| DT Sessions Conference | Asia 01:00–09:00 Kyiv (=23:00–07:00 UTC), Frankfurt bridge 07–08 UTC, OTT = London KZ + NY KZ |
| DT Sessions Conference | "Нова сесія завжди прагне зняти ліквідність з попередньої" — rebalancing pattern |
| DT Sessions Conference | "Свіпи фітилями, а не тілами" — wick vs body sweep distinction for F9 |
| `docs/system_current_overview.md` | "Sessions/Killzones (E3) — не реалізовано" |

### 1.3 Data-lineage: звідки беремо M1

```
FXCM → broker_sidecar → m1_ingestion_worker → UDS (M1 SSOT)
                                                  ↓
                                        derive_engine: M1→M3→...→H4+D1
                                                  ↓
                                        smc_runner.on_bar() → SmcEngine
                                                  ↓ (NEW)
                                        sessions.py: compute_session_levels(m1_bars, session_cfg)
```

Session H/L обчислюються з **M1 барів** (найточніше), але привʼязані до **D1** (прив'язка до торгового дня). Це відрізняється від PDH/PDL які обчислюються з H1/H4/D1 completed bars.

---

## §2. Розглянуті варіанти

### Варіант A: Session levels як розширення key_levels.py (ОБРАНО)

**Суть**: Додати session level kinds до існуючої системи `SmcLevel` + `_KEY_LEVEL_ALLOW`, обчислювати session H/L з M1 aggregate у новому `core/smc/sessions.py`.

- ✅ Мінімальна інвазивність: `SmcLevel` вже є, cross-TF injection працює
- ✅ `confluence.py` вже має extensible factor list (8 → 9)
- ✅ `OverlayRenderer` вже рендерить levels з kind → style mapping
- ✅ Детермінізм: same M1 bars + session config → same session levels (S2)
- ⚠️ Потребує M1 bars у SmcEngine (зараз engine отримує тільки бари свого TF)

### Варіант B: Окремий SessionEngine поруч із SmcEngine

**Суть**: Новий клас `SessionEngine` в `runtime/smc/`, паралельний callback `on_m1_bar()`.

- ✅ Повна ізоляція (не торкає SmcEngine)
- ❌ Два місця рендерингу levels → split-brain risk
- ❌ Два wire-формати для levels
- ❌ Порушує I1: UDS не має двох overlay sources

### Варіант C: Sessions тільки в narrative (UI context, без levels)

**Суть**: `narrative.py` отримує `current_session` + `in_killzone` з `market_calendar.py`, але session H/L не обчислюються.

- ✅ Мінімальний обсяг
- ❌ Трейдер не бачить Asia H/L на chart → основна вимога не задоволена
- ❌ Confluence не може рахувати session sweep

**Рішення: Варіант A** — session levels як SmcLevel kinds, обчислення в `core/smc/sessions.py`, injection через існуючий `_KEY_LEVEL_ALLOW` pipeline.

---

## §3. Архітектурне рішення

### 3.1 Визначення сесій (UTC, SSOT — config.json)

```jsonc
// config.json → smc.sessions
"sessions": {
    "enabled": true,
    "definitions": {
        "asia": {
            "label": "Asia",
            "open_utc":  "00:00",     // Tokyo 09:00 JST
            "close_utc": "07:00",     // DT: 09:00 Kyiv=07:00 UTC. No overlap with London.
            "killzone_start_utc": "00:00",
            "killzone_end_utc":   "03:00"
        },
        "london": {
            "label": "London",
            "open_utc":  "07:00",     // Frankfurt bridge 07-08, London proper 08:00
            "close_utc": "16:00",     // DT OTT morning = KZ 07-10 UTC ✅
            "killzone_start_utc": "07:00",
            "killzone_end_utc":   "10:00"
        },
        "newyork": {
            "label": "New York",
            "open_utc":  "12:00",     // DT OTT afternoon starts 12:00 UTC (14:00 Kyiv)
            "close_utc": "21:00",     // NY proper open 13:00 UTC (15:00 Kyiv)
            "killzone_start_utc": "12:00",
            "killzone_end_utc":   "15:00"
        }
    },
    "level_budget_per_session": 2,    // max H/L levels per session
    "previous_session_ttl_bars": 500, // TTL для prev session H/L (аналог zone decay)
    "sweep_lookback_bars": 30,        // для confluence factor: bars after sweep
    "sweep_body_ok": false            // true = body close beyond level counts as sweep (score 1 max). DT: false = wick only (default)
}
```

**Примітки R_ARCHITECT:**

- DST-проблема: London/NY зсуваються ±1h двічі на рік. UTC є SSOT — трейдер коригує в config якщо потрібно.
- **Asia/London НЕ перекриваються** (Asia close 07:00 = London open 07:00). Frankfurt bridge (07:00–08:00) = перша година London де відбувається sweep Asia liquidity (DT: "Франкфурт займає час між закриттям Азії та відкриттям Лондона").
- London/NY **перекриваються** (London 07–16 × NY 12–21): це нормально, в overlap обидві активні.
- Weekend: ніяких session levels (market_calendar.is_trading_minute() = false → skip).
- **OTT (Optimal Trade Time)** = London KZ (07–10 UTC) + NY KZ (12–15 UTC). Підтверджено DT research. Lunch (10–12 UTC) = нижча волатильність, але не hard stop (DT: "не означає що я не можу відкрити позицію").

### 3.2 Нові Level Kinds

```python
# core/smc/types.py — додати до LEVEL_KINDS
SESSION_LEVEL_KINDS = frozenset({
    # Asia session
    "as_h", "as_l",           # Active Asia High/Low (running)
    "p_as_h", "p_as_l",       # Previous Asia High/Low (locked)
    # London session
    "lon_h", "lon_l",         # Active London High/Low
    "p_lon_h", "p_lon_l",     # Previous London High/Low
    # New York session
    "ny_h", "ny_l",           # Active New York High/Low
    "p_ny_h", "p_ny_l",       # Previous New York High/Low
})

LEVEL_KINDS = frozenset({
    ... existing ...,
    *SESSION_LEVEL_KINDS,
})
```

**Naming convention**: `{p_}{session_abbr}_{h|l}` — `p_` prefix = previous (locked), без prefix = active (running). Аналог PDH/PDL pattern.

### 3.3 Pure Logic: `core/smc/sessions.py`

```python
"""
core/smc/sessions.py — Trading Session H/L Computation (ADR-0035).

S0: pure logic, NO I/O.
S2: deterministic — same bars + config → same levels.
S5: session windows — SSOT in config.json:smc.sessions.
"""

@dataclass(frozen=True)
class SessionWindow:
    """Визначення однієї торгової сесії."""
    name: str           # "asia" | "london" | "newyork"
    label: str          # "Asia" | "London" | "New York"
    open_utc_min: int   # minutes from midnight UTC (0 = 00:00)
    close_utc_min: int  # minutes from midnight (540 = 09:00)
    kz_start_min: int   # killzone start (minutes from midnight)
    kz_end_min: int     # killzone end

@dataclass(frozen=True)
class SessionState:
    """Стан однієї сесії: running H/L + previous H/L."""
    name: str
    active: bool           # True якщо зараз в межах open..close
    in_killzone: bool      # True якщо kz_start ≤ now < kz_end
    current_high: Optional[float]    # Running session high (None якщо not active)
    current_low: Optional[float]     # Running session low
    current_start_ms: Optional[int]  # Session open bar ms
    previous_high: Optional[float]   # Locked previous session high
    previous_low: Optional[float]    # Locked previous session low
    previous_start_ms: Optional[int] # When previous session started

def classify_bar_session(bar_open_ms: int, sessions: List[SessionWindow]) -> List[str]:
    """Визначити, до яких сесій належить бар (може бути >1 при overlap)."""

def compute_session_levels(
    bars: List[CandleBar],
    sessions: List[SessionWindow],
    current_time_ms: int,
    symbol: str,
    tf_s: int = 86400,  # session levels живуть під D1 tf_s
) -> Tuple[List[SmcLevel], List[SessionState]]:
    """Обчислити session H/L levels + session states.

    Алгоритм:
    1. Для кожної сесії:
       a. Знайти бари що належать до ПОТОЧНОЇ сесії → running H/L
       b. Знайти бари що належать до ПОПЕРЕДНЬОЇ сесії → locked H/L
    2. Генерувати SmcLevel для кожного H/L з відповідним kind

    Args:
        bars: M1 бари, sorted by open_time_ms (oldest first)
        sessions: session definitions з config
        current_time_ms: поточний час (epoch ms)
        symbol: символ
        tf_s: TF під яким session levels зберігаються (86400 = D1 scope)

    Returns:
        levels: List[SmcLevel] — max 12 (3 sessions × 2 H/L × 2 [current+prev])
        states: List[SessionState] — поточний стан кожної сесії
    """
```

**Чому M1?** Session H/L мають бути максимально точними. D1 бар = один OHLC на день, він не показує Asia H/L. H1 → 9 барів на Asia, але M1 → 540 барів = найточніший H/L.

**Вхідні дані**: SmcEngine вже отримує бари через `on_bar()`. Для session levels потрібні M1 бари останніх ~48h (два торгових дні). `smc_runner.py` має доступ до UDS для warmup — передає M1 bars при виклику session computation.

### 3.4 Cross-TF Injection Map

```python
# engine.py → _KEY_LEVEL_ALLOW розширення
_KEY_LEVEL_ALLOW = {
    300: frozenset({
        ... existing D1+H4+H1 ...,
        # Session levels: M5 viewer бачить все (трейдер шукає entry)
        "as_h", "as_l", "p_as_h", "p_as_l",
        "lon_h", "lon_l", "p_lon_h", "p_lon_l",
        "ny_h", "ny_l", "p_ny_h", "p_ny_l",
    }),
    900: frozenset({
        ... existing D1+H4 ...,
        # M15 viewer: active + previous session H/L
        "as_h", "as_l", "p_as_h", "p_as_l",
        "lon_h", "lon_l", "p_lon_h", "p_lon_l",
        "ny_h", "ny_l", "p_ny_h", "p_ny_l",
    }),
    3600: frozenset({
        ... existing D1+H4 ...,
        # H1 viewer: session context = ключовий
        "as_h", "as_l", "p_as_h", "p_as_l",
        "lon_h", "lon_l", "p_lon_h", "p_lon_l",
        "ny_h", "ny_l", "p_ny_h", "p_ny_l",
    }),
    14400: frozenset({
        "pdh", "pdl", "dh", "dl",
        # H4 viewer: тільки prev session H/L (current видно з H4 свічок)
        "p_as_h", "p_as_l",
        "p_lon_h", "p_lon_l",
        "p_ny_h", "p_ny_l",
    }),
    86400: frozenset(),  # D1 viewer: sessions видно у свічках
}
```

### 3.5 Confluence Extension: F9 `session_sweep`

```python
# confluence.py — додати 9-й фактор
def _check_session_sweep(zone, session_levels, bars, tf_s, config):
    # type: (dict, list, list, int, dict) -> int
    """F9: +1 якщо ціна swept session H/L за N барів ПЕРЕД утворенням зони.

    Session sweep = liquidity grab перед delivery → вища ймовірність зони.
    Аналог: знімаємо Asia H → входимо London OB → trade.
    """
```

**F9 = тристанний фактор** (Cross-Role: R_TRADER + R_SMC_CHIEF):

- 0 = no session sweep nearby
- 1 = sweep detected, no confirmation candle
- 2 = sweep + confirmation candle (displacement/rejection)

**Sweep type distinction** (DT: "свіпи фітилями, а не тілами"):

- **Wick sweep** (price.h > level, bar.c < level — або price.low < level, bar.c > level): preferred, price explored beyond level and rejected → F9 score as above (0/1/2)
- **Body close** beyond level: weaker signal для HTF context, acceptable для intraday session context
- Implementation: `_check_session_sweep()` default = wick-only sweep detection. Config option `smc.sessions.sweep_body_ok: false` (SSOT). При `true` — body close теж вважається sweep зі score 1 (не 2)
- Rationale: DT чітко розділяє — для HTF свіпи лише фітилями, для сесійної роботи body close допустимий

**Grade thresholds з F9**:

- Max score: 10 (було 8)
- A+ ≥ 8 (без змін)
- A ≥ 6 (без змін)
- B ≥ 4 (без змін)

### 3.6 Narrative Extension

```python
# NarrativeBlock — нові поля
@dataclass(frozen=True)
class NarrativeBlock:
    ... existing fields ...,
    current_session: str       # "asia" | "london" | "newyork" | "off_session"
    in_killzone: bool          # True → "London killzone active"
    session_context: str       # "Asia H swept at 07:15 → London delivery expected"
```

**Killzone = Context + Downgrade Rule** (Cross-Role: R_TRADER + R_SMC_CHIEF):

- Killzone **не додає** confluence score
- Entry **поза killzone** → narrative.mode = "wait" з повідомленням "Wait for killzone" або "Reduced probability outside killzone"
- Це не блокування entry, а **інформаційний downgrade**: трейдер бачить чіткий сигнал що ймовірність нижча
- DT підтверджує: "Сесія — це той же самий інструмент... важлива сукупність факторів, а не один фактор"
- Asia H/L = PRIMARY liquidity targets для London. В Focus mode на M15/M5 вони показуються завжди (proximity filter не застосовується до session levels active session)

**Rebalancing Pattern** (DT: "Нова сесія завжди буде прагнути зняти ліквідність з попередньої"):

Новаtoк `narrative.py` для session context:

1. New session open → "Expecting sweep of {prev_session} H/L"
2. Sweep detected → "Session sweep: {prev_session} H swept at {time}"
3. Rebalancing (FVG fill after sweep) → "{session} rebalancing after sweep"
4. Continuation → "{session} delivery after rebalancing"

Ця послідовність = основний торговий паттерн DT для внутрішньоденної торгівлі.

### 3.7 Wire Format Extension

```typescript
// ui_v4/src/lib/types.ts — SmcLevel вже ready
interface SmcLevel {
    id: string;
    kind: string;     // + "as_h" | "as_l" | "p_as_h" | ... 
    price: number;
    t_ms?: number;
}

// NarrativeBlock extension
interface NarrativeBlock {
    ... existing ...,
    current_session: string;
    in_killzone: boolean;
    session_context: string;
}
```

### 3.8 UI Rendering: Session Level Styling (Cross-Role: R_CHART_UX)

```typescript
// OverlayRenderer.ts — LEVEL_STYLES розширення
const SESSION_LEVEL_STYLES = {
    // Active session: solid thin, bright
    "as_h":  { color: "#CE93D8", dash: [],     width: 1, label: "Asia H" },
    "as_l":  { color: "#CE93D8", dash: [],     width: 1, label: "Asia L" },
    "lon_h": { color: "#FF9800", dash: [],     width: 1, label: "LON H" },
    "lon_l": { color: "#FF9800", dash: [],     width: 1, label: "LON L" },
    "ny_h":  { color: "#42A5F5", dash: [],     width: 1, label: "NY H" },
    "ny_l":  { color: "#42A5F5", dash: [],     width: 1, label: "NY L" },
    // Previous session: dashed, dimmed
    "p_as_h":  { color: "#CE93D880", dash: [4,4], width: 1, label: "prev Asia H" },
    "p_as_l":  { color: "#CE93D880", dash: [4,4], width: 1, label: "prev Asia L" },
    "p_lon_h": { color: "#FF980080", dash: [4,4], width: 1, label: "pLON H" },
    "p_lon_l": { color: "#FF980080", dash: [4,4], width: 1, label: "pLON L" },
    "p_ny_h":  { color: "#42A5F580", dash: [4,4], width: 1, label: "pNY H" },
    "p_ny_l":  { color: "#42A5F580", dash: [4,4], width: 1, label: "pNY L" },
};
// Colors: LightPurple=Asia, Orange=London, Blue=NY (R_CHART_UX: NY≠Green to avoid candle confusion)
// Convention: active=solid, previous=dashed+50% opacity
```

**Line Extent Rules (R_CHART_UX):**

- Active session level: extends from `session_start_ms` to current bar (shows where range formed)
- Previous session level: extends across full visible range (locked historical level)
- Complies with ADR-0026 L3: NOT full-width, has defined start point

**Killzone Indicator (R_CHART_UX):**

- NOT overlay band (占 canvas space, adds clutter)
- Killzone = subtle time-axis indicator: colored dot or underline on time labels during killzone hours
- Alternative: thin 2px horizontal strip at chart bottom during killzone
- Minimal visual weight — трейдер знає час killzone, система лише підтверджує

**DisplayBudget Rules (R_SMC_CHIEF):**

- Focus mode: max 4 session levels total (active session H/L + prev session H/L of closest active session)
- Research mode: all 12 session levels
- During London+NY overlap (12–16 UTC): show London H/L + NY H/L (4 levels), hide previous
- Session levels compete within existing `level` budget, not separate budget
- Proximity filter: `atr_mult × 0.5` for session levels (tighter than zones)
- **Exception**: active session H/L of current session bypass proximity filter (R_TRADER: they are PRIMARY targets)

---

## §4. P-Slice Plan

| Slice | Scope | Files | LOC est | Invariant | Rail |
|-------|-------|-------|---------|-----------|------|
| **P0** | Types + Config | `types.py`, `config.py`, `config.json` | ~40 | S5: SSOT config | Type guard |
| **P1** | Session logic (pure) | `core/smc/sessions.py` (NEW) | ~120 | S0: no I/O, S2: deterministic | Unit tests |
| **P2** | Engine integration | `engine.py`, `smc_runner.py` | ~60 | S4: perf < 10ms | Budget rail |
| **P3** | Cross-TF injection | `engine.py` _KEY_LEVEL_ALLOW | ~30 | SSOT: one map | Display filter |
| **P4** | Confluence F9 | `confluence.py` | ~30 | Grade stability test | |
| **P5** | Narrative extension | `narrative.py`, `types.py` | ~40 | N3: never None | Fallback |
| **P6** | Wire + Frontend | `types.ts`, `OverlayRenderer.ts`, `smcStore.ts` | ~60 | S6: wire contract | |
| **P7** | Tests + verify | `tests/test_smc_sessions.py` (NEW) | ~100 | | |
| | **TOTAL** | | **~480** | | |

### Залежності між slices

```
P0 (types/config) → P1 (sessions.py) → P2 (engine) → P3 (injection)
                                      → P4 (confluence)
                                      → P5 (narrative)
                     P1 → P6 (wire/UI)
                     P1 → P7 (tests — починається з P1, росте з кожним slice)
```

---

## §5. Інваріанти та обмеження

### 5.1 Платформні інваріанти (I0–I6 compliance)

| ID | Check | Compliance |
|----|-------|------------|
| I0 | `core/smc/sessions.py` = pure, no I/O | ✅ No imports from runtime |
| I1 | No OHLCV writes | ✅ Read-only: M1 bars → levels |
| I2 | Time geometry | ✅ Session boundaries = UTC minutes, bar open_ms = epoch ms |
| I3 | Final > Preview | ✅ Session H/L з complete bars only (preview bars excluded) |
| I5 | Degraded-but-loud | ✅ Empty sessions → warning in NarrativeBlock.warnings |
| I6 | Stop-rule | N/A — не ламає I0–I5 |

### 5.2 SMC інваріанти (S0–S6 compliance)

| ID | Check | Compliance |
|----|-------|------------|
| S0 | `sessions.py` = pure | ✅ In `core/smc/`, no I/O |
| S2 | Deterministic | ✅ Same M1 bars + config → same levels |
| S3 | Level IDs deterministic | ✅ `{kind}_{symbol}_{tf_s}_{price×100}` |
| S4 | Performance < 10ms | ⚠️ M1 scan ~540 bars per session — потрібен benchmark |
| S5 | Config SSOT | ✅ Session defs in config.json |
| S6 | Wire match TS types | ✅ SmcLevel already has kind field |

### 5.3 Граничні випадки (Failure Model)

| # | Сценарій | Поведінка |
|---|----------|-----------|
| F1 | M1 бари відсутні (cold start) | Повернути [] levels + warning "insufficient_m1_data" |
| F2 | Перехід DST (London ±1h) | Config = UTC фіксований; трейдер оновлює config якщо потрібно |
| F3 | Weekend (ринок закритий) | `market_calendar.is_trading_minute()` = false → skip session computation |
| F4 | Overlap London+NY (12:00–16:00) | Обидві сесії active → обидва набори H/L відображаються. Asia/London НЕ перекриваються (Asia close=07:00=London open). |
| F8 | Asia trending (not ranging) | DT: "Азія не є трендовою, ніякого розриву не створює" vs "Азія може йти в якесь сильне трендове рухання. І це цілком нормально." → compute H/L regardless, narrative says "Asia trending" or "Asia ranging" |
| F9 | Price runs without test (no entry) | DT: "ціна просто летить без жодного тесту" — narrative warns "no retest" but session H/L still valid |
| F10 | ADR range exhaustion | DT: "якщо Азія зробила половину цього руху, то нема сенсу чекати сильного руху" — narrative condition for v2 |
| F5 | Session H/L = однакова ціна | Два levels з різними kinds, rendering нормальний (merge по L1 ADR-0026) |
| F6 | Символ з іншим session profile (HKG33) | Config per symbol group — v1: один config для всіх FX |
| F7 | Gap при відкритті session | Перший M1 бар після gap = session start; H/L починається з нього |

---

## §6. Rollback

1. `config.json → smc.sessions.enabled = false` — вимикає все
2. `_KEY_LEVEL_ALLOW` без session kinds → levels не з'являються на chart
3. `confluence.py` без F9 → score повертається до max 8 (тристанний F9: max 10 → max 8)
4. `NarrativeBlock` fallback: `current_session=""`, `in_killzone=false` — backward compatible

---

## §7. Cross-Role Review — Resolved Questions

| # | Питання | Вирішив | Рішення |
|---|---------|---------|---------|
| Q1 | Asia 00–07 UTC? | R_TRADER + DT Research | **REVISED.** Asia close_utc = 07:00 (DT: 09:00 Kyiv = 07:00 UTC). Попередній 09:00 UTC створював 2h overlap з London, розмиваючи Asia H/L Frankfurt opening volatility. Asia/London boundary = exact (07:00 UTC = Asia close = London open) |
| Q2 | Killzone: context чи factor? | R_TRADER + R_SMC_CHIEF | **Context + Downgrade.** DT підтверджує: "Сесія — це той же самий інструмент... сукупність факторів" |
| Q3 | Session open price? | R_TRADER | **DEFER v2.** Asia/London/NY H/L достатньо для v1 |
| Q4 | Overlap styling? | R_CHART_UX | **Both session colors visible.** During overlap: London H/L (Orange) + NY H/L (Blue), hide previous |
| Q5 | Sweep lookback? | R_SMC_CHIEF | **Config-driven** (`smc.sessions.sweep_lookback_bars: 30`). Default 30 M1 bars |
| Q6 | H4 viewer: prev only? | R_TRADER | **ACCEPT.** H4 svічки самі показують range — тільки previous session H/L |
| Q7 | Per-symbol sessions? | R_ARCHITECT | **DEFER v2.** v1 = one config for all FX. HKG33/NGAS можуть мати інший профіль → v2 |
| Q8 | DST handling? | R_BUG_HUNTER | **Manual.** Config = UTC фіксований. Narrative з "(UTC)" позначкою. Трейдер коригує в config |
| Q9 | Кольори? | R_CHART_UX | **Asia=#CE93D8 (LightPurple), London=#FF9800 (Orange), NY=#42A5F5 (Blue)** |
| Q10 | Performance? | R_BUG_HUNTER | **OK.** O(n) scan, n≈1050 (7h×60×2.5 sessions), theoretical 0.1ms. Потребує benchmark у P2 |

### Q11–Q14: Нові питання з DT Sessions Research

| # | Питання | Вирішив | Рішення |
|---|---------|---------|---------|
| Q11 | Frankfurt як окрема сесія? | R_ARCHITECT + DT | **NO.** DT: "Нам від нього треба лише одна година" (07–08 UTC). Frankfurt = перша година London. Не окремий tracked session — вхідний sweep Asia liquidity відбувається саме в London KZ (07–10), яка покриває Frankfurt. |
| Q12 | F9 wick vs body sweep? | R_TRADER + DT | **WICK preferred.** DT: "Свіпи фітилями, а не тілами. Для сесії це нормально, для HTF нема." Config: `sweep_body_ok: false` (default). Body close = score 1 max (коли enabled). |
| Q13 | OTT = killzones? | R_ARCHITECT + DT | **YES.** DT OTT = 07–10 UTC (ранок) + 12–15 UTC (обід) = London KZ + NY KZ. Повний збіг. Lunch 10–12 UTC = lower activity але не hard block. |
| Q14 | Weekly templates (Monday correction)? | R_ARCHITECT | **DEFER v2.** DT: "тижневі шаблони — понеділок корекція, середина тижня тренд." Патерн day-of-week bias виходить за scope sessions v1. |
| Q15 | H1 = session analysis TF? | R_TRADER + DT | **CONFIRMED.** DT: "Основний аналіз сесії робиться на годинному таймфреймі." H1 в _KEY_LEVEL_ALLOW вже має всі session kinds. |
| Q16 | Liquidity pool hierarchy? | R_SMC_CHIEF + DT | **CONFIRMED.** DT hierarchy: Session H/L → PDH/PDL → PWH/PWL → PMH/PML → hourly fractals. PDH/PDL вже в ADR-0024b. Session H/L = найголовніше для intraday. |

---

## §8. Cross-Role Review Summary

### R_TRADER (Conditional ACCEPT → ACCEPT after corrections)

- F9 session_sweep = тристанний (0/1/2) ✅ (incorporated §3.5)
- Killzone downgrade rule ✅ (incorporated §3.6)
- Asia H/L bypass proximity filter ✅ (incorporated §3.8)
- Session open price → deferred to v2

### R_TRADER — DT Research Enhancement (2026-03-09)

- Asia close_utc revised 09:00 → 07:00 ✅ (DT: no Asia/London overlap)
- Wick vs body sweep distinction ✅ (DT: "фітилями, а не тілами", config-driven)
- Rebalancing narrative sequence ✅ (DT: "нова сесія → sweep → rebalancing → continuation")
- Sessions = supplementary tool ✅ (DT: "сукупність факторів")
- H1 = session analysis TF ✅ (DT: "годинний таймфрейм")

### R_SMC_CHIEF (Conditional ACCEPT → ACCEPT after corrections)

- DisplayBudget: max 4 session levels in Focus ✅ (incorporated §3.8)
- Overlap rule: show active, hide previous ✅ (incorporated §3.8)
- F9 тристанний scoring ✅ (incorporated §3.5)
- Rendering priority hierarchy documented

### R_SMC_CHIEF — DT Research Enhancement (2026-03-09)

- Liquidity pool hierarchy confirmed: Session H/L → PDH/PDL → PWH/PWL ✅
- F9 sweep_body_ok config option ✅ (body close = max score 1, wick = up to 2)
- Rebalancing = FVG fill after sweep → narrative can detect pattern
- Weekly template bias (day-of-week) → deferred v2

### R_BUG_HUNTER (ACCEPT, 6 findings for P-slices)

| Sev | Finding | P-slice |
|-----|---------|---------|
| S1 | M1 bars must be sorted by open_ms → assert | P1 |
| S2 | Session boundary: bar.open_ms >= session_open, < session_close | P1 |
| S2 | Level ID rounding: `int(round(price * 100))` | P1 |
| S2 | Partial session coverage → warning "session_incomplete" | P1 |
| S3 | DST: narrative label must include "(UTC)" | P5 |
| S3 | Same-price levels merge rule per ADR-0026 L1 | P6 |

### R_BUG_HUNTER — DT Research Enhancement (2026-03-09)

| Sev | Finding | P-slice |
|-----|---------|---------|
| S2 | **Asia close = 07:00 UTC** (was 09:00) — overlap eliminated, reduces false Asia H/L contamination | P0 config |
| S2 | F9 body close detection needs separate code path from wick sweep | P4 |
| S3 | Asia trending vs ranging: narrative distinction (DT describes both patterns) | P5 |
| S3 | "No retest" scenario (DT: "ціна летить без тесту") → narrative warning condition | P5 |

### R_CHART_UX (Conditional ACCEPT → ACCEPT after corrections)

- Color palette: Purple→LightPurple, Green→Blue ✅ (incorporated §3.8)
- Line extent: session_start_ms → now (active), full visible (previous) ✅ (incorporated §3.8)
- Killzone indicator: time-axis dot, not overlay band ✅ (incorporated §3.8)
- Proximity filter: `atr_mult × 0.5` for session levels ✅ (incorporated §3.8)

### R_CHART_UX — DT Research Enhancement (2026-03-09)

- Frankfurt bridge hour (07–08 UTC): no separate visual treatment needed — London KZ covers it ✅
- OTT = killzone visual (already designed as time-axis indicator) ✅
- Lunch period (10–12 UTC): NOT visualized (DT: not important enough for hard indicator)

### Final Status: **ACCEPTED** (all 4 roles approved with incorporated corrections + DT research enhancements)

---

## §9. v2 Backlog (Deferred)

Елементи, що виходять за scope `smc_sessions_v1`, але підтверджені research і потребують окремого ADR або P-slice initiative.

| ID | Назва | Джерело | Що потрібно | Залежності |
|----|-------|---------|-------------|------------|
| **V2-1** | **Session Open Price** | Q3, R_TRADER | Додати `as_open`, `lon_open`, `ny_open` session levels. DT: "open price — це рівень, від якого рахується контекст". Потребує новий kind + rendering (горизонтальна лінія від session open). | P0 types, P1 sessions.py |
| **V2-2** | **Weekly Templates (day-of-week bias)** | Q14, DT Conference | DT: "тижневі шаблони — понеділок корекція, середина тижня тренд". Pattern: Mon=correction into HTF zone, Tue-Wed=delivery, Thu=reversal/continuation, Fri=closure. Потребує: день тижня в `narrative.py`, bias map для day-of-week. | ADR-0033 narrative, ADR-0031 bias |
| **V2-3** | **ADR Range Exhaustion** | F10, DT Conference | DT: "якщо Азія зробила половину цього руху, то нема сенсу чекати сильного руху". Narrative condition: `if session_range > avg_daily_range * threshold → "range exhaustion, reduced probability"`. Потребує: ATR/ADR reference з `config.json` або rolling compute. | P5 narrative, ADR-0024a momentum.py |
| **V2-4** | **Per-Symbol Session Profiles** | Q7, R_ARCHITECT | HKG33/NGAS мають інший session profile (Asian session = primary). Config: `smc.sessions.profiles.{symbol_group}` з override definitions. | P0 config |
| **V2-5** | **Lunch Period Indicator** | DT Conference | 10:00–12:00 UTC = lower volatility. DT: "не означає що не можу відкрити позицію". Опціональний dim indicator на time axis. Low priority. | P6 UI |
