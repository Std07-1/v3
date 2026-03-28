# ADR-0043: UI v4 — Canvas Safe Zones + State Sync Hardening

- **Статус**: Accepted — Implemented (2026-03-24, patch-master, changelog 20260324-001)
- **Дата**: 2026-03-23
- **Автор**: R_ARCHITECT
- **Initiative**: `ui_v4_hardening_v1`
- **Пов'язані ADR**: ADR-0024c (zone rendering Z1–Z10), ADR-0026 (level rendering L1–L6), ADR-0032 (render throttle), ADR-0036 (premium shell), ADR-0041 (P/D badge), ADR-0042 (delta frame sync)

---

## 1. Контекст і проблема

### 1.1 Що зламано

Після серії ADR (0024–0042) UI v4 накопичив 6 підтверджених дефектів, що поділяються на два кластери:

**Кластер A — Canvas Safe Zones & UI Collisions (D1, D2, D7)**:
Canvas overlay елементи (BOS/CHoCH labels, zone labels, level labels) рендеряться без обмежень по Y-координаті. Відсутня константа `CANVAS_SAFE_TOP_Y` — елементи заходять під HUD зону (y < 75px). UI-елементи `smc-panel` і `top-right-bar` мають z-index колізію.

**Кластер B — State Sync & SSOT (D3, D4, D5, D6)**:
Thick-delta metadata merging розподілений між 3 файлами без єдиного SSOT. `pd_state: null` не очищає кеш. `boot_id` change не скидає UI кеші. `filterMitigatedZones` — dead code.

### 1.2 Evidence Pack

| ID | Defect | Severity | Evidence |
|----|--------|----------|----------|
| D1 | `CANVAS_SAFE_TOP_Y` не реалізований — overlay елементи заходять під HUD | S1 | [NOT FOUND] `grep CANVAS_SAFE_TOP_Y ui_v4/src/` → 0 results |
| D2 | `tv-legend` (OHLCV tooltip) позиціонування | S3 | [VERIFIED `OhlcvTooltip.svelte:82`] `top: 52px`. Клас `shell-chips` відсутній у коді — колізія C1 з RECON report не підтверджена. Потребує ревізії з chart-ux memory. |
| D3 | `pd_state: null` з delta frame не очищає кеш — guard `pd !== null` блокує | S2 | [VERIFIED `ChartPane.svelte:424`] `pd !== undefined && pd !== null ? { pd_state: pd } : {}` — null заблоковано. [VERIFIED `App.svelte:289`] `f?.pd_state !== undefined` — тут null пропускається коректно, але значення = null не propagate до ChartPane. [VERIFIED `smcStore.ts:100`] `pd_state: current.pd_state` — delta ігнорується повністю. |
| D4 | `filterMitigatedZones` — dead code, toggle відсутній | S2 | [VERIFIED `smcStore.ts:124-128`] Функція визначена, exported, нігде не викликається. `hide_mitigated` згадується тільки в коментарі (line 121). |
| D5 | `boot_id` change не очищає UI кеші | S2 | [VERIFIED `frameRouter.ts:89-101`] При зміні boot_id: ✅ `lastSeq` скидається, ❌ `smcData`, `cachedNarrative`, `cachedShell`, `cachedBiasMap`, `cachedPdState` — не очищаються. Stale overlay між restart detection та першим full frame. |
| D6 | Thick-delta metadata merging у 3 місцях | S3 | [VERIFIED `smcStore.ts:100`] applySmcDelta не оновлює zone_grades/bias_map/momentum_map/pd_state — зберігає current. [VERIFIED `ChartPane.svelte:406-427`] Після applySmcDelta — окремий merge block. [VERIFIED `App.svelte:278-289`] Третій кеш-шар. Одна відповідальність — три місця. |
| D7 | `smc-panel` (z:36) vs `top-right-bar` (z:35) — overlap | S3 | [VERIFIED `ChartPane.svelte:788`] smc-panel: top 36px, right 64px, z-index 36. [VERIFIED `App.svelte:604`] top-right-bar: top 8px, right 64px, z-index 35. Обидва at right:64px з вертикальним overlap. |

### 1.3 Failure Model

