> **ARCHIVE / NON-CANONICAL SNAPSHOT** — Цей файл зберігає окремий зріз стану ADR-0034
> на момент, коли P2–P6 були реалізовані до rollback. Це **не підтримуваний** документ
> і не поточне джерело правди для системи, але це **не сміттєва копія**: він лишається
> як історичний запис того проміжного стану. Канонічний і підтримуваний документ:
> [`0034-advanced-market-analysis-tda.md`](../0034-advanced-market-analysis-tda.md).
> Поки цей файл існує, його слід трактувати як archive-only reference, не як active ADR state.

# ADR-0034: Advanced Market Analysis — TDA (ARCHIVE SNAPSHOT)

- **Статус**: **Archived / Non-Canonical** — historical snapshot, see canonical `0034-advanced-market-analysis-tda.md`
- **Дата**: 2026-03-09
- **Snapshot of**: 2026-03-10 — стан перед rollback P2–P6
- **Rolled back**: 2026-03-10 — механіка не тестувалась в реальних торгових умовах; ефект на якість аналізу невідомий. Реалізацію скасовано до MVP-валідації (P0+P1 першими).
- **MVP plan**: реалізувати P0 (IFVG) + P1 (Breaker) → верифікувати на 1-2 торгових сесіях → продовжувати P2–P7 або відмовитись
- **Автор**: R_ARCHITECT
- **Initiative**: `smc_tda_v1`
- **Пов'язані ADR**: ADR-0024 (Engine), ADR-0024c (Zone POI), ADR-0028 (Elimination), ADR-0029 (Confluence), ADR-0033 (Context Flow Narrative)
- **Джерело**: `research/Advanced Market Analysis. Dark Trader.csv` — конспект конференції з TDA-методології

---

## 1. Контекст і проблема

### 1.1 Поточний стан

> **Примітка читача**: ця секція описує historical attempted state на момент до rollback.
> Вона навмисно збережена як snapshot і може не відповідати поточному коду.

SMC Engine (ADR-0024) реалізує 8 детекторів, Context Stack (L1/L2/L3), Confluence scoring (8 факторів), Narrative Engine (ADR-0033) і Bias Banner. TDA (Top-Down Analysis) повністю реалізований і активований (2026-03-10):

| Компонент | Статус | Реалізація |
|-----------|--------|------------|
| Cross-TF zone injection + TF-sync validation | ✅ Implemented | `narrative.py:_compute_tf_sync()`, `smc.tda.tf_sync_enabled` |
| FVG detection + lifecycle + **IFVG** | ✅ Implemented | `fvg.py:_FVG_TO_IFVG_KIND`, `engine.py` step 4b |
| OB detection + lifecycle + **Breaker** | ✅ Implemented | `engine.py:_apply_breaker_transition()`, step 7b |
| Trend bias per TF + **Decisional Point** | ✅ Implemented | `engine.py:_apply_decisional_point_tagging()`, step 8 |
| Fractals + **Protected Fractal** | ✅ Implemented | `engine.py` line 891, `is_protected` field |
| Confluence scoring 8-factor + **Quasimodo F10** | ✅ Implemented | `quasimodo.py:detect_quasimodo()`, `confluence.py:_check_quasimodo()` |
| Narrative invalidation + **idea-level invalidation** | ✅ Implemented | `narrative.py:619`, `invalidation_tf` field in NarrativeBlock |
| Context Stack L1/L2/L3 + **TF-chain validation** | ✅ Implemented | `narrative.py:_compute_tf_sync()`, `tf_chains` in config |

### 1.2 Що таке TDA (з дослідження)

**TDA (Top-Down Analysis)** — це послідовний аналіз від найстаршого TF до найменшого:

```
D1 → H4 → H1 → M30/M15 → M5/M3
 ↓      ↓      ↓         ↓
trigger  test  confirmation  entry
```

> **Обмеження нашої системи**: Monthly та Weekly TF **не аналізуються** (немає даних). Наш каскад починається з D1.

Ключові принципи TDA:

1. **Послідовність**: не можна "стрибнути" з H1 на M3 без аналізу M15
2. **Confirmation на кожному рівні**: вижчий TF дає trigger → нижчий TF дає тест/підтвердження → тільки тоді ідемо далі
3. **Стоп = invalidation ідеї**: стоп за точкою скасування ідеї на тому TF, з якого trigger
4. **DP (Decisional Point)**: зона/FVG, яка визначила поточний напрямок. Якщо DP invertується (IFVG/BPR) → контекст змінюється
5. **IFVG**: FVG що був пробитий і тепер працює навпаки (інвертований)
6. **Rebalancing**: корекційний рух до FVG — заповнення дисбалансу
7. **Quasimodo**: найпростіша модель входу: sweep ліквідності → BOS → zone → entry

### 1.3 Gap Analysis: чого бракує

| # | Концепт | Існуючий стан | Потрібно |
|---|---------|---------------|----------|
| G1 | **IFVG** (Inverted FVG) | `[MISSING]` — згадка в ADR-0024c footnoте як future | Новий zone kind `ifvg_bull`/`ifvg_bear` + detection в `fvg.py` |
| G2 | **BPR** (Breaker transition) | `[PARTIAL]` — `"breaker"` status існує, transition code відсутній | Імплементувати `mitigated → breaker` transition в `order_blocks.py` |
| G3 | **DP** (Decisional Point) | `[MISSING]` — zero references | Tagging mechanism: property на zone що є DP |
| G4 | **Quasimodo** | `[MISSING]` — zero references | Pattern detection з існуючих swings + structure |
| G5 | **TF Synchronization** | `[PARTIAL]` — injection є, mandatory chain validation відсутня | Validation layer в engine або narrative |
| G6 | **Idea-level Invalidation** | `[PARTIAL]` — zone-level є, HTF cascade відсутній | Cascade invalidation в narrative |
| G7 | **Protected Fractal** | `[MISSING]` — Williams fractals є, "protected" concept відсутній | Post-processing tag on fractal swings |
| G8 | **Rebalancing** | `[PARTIAL]` — narrative text, no algorithm | FVG fill event як "rebalancing" |

### 1.4 Failure Model

| # | Сценарій | Без TDA | З TDA |
|---|----------|---------|-------|
| F1 | D1 bullish, H4 dає retest. Трейдер стрибає на M3 | Narrative: "trade". Без TF-sync warning | TF-sync: "⚠ H1 not confirmed. Wait H1 IFVG/BOS" |
| F2 | FVG пробитий тілом свічки → тепер працює навпаки | FVG status: `filled`. Зникає | IFVG створюється — нова зона з inverted context |
| F3 | OB mitigated, потім CHoCH в зворотному напрямку | OB зникає (hide_mitigated) | OB → breaker. Нова POI з протилежним bias |
| F4 | Стоп за M5 зоною, але ідея з H4 | Стоп вибитий, але контекст лишився | Narrative показує: "Invalidation: H4 swing at 1920" |
| F5 | Fractal на D1 — "захищений" чи ні? | Просто fractal_high/low marker | Protected = D1 test + H4 confirmation + BOS |
| F6 | Вхід Quasimodo на M15: sweep → BOS → зона | Бачить окремо: sweep + BOS + OB | Quasimodo badge на OB. Confluence +1 factor |

### 1.5 Evidence Pack

| Факт | Evidence |
|------|---------|
| `"breaker"` status = dead vocabulary | [VERIFIED `core/smc/types.py:23`] `ZONE_STATUSES` містить `"breaker"`. [VERIFIED `core/smc/engine.py` `_STATUS_RANK`] breaker=3. Transition code в `_update_zone_lifecycle()` НЕ реалізований |
| IFVG згадка в ADR-0024c | [VERIFIED `docs/adr/0024c-smc-zone-poi-rendering.md`] footnote S4: "Inverse FVG (IFVG) — future scope" |
| FVG lifecycle: partially_filled → filled | [VERIFIED `core/smc/fvg.py:102-106`] `_update_fvg_status()` — bar closes through → filled |
| Cross-TF injection не валідує ланцюг | [VERIFIED `core/smc/engine.py:466+`] `get_display_snapshot()` — інжектить HTF зони, але не перевіряє чи кожен проміжний TF confirmed |
| Narrative invalidation = zone-level | [VERIFIED `core/smc/narrative.py`] `_find_invalidation(zone)` → zone boundary. Не HTF cascade |
| Williams fractals = display-only | [VERIFIED `core/smc/swings.py`] `detect_fractals()` — markers без "protected" logic |
| compute_tfs = [300, 900, 3600, 14400, 86400] | [VERIFIED `config.json:272`] M5, M15, H1, H4, D1 |
| Quasimodo = zero references | [VERIFIED grep] жодного файлу в codebase |

