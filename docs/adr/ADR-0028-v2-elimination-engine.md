# ADR-0028 v2: Elimination Engine — Display Filter Pipeline для SMC Zones

- **Статус**: Proposed → Accepted → **Implemented** (Φ0 done: 4 P-slices, 415/422 tests pass)
- **Дата**: 2026-03-04
- **Автор**: Chief Strategist + System Architect
- **Reviewer**: Patch Master + Bug Hunter (GO/NO-GO: CONDITIONAL GO → 4 errata → **GO**)
- **Initiative**: SMC-VIS-Φ0 (Display Filter Layer)
- **Залежності**: ADR-0024 (SMC Engine), ADR-0024c (Zone POI Rendering)
- **Scope**: config.json:smc tuning + server-side `_filter_for_display()` enhancement + client-side budget toggle
- **Попередня версія**: ADR-0028 v1 отримала NO-GO (D-01..D-06). Ця версія — виправлення.

### Errata (applied 2026-03-04, Patch Master review)

| # | Defect | Severity | Що було | Що стало |
|---|--------|----------|---------|----------|
| F1 | `min_display_strength` маркувалось як "TUNING existing" — поле **не існує** в `SmcDisplayConfig` (config.py:143-148). `0.15` у engine.py:141 = decay floor | S3 | `TUNING 0.15→0.25` | `NEW` + пояснення що decay floor ≠ display threshold |
| F2 | `z.side === 'bearish'` у DisplayBudget.ts — `SmcZone` не має поля `side` (types.py:46-60, types.ts:23-37) | S3 | `z.side` | `z.kind.includes('bear'/'bull')` + VERIFIED маркер |
| F3 | `smc.display.hide_mitigated` — поле живе в `smc` root (`SmcConfig`), не в `smc.display` | S3 | `smc.display.hide_mitigated` | `smc.hide_mitigated` + VERIFIED path |
| F4 | `zone.mitigated_at_bar` — `SmcZone` не має такого поля. `end_ms` заповнюється при мітигації (engine.py:117) | S2 | `zone.mitigated_at_bar` | computation via `end_ms`, zero types.py changes |

**Count correction**: 4 tuned + 5 new → **2 tuned + 1 extend + 6 new** = 9 params.

---

## 0. Executive Summary

### Проблема (від скрінів до коду)

M15 chart XAU/USD показує ~20+ зон, що перекриваються. Трейдер не може відповісти на питання "що робити?" за 3 секунди.

**V1 стверджував**: "відсутні mitigation / proximity / TTL / budget / sorting". Це було **неточно**. Аудит Patch Master + Bug Hunter виявив, що базова інфраструктура **існує**, але **недотюнена або неповна**. Чесна картина — нижче у §1.

### Рішення

Не будувати нову систему, а **дотюнити і доповнити існуючу** — ужорсточити proximity, додати per-side budget cap, додати TTL post-mitigation fade, додати client-side Focus/Research toggle. Мінімальний diff, максимальний ефект.

### Scope

**Тільки Φ0 (Elimination Engine)**. Фази Φ1–Φ3 з v1 — окремі ADR (0029, 0030, 0031) коли Φ0 пройде gate.

---

## 1. Контекст: що ІСНУЄ vs що ВІДСУТНЄ

### 1.1 Honest Evidence Ledger

