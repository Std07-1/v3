# ADR-0040: TDA Cascade — Daily Signal Engine Rebuild

- **Статус**: Implemented
- **Дата**: 2026-03-18
- **Автор**: R_ARCHITECT
- **Initiative**: `tda_cascade_v1`
- **Замінює**: ADR-0039 (Signal Engine v1 — zone-reactive)
- **Пов'язані ADR**: ADR-0033 (Narrative), ADR-0024 (SMC Engine), ADR-0029 (Confluence), ADR-0035 (Sessions), ADR-0036 (Shell)

---

## 1. Контекст і проблема

### 1.1 Поточний стан — ADR-0039 Signal Engine v1

ADR-0039 реалізує **zone-reactive** модель:

- Бере **будь-яку** зону зі SmcSnapshot будь-якого TF
- Обчислює entry/SL/TP для зони + lifecycle state
- Працює на кожному bar close для кожного viewer TF

### 1.2 Проблема: Simulation vs Live розбіжність

49-денна симуляція (2026-01-06 — 2026-03-14) протестувала **іншу** систему — 4-stage **daily cascade** (TDA):

| Аспект | ADR-0039 (v1) | TDA Simulation |
|--------|---------------|----------------|
| Trigger | Будь-яка зона зі SmcSnapshot | 4-stage daily filter: D1→H4→Session→M15 FVG |
| Частота | Багато сигналів на всіх TF | Max 1 signal/day (quality over quantity) |
| Entry model | OTE/zone_edge на зоні | FVG touch + close outside (price action confirm) |
| SL | Zone edge + buffer | FVG edge + proportional buffer |
| TP | Key level → swing → ATR fallback | Fixed R:R 3:1 (з partial close at 1R) |
| Trade mgmt | Немає | Config F: partial 50% at 1R + trail remaining |

### 1.3 Evidence: Backtest Results

| Метрика | ADR-0039 live (3 дні) | TDA Simulation (49 днів) |
|---------|----------------------|--------------------------|
| Сигналів | 28 events / 1 zone / all approaching | 9 signals / 7 closed |
| Win rate | N/A (жоден не дійшов до ready) | 75% (Config F) |
| Net R | N/A | +2.97R (Config F) |
| Max DD | N/A | 1.00R (Config F) |

### 1.4 Рішення

Повністю замінити ADR-0039 zone-reactive engine на TDA 4-stage cascade.
Це НЕ рефакторинг — це нова архітектура сигнального движка, підтверджена 49-денним бектестом.

---

## 2. Архітектура TDA Cascade

### 2.1 Signal Flow (Daily Cascade)

```
┌─────────────────────────────────────────────────────┐
│                    TDA CASCADE                       │
│                                                      │
│  D1 bar close       H4 bar close      H1 bar close  │
│       │                  │                  │        │
│  ┌────▼────┐      ┌─────▼─────┐     ┌──────▼──────┐ │
│  │ Stage 1 │      │  Stage 2  │     │   Stage 3   │ │
│  │D1 Macro │─YES─▶│H4 Confirm │─YES▶│Session Narr.│ │
│  │Direction│      │ Alignment │     │ Sweep Detect│ │
│  └────┬────┘      └─────┬─────┘     └──────┬──────┘ │
│       │NO               │NO                │NO      │
│       ▼                 ▼                  ▼        │
│    STAYOUT           STAYOUT            STAYOUT     │
│                                                      │
│                               │YES                   │
│                        ┌──────▼──────┐               │
│                        │   Stage 4   │               │
│                        │ M15 FVG     │               │
│                        │ Entry Model │               │
│                        └──────┬──────┘               │
│                               │                      │
│                        ┌──────▼──────┐               │
│                        │   Config F  │               │
│                        │ Trade Mgmt  │               │
│                        │ Partial @1R │               │
│                        │ Trail 50%   │               │
│                        └─────────────┘               │
└─────────────────────────────────────────────────────┘
```

### 2.2 Stage Specification

#### Stage 1: D1 Macro Direction

**Input**: Completed D1 bars (open_time_ms + 86400000 ≤ current_ms)
**Gate**: ≥5 completed D1 bars, else CFL (conflict)
**Algorithm**: 3-bar pivot detection → linear slope fallback
**Output**: `"BULL" | "BEAR" | "CFL"`

