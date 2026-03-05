# ADR-0029: OB Confluence Scoring + Grade System

- **Статус**: **Implemented** (Φ1 done: 4 P-slices, 424/431 tests pass, 9 new confluence tests)
- **Дата**: 2026-03-05
- **Автор**: Chief Strategist + System Architect
- **Reviewer**: R_SMC_CHIEF (CONDITIONAL GO → 6 errata → **GO**)
- **Mode**: BUILD (новий модуль + тести, >150 LOC total — правило A1)
- **Initiative**: SMC-VIS-Φ1 (Confluence Scoring)
- **Залежності**: ADR-0024 (SMC Engine), ADR-0028 v2 (Elimination Engine — Φ0 DONE)
- **Scope**: `core/smc/confluence.py` (pure scoring) + `engine.py` (integration) + UI (grade badge + budget) + config
- **Наступний**: ADR-0030 (Wick-OB Detector) — scoring з 0029 працює автоматично

---

## 0. Review Log

### Errata (applied 2026-03-05, R_SMC_CHIEF review)

| # | Дефект | Sev | Fix | Статус |
|---|--------|-----|-----|--------|
| E1 | F1: `s.get('swept')` — поле `swept` не існує в SmcSwing [VERIFIED types.py] | S1 | Scoring приймає `bars` в сигнатурі, F1 обчислює sweep in-situ (`bar.l < swing.price` у вікні) | ✅ Applied |
| E2 | F7: `s.get('direction')` не існує; `s.get('bar_ms')` → `time_ms` [VERIFIED types.py, structure.py] | S2 | Direction з `kind.endswith('_bull')`, timestamp = `time_ms` | ✅ Applied |
| E3 | F1/F4/F6: `s['type']` → `s['kind']`, values `'high'/'low'` → `'swing_high'/'swing_low'` [VERIFIED types.py] | S2 | Replaced throughout F1, F4, F6 | ✅ Applied |
| E4 | SmcDelta wire format не має `zone_grades` [VERIFIED types.py] | S2 | UI зберігає останній повний `zone_grades` і мержить з delta (видаляє mitigated) | ✅ Applied |
| E5 | HTF zones доступні тільки в `get_display_snapshot()`, не в `_compute_snapshot()` [VERIFIED engine.py:520-555] | S3 | Scoring виконується в `get_display_snapshot()` ПІСЛЯ cross-TF injection, ПЕРЕД return | ✅ Applied |
| ER-6 | `_filter_for_display()` виконується в `_compute_snapshot()` ДО scoring → grades=None завжди [VERIFIED engine.py:668] | S2 | Server-side `_zone_rank` залишається strength-only. Grade-aware фільтрація → тільки client-side (DisplayBudget.ts). Див. §R1 | ✅ Applied |

### Architectural Notes

| # | Тема | Рішення |
|---|------|---------|
| N1 | `_zone_rank` з grades потребувала closure pattern | → Знято через ER-6: rank залишається strength-only на сервері |
| N2 | FVG refinement ≠ confluence scoring — різна природа | Окремий P-slice (P-Φ1-1b) |
| N3 | Total LOC > 150 | MODE=BUILD (правило A1: новий модуль з тестами) |

---

## 1. Executive Summary

### Проблема

Після Φ0 (ADR-0028) графік чистіший — менше зон, budget cap працює. Але **всі зони виглядають однаково**. Трейдер бачить 6 зон на M15 і не знає яка з них A+ (варта entry) і яка C (шум). `strength` зараз = impulse/ATR ratio — одновимірна метрика що не враховує контекст.

### Що є зараз (VERIFIED)

- `engine.py:detect_order_blocks()` → знаходить OB candidates [VERIFIED core/smc/order_blocks.py]
- `engine.py:65-68` → `_zone_rank(z)` = `(-strength, status_rank, -anchor_bar_ms, id)` [VERIFIED]
- `SmcZone.strength` = impulse/ATR (float) [VERIFIED core/smc/types.py]
- `SmcZone` = frozen dataclass: id, kind, symbol, tf_s, high, low, start_ms, end_ms, anchor_bar_ms, status, strength, context_layer [VERIFIED]
- `SmcZone` **НЕ має** поля `grade` або `confluence_score` [VERIFIED]
- `POI_GRADES = frozenset({"A+","A","B","C"})` — оголошено але **НЕ використовується** [VERIFIED types.py]
- `DisplayBudget.ts` (Φ0) → per-side budget, opacity = `strengthToOpacity(strength)` [VERIFIED]
- `SmcSwing` fields: `id, symbol, tf_s, kind, price, time_ms, confirmed` [VERIFIED types.py]
- Structure events = SmcSwing з `kind` ∈ `{bos_bull, bos_bear, choch_bull, choch_bear}`, `time_ms` [VERIFIED structure.py]
- `SmcSnapshot.to_wire()` → `{zones, swings, levels, trend_bias}` — no grades [VERIFIED types.py]

### Рішення

**Scoring layer поверх існуючого detector** — enhancement, не rewrite.

```
detect_order_blocks() → OB candidates         (НЕЗМІННИЙ)
       ↓
score_zone_confluence(zone, ctx) → grade       ← NEW (core/smc/confluence.py)
       ↓
zone_grades dict → wire payload                ← NEW (engine.py)
       ↓
DisplayBudget.ts → grade-aware Focus filter    ← EXTEND (client-only, ER-6)
       ↓
OverlayRenderer.ts → grade badge "A+"          ← NEW (client)
```

Scoring **не знає і не має знати** як зона була знайдена. Body-OB, Wick-OB (ADR-0030), Breaker-OB — scoring приймає SmcZone і ранжує за контекст.

### Scope guard

| В scope | НЕ в scope |
|---------|------------|
| Confluence scoring function (pure) | Wick-OB detection (ADR-0030) |
| Grade assignment (A+/A/B/C) | OB boundary rewrite (тіло vs тінь) |
| Grade badge в UI | Breaker/Mitigation rendering |
| FVG strength refinement + partial fill | Alignment Banner (Φ3) |
| Grade-aware DisplayBudget (client) | Server-side `_zone_rank` з grade (→ §R1) |
| `zone_grades` у wire payload | Risk Calculator |