| # | Claim з v1 | Реальний стан | Evidence | Що реально потрібно |
|---|-----------|---------------|----------|---------------------|
| C1 | "Відсутній mitigation lifecycle" | **EXISTS**: `engine.py:104-118` — `_update_zone_lifecycle()` step 3: R-04 mitigation by CLOSE (not wick). Bullish: `bar.c < zone.low` → mitigated. `engine.py:154-161`: `hide_mitigated` config → mitigated OB видаляються, mitigated FVG зберігаються як dimmed. `OverlayRenderer.ts:505`: `isDimmed` для mitigated/filled → `dimMult=0.35` | [VERIFIED engine.py:104-118, 154-161] [VERIFIED OverlayRenderer.ts:505] | **MISSING**: TTL post-mitigation (зона dimmed **назавжди**, потрібен 20-bar fade → hide) |
| C2 | "Відсутній proximity filter" | **EXISTS**: `engine.py:683-695` — `_filter_for_display()` фільтрує non-FVG зони за `proximity_atr_mult × ATR`. Config: `proximity_atr_mult: 15.0`. `OverlayRenderer.ts:525-540`: UI-side proximity fog (distFromEdge → rawProx → bodyPx), далекі зони = тільки origin marker 30px | [VERIFIED engine.py:683-695] [VERIFIED OverlayRenderer.ts:525-540] | **TUNING**: proximity_atr_mult=15.0 занадто широкий (15×ATR = майже весь видимий range). Потрібно ужорсточити до 5.0–8.0 |
| C3 | "Відсутній TTL/decay" | **EXISTS**: `engine.py:120-147` — age_bars calculation, TF-aware `_TF_DECAY_PROFILE`, strength decay (0.97 gentle / 0.92 aggressive), expire at 500/2000/5000 барів | [VERIFIED engine.py:120-147] | **MISSING**: opacity-based fade (decay працює на strength, не на opacity). **MISSING**: structure label TTL (BOS/CHoCH не мають auto-hide) |
| C4 | "Відсутній display budget" | **PARTIALLY EXISTS**: `engine.py:702` — `max_display_zones: 8` cap. Але: FVG pass through без cap; немає per-side budget (supply/demand); немає total budget across element types | [VERIFIED engine.py:702] | **MISSING**: per-side budget (N supply + N demand). **MISSING**: total cap across zones + levels + structure labels |
| C5 | "Відсутній grade/strength sorting" | **EXISTS**: `engine.py:65-67` — `_zone_rank()` сортує за `(-strength, status, -anchor_bar_ms)` | [VERIFIED engine.py:65-67] | **MISSING**: confluence-based grading (A+/A/B/C). Але це Φ1 scope (ADR-0029), не Φ0 |
| C6 | "Cross-TF без семантики" | **EXISTS**: `engine.py` — Context Stack injection (cross-TF structure, FVG, OB, levels). `OverlayRenderer.ts` рендерить проєкції з origin_tf | [VERIFIED engine.py get_display_snapshot()] | **MISSING**: візуальна диференціація (рідна зона vs проєкція = однакова вага). Але це Φ2 scope (ADR-0030), не Φ0 |
| C7 | "OB detection не за SMC-каноном" | EXISTS як базовий detector. MISSING: confluence scoring | [VERIFIED engine.py detect_order_blocks()] | Φ1 scope (ADR-0029), не Φ0 |

### 1.2 Висновок

**Проблема реальна** (M15 overcrowded), але **root cause — не "відсутність інфраструктури"**. Root cause — **параметри занадто м'які** (proximity=15×ATR, budget=8 без per-side, mitigated зони dimmed але не зникають, FVG без cap) + **відсутні кілька конкретних механізмів** (post-mitigation TTL, per-side budget, structure label TTL, opacity lifecycle).

---

## 2. Розглянуті варіанти

### Варіант A: Тільки config tuning (proximity: 15→5, budget: 8→4)

**Плюси**: 0 LOC, тільки config.json зміна.
**Мінуси**: Без per-side budget — 4 зони можуть бути всі supply. Без post-mitigation TTL — dimmed зони залишаються назавжди. Без structure label TTL — BOS/ChoCH накопичуються.

**Вердикт**: ❌ Необхідний, але недостатній.

### Варіант B: Config tuning + server-side enhancement (обрано)

**Плюси**: Мінімальний diff (~100–150 LOC server-side). Використовує існуючу `_filter_for_display()`. Config-driven. Per-side budget. Post-mitigation TTL.
**Мінуси**: Focus↔Research toggle потребує server round-trip або client-side cap.

**Вердикт**: ✅ Обрано з гібридним доповненням (C).

### Варіант C: Hybrid — server primary + client budget toggle

Server: повна фільтрація (mitigation, proximity, TTL, strength). Відправляє **більше зон ніж Focus budget** (research-ready payload).
Client: застосовує Focus/Research budget cap + opacity mapping. Toggle миттєвий, без round-trip.

**Плюси**: UX (миттєвий toggle). Server не знає про display mode. Жодної логіки не дублюється (server = eligibility, client = budget).
**Мінуси**: Wire payload трохи більший (research-size, не focus-size).

**Вердикт**: ✅ Прийнято як доповнення до B.

---

## 3. Рішення

### 3.0 Архітектурне рішення: Filter Placement (вирішує K1)

