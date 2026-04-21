# ROADMAP M3 → M4 → M5 — Trading Platform v3 + Арчі

> **Статус**: Living document. Затверджений Owner 2026-04-19.
> **Ціль**: закрити питання "що далі" назавжди. Кожен strategic ADR має ref сюди.
> **Reviewer**: Owner. **Sync**: щомісячний R_ELEVATOR audit.

---

## 0. Де ми зараз (M3 — Multi-TF SMC + Confluence Scoring)

**Підтверджено артефактами**:

| Capability | Evidence |
|-----------|----------|
| Swings, BOS/CHoCH, OB, FVG, Liquidity, P/D | `core/smc/` — 15+ modules, S0-S6 invariants |
| Multi-TF cascade D1→H4→H1→M15→M5 | ADR-0002 DeriveChain, ADR-0040 TDA |
| Confluence scoring (8 factors, A+/A/B/C) | `core/smc/confluence.py`, ADR-0029 |
| Signal Engine (entry/SL/TP specs) | ADR-0039, `core/smc/signals.py` |
| Narrative Engine (trade/wait modes) | ADR-0033, `core/smc/narrative.py` |
| AI Agent з автономією (Арчі) | trader-v3, 49 ADR, I7 invariant |
| Wake Engine ($0 platform-side) | ADR-0048/0049, `runtime/smc/wake_engine.py` |
| Market Reading (OHLCV → arithmetic analysis) | ADR-043 G6, `market_reader.py` + `chart_reader.py` |
| Enrichment (DXY, US10Y, SPX, news, calendar) | `bot/enrichment/market_data.py`, `news_feed.py` |
| Workspace (self-managed agent desk) | ADR-044, deployed |

**Що НЕ рахується M3**: Арчі (trader-v3) — це вже M6-territory по ladder, але platform ≠ agent. Platform = M3, Agent = окрема вісь.

---

## 1. M3 Consolidation (перед стрибком на M4)

**Принцип**: не можна будувати M4 на нестабільному M3. Закриваємо техборг.

| ID | Gap | Що робити | LOC | Priority |
|----|-----|-----------|-----|----------|
| C1 | Workspace adoption слабке | ✅ Зроблено 2026-04-19: prompt triggers, morning briefing КРОК 5, sleep ritual КРОК 5 | — | Done |
| C2 | Chat mobile UX — input unreachable | ✅ Зроблено 2026-04-19: chat-context-rail з max-height 40vh | — | Done |
| C3 | ADR-044 Phase 3 — Session Awareness | Owner presence key, heartbeat, bot reads last_seen | ~120 | Low |
| C4 | Mechanical.py refactor | ObservationRouter cleanup post-Haiku-gate removal | ~80 | Low |
| C5 | Test coverage gaps | 4 test files need aiogram mock або skip | ~40 | Low |

**Exit criteria M3-consolidation**: C1+C2 done (вже). C3-C5 = nice-to-have, не блокують M4.

---

## 2. M4 — Market Regime Engine

> **Signal**: "система не просто бачить зони — вона розуміє в якому ми ринку"
> **Estimated effort**: 6-10 focused sessions (R3+ patches)

### 2.1 What is Regime?

Ринок завжди в одному з режимів. Той самий H4 FVG означає різне в trending vs ranging market:

```
TRENDING_UP   — higher highs, higher lows, strong displacement
TRENDING_DOWN — lower highs, lower lows, strong displacement
RANGING       — price oscillates between support/resistance, no clear direction
SQUEEZE       — ATR contracting, Bollinger bands narrowing → breakout incoming
EXPANSION     — ATR expanding after squeeze, big candles
DISTRIBUTION  — after trend, volume shifts, smart money exits
```

### 2.2 ADR Plan

| ADR | Title | Layer | Depends on | P-slices |
|-----|-------|-------|-----------|----------|
| **ADR-052** | Regime Types + Classification | `core/smc/` | — | P1: types, P2: classifier, P3: tests |
| **ADR-053** | Volatility Regime (ATR-based) | `core/smc/` | ADR-052 | P1: calculator, P2: regime bands, P3: tests |
| **ADR-054** | Adaptive Confluence (regime-weighted) | `core/smc/` | ADR-052+053 | P1: weight matrix, P2: scoring refactor, P3: tests |
| **ADR-055** | Correlation Context Engine | `core/smc/` + enrichment | ADR-052 | P1: correlation types, P2: analyzer, P3: threshold config |
| **ADR-056** | Regime → Narrative Integration | `core/smc/` | ADR-052+054 | P1: narrative consumes regime, P2: regime badge UI |

