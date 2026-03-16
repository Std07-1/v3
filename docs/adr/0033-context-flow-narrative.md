# ADR-0033: Context Flow — Multi-TF Narrative Engine

- **Статус**: **Implemented**
- **Дата**: 2026-03-08
- **Revised**: 2026-03-09 (Rev 2: R_TRADER + R_SMC_CHIEF + R_BUG_HUNTER review incorporated)
- **Revised**: 2026-03-15 (Rev 3: counter-trend sub_mode + trigger proximity/displacement guards)
- **Revised**: 2026-03-16 (Rev 4: Audit fixes P1–P6 + P5B FVG candidate gate)
- **Автор**: R_ARCHITECT
- **Initiative**: `smc_vis_phi3`
- **Пов'язані ADR**: ADR-0024 (Engine), ADR-0024c (Zone POI + Context Stack), ADR-0028 (Elimination), ADR-0029 (Confluence), ADR-0030-alt (TF Sovereignty), ADR-0031 (Bias Banner)

---

## 1. Контекст і проблема

### 1.1 Поточний стан

Платформа має повний SMC compute pipeline:

| Компонент | Статус | SSOT |
|-----------|--------|------|
| SmcEngine: 8 детекторів (OB, FVG, structure, liquidity, P/D, inducement, fractals, displacement) | ✅ Implemented | `core/smc/engine.py` |
| Zone lifecycle (merge / decay / mitigation / TTL) | ✅ Implemented | `engine._update_zone_lifecycle()` |
| Context Stack L1/L2/L3 — cross-TF zone injection | ✅ Implemented | `core/smc/context_stack.py` |
| Confluence scoring — 8-factor, grade A+/A/B/C | ✅ Implemented | `core/smc/confluence.py` |
| Elimination Engine — display budget, proximity, TTL | ✅ Implemented | `engine._filter_for_display()` |
| Bias Banner — multi-TF trend_bias + momentum | ✅ Implemented | `BiasBanner.svelte`, `bias_map` in WS frame |
| Cross-TF projection styling (opacity, dashed) | ✅ Implemented | `OverlayRenderer.ts`, ADR-0030-alt |
| Zone grades in UI (badge A+/A/B/C on OB) | ✅ Implemented | `zone_grades` in WS full frame |

### 1.2 Що відсутнє — "Last Mile" від даних до рішення

Трейдер бачить:

- 5–12 зон на графіку (з grade badge)
- Bias banner (D1↑ H4↓ H1↑ M15↓)
- Key levels (PDH, PDL, HOD, LOD)
- Tooltips на зонах (при hover)

Трейдер **НЕ** бачить відповідь на 3 ключові питання:

| # | Питання | Час на відповідь зараз | Ціль |
|---|---------|----------------------|------|
| Q1 | "Торгувати чи чекати?" | 15–30 сек (сканування зон + bias + grades) | **< 1 сек** |
| Q2 | "Який сценарій активний?" | 30–60 сек (mental cross-TF synthesis) | **< 3 сек** |
| Q3 | "Де target і де invalidation?" | 10–20 сек (пошук levels + grades) | **< 1 сек** |

### 1.3 Failure Model

| # | Сценарій | Без narrative | З narrative |
|---|----------|--------------|-------------|
| F1 | Всі TF bearish, 1 OB A+(8) на M5 | Трейдер бачить зону + grade, але мусить сам зібрати bias alignment | Banner: "🔴 SELL bias aligned" + Active Scenario з target/invalidation |
| F2 | Mixed bias (H4↑, M15↓) | Трейдер бачить conflict у pills, не знає чи це корекція чи розворот | "⚠ Wait: M15 correction in H4 bullish. Watch: M15 ChoCH↑ near 5144" |
| F3 | Жоден setup A+/A | Графік з 8 B/C зонами — шум | "🟡 No setup. Next area: OB 5144 (B4). Wait M15 CHoCH" |
| F4 | Post-mitigation cleanup | Старі зони зникають, нове з'являється | Scenario auto-transitions: "Previous: mitigated → Scanning new zones" |
| F5 | FVG без OB поряд | Ще одна зона на графіку, відволікає | FVG = context hint (subdued). OB з FVG confirmation = F2 factor badge |
| F6 | D1 reversal + H4 continuation | Суперечливі signals | "🔴 CAUTION: D1 reversal forming (ChoCH↓). H4 still bullish. Reduce size." |

### 1.4 Evidence Pack

| Факт | Evidence |
|------|---------|
| SmcSnapshot не має narrative поля | [VERIFIED `core/smc/types.py`] — SmcSnapshot: `symbol, tf_s, zones, swings, levels, trend_bias, last_bos_ms, last_choch_ms, computed_at_ms, bar_count`. Без scenario/narrative. |
| WS full frame не має narrative поля | [VERIFIED `runtime/ws/ws_server.py`] — `_build_full_frame()`: zones, swings, levels, trend_bias, zone_grades, bias_map, momentum_map. Без narrative. |
| UI types не мають narrative | [VERIFIED `ui_v4/src/types.ts`] — SmcData: zones, swings, levels, trend_bias, zone_grades, bias_map, momentum_map. Без narrative. |
| ChartHud не має scenario panel | [VERIFIED `ui_v4/src/layout/ChartHud.svelte`] — тільки symbol/TF picker, price, bias pills, streaming dot. |
| ADR-0024 §9А.2 містить "L3 Narrative engine" як future vision | [VERIFIED `docs/adr/0024-smc-engine.md`] — без контрактів, без деталізації. |
| Context Stack L1/L2/L3 вже таґить зони | [VERIFIED `core/smc/context_stack.py`] — `collect_htf_zones()`, `tag_local_zones()`, scoring per layer. |
| Confluence grade вже визначає якість setup | [VERIFIED `core/smc/confluence.py`] — 8 factors, score 0–11, grade A+/A/B/C. |
| bias_map вже дає multi-TF alignment | [VERIFIED `runtime/smc/smc_runner.py`] — `get_bias_map()` + `get_momentum_map()`. |

---

## 2. Обмеження (Constraints)

### 2.1 Інваріанти

| ID | Перевірка | Наслідок для narrative |
|----|-----------|----------------------|
| I0 | Dependency Rule | Narrative synthesis = `core/smc/` (pure). Без I/O. |
| I1 | UDS = вузька талія | Narrative НЕ пише в UDS. Read-only overlay (як весь SMC). |
| I4 | Один update-потік | Narrative передається через існуючий WS frame. Без нового каналу. |
| I5 | Degraded-but-loud | Якщо narrative не може бути обчислений → explicit fallback text, не порожній рядок. |
| S0 | `core/smc/` = pure | Narrative module = pure function, no I/O. |
| S2 | Deterministic | Same snapshot → same narrative. |
| S5 | Config SSOT | Пороги wait/trade/caution → `config.json:smc.narrative`. |

### 2.2 Performance

- Narrative computation = post-processing SmcSnapshot + bias_map + zone_grades.
- Бюджет: **< 5 ms** (після on_bar, не під час). Це string formatting, не число-дробіння.
- Wire size: narrative block ≤ 500 bytes (JSON). Не full text dump.

### 2.3 Backward Compatibility

- WS frame: нові поля additive (`narrative?` optional). Старий UI ігнорує.
- SmcData: `narrative?` optional. Store defaults undefined.
- OverlayRenderer: narrative panel = новий render step. Не торкає існуючі.

