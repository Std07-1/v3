# ADR-0042: Delta Frame State Synchronization

- **Статус**: Implemented
- **Дата**: 2026-03-23
- **Автор**: R_ARCHITECT
- **Initiative**: `delta_frame_parity_v1`
- **Пов'язані ADR**: ADR-0024 (SMC Engine), ADR-0028 (Elimination Engine), ADR-0029 (Confluence Scoring), ADR-0031 (Bias Banner), ADR-0033 (Narrative), ADR-0039 (Signal Engine), ADR-0041 (P/D Badge + EQ Line)

---

## 1. Контекст і проблема

### 1.1 Виявлення

Повне дослідження задокументовано у `research/DISCOVERY REPORT.md` (MODE=DISCOVERY, 6 findings: 2 CRITICAL, 3 MEDIUM, 1 INFO). Нижче — архітектурний виклад проблеми з кодовими доказами.

### 1.2 Суть: Full/Delta frame parity violation

WS-сервер (`runtime/ws/ws_server.py`) відправляє два типи frames до UI:

- **Full frame** (`frame_type: "full"`) — при TF switch, reconnect, replay. Містить повний стан: `zones`, `swings`, `levels`, `trend_bias`, `zone_grades`, `bias_map`, `momentum_map`, `pd_state`, `signals`, `narrative`, `shell`.
- **Delta frame** (`frame_type: "delta"`) — при кожному bar update (1s poll loop). Містить інкрементальні зміни: `smc_delta` (new/updated/mitigated zones, swings, levels), `narrative`, `shell`, `session_levels`.

**Порушення**: Delta frames **не включають** metadata, необхідну для коректного рендерингу:

| Поле | Full frame | Delta frame | Наслідок |
|------|-----------|-------------|----------|
| `zone_grades` | ✅ [ws_server.py:435-436] | ❌ | Зони без grade → DisplayBudget фільтрує як 'C' → **невидимі** |
| `bias_map` | ✅ [ws_server.py:439] | ❌ | BiasBanner показує stale bias між full frames |
| `momentum_map` | ✅ [ws_server.py:441] | ❌ | Directional displacement stale |
| `pd_state` | ✅ [ws_server.py:443] | ❌ | P/D badge зникає після delta |
| `signals` | ✅ [ws_server.py:678-685] | ❌ | Signal badges flicker |

Додатково, `applySmcDelta()` на фронтенді (`smcStore.ts:100`) **не зберігає** `zone_grades` та `pd_state` з попереднього стану:

```typescript
// smcStore.ts:100 — return statement applySmcDelta():
return { zones, swings, levels,
    trend_bias: delta.trend_bias ?? current.trend_bias ?? null,
    bias_map: current.bias_map,           // ✅ preserves
    momentum_map: current.momentum_map    // ✅ preserves
};
//  zone_grades: MISSING  ❌
//  pd_state: MISSING     ❌
```

[VERIFIED smcStore.ts:100] — return object має 6 полів; SmcData interface має 8 полів.
[VERIFIED ws_server.py:420-444] — `_build_full_frame()` включає zone_grades, bias_map, momentum_map, pd_state.
[VERIFIED ws_server.py:1092-1175] — delta loop: get_bias_map/get_momentum_map/get_pd_state/get_signals/get_zone_grades **не викликаються**.

### 1.3 Impact chain (F1 → зони зникають)

```
T=0ms   WS: delta frame (no zone_grades, no pd_state)
T=2ms   applySmcDelta() → returns SmcData WITHOUT zone_grades, pd_state
T=5ms   OverlayRenderer.patch() → normalizeSmcData() → zone_grades={}
T=6ms   _gradeCache update: Object.keys({}).length === 0 → SKIP update
        [VERIFIED OverlayRenderer.ts:303-305]
T=16ms  render() → OB zone: _gradeCache[z.id] === undefined → gradeSuffix = ''
        → DisplayBudget treats as grade 'C' → FILTERED in Focus AND Research mode
        → ZONE INVISIBLE
```

Порівняння: `applySmcFull()` (smcStore.ts:21-39) коректно включає ВСІ 8 полів SmcData.