### 2.3 Architecture

```
┌─────────────────────────────────────────────────────┐
│  core/smc/                                          │
│                                                     │
│  regime_types.py    ← MarketRegime enum + state     │
│  regime_classifier.py ← pure logic, NO I/O          │
│  volatility.py      ← ATR-based vol regime          │
│  correlation.py     ← cross-asset analysis          │
│                                                     │
│  confluence.py      ← MODIFIED: regime-aware weights│
│  narrative.py       ← MODIFIED: consumes regime     │
│  signals.py         ← MODIFIED: regime filter       │
└─────────────┬───────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────┐
│  runtime/smc/smc_runner.py                          │
│                                                     │
│  SmcRunner.tick() ← regime_classifier.classify()    │
│                     called AFTER structure/zones     │
│                     BEFORE narrative/signals         │
│                                                     │
│  SmcSnapshot ← +regime: MarketRegime                │
│               +volatility_regime: VolRegime          │
│               +correlation_health: dict              │
└─────────────┬───────────────────────────────────────┘
              │
              ▼  render_frame  
┌─────────────────────────────────────────────────────┐
│  UI: regime badge + vol indicator + session context  │
└─────────────────────────────────────────────────────┘
```

### 2.4 Key Decisions (pre-resolved)

| Decision | Choice | Why |
|----------|--------|-----|
| Regime source | OHLCV only (price action) | $0, no external deps. ATR + structure + displacement = sufficient |
| Regime per TF or global? | Per-symbol, **D1 is primary** with H4 confirmation | D1 regime = strategic, H4 = tactical. Conflict = "mixed" regime |
| Hysteresis | Min 3 bars to confirm regime change | Prevents whipsaw on single candle |
| Config SSOT | `config.json → regime` section | Consistent з I0 |
| S0 compliance | Regime = read-only overlay, NOT stored in UDS | Ephemeral like all SMC analysis |

### 2.5 Concrete Deliverables

**ADR-052: Regime Types + Classification** (START HERE)

New files:
- `core/smc/regime_types.py` (~60 LOC): `MarketRegime` enum, `RegimeState` dataclass (regime, confidence, bars_held, changed_at)
- `core/smc/regime_classifier.py` (~120 LOC): `classify(bars, structure_events) → RegimeState`
  - Logic: count HH/HL vs LH/LL in last N bars
  - ATR expansion/contraction for squeeze/expansion detection
  - Use existing `structure.py` BOS/CHoCH events as inputs
- `tests/test_regime_classifier.py` (~150 LOC): trending, ranging, squeeze, transition cases

**ADR-053: Volatility Regime**

New files:
- `core/smc/volatility.py` (~80 LOC): `VolRegime` enum (LOW/NORMAL/HIGH/EXTREME), `classify_volatility(bars, lookback) → VolRegime`
  - ATR percentile vs rolling window
  - Bollinger width as secondary confirmation
- Config addition: `regime.volatility_lookback`, `regime.vol_thresholds`

**ADR-054: Adaptive Confluence**

Modified files:
- `core/smc/confluence.py`: weight matrix keyed by `(regime, vol_regime)`
  - TRENDING + NORMAL → standard weights
  - RANGING + LOW → lower structure weight, higher OB/FVG weight
  - SQUEEZE → all weights reduced (no-trade bias)
  - EXPANSION → momentum weight boosted

**ADR-055: Correlation Context**

- `core/smc/correlation.py` (~100 LOC): `analyze_correlations(xau_bars, dxy_bars, us10y_bars) → CorrelationSnapshot`
- Consumes enrichment data that already exists in `market_data.py`
- Output: correlation direction, strength, divergence flag

**ADR-056: Regime → Narrative**

- `narrative.py` modification: inject regime into `NarrativeBlock.mode` and `.headline`
- "Trending market — follow the bias" vs "Ranging — fade the extremes"
- UI regime badge in header bar

### 2.6 Exit Criteria M4

```
[ ] RegimeClassifier deployed, tested (ADR-052)
[ ] VolatilityRegime deployed, tested (ADR-053)  
[ ] Confluence weights regime-adaptive (ADR-054)
[ ] Correlation analysis live (ADR-055)
[ ] Narrative consumes regime (ADR-056)
[ ] SmcSnapshot includes regime in render_frame
[ ] UI shows regime badge
[ ] Арчі бачить regime у context (read_market returns regime)
[ ] Min 20 тестів на regime edge cases
```

