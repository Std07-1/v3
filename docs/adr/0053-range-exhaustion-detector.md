# ADR-0053: Range Exhaustion Detector — ATR-based Daily/Session Travel Gauge

- **Status**: Accepted
- **Date**: 2026-04-24
- **Author**: Стас
- **Initiative**: `range_exhaustion_v1`
- **Related ADRs**:
  - ADR-0024 (SMC Engine — core primitives)
  - ADR-0023 (D1 Live Derive from M1 — anchor 79200s)
  - ADR-0029 (Confluence Scoring — consumer)
  - ADR-0033 (Context Flow Narrative — consumer)
  - ADR-0035 (Sessions & Killzones — session anchors)
  - ADR-0039 (Signal Engine — consumer)
- **Cross-ref**: `trader-v3/` consumes via WS wire frame (не пише)
- **Quality Axes**: Ambition target R2 (нова pure-compute фіча з широким consumer reach). Maturity impact: M3 (consolidates SMC stack with volatility layer).

---

## 1. Контекст і проблема

### 1.1 Що є зараз

v3 SMC engine обчислює **зони** (OB, FVG), **structure** (BOS/CHoCH), **liquidity** (PDH/PDL, session H/L), **bias** (multi-TF trend), **narrative** (scenarios), **signals** (entry/SL/TP). Все це — "ЩО відбувається у структурі".

Чого **нема**: відповіді на запитання **"скільки сил ще залишилось у цього руху"**. Тобто — трейдер бачить валідну D1 bearish OB зону, ціна підходить знизу, confluence high, але:

- Якщо XAU/USD вже пройшов сьогодні 120 pips (≈1.1×ATR14 на D1) — **рух вичерпаний**, ймовірність продовження вниз від зони ↓ суттєво
- Якщо пройшов 30 pips (≈0.3×ATR) — **рух тільки починається**, imbalance ще далеко, setup якісний
- Якщо пройшов 70 pips (≈0.7×ATR) — middle ground, confluence треба дивитись акуратніше

Цей показник **не є SMC primitive** (не зона, не structure event) — це **volatility/momentum gauge**. Але він критичний для:

- **Confluence scoring** (ADR-0029) — late phase = knock-down confluence bonus
- **Signal Engine** (ADR-0039) — exhausted = suppress new entries в напрямку руху
- **Narrative** (ADR-0033) — "ціна вже сходила 1.1 ATR, чекаємо pullback" як рядок сценарію
- **Trader-v3 Арчі** — rational для "не беру зараз" без того щоб агент це вигадував

### 1.2 Чому тепер

Три тригери:

1. **XAU/USD фокус** (основний символ платформи) — gold має сильний daily rhythm:
   impulse до ~1.0–1.3 ATR, далі consolidation або reversal. Без gauge Арчі
   періодично рекомендує setup на exhausted moves — трейдер каже "вже пізно",
   але code-level обґрунтування відсутнє
2. **Confluence calibration drift** (спостережене 2026-04) — deep retraces все одно отримують grade A+ бо confluence не знає що рух вичерпався
3. **Narrative credibility** — премʼєм-ціль платформи (ADR-0036, ADR-0048 Narrative Enricher) вимагає щоб сценарії звучали як професійна аналітика, а не як bare "price in zone"

### 1.3 Формалізація

Дано символ `s`, поточну ціну `P`, список anchor reference points `A = {d1_open, london_open, ny_open, week_open}`. Для кожного `a ∈ A` обчислити:

```
traveled_abs   = |P - a.price|
traveled_dir   = sign(P - a.price)     # +1 bull, -1 bear
atr_baseline   = ATR(14) on reference TF  (D1 для d1_open/week_open, H1 для session_*)
traveled_mult  = traveled_abs / atr_baseline
phase          = classify(traveled_mult, thresholds)
remaining      = max(0.0, exhaustion_cap - traveled_mult)
```