```
engine.py:_filter_for_display()          UI: DisplayBudget.ts (НОВИЙ)
         SERVER                                    CLIENT
┌─────────────────────────┐          ┌─────────────────────────┐
│ F1: Mitigation status   │          │ F6: Budget cap          │
│     (mitigated+TTL→hide)│          │     (per-side + total)  │
│ F2: Proximity filter    │          │ F7: Opacity mapping     │
│     (ATR-based)         │          │     (strength→opacity)  │
│ F3: Age/TTL filter      │          │ F8: Focus/Research      │
│     (expire old zones)  │ ──wire─► │     toggle (instant)    │
│ F4: Strength gate       │          │                         │
│     (min threshold)     │          │ (жодна eligibility      │
│ F5: Sort by rank        │          │  логіка не дублюється)  │
│     (existing _zone_rank)│         │                         │
└─────────────────────────┘          └─────────────────────────┘
         ↑                                     ↑
   ELIGIBILITY                            PRESENTATION
   "може бути показана"                "скільки і як показати"
```

**SSOT rule**: Server визначає **eligibility** (зона пройшла фільтри → може бути показана). Client визначає **presentation** (budget cap, opacity, mode toggle). Логіка НЕ дублюється. Server не знає про Focus/Research. Client не фільтрує за proximity/TTL.

**Wire format**: server відправляє research-size payload (всі eligible зони, до `max_display_zones` config). Client застосовує Focus budget (менше) або Research budget (більше). Toggle = instant, zero round-trip.

### 3.1 Архітектурне рішення: Display DTO (вирішує K3 — frozen dataclass)

**Проблема**: `SmcZone` є `frozen=True` dataclass (`types.py:46`). Pseudo-code з v1 мутував зони in-place (`z.display_opacity = ...`), що неможливо.

**Рішення**: Client-side `DisplayBudget.ts` працює з **computed display properties** — не мутує зони, а створює окремий display dict:

```typescript
interface ZoneDisplayProps {
  zone_id: string;           // посилання на SmcZone
  visible: boolean;          // пройшла budget cap
  opacity: number;           // computed: strength × proximity × age
  is_projection: boolean;    // source_tf ≠ chart_tf
  // zone data залишається immutable, display props — окремо
}

// Pipeline: zones.map(z => computeDisplayProps(z, price, atr, config, mode))
//           .filter(d => d.visible)
//           .sort(byRank)
//           .slice(0, budget)
```

**Server-side**: `_filter_for_display()` продовжує повертати `List[dict]` (serialized SmcZone). Без мутації frozen об'єктів. Фільтри працюють як predicate → exclude, не як mutation.

**I0 compliance**: types.py залишається frozen. Жодних змін у core/smc/types.py.

### 3.2 Config changes (≤10 нових параметрів, вирішує K2)

**Принцип**: не 40 нових параметрів, а **tuning існуючих + мінімум нових**.

| Параметр | Поточне значення | Нове значення | Тип зміни |
|----------|-----------------|---------------|-----------|
| `smc.display.proximity_atr_mult` | `15.0` | `6.0` | **TUNING** існуючого |
| `smc.display.max_display_zones` | `8` | `10` (research) | **TUNING** існуючого |
| `smc.display.min_display_strength` | — (не існує) | `0.25` | **NEW**: поріг видимості зон за strength. `0.15` у `engine.py:141` — це decay floor, НЕ display threshold. Потрібне нове поле в `SmcDisplayConfig` + фільтр у `_filter_for_display()` |
| `smc.hide_mitigated` | `true` (OB only) | `true` (OB + FVG після TTL) | **EXTEND** семантики. **Path**: `smc` root → `SmcConfig.hide_mitigated` ([VERIFIED config.py:173, config.json:310]) |
| `smc.display.mitigated_ttl_bars` | — (new) | `20` | **NEW** |
| `smc.display.focus_budget_per_side` | — (new) | `3` | **NEW** |
| `smc.display.focus_budget_total` | — (new) | `12` | **NEW** |
| `smc.display.structure_label_max` | — (new) | `4` | **NEW** |
| `smc.display.fvg_display_cap` | — (new) | `4` | **NEW** |

**Validation guard** (новий, у config.py):

```python
assert focus_budget_per_side * 2 + structure_label_max <= focus_budget_total, \
    f"Budget components ({focus_budget_per_side}*2 + {structure_label_max}) exceed total ({focus_budget_total})"
```

**Total**: 2 tuned + 1 extend + 6 new = 9 параметрів. Всі з default values. Config validation guard.