---

## 3. M5 — Market State Understanding

> **Signal**: "система формулює thesis на рівні junior/mid trader"
> **Depends on**: M4 complete (regime classification must be stable)
> **Estimated effort**: 8-15 focused sessions

### 3.1 What is Market State?

M4 = окремі pieces (regime, vol, correlation). M5 = **unified understanding**:

```python
@dataclass
class MarketState:
    regime: RegimeState          # from M4
    volatility: VolRegime        # from M4
    correlation: CorrelationSnap # from M4
    
    # NEW in M5:
    fundamental_sentiment: Sentiment  # bullish/bearish/neutral + confidence
    calendar_risk: CalendarRisk       # clear/low/high/critical
    session_phase: SessionPhase       # asia_range/london_drive/ny_overlap/ny_afternoon
    coherence_score: float            # 0-1, how aligned are all signals
    conflict_flags: list[str]         # "macro bullish but structure bearish"
    
    thesis: str                       # 1-sentence market read
    recommended_posture: Posture      # AGGRESSIVE / NORMAL / CAUTIOUS / AVOID
```

### 3.2 ADR Plan

| ADR | Title | Layer | Depends on |
|-----|-------|-------|-----------|
| **ADR-057** | MarketState Aggregate Type | `core/smc/` | M4 complete |
| **ADR-058** | Fundamental Sentiment Engine | `core/smc/` + enrichment | ADR-057 |
| **ADR-059** | Calendar-Aware Decision Layer | `core/smc/` + enrichment | ADR-057 |
| **ADR-060** | Coherence Scoring + Conflict Detection | `core/smc/` | ADR-057+058+059 |
| **ADR-061** | read_market v2 — Market State Context | trader-v3 | ADR-057 |
| **ADR-062** | Agent Context Injection — MarketState → Prompt | trader-v3 | ADR-061 |

### 3.3 What Changes for Арчі (trader-v3 side)

**read_market tool enhancement** (ADR-061):
```
read_market(symbol='XAU/USD', tf='H4', limit=20)

CURRENT response:
  bars: [{open, high, low, close, volume, candle_type, ...}]
  structure: [{type: "BOS_BULL", ...}]
  trend: "bullish"

M5 response ADDS:
  regime: "TRENDING_UP (14 bars, 0.85 confidence)"  
  volatility: "NORMAL (ATR 25.3, percentile 55%)"
  correlation: "DXY inverse -0.72 (strong), US10Y -0.45 (moderate)"
  calendar: "FOMC 19:00 UTC — HIGH IMPACT, avoid entry 18:00-20:00"
  market_state: "Bullish trend, normal vol, aligned correlations. NORMAL posture."
  conflicts: []
```

**Prompt enhancement** (ADR-062):
- Morning briefing КРОК 1 returns MarketState block
- Арчі reads it and incorporates into workspace briefing
- Observation prompt includes MarketState delta ("що змінилось з останнього разу")

### 3.4 What M5 Does NOT Include (M6 territory)