---

## 3. Розглянуті альтернативи

### Альтернатива A: "Backend Narrative" — pure core/smc/ synthesis

**Суть**: Новий модуль `core/smc/narrative.py` — pure function що приймає SmcSnapshot + bias_map + zone_grades → повертає `NarrativeBlock` dataclass. Передається через WS frame як `narrative: {...}`. UI рендерить as-is.

**Narrative = computed on backend, displayed on frontend.**

```
SmcEngine.get_display_snapshot()
    ↓
    + bias_map, zone_grades, momentum_map
    ↓
synthesize_narrative(snapshot, bias_map, grades, momentum, config)
    ↓
NarrativeBlock(
  mode: "trade" | "wait" | "caution",
  headline: "🔴 SELL setup ready",
  scenario: "SHORT @ OB A(6) 5144–5225. Wait: M15 ChoCH↓. Target: PDL 5062. Inv: >5230",
  bias_summary: "H1↓ aligned, M15 correction completing",
  next_area: "5144 bearish OB (wait M15 alignment)",
  confidence: 0.82
)
    ↓
WS frame: { ..., narrative: {...} }
    ↓
UI: NarrativePanel.svelte renders block
```

**Pros:**

- Single source of truth для narrative text → UI є thin renderer (I4)
- Deterministic (S2): same snapshot → same narrative
- Testable: pure function, фіксовані inputs → фіксований output → pytest
- Backend контролює logic centrally (один `narrative.py`, не розкидана логіка по UI)
- Wire format versioned: `narrative_v1` контракт

**Cons:**

- +1 module в `core/smc/` (~150–200 LOC)
- Narrative text = business logic в Python. Зміна формулювань → Python patch + redeploy
- Wire size збільшується (~300–500 bytes per full frame)
- Нові strings в Python для Ukrainian/English → potential i18n debt

**Blast radius:** `core/smc/narrative.py` (new), `core/smc/types.py` (+NarrativeBlock), `runtime/smc/smc_runner.py` (+call), `runtime/ws/ws_server.py` (+wire field), `ui_v4/src/types.ts` (+interface), `ui_v4/src/stores/smcStore.ts` (+field), `ui_v4/src/layout/NarrativePanel.svelte` (new), `config.json:smc.narrative` (+section).

**LOC estimate:** ~350 (Python ~200, TS/Svelte ~150).

---

### Альтернатива B: "Frontend Narrative" — UI-side synthesis від існуючих даних

**Суть**: Без нового backend module. Frontend `narrativeEngine.ts` бере `SmcData` (zones, bias_map, zone_grades, momentum_map) і синтезує narrative client-side. Без змін wire format.

```
smcStore (existing SmcData)
    ↓
deriveNarrative(smcData, currentTf, config) → NarrativeState
    ↓
NarrativePanel.svelte renders state
```

**Pros:**

- Zero backend changes (Python untouched)
- Zero wire format changes (backward-compatible trivially)
- Faster iteration: зміна narrative = npm run build (seconds), не Python redeploy
- i18n potentially easier (TS string templates)
- Менший blast radius: тільки `ui_v4/`

**Cons:**

- **Порушує принцип thin UI (G1)**: narrative = доменна логіка (визначення trade/wait/caution), не лише рендер
- **Дублювання**: `narrativeEngine.ts` мусить replicate scoring logic і bias alignment rules вже наявні в Python
- **Не тестується pytest**: logic в TypeScript = окрема тестова інфраструктура (vitest/playwright)
- **S2 risk**: якщо TS synthesis розходиться з Python scoring → split-brain narrative
- Нетривіальна складність: derive narrative потребує zone_grades, bias alignment, proximity analysis — це ~150 LOC logic (не просто string formatting)

**Blast radius:** `ui_v4/src/lib/narrativeEngine.ts` (new), `ui_v4/src/stores/smcStore.ts` (minor), `ui_v4/src/layout/NarrativePanel.svelte` (new).

**LOC estimate:** ~250 (TS ~200, Svelte ~50).

---

### Альтернатива C: Hybrid — Backend computes structured data, Frontend renders narrative text

**Суть**: Backend обчислює structured `NarrativeData` (mode, top_zone_id, target_ids, bias_alignment enum) — без text strings. Frontend має template engine що перетворює structured data у читаний текст.

**Pros:**

- Backend = structured, testable, deterministic
- Frontend = presentation (text rendering, i18n, formatting)
- Зміна формулювань = npm rebuild, не Python

**Cons:**

- Складніший контракт (structured data ≠ simple string)
- Frontend template engine = ще один шар абстракції
- Дві точки maintenance: Python (structure) + TS (templates)
- Більший wire format (structured data > text string)

**Blast radius:** Same as Alt A + template engine in UI.

**LOC estimate:** ~400 (Python ~150, TS engine ~150, templates ~100).

---

### Вибір: Альтернатива A — "Backend Narrative"

**Обґрунтування:**

1. **Принцип thin UI (G1)** — narrative synthesis = доменна логіка ("trade чи wait?"), не презентація. Її місце — `core/smc/`, не `ui_v4/`. Визначити що "це A+ setup з aligned bias" = та сама логіка що confluence scoring.

2. **Determinism (S2)** — один Python module, one source of truth. Якщо backend каже "wait" → UI показує "wait". Без можливості розходження.

3. **Testability** — pytest з фіксованими SmcSnapshot + bias_map → assert narrative.mode == "trade". Вже 431+ тестів у такій інфраструктурі.

4. **Consistency з existing pattern** — bias_map, zone_grades, momentum_map — всі computed backend, sent via WS, displayed UI. Narrative = той самий pattern.

5. **i18n trade-off прийнятний** — для v1 narrative text = англійська з emoji markers. Тіло narrative = structured (mode + zone_id + target_id). Текстовий шаблон живе в Python. Якщо колись потрібно i18n → виносимо templates. Не зараз (YAGNI).

6. **LOC reasonable** — ~200 LOC Python pure logic + ~150 UI = ~350 total, розбивається на 5 P-slices ≤80 LOC кожен.

**Чому не Альтернатива B:** Порушує G1 (thin UI). Дублює scoring logic. Не тестується pytest.
**Чому не Альтернатива C:** Over-engineering. Structured data → template engine = два шари замість одного. Виграш від i18n не виправдовує складність при 1 мові.

---

## 4. Рішення (деталі)

### 4.1 Types / Contracts (FIRST)

#### Python: `core/smc/types.py` — нові типи