---

## 2. Контекст: SMC-канон confluence

З верифікованого документа (Doc 1 §3.1, §5.1; Doc 2 §1):

**OB мінімально валідний** = контекст + зняття ліквідності + поглинання + правильні межі.

**Чому "просто impulse/ATR" недостатньо**: великий імпульс на новинах (NFP) може створити OB зі strength=0.9, але якщо він в premium без HTF alignment і без liquidity sweep — це шум, не A+ зона. Контекст важливіший за розмір.

---

## 3. Confluence Scoring Function

### 3.1 Сигнатура (E1 fix: `bars` в параметрах)

**Файл**: `core/smc/confluence.py` (НОВИЙ, pure, zero I/O)

```python
"""ADR-0029: OB Confluence Scoring. Pure function, zero I/O."""

def score_zone_confluence(
    zone,           # SmcZone dict (from .to_wire())
    bars,           # List[bar dicts] — для sweep detection in-situ (E1)
    swings,         # List[SmcSwing dicts]
    zones_all,      # List[SmcZone dicts] — для FVG-after check
    htf_zones,      # List[SmcZone dicts] — cross-TF context (E5: available in get_display_snapshot)
    structure,      # List[SmcSwing dicts] — BOS/CHoCH events
    atr,            # float — ATR(14) for this TF
    current_price,  # float — для premium/discount
    tf_s,           # int — timeframe seconds
    config,         # SmcConfluenceConfig attrs
) -> dict:
    """Returns: {'score': int (0-11), 'grade': str, 'factors': list[str]}"""
```

### 3.2 Factors (8 факторів, max 11 балів)

| # | Factor | Pts | Умова | SMC Canon | Складність |
|---|--------|-----|-------|-----------|------------|
| F1 | **Liquidity sweep** | +2 | Swing зняте у вікні N барів перед OB anchor (in-situ з bars) | Doc 1 §3.1 | MEDIUM |
| F2 | **FVG after engulfing** | +2 | FVG знайдено у вікні 3 барів після OB anchor | Doc 1 §3.1 | LOW |
| F3 | **HTF zone alignment** | +2 | OB mid-price всередині HTF zone [high, low] | Doc 1 §5.4 | LOW |
| F4 | **Extremum position** | +1 | OB anchor = swing point ± tolerance | Doc 1 §3.1 | LOW |
| F5 | **Strong impulse** | +1 | zone.strength ≥ threshold | Implied | TRIVIAL |
| F6 | **P/D alignment** | +1 | Bullish OB in discount / Bearish OB in premium | Doc 1 §3.1 | MEDIUM |
| F7 | **Structure confirm** | +1 | BOS/CHoCH у напрямку OB після anchor | Doc 1 §2.2 | LOW |
| F8 | **TF significance** | +1 | tf_s ≥ 14400 (H4+) | Doc 1 §2.1 | TRIVIAL |

**A+ потребує ≥8**: мінімум 3 "великих" (F1+F2+F3 = 6) + 2 "малих". A+ badge рідкісний — правильна жорсткість.

### 3.3 Factor Implementations (E1/E2/E3 applied)

#### F1: Liquidity Sweep (+2) — E1 fix: in-situ з bars

```python
def _check_liquidity_sweep(zone, bars, swings, tf_s, config):
    """Sweep обчислюється in-situ: bar.l < swing.price у вікні перед OB."""
    lookback = config.get('sweep_lookback_bars', 10)
    tf_ms = tf_s * 1000
    anchor = zone.get('anchor_bar_ms', 0)
    window_start = anchor - lookback * tf_ms

    relevant = [s for s in swings
                if s['kind'] in ('swing_high', 'swing_low')       # E3
                and window_start <= s.get('time_ms', 0) < anchor]  # E2: time_ms
    if not relevant:
        return 0
    for sw in relevant:
        sw_price = sw.get('price', 0)
        sw_time = sw.get('time_ms', 0)
        sweep_bars = [b for b in bars
                      if sw_time < b.get('open_time_ms', 0) <= anchor]
        if zone['kind'].startswith('ob_bull'):
            if any(b.get('l', 999999) < sw_price
                   for b in sweep_bars if sw['kind'] == 'swing_low'):
                return 2
        else:
            if any(b.get('h', 0) > sw_price
                   for b in sweep_bars if sw['kind'] == 'swing_high'):
                return 2
    return 0
```

#### F2: FVG After Engulfing (+2)

```python
def _check_fvg_after(zone, all_zones, tf_s, config):
    tf_ms = tf_s * 1000
    window = config.get('fvg_lookforward_bars', 3) * tf_ms
    anchor = zone.get('anchor_bar_ms', 0)
    return 2 if any(z for z in all_zones
                    if z.get('kind', '').startswith('fvg')
                    and anchor < z.get('anchor_bar_ms', 0) <= anchor + window) else 0
```

#### F3: HTF Zone Alignment (+2)

```python
def _check_htf_alignment(zone, htf_zones):
    mid = (zone['high'] + zone['low']) / 2
    return 2 if any(hz for hz in htf_zones
                    if hz.get('low', 0) <= mid <= hz.get('high', 0)
                    and hz.get('status') == 'active') else 0
```

#### F4: Extremum Position (+1) — E3 fix

```python
def _check_extremum(zone, swings, atr, tf_s, config):
    tol = config.get('extremum_tolerance_atr', 0.3) * atr
    tf_ms = tf_s * 1000
    for s in swings:
        if abs(s.get('time_ms', 0) - zone.get('anchor_bar_ms', 0)) < tf_ms * 3:
            if zone['kind'].startswith('ob_bull') and s['kind'] == 'swing_low':
                if abs(s['price'] - zone['low']) < tol:
                    return 1
            elif zone['kind'].startswith('ob_bear') and s['kind'] == 'swing_high':
                if abs(s['price'] - zone['high']) < tol:
                    return 1
    return 0
```

#### F5: Strong Impulse (+1)