де `thresholds` та `exhaustion_cap` — з config.json (SSOT).

---

## 2. Альтернативи

### Alt A: Pure ATR(14) travel з D1 open — simplest

- ✅ Знайомий трейдерам (industry standard, "ADR meter" у TradingView)
- ✅ Детермінізм, легко калібрувати
- ✅ `compute_atr()` вже є (core/smc/swings.py:102)
- ❌ Один anchor (D1 open) — нечутливий до session-level exhaustion (London vs NY)
- ❌ Не враховує gap-open дні (понеділок після weekend gap)

### Alt B: Multi-anchor travel (D1 + session + week) — обрана

- ✅ Багатший сигнал: можна ловити "D1 ще не exhausted, але London вже — чекаємо NY"
- ✅ Композиційно — Narrative може вибрати найбільш inform anchor
- ✅ Gold та major FX мають сильну session segmentation — природний fit
- ✳️ +CPU: три ATR паралельно, але все одно O(14×3) на тік — тривіально
- ❌ Більше конфігурації (6 threshold групп)

### Alt C: Regression-based Expected Daily Range (IV-style)

- ✅ Точніший forecast (враховує day-of-week, session seasonality)
- ❌ Black-box для трейдера, важко пояснити
- ❌ Потребує training pipeline, offline recalibration, monitoring drift
- ❌ Rollback складний (модель embedded)

### Alt D: Percentile-based (std dev of daily returns, Bollinger-style)

- ✅ Статистично обґрунтовано
- ❌ Для трейдера менш інтуїтивно (mult of ATR — легше читається)
- ❌ Вимагає більшого вікна (>60 днів) → warmup painful для virgin symbols

**Обрано Alt B**. Причини: максимальний reach за мінімум коду, composable з існуючими anchors (ADR-0023 + ADR-0035), explainable.

---

## 3. Рішення

### 3.1 Архітектура

Новий модуль **`core/smc/range_exhaustion.py`** — pure compute, no I/O:

```
D1 bars (UDS read) ──┐
H1 bars (UDS read) ──┼─► compute_range_exhaustion(bars, anchors, cfg)
current price  ──────┤                     │
session context ─────┘                     ▼
                              RangeExhaustionState (per anchor)
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
              Confluence             Narrative            Signal Engine
              (cap bonus)          (phase phrase)        (suppress entry)
                                           │
                                           ▼
                                     Wire frame ──► UI + trader-v3
```

Жорстко read-only: **ніколи не пише в UDS** (S1 compliance, як весь SMC overlay).

### 3.2 Types (`core/smc/types.py` extension)

```python
@dataclass(frozen=True)
class RangeExhaustionState:
    """ATR-based travel gauge для одного reference anchor."""
    anchor_kind: Literal["d1_open", "london_open", "ny_open", "week_open"]
    anchor_ms: int
    anchor_price: float
    current_price: float
    traveled_abs: float           # price units
    traveled_dir: int             # +1 bull, -1 bear, 0 flat
    atr_baseline: float           # ATR(14) on reference TF
    traveled_mult: float          # key number: traveled_abs / atr_baseline
    phase: Literal["early", "mid", "late", "exhausted"]
    remaining_budget: float       # max(0, exhaustion_cap - traveled_mult)
    confidence_delta: float       # suggested score adjustment (-0.3..0.0)
    degraded: list[str]           # reasons if compute недостатнє (I5)


@dataclass(frozen=True)
class RangeExhaustionSnapshot:
    """Set of states по всіх anchors для символу — те що йде у wire frame."""
    symbol: str
    primary: RangeExhaustionState        # обраний best anchor (D1 за замовчуванням)
    by_anchor: dict[str, RangeExhaustionState]
    computed_at_ms: int
```

### 3.3 Алгоритм (core compute)