```python
@dataclass(frozen=True)
class ActiveScenario:
    """Один actionable scenario для трейдера."""
    zone_id: str               # ID зони-тригера (OB A+/A)
    direction: str             # "long" | "short"
    entry_desc: str            # "OB▲ A(6) 5144–5225"
    trigger: str               # 4 states: "approaching" | "in_zone" | "triggered" | "ready"
    trigger_desc: str          # "Approaching: 15 pts from zone" | "In zone: wait M15 ChoCH↓"
    target_desc: str           # "PDL 5062" | None якщо target невідомий
    invalidation: str          # "Above 5230" | "Below 5100"

@dataclass(frozen=True)
class NarrativeBlock:
    """Повний narrative для одного symbol+viewer_tf.
    
    Cross-role review decisions (Rev 2):
    - T-1: scenarios list замість single scenario (max 2: primary + alternative)
    - T-4/BH-3: confidence видалено (grade badge на зоні = достатньо)
    - SC-1: market_phase = 3 trend states, не Wyckoff phases
    - SC-3: 2 modes (trade/wait), trade має sub-qualifier
    - SC-4: alignment = D1+H4 only, LTF disagree = normal correction
    - SC-7/BH-1: current_price required для invalidation detection
    - BH-4: target_desc Optional, explicit warnings
    - BH-8: fallback = degraded NarrativeBlock, не None
    """
    mode: str                  # "trade" | "wait" (2 modes, not 3 — SC-3)
    sub_mode: str              # "aligned" | "counter" | "reduced" | "" (Rev 3: +counter)
    headline: str              # "🔴 SELL setup ready" | "🟡 SELL — counter-trend" | "🟡 No setup — wait"
    bias_summary: str          # Context beyond pills: "H4 pullback to premium OB — expect rejection"
    scenarios: list            # List[ActiveScenario], max 2 (primary + alternative) — T-1
    next_area: str             # "{price} {dir} {type} ({grade}/{score}) — {condition}"
    fvg_context: str           # "" якщо немає. "OB entry refined by FVG: 5147–5155" або "FVG gap at 5180"
    market_phase: str          # "trending_up" | "trending_down" | "ranging" (display-only, SC-1/T-6)
    warnings: list             # List[str] — degraded signals (BH-4/BH-8). E.g. ["no_target_found"]
```

**Removed** (Rev 2): `confidence: float` (T-4/BH-3 — grade badge sufficient), `scenario: ActiveScenario` (→ `scenarios: List`).

**Changed** (Rev 2): `mode` = 2 states not 3 (SC-3). `market_phase` = 3 trend states not 5 Wyckoff (SC-1).

#### TypeScript: `ui_v4/src/types.ts` — нові інтерфейси

```typescript
interface ActiveScenario {
  zone_id: string;
  direction: 'long' | 'short';
  entry_desc: string;
  trigger: 'approaching' | 'in_zone' | 'triggered' | 'ready';
  trigger_desc: string;
  target_desc: string | null;    // null якщо target невідомий (BH-4)
  invalidation: string;
}

interface NarrativeBlock {
  mode: 'trade' | 'wait';                     // 2 modes (SC-3)
  sub_mode: 'aligned' | 'counter' | 'reduced' | '';  // Rev 3: +counter
  headline: string;
  bias_summary: string;
  scenarios: ActiveScenario[];                 // max 2 (T-1)
  next_area: string;
  fvg_context: string;                         // "" if none
  market_phase: 'trending_up' | 'trending_down' | 'ranging';  // SC-1
  warnings: string[];                          // degraded signals (BH-4/BH-8)
}
```

**Convention (BH-10)**: Scalar strings = empty string for absent. Objects/arrays = null/empty. Consistent across Python ↔ TS.

#### Wire format (WS full frame): `narrative?: NarrativeBlock`

#### Config: `config.json:smc.narrative`

```json
"narrative": {
    "enabled": true,
    "trade_min_grade": "A",
    "trade_min_score": 6,
    "max_scenarios": 2,
    "market_phase_enabled": true,
    "fvg_context_enabled": true,
    "trigger_structure_lookback_bars": 5,
    "trigger_proximity_atr": 3.0,
    "trigger_displacement_window": 3,
    "target_lookback_bars": 100,
    "phase_hysteresis_bars": 3,
    "max_wire_bytes": 600
}
```

**Removed** (Rev 2): `caution_on_mixed_bias` (SC-3: no caution mode), `market_phase_n_swings/range_atr/range_min_bars` (SC-1: simplified to 3 trend states).

**Added** (Rev 2): `max_scenarios` (T-1), `phase_hysteresis_bars` (BH-6), `max_wire_bytes` (BH-5).

**Added** (Rev 3): `trigger_proximity_atr` (max ATR distance for "triggered" state, default 3.0), `trigger_displacement_window` (±N viewer-TF bars to look for displacement near CHoCH, default 3).

### 4.2 Pure Logic: `core/smc/narrative.py`

#### Головна функція

```python
def synthesize_narrative(
    snapshot: SmcSnapshot,
    bias_map: Dict[str, str],        # {"900": "bullish", ...}
    zone_grades: Dict[str, dict],    # {zone_id: {score, grade, factors}}
    momentum_map: Dict[str, dict],   # {"900": {b: int, r: int}}
    viewer_tf_s: int,
    current_price: float,            # BH-1: required for invalidation + proximity
    atr: float,                      # BH-1: required for distance checks
    config: dict                     # smc.narrative section
) -> NarrativeBlock:
```

**Note (BH-8)**: Function NEVER returns None. On error → returns fallback NarrativeBlock with `mode="wait"`, `headline="⚠ Narrative unavailable"`, `warnings=["computation_error"]`. Caller logs error.

#### Decision Logic (Mode Selection) — Revised (SC-3, SC-4, SC-7)

```
Step 1: HTF Bias Alignment (SC-4: D1 + H4 only, LTF disagree = normal)
  ├─ htf_aligned = D1 and H4 bias same direction
  ├─ htf_mixed = D1 and H4 bias disagree
  └─ no_data = D1 or H4 bias absent

Step 2: Best Zones (T-1: up to 2)
  ├─ Filter zones: grade >= trade_min_grade AND score >= trade_min_score
  ├─ Sort by: score DESC, then proximity to current_price ASC
  ├─ primary_zone = first match or None
  └─ alt_zone = second match (opposite direction if possible) or None

Step 2b: Invalidation check (SC-7, BH-1)
  ├─ For each candidate zone: check if current_price has crossed invalidation level
  └─ Discard invalidated zones, add "scenario_invalidated" to warnings

Step 3: Mode Decision (SC-3 + Rev 3: counter-trend detection)
  ├─ mode = "trade", sub_mode = "aligned"   IF primary_zone AND htf_aligned AND zone_dir matches HTF
  ├─ mode = "trade", sub_mode = "counter"   IF primary_zone AND htf_aligned AND zone_dir opposes HTF
  ├─ mode = "trade", sub_mode = "reduced"   IF primary_zone AND htf_mixed
  └─ mode = "wait",  sub_mode = ""          IF no primary_zone OR no_data
```

#### Headline Generation (SC-3: 2 modes + sub-qualifier)

```python
MODE_HEADLINES = {
    ("trade", "aligned", "long"):    "🟢 BUY setup ready",
    ("trade", "aligned", "short"):   "🔴 SELL setup ready",
    ("trade", "counter", "long"):    "🟡 BUY — counter-trend",        # Rev 3
    ("trade", "counter", "short"):   "🟡 SELL — counter-trend",       # Rev 3
    ("trade", "reduced", "long"):    "🟢 BUY — reduced: mixed HTF",
    ("trade", "reduced", "short"):   "🔴 SELL — reduced: mixed HTF",
    ("wait", "", None):              "🟡 No setup — wait",
}
# Degraded fallback (BH-8):
DEGRADED_HEADLINE = "⚠ Narrative unavailable"
```

#### Bias Summary Generation (T-8: context beyond pills, not duplication)

Compose from `bias_map` + `momentum_map` + zone position. NOT repeating BiasBanner pills:

```
"H4 pullback to premium OB — expect rejection"       (htf_aligned, zone in premium)
"H4 retrace completing — watch M15 structure"         (htf_aligned, LTF counter-trend)
"D1↓ but H4↑ — mixed: wait or reduce size"           (htf_mixed)
"Insufficient HTF data (1/2 TF)"                      (no_data)
```

#### Active Scenario Construction (T-1: up to 2 scenarios)

Якщо `mode == "trade"` і є qualifying zones:

```python
scenarios = []
for zone in [primary_zone, alt_zone]:
    if zone is None:
        continue
    grade_info = zone_grades.get(zone.id, {})
    scenarios.append(ActiveScenario(
        zone_id=zone.id,
        direction="short" if "bear" in zone.kind else "long",
        entry_desc=_format_entry(zone, grade_info),           # "OB▼ A(6) 5144–5225"
        trigger=_resolve_trigger_state(snapshot, zone, viewer_tf_s, current_price),
        trigger_desc=_resolve_trigger_desc(snapshot, zone, viewer_tf_s, current_price, atr),
        target_desc=_find_target(snapshot, zone, direction),  # Optional[str]
        invalidation=_find_invalidation(zone),                # "Above 5230"
    ))
    if len(scenarios) >= config.get("max_scenarios", 2):
        break
```

Primary scenario = highest score. Alternative = next qualifying zone (opposite direction preferred for fallback).

If `mode == "wait"` → `scenarios = []`.

#### Trigger Resolution — 4 IOFED States + Proximity/Displacement (T-2, Rev 3)

Rev 3 replaces the simple `_has_structure_aligned()` boolean with 3 doctrine-aligned helpers:

1. **`_find_qualifying_choch(snapshot, zone)`** → `SmcSwing | None`  
   Most recent CHoCH/BOS aligned with zone direction (after zone anchor).

2. **`_has_displacement_near(snapshot, choch, zone, viewer_tf_s, config)`** → `bool`  
   Displacement marker within ±`trigger_displacement_window` viewer-TF bars of the CHoCH.  
   ICT doctrine: MS Shift requires impulse/displacement, not just structure break.

3. **`_is_price_proximate(zone, current_price, atr, config)`** → `bool`  
   Price within `trigger_proximity_atr` × ATR of the nearest zone edge.

**Trigger matrix** (Rev 3 — proximity + displacement guards):

```python
def _resolve_trigger_state(snapshot, zone, viewer_tf_s, current_price, atr, config):
    """T-2: 4 IOFED trigger states with proximity + displacement guards.

    Matrix:
      CHoCH + displacement + in_zone          → "ready"
      CHoCH + in_zone (no displacement)       → "in_zone"  (Rev 3: was "ready")
      CHoCH + displacement + proximate        → "triggered"
      CHoCH + not proximate                   → "approaching"  (Rev 3: was "triggered")
      CHoCH + proximate (no displacement)     → "approaching"
      no CHoCH + in_zone                      → "in_zone"
      no CHoCH                                → "approaching"
    """
    in_zone = zone.low <= current_price <= zone.high
    choch = _find_qualifying_choch(snapshot, zone)
    has_disp = False
    if choch is not None:
        has_disp = _has_displacement_near(snapshot, choch, zone, viewer_tf_s, config)
    proximate = _is_price_proximate(zone, current_price, atr, config)

    if choch is not None and has_disp and in_zone:
        return "ready"
    if choch is not None and in_zone:
        return "in_zone"
    if choch is not None and has_disp and proximate:
        return "triggered"
    if in_zone:
        return "in_zone"
    return "approaching"
```

**Trigger descriptions** (Rev 3 — richer state messages):

```python
def _resolve_trigger_desc(snapshot, zone, viewer_tf_s, current_price, atr, config):
    state = _resolve_trigger_state(snapshot, zone, viewer_tf_s, current_price, atr, config)
    # ...
    if state == "ready":     return "Ready: structure + displacement confirmed in zone"
    if state == "triggered":  return "Triggered: M15 CHoCH↓ + displacement — seek entry"
    if state == "in_zone":
        if choch:             return "In zone: CHoCH↓ seen, await displacement"
        else:                 return "In zone: wait M15 CHoCH↓"
    else:                     return "Approaching: {n} pts from zone"
```

**Key Rev 3 doctrine fixes:**

- CHoCH far from zone (>3 ATR) → "approaching" not "triggered" (prevents false alerts on LTF cross-TF zones)
- CHoCH without displacement → "in_zone" not "ready" (MS Shift ≠ just BOS)
- `atr` and `config` added to function signature for proximity/displacement checks

#### Target Resolution (`_find_target`) — BH-4: Optional, no silent fallback

Пріоритет (перший знайдений):

1. Key level у напрямку trade (PDL для short, PDH для long) → "PDL 5062"
2. Opposite-side HTF zone (institutional L1) → "H4 OB 5300"
3. Recent swing extreme → "Recent HL 5090"
4. Fallback → **`None`** (not magic string). Caller adds `"no_target_found"` to `warnings`.

**Note (T-7)**: Session levels (Asia H/L, London H/L) not in priority chain for v1. `SmcLevel` currently lacks session level kinds. Deferred to ADR for session module. See Q6.

#### FVG Context (T-5: actionable wording)

```
IF any FVG that overlaps top_zone price range:
    → "OB entry refined by FVG: {fvg.low}–{fvg.high}"
ELIF closest FVG within 2×ATR:
    → "FVG gap at {fvg_price} — rebalancing expected before zone"
ELSE:
    → "" (empty, не показуємо)
```

#### Trend Context Detection (`_detect_market_phase`) — SC-1: 3 states, not Wyckoff

Simplified trend heuristic. **No Wyckoff terminology** (accumulation/distribution require volume — SC-1).

```
IF last_n_swings show HH+HL pattern (≥2 consecutive):  → "trending_up"
IF last_n_swings show LH+LL pattern (≥2 consecutive):  → "trending_down"
ELSE: → "ranging"
```

**Hysteresis (BH-6)**: Phase switch requires `phase_hysteresis_bars` (default 3) consecutive bars confirming new state. Prevents jitter on volatile sessions.

**Display-only constraint (T-6)**: `market_phase` does NOT influence mode selection. It's displayed in NarrativePanel as context label only.

Config: `phase_hysteresis_bars = 3`.

### 4.3 Runtime Integration: `runtime/smc/smc_runner.py`

Додати метод:

```python
def get_narrative(self, symbol, viewer_tf_s, current_price, atr):
    """Synthesize narrative for display. Never returns None (BH-8)."""
    cfg = self._config.get("smc", {}).get("narrative", {})
    if not cfg.get("enabled", False):
        return None  # Feature disabled = explicit opt-out, not error
    try:
        snap = self.get_snapshot(symbol, viewer_tf_s)
        bias = self.get_bias_map(symbol)
        grades = self.get_zone_grades(symbol, viewer_tf_s)
        momentum = self.get_momentum_map(symbol)
        return synthesize_narrative(
            snap, bias, grades, momentum,
            viewer_tf_s, current_price, atr, cfg
        )
    except Exception:
        logging.exception("NARRATIVE_ERROR symbol=%s tf=%d", symbol, viewer_tf_s)
        return _fallback_narrative_block()  # mode=wait, headline=degraded, warnings
```