```python
# Primary: 3-bar pivot in last 20 completed D1 bars
pivH = recent bars where b[i].h ≥ b[i-1].h AND b[i].h ≥ b[i+1].h
pivL = recent bars where b[i].low ≤ b[i-1].low AND b[i].low ≤ b[i+1].low

IF len(pivH) ≥ 2 AND len(pivL) ≥ 2:
    hh = pivH[-1] > pivH[-2]; hl = pivL[-1] > pivL[-2]
    lh = pivH[-1] < pivH[-2]; ll = pivL[-1] < pivL[-2]
    IF hh AND hl → BULL; IF lh AND ll → BEAR
    IF hh OR hl → BULL; IF lh OR ll → BEAR

# Fallback: linear slope on last 10 closes
slope_pct = (slope/mean)*100
IF slope_pct > 0.03 → BULL; IF < -0.03 → BEAR
ELSE → CFL
```

#### Stage 2: H4 Confirmation

**Input**: H4 bars before London open (07:00 UTC)
**Gate**: ≥5 H4 bars before cutoff
**Algorithm**: Price position vs range midpoint + 3-bar trend

```python
cutoff = day_ms + 7*3600*1000  # 07:00 UTC
recent = last 10 H4 bars before cutoff
highest = max(b.h); lowest = min(b.low); midpt = (highest+lowest)/2
last3_closes = [b.c for b in recent[-3:]]

IF macro == "BULL":
    confirmed = (cur > midpt) OR (last3[-1] >= last3[0])
IF macro == "BEAR":
    confirmed = (cur < midpt) OR (last3[-1] <= last3[0])
```

#### Stage 3: Session Narrative + Sweep Detection

**Input**: H1 + M15 bars, session windows (Asia 23:00-07:00, London 08:00-13:00)
**Gate**: Asia range ≥ 5 pts AND ≥ 2 H1 bars
**Algorithm**: Detect sweep of Asia H/L in London session

```python
# Sweep detection in London H1 bars
swept_high = any(b.h > asia_high)  →  swept_high_bar
swept_low  = any(b.low < asia_low)  →  swept_low_bar

# Double sweep = ambiguous → NO_NARRATIVE
IF swept_high AND swept_low → NO_NARRATIVE

# Single sweep + return = hunt pattern (aligned with macro)
IF swept_high AND returned_below_asia_high:
    IF macro == "BEAR" → "HUNT_PREV_HIGH" (aligned ✓)
    ELSE → "COUNTER_TREND" (skip)

IF swept_low AND returned_above_asia_low:
    IF macro == "BULL" → "HUNT_PREV_LOW" (aligned ✓) 
    ELSE → "COUNTER_TREND" (skip)
```

**Actionable narratives**: `HUNT_PREV_HIGH`, `HUNT_PREV_LOW`, `CONTINUATION`
**Skip narratives**: `NO_NARRATIVE`, `COUNTER_TREND`

#### Stage 4: M15 FVG Entry Model

**Input**: M15 bars 09:00-16:00 UTC, sweep price from Stage 3
**Algorithm**:

1. **Find FVG**: 3-bar gap (b0.h < b2.low for bull, b0.low > b2.h for bear)
   - Min size: max(1.0 pt, ATR14 × 0.15)
   - Proximity: |fvg_mid - sweep_price| < 200 pts
   - Select: largest candidate by size

2. **Find Entry**: Bar touches FVG then closes outside
   - Bull: bar.low ≤ fvg_high AND bar.c > fvg_high AND bar.c ≥ fvg_low
   - Bear: bar.h ≥ fvg_low AND bar.c < fvg_low AND bar.c ≤ fvg_high

3. **SL**: fvg_edge − fvg_size × 0.5 (proportional buffer)
4. **TP**: entry + risk × 3.0 (fixed 3:1 R:R)
5. **Gate**: R:R ≥ 2.5

### 2.3 Trade Management — Config F

**Philosophy**: Lock profits at 1R, trail remaining 50% to capture runners.

```
1. Position opens at entry_price with full size
2. When MFE ≥ 1R:
   - Close 50% position → lock +0.5R
   - Move SL to entry (breakeven)
3. When MFE ≥ 2R:
   - Trail SL = entry + (MFE - 1R)
4. Outcomes:
   - LOSS:         -1.0R (hit original SL before partial)
   - BE:           ~0.0R (partial taken, trail hit at entry)
   - PARTIAL_WIN:  0.5R + trail (partial taken, trail hit above entry)
   - WIN:          0.5R + 1.5R = 2.0R (both portions hit TP)
5. Max open time: 96 bars M15 (~24h)
```

**Proven results** (49-day backtest, 9 signals):

- Original fixed 3:1: NetR=+1.0R, MaxDD=5.0R, WR=29%
- Config F: NetR=+2.97R, MaxDD=1.0R, WR=75%