---

## 2. Обмеження (Constraints)

### 2.1 Інваріанти

| ID | Перевірка | Наслідок |
|----|-----------|----------|
| I0 | Dependency Rule | Всі нові детектори → `core/smc/` (pure). Без I/O |
| I1 | UDS = вузька талія | SMC = read-only overlay. Жодних UDS writes |
| S0 | `core/smc/` = pure | Нові модулі = pure functions, no I/O |
| S2 | Deterministic | Same bars → same IFVG/DP/Quasimodo |
| S3 | Zone ID deterministic | `{kind}_{symbol}_{tf_s}_{anchor_bar_ms}` pattern |
| S5 | Config SSOT | Всі нові пороги → `config.json:smc.tda` |
| S6 | Wire format matches TS | Нові zone kinds → update `ui_v4/src/types.ts` |

### 2.2 Performance

- IFVG detection: ≤ 1 ms (post-processing FVG lifecycle — не новий scan)
- Breaker transition: ≤ 0.5 ms (check existing mitigated OBs against CHoCH events)
- TF-sync validation: ≤ 1 ms (check bias_map alignment per chain)
- DP tagging: ≤ 0.5 ms (scan existing zones for DP criteria)
- Total overhead: **< 3 ms** (в межах `max_compute_ms: 10`)

### 2.3 Backward Compatibility

- Нові zone kinds additive (UI ігнорує unknown kinds → graceful fallback)
- `"breaker"` status вже в vocabulary — activation не ламає wire format
- TF-sync info → `NarrativeBlock.tf_sync` optional field
- Config: нова секція `smc.tda` (default: enabled=false для поступового rollout)

### 2.4 Обмеження TF

Наша система не має Monthly/Weekly даних. TDA каскад:

```
D1 → H4 → H1 → M15 → M5
```

Це 5 рівнів з `compute_tfs`. M30 обчислюється (в derive chain) але НЕ в `compute_tfs` для SMC. M3/M1 = entry TF, не compute TF.

---

## 3. Розглянуті альтернативи

### Alt A: Modular Extension (обрано) ✅

Розширити існуючі модулі новими концептами поступово (P-slices). Кожен концепт — окремий файл або розширення існуючого.

| Плюс | Мінус |
|------|-------|
| Мінімальний blast radius per slice | 6–8 P-slices → тривалий rollout |
| Кожен slice = окремий verify + rollback | Деякі концепти залежать від попередніх (DP → від IFVG) |
| Backward compatible (additive kinds) | TF-sync validation може бути over-restrictive для narrative |
| Використовує існуючі extension points | |

**Реалізація**:

1. P0: IFVG detection (fvg.py extension)
2. P1: Breaker transition (order_blocks.py + engine.py)
3. P2: TF-sync validation (engine.py або narrative.py)
4. P3: Decisional Point tagging (engine.py post-processing)
5. P4: Protected Fractal (swings.py post-processing)
6. P5: Quasimodo pattern + confluence factor
7. P6: Narrative integration (idea-level invalidation + TF-sync warnings)
8. P7: Config + wire format + UI types

### Alt B: TDA Engine as Separate Module

Новий файл `core/smc/tda.py` який приймає multi-TF SmcSnapshot dict і повертає TDA assessment.

| Плюс | Мінус |
|------|-------|
| Ізольований модуль, чисті inter-module boundaries | Дублює cross-TF logic з engine.py get_display_snapshot() |
| Легко вимкнути/замінити | Нова абстракція без 2+ use cases (порушення P6 anti-bloat) |
| Зрозуміла ownership | +1 файл з перетином відповідальності з narrative.py |

**Не обрано**: порушення F5 (dependency rule overhead — TDA потребує тих же snapshots що engine), дублювання cross-TF injection logic.

### Alt C: Pure Narrative Extension