```python
def compute_range_exhaustion(
    bars_d1: list[CandleBar],
    bars_h1: list[CandleBar],
    current_price: float,
    session_context: SessionContext,  # ADR-0035
    cfg: RangeExhaustionConfig,
) -> RangeExhaustionSnapshot:
    now_ms = int(time.time() * 1000)
    states: dict[str, RangeExhaustionState] = {}

    # D1 open (anchor 79200s — ADR-0023)
    d1_bar = _latest_forming_bar(bars_d1)  # current D1 candle (complete OR forming)
    if d1_bar is None:
        states["d1_open"] = _degraded("no_d1_bar")
    else:
        atr_d1 = compute_atr(bars_d1, period=cfg.atr_period)  # reuse swings.py
        states["d1_open"] = _build_state(
            anchor_kind="d1_open",
            anchor_ms=d1_bar.open_ms,
            anchor_price=d1_bar.o,
            current_price=current_price,
            atr_baseline=atr_d1,
            cfg=cfg,
        )

    # London open (07:00 UTC — ADR-0035)
    if session_context.london_open_ms and session_context.london_open_price:
        atr_h1 = compute_atr(bars_h1, period=cfg.atr_period) * cfg.session_atr_scale
        states["london_open"] = _build_state(
            anchor_kind="london_open",
            anchor_ms=session_context.london_open_ms,
            anchor_price=session_context.london_open_price,
            current_price=current_price,
            atr_baseline=atr_h1,
            cfg=cfg,
        )

    # NY open (12:00 UTC) — той самий патерн
    # Week open (Sunday 22:00 UTC) — той самий патерн з H4/D1 baseline

    primary = _select_primary(states, session_context, cfg)
    return RangeExhaustionSnapshot(
        symbol=session_context.symbol,
        primary=primary,
        by_anchor=states,
        computed_at_ms=now_ms,
    )


def _build_state(*, anchor_kind, anchor_ms, anchor_price,
                 current_price, atr_baseline, cfg) -> RangeExhaustionState:
    traveled_abs = abs(current_price - anchor_price)
    traveled_dir = 1 if current_price > anchor_price else (-1 if current_price < anchor_price else 0)
    traveled_mult = traveled_abs / max(atr_baseline, 1e-9)

    # Phase classification (config-driven)
    t = cfg.phase_thresholds  # dict {early_max, mid_max, late_max}
    if traveled_mult < t.early_max:
        phase = "early"
    elif traveled_mult < t.mid_max:
        phase = "mid"
    elif traveled_mult < t.late_max:
        phase = "late"
    else:
        phase = "exhausted"

    # Confidence delta: negative у late/exhausted, 0 у early/mid
    confidence_delta = cfg.confidence_delta_map.get(phase, 0.0)

    return RangeExhaustionState(
        anchor_kind=anchor_kind,
        anchor_ms=anchor_ms,
        anchor_price=anchor_price,
        current_price=current_price,
        traveled_abs=traveled_abs,
        traveled_dir=traveled_dir,
        atr_baseline=atr_baseline,
        traveled_mult=round(traveled_mult, 3),
        phase=phase,
        remaining_budget=max(0.0, cfg.exhaustion_cap - traveled_mult),
        confidence_delta=confidence_delta,
        degraded=[],
    )
```

### 3.4 Config (`config.json` SSOT)

```json
{
  "smc": {
    "range_exhaustion": {
      "enabled": true,
      "atr_period": 14,
      "session_atr_scale": 6.0,
      "phase_thresholds": {
        "early_max": 0.35,
        "mid_max":   0.70,
        "late_max":  1.00
      },
      "exhaustion_cap": 1.50,
      "confidence_delta_map": {
        "early":      0.00,
        "mid":        0.00,
        "late":      -0.15,
        "exhausted": -0.30
      },
      "primary_anchor_rules": {
        "asia_session":   "d1_open",
        "london_session": "london_open",
        "ny_session":     "ny_open",
        "weekend":        "week_open"
      }
    }
  }
}
```