### 4.4 WS Frame: `runtime/ws/ws_server.py`

В `_build_full_frame()` додати:

```python
narrative = runner.get_narrative(symbol, tf_s, current_price, atr)
if narrative:
    wire = narrative_to_wire(narrative)
    frame["narrative"] = wire
```

**Wire serialization (BH-5)**: `narrative_to_wire()` = `dataclasses.asdict()` for NarrativeBlock. ActiveScenario nested → recursive asdict handles it. None fields → `null` in JSON.

**Size guard (BH-5)**: `assert len(json.dumps(wire)) <= config.get("max_wire_bytes", 600)`. If exceeded → log warning, truncate scenarios to 1.

**N4 constraint (BH-9)**: Narrative MUST NOT be computed in delta frame path. Only full frames. Delta frames remain unchanged (bars only).

Delta frames: narrative НЕ відправляється (перераховується при кожному full frame / reconnect / TF switch). Delta only = бари. Narrative оновлюється через periodic full frame refresh (existing pattern).

### 4.5 UI Components

#### `NarrativePanel.svelte` — головний компонент (SC-6: collapsed default)

Розташування: під BiasBanner, зверху chart area (overlay position, pointer-events: none для chart area, pointer-events: auto для panel).

**Default state = collapsed** (SC-6: Clean Chart Doctrine). Only headline bar visible:

```
┌──────────────────────────────────────────────────────────────┐
│ 🔴 SELL A+(8) 5144 │ Trigger: In zone, wait ChoCH↓  [▸ N]  │ ← collapsed (1-line bar)
└──────────────────────────────────────────────────────────────┘
```

**Expanded** (click or hotkey N):

```
┌─────────────────────────────────────────────────────────┐
│ 🔴 SELL setup ready                           [▾ hide]  │ ← headline + collapse
│ H4 pullback to premium OB — expect rejection            │ ← bias_summary (T-8)
│ ─────────────────────────────────────────────────────── │
│ ❶ SHORT @ OB▼ A(6) 5144–5225                           │ ← scenarios[0]
│    Trigger: In zone: wait M15 ChoCH↓                    │ ← trigger_desc
│    Target: PDL 5062  │  Inv: >5230                      │ ← target + inv
│ ❷ LONG @ OB▲ A(6) 5080–5095  (fallback)                │ ← scenarios[1] (T-1)
│    Trigger: Approaching: 28 pts from zone               │
│    Target: H4 OB 5200  │  Inv: <5070                    │
│ ─────────────────────────────────────────────────────── │
│ FVG: OB entry refined by FVG: 5147–5155                 │ ← fvg_context (T-5)
│ Phase: trending_down                                    │ ← market_phase (SC-1)
└─────────────────────────────────────────────────────────┘
```

**Wait mode (no setup):**

```
┌─────────────────────────────────────────────────────────┐
│ 🟡 No setup — wait                           [▾ hide]  │
│ D1↓ but H4↑ — mixed: wait or reduce size               │
│ ─────────────────────────────────────────────────────── │
│ Next area: 5144 ▼ OB (B/4) — wait D1+H4 align          │ ← next_area (SC-5)
│ Phase: ranging                                          │
└─────────────────────────────────────────────────────────┘
```

**Visual rules:**

- Background: semi-transparent glassmorphism (consistent з BiasBanner)
- Mode color: trade=green/red (per direction), wait=muted yellow
- Font: monospace для prices, sans-serif для text
- **Default collapsed** (SC-6). Expand = click or hotkey N. Auto-collapse = 10 sec inactivity.
- Collapsed headline = 1-line bar under BiasBanner: `"🔴 SELL A+(8) 5144 | Trigger: ChoCH↓"`.
- Max width: 420px. Z-index: 31 (above BiasBanner z:30)
- Pointer-events: auto (clickable collapse), но не блокує chart під собою
- Compact mode для мобільних: тільки headline

#### `smcStore.ts` — додати narrative field

```typescript
// В SmcData:
narrative?: NarrativeBlock;

// В applySmcFull():
narrative: frame.narrative ?? undefined,

// NarrativeBlock.scenarios is array — UI maps over it.
```

### 4.6 FVG Display Policy (доповнення до ADR-0028 display filter) — SC-2 revised

FVG зони отримують context-aware rendering:

| FVG State | Render Rule | Evidence |
|-----------|-------------|---------|
| FVG overlaps active OB (**any** price overlap > 0, SC-2) | **Не рендерити окремо**. OB badge: "FVG✓". FVG contribution вже в A(6) grade. | Reduces noise. F2 factor already communicates confluence. |
| FVG standalone, grade B+ | Render з subdued opacity (0.15 fill, 0.3 border) | Visible but не конкурує з OB zones. |
| FVG standalone, no OB nearby, grade C | **Не рендерити** (below display filter threshold) | Шум reduction. Якщо потрібно — Research mode. |

Implementation: В `_filter_for_display()` додати FVG-OB overlap check. FVG price range intersects with active OB price range (**any** overlap > 0, not %) → exclude FVG від display, ensure OB has "fvg_confirmed" flag.

**Simplified** (SC-2): Previous threshold of 50% overlap removed. Any price overlap OR time adjacency (F2 `fvg_after_lookback_bars`) = overlap. This aligns with confluence F2 check already in `confluence.py`.

Config: `smc.display.fvg_ob_overlap_hide: true` (default).

### 4.7 Config (повна секція) — Rev 4

```json
"smc": {
    "narrative": {
        "enabled": true,
        "trade_min_grade": "A",
        "trade_min_score": 6,
        "fvg_trade_min_score": 99,
        "target_max_atr_distance": 12,
        "max_scenarios": 2,
        "market_phase_enabled": true,
        "phase_hysteresis_bars": 3,
        "fvg_context_enabled": true,
        "target_lookback_bars": 100,
        "trigger_structure_lookback_bars": 5,
        "trigger_proximity_atr": 3.0,
        "trigger_displacement_window": 3,
        "max_wire_bytes": 600
    },
    "display": {
        "fvg_ob_overlap_hide": true
    }
}
```

---

## 5. P-Slices (план реалізації)

| Slice | Scope | LOC est. | Інваріант | Verify | Rollback |
|-------|-------|----------|-----------|--------|----------|
| **P1** | Types: `NarrativeBlock`, `ActiveScenario` в `core/smc/types.py` + `ui_v4/src/types.ts` | ~40 | I0 (pure), S6 (wire match) | `pytest` type import + TS `npm run build` | `git checkout` both files |
| **P2** | Core logic: `core/smc/narrative.py` — `synthesize_narrative()` + helpers | ~150 | I0, S0 (pure), S2 (deterministic), S5 (config) | `pytest tests/test_smc_narrative.py` (5+ test cases: trade/wait/caution/no_data/fvg_context) | `git checkout core/smc/narrative.py` + remove test file |
| **P3** | FVG display policy: `engine._filter_for_display()` FVG-OB overlap | ~30 | S2 (deterministic) | existing tests + new overlap test | `git checkout engine.py` |
| **P4** | Runtime wiring: `smc_runner.get_narrative()` + `ws_server._build_full_frame()` narrative field | ~40 | I4 (one update path), I5 (degraded-loud) | `python -m app.main --mode ws_server`, curl full frame, check narrative field | `git checkout` both files |
| **P5** | UI: `NarrativePanel.svelte` + `smcStore.ts` field + `ChartPane.svelte` mount | ~90 | S6 (wire type match) | `npm run build`, visual check in browser | `git checkout` UI files |