Реалізувати все через narrative.py — TF-sync, DP, IFVG як narrative-only concepts без нових zone kinds.

| Плюс | Мінус |
|------|-------|
| Один файл для всього | Narrative стає god-module (>500 LOC) |
| Без змін у types.py / wire format | IFVG/Breaker = real zones для UI rendering, не лише text |
| | Порушує S2: narrative не deterministic якщо based on interpretations |
| | Трейдер не бачить IFVG/Breaker на графіку — тільки текст |

**Не обрано**: IFVG і Breaker повинні бути видимими зонами на графіку. Текстовий опис без візуалізації порушує принцип "Clean Chart shows what matters".

---

## 4. Рішення

**Alt A: Modular Extension** — поступове розширення існуючих модулів.

### 4.1 Нові типи

#### 4.1.1 Zone Kinds (розширення `ZONE_KINDS`)

```python
# core/smc/types.py — додавання до ZONE_KINDS
ZONE_KINDS = frozenset({
    # Existing
    "ob_bull", "ob_bear",
    "fvg_bull", "fvg_bear",
    "premium", "discount",
    # NEW: ADR-0034
    "ifvg_bull", "ifvg_bear",    # Inverted FVG (§4.2)
})
```

#### 4.1.2 Zone Statuses (розширення: без нових — aktivation `"breaker"`)

```python
# "breaker" вже в ZONE_STATUSES — тільки activation:
# mitigated OB + CHoCH in opposite direction → status="breaker"
```

#### 4.1.3 Swing Kinds (розширення `SWING_KINDS`)

```python
# core/smc/types.py — додавання до SWING_KINDS
SWING_KINDS = frozenset({
    # Existing...
    # NEW: ADR-0034
    "quasimodo_bull", "quasimodo_bear",  # Quasimodo entry model (§4.6)
})
```

#### 4.1.4 SmcZone — нові optional поля

```python
@dataclasses.dataclass(frozen=True)
class SmcZone:
    # Existing fields...
    is_decisional: bool = False     # DP: key zone determining context (§4.4)
    is_protected: bool = False      # Protected fractal backing this zone
    origin_zone_id: Optional[str] = None  # IFVG: id of original FVG that was inverted
```

#### 4.1.5 NarrativeBlock — новий optional field

```python
@dataclasses.dataclass
class NarrativeBlock:
    # Existing fields...
    tf_sync: str = ""   # TF sync status: "full" | "partial:{missing}" | "broken:{detail}"
    invalidation_tf: str = ""  # TF-рівень invalidation: "H4", "D1", etc.
```

### 4.2 IFVG Detection (P0)

**Де**: `core/smc/fvg.py` — extension до `_update_fvg_status()`.

**Логіка**:

1. Коли FVG стає `filled` (bar closes through opposite edge)
2. Якщо bar closes **beyond** the FVG boundary (не просто fills, а пробиває з закріпленням)
3. → Створити нову зону `ifvg_bull` / `ifvg_bear` з інвертованими boundaries
4. IFVG = дзеркальна версія original FVG. Bullish FVG → bearish IFVG (і навпаки)

```
Original FVG bull:  high=1925, low=1920
Bar closes below 1920 → FVG filled
→ Create ifvg_bear:  high=1925, low=1920, origin_zone_id=original_fvg_id
```

**ID**: `ifvg_{bear}_{symbol}_{tf_s}_{anchor_bar_ms}` де anchor = bar що інвертував.

**Config**: `smc.tda.ifvg_enabled: true`, `smc.tda.ifvg_max_active: 4`.

### 4.3 Breaker Transition (P1)

**Де**: `core/smc/engine.py` — `_update_zone_lifecycle()`.

**Логіка**:

1. OB стає `mitigated` (ціна close через зону)
2. Після mitigation — detect CHoCH в протилежному напрямку (не BOS — саме CHoCH)
3. → `status = "breaker"`. Breaker = OB що тепер працює навпаки

**Trigger**: CHoCH event з `structure_events` after OB mitigation timestamp.

**Rendering**: breaker отримує dashed border + inverted color (spec для R_CHART_UX в P7).

### 4.4 Decisional Point Tagging (P3)

**Де**: `core/smc/engine.py` — post-processing в `_compute_snapshot()`.