- Арчі making autonomous trade decisions (that's M6)
- Position sizing based on regime (M6)
- Real money execution (M6+)
- UI with trade management buttons (M7)

M5 = **understanding**. M6 = **acting on understanding**.

### 3.5 Exit Criteria M5

```
[ ] MarketState dataclass deployed (ADR-057)
[ ] Fundamental sentiment scoring live (ADR-058)
[ ] Calendar risk gating works (ADR-059)
[ ] Coherence score + conflict flags (ADR-060)
[ ] read_market v2 returns MarketState (ADR-061)
[ ] Арчі prompt receives MarketState (ADR-062)
[ ] Morning briefing references regime + sentiment
[ ] Workspace briefing includes "market posture" field
[ ] Min 30 тестів на MarketState integration
[ ] Арчі correctly says "avoid trades" when coherence < 0.3
```

---

## 4. Timeline (realistic)

```
2026 Q2 (Apr-Jun):
  ✅ M3 consolidation (workspace, UX fixes) — DONE
  🎯 ADR-052 (Regime Types) — START
  🎯 ADR-053 (Volatility Regime)
  🎯 ADR-054 (Adaptive Confluence)

2026 Q3 (Jul-Sep):
  🎯 ADR-055 (Correlation Context)
  🎯 ADR-056 (Regime → Narrative)
  ✅ M4 EXIT CRITERIA MET
  🎯 ADR-057 (MarketState Aggregate)

2026 Q4 (Oct-Dec):
  🎯 ADR-058 (Fundamental Sentiment)
  🎯 ADR-059 (Calendar Decision Layer)
  🎯 ADR-060 (Coherence + Conflicts)
  🎯 ADR-061+062 (read_market v2 + Agent Context)
  ✅ M5 EXIT CRITERIA MET

2027 Q1+:
  M6 territory — autonomous decision-making
```

**Caveat**: timeline = aspirational. Actual pace = constrained by API budget, Стас availability, rate limits. Кожен ADR = 1-3 focused sessions.

---

## 5. Dependencies Map

```
M3 (done)
  │
  ├── ADR-052: Regime Types ──────┐
  │                                │
  ├── ADR-053: Volatility ────────┤
  │                                │
  │   ADR-054: Adaptive Confl. ◄──┘ (needs 052+053)
  │   ADR-055: Correlation ◄──────── (needs 052)
  │   ADR-056: Regime→Narrative ◄─── (needs 052+054)
  │                                │
  │   ═══ M4 EXIT ════════════════╪═══
  │                                │
  ├── ADR-057: MarketState ◄──────┘ (needs M4)
  │                                │
  ├── ADR-058: Fundamental ◄──────── (needs 057)
  ├── ADR-059: Calendar Gate ◄────── (needs 057)
  │                                │
  │   ADR-060: Coherence ◄────────── (needs 057+058+059)
  │   ADR-061: read_market v2 ◄───── (needs 057)
  │   ADR-062: Agent Context ◄────── (needs 061)
  │                                │
  │   ═══ M5 EXIT ════════════════╪═══
  │                                │
  └── M6: Autonomous Agent ◄──────┘ (needs M5)
```

---

## 6. Autonomy Improvements (crosscutting, not maturity-gated)

Ці пункти покращують Арчі незалежно від M-level:

| ID | What | Impact | Status |
|----|------|--------|--------|
| A1 | Workspace workflow triggers | Арчі активно використовує стіл | ✅ Done 2026-04-19 |
| A2 | Morning briefing → workspace items | Кожен ранок = briefing + levels + scenarios на столі | ✅ Done 2026-04-19 |
| A3 | Sleep ritual workspace cleanup | Чистий стіл вранці | ✅ Done 2026-04-19 |
| A4 | Self-review cycle | daily_review перевіряє workspace predictions vs fact | Backlog |
| A5 | Observation → workspace note | Якщо insight під час observation → note на стіл | Backlog |
| A6 | Regime-aware wake conditions | Wake on regime change (M4 prereq) | Blocked by M4 |
| A7 | MarketState in morning briefing | Арчі бачить повний стан ринку, не збирає по частинах | Blocked by M5 |

---

## 7. Anti-patterns (reminder from SYSTEM_MATURITY_LADDER.md)

- ❌ Робити M4+M5 одночасно → focus collapse
- ❌ Скіпати M4 → narrative без regime = hallucination
- ❌ Claim'ити M4 без тестів і deployed ADR → self-deception
- ❌ "Ще один indicator" без зв'язку з roadmap → M3 plateau
- ❌ Красивий UI (M7) замість intelligence (M4) → порожня обгортка

---

## 8. Порівняння з конкурентами

| | Наша платформа | TradingView | MetaTrader | QuantConnect |
|---|---|---|---|---|
| **Chart + Pipeline (M2)** | Sufficient | World-class | Good | N/A (no chart) |
| **SMC Intelligence (M3)** | ✅ Deployed | ❌ None | ❌ None | ❌ None |
| **Regime Engine (M4)** | 🎯 Next | ❌ None | ❌ None | Partial (Pine/Python) |
| **Market State (M5)** | 📋 Planned | ❌ None | ❌ None | Manual only |
| **Autonomous Agent (M6)** | ⏳ Арчі exists, needs M5 data | ❌ None | EA (primitive) | Algo (no personality) |
| **Product Polish (M7)** | WIP | ✅ World-class | Dated | Dev-only |

**Наша перевага**: M3-M6 axis. Жоден конкурент не має autonomous AI agent з personality + SMC intelligence + workspace self-management. TradingView = M2+M7, ми = M3+M6(partial). Після M4+M5 ми будемо в унікальній позиції.

---

> **Цей документ закриває питання "що далі".**
> Наступний крок: ADR-052 (Regime Types + Classification).
> Коли починати: коли Owner скаже "го".
