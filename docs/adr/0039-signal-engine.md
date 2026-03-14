# ADR-0039: Signal Engine — Numeric Entry/SL/TP + R:R + Alerts

- **Статус**: Proposed
- **Дата**: 2026-03-14
- **Автор**: R_ARCHITECT
- **Initiative**: `signal_engine_v1`
- **Пов'язані ADR**: ADR-0033 (Narrative Engine), ADR-0024 (SMC Engine), ADR-0029 (Confluence Scoring), ADR-0035 (Sessions & Killzones), ADR-0036 (Premium Shell — Proposed)

---

## 1. Контекст і проблема

### 1.1 Поточний стан

ADR-0033 (Narrative Engine) — **Implemented**. Платформа вже відповідає на три питання трейдера:

| Питання | Як відповідає | Формат |
|---------|---------------|--------|
| Q1: Торгувати чи чекати? | `NarrativeBlock.mode` — `"trade"` / `"wait"` | Badge + headline |
| Q2: Який сценарій? | `ActiveScenario` (zone_id, direction, trigger state) | Entry desc + trigger_desc |
| Q3: Де target / invalidation? | `target_desc`, `invalidation` (text) | "PDL 5062" / "Above 5230" |

### 1.2 Що відсутнє — "Decision to Execution" gap

Narrative дає **текстовий** контекст, але трейдер мусить **ручно** обчислювати числові параметри:

| Параметр | Статус | Хто обчислює зараз |
|----------|--------|--------------------|
| `entry_price` (точна ціна входу) | ❌ Відсутній | Трейдер вручну: середина зони або OTE |
| `stop_loss` (числовий SL) | ⚠️ Proxy | Invalidation = протилежний край зони (текст) |
| `take_profit` (числовий TP) | ⚠️ Proxy | Target = key level або swing (текст) |
| `risk_reward_ratio` (R:R) | ❌ Відсутній | Трейдер калькулятором |
| `confidence` (% впевненості) | ❌ Відсутній | Тільки grade A+/A/B/C |
| `alert` (intra-bar сповіщення) | ❌ Відсутній | Narrative оновлюється лише на bar close |
| Signal lifecycle | ❌ Відсутній | Scenario з'являється / зникає — без explicit state machine |

### 1.3 Failure Model

| # | Сценарій | Без Signal Engine | З Signal Engine |
|---|----------|-------------------|-----------------|
| F1 | A+ зона наближається | Трейдер дивиться narrative кожні 5 хв | Alert: "XAU/USD approaching OB A+(8) 2870. R:R 2.4:1" |
| F2 | Зона досягнута + структура підтверджена | Trigger state = `ready`, ні entry_price ні SL | Signal `READY`: entry=2872, SL=2865, TP=2890, R:R=2.57 |
| F3 | Ціна пробила invalidation | Scenario зникає, трейдер не помітив | Alert: "Signal INVALIDATED: price broke 2865" |
| F4 | Target досягнутий | Target_desc = "PDL 5062", трейдер стежить вручну | Alert: "Signal COMPLETED: target 5062 reached (+180pts)" |
| F5 | Mixed confidence (HTF↑ LTF↓) | sub_mode="reduced" (текст) | Confidence=62% з розбивкою: bias 80%, structure 70%, session 40% |
| F6 | Два сигнали на різних символах | Дивитись 2 вкладки | Signal Dashboard: ranked by confidence × R:R |

### 1.4 Evidence Pack

| Факт | Evidence |
|------|---------|
| NarrativeBlock не має числових entry/SL/TP | [VERIFIED `core/smc/types.py:289`] — тільки текстові entry_desc, target_desc, invalidation |
| Zone має price_high/price_low | [VERIFIED `core/smc/types.py:SmcZone`] — `price_high`, `price_low` = числові |
| Confluence score = 0–11 | [VERIFIED `core/smc/confluence.py`] — 8 факторів, grade A+/A/B/C |
| ATR вже обчислюється | [VERIFIED `core/smc/engine.py`] — `self._atr` available per TF |
| Session context є | [VERIFIED ADR-0035] — killzone, session H/L, F9 sweep |
| Key levels (PDH/PDL/DH/DL) є | [VERIFIED `core/smc/key_levels.py`] — числові ціни доступні |
| WS frame вже несе narrative | [VERIFIED `runtime/ws/ws_server.py`] — narrative field у full/delta frame |

---

## 2. Обмеження (Constraints)

### 2.1 Інваріанти