**Логіка**:
DP = зона (FVG або OB), яка **створила** поточний контекст:

1. Знайти останній BOS/CHoCH event → zone що його спровокувала
2. FVG/OB що безпосередньо передував structure break → tag `is_decisional=True`
3. Тільки 1 DP per (symbol, tf_s) — найсвіжіший

**Значення для трейдера**: якщо DP invertується (IFVG/BPR) → контекст змінюється. Narrative показує DP як "ключову точку".

### 4.5 TF Synchronization Validation (P2)

**Де**: `core/smc/narrative.py` — extension до `synthesize_narrative()`.

**Логіка**:

1. Визначити **TDA chain** для viewer TF:
   - M5 viewer: chain = [D1, H4, H1, M15, M5]
   - M15 viewer: chain = [D1, H4, H1, M15]
   - H1 viewer: chain = [D1, H4, H1]
   - H4 viewer: chain = [D1, H4]
2. Для кожного TF в chain перевірити: є confirmation (trend_bias != None)?
3. Знайти **перший missing** TF в chain → `tf_sync = "partial:H1"`
4. Якщо всі TF aligned → `tf_sync = "full"`
5. Якщо conflict (H4 bullish, H1 bearish) → `tf_sync = "broken:H1 bearish in H4 bullish"`

**TDA Chain mapping** (SSOT в config):

```python
# config.json:smc.tda.tf_chains
{
    "300":   [86400, 14400, 3600, 900, 300],     # M5: D1→H4→H1→M15→M5
    "900":   [86400, 14400, 3600, 900],           # M15: D1→H4→H1→M15
    "3600":  [86400, 14400, 3600],                # H1: D1→H4→H1
    "14400": [86400, 14400],                      # H4: D1→H4
    "86400": [86400]                              # D1: D1 only
}
```

**В Narrative**: якщо `tf_sync != "full"` → sub_mode += " | TF sync: partial".
Не блокує trade mode, але знижує confidence.

### 4.6 Quasimodo Pattern Detection (P5)

**Де**: новий файл `core/smc/quasimodo.py` (S0: pure, ~60 LOC).

**Логіка** (з дослідження: "знімає фрактал → формує BOS → ось і є Квазімодо"):

1. Detect liquidity sweep: swing violated within last N bars
2. After sweep → detect BOS/CHoCH в протилежному напрямку (structure event)
3. → Tag як `quasimodo_bull` / `quasimodo_bear` swing at the BOS point

**Variant**: замість нового файлу — post-processing в engine.py що сканує existing classified_swings + struct_events. Перевага: менше файлів. Але Quasimodo = composable pattern з 3 компонентів → окремий модуль чистіше.

**Confluence integration**: +1 factor `F9: quasimodo` в `confluence.py` (weight = 1).

### 4.7 Protected Fractal (P4)

**Де**: `core/smc/swings.py` — post-processing `detect_fractals()` output.

**Логіка** (з дослідження: "фрактал стає захищеним тільки тоді, коли є тести FVG зі старшого TF + підтвердження з молодшого TF"):

1. Fractal на TF_X
2. Check: є HTF zone (TF > TF_X) в proximity (ATR × tolerance)?
3. Check: є LTF confirmation (BOS/CHoCH на TF < TF_X) за direction фрактала?
4. Both true → tag existing `fractal_high`/`fractal_low` swing з `is_protected: True`

**Rendering**: protected fractal отримує filled diamond marker замість hollow.

### 4.8 Idea-Level Invalidation (P6)

**Де**: `core/smc/narrative.py` — extension до `_find_invalidation()`.

**Поточний стан**: invalidation = zone boundary (low/high of target zone).

**TDA invalidation**: stоp має стояти за точкою скасування **ідеї**, не локальної зони:

1. Визначити TF trigger (звідки прийшла ідея) — `scenario.trigger_tf`
2. Invalidation = swing на trigger TF (не entry TF)
3. Example: якщо trigger = H4 zone test, entry = M15 → invalidation = H4 swing (not M15 boundary)

```python
# Pseudo-logic in narrative
if scenario.trigger_tf:
    htf_snap = snapshots.get(scenario.trigger_tf)
    if htf_snap:
        invalidation = _find_htf_swing_invalidation(htf_snap, scenario.direction)
```