### 1.4 Три порушені властивості

| Властивість | Визначення | Як порушена |
|-------------|-----------|-------------|
| **Детермінізм** | Same bar → same UI result | Один і той самий бар при отриманні через full frame показує OB з grade A+; через delta frame — OB невидима (grade C fallback) |
| **Ідемпотентність** | `applySmcFull()` ≡ accumulated `applySmcDelta()` | Після N delta frames: `zone_grades={}`, `pd_state=null`, `bias_map=stale`. Після full frame: всі поля актуальні. Стан дивергує з часом. |
| **Контракт-first** | UI може реконструювати повний стан з frames | UI **не може** відновити zone_grades/pd_state з delta frame — ці дані просто відсутні у wire format |

### 1.5 Перелік findings (references to Discovery Report)

| ID | Severity | Суть | Root cause |
|----|----------|------|------------|
| **F1** | S1 (CRITICAL) | `applySmcDelta()` губить zone_grades та pd_state | smcStore.ts return statement — 2 поля не включені |
| **F2** | S1 (CRITICAL) | bias_map, momentum_map, pd_state не надсилаються у delta frame | ws_server.py delta loop — відповідні getter'и не викликаються |
| **F4** | S2 (MEDIUM) | signals не надсилаються у delta frame | ws_server.py delta loop — get_signals() не викликається |
| **F6** | S2 (INFO) | pd_state загублений у delta (same root as F1) | Те саме applySmcDelta() return statement |
| **F3** | S2 (MEDIUM) | Label pill background непослідовний | OverlayRenderer.ts pill alpha formula + isLightTheme gate |
| **F5** | S2 (MEDIUM) | FVG flicker на межі lookback window | engine.py D-02 eviction без grace period |

### 1.6 Failure Model

| # | Сценарій | Наслідок | Поточний захист |
|---|----------|----------|-----------------|
| FM-1 | Трейдер дивиться M15, новий OB з'являється через delta | OB невидимий (grade not propagated → C → filtered) | Жоден. Тільки TF switch (full frame) «лікує» |
| FM-2 | H4 бар завершується, bias змінюється з bearish на bullish | BiasBanner показує старе bearish до наступного full frame | Часткова: App.svelte `cachedBiasMap` зберігає останній, але не оновлюється з нового bias_map |
| FM-3 | Signal з'являється, наступний delta не має signals | Signal badge зникає → з'являється при full frame → зникає | Жоден |
| FM-4 | FVG на межі lookback window: bar N inside, bar N+1 outside | FVG з'являється, зникає, знову з'являється | Жоден — D-02 eviction без grace period |
| FM-5 | Трейдер не перемикає TF протягом 30 хвилин | zone_grades = stale від останнього full frame. Нові зони = без grade = C = invisible | Жоден |
| FM-6 | Cold start: перший frame = full (ok), усі наступні = delta (broken state) | Після першого delta: pd_state=null, zone_grades порожній | Жоден |
| FM-7 | Backend failure у get_bias_map() під час delta | bias_map взагалі не надсилається (не delta-specific, але в delta не recoverable) | Full frame при TF switch |

---

## 2. Constraints

- **Інваріанти**: I4 (single update stream — delta через той самий WS), S0 (core/ pure — не торкаємось), S5 (config SSOT), S6 (wire format sync types.py ↔ types.ts)
- **НЕ торкається**: I0 (dependency rule), I1 (UDS), I2 (time geometry), I3 (final > preview)
- **Budget**: ~120 LOC across 5 files (backend + frontend), 0 new files, 0 new dependencies
- **Backward compatibility**: Wire format extension (additive — нові поля в delta frame). Old clients ігнорують unknown fields.
- **Performance**: zone_grades ~200 bytes, bias_map ~100 bytes, pd_state ~80 bytes, momentum_map ~100 bytes. Додається **тільки** коли `_any_complete=true` (тобто коли є завершений бар). ~500 bytes на complete bar event — незначно для WS.
- **Frequency**: delta frames ~1/sec, complete bars ~1/min (M1). Metadata повторюється тільки при complete bar — не кожну секунду.