```python
def _check_impulse(zone, config):
    return 1 if zone.get('strength', 0) >= config.get('strong_impulse_threshold', 0.7) else 0
```

#### F6: Premium/Discount Alignment (+1) — E3 fix

```python
def _check_premium_discount(zone, current_price, swings):
    highs = [s['price'] for s in swings if s['kind'] == 'swing_high'][-5:]
    lows = [s['price'] for s in swings if s['kind'] == 'swing_low'][-5:]
    if not highs or not lows:
        return 0
    mid = (max(highs) + min(lows)) / 2
    if zone['kind'].startswith('ob_bull') and current_price < mid:
        return 1
    if zone['kind'].startswith('ob_bear') and current_price > mid:
        return 1
    return 0
```

#### F7: Structure Confirmation (+1) — E2 fix

```python
def _check_structure(zone, structure):
    is_bull_ob = zone['kind'].startswith('ob_bull')
    anchor = zone.get('anchor_bar_ms', 0)
    for s in structure:
        if s.get('time_ms', 0) > anchor:                           # E2: time_ms, not bar_ms
            s_bull = s.get('kind', '').endswith('_bull')             # E2: direction from kind
            if (is_bull_ob and s_bull) or (not is_bull_ob and not s_bull):
                return 1
    return 0
```

#### F8: TF Significance (+1)

```python
def _check_tf_significance(tf_s):
    return 1 if tf_s >= 14400 else 0
```

### 3.4 Orchestrator + Grade Mapping

```python
def _score_to_grade(score, config):
    thresholds = config.get('grade_thresholds', {'a_plus': 8, 'a': 6, 'b': 4})
    if score >= thresholds.get('a_plus', 8): return 'A+'
    if score >= thresholds.get('a', 6):      return 'A'
    if score >= thresholds.get('b', 4):      return 'B'
    return 'C'

def score_zone_confluence(zone, bars, swings, zones_all, htf_zones,
                          structure, atr, current_price, tf_s, config):
    if not zone.get('kind', '').startswith('ob_'):
        return {'score': 0, 'grade': 'C', 'factors': []}
    factors = []
    score = 0
    checks = [
        ('sweep',     _check_liquidity_sweep, (zone, bars, swings, tf_s, config)),
        ('fvg_after', _check_fvg_after,       (zone, zones_all, tf_s, config)),
        ('htf_align', _check_htf_alignment,   (zone, htf_zones)),
        ('extremum',  _check_extremum,        (zone, swings, atr, tf_s, config)),
        ('impulse',   _check_impulse,         (zone, config)),
        ('pd_align',  _check_premium_discount,(zone, current_price, swings)),
        ('structure', _check_structure,       (zone, structure)),
        ('tf_sig',    _check_tf_significance, (tf_s,)),
    ]
    for name, fn, args in checks:
        pts = fn(*args)
        if pts > 0:
            factors.append("{} +{}".format(name, pts))
            score += pts
    return {'score': score, 'grade': _score_to_grade(score, config), 'factors': factors}
```

### 3.5 FVG Strength Refinement (P-Φ1-1b)

```python
def score_fvg_strength(fvg_zone, atr, partial_fill_pct=0.0):
    """FVG strength = f(gap_size/ATR, partial_fill). Окрема від confluence scoring."""
    gap = fvg_zone.get('high', 0) - fvg_zone.get('low', 0)
    ratio = gap / atr if atr > 0 else 0
    if ratio > 1.5:     base = 0.9
    elif ratio > 0.8:   base = 0.6
    elif ratio > 0.3:   base = 0.3
    else:                base = 0.1
    if partial_fill_pct > 0.8:   base *= 0.3
    elif partial_fill_pct > 0.5: base *= 0.5
    elif partial_fill_pct > 0.0: base *= 0.7
    return min(1.0, base)
```

---

## 4. Integration

### 4.1 engine.py: Scoring Point (E5 fix)

Scoring виконується у `get_display_snapshot()`, **ПІСЛЯ** cross-TF injection (HTF zones доступні для F3), **ПЕРЕД** return:

```python
# engine.py: get_display_snapshot()
# ... steps 1-6: viewer mapping, swings, structure, FVG inject, Context Stack, key levels ...
# На цьому етапі: base_zones = local + HTF zones, bars/atr/last_bar вже обчислені

zone_grades = {}
for z in base_zones:
    z_wire = z.to_wire()
    if z_wire.get('kind', '').startswith('ob_'):
        htf_context = [hz.to_wire() for hz in base_zones if hz.tf_s > base_tf]
        struct_events = [s.to_wire() for s in base_swings
                         if s.kind.startswith('bos_') or s.kind.startswith('choch_')]
        bar_dicts = [{'open_time_ms': b.open_time_ms, 'h': b.h, 'l': b.l}
                     for b in bars[-50:]]
        swing_dicts = [s.to_wire() for s in base_swings
                       if not s.kind.startswith('bos_') and not s.kind.startswith('choch_')]
        result = score_zone_confluence(
            zone=z_wire, bars=bar_dicts,
            swings=swing_dicts,
            zones_all=[zz.to_wire() for zz in base_zones],
            htf_zones=htf_context, structure=struct_events,
            atr=atr, current_price=last_bar.c, tf_s=base_tf,
            config=self._config.confluence.__dict__,
        )
        zone_grades[z_wire['id']] = result
```

### 4.2 Wire Format (E4 fix)

**Full frame**: `zone_grades` як окремий dict у snapshot wire:

```python
# SmcSnapshot.to_wire() НЕ змінюється (S6 invariant)
# Натомість: caller (ws_server) додає zone_grades до frame:
smc_wire = snap.to_wire()
smc_wire['zone_grades'] = zone_grades  # {'zone_id': {'score': 8, 'grade': 'A+', 'factors': [...]}}
```

**Delta frame** (E4): grades приходять тільки з full frame. Client:

- Зберігає останній `zone_grades` dict
- `mitigated_zone_ids` → видаляє з cache
- Нові зони без grade → `'C'` до наступного full frame