---

## 3. Module Design

### 3.1 File Layout (core/smc/ — pure logic, S0)

```
core/smc/
  tda/                          # NEW package
    __init__.py
    types.py                    # TdaStageResult, TdaSignal, TdaConfig, TradeState
    stage1_macro.py             # get_macro_direction(d1_bars) → MacroResult
    stage2_h4_confirm.py        # h4_confirmed(h4_bars, macro, day_ms) → bool
    stage3_session_narrative.py # get_session_narrative(h1, m15, macro, day_ms) → NarrativeResult
    stage4_fvg_entry.py         # find_fvg_entry(m15_bars, direction, sweep_price, atr) → EntryResult
    trade_management.py         # Config F: partial TP + trailing logic
    cascade.py                  # TdaCascade orchestrator: run_daily_cascade()
```

### 3.2 Runtime Wiring

```
runtime/smc/
  smc_runner.py                 # MODIFIED: add TDA cascade wiring
  tda_live.py                   # NEW: TdaLiveRunner — connects TDA cascade to bar events
  signal_journal.py             # MODIFIED: add TDA-specific journal fields
```

### 3.3 Key Types (core/smc/tda/types.py)

```python
@dataclass(frozen=True)
class MacroResult:
    direction: str          # "BULL" | "BEAR" | "CFL"
    method: str             # "pivot" | "slope"
    confidence: str         # "strong" | "moderate" | "weak"

@dataclass(frozen=True)
class SessionNarrative:
    narrative: str          # "HUNT_PREV_HIGH" | "HUNT_PREV_LOW" | "CONTINUATION" | ...
    asia_high: float
    asia_low: float
    sweep_direction: Optional[str]   # "BULL" | "BEAR"
    sweep_price: Optional[float]

@dataclass(frozen=True)
class FvgEntry:
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    direction: str          # "LONG" | "SHORT"
    fvg_high: float
    fvg_low: float
    fvg_size: float
    entry_bar_ms: int

@dataclass
class TradeState:
    """Mutable trade management state (Config F)."""
    signal_id: str
    entry: FvgEntry
    status: str             # "open" | "partial" | "closed"
    partial_closed: bool
    partial_r: float        # R locked from partial close
    max_favorable: float    # MFE in points
    trail_sl: float         # Current trailing SL price
    bars_elapsed: int
    
@dataclass(frozen=True)
class TdaSignal:
    """Complete TDA signal — replaces ADR-0039 SignalSpec."""
    signal_id: str
    symbol: str
    date_str: str           # "2026-01-09" 
    
    # Stage results
    macro_direction: str
    macro_method: str
    h4_confirmed: bool
    narrative: str
    sweep_price: Optional[float]
    
    # Entry
    entry: FvgEntry
    
    # Quality
    grade: str              # "A+" | "A" | "B" | "C"
    grade_score: int
    
    # Trade management (Config F)
    trade_state: TradeState
    
    # Lifecycle
    state: str              # "pending" | "active" | "partial" | "closed"
    outcome: str            # "" | "WIN" | "PARTIAL_WIN" | "LOSS" | "BE" | "OPEN"
    net_r: float            # Realized R
    
    created_ms: int
    updated_ms: int
```

### 3.4 Config SSOT (config.json)

Note: `smc.tda` вже зайнятий ADR-0034 (IFVG/Breaker). Cascade config → `smc.tda_cascade`.

```json
{
  "smc": {
    "tda_cascade": {
      "enabled": true,
      "macro_min_bars": 5,
      "macro_slope_threshold": 0.03,
      "h4_confirm_bars": 10,
      "h4_cutoff_hour_utc": 7,
      "asia_start_hour_utc": 23,
      "asia_end_hour_utc": 7,
      "london_start_hour_utc": 8,
      "london_end_hour_utc": 13,
      "entry_search_start_utc": 9,
      "entry_search_end_utc": 16,
      "fvg_min_atr_ratio": 0.15,
      "fvg_min_abs_pts": 1.0,
      "fvg_proximity_pts": 200,
      "sl_buffer_ratio": 0.5,
      "rr_target": 3.0,
      "min_rr": 2.5,
      "max_open_bars_m15": 96,
      "partial_tp_enabled": true,
      "partial_tp_at_r": 1.0,
      "partial_tp_pct": 0.5,
      "trail_start_r": 2.0,
      "asia_min_range_pts": 5,
      "asia_min_h1_bars": 2,
      "grade_enabled": true,
      "min_grade_for_entry": "C"
    }
  }
}
```