> **Уточнення бюджету**: `fvg_display_cap` = server-side wire cap. На client-side FVG входять у per-side zone budget разом з OB (фільтр за `kind.includes('bear'/'bull')`). Validation formula `perSide×2 + structureMax ≤ total` коректна — FVG вже включені. Levels отримують залишок: `total − zones − structure`.

### 3.3 Server-side changes: `engine.py:_filter_for_display()`

Існуюча функція вже робить: proximity filter + strength gate + sort + cap. Потрібно **доповнити**, не переписувати.

#### 3.3.1 Post-mitigation TTL (MISSING → ADD)

**Де**: `engine.py:_update_zone_lifecycle()`, після step 3 (mitigation detection).

**Що додати**: якщо зона mitigated і пройшло > `mitigated_ttl_bars` барів → видаляємо з active_zones.

**Anchor**: `SmcZone.end_ms` заповнюється при мітигації ([VERIFIED engine.py:117]: `end_ms=last_bar.open_time_ms`). Нових полів у `types.py` **НЕ** потрібно.

```python
# ПІСЛЯ існуючого mitigation detection (engine.py:~118)
# Новий step 3b: post-mitigation TTL
# SmcZone.end_ms вже ставиться при мітигації (engine.py:117)
bar_ms = tf_s * 1000
if zone.status == 'mitigated' and zone.end_ms is not None and bar_ms > 0:
    bars_since_mitigation = (last_bar.open_time_ms - zone.end_ms) // bar_ms
    if bars_since_mitigation > cfg.display.mitigated_ttl_bars:  # default 20
        to_delete.append(zid)
```

**LOC**: ~8 рядків. Один mutation site (вже в lifecycle loop). Zero types.py changes.

#### 3.3.2 FVG display cap (MISSING → ADD)

**Де**: `engine.py:_filter_for_display()`, після існуючого max_display_zones cap.

**Що додати**: FVG окремий cap (зараз FVG pass through без ліміту).

```python
# ПІСЛЯ існуючого non-FVG filtering
fvg_zones = [z for z in eligible if z['kind'] == 'fvg']
fvg_zones = sorted(fvg_zones, key=_zone_rank)[:cfg.fvg_display_cap]  # default 4
```

**LOC**: ~4 рядки.

#### 3.3.3 Proximity tuning (EXISTS → TUNE)

**Де**: `config.json:smc.display.proximity_atr_mult`

**Зміна**: `15.0` → `6.0`. Це означає зони далі ніж 6×ATR від ціни — не проходять server-side filter. Для XAU/USD H1 (ATR~30): зони далі ~180 пунктів від ціни = не eligible.

**LOC**: 0 (config only).

#### 3.3.4 Min strength filter (NEW → ADD)

**Де**: `engine.py:_filter_for_display()`, новий крок перед proximity filter.
**Config**: `smc.display.min_display_strength` — нове поле в `SmcDisplayConfig` (default `0.25`).

**Що додати**: зони зі `strength < min_display_strength` не проходять server-side фільтр.

> **Примітка**: У поточному коді `0.15` ([VERIFIED engine.py:141]) — це **decay floor** (`max(0.15, z.strength * factor)`), НЕ display threshold. Ці два поняття ортогональні: decay визначає мінімальну strength живої зони, display threshold — мінімальну strength для показу.

```python
# У _filter_for_display(), перед proximity filter
min_str = disp.min_display_strength  # default 0.25
eligible = [z for z in snap.zones if z.strength >= min_str]
```

**LOC**: ~3 рядки (engine.py) + ~2 рядки (config.py: field + from_dict).

### 3.4 Client-side changes: `DisplayBudget.ts` (NEW)

Новий модуль у `ui_v4/src/smc/`. Приймає eligible зони від server, застосовує budget + opacity.