**Важливо**: значення — стартові, підбираються на реальних XAU/USD даних. Calibration окремий P-slice після MVP.

### 3.5 Інтеграція у SmcRunner

```python
# runtime/smc/smc_runner.py (extend існуючий compute)
class SmcRunner:
    def compute(self, symbol: str, tf_s: int) -> SmcSnapshot:
        # ... existing ...
        range_snap = None
        if self.cfg.range_exhaustion.enabled:
            try:
                range_snap = compute_range_exhaustion(
                    bars_d1=self._bars(symbol, 86400, n=30),
                    bars_h1=self._bars(symbol, 3600, n=48),
                    current_price=current_price,
                    session_context=self._session_ctx(symbol),
                    cfg=self.cfg.range_exhaustion,
                )
            except Exception as e:
                # I5 degraded-but-loud — НЕ silent pass
                logger.warning("range_exhaustion.compute_failed symbol=%s err=%s", symbol, e)
                range_snap = None

        return SmcSnapshot(
            # ... existing fields ...
            range_exhaustion=range_snap,
        )
```

### 3.6 Wire frame shape

Додається у `SmcSnapshot` → серіалізується як:

```json
{
  "range_exhaustion": {
    "primary": {
      "anchor_kind": "d1_open",
      "traveled_mult": 0.82,
      "traveled_dir": 1,
      "phase": "late",
      "remaining_budget": 0.68,
      "confidence_delta": -0.15
    },
    "by_anchor": { "d1_open": {...}, "london_open": {...}, "ny_open": {...} }
  }
}
```

Free tier: тільки `primary.phase` + `primary.traveled_mult` (для UI pill).
Premium (ADR-0048 tier gate): вся структура `by_anchor`.

### 3.7 Consumer wiring

| Consumer | Як використовує | Файл |
|---|---|---|
| **Confluence scorer** | `final_score += state.confidence_delta` для setups у напрямку `traveled_dir` | `core/smc/confluence.py` |
| **Signal Engine** | Якщо `primary.phase == "exhausted"` І setup_dir == traveled_dir → `lifecycle = "suppressed"` замість "pending" | `core/smc/signals.py` |
| **Narrative** | Додати у scenario text: "D1 пройшов 0.82 ATR (late phase) — чекаємо pullback" | `core/smc/narrative.py` |
| **UI pill** | Маленький badge у Trader-Bar: "D1 0.82×" у amber якщо late, red якщо exhausted | `ui_v4/src/layout/TraderBar.svelte` |
| **Trader-v3 Арчі** | Читає з wire frame у wake condition check, пояснює користувачу чому не бере setup | `trader-v3/bot/agent/context.py` |

---

## 4. Інваріанти та дотримання

| # | Invariant | Як дотримано |
|---|-----------|--------------|
| I1 | UDS = narrow waist | `compute_range_exhaustion` **тільки читає** через SmcRunner helpers, не пише |
| I2 | Time geometry | D1 anchor через existing `79200s` (ADR-0023), session anchors через SessionContext (ADR-0035). Ніяких нових часових перетворень |
| I3 | Final > Preview | Використовує forming D1 bar (preview) для live mult — це очікувано (ціна живе), але **atr_baseline рахується тільки з complete bars** |
| I5 | Degraded-but-loud | Якщо D1 bars недоступні → `degraded=["no_d1_bar"]` у state, explicit warning log. **НІ silent fallback** до zero |
| I7 | Autonomy-first (trader-v3) | Агент **читає** exhaustion, але **сам** вирішує що робити. Code не блокує — тільки інформує. `confidence_delta` — suggestion, не hard gate |
| S0 | SMC read-only overlay | New types `frozen=True`, runner не mutates. OK |
| S1 | SMC ephemeral | State обчислюється кожен тік заново, нічого не persists |
| X28 | Frontend re-derives | Phase/mult обчислюється на backend, UI лише фарбує (amber/red). Ніяких розрахунків на UI |