**Total: ~350 LOC across 5 slices.**

**Sequence:** P1 → P2 → P3 → P4 → P5 (strict dependency chain: types → logic → integration → UI).

---

## Implementation Record (2026-03-09)

Усі 5 P-Slices реалізовані. Build: 172 modules, 307 KB, 0 errors. 196 SMC tests pass (20 нових narrative).

### P1: Types — `core/smc/types.py` + `ui_v4/src/types.ts`

**~40 LOC.** Додано frozen dataclasses `ActiveScenario` (zone_id, direction, entry_desc, trigger, trigger_desc, target_desc, invalidation) та `NarrativeBlock` (mode, sub_mode, headline, bias_summary, scenarios, next_area, fvg_context, market_phase, warnings). Exports в `__init__.py`. TS interfaces в `types.ts` + `RenderFrame.narrative` field.

### P2: Core logic — `core/smc/narrative.py` (NEW, ~350 LOC)

**Pure синтезуючий модуль.** `synthesize_narrative()` — єдина public функція (N3: ніколи не повертає None — fallback "wait" при виключенні). Helpers: `_resolve_htf_alignment()` (D1+H4 only), `_select_candidate_zones()` (grade A+/A), `_resolve_trigger_state()` (4 стани IOFED: approaching/in_zone/triggered/ready), `_find_target()` (Optional), `_build_fvg_context()`, `_detect_market_phase()` (trending/ranging/volatile + hysteresis), `_build_bias_summary()`, `narrative_to_wire()`. 20 тестів у `test_smc_narrative.py` — all 16 ADR matrix cases + 4 wire/helper.

### P3: FVG-OB overlap — `core/smc/engine.py` + `config.py`

**~30 LOC.** В `_filter_for_display()` додано перевірку: FVG зони з overlap будь-якого розміру з активними OB приховуються. Контроль через `SmcDisplayConfig.fvg_ob_overlap_hide: bool = True`.

### P4: Runtime wiring — `runtime/smc/smc_runner.py` + `runtime/ws/ws_server.py`

**~50 LOC.** `SmcRunner.get_narrative()` — exception-safe, перевіряє `smc.narrative.enabled`. `ws_server._build_full_frame()` інжектить narrative тільки у full frames (N4). ATR estimate = `h - l` останнього бару (v1 approximation).

### P5: UI — `NarrativePanel.svelte` (NEW, ~120 LOC) + `ChartPane.svelte`

**Default collapsed** headline bar з mode-кольором, phase badge, expand arrow. Expanded: bias summary, scenarios (direction, entry_desc, trigger state badge з colour-coding, target, invalidation), FVG context, warnings. Auto-collapse 10s (SC-6). Position: `bottom:40px; left:10px; z-index:31`. Glassmorphic styling. Wired через `(currentFrame as any).narrative ?? null` в ChartPane.

### Rev 3: Counter-Trend Mode + Trigger Proximity/Displacement (2026-03-15)

**S1 fix — Direction-Alignment Mismatch:** Previously Step 3 only checked `alignment=="aligned"` (D1==H4) without verifying if the zone direction matches HTF bias. When HTF=bullish but best zone=ob_bear → headline "🔴 SELL setup ready" + "HTF bullish aligned" = contradiction. Fix: `sub_mode="counter"` with 🟡 yellow headlines and `counter_trend` warning.

**S1 fix — Trigger Proximity/Displacement:** Previously `_resolve_trigger_state()` returned "triggered" for ANY CHoCH after zone anchor, regardless of price distance or impulse quality. Two doctrine violations:

- Price 84 pts (3.36 ATR) from zone → "triggered" (should be "approaching")
- CHoCH without displacement → "ready" (should require MS Shift = impulse)

Fix: 3 new helpers replace `_has_structure_aligned()`. Function signature expanded with `atr` and `config`. See trigger matrix above.

**Config keys added:** `trigger_proximity_atr=3.0`, `trigger_displacement_window=3`.

**Tests:** 513/513 pass (3 new trigger matrix tests). 5-case manual validation confirmed.

### Rev 4: Audit Fixes P1–P6 + P5B FVG Candidate Gate (2026-03-16)

Source: `research/Context.txt` — 6-defect audit of `narrative.py`.

**P1 — 'counter' sub_mode TS contract:** `ui_v4/src/types.ts` — додано `'counter'` до `sub_mode` union type. `NarrativePanel.svelte` — `.counter` CSS клас з жовтою рамкою.

**P2 — Directional gates in target resolution:** `_find_target_key_level()` і `_find_target_swing()` тепер фільтрують: для bullish — target > current_price, для bearish — target < current_price. Раніше могли повернути target позаду ціни.

**P3 — Phase hysteresis fix:** `_detect_market_phase()` виправлено: перевірка `phase_hysteresis_bars` тепер рахує **строго останні N свінгів підряд** (slice від кінця), а не голосування з `count()`/`len()` що нормалізувалось некоректно.

**P4 — Per-scenario trigger class:** `NarrativePanel.svelte` — замінено глобальний `$derived(triggerClass)` на функцію `getTriggerClass(state)` яка визначає CSS клас для кожного сценарію індивідуально.

**P5B — FVG candidate gate (→ ADR-0029):** `_select_candidate_zones()` тепер підтримує FVG-зони для narrative:
- Новий конфіг-ключ `fvg_trade_min_score: 99` — FVG зона стає кандидатом тільки при score ≥ threshold
- Значення 99 = FVG-зони отримують grade badge, але НІКОЛИ не стають trade кандидатами (display-only)
- Щоб увімкнути FVG як trade candidates → знизити `fvg_trade_min_score` до 4
- Scoring реалізовано в `core/smc/confluence.py:score_fvg_confluence()` (ADR-0029 amendment)

**P6 — HTF-aligned sort bonus:** `_select_candidate_zones()` — зони з HTF alignment отримують +3 sort bonus (sort key: `(-score - bonus, open_ms)`). Гарантує що HTF-aligned зони виходять першими.

**Config changes (Rev 4):**
- `smc.narrative.fvg_trade_min_score: 99` (P5B gate)

**Files touched:** `core/smc/narrative.py`, `core/smc/confluence.py`, `ui_v4/src/types.ts`, `ui_v4/src/layout/NarrativePanel.svelte`, `config.json`.

**Tests:** 535/535 pass (8 new P1–P6 tests + 6 new P5B confluence tests + 2 P5B candidate gate tests).

### Feature Gate

`config.json → smc.narrative.enabled = false` за замовчуванням. Zero impact коли вимкнено.

---

## 6. Наслідки

### Що змінюється