| ID | Вплив | Наслідок |
|----|-------|----------|
| I0 | Dependency Rule | Signal computation = `core/smc/signals.py` (pure). NO I/O. |
| I1 | UDS = вузька талія | Signal Engine = read-only overlay. Жодних UDS writes. |
| I4 | Один update-потік | Signals передаються через існуючий WS frame field. Без нового каналу. |
| I5 | Degraded-but-loud | Якщо signal computation неможливий → `warnings[]`, not silent None. |
| S0 | core/smc = pure | Жодного I/O у signal computation. |
| S2 | Determinism | Same input → same signals. |
| S5 | Config SSOT | Всі пороги / множники / параметри → `config.json:smc.signals`. |

### 2.2 Бюджетні обмеження

| Обмеження | Значення | Обґрунтування |
|-----------|----------|---------------|
| Computation budget | ≤ 10ms per bar (включається в `smc.max_compute_ms` = 50ms) | Signal = post-processing Narrative, не окремий engine cycle |
| Wire overhead | ≤ 500 bytes per signal | 1–2 signals × ~250 bytes кожен |
| Max active signals | 3 per symbol | Display budget: primary + alt + watchlist |
| Max alerts per minute | 5 | Anti-spam: intra-bar alerts rate-limited |

### 2.3 Scope Control — що НЕ входить

| Out of scope | Чому |
|-------------|------|
| Order execution (place/cancel orders) | Платформа = decision support, не execution engine |
| Position sizing / lot calculation | Залежить від account size, leverage, broker — зовнішній контекст |
| Trailing SL / scale-in/out | Потребує order management, не в scope v1 |
| Backtesting signals | Окремий initiative (ADR-0040+) |
| Cross-symbol portfolio risk | Потребує portfolio engine, не в scope v1 |
| Push notifications (mobile/email) | Потребує notification service, не в scope v1 |

---

## 3. Розглянуті варіанти

### 3.1 Alt A: Signal як розширення NarrativeBlock (обрано)

**Суть**: Додати `SignalSpec` dataclass поруч із `NarrativeBlock`. `synthesize_signals()` викликається после `synthesize_narrative()`, споживає NarrativeBlock + SmcSnapshot + zone data. Результат = список `SignalSpec`.

```
NarrativeBlock (existing)
    ↓ feeds into
SignalSpec[] (new — числовий entry/SL/TP/R:R/confidence + lifecycle state)
    ↓ wire
WS frame: {"signals": [...]}
    ↓
UI: Signal panel / alert toast / HUD indicator
```

**Плюси**:

- Мінімальний blast radius: один новий файл `core/smc/signals.py` + wire extension
- Використовує вже обчислені zone prices, confluence scores, ATR
- Narrative залишається незмінним (backward compatible)
- Signal lifecycle = state machine поверх existing trigger states

**Мінуси**:

- Два рівні абстракції (narrative + signal) — можлива плутанина
- Signal повторює деякі дані з ActiveScenario (entry zone, direction)

### 3.2 Alt B: Вбудувати числові поля прямо в ActiveScenario

**Суть**: Розширити `ActiveScenario` полями `entry_price`, `stop_loss`, `take_profit`, `risk_reward`.

**Плюси**:

- Один тип замість двох
- Менше дублювання

**Мінуси**:

- **Breaking change** wire format (ActiveScenario → всі UI компоненти треба оновити)
- Змішує "narrative context" і "actionable signal" — різні lifecycle
- ActiveScenario frozen, а signal lifecycle потребує state transitions
- Складніше відключити signals незалежно від narrative

### 3.3 Alt C: Окремий Signal Service (process)

**Суть**: Новий фоновий процес, що підписується на SmcSnapshot через Redis і генерує signals.

**Плюси**:

- Повна ізоляція від SMC compute
- Можливість горизонтального масштабування

**Мінуси**:

- **Порушує I4** (другий update-потік)
- Потребує нового Redis каналу
- Overengineering для 1–3 signals/symbol
- Додаткова латентність (Redis hop)

---

## 4. Рішення: Alt A — SignalSpec як розширення Narrative

### 4.1 Нові типи (`core/smc/types.py`)