---

## 5. Консеквенції

### 5.1 Позитивні

1. Confluence scoring стає volatility-aware → менше false A+ grades на exhausted moves
2. Narrative звучить професійно: "D1 вже пройшов 0.95 ATR, вище 1.0 вхід ризикований" замість просто "price in zone"
3. Signal Engine не генерує entries у exhausted напрямку — зменшує noise
4. UI отримує credibility pill — тонкий premium touch (ADR-0036 spirit)
5. Арчі (trader-v3) отримує первинний rationale "чому не заходимо" — зменшує hallucination risk
6. $0 додатковий cost — все in-memory, pure compute, reuse `compute_atr()`

### 5.2 Негативні / ризики

1. **Config calibration** — thresholds (0.35/0.70/1.00) взяті з industry heuristics, але під XAU/USD можуть треба інші (gold volatile більше за FX). Потрібен post-MVP calibration P-slice
2. **Wire frame bytes** — +~200 bytes per frame при full by_anchor. Delta frame оптимізація (ADR-0042) має увімкнути diff-only оновлення
3. **Confluence regression risk** — якщо `confidence_delta` занадто агресивний, валідні setups можуть втрачати A+ grade. Mitigation: `enabled: true` з можливістю `confidence_delta_map: {all: 0}` якщо спостерігаємо проблему
4. **Session ATR scaling** — `session_atr_scale: 6.0` — rough (D1 ≈ 6×H1 range) припущення. Краще було б reused session-specific ATR, але це +module. P2 follow-up
5. **Week anchor недосконалий** — Sunday 22:00 UTC open може мати gap. Degradation path: якщо gap >1 ATR → `degraded=["week_gap_open"]`, skip week_open від primary selection

### 5.3 Нейтральні

- Потрібні нові тести (≥3): pure compute correctness, degraded states, phase boundary classification
- Доки запис: оновити `docs/CODEMAP.md` (новий файл core/smc/range_exhaustion.py), `docs/contracts.md` (wire frame extension), NarrativePanel spec

---

## 6. План імплементації (P-slices)

| # | Slice | Файли | LOC | Depends |
|---|---|---|---|---|
| P1 | Types + config schema | `core/smc/types.py` (extend), `core/smc/config.py` (extend), `config.json` (new section, `enabled: false` за замовчуванням) | ≤80 | — |
| P2 | Pure compute + tests | `core/smc/range_exhaustion.py` (NEW), `tests/core/smc/test_range_exhaustion.py` (NEW, ≥6 тестів) | ≤150 | P1 |
| P3 | SmcRunner integration | `runtime/smc/smc_runner.py` (compute wiring), `core/smc/engine.py` (snapshot extension) | ≤40 | P2 |
| P4 | Wire frame + delta | `runtime/ws/ws_server.py` (serialize), `runtime/ws/delta.py` (diff support) | ≤50 | P3 |
| P5 | Confluence consumer | `core/smc/confluence.py` (apply confidence_delta), tests update | ≤60 | P3 |
| P6 | Narrative phrase | `core/smc/narrative.py` (додати рядок про travel phase) | ≤50 | P3 |
| P7 | Signal suppression | `core/smc/signals.py` (suppressed lifecycle якщо exhausted && same dir) | ≤40 | P3 |
| P8 | UI pill (TraderBar) | `ui_v4/src/layout/TraderBar.svelte` (NEW pill), `ui_v4/src/lib/types.ts` (extend) | ≤60 | P4 |
| P9 | Tier gate (free/premium) | `runtime/ws/tier_gate.py` (strip `by_anchor` для free) — якщо ADR-0048 P6 вже зроблено, просто register field | ≤20 | P4, ADR-0048 P6 |
| P10 | Calibration pass | Рекалібровка thresholds на реальних XAU/USD M1 даних з Apr 2026, tune `confidence_delta_map` | — | P7 |