---

## 3. Розглянуті альтернативи

### Alternative A: "Thick delta on complete" — metadata у delta frame при завершенні бару

- **Суть**: Backend: у delta loop, **після `_any_complete`**, викликати `get_zone_grades()`, `get_bias_map()`, `get_momentum_map()`, `get_pd_state()`, `get_signals()` і включити у frame. Frontend: `applySmcDelta()` зберігає `zone_grades` та `pd_state` з `current` (або мержить з нового delta). Pill alpha floor. FVG grace period.
- **Pros**: Мінімальний blast radius. Metadata оновлюється при кожному complete bar (коли state реально змінюється). Не збільшує трафік preview-only frames. Зберігає lazy evaluation (метадані тільки коли потрібно).
- **Cons**: Між complete bars metadata = stale (але це ≤1 хв для M1, ≤5 хв для M5 — прийнятно, бо state не змінюється між bars).
- **Blast radius**: ws_server.py (add getters in delta loop), smcStore.ts (fix return + merge), OverlayRenderer.ts (pill floor), engine.py (grace period).
- **LOC estimate**: ~120

### Alternative B: "Always-thick delta" — metadata у кожному delta frame

- **Суть**: Кожен delta frame (навіть preview-only) включає zone_grades, bias_map, momentum_map, pd_state, signals. Frontend завжди отримує повний metadata.
- **Pros**: Максимальна простота на фронтенді — кожен frame self-sufficient. Ніколи не stale.
- **Cons**: **Збільшує трафік WS в ~2-3x**: metadata ~500 bytes × 1 frame/sec = додатковий 0.5KB/s per subscription. Для 13 символів = +6.5KB/s. Not critical, але unnecessary — metadata не змінюється без complete bar. Зайві виклики `get_zone_grades()` / `get_signals()` (CPU overhead — get_signals рахує narrative).
- **Blast radius**: ws_server.py, smcStore.ts.
- **LOC estimate**: ~80

### Alternative C: "Frontend caches everything" — metadata тільки у full frame, frontend зберігає

- **Суть**: Backend НЕ змінюється. Frontend `applySmcDelta()` виправляється щоб зберігати zone_grades/pd_state з `current`. bias_map вже зберігається. Додати signals caching в App.svelte.
- **Pros**: 0 backend змін.
- **Cons**: **Не вирішує F2**: bias_map оновлюється тільки при full frame (TF switch / reconnect). Якщо H4 bar завершується і bias змінюється з bearish→bullish — UI не побачить до перемикання TF. **Не вирішує F4**: signals теж не оновлюються. **Часткова**: вирішує тільки F1+F6 (frontend drop). Порушене властивість idempotency залишається — backend не відправляє metadata at all.
- **Blast radius**: smcStore.ts, App.svelte.
- **LOC estimate**: ~30

### Рішення: Alternative A — "Thick delta on complete"

**Обґрунтування**: Єдина альтернатива, що вирішує all 6 findings. Alt B — overkill (зайвий трафік, CPU). Alt C — неповна (F2, F4 не вирішені; bias stale; idempotency порушена). Alt A — balanced: metadata тільки при complete bar (коли state може змінитись), мінімальний overhead.

---

## 4. Рішення (деталі)

### 4.1 Types / Contracts

#### Wire format зміна: delta frame

Поточний delta frame:
```json
{
  "type": "render_frame",
  "frame_type": "delta",
  "candles": [...],
  "smc_delta": { "new_zones": [], "mitigated_zone_ids": [], ... },
  "narrative": {...},
  "shell": {...},
  "session_levels": [...]
}
```

Розширення (**additive**, тільки при `_any_complete`):
```json
{
  ...existing fields...,
  "zone_grades": { "zone_id": { "score": 8, "grade": "A+", "factors": [...] } },
  "bias_map": { "900": "bullish", "3600": "bearish", ... },
  "momentum_map": { "900": { "b": 3, "r": 1 }, ... },
  "pd_state": { "range_high": 3360.0, "range_low": 3280.0, "equilibrium": 3320.0, "pd_percent": 72.0, "label": "PREMIUM" },
  "signals": [{ ... }]
}
```