```python
@dataclasses.dataclass(frozen=True)
class SignalSpec:
    """Числовий actionable signal для трейдера (ADR-0039).
    
    Lifecycle: pending → approaching → active → ready → 
               invalidated | completed | expired
    """
    signal_id: str            # Deterministic: "sig_{symbol}_{tf_s}_{zone_id}_{created_ms}"
    zone_id: str              # Source zone ID (same as ActiveScenario.zone_id)
    symbol: str
    tf_s: int                 # Viewer TF
    direction: str            # "long" | "short"
    
    # Числові рівні
    entry_price: float        # OTE (Optimal Trade Entry) або zone midpoint
    stop_loss: float          # Zone opposite edge ± ATR buffer
    take_profit: float        # Resolved from key levels / zones / swings
    risk_reward: float        # (TP - entry) / (entry - SL) для long, inverse для short
    
    # Entry method
    entry_method: str         # "ote" | "zone_edge" | "market" | "limit"
    entry_desc: str           # Human-readable: "OTE 61.8% of OB 2868–2878 = 2874.2"
    
    # Confidence
    confidence: int           # 0–100 composite score
    confidence_factors: dict  # {"bias_alignment": 90, "structure": 75, "session": 60, "momentum": 80}
    grade: str                # From confluence: "A+" | "A" | "B" | "C"
    
    # Lifecycle
    state: str                # "pending" | "approaching" | "active" | "ready" | "invalidated" | "completed" | "expired"
    state_reason: str         # "Structure confirmed in zone" | "Price broke invalidation"
    created_ms: int           # When signal was first generated
    updated_ms: int           # Last state transition
    
    # Context
    session: str              # "london" | "newyork" | "asia" | ""
    in_killzone: bool
    warnings: list            # ["mixed_htf_bias", "approaching_session_close"]
```

### 4.2 Entry Price Resolution

Пріоритет entry_price (конфігурується через `config.json:smc.signals.entry_method`):

| Method | Формула | Коли |
|--------|---------|------|
| `ote` (default) | Zone low + 0.618 × (zone high - zone low) для short; mirror для long | OTE = Optimal Trade Entry (Fibonacci 61.8% retracement of zone) |
| `zone_edge` | Zone high (short) / zone low (long) — агресивний вхід | Якщо зона тонка (< 0.5 ATR) |
| `zone_mid` | (zone high + zone low) / 2 | Fallback |

### 4.3 Stop Loss Resolution

```
SL_long  = zone_low  - atr * sl_buffer_atr  (config: default 0.2)
SL_short = zone_high + atr * sl_buffer_atr
```

ATR buffer запобігає stop hunt на краю зони. Конфігурується.

### 4.4 Take Profit Resolution

Пріоритет (перший знайдений):

1. **Key level** (PDH/PDL/DH/DL/session H/L) у напрямку сигналу
2. **HTF institutional zone** (A+/A grade) у напрямку
3. **Swing extreme** (last swing high/low)
4. **ATR multiple** (fallback): entry ± atr × tp_atr_multiplier (config: default 2.0)

Якщо жоден target не знайдено → `warnings: ["no_target_found"]`, TP = ATR fallback.

### 4.5 Confidence Score

Composite 0–100 з розбивкою по факторах:

| Фактор | Вага | 0 pts | 50 pts | 100 pts |
|--------|------|-------|--------|---------|
| `bias_alignment` | 30% | D1↔H4 conflict | One aligned | Both aligned |
| `structure` | 25% | No recent BOS/CHoCH | BOS in direction | CHoCH + BOS confirmation |
| `confluence_grade` | 20% | C (0–3) | B (4–5) | A/A+ (6–11) |
| `session` | 15% | Off-session | Session active | Killzone + direction match |
| `momentum` | 10% | No displacement | 1 displacement bar | 2+ displacement bars |

```
confidence = Σ (factor_score × weight)
```

### 4.6 Signal Lifecycle (State Machine)

```
                ┌──────────────────────────┐
                │        PENDING           │  Zone detected, no proximity
                └────────────┬─────────────┘
                             │ price within approach_atr_mult × ATR
                ┌────────────▼─────────────┐
                │       APPROACHING        │  Alert: "approaching zone"
                └────────────┬─────────────┘
                             │ price enters zone
                ┌────────────▼─────────────┐
                │         ACTIVE           │  In zone, awaiting structure
                └────────────┬─────────────┘
                             │ structure break confirmed (BOS/CHoCH aligned)
                ┌────────────▼─────────────┐
                │          READY           │  Entry condition met
                └──────┬─────────────┬─────┘
                       │             │
      price hits SL    │             │  price hits TP
   ┌───────────▼───┐   │   ┌────────▼────────┐
   │  INVALIDATED  │   │   │   COMPLETED     │
   └───────────────┘   │   └─────────────────┘
                       │
                       │  TTL expired (config: 50 bars)
              ┌────────▼────────┐
              │    EXPIRED      │
              └─────────────────┘
```