```typescript
// ui_v4/src/smc/DisplayBudget.ts

export interface BudgetConfig {
  perSide: number;       // з config: focus_budget_per_side (default 3)
  total: number;         // з config: focus_budget_total (default 12)
  structureMax: number;  // з config: structure_label_max (default 4)
}

export type DisplayMode = 'focus' | 'research';

export function applyBudget(
  zones: SmcZone[],
  levels: LiquidityLevel[],
  structure: StructureLabel[],
  mode: DisplayMode,
  config: BudgetConfig,
): FilteredPayload {
  if (mode === 'research') {
    // Research: show all eligible (server already capped at max_display_zones)
    return { zones, levels, structure };
  }

  // Focus: apply strict per-side budget
  // SmcZone не має поля `side`. Side визначається з `kind`:
  // ob_bear / fvg_bear → supply, ob_bull / fvg_bull → demand
  // [VERIFIED types.py:46-60, types.ts:23-37: SmcZone has `kind`, NOT `side`]
  const supply = zones
    .filter(z => z.kind.includes('bear'))
    .slice(0, config.perSide);  // вже sorted by server (_zone_rank)

  const demand = zones
    .filter(z => z.kind.includes('bull'))
    .slice(0, config.perSide);

  const budgetZones = [...supply, ...demand];
  const budgetStructure = structure.slice(0, config.structureMax);

  // Total cap enforcement
  let result = { zones: budgetZones, levels, structure: budgetStructure };
  const total = budgetZones.length + levels.length + budgetStructure.length;
  if (total > config.total) {
    // Trim levels first (least valuable in Focus)
    const excess = total - config.total;
    result.levels = levels.slice(0, Math.max(0, levels.length - excess));
  }

  return result;
}
```

**LOC**: ~50 рядків (включаючи opacity mapping helper).

**Opacity mapping** (strength → visual weight):

```typescript
export function strengthToOpacity(strength: number): number {
  if (strength >= 0.8) return 1.0;
  if (strength >= 0.5) return 0.7;
  if (strength >= 0.3) return 0.4;
  return 0.15;  // dimmed but visible in research
}
```

### 3.5 OverlayRenderer.ts changes

**Мінімальні** — інтеграція з DisplayBudget:

```typescript
// Перед renderZones():
const filtered = applyBudget(snapshot.zones, snapshot.levels, snapshot.structure, displayMode, budgetConfig);
// Далі renderZones(filtered.zones) замість renderZones(snapshot.zones)
```

**LOC**: ~5 рядків (import + call + mode state).

**Display mode toggle**: кнопка або hotkey (F=Focus, R=Research) у HUD. State = reactive (Svelte store або simple variable). Toggle = re-render з іншим budget. Zero server call.

---

## 4. Наслідки

### 4.1 Що змінюється

| Компонент | Зміна | LOC | Файлів |
|-----------|-------|-----|--------|
| `config.json:smc.display` | 2 tuned + 1 extend + 6 new params | ~15 | 0 (існуючий) |
| `config.py` | `min_display_strength` field + budget validation guard | ~8 | 0 (існуючий) |
| `engine.py:_update_zone_lifecycle()` | Post-mitigation TTL (step 3b, anchor=`end_ms`) | ~8 | 0 (існуючий) |
| `engine.py:_filter_for_display()` | FVG display cap + min-strength gate | ~7 | 0 (існуючий) |
| `ui_v4/src/smc/DisplayBudget.ts` | Budget cap + opacity + mode toggle | ~50 | **1 новий** |
| `ui_v4/src/smc/OverlayRenderer.ts` | Integration (import + call) | ~5 | 0 (існуючий) |
| `ui_v4/src/` (HUD) | Focus/Research toggle button | ~15 | 0 (існуючий) |
| Tests | 6–8 unit tests | ~80 | **1 новий** |

**Total**: ~195 LOC, 2 нових файли (DisplayBudget.ts + test). Решта — зміни в існуючих.

### 4.2 Що НЕ змінюється

- `core/smc/types.py` — SmcZone залишається frozen. Zero changes.
- SmcEngine detection pipeline — алгоритми OB/FVG/structure не змінюються.
- Wire format — server→client payload format не змінюється (додаткові поля backwards-compatible).
- UDS / data pipeline — zero changes.
- Derive chain — zero changes.
- Існуючі exit gates — zero regression.

### 4.3 Вплив на інваріанти

| Інваріант | Вплив |
|-----------|-------|
| I0 (Dependency Rule) | ✅ DisplayBudget.ts = UI layer. engine.py = runtime. Жодних нових core/ файлів |
| I1 (UDS narrow waist) | ✅ Не торкається |
| I2 (Time geometry) | ✅ TTL в барах (рахується тільки коли market open — бари течуть тільки при data) |
| I5 (Degraded-but-loud) | ✅ Приховування зони = by design (Focus mode). Research mode = fallback для всіх зон |
| S0–S6 (SMC) | ✅ Compute pipeline не змінюється |
| D0 (Compute ≠ Display) | ✅ NEW: server = eligibility, client = presentation. Explicit split |
| D3 (Budget = cap) | ✅ NEW: validation guard в config.py |