Поля додаються **опціонально** — тільки коли complete bar ≥1 у delta batch. Це відповідає семантиці: metadata може змінитись тільки після завершеного бару.

#### `SmcDeltaWire` (types.ts) — **НЕ змінюється**

zone_grades, bias_map, momentum_map, pd_state, signals — це **top-level frame** поля (не частина SmcDeltaWire). Це вже відповідає [VERIFIED types.ts:85-94] — SmcDeltaWire описує тільки інкрементальні зміни зон/свінгів/рівнів. Metadata = frame-level, як і в full frame.

#### `SmcData` (types.ts) — **НЕ змінюється**

Вже має всі 8 полів включаючи zone_grades, bias_map, momentum_map, pd_state. [VERIFIED types.ts:63-75].

### 4.2 Backend: delta loop metadata injection (ws_server.py)

Місце: `_global_delta_loop()` (ws_server.py:843), після блоку `if _any_complete and candles:` (line 1117).

**Логіка**:
```python
# After existing narrative/shell/session_levels injection:
if _any_complete:
    # Zone grades (ADR-0029): refresh after on_bar() processed complete bars
    _zg = _smc_runner.get_zone_grades(symbol, tf_s)
    if _zg:
        frame["zone_grades"] = _zg
    # Bias map (ADR-0031): may change after structure detection on complete bar
    _bm = _smc_runner.get_bias_map(symbol)
    if _bm:
        frame["bias_map"] = _bm
    # Momentum map: displacement counts
    _mm = _smc_runner.get_momentum_map(symbol)
    if _mm:
        frame["momentum_map"] = _mm
    # P/D state (ADR-0041): recalculated after new swings from complete bar
    _pd = _smc_runner.get_pd_state(symbol, tf_s)
    if _pd:
        frame["pd_state"] = _pd
    # Signals (ADR-0039): refresh after narrative update
    # (only when narrative was successfully computed above)
    if "narrative" in frame:
        _narr_obj = _smc_runner.get_narrative(...)  # already have from above
        _sigs, _sig_alerts = _smc_runner.get_signals(...)
        if _sigs:
            frame["signals"] = [s.to_wire() for s in _sigs]
```

**Нюанс**: `get_signals()` потребує narrative об'єкт — він вже обчислений декількома рядками вище у тому ж `if _any_complete` блоці. Сигнали додаються тільки коли narrative є.

**Error handling**: кожен getter у `try/except` з `_log.warning()` (аналогічно full frame path, ws_server.py:631-642). Жоден збій не ламає frame — поле просто відсутнє.

### 4.3 Frontend: applySmcDelta() fix (smcStore.ts)

Виправити return statement (line ~100):

```typescript
// BEFORE (broken):
return { zones, swings, levels,
    trend_bias: delta.trend_bias ?? current.trend_bias ?? null,
    bias_map: current.bias_map,
    momentum_map: current.momentum_map
};

// AFTER (fixed):
return { zones, swings, levels,
    trend_bias: delta.trend_bias ?? current.trend_bias ?? null,
    zone_grades: current.zone_grades,     // preserve from last full/delta
    bias_map: current.bias_map,
    momentum_map: current.momentum_map,
    pd_state: current.pd_state,           // preserve from last full/delta
};
```

Це забезпечує що zone_grades та pd_state зберігаються між delta frames. Коли наступний delta frame приносить нові zone_grades (після complete bar) — ChartPane оновить SmcData з frame-level полями (аналогічно full frame parsing path).

#### ChartPane delta frame parsing

ChartPane.svelte (або відповідний frame router) має мержити frame-level metadata:

```typescript
// In delta frame handler:
if (frame.zone_grades) smcData.zone_grades = frame.zone_grades;
if (frame.bias_map) smcData.bias_map = frame.bias_map;
if (frame.momentum_map) smcData.momentum_map = frame.momentum_map;
if (frame.pd_state !== undefined) smcData.pd_state = frame.pd_state;
if (frame.signals) // update signal store
```