Transitions відбуваються в `update_signal_state()` на кожному bar close.

### 4.7 Alert Model

Signal Engine генерує alert-events при state transitions:

```python
@dataclasses.dataclass(frozen=True)
class SignalAlert:
    """Alert event для UI (toast/sound/badge). Read-only, ephemeral."""
    signal_id: str
    alert_type: str    # "approaching" | "active" | "ready" | "invalidated" | "completed"
    headline: str      # "XAU/USD: OB▲ A+(8) READY — entry 2874, R:R 2.4:1"
    priority: str      # "high" | "medium" | "low"
    ts_ms: int
```

Alerts передаються через WS frame field `signal_alerts: [...]` (ephemeral — надсилаються один раз, UI відповідає за відображення/dismissal).

### 4.8 Алгоритм `synthesize_signals()`

```python
def synthesize_signals(
    narrative: NarrativeBlock,
    snapshot: SmcSnapshot,
    zone_grades: Dict[str, dict],
    key_levels: List[SmcLevel],
    current_price: float,
    atr: float,
    config: dict,
    previous_signals: List[SignalSpec],  # для lifecycle continuity
    session_info: Optional[Tuple[str, bool]] = None,
) -> Tuple[List[SignalSpec], List[SignalAlert]]:
    """
    1. Map NarrativeBlock.scenarios → candidate signals
    2. Resolve entry/SL/TP per candidate (§4.2–4.4)
    3. Calculate R:R, confidence (§4.5)
    4. Match with previous_signals for lifecycle continuity
    5. Update states via state machine (§4.6)
    6. Generate alerts for state transitions (§4.7)
    7. Return (signals, alerts)
    """
```

### 4.9 Wire Format

```typescript
// ui_v4/src/types.ts (extension)
interface SignalSpec {
  signal_id: string;
  zone_id: string;
  symbol: string;
  tf_s: number;
  direction: 'long' | 'short';
  
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  risk_reward: number;
  
  entry_method: 'ote' | 'zone_edge' | 'zone_mid' | 'limit';
  entry_desc: string;
  
  confidence: number;           // 0–100
  confidence_factors: Record<string, number>;
  grade: string;
  
  state: 'pending' | 'approaching' | 'active' | 'ready' | 'invalidated' | 'completed' | 'expired';
  state_reason: string;
  created_ms: number;
  updated_ms: number;
  
  session: string;
  in_killzone: boolean;
  warnings: string[];
}

interface SignalAlert {
  signal_id: string;
  alert_type: string;
  headline: string;
  priority: 'high' | 'medium' | 'low';
  ts_ms: number;
}

// In SmcData frame extension:
interface SmcData {
  // ... existing fields
  signals?: SignalSpec[];
  signal_alerts?: SignalAlert[];
}
```

### 4.10 Config SSOT (`config.json:smc.signals`)

```json
{
  "smc": {
    "signals": {
      "enabled": false,
      "entry_method": "ote",
      "sl_buffer_atr": 0.2,
      "tp_atr_multiplier": 2.0,
      "approach_atr_mult": 1.5,
      "signal_ttl_bars": 50,
      "max_active_signals": 3,
      "min_risk_reward": 1.5,
      "confidence_weights": {
        "bias_alignment": 0.30,
        "structure": 0.25,
        "confluence_grade": 0.20,
        "session": 0.15,
        "momentum": 0.10
      },
      "alert_rate_limit_per_min": 5
    }
  }
}
```

> **K5 compliance**: `enabled: false` тому що ADR status = **Proposed**.

---

## 5. Blast Radius

### 5.1 Файли

| Файл | Зміна | Шар |
|------|-------|-----|
| `core/smc/types.py` | +`SignalSpec`, +`SignalAlert` dataclasses | core |
| `core/smc/signals.py` | **NEW** — `synthesize_signals()`, entry/SL/TP/confidence resolution, state machine | core |
| `runtime/smc/smc_runner.py` | Wire `synthesize_signals()` в `on_bar()` post-narrative | runtime |
| `runtime/ws/ws_server.py` | `signals` + `signal_alerts` fields у WS frame | runtime |
| `ui_v4/src/types.ts` | +`SignalSpec`, +`SignalAlert` interfaces | ui |
| `ui_v4/src/stores/` | Signal store (reactive state) | ui |
| `ui_v4/src/layout/` | Signal panel / alert toast (UI) | ui |
| `config.json` | `smc.signals` section | config |
| `tests/test_smc_signals.py` | **NEW** — unit tests | tests |