| # | Сценарій | Наслідок | Ймовірність |
|---|----------|----------|-------------|
| F1 | BOS/CHoCH label рендериться при y=10 — під HUD | Трейдер не бачить critical structure label. Може пропустити setup. | Висока (кожен TF де верхня зона близько до price range top) |
| F2 | Сервер надсилає `pd_state: null` (інструмент без P/D) | Старий pd_state залишається — badge показує Premium/Discount для інструменту що не підтримує P/D | Середня (switch між crypto і forex) |
| F3 | Server restart (boot_id change) + delta before full | Stale SMC overlay (старі зони/рівні) поверх нових candles. Трейдер бачить фантомні зони ~1-3s. | Низька (тільки при server restart) |
| F4 | Mitigated зони не ховаються навіть при config hide_mitigated=true | Clean Chart Doctrine порушена — "кладовище" зон на чарті. Budget violation S1. | Середня (залежить від config) |
| F5 | Click на правому верхньому куті — smc-panel перехоплює event замість top-right-bar | Theme/candle-style picker недоступний коли smc-panel видимий | Низька (вузька зона overlap) |

---

## 2. Обмеження (Constraints)

- **Інваріанти**: I4 (один update-потік), I5 (degraded-but-loud), S0 (SMC = read-only overlay), S6 (wire format sync)
- **Бюджет**: ~300 LOC total across 5 files. Жодних нових залежностей.
- **Зворотна сумісність**: wire format НЕ змінюється. Тільки client-side обробка.
- **Performance**: CANVAS_SAFE_TOP_Y guard = 1 порівняння per label render. Negligible.
- **Файли у scope**: `OverlayRenderer.ts`, `ChartPane.svelte`, `App.svelte`, `smcStore.ts`, `frameRouter.ts`

---

## 3. Розглянуті альтернативи

### Альтернатива A: Централізований state manager + canvas safe zone constants

**Суть**: Винести всі thick-delta metadata fields до `applySmcDelta` (єдиний SSOT). Додати canvas safe zone constants в `OverlayRenderer.ts`. Підключити `boot_id` reset через callback у frameRouter. Активувати `filterMitigatedZones`.

- **Pros**:
  - Один SSOT для state merging (smcStore.ts)
  - Canvas safe zones = одна група констант у OverlayRenderer
  - boot_id reset = explicit callback, не implicit hope на full frame
  - filterMitigatedZones оживає з toggle
  - Мінімальний blast radius — зміни тільки в існуючих файлах
- **Cons**:
  - Потрібно змінити сигнатуру `applySmcDelta` (додати optional metadata fields)
  - ChartPane thick-delta block стає менше, але не зникає повністю (UI-specific fields залишаються)
- **Blast radius**: `smcStore.ts`, `ChartPane.svelte`, `App.svelte`, `frameRouter.ts`, `OverlayRenderer.ts`
- **LOC estimate**: ~250

### Альтернатива B: Окремий SmcStateManager class

**Суть**: Створити новий файл `smcStateManager.ts` — клас що інкапсулює всю state management логіку (apply full/delta, cache, metadata merge, boot_id reset). ChartPane і App.svelte делегують йому.

- **Pros**:
  - Чіткий separation of concerns
  - Один файл = один SSOT для всього SMC state
  - Легше тестувати ізольовано
- **Cons**:
  - Новий файл = новий модуль у dependency graph
  - Потрібно рефакторити App.svelte caching layer (великий blast radius)
  - Over-engineering для 6 дефектів — по суті переписування state layer
  - Svelte 5 runes/stores інтеграція ускладнюється з зовнішнім class
- **Blast radius**: Новий файл + `smcStore.ts` + `ChartPane.svelte` + `App.svelte` + `frameRouter.ts` + тести
- **LOC estimate**: ~400+

### Вибір: Альтернатива A

**Обґрунтування**: Альтернатива A вирішує всі 6 підтверджених дефектів з мінімальним blast radius. Не створює нових файлів, не змінює архітектурну модель. Кожна зміна ізольована і може бути rollback per-file. Альтернатива B — over-engineering (Z4): створювати новий state manager для 6 точкових фіксів = YAGNI. Якщо state management стане складнішим (>3 нових metadata fields), тоді SmcStateManager буде доречний — але це окремий ADR.

---

## 4. Рішення (деталі)

### 4.1 Canvas Safe Zone Constants (D1)

Файл: `OverlayRenderer.ts`

```typescript
// Canvas safe zones — overlay елементи НЕ рендеряться в цих зонах
const CANVAS_SAFE_TOP_Y = 75;       // HUD + OHLCV tooltip clearance (px)
const CANVAS_SAFE_RIGHT_X = 850;    // Price axis clearance (px, залежить від canvas width)
const CANVAS_SAFE_BOTTOM_Y = 30;    // Time axis clearance (px)
```