**NarrativeBlock**: `invalidation_tf = "H4"` — показує трейдеру на якому TF invalidation.

### 4.9 Config (SSOT)

```json
{
    "smc": {
        "tda": {
            "enabled": false,
            "ifvg_enabled": true,
            "ifvg_max_active": 4,
            "breaker_enabled": true,
            "breaker_choch_lookback_bars": 10,
            "dp_enabled": true,
            "quasimodo_enabled": true,
            "quasimodo_sweep_lookback_bars": 5,
            "protected_fractal_enabled": true,
            "protected_fractal_atr_tolerance": 2.0,
            "tf_sync_enabled": true,
            "tf_chains": {
                "300":   [86400, 14400, 3600, 900, 300],
                "900":   [86400, 14400, 3600, 900],
                "3600":  [86400, 14400, 3600],
                "14400": [86400, 14400],
                "86400": [86400]
            },
            "idea_invalidation_enabled": true
        }
    }
}
```

Master toggle `tda.enabled` контролює всі sub-features. Кожна sub-feature має свій toggle для поступового rollout.

---

## 5. P-Slices (реалізація)

| # | Slice | Scope | LOC | Файли | Verify |
|---|-------|-------|-----|-------|--------|
| P0 | **IFVG Detection** | `fvg.py` extension + `types.py` kinds | ~80 | `fvg.py`, `types.py`, `config.py` | Unit test: FVG filled → IFVG created with inverted kind |
| P1 | **Breaker Transition** | `engine.py` lifecycle + `order_blocks.py` | ~60 | `engine.py`, `order_blocks.py` | Test: mitigated OB + CHoCH → status="breaker" |
| P2 | **TF-Sync Validation** | `narrative.py` extension | ~70 | `narrative.py`, `types.py` (NarrativeBlock.tf_sync) | Test: chain [D1↑, H4↑, H1=None] → "partial:H1" |
| P3 | **Decisional Point** | `engine.py` post-processing | ~50 | `engine.py`, `types.py` (SmcZone.is_decisional) | Test: BOS + preceding FVG → tagged DP |
| P4 | **Protected Fractal** | `swings.py` post-processing | ~60 | `swings.py`, cross ref HTF snapshots | Test: fractal + HTF zone + LTF BOS → protected=True |
| P5 | **Quasimodo + Confluence** | New `quasimodo.py` + `confluence.py` F9 | ~80 | `quasimodo.py`, `confluence.py`, `engine.py` | Test: sweep+BOS → quasimodo swing |
| P6 | **Idea Invalidation** | `narrative.py` extension | ~50 | `narrative.py` | Test: trigger H4, entry M15 → invalidation = H4 swing |
| P7 | **Config + Types + UI** | Config section + TS types | ~60 | `config.json`, `config.py`, `types.ts` | Exit gate: contract check |

**Total**: ~510 LOC, 8 slices × ≤80 LOC кожен.

**Залежності**: P0 незалежний. P1 незалежний. P2 незалежний. P3 потребує P0 (IFVG як DP candidate). P4 потребує cross-TF snapshots. P5 незалежний. P6 потребує P2 (tf_sync context). P7 = фіналізація.

```
Рекомендований порядок:
P0 (IFVG) → P1 (Breaker) → P3 (DP) → P5 (Quasimodo)
         ↘ P2 (TF-sync) → P6 (Idea Invalidation)
                    P4 (Protected Fractal)
                              → P7 (Config + UI types)
```

---

## 6. Blast Radius

| Зона впливу | Файли |
|-------------|-------|
| Core types | `core/smc/types.py` (ZONE_KINDS, SWING_KINDS, SmcZone fields, NarrativeBlock field) |
| FVG detector | `core/smc/fvg.py` (IFVG creation) |
| OB lifecycle | `core/smc/order_blocks.py` (breaker transition) |
| Engine orchestrator | `core/smc/engine.py` (DP tagging, breaker in lifecycle, protected fractal) |
| Narrative | `core/smc/narrative.py` (TF-sync, idea invalidation) |
| Confluence | `core/smc/confluence.py` (F9 quasimodo factor) |
| New module | `core/smc/quasimodo.py` (~60 LOC) |
| Config | `config.json` (+`smc.tda` section), `core/smc/config.py` (+TdaConfig) |
| Wire format | `ui_v4/src/types.ts` (new zone kinds in SmcZone, NarrativeBlock) |
| Tests | `tests/test_smc_tda_*.py` (нові) |