### 4.3 Server-Side Rank: Без Змін (ER-6 fix)

`_zone_rank(z)` залишається `(-strength, status_rank, -anchor_bar_ms, id)`. Не змінюється. Grade-aware фільтрація — тільки client-side.

Обґрунтування ER-6 та відкладені альтернативи → §R1.

### 4.4 Client: DisplayBudget.ts — Grade Filter

```typescript
// applyBudget() extension — grade-aware budget у Focus mode:
function gradePassesFocus(zoneId: string, grades: Record<string, any>): boolean {
    const g = (grades[zoneId] || {}).grade || 'C';
    return g === 'A+' || g === 'A';
}

// Focus path: filter by grade BEFORE per-side slicing
const gradeFiltered = zones.filter(z => gradePassesFocus(z.id, grades));
const supply = gradeFiltered.filter(isBearishZone).slice(0, config.perSide);
const demand = gradeFiltered.filter(isBullishZone).slice(0, config.perSide);

// Research path: show A+/A/B, hide C
const researchFiltered = zones.filter(z => {
    const g = (grades[z.id] || {}).grade || 'C';
    return g !== 'C';
});
```

### 4.5 Client: OverlayRenderer.ts — Grade Badge

```typescript
function renderGradeBadge(ctx: CanvasRenderingContext2D, grade: string, x: number, y: number) {
    if (!grade || grade === 'C') return;
    const colors: Record<string, string> = {
        'A+': 'rgba(255,215,0,0.9)',     // gold
        'A':  'rgba(255,255,255,0.65)',   // white
        'B':  'rgba(150,150,150,0.45)',   // gray
    };
    ctx.font = 'bold 10px Arial';
    const m = ctx.measureText(grade);
    const pad = 3;
    ctx.fillStyle = colors[grade] || colors['B'];
    ctx.beginPath();
    ctx.roundRect(x, y, m.width + pad * 2, 14 + pad, 3);
    ctx.fill();
    ctx.fillStyle = '#000';
    ctx.fillText(grade, x + pad, y + 12);
}
```

**Placement**: верхній лівий кут зони, badge ≤ 3 символи.

### 4.6 Client: Grade Cache (E4 fix)

```typescript
// OverlayRenderer або SmcStore:
let cachedGrades: Record<string, {score: number; grade: string}> = {};

function onFullFrame(snapshot: any) {
    cachedGrades = snapshot.zone_grades || {};
}
function onDelta(delta: any) {
    for (const id of delta.mitigated_zone_ids || []) {
        delete cachedGrades[id];
    }
}
```

---

## 5. Config

### 5.1 config.json extension

```json
{
  "smc": {
    "confluence": {
      "sweep_lookback_bars": 10,
      "fvg_lookforward_bars": 3,
      "extremum_tolerance_atr": 0.3,
      "strong_impulse_threshold": 0.7,
      "grade_thresholds": { "a_plus": 8, "a": 6, "b": 4 }
    }
  }
}
```

### 5.2 SmcConfluenceConfig dataclass

```python
@dataclass
class SmcConfluenceConfig:
    sweep_lookback_bars: int = 10
    fvg_lookforward_bars: int = 3
    extremum_tolerance_atr: float = 0.3
    strong_impulse_threshold: float = 0.7
    grade_thresholds_a_plus: int = 8
    grade_thresholds_a: int = 6
    grade_thresholds_b: int = 4

    @classmethod
    def from_dict(cls, d):
        gt = d.get('grade_thresholds', {})
        return cls(
            sweep_lookback_bars=d.get('sweep_lookback_bars', 10),
            fvg_lookforward_bars=d.get('fvg_lookforward_bars', 3),
            extremum_tolerance_atr=d.get('extremum_tolerance_atr', 0.3),
            strong_impulse_threshold=d.get('strong_impulse_threshold', 0.7),
            grade_thresholds_a_plus=gt.get('a_plus', 8),
            grade_thresholds_a=gt.get('a', 6),
            grade_thresholds_b=gt.get('b', 4),
        )
```

Додається як поле `confluence: SmcConfluenceConfig` в `SmcConfig`.

---

## 6. P-Slices

### P-Φ1-1a: `core/smc/confluence.py` — OB Scoring (~65 LOC)

- Новий файл: `score_zone_confluence()` + 8 factor functions + `_score_to_grade()`
- `SmcConfluenceConfig` в config.py
- `config.json:smc.confluence` секція
- **Gate**: unit test — фіксована зона + mock context → expected score/grade
- **Rollback**: delete `confluence.py`, remove config

### P-Φ1-1b: FVG Strength Refinement (~15 LOC)

- `score_fvg_strength()` в тому ж `confluence.py`
- **Gate**: unit test — gap/ATR ratios → expected strength
- **Rollback**: remove function

### P-Φ1-2: engine.py Integration (~25 LOC)

- Scoring call у `get_display_snapshot()` після cross-TF injection (E5)
- `zone_grades` dict додається у wire payload
- **Gate**: snapshot WS frame містить `zone_grades`, grades коректні для відомих зон
- **Rollback**: remove scoring call + `zone_grades` from payload

### P-Φ1-3: UI — Grade Badge + Budget Filter (~25 LOC)

- `OverlayRenderer.ts`: `renderGradeBadge()` + grade cache
- `DisplayBudget.ts`: grade-aware filter (A+/A pass Focus, C hidden)
- **Gate**: visual — A+ gold badge, B dim, C hidden in Focus
- **Rollback**: remove badge, revert DisplayBudget

### Total: ~130 LOC prod + ~60 LOC tests = ~190 LOC. 1 новий файл. MODE=BUILD

---

## 7. Acceptance Criteria

| AC | Given | When | Then |
|----|-------|------|------|
| AC-1 | OB + sweep + FVG after + HTF aligned (score ≥ 8) | Scoring | Grade A+, gold badge |
| AC-2 | OB без sweep, без HTF, weak impulse (score ≤ 3) | Scoring | Grade C, hidden у Focus |
| AC-3 | Два OB: один A+, один B | Focus mode | A+ visible + badge, B hidden |
| AC-4 | FVG gap > 1.5×ATR, not filled | FVG strength | strength ≥ 0.9 |
| AC-5 | FVG filled > 50% | FVG strength | strength × 0.5 (decay) |
| AC-6 | Same zone + same context, twice | Determinism | Same score (S2) |
| AC-7 | Future Wick-OB zone (kind='ob_*') | Scoring | Grade assigned correctly |