Guard у кожному render method (`renderLevels`, `renderZones`, `renderLabels`):
```typescript
if (y < CANVAS_SAFE_TOP_Y) continue;  // skip — під HUD
```

### 4.2 pd_state null-clear fix (D3)

Файл: `smcStore.ts` — `applySmcDelta` оновлюється:

```typescript
// BEFORE (broken): pd_state: current.pd_state — delta ігнорується
// AFTER (fixed):
pd_state: delta.pd_state !== undefined ? delta.pd_state : current.pd_state,
zone_grades: delta.zone_grades ?? current.zone_grades,
bias_map: delta.bias_map ?? current.bias_map,
momentum_map: delta.momentum_map ?? current.momentum_map,
```

Файл: `ChartPane.svelte` — thick-delta merge block спрощується (делегує smcStore).

> **Implementation Note (2026-03-24)**: D6 вирішено повністю. ChartPane ADR-0042 P2 thick-delta block видалений повністю — `applySmcDelta` в `smcStore.ts` є єдиним SSOT для metadata merge (zone_grades, bias_map, momentum_map, pd_state). `smcStore.test.ts` TC-11..TC-15 адаптовані під новий SSOT. D3 fix підтверджений через новий TC-11: `pd_state: null` propagate як explicit clear. [VERIFIED changelog.jsonl:20260324-001]

### 4.3 boot_id reset callback (D5)

Файл: `frameRouter.ts` — додати callback:

```typescript
// При зміні boot_id — очистити UI кеші
if (frame.meta.boot_id !== lastBootId) {
    lastBootId = frame.meta.boot_id;
    lastSeq = frame.meta.seq;
    onBootIdChange?.();  // callback до App.svelte
    addUiWarning(...);
}
```

Файл: `App.svelte` — передати callback що очищає cachedNarrative, cachedShell, cachedBiasMap, cachedMomentumMap, cachedPdState.

### 4.4 filterMitigatedZones activation (D4)

Файл: `OverlayRenderer.ts` або `ChartPane.svelte` — додати виклик:

```typescript
const zones = config.hide_mitigated
    ? filterMitigatedZones(smcData.zones)
    : smcData.zones;
```

Config key: `smc.display.hide_mitigated` (default: `false`). Додати в `config.json`.

### 4.5 UI collision fixes (D7)

Файл: `ChartPane.svelte` — smc-panel: змінити `top: 36px` → `top: 48px` (clear HUD zone).
Файл: `App.svelte` — top-right-bar: змінити `top: 8px` → `top: 8px` (залишити), але `right: 64px` → `right: 140px` (зсунути лівіше від smc-panel).

Або: об'єднати в один контейнер з `flex` layout — один z-index, один positioning SSOT.

### 4.6 D2 Status — потребує додаткового RECON

RECON report зафіксував колізію `tv-legend` (y=34–62) з `shell-chips` (y=29–47). Верифікація показала:
- `tv-legend` знаходиться при `top: 52px` [VERIFIED OhlcvTooltip.svelte:82]
- Клас `shell-chips` НЕ знайдений у коді

**Рішення**: D2 потребує окремого RECON з chart-ux memory. Можливо клас перейменований або інлайнений. Не включаємо в P-slices до підтвердження.

---

## 5. P-Slices (план реалізації)

| Slice | Scope | LOC | Інваріант | Verify | Rollback |
|-------|-------|-----|-----------|--------|----------|
| P1 | Canvas safe zone constants + Y-guard в OverlayRenderer | ~40 | S0 (read-only), I0 (pure render) | Visual: labels не заходять під HUD. `npm run build` clean. | `git checkout -- ui_v4/src/lib/OverlayRenderer.ts` |
| P2 | applySmcDelta: centralize metadata merge (zone_grades, bias_map, momentum_map, pd_state) + simplify ChartPane thick-delta block | ~60 | S6 (wire format), I4 (update flow) | `smcStore.test.ts` — додати тест: delta з `pd_state: null` → pd_state = null. Existing tests pass. | `git checkout -- ui_v4/src/stores/smcStore.ts ui_v4/src/layout/ChartPane.svelte` |
| P3 | boot_id change → callback → clear UI caches in App.svelte | ~30 | I5 (degraded-but-loud) | Manual: restart server → UI warning shown + overlay cleared + full frame restores. | `git checkout -- ui_v4/src/app/frameRouter.ts ui_v4/src/App.svelte` |
| P4 | filterMitigatedZones activation + config key | ~25 | Clean Chart Doctrine | Manual: set `hide_mitigated: true` → mitigated zones hidden. `false` → visible. | `git checkout -- ui_v4/src/layout/ChartPane.svelte` + remove config key |
| P5 | smc-panel / top-right-bar z-index + positioning fix | ~15 | UI (no invariant) | Visual: both panels accessible, no click interception. | `git checkout -- ui_v4/src/layout/ChartPane.svelte ui_v4/src/App.svelte` |