| Файл | Зміна | LOC |
|------|-------|-----|
| `core/smc/types.py` | +NarrativeBlock, +ActiveScenario dataclasses | ~25 |
| `core/smc/narrative.py` | **New file**: synthesize_narrative() + 6 helpers | ~150 |
| `core/smc/engine.py` | `_filter_for_display()`: FVG-OB overlap check | ~15 |
| `runtime/smc/smc_runner.py` | `get_narrative()` method | ~15 |
| `runtime/ws/ws_server.py` | `_build_full_frame()`: +narrative field | ~5 |
| `ui_v4/src/types.ts` | +NarrativeBlock, +ActiveScenario interfaces | ~15 |
| `ui_v4/src/stores/smcStore.ts` | +narrative field in SmcData, applySmcFull | ~5 |
| `ui_v4/src/layout/NarrativePanel.svelte` | **New file**: narrative display component | ~70 |
| `ui_v4/src/layout/ChartPane.svelte` | Mount NarrativePanel | ~5 |
| `config.json` | `smc.narrative` section + `smc.display.fvg_ob_overlap_hide` | ~15 |
| `tests/test_smc_narrative.py` | **New test file**: 8+ test cases | ~120 |

### Що НЕ змінюється

- SmcEngine core algorithms (detectors, lifecycle) — untouched
- Existing zone rendering pipeline — untouched
- BiasBanner — untouched (narrative є доповненням, не заміною)
- Delta frame protocol — unchanged (narrative only in full frames)
- Confluence scoring — untouched (consumed, not modified)
- Display budget / elimination engine — only FVG overlap addition

### Нові інваріанти — Rev 2

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| N0 | `narrative.py` = pure, no I/O | I0 AST gate covers `core/smc/` |
| N1 | Deterministic: same inputs → same NarrativeBlock | Test with frozen snapshot |
| N2 | Config SSOT: all thresholds from `config.json:smc.narrative` | No hardcoded values |
| N3 | Graceful degradation: if narrative fails → fallback NarrativeBlock (mode=wait, warnings), NOT None (BH-8) | try/except with log + fallback |
| N4 | Narrative MUST NOT be computed in delta path. Only full frame (BH-9) | Code rail: assert |
| N5 | `market_phase` = display-only, does NOT influence mode selection (T-6) | Verified in tests |
| N6 | HTF alignment = D1+H4 only. LTF disagreement is NOT "mixed bias" (SC-4) | Logic in synthesize_narrative() |
| N7 | Counter-trend = HTF aligned but zone opposes HTF direction → 🟡 warning (Rev 3) | Step 3 direction check |
| N8 | Trigger "ready" requires displacement, not just CHoCH (Rev 3) | `_has_displacement_near()` |
| N9 | Trigger "triggered" requires proximity ≤ `trigger_proximity_atr` × ATR (Rev 3) | `_is_price_proximate()` |
| N10 | Target directional gate: bullish target > price, bearish target < price (Rev 4, P2) | `_find_target_key_level()`, `_find_target_swing()` |
| N11 | FVG candidate gate: FVG zone needs score ≥ `fvg_trade_min_score` to be narrative candidate (Rev 4, P5B) | `_select_candidate_zones()` config check |
| N12 | HTF-first ranking: HTF-aligned zones sort before non-aligned at equal grade (Rev 4, P6) | +3 sort bonus in `_select_candidate_zones()` |

### Performance / SLO

- Narrative computation: ~1–3 ms (string formatting + sort + filter). Нижче S4 budget.
- Wire size increase: ~200–400 bytes per full frame (negligible vs candle payload).
- No impact на delta frame latency (narrative absent from deltas).

---

## 7. Rollback

### Per-slice rollback

| Slice | Rollback |
|-------|----------|
| P1 | `git checkout -- core/smc/types.py ui_v4/src/types.ts` |
| P2 | `rm core/smc/narrative.py tests/test_smc_narrative.py` |
| P3 | `git checkout -- core/smc/engine.py` |
| P4 | `git checkout -- runtime/smc/smc_runner.py runtime/ws/ws_server.py` |
| P5 | `rm ui_v4/src/layout/NarrativePanel.svelte` + `git checkout -- ui_v4/src/stores/smcStore.ts ui_v4/src/layout/ChartPane.svelte` |

### Full rollback

```bash
git checkout -- core/smc/types.py core/smc/engine.py runtime/smc/smc_runner.py runtime/ws/ws_server.py
rm core/smc/narrative.py tests/test_smc_narrative.py
# UI
git checkout -- ui_v4/src/types.ts ui_v4/src/stores/smcStore.ts ui_v4/src/layout/ChartPane.svelte
rm ui_v4/src/layout/NarrativePanel.svelte
cd ui_v4 && npm run build
# Config: remove smc.narrative section from config.json
```

### Feature flag

`config.json:smc.narrative.enabled = false` → `get_narrative()` returns None → WS frame без narrative → UI показує нічого. **Zero impact при вимкненні.**

---

## 8. Open Questions — Rev 2

| # | Питання | Хто перевіряє | Рекомендація |
|---|---------|---------------|-------------|
| Q1 | Чи потрібен narrative в delta frames (live update кожен бар)? | R_TRADER + R_CHART_UX | **Ні для v1.** Full frame достатньо. N4 constraint: delta path excluded. |
| Q2 | Чи потрібен market_phase у v1? | R_TRADER | **Так, display-only.** 3 trend states (trending_up/down/ranging). With hysteresis. N5 constraint. |
| Q3 | Мова narrative: English? Ukrainian? Configurable? | R_TRADER + R_CHART_UX | **English для v1.** Trading terms інтернаціональні. |
| Q4 | FVG-OB overlap threshold | R_SMC_CHIEF | **Resolved (SC-2)**: any overlap > 0 + time adjacency. No percentage threshold. |
| Q5 | NarrativePanel position: overlay чи sidebar? | R_CHART_UX | **Overlay (top-left, collapsible, default collapsed — SC-6)** для v1. |
| Q6 | Session levels (Asia H/L, London H/L) в target resolution + killzone awareness? | R_TRADER + R_ARCHITECT | **Deferred.** Session module = окремий ADR. SmcLevel не має session kinds. T-3/T-7 зафіксовано як explicit limitation v1. |
| Q7 | `bias_summary` = повторює Banner чи дає нову інформацію? | R_TRADER | **Resolved (T-8)**: bias_summary = context beyond direction pills. Not duplication of BiasBanner. |

---

## 9. Cross-Role Review Log (Rev 2)

### Round 1 Summary