---

## 8. Invariant Compliance

| Інваріант | Вплив |
|-----------|-------|
| I0 | ✅ `confluence.py` = `core/smc/` (pure). Zero I/O |
| S0 | ✅ Pure function, no side effects |
| S2 | ✅ Deterministic: same zone + same context = same score |
| S5 | ✅ All thresholds from `config.json:smc.confluence` |
| S6 | ✅ Wire format: `zone_grades` as separate dict. `SmcZone` / `SmcSnapshot.to_wire()` **UNCHANGED** |
| D0 (ADR-0028) | ✅ Scoring = compute layer. Badge = display layer. Separate concerns |

---

## 9. Rollback

| P-slice | Rollback |
|---------|----------|
| P-Φ1-1a | Delete `core/smc/confluence.py`. Remove `SmcConfluenceConfig`. Remove `config.json:smc.confluence` |
| P-Φ1-1b | Remove `score_fvg_strength()` from confluence.py |
| P-Φ1-2 | Remove scoring call + `zone_grades` from `engine.py:get_display_snapshot()` |
| P-Φ1-3 | Remove `renderGradeBadge()`. Revert `DisplayBudget.ts`. Remove grade cache |

Full rollback: ~5 хв. System returns to Φ0 behavior (zones without grades).

---

## 10. Зв'язок з ADR-0030 (Wick-OB)

```
ADR-0029 (THIS):
  score_zone_confluence(zone, ...) → grade
  ↑ Приймає БУДЬ-ЯКУ SmcZone де kind='ob_*'
  ↑ Scoring zone-type-agnostic

ADR-0030 (NEXT):
  detect_wick_ob(bars, swings, config) → List[SmcZone]
  ↑ Повертає SmcZone з kind='ob_bull'/'ob_bear'
  ↑ Scoring з ADR-0029 працює автоматично day one
```

---

## 11. Очікуваний ефект

**До**: всі OB зони однакові візуально (strength-only opacity).

**Після**: трейдер бачить gold badge "A+" на зоні де confluence = sweep + FVG + HTF aligned. Зони без контексту (C) зникають з Focus. Зони з часткового контексту (B) видні тільки в Research.

На H4 chart: 1–2 зони з "A+" badge + 1–2 з "A". Решта — hidden або dim.

---

## R1. Відкладені рішення (Decision Log)

Тут зафіксовані альтернативи, які **свідомо відкладені**, з обґрунтуванням. Якщо в майбутньому контекст зміниться — ці записи пояснять чому було вирішено інакше.

### R1.1 Server-side `_zone_rank` з grade (ВІДКЛАДЕНО → ER-6)

**Що пропонувалось**: Модифікувати `_zone_rank(z)` → `_zone_rank(z, grades=None)` через closure `_make_rank_key(grades)`. Grade стає першим ключем сортування на сервері.

**Чому відкладено**:

- `_filter_for_display()` виконується у `_compute_snapshot()` [VERIFIED engine.py:668] — **до** scoring. Тому `grades=None` завжди. Щоб це виправити, потрібно або перенести `_filter_for_display` з `_compute_snapshot` в `get_display_snapshot`, або дублювати фільтрацію.
- Перенесення `_filter_for_display` — занадто великий blast radius для Φ1 (торкається display pipeline всіх TF). Це окремий initiative.
- Client-side grade filter у `DisplayBudget.ts` вже реалізує потрібну поведінку (Focus = A+/A only, Research = A+/A/B) і простіший у rollback.

**Коли повернутись**: Якщо виникне необхідність зменшити wire payload (не відправляти С-зони взагалі) — тоді server-side grade filter стане доцільним. Це буде окремий P-slice з переносом `_filter_for_display` у `get_display_snapshot`.

### R1.2 Grade поле в SmcZone dataclass (ВІДКЛАДЕНО)

**Що пропонувалось**: Додати `grade: Optional[str] = None` в SmcZone.

**Чому відкладено**:

- SmcZone = frozen dataclass. Додавання поля потребує `dataclasses.replace()` при кожному scoring.
- Scoring відбувається в `get_display_snapshot()` — це display path, не lifecycle. Grade не є властивістю зони, а властивістю контексту (змінюється при зміні HTF, price, structure).
- Окремий `zone_grades` dict — чистіший: не мутує SSOT зон, чіткий ownership, легкий rollback.

**Коли повернутись**: Якщо з'являться інші computed properties на зонах (confidence, risk score) — може мати сенс створити `SmcZoneDisplay` wrapper з усіма display-only полями. Поки цього не потрібно.

### R1.3 SmcDelta з zone_grades (ВІДКЛАДЕНО → E4)

**Що пропонувалось**: Додати `zone_grades` у `SmcDelta.to_wire()`.

**Чому відкладено**:

- Delta = інкрементальне оновлення. Scoring потребує повного контексту (HTF zones, all swings, structure) — partial re-scoring при delta ненадійний.
- Client cache + full-frame refresh — простіший і надійніший. Нова зона без grade = `'C'` до наступного full frame (1-2 секунди).
- SmcDelta.to_wire() залишається UNCHANGED (S6).

**Коли повернутись**: Якщо full-frame interval стане занадто довгим (>5с) і користувач помітить затримку появи badge — тоді додати scoring на delta path.

### R1.4 `_filter_for_display` перенос у `get_display_snapshot` (ВІДКЛАДЕНО)

**Що пропонувалось**: Перенести виклик `_filter_for_display()` з `_compute_snapshot()` у `get_display_snapshot()`, щоб display filter працював після scoring і cross-TF injection.

**Чому відкладено**:

- Blast radius: `_compute_snapshot()` повертає вже відфільтрований snapshot. Зміна порядку → зберігаємо більше зон у RAM-state → потенційний memory budget перевищення.
- Перенос display filter — це architectural change, не Φ1 scope. Потрібен окремий audit (RAM impact, performance budget).
- Для цілей Φ1 scoring works fine в `get_display_snapshot()` поверх уже відфільтрованого snapshot.

**Коли повернутись**: При wire payload optimization або server-side grade filtering (R1.1).

---

## R2. Implementation Notes (для Patch Master)

### IN-1: SmcZone → dict serialization

`get_display_snapshot()` працює зі `SmcZone` objects (frozen dataclass). Scoring приймає dicts. Використовувати `z.to_wire()` перед передачею. Scoring залишається dict-based — працює з будь-яким source.

### IN-2: bars/atr/last_bar доступні локально

В `get_display_snapshot()` на [рядках 520-530](core/smc/engine.py) вже є `bars` (з `state.bars_list()`), `atr` (з `compute_atr(bars)`), `last_bar` (з `bars[-1]`). Не потрібні окремі методи `_get_recent_bars()` / `_get_atr()` / `_get_last_price()`.

### IN-3: Scope base_swings filtering

`base_swings` у `get_display_snapshot()` — це суміш swing_high/swing_low + bos_*/choch_*. Для scoring потрібно розділити: swings для F1/F4/F6, structure events для F7. Фільтрація по `kind` prefix.
На M15 chart: stronger visual hierarchy — трейдер одразу бачить яка зона "гаряча".

---

## BUILD Report (2026-03-05)

> **Φ1 BUILD завершено.** 4 P-slices реалізовано за один сеанс.
> Tests: 424 pass / 7 pre-existing fail / 9 new confluence tests.
> UI build: 170 modules, 296.24 kB — zero regressions.

---

### "До" (стан після ADR-0028 Φ0)

```
┌─────────────────────────────────────────────────────────────────┐
│  Всі OB зони виглядають однаково.                               │
│  Єдиний розрізнювач — strength (impulse/ATR ratio):             │
│    • DisplayBudget: opacity = strengthToOpacity(strength)       │
│    • Focus mode: per-side cap по strength, без контексту         │
│    • Жодних badge / grade / confluence signals                  │
│                                                                 │
│  Трейдер бачить 6 OB зон — не знає яка варта entry.            │
│  OB після sweep+FVG+HTF alignment виглядає так само як           │
│  дрібний OB без контексту.                                      │
│                                                                 │
│  Data flow (wire):                                              │
│    engine._compute_snapshot()                                   │
│      → SmcSnapshot(zones, swings, levels, trend_bias)           │
│        → .to_wire() → WS full frame                             │
│          → SmcData { zones, swings, levels, trend_bias }        │
│            → DisplayBudget(strength-only opacity)               │
│              → OverlayRenderer.renderZones() — plain rectangles │
└─────────────────────────────────────────────────────────────────┘
```

### "Після" (стан після ADR-0029 Φ1)

```
┌─────────────────────────────────────────────────────────────────┐
│  Кожна OB зона має confluence grade: A+ / A / B / C            │
│  8 факторів × max 11 балів:                                     │
│    F1 sweep(+2), F2 FVG(+2), F3 HTF(+2), F4 extremum(+1),     │
│    F5 impulse(+1), F6 P/D(+1), F7 structure(+1), F8 TF(+1)    │
│                                                                 │
│  Grade thresholds (config.json SSOT):                           │
│    A+ ≥ 8  |  A ≥ 6  |  B ≥ 4  |  C < 4                       │
│                                                                 │
│  Візуально:                                                     │
│    A+ = gold badge             — найсильніша зона, entry-ready  │
│    A  = white badge            — сильна, варта уваги            │
│    B  = gray badge             — часткова confluence             │
│    C  = без badge, hidden/dim  — шум, відфільтрований           │
│                                                                 │
│  DisplayBudget (client-side, ER-6):                             │
│    Focus:    показує тільки A+ і A (OB); non-OB зони passthrough│
│    Research: показує A+, A, B; ховає C (OB only)                │
│                                                                 │
│  Ефект на H4 chart:                                              │
│    1–2 зони з "A+" gold badge, 1–2 зони з "A" white badge      │
│    Решта — hidden (Focus) або dim gray (Research)               │
│    Трейдер одразу бачить де confluence, де шум                   │
└─────────────────────────────────────────────────────────────────┘
```

---

### Повний Data Flow (end-to-end)