**Порядок**: P1 → P2 → P3 → P4 → P5 (незалежні, можна паралелити P1+P5 та P2+P3)

---

## 6. Наслідки

### Що змінюється
- `OverlayRenderer.ts`: 3 нових константи + Y-guard в render methods
- `smcStore.ts`: applySmcDelta розширюється metadata fields
- `ChartPane.svelte`: thick-delta merge block спрощується, smc-panel repositioned
- `App.svelte`: boot_id reset callback, top-right-bar repositioned
- `frameRouter.ts`: boot_id callback mechanism
- `config.json`: новий ключ `smc.display.hide_mitigated`

### Що НЕ змінюється
- Wire format (types.ts / types.py) — без змін
- frameRouter frame validation logic — без змін
- smcStore applySmcFull — без змін
- Canvas render pipeline (RAF/doubleRAF) — без змін
- Svelte stores / reactivity model — без змін

### Нові інваріанти
- **UI-1**: Canvas overlay елементи рендеряться тільки при `y > CANVAS_SAFE_TOP_Y`
- **UI-2**: Metadata merge для thick-delta = тільки в `applySmcDelta` (SSOT)

### Вплив на performance / SLO
- Negligible: 1 порівняння `y < 75` per label = ~0.001ms per render cycle

### Нові gates / rails
- smcStore.test.ts: тест на `pd_state: null` propagation
- smcStore.test.ts: тест на metadata merge completeness

---

## 7. Rollback

| Slice | Rollback command | Ефект |
|-------|-----------------|-------|
| P1 | `git checkout -- ui_v4/src/lib/OverlayRenderer.ts` | Labels знову рендеряться під HUD (повернення D1) |
| P2 | `git checkout -- ui_v4/src/stores/smcStore.ts ui_v4/src/layout/ChartPane.svelte` | Thick-delta merge знову в 3 місцях (повернення D3+D6) |
| P3 | `git checkout -- ui_v4/src/app/frameRouter.ts ui_v4/src/App.svelte` | boot_id не очищає кеші (повернення D5) |
| P4 | `git checkout -- ui_v4/src/layout/ChartPane.svelte` + видалити config key | filterMitigated знову dead code (повернення D4) |
| P5 | `git checkout -- ui_v4/src/layout/ChartPane.svelte ui_v4/src/App.svelte` | z-index overlap повертається (повернення D7) |

Кожен slice незалежний — rollback одного не впливає на інші.

---

## 8. Open Questions

| # | Питання | Хто перевіряє | Дедлайн |
|---|---------|---------------|---------|
| Q1 | D2 (tv-legend / shell-chips) — клас `shell-chips` не знайдений. Перевірити chart-ux memory та з'ясувати реальну колізію. | R_CHART_UX | Before P5 |
| Q2 | `CANVAS_SAFE_RIGHT_X` — значення 850px hard-coded чи залежить від canvas width? Потрібен responsive calc? | R_CHART_UX | Before P1 |
| Q3 | `hide_mitigated` default — `false` (backward-compatible) чи `true` (Clean Chart preferred)? | R_SMC_CHIEF | Before P4 |
| Q4 | Чи потрібен event/signal від frameRouter при boot_id change, чи достатньо callback? | R_ARCHITECT | P3 design |

---

## 9. Cross-Role Plan

| Role | Task | When |
|------|------|------|
| R_ARCHITECT | ADR-0043 authored, challenge response | Now |
| R_CHART_UX | Resolve Q1 (D2 collision), Q2 (CANVAS_SAFE_RIGHT_X) | Before P1 |
| R_SMC_CHIEF | Resolve Q3 (hide_mitigated default) | Before P4 |
| R_PATCH_MASTER | Implement P1–P5 | After Accept |
| R_BUG_HUNTER | Review each P-slice for regression | After each slice |
| R_TRADER | Validate: labels visible, no phantom zones, clean chart | After P4 |
| R_DOC_KEEPER | Sync docs after all slices | After P5 |
| R_REJECTOR | Final gate before "done" | After all verified |