**Не зачіпає**: UDS, runtime/store, HTTP API, ws_server frame composition (additive fields), OverlayRenderer (нові kinds = fallback to default style).

---

## 7. Наслідки

### 7.1 Для трейдера (R_TRADER)

- IFVG видно на графіку → зрозуміло коли контекст інвертується
- Breaker = inverted OB → not just "mitigated and gone"
- DP badge → "ця зона визначає контекст"
- TF-sync в narrative → "H1 ще не confirmed, чекай"
- Idea invalidation → "стоп за H4 swing, не за M5 зоною"

### 7.2 Для системи

- +2 zone kinds, +2 swing kinds → wire format additive
- +1 config section → `smc.tda` з master + sub toggles
- +1 confluence factor → max score стає 12 (було 11)
- Grade thresholds залишаються (A+ ≥ 8, A ≥ 6, B ≥ 4) — Quasimodo лише збільшує ceiling для A+ setups

### 7.3 Performance

- Всі нові операції = post-processing існуючих результатів (не повторний scan)
- Total overhead: < 3 ms (в межах існуючого budget 10 ms)

---

## 8. Rollback

### Per-slice rollback

Кожен P-slice = окремий rollback:

- P0: видалити IFVG creation код з `fvg.py`, видалити `ifvg_*` з `ZONE_KINDS`
- P1: видалити breaker transition з `engine.py`
- P2: видалити TF-sync з `narrative.py`, видалити `tf_sync` field
- P3: видалити DP tagging, видалити `is_decisional` field
- P4: видалити protected fractal logic
- P5: видалити `quasimodo.py`, видалити F9 з confluence
- P6: видалити idea invalidation з narrative
- P7: видалити config section + TS types

### Master rollback

`config.json:smc.tda.enabled = false` → вимикає все. Zero code changes needed.

### Data impact

Жодних змін у SSOT data (JSONL/Redis). SMC = read-only overlay. Rollback = чистий.

---

## 9. Open Questions

| # | Питання | Варіант відповіді | Вирішити до |
|---|---------|-------------------|-------------|
| Q1 | Чи FWG = FVG? В транскрипті використовуються обидва | Так, FWG = альтернативна назва FVG. Не потрібен окремий тип | P0 |
| Q2 | Чи Quasimodo = окремий swing kind чи badge на OB? | Swing kind (quasimodo_bull/bear) — бо це entry pattern, не зона | P5 |
| Q3 | Чи TF-sync повинен блокувати trade mode в narrative? | Ні — advisory only. "partial" знижує confidence, не блокує. Трейдер вирішує | P2 |
| Q4 | Чи DP = окремий zone kind чи flag на existing zone? | Flag `is_decisional` — бо DP може бути будь-який FVG/OB | P3 |
| Q5 | IFVG max_active per side чи total? | Total per (symbol, tf) — як FVG | P0 |

---

## 10. Пов'язані ADR

| ADR | Зв'язок |
|-----|---------|
| ADR-0024 | Engine architecture — base для всіх extensions |
| ADR-0024c | Zone POI rendering — IFVG rendering rules |
| ADR-0028 | Elimination Engine — IFVG/Breaker потрапляють під display budget |
| ADR-0029 | Confluence Scoring — F9 Quasimodo factor |
| ADR-0033 | Narrative — TF-sync, idea invalidation extend narrative |

---

## 11. Self-Check 10/10

- [x] Root cause: трейдер не має TDA methodology support у системі
- [x] I0: всі нові модулі в `core/smc/` (pure)
- [x] I1: SMC = read-only, no UDS writes
- [x] I2: N/A (no new time geometry)
- [x] I3: N/A (no Final/Preview changes)
- [x] S0/S5: pure + config SSOT
- [x] SSOT: types.py для kinds, config.json для params
- [x] Mutation sites: ZONE_KINDS (1 місце), SWING_KINDS (1 місце), config (1 секція)
- [x] Blast radius: documented in §6
- [x] Rollback: per-slice + master toggle