```
═══ SERVER ══════════════════════════════════════════════════════════

  core/smc/engine.py : get_display_snapshot()
  │
  │  Step 1-4: viewer mapping, swings, structure, FVG inject
  │  Step 5:   cross-TF injection (HTF zones merged into base_zones)
  │  Step 6:   key levels
  │
  │  ┌─── Step 7: CONFLUENCE SCORING (NEW) ──────────────────────┐
  │  │                                                            │
  │  │  for each OB zone in base_zones:                           │
  │  │    z_wire = zone.to_wire()                                 │
  │  │    swing_dicts = [s for s if kind ∈ swing_high/swing_low]  │
  │  │    struct_dicts = [s for s if kind ∈ bos_*/choch_*]        │
  │  │    htf_context = [z for z if z.tf_s > base_tf]             │
  │  │    bar_dicts = bars[-50:] → {open_time_ms, h, l}           │
  │  │                                                            │
  │  │    result = score_zone_confluence(                          │
  │  │      zone=z_wire, bars=bar_dicts,                          │
  │  │      swings=swing_dicts, zones_all=all_wired,              │
  │  │      htf_zones=htf_context, structure=struct_dicts,        │
  │  │      atr=atr, current_price=last_bar.c,                    │
  │  │      tf_s=base_tf, config=confluence_config                │
  │  │    )                                                       │
  │  │    → { score: 9, grade: 'A+', factors: ['sweep +2', ...]} │
  │  │                                                            │
  │  │    _zone_grades[(symbol, tf_s)][zone_id] = result          │
  │  └────────────────────────────────────────────────────────────┘
  │
  │  return snapshot (SmcSnapshot.to_wire() — UNCHANGED, S6)
  ▼

  core/smc/engine.py : get_zone_grades(symbol, tf_s)
  │  → returns dict { zone_id: {score, grade, factors} }
  ▼

  runtime/smc/smc_runner.py : get_zone_grades(symbol, tf_s)
  │  → delegates to self._engine.get_zone_grades()
  ▼

  runtime/ws/ws_server.py : _send_full_frame()
  │  1. snap = _smc_runner.get_snapshot(symbol, tf_s)
  │  2. smc_wire = snap.to_wire()   ← zones, swings, levels, trend_bias
  │  3. _zg = _smc_runner.get_zone_grades(symbol, tf_s)
  │  4. if _zg: smc_wire["zone_grades"] = _zg     ← INJECTED
  │  ▼
  │  _build_full_frame(candles, smc_wire, ...)
  │    → frame = { type, frame_type, candles, zones, swings, levels, ... }
  │    → if smc_wire.zone_grades: frame["zone_grades"] = {...}
  │    → JSON → WebSocket

═══ WIRE ════════════════════════════════════════════════════════════

  {
    "type": "render_frame",
    "frame_type": "full",
    "zones": [...],
    "swings": [...],
    "levels": [...],
    "trend_bias": "bullish",
    "zone_grades": {                          ← NEW FIELD
      "ob_bull_XAUUSD_900_1741...": {
        "score": 9,
        "grade": "A+",
        "factors": ["sweep +2", "fvg_after +2", "htf_align +2", ...]
      },
      "ob_bear_XAUUSD_900_1741...": {
        "score": 3,
        "grade": "C",
        "factors": ["impulse +1", "structure +1", "pd_align +1"]
      }
    },
    "meta": { "schema_v": "ui_v4_v2", ... }
  }

═══ CLIENT ══════════════════════════════════════════════════════════

  ChartPane.svelte : WS frame received
  │  smcData = applySmcFull(
  │    frame.zones, frame.swings, frame.levels,
  │    frame.trend_bias,
  │    frame.zone_grades          ← PASSED THROUGH
  │  )
  │  → SmcData { zones, swings, levels, trend_bias, zone_grades }
  ▼

  OverlayRenderer.ts : patch(smcData)
  │  this.frame = normalizeSmcData(smcData)
  │    → extracts zone_grades ?? {}
  │  newGrades = this.frame.zone_grades
  │  if Object.keys(newGrades).length > 0:
  │    this._gradeCache = { ...newGrades }    ← CACHED for delta path
  ▼

  OverlayRenderer.ts : render()
  │  zones = applyBudget(this.frame.zones, displayMode, budgetConfig,
  │                       this._gradeCache)   ← GRADES TO BUDGET
  ▼

  DisplayBudget.ts : applyBudget(zones, mode, config, grades)
  │  ┌─ Focus mode ─────────────────────────────────────────────┐
  │  │  OB zones: filter grade ∈ {A+, A} BEFORE per-side slice  │
  │  │  Non-OB zones: passthrough                                │
  │  └──────────────────────────────────────────────────────────┘
  │  ┌─ Research mode ──────────────────────────────────────────┐
  │  │  OB zones: hide C grade; show A+, A, B                   │
  │  │  Non-OB zones: passthrough                                │
  │  └──────────────────────────────────────────────────────────┘
  │  → filtered zones list
  ▼

  OverlayRenderer.ts : renderZones()
  │  for each visible zone:
  │    1. Draw zone rectangle (gradient fill)
  │    2. Draw zone label (kind + strength)
  │    3. Draw grade badge:                    ← NEW
  │       gradeInfo = _gradeCache[zone.id]
  │       if gradeInfo and grade ≠ 'C':
  │         _renderGradeBadge(x, y, grade, alpha)
  │           A+ → gold pill (rgba 255,215,0)
  │           A  → white pill (rgba 255,255,255)
  │           B  → gray pill (rgba 150,150,150)
  │           C  → not rendered
  ▼

  Canvas → User sees graded zones with visual hierarchy
```

### Delta Path (grade persistence)

```
Delta frame (smc_delta) → smcData = applySmcDelta(current, delta)
  ↓
  zone_grades НЕ приходить у delta (E4: full-frame-only)
  ↓
  _gradeCache зберігається з останнього full frame
  ↓
  mitigated_zone_ids → removeMitigatedGrades(ids)
    → delete _gradeCache[id] для кожного mitigated
  ↓
  Нові зони без grade → grade = undefined → 'C' by default
    → з'явиться реальний grade на наступному full frame (1–2с)
```

---

### Що реалізовано (P-slices)

#### P-Φ1-1a: `core/smc/confluence.py` — Scoring Module (NEW)

| Елемент | Файл | Рядки |
|---------|------|-------|
| `_check_liquidity_sweep()` (F1, +2pts) | `core/smc/confluence.py` | :16 |
| `_check_fvg_after()` (F2, +2pts) | `core/smc/confluence.py` | :48 |
| `_check_htf_alignment()` (F3, +2pts) | `core/smc/confluence.py` | :61 |
| `_check_extremum()` (F4, +1pt) | `core/smc/confluence.py` | :72 |
| `_check_impulse()` (F5, +1pt) | `core/smc/confluence.py` | :90 |
| `_check_premium_discount()` (F6, +1pt) | `core/smc/confluence.py` | :96 |
| `_check_structure()` (F7, +1pt) | `core/smc/confluence.py` | :112 |
| `_check_tf_significance()` (F8, +1pt) | `core/smc/confluence.py` | :125 |
| `_score_to_grade()` | `core/smc/confluence.py` | :131 |
| `score_zone_confluence()` — orchestrator | `core/smc/confluence.py` | :143 |
| `score_fvg_strength()` (P-Φ1-1b) | `core/smc/confluence.py` | :175 |
| `SmcConfluenceConfig` dataclass | `core/smc/config.py` | :177–213 |
| `confluence` field in SmcConfig | `core/smc/config.py` | :262 |
| `from_dict()` parsing | `core/smc/config.py` | :283 |
| `smc.confluence` config section | `config.json` | :335–345 |