**Total**: ~550 LOC, 10 slices, strictly parallelizable після P2.
**MVP**: P1→P2→P3→P4 (types + compute + pipeline) — вже продукт з wire frame, далі consumers incremental.

---

## 7. Rollback

**Feature flag**: `smc.range_exhaustion.enabled = false` у config.json повертає систему у pre-0053 стан. Пояснення:

- SmcRunner не викликає `compute_range_exhaustion` → `range_exhaustion` поле у snapshot = `None`
- Confluence/Narrative/Signals перевіряють `if range_snap is None: return` → не впливає на scoring
- UI TraderBar pill не рендериться якщо `range_exhaustion == null`
- Wire frame не включає поле → backward compatible для старих клієнтів

**Повний revert**: видалити файл `core/smc/range_exhaustion.py` + секцію у config + type. Оскільки всі consumer-сайти мають `if range_snap is None` guard — revert cost ≈ 15 хвилин.

**Data migration**: не потрібно — нічого не persists.

---

## 8. Тестування

Обовʼязкові тести (min):

1. `test_early_phase_small_travel` — traveled_mult=0.2 → phase="early", confidence_delta=0.0
2. `test_exhausted_large_travel` — traveled_mult=1.3 → phase="exhausted", confidence_delta=-0.3
3. `test_phase_boundary_mid_late` — mult=0.70 → phase="mid" (inclusive lower), mult=0.71 → "late"
4. `test_degraded_no_d1_bar` — empty bars → state з `degraded=["no_d1_bar"]`, не raise
5. `test_multi_anchor_london_vs_d1` — ситуація де d1="mid" але london="exhausted" → primary selection за session context
6. `test_confidence_delta_applied_to_confluence` — integration з confluence scorer (P5)
7. `test_signal_suppression_exhausted_same_dir` — integration з signal engine (P7)
8. `test_wire_frame_free_vs_premium` — free tier має тільки `primary`, premium — `by_anchor` (P9)

Performance budget:

- Single compute call ≤ 0.5ms (O(14) × 3 anchors)
- SmcRunner frame computation total budget ≤ 3ms (unchanged)

---

## 9. Open Questions

1. **Calibration dataset**: який період XAU/USD брати для тюнінгу thresholds? Propose: Apr 2026 M1 (reasonable, без covid-era extreme vol)
2. **Session ATR baseline**: замість `H1 * 6` — окремий `compute_session_atr()` на M15 bars? Trade-off: точніше vs +код. Вирішується у calibration pass (P10)
3. **Week anchor reliability**: Sunday gap-open може дати хибне `exhausted` стан у понеділок вранці. Mitigation: skip week_open у primary selection якщо gap > `cfg.week_gap_cap` (= 0.5 ATR)
4. **Inter-symbol applicability**: thresholds універсальні чи per-symbol? Gold має більшу щоденну volatility за EUR/USD. Для MVP — єдині, у P10 калібровка розгляне per-symbol overlay у config

---

## 10. References

- ADR-0023 D1 Live Derive from M1 (anchor 79200s)
- ADR-0024 SMC Engine (core primitives)
- ADR-0024a SMC Engine Self-Audit (ATR dedup precedent)
- ADR-0029 OB Confluence Scoring (primary consumer)
- ADR-0033 Context Flow Narrative (consumer)
- ADR-0035 Sessions & Killzones (session anchors)
- ADR-0036 Premium Trader-First Shell (UI credibility integration)
- ADR-0039 Signal Engine (consumer)
- ADR-0042 Delta Frame State Sync (wire frame extension)
- ADR-0048 Platform Wake Engine (tier gate precedent)
- `core/smc/swings.py:102` — existing `compute_atr()` reuse
- Industry reference: Average Daily Range (ADR) meter у TradingView, "ADR%" у Forex traders, ICT "Daily Range Profile"