### 4.4 Rendering fixes: pill alpha floor + FVG grace period

#### F3: Pill alpha floor (OverlayRenderer.ts)

Поточна формула (line 773):
```typescript
const pillAlpha = (0.20 + 0.55 * proximity) * dimMult;
```

При `proximity=0, dimMult=0.35` (projected zone) → `pillAlpha = 0.07` — невидимий.

Фікс: додати floor:
```typescript
const pillAlpha = Math.max(0.15, (0.20 + 0.55 * proximity) * dimMult);
```

`0.15` = мінімальний видимий alpha для pill на dark background. Проекції та далекі зони матимуть subtle але видимий pill. Light theme gate (`!this._isLightTheme`) залишається — на light theme pills не потрібні (text достатньо контрастний).

#### F5: FVG grace period (engine.py)

Поточна D-02 eviction (line 112-122):
```python
stale_fvg = [
    zid for zid, z in active_zones.items()
    if z.kind.startswith("fvg")
    and zid not in fresh_ids
    and z.status not in ("filled", "mitigated")
]
for zid in stale_fvg:
    del active_zones[zid]
```

Проблема: FVG на межі lookback window — inside → outside → inside по черзі → flicker.

Фікс: додати grace period через `age_bars` tracking:
```python
stale_fvg = [
    zid for zid, z in active_zones.items()
    if z.kind.startswith("fvg")
    and zid not in fresh_ids
    and z.status not in ("filled", "mitigated")
    and z.age_bars >= config.fvg_grace_bars  # NEW: grace period
]
```

`config.fvg_grace_bars` = 3 (default). FVG що прожили ≥3 бари і зникли з fresh — evict. FVG молодше 3 барів — пережидають grace period. Це запобігає flicker на межі lookback без впливу на zone cap (cap діє після D-02).

**Config**: новий ключ `config.json:smc.fvg_grace_bars: 3` (default 3). SSOT-compliant (S5).

### 4.5 Config зміни

Один новий ключ у `config.json:smc`:

```json
"smc": {
    ...existing keys...,
    "fvg_grace_bars": 3
}
```

`SmcConfig` (config.py): додати `fvg_grace_bars: int = 3`.

---

## 5. P-Slices (план реалізації)

| Slice | Scope | Files | LOC | Інваріант | Verify | Rollback |
|-------|-------|-------|-----|-----------|--------|----------|
| **P1** | F1+F6 fix: `applySmcDelta()` preserves zone_grades + pd_state | smcStore.ts | ~5 | S6 (wire parity) | 1) Start system, view M15 XAU/USD. 2) Wait for delta frame (no TF switch). 3) Verify OB zones remain visible with grade badges. 4) Verify pd_state not null in dev console `smcData`. | `git checkout -- ui_v4/src/stores/smcStore.ts` |
| **P2** | F2+F4: Backend sends zone_grades, bias_map, momentum_map, pd_state, signals in delta on complete bar | ws_server.py, ChartPane.svelte (delta merge) | ~50 | I4 (single stream), S6 (wire) | 1) Watch live M1 XAU/USD, no TF switch for 5+ min. 2) Verify BiasBanner updates after H1 complete bar (check dev console for frame containing bias_map). 3) Verify signal badge persists between delta frames. 4) Check WS frame size: delta with metadata ≤ 2KB. | `git checkout -- runtime/ws/ws_server.py ui_v4/src/layout/ChartPane.svelte` |
| **P3** | F3: Pill alpha floor + F5: FVG grace period | OverlayRenderer.ts, engine.py, config.py, config.json | ~20 | S5 (config SSOT), Display budget | 1) Visual: all zone pills have visible background in dark theme (min alpha 0.15). 2) FVG test: run replay, observe no FVG flicker at lookback boundary. 3) `pytest tests/test_smc_e1.py -v` passes. | `git checkout -- ui_v4/src/chart/overlay/OverlayRenderer.ts core/smc/engine.py core/smc/config.py config.json` |