---

## 5. Acceptance Criteria

| AC | Given | When | Then | Test type |
|----|-------|------|------|-----------|
| AC-1 | M15 chart, XAU/USD, active market, Focus mode | Count visible SMC zones | ≤ `focus_budget_per_side × 2` (default 6) | Automated |
| AC-2 | Zone mitigated (status='mitigated') | 20+ bars pass (mitigated_ttl_bars) | Zone removed from server payload | Unit test |
| AC-3 | Zone > 6×ATR from price (proximity_atr_mult) | Server filter | Zone excluded from eligible payload | Unit test |
| AC-4 | FVG count exceeds fvg_display_cap | Server filter | Only top-N FVG by rank in payload | Unit test |
| AC-5 | Toggle Focus → Research (UI) | Re-render | More zones visible, zero server call, <16ms | Visual + timing |
| AC-6 | Toggle Research → Focus (UI) | Re-render | Budget cap applied, zones reduced | Visual |
| AC-7 | Config: focus_budget_per_side=3, structure_label_max=4, total=12 | Validation | Assert passes (3*2+4=10 ≤ 12) | Unit test |
| AC-8 | Config: focus_budget_per_side=6, structure_label_max=4, total=12 | Validation | Assert FAILS (6*2+4=16 > 12) | Unit test |

---

## 6. P-Slices (implementation plan)

### P-Φ0-1: Config + `SmcDisplayConfig` extension + validation guard (~35 LOC)

- `config.json`: proximity 15→6, + 6 нових params (`min_display_strength`, `mitigated_ttl_bars`, `focus_budget_per_side`, `focus_budget_total`, `structure_label_max`, `fvg_display_cap`)
- `config.py`: `min_display_strength` field in `SmcDisplayConfig` + budget validation guard
- **Gate**: AC-7, AC-8 (config validation tests)
- **Rollback**: revert config.json + remove guard

### P-Φ0-2: Post-mitigation TTL + FVG cap + strength gate (~20 LOC)

- `engine.py:_update_zone_lifecycle()`: step 3b (mitigated TTL, anchor=`end_ms`)
- `engine.py:_filter_for_display()`: FVG cap + `min_display_strength` threshold
- **Gate**: AC-2, AC-4 (unit tests)
- **Rollback**: remove step 3b + FVG cap lines

### P-Φ0-3: Client-side DisplayBudget.ts (~65 LOC)

- New file: `ui_v4/src/smc/DisplayBudget.ts`
- Integration in OverlayRenderer.ts (~5 LOC)
- **Gate**: AC-1, AC-5, AC-6 (visual + automated)
- **Rollback**: delete DisplayBudget.ts, revert OverlayRenderer integration

### P-Φ0-4: Focus/Research toggle UI (~20 LOC)

- HUD button or hotkey (F/R)
- Reactive state → DisplayBudget mode switch
- **Gate**: AC-5, AC-6 (visual UX)
- **Rollback**: remove toggle, hardcode 'research' mode

### Total: 4 P-slices, ~145 LOC production + ~80 LOC tests = ~225 LOC

---

## 7. Rollback

### Per-slice rollback (кожен P-slice незалежно reversible)

| P-slice | Rollback |
|---------|----------|
| P-Φ0-1 | Revert config.json to previous values. Remove validation guard |
| P-Φ0-2 | Remove step 3b in lifecycle. Remove FVG cap line |
| P-Φ0-3 | Delete DisplayBudget.ts. Revert OverlayRenderer.ts to direct rendering |
| P-Φ0-4 | Remove toggle. Set mode='research' (show all eligible) |

### Full rollback

Revert all config changes + delete DisplayBudget.ts + remove lifecycle step 3b + remove FVG cap. System returns to pre-ADR-0028 behavior. ~5 хвилин.

---

## 8. Очікуваний ефект

На базі M15 скріна:

| Фільтр | Зон до | Зон після | Що прибрало |
|--------|--------|-----------|-------------|
| Proximity 15→6 ATR | ~20 | ~12–14 | Далекі зони (нижня/верхня частина chart) |
| Min strength 0.15→0.25 | ~14 | ~10–12 | Слабкі зони (noise) |
| Post-mitigation TTL | ~12 | ~8–10 | Зони що ціна вже пройшла (dimmed ghosts) |
| FVG cap (4) | ~10 | ~8 | Зайві FVG |
| **Focus budget** (3/side) | ~8 | **≤6 zones** | Найслабші з eligible |