**Загалом**: 195 LOC (confluence.py) + ~40 LOC (config.py) + 10 LOC (config.json). Pure, zero I/O (I0/S0 ✅).

**Errata applied**: E1 (sweep in-situ з bars), E2 (time_ms + direction from kind), E3 (kind замість type).

#### P-Φ1-1b: FVG Strength Refinement

Включено в `confluence.py:175` — `score_fvg_strength()`. Gap/ATR ratio з partial fill decay. Окрема від confluence scoring.

#### P-Φ1-2: Engine Wiring + Wire

| Елемент | Файл | Рядки |
|---------|------|-------|
| Import `score_zone_confluence` | `core/smc/engine.py` | :21 |
| `_zone_grades` dict init | `core/smc/engine.py` | :251 |
| Step 7: scoring loop (AFTER cross-TF, E5) | `core/smc/engine.py` | :555–593 |
| `get_zone_grades()` getter | `core/smc/engine.py` | :604–606 |
| `get_zone_grades()` delegation | `runtime/smc/smc_runner.py` | :239–240 |
| zone_grades fetch + inject | `runtime/ws/ws_server.py` | :703–704 |
| zone_grades into frame payload | `runtime/ws/ws_server.py` | :540 |

**Ключове рішення (E5)**: scoring виконується у `get_display_snapshot()` **після** cross-TF injection (step 5), де HTF zones вже merged в `base_zones`. Це забезпечує коректність F3 (HTF alignment).

**Ключове рішення (ER-6)**: `_zone_rank()` залишається strength-only на сервері. Grade-aware фільтрація — тільки client-side (DisplayBudget.ts). Обґрунтування в §R1.1.

**Wire format (S6 ✅)**: `SmcSnapshot.to_wire()` НЕ змінено. `zone_grades` додається як окремий dict у frame payload caller-ом (ws_server). SmcZone frozen dataclass — UNCHANGED.

#### P-Φ1-3: UI — Grade Badge + Budget Filter

| Елемент | Файл | Рядки |
|---------|------|-------|
| `ZoneGradeInfo` interface | `ui_v4/src/types.ts` | :63 |
| `zone_grades` в SmcData | `ui_v4/src/types.ts` | :59 |
| `zone_grades` в RenderFrame | `ui_v4/src/types.ts` | :145 |
| `applySmcFull()` + zone_grades param | `ui_v4/src/stores/smcStore.ts` | :20–26 |
| ChartPane: zone_grades passthrough (×2) | `ui_v4/src/layout/ChartPane.svelte` | :248, :341 |
| `_gradeCache` field | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | :176 |
| `patch()` → update _gradeCache | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | :273–280 |
| `_renderGradeBadge()` method | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | :468–487 |
| Badge call in renderZones | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | :717–720 |
| Grade filter (Focus: A+/A only) | `ui_v4/src/chart/overlay/DisplayBudget.ts` | :166–169 |
| Grade filter (Research: hide C) | `ui_v4/src/chart/overlay/DisplayBudget.ts` | :154–156 |
| `grades` param in applyBudget | `ui_v4/src/chart/overlay/DisplayBudget.ts` | :157 |

**Badge rendering**: Canvas pill-shape з `roundRect`. Колір по grade: gold (A+), white (A), gray (B), hidden (C). Розмір = `measureText(grade).width + 6px padding × 14px height`.

**Grade cache (E4)**: `_gradeCache` зберігається між full/delta frames. Full frame → повна заміна cache. Delta → тільки видалення mitigated IDs. Нові зони без grade = `'C'` до наступного full frame.

---

### Тести (9 нових)

| Тест | AC | Що перевіряє |
|------|----|-------------|
| `test_a_plus_zone` | AC-1 | OB з sweep+FVG+HTF+impulse+structure → score ≥ 8, grade A+ |
| `test_c_grade_minimal_context` | AC-2 | OB без контексту → score ≤ 3, grade C |
| `test_non_ob_zone_returns_c` | — | FVG zone → score 0, grade C (scoring тільки для OB) |
| `test_determinism_ac6` | AC-6 | Same input → same output (S2 determinism) |
| `test_b_grade_moderate` | — | OB з partial context → grade B |
| `test_ac4_large_gap` | AC-4 | FVG gap > 1.5×ATR → strength ≥ 0.9 |
| `test_ac5_partial_fill` | AC-5 | FVG filled > 50% → strength decayed ×0.5 |
| `test_small_gap` | — | FVG gap < 0.3×ATR → strength = 0.1 |
| `test_zero_atr` | — | ATR = 0 → strength = 0 (div-by-zero guard) |

**Файл**: `tests/test_smc_confluence.py` (~152 LOC, 9 tests). Час виконання: 0.11s.

---

### Інваріанти (перевірено)

| Інваріант | Статус | Доказ |
|-----------|--------|-------|
| I0 (Dependency Rule) | ✅ | `confluence.py` у `core/smc/` — pure, zero I/O, no runtime imports |
| I1 (UDS = вузька талія) | ✅ | Scoring read-only, не пише в UDS |
| S0 (core/smc pure) | ✅ | `score_zone_confluence()` = pure function, no side effects |
| S2 (Determinism) | ✅ | `test_determinism_ac6` — same input → same output |
| S5 (Config SSOT) | ✅ | Всі thresholds з `config.json:smc.confluence` |
| S6 (Wire format) | ✅ | `SmcSnapshot.to_wire()` UNCHANGED. `zone_grades` = separate dict |
| ER-6 (Server rank) | ✅ | `_zone_rank()` UNCHANGED. Grade filter = client-only |

---

### Регресія

```
424 passed, 7 failed (pre-existing), 46 warnings — 18.15s
UI build: 170 modules, 296.24 kB — 0 TypeScript errors
```

7 pre-existing failures (не пов'язані з ADR-0029):

- `test_qa_002::test_uds_split_brain` — PermissionError (Windows file lock)
- `test_tv_mismatch_probe::*` (×6) — ImportError (missing dependency)