**Total**: ~75 LOC, 6 files (0 new).

**Order**: P1 → P2 → P3. Strict ordering: P1 is prerequisite for P2 (frontend must preserve state before backend sends it). P3 is independent but logically last (rendering refinement).

### P-Slice details

#### P1: Frontend state preservation (~5 LOC, 1 file)

- **Що**: Додати `zone_grades: current.zone_grades` та `pd_state: current.pd_state` до return statement `applySmcDelta()`.
- **Чому перший**: Мінімальний ризик, максимальний ефект. Вирішує F1+F6 повністю. Навіть без P2 (backend fix) — grade cache перестане очищатись при delta frames. Зони збережуть grade від останнього full frame.
- **Test**: Dev console: `JSON.stringify(smcData.zone_grades)` не порожній після 5 delta frames без TF switch.

#### P2: Backend thick delta on complete (~50 LOC, 2 files)

- **Що (backend)**: У `_global_delta_loop()`, після `if _any_complete:` блоку з narrative, додати виклики `get_zone_grades()`, `get_bias_map()`, `get_momentum_map()`, `get_pd_state()`, `get_signals()` з try/except і додати у frame.
- **Що (frontend)**: У ChartPane delta frame handler — мержити frame-level zone_grades, bias_map, momentum_map, pd_state, signals у SmcData (override current з нового якщо present).
- **Чому другий**: Залежить від P1 (frontend має зберігати state). Backend починає відправляти — frontend мусить правильно обробити.
- **Test**: WS frame logger (dev console): delta frame після M1 complete bar має `zone_grades`, `bias_map`.

#### P3: Rendering refinement (~20 LOC, 4 files)

- **Що**: 1) `OverlayRenderer.ts`: `Math.max(0.15, pillAlpha)`. 2) `engine.py`: grace period condition `z.age_bars >= config.fvg_grace_bars`. 3) `config.py`: new field `fvg_grace_bars: int = 3`. 4) `config.json`: `"fvg_grace_bars": 3`.
- **Чому третій**: Rendering refinement, не впливає на state pipeline. Можна паралельно з P2 при потребі.
- **Test**: Visual check pill alpha + replay test for FVG stability.

---

## 6. Consequences

### Що ЗМІНЮЄТЬСЯ

| Що | Деталі |
|----|--------|
| `applySmcDelta()` return (smcStore.ts) | Додано `zone_grades` та `pd_state` з `current` |
| Delta frame wire format (ws_server.py) | Additive: zone_grades, bias_map, momentum_map, pd_state, signals при complete bar |
| ChartPane delta handler | Merge frame-level metadata у SmcData |
| Pill alpha (OverlayRenderer.ts) | Floor 0.15 замість 0 |
| FVG eviction (engine.py) | Grace period `fvg_grace_bars` (config-driven) |
| config.json:smc | Новий ключ `fvg_grace_bars: 3` |
| SmcConfig (config.py) | Новий field `fvg_grace_bars` |

### Що НЕ ЗМІНЮЄТЬСЯ

- `SmcDeltaWire` (types.ts) — delta інкрементальна структура зон/свінгів/рівнів не змінюється
- `SmcData` (types.ts) — вже має всі потрібні поля
- Full frame path — без змін (вже коректний)
- `_build_full_frame()` — без змін
- `applySmcFull()` — без змін (вже коректний)
- `normalizeSmcData()` — без змін (generic, працює для будь-якого SmcData)
- core/smc/ детектори — без змін (pure layer)
- UDS / derive / ingest pipeline — не торкається

### Нові інваріанти

- **DF-1**: `applySmcDelta()` ПОВИНЕН повертати об'єкт з усіма 8 полями SmcData. Неповне повернення = contract violation.
- **DF-2**: Delta frame з `_any_complete=true` ПОВИНЕН включати zone_grades та bias_map. Відсутність при наявності smc_runner = degradation (log warning).
- **DF-3**: FVG eviction D-02 не діє на зони молодші `fvg_grace_bars` (запобігає boundary flicker).

### Performance impact