---

## 4. P-Slice Plan

| Slice | Scope | Files | LOC | Verify |
|-------|-------|-------|-----|--------|
| **P0** | Types + TDA config | `core/smc/tda/types.py`, `core/smc/tda/__init__.py`, `config.json` | ~80 | Import OK, types freeze |
| **P1** | Stage 1: D1 Macro | `core/smc/tda/stage1_macro.py`, `tests/test_tda_stage1.py` | ~120 | 10+ tests, determinism |
| **P2** | Stage 2: H4 Confirm | `core/smc/tda/stage2_h4_confirm.py`, `tests/test_tda_stage2.py` | ~80 | 8+ tests |
| **P3** | Stage 3: Session Narrative | `core/smc/tda/stage3_session_narrative.py`, `tests/test_tda_stage3.py` | ~130 | 12+ tests, sweep detection |
| **P4** | Stage 4: M15 FVG Entry | `core/smc/tda/stage4_fvg_entry.py`, `tests/test_tda_stage4.py` | ~120 | 10+ tests, entry model |
| **P5** | Trade Management | `core/smc/tda/trade_management.py`, `tests/test_tda_trade_mgmt.py` | ~100 | Config F proven scenarios |
| **P6** | Cascade Orchestrator | `core/smc/tda/cascade.py`, `tests/test_tda_cascade.py` | ~100 | Full cascade, 9-signal replay |
| **P7** | Runtime wiring | `runtime/smc/tda_live.py`, smc_runner.py mods | ~120 | Integration with bar events |
| **P8** | Config + old cleanup | config.json update, signals.py deprecation | ~30 | Exit gates pass |

**Invariant per slice**: `get_errors()` clean, tests pass, ≤3 files changed.

---

## 5. Інваріанти

| ID | Rule | How |
|----|------|-----|
| I0 | core/smc/tda/ = pure, NO I/O | Same as core/smc/ |
| S0 | No Redis/disk in cascade logic | All bar data passed as arguments |
| S2 | Deterministic: same bars → same signal | Pure functions, no time.time() |
| S5 | Config SSOT | All thresholds from config.json:smc.tda |
| NEW | Max 1 signal per day per symbol | Daily cascade design |
| NEW | Config F immutable after entry | entry/SL/TP locked; only trail_sl moves |

---

## 6. Migration Path

### 6.1 ADR-0039 → ADR-0040

| Component | Action |
|-----------|--------|
| `core/smc/signals.py` | Keep as-is (future removal). TDA cascade replaces its role. |
| `SignalSpec` type | Keep in types.py. TdaSignal is new primary type. |
| `smc_runner.get_signals()` | Delegated to TdaLiveRunner when tda.enabled=true |
| Signal journal | Reused, extended with TDA fields |
| WS frame `signals` field | Wire format updated: TdaSignal.to_wire() |
| Shell `signal` field | Updated to accept TdaSignal |
| Config `smc.signals` | Kept for backward compat; `smc.tda` takes priority |

### 6.2 Rollback

```bash
# config.json: set smc.tda.enabled = false
# This falls back to ADR-0039 zone-reactive signals
```

---

## 7. Consequences

### Позитивні

- **Proven**: 49-day backtest, +2.97R net, 1.00R MaxDD, 75% WR
- **Disciplined**: Max 1 signal/day = quality > quantity (0.75 signals/week)
- **Config F**: Partial TP at 1R dramatically reduces drawdown
- **Clear stages**: Each stage independently testable and tunable
- **Daily rhythm**: Aligns with how institutional traders operate

### Негативні

- New code (~750 LOC total across all slices)
- ADR-0039 signals become secondary/deprecated
- Different signal frequency: fewer signals, higher quality
- Requires D1 + H4 + H1 + M15 bars available (already are via UDS)

### Ризики

| Risk | Mitigation |
|------|-----------|
| Overfitting to 9 trades | Config F is universal principle; stage logic is ICT methodology |
| Missing signals when cascade too strict | `min_grade_for_entry: "C"` allows all grades; tighten later |
| D1/H4 bars delayed on cold start | Cascade waits for data; STAYOUT until stages confirm |

---

## 8. Acceptance Criteria

- [ ] All 4 stages pass unit tests with deterministic inputs
- [ ] Config F trade management passes all 9 historical scenarios
- [ ] Full cascade reproduces simulation's 9 signals on 49-day data
- [ ] Runtime wiring produces signals in WS frame
- [ ] Exit gates pass
- [ ] Old `smc.signals` still works when `tda.enabled=false`