| ID | Source | Sev | Issue | Decision |
|----|--------|-----|-------|----------|
| T-1 | R_TRADER | S1 | Single scenario → no fallback on invalidation | **ACCEPT**: `scenarios: List`, max 2 |
| T-2 | R_TRADER | S1 | Trigger missing IOFED 5-step awareness | **ACCEPT**: 4 trigger states (approaching/in_zone/triggered/ready) |
| T-3 | R_TRADER | S2 | No session/killzone awareness | **DEFER** → Q6. Session module = separate ADR |
| T-4 | R_TRADER | S2 | Confidence = misleading linear normalization | **ACCEPT**: Removed confidence field |
| T-5 | R_TRADER | S3 | FVG context wording not actionable | **ACCEPT**: 2 template variants |
| T-6 | R_TRADER | S3 | Market phase should be display-only | **ACCEPT**: Added N5 constraint |
| T-7 | R_TRADER | S2 | No session H/L in target resolution | **DEFER** → Q6. Same as T-3 |
| T-8 | R_TRADER | S2 | bias_summary duplicates BiasBanner | **ACCEPT**: Context beyond pills |
| SC-1 | R_SMC_CHIEF | S1 | Fake Wyckoff phases without volume | **ACCEPT**: 3 trend states, no Wyckoff names |
| SC-2 | R_SMC_CHIEF | S2 | FVG overlap 50% threshold arbitrary | **ACCEPT**: Any overlap > 0 |
| SC-3 | R_SMC_CHIEF | S2 | "Caution" mode has no ICT equivalent | **ACCEPT**: 2 modes (trade/wait) + sub_mode |
| SC-4 | R_SMC_CHIEF | S2 | Alignment ignores TF hierarchy | **ACCEPT**: HTF only (D1+H4). N6 constraint |
| SC-5 | R_SMC_CHIEF | S3 | next_area undefined format | **ACCEPT**: Structured format |
| SC-6 | R_SMC_CHIEF | S2 | Panel pollutes chart (Clean Chart Doctrine) | **ACCEPT**: Default collapsed, auto-collapse |
| SC-7 | R_SMC_CHIEF | S2 | No invalidation detection | **ACCEPT**: current_price check before scenario |
| BH-1 | R_BUG_HUNTER | S0 | Missing current_price param | **ACCEPT**: Added to signature |
| BH-2 | R_BUG_HUNTER | S1 | Type error: not Optional | **ACCEPT**: scenarios=list, target=Optional |
| BH-3 | R_BUG_HUNTER | S1 | Hardcoded max score denominator | **ACCEPT**: Removed confidence (T-4) |
| BH-4 | R_BUG_HUNTER | S1 | "No clear target" = silent fallback | **ACCEPT**: target_desc Optional, warnings list |
| BH-5 | R_BUG_HUNTER | S1 | Wire serialization undefined | **ACCEPT**: Defined + size guard |
| BH-6 | R_BUG_HUNTER | S2 | Phase jitter on volatile sessions | **MITIGATED**: Hysteresis config |
| BH-7 | R_BUG_HUNTER | — | Race on frozen snapshot | **NOT A BUG**: SmcSnapshot frozen=True |
| BH-8 | R_BUG_HUNTER | S1 | None fallback = I5 violation | **ACCEPT**: Fallback NarrativeBlock, N3 updated |
| BH-9 | R_BUG_HUNTER | S3 | Delta frame guard missing | **ACCEPT**: N4 constraint added |
| BH-10 | R_BUG_HUNTER | S3 | None vs "" mixed convention | **ACCEPT**: Documented convention |

### Deferred Items (separate ADR needed)

| Item | Why Deferred | Prerequisite |
|------|-------------|--------------|
| T-3/T-7: Session/killzone module | SmcLevel lacks session level kinds. Market calendar integration needed. | ADR for `core/smc/sessions.py` |
| SC-1 (full): Volume-based Wyckoff phases | No volume profile in SmcEngine. | ADR for volume integration |

### Explicit v1 Limitations

1. **No session/killzone awareness**: Narrative doesn't know about trading sessions or killzones. Trades outside killzone are not flagged. (T-3)
2. **No session levels in targets**: Asia H/L, London H/L not available for target resolution. (T-7)
3. **Trend context approximation**: `market_phase` uses swing-based heuristic only. Display-only, does not affect mode selection. (T-6, SC-1)
4. **Max 2 scenarios**: Alternative scenario = next qualifying zone. Not full IOFED drill for alternative. (T-1)

---

## 10. Cross-Role Implementation Plan

| Phase | Role | Task |
|-------|------|------|
| **ACCEPT** | R_ARCHITECT | Finalize ADR-0033 після review |
| **P1 Types** | R_PATCH_MASTER | NarrativeBlock + ActiveScenario в Python + TS |
| **P2 Logic** | R_PATCH_MASTER | `narrative.py` + tests |
| **P3 FVG** | R_PATCH_MASTER | FVG-OB overlap in display filter |
| **P4 Wire** | R_PATCH_MASTER | smc_runner + ws_server wiring |
| **P5 UI** | R_CHART_UX | NarrativePanel.svelte + store + mount |
| **Review** | R_BUG_HUNTER | After each P-slice |
| **Validate** | R_TRADER | After P5: "Торгуешся з цього?" |
| **SMC Review** | R_SMC_CHIEF | P2 + P3: narrative logic + FVG policy |
| **Docs** | R_DOC_KEEPER | After all slices: sync ADR index + docs |

---

## Appendix A: Test Matrix for P2 — Rev 2

| Test Case | Input | Expected Narrative |
|-----------|-------|--------------------|
| `test_trade_aligned_bearish` | D1+H4 bearish, OB A(6) present, PDL level | mode="trade", sub_mode="aligned", headline="🔴 SELL setup ready", scenarios[0] with target=PDL |
| `test_trade_aligned_bullish` | D1+H4 bullish, OB A(7) present | mode="trade", sub_mode="aligned", headline="🟢 BUY setup ready" |
| `test_trade_reduced_mixed_htf` | D1↓ H4↑, OB A(6) present | mode="trade", sub_mode="reduced", headline contains "reduced" |
| `test_wait_no_setup` | No zones with grade ≥ A | mode="wait", scenarios=[], next_area non-empty |
| `test_wait_no_data` | Empty bias_map | mode="wait", warnings contains info |
| `test_two_scenarios` | OB bear A(6) + OB bull A(6) | scenarios length = 2 |
| `test_trigger_approaching` | Zone far from current_price | scenarios[0].trigger = "approaching" |
| `test_trigger_in_zone` | current_price within zone, no CHoCH | scenarios[0].trigger = "in_zone" |
| `test_trigger_ready` | In zone + CHoCH + displacement | scenarios[0].trigger = "ready" |
| `test_trigger_in_zone_choch_no_displacement` | In zone + CHoCH, no displacement | scenarios[0].trigger = "in_zone" (Rev 3: N8) |
| `test_trigger_approaching_far_with_choch` | CHoCH + displacement, >3 ATR from zone | scenarios[0].trigger = "approaching" (Rev 3: N9) |
| `test_trigger_triggered_proximate_with_displacement` | CHoCH + displacement, <3 ATR from zone | scenarios[0].trigger = "triggered" |
| `test_invalidation_crossed` | current_price > invalidation level | scenarios empty (zone discarded), warnings contains "scenario_invalidated" |
| `test_fvg_context_overlap` | OB + overlapping FVG | fvg_context contains "OB entry refined by FVG" |
| `test_fvg_context_empty` | OB without nearby FVG | fvg_context = "" |
| `test_market_phase_trending_down` | 2+ LH/LL swing pattern | market_phase = "trending_down" |
| `test_market_phase_ranging` | No clear pattern | market_phase = "ranging" |
| `test_target_none_fallback` | No key levels, no HTF zones, no swings | target_desc = None, warnings contains "no_target_found" |
| `test_degraded_fallback` | Trigger exception in synthesize | mode="wait", headline="⚠ Narrative unavailable", warnings=["computation_error"] |

## Appendix B: Future Extensions (NOT in this ADR)

| Extension | Trigger for ADR |
|-----------|----------------|
| **Delta narrative** (live update per bar) | Якщо трейдер скаржиться що narrative stale |
| **Session/killzone module** | ICT session-aware narrative (T-3/T-7) |
| **Volume-based market phases** | Wyckoff phases require volume profile (SC-1) |
| **Narrative history** (timeline of mode changes) | Replay mode enhancement |
| **Audio/notification** on mode transition | Alert system ADR |
| **Sidebar layout** (dedicated narrative panel area) | UI layout redesign |
| **i18n** (Ukrainian narratives) | Explicit user request |