- Delta frame size: +~500 bytes при complete bar (~1/min для M1). Negligible.
- CPU: 5 додаткових getter calls при complete bar. get_zone_grades() = dict lookup O(1). get_bias_map() = dict copy O(TF_count). get_signals() = вже обчислений narrative reuse. Total: <1ms.
- No impact on preview-only delta frames (non-complete bars).

---

## 7. Rollback

### Per-slice rollback

| Slice | Rollback command | Side effects |
|-------|-----------------|--------------|
| P1 | `git checkout -- ui_v4/src/stores/smcStore.ts` | zone_grades/pd_state знову будуть губитись при delta — повернення до F1 поведінки |
| P2 | `git checkout -- runtime/ws/ws_server.py ui_v4/src/layout/ChartPane.svelte` | Delta не матиме metadata — повернення до F2/F4 поведінки. P1 все ще зберігає state від full frame. |
| P3 | `git checkout -- ui_v4/src/chart/overlay/OverlayRenderer.ts core/smc/engine.py core/smc/config.py config.json` | Pill alpha повернеться до 0 floor; FVG grace period зникне. Потребує rebuild ui_v4. |

### Full rollback

```
git checkout -- ui_v4/src/stores/smcStore.ts runtime/ws/ws_server.py \
  ui_v4/src/layout/ChartPane.svelte ui_v4/src/chart/overlay/OverlayRenderer.ts \
  core/smc/engine.py core/smc/config.py config.json
cd ui_v4 && npm run build && cd ..
```

---

## 8. Test Matrix

| Finding | P-Slice | Unit test | Integration test | Visual test |
|---------|---------|-----------|------------------|-------------|
| F1+F6 | P1 | `applySmcDelta()` returns all 8 SmcData fields | — | Zones visible after 5 delta frames |
| F2 | P2 | — | WS frame contains bias_map after complete bar | BiasBanner updates without TF switch |
| F4 | P2 | — | WS frame contains signals after complete bar | Signal badge persists between deltas |
| F3 | P3 | — | — | Pill alpha ≥ 0.15 on all zones (dark theme) |
| F5 | P3 | `_update_zone_lifecycle()` with age_bars < grace | Replay: FVG at lookback boundary = stable | No FVG flicker in replay |

### Recommended tests to add

1. **test_smc_store_delta_parity.ts** (new): `applySmcDelta()` output has same keys as `applySmcFull()` output.
2. **test_fvg_grace_period.py** (addition to test_smc_e1.py): FVG with age_bars < fvg_grace_bars survives D-02 eviction.
3. **WS integration** (manual): delta frame after M1 complete → `zone_grades` present → dev console check.

---

## 9. Open Questions

| # | Питання | Хто верифікує | Дедлайн |
|---|---------|---------------|---------|
| OQ-1 | Як часто full frame надсилається за нормальних умов? (Тільки TF switch + reconnect? Чи є periodic refresh?) | R_BUG_HUNTER: grep WS logs + code | Перед P2 |
| OQ-2 | Чи App.svelte `cachedBiasMap` (line ~289) частково компенсує F2? Чи потрібна міграція на direct SmcData usage? | R_PATCH_MASTER: trace App.svelte bias flow | Під час P2 |
| OQ-3 | Чи signals мають кешуватись на App.svelte level (як `cachedBiasMap`), чи достатньо SmcData flow? | R_CHART_UX: визначити UX pattern | Перед P2 impl |

---

## 10. Cross-Role Plan

| Роль | Завдання | Коли |
|------|---------|------|
| **R_PATCH_MASTER** | P1 → P2 → P3 sequential implementation | Після accept |
| **R_BUG_HUNTER** | Review кожного slice: verify evidence lines, contract compliance | Після кожного slice |
| **R_CHART_UX** | Visual verification P1 (zones visible), P2 (bias updates), P3 (pill alpha) | Після P1, P2, P3 |
| **R_DOC_KEEPER** | Update contracts.md (delta frame additive fields), ADR index | Після all slices |
| **R_TRADER** | Validate: OB zones з grade visible? Bias timely? Signal persistent? | Після P2 |