**Результат**: з ~20+ зон → **≤6 zones + ≤4 structure labels + levels = ≤12 total**. Це відповідає Clean Chart Doctrine budget rule.

---

## 9. Ризики

| # | Ризик | Probability | Impact | Mitigation |
|---|-------|-------------|--------|------------|
| R1 | proximity=6 ховає потрібну зону | LOW | Missed zone | Research mode fallback. Якщо часто — tune up |
| R2 | Focus budget=3/side мало | LOW | Missed zone | Research mode. Config adjustable |
| R3 | Post-mitigation TTL=20 — зона зникає до того як трейдер її помітив | VERY LOW | — | 20 bars = 5h на M15, достатньо |
| R4 | Client-side budget out of sync з server filter | IMPOSSIBLE | — | Server = eligibility, client = presentation. Ортогональні concerns |

---

## 10. Відкриті питання (вирішені)

| # | Питання (з v1) | Рішення | Обґрунтування |
|---|----------------|---------|---------------|
| K1 | Server vs Client filter | **Hybrid**: server=eligibility, client=presentation | Instant toggle UX + no logic duplication |
| K2 | Config explosion (40 params) | **9 params** (2 tuned + 1 extend + 6 new) + validation guard | Мінімальний набір для Φ0 |
| K3 | frozen=True SmcZone conflict | **Display DTO** client-side, server не мутує | Zero changes в types.py |
| D-05 | Scope creep (Φ1=detection rewrite) | **Φ0 only**. Φ1→ADR-0029 | Clean scope boundary |

---

## 11. Display Інваріанти (D-серія, Φ0 subset)

| # | Інваріант | Enforcement |
|---|-----------|-------------|
| D0 | **Server = eligibility, Client = presentation**: server не знає про Focus/Research, client не фільтрує за proximity/TTL | Code review: DisplayBudget.ts не імпортує proximity logic. engine.py не імпортує budget mode |
| D1 | **Mitigation = CLOSE, not wick**: існуючий інваріант (R-04), зберігається | [EXISTS engine.py:104-118] |
| D3 | **Budget ≤ config cap**: порушення = баг S1 | Config validation guard + AC-1 test |
| D6 | **Mitigated zone TTL = finite**: mitigated зона зникає за `mitigated_ttl_bars` барів | Step 3b + AC-2 test |

---

## 12. Зв'язок з наступними ADR

```
ADR-0028 v2 (THIS: Elimination Engine / Φ0)
    │
    ├── ADR-0029 (FUTURE): OB Confluence Engine / Φ1
    │     └── Confluence scoring, Wick-OB, FVG strength
    │
    ├── ADR-0030 (FUTURE): TF Sovereignty / Φ2
    │     └── Cross-TF projection semantics, visibility matrix
    │
    └── ADR-0031 (FUTURE): Scenario Product / Φ3
          └── Alignment Banner, Bias engine, Scenario state machine
```

**Gate rule**: ADR-0029 не стартує поки ADR-0028 AC-1..AC-8 не пройдені. ADR-0030 не стартує поки ADR-0029 не пройшов gate. І так далі.

---

## Appendix A: Порівняння v1 vs v2

| Аспект | v1 (NO-GO) | v2 (цей) |
|--------|------------|----------|
| Scope | Φ0+Φ1+Φ2+Φ3 (4 фази) | Φ0 тільки |
| Evidence | Screenshots, 0 path:line | 7 [VERIFIED path:line] |
| Root cause accuracy | 4/7 claims false/misleading | Honest: EXISTS vs MISSING table |
| frozen=True | Not addressed (D-02 blocker) | Display DTO, zero types.py changes |
| Filter placement | Contradictory (Q1-B vs §3.0.8) | Explicit hybrid: server=eligibility, client=presentation |
| Config params | ~40 нових | 9 (2 tuned + 1 extend + 6 new) + validation |
| LOC estimate | ~1200–1600 | ~225 (145 prod + 80 test) |
| New files | 5 | 2 (DisplayBudget.ts + test) |
| P-slices | Not defined concretely | 4 explicit P-slices |
| Rollback | Per-phase | Per-slice (кожен незалежно) |