### 5.2 Процеси

| Процес | Вплив |
|--------|-------|
| `ws_server` | Додатковий field у frame, ~500 bytes/signal |
| SmcRunner `on_bar()` | +≤10ms computation budget |
| UI render | Signal panel overlay (behind ADR-0036 shell) |

---

## 6. P-Slices (план реалізації)

| Slice | Scope | Files | LOC est. | Verify |
|-------|-------|-------|----------|--------|
| **P0** | Types + Config | `core/smc/types.py`, `config.json` | ~60 | `get_errors()` clean, config parseable |
| **P1** | Core: `signals.py` — entry/SL/TP resolution + R:R + confidence | `core/smc/signals.py` | ~150 | Unit tests: entry resolution, R:R calc, confidence |
| **P2** | Core: lifecycle state machine | `core/smc/signals.py` | ~100 | Unit tests: state transitions, TTL, invalidation |
| **P3** | Core→Runtime: SmcRunner wiring + WS frame | `runtime/smc/smc_runner.py`, `runtime/ws/ws_server.py` | ~80 | Integration: signals in WS frame, backward compat |
| **P4** | UI: types + store + signal panel | `ui_v4/src/types.ts`, stores, layout | ~120 | Visual: signal renders in HUD |
| **P5** | UI: alerts (toast/badge) | `ui_v4/src/layout/` | ~80 | Visual: alert appears on state transition |

**Загалом**: ~590 LOC, 6 slices, ~3 нових файли.

---

## 7. Наслідки

### 7.1 Що змінюється

- Трейдер отримує числовий entry/SL/TP/R:R замість текстового опису
- Alert model дає intra-lifecycle сповіщення (не тільки bar close)
- Confidence score додає dimension до grade (A+ zone може мати 60% confidence якщо bias mixed)
- Signal lifecycle = explicit state machine (не implicit appearance/disappearance)

### 7.2 Що НЕ змінюється

- NarrativeBlock залишається as-is (backward compatible)
- SMC Engine compute pipeline незмінний
- Existing WS clients без signal support продовжують працювати (optional field)
- Config gate: `smc.signals.enabled=false` за замовчуванням

### 7.3 Ризики

| Ризик | Ймовірність | Мітигація |
|-------|-------------|-----------|
| Entry/SL/TP не відповідає "real" trader expectation | Medium | R_TRADER validation per slice. OTE = standard ICT entry. |
| Confidence score занадто synthetic | Medium | Weights конфігуруються. v1 = simple weighted average, v2 може стати ML. |
| Alert fatigue (занадто багато alerts) | Low | Rate limit 5/min. Only state transitions, not constant updates. |
| Stale signals (lifecycle не cleanup) | Low | TTL 50 bars + explicit EXPIRED state. |
| Wire overhead | Very Low | ~500B × 3 signals = 1.5KB. Negligible vs existing frame. |

---

## 8. Rollback

1. Set `config.json:smc.signals.enabled` → `false` (instant kill-switch)
2. SmcRunner skips `synthesize_signals()` call
3. WS frame omits `signals` / `signal_alerts` fields
4. UI renders without signal panel (graceful degradation)
5. If need to remove code: revert P5→P0 in reverse order

---

## 9. Зв'язок з ADR-0036 (Premium Shell)

ADR-0036 (Proposed) визначає **де** signals з'являються в UI:

- **Thesis bar** = shell stage derived from signal state (WAIT/PREPARE/READY/TRIGGERED)
- **Tactical strip** = entry + target + invalidation + R:R
- **Alert toast** = state transition notifications

Signal Engine (цей ADR) визначає **що** обчислюється backend-ом. Premium Shell визначає **як** це виглядає.

Послідовність: **ADR-0039 → P0–P3 (backend)** → **ADR-0036 → P4–P5 (UI integration)**.

---

## 10. Відкриті питання

| # | Питання | Варіанти | Хто вирішує |
|---|---------|----------|-------------|
| OQ-1 | Чи потрібен multi-symbol signal dashboard? | v1: per-symbol only. v2: dashboard | R_TRADER |
| OQ-2 | Чи confidence впливає на display budget? | v1: display budget = grade only. v2: confidence threshold | R_SMC_CHIEF |
| OQ-3 | Чи потрібен sound alert? | v1: visual only. v2: optional sound | R_CHART_UX |
| OQ-4 | Entry method selection: auto per zone width чи trader preference? | v1: config global. v2: per-signal override | R_TRADER |
