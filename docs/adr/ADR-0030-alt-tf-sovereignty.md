# ADR-0030-alt: TF Sovereignty — Cross-TF Projection Styling

- **Статус**: Proposed
- **Дата**: 2026-03-06
- **Автор**: System Architect
- **Mode**: PATCH (зміни тільки в OverlayRenderer.ts, ~60 LOC)
- **Initiative**: SMC-VIS-Φ2 (TF Sovereignty)
- **Залежності**: ADR-0028 v2 (Φ0 DONE), ADR-0029 (Φ1 DONE)
- **Scope**: `ui_v4/src/smc/OverlayRenderer.ts` — тільки UI. Zero backend changes.

---

## 0. Executive Summary

### Проблема

H4 FVG (87pt range, strength 77%) на M15 chart має ту ж fill color, fill alpha і gradient що й рідна M15 OB (3.5pt range). Великий червоний блок cross-TF проєкції домінує chart і краде увагу від рідних зон, де трейдер має діяти.

### Що є зараз [VERIFIED з RECON dump]

Wire payload містить **всю потрібну інформацію**:
- `zone.tf_s` = origin TF (наприклад, 14400 для H4 зони на M15 chart)
- `zone.context_layer` = "institutional" / "intraday" / "local"

Renderer вже використовує це для:
- Label prefix: "H4 FVG▼" [VERIFIED OverlayRenderer.ts:154]
- Border width: institutional=2.5px, intraday=1.5px, local=1px [VERIFIED OverlayRenderer.ts:145]
- Render order: institutional→1, intraday→2, local→3 [VERIFIED OverlayRenderer.ts:137]

Renderer **НЕ** використовує tf_s / context_layer для:
- Fill color — тільки від `kind` [VERIFIED OverlayRenderer.ts:456]
- Fill alpha / gradient — від `strength × proximity × dimMult × budgetOpacity` [VERIFIED OverlayRenderer.ts:596]
- Fog dissolve — однакове для всіх (ORIGIN_PX=30, MAX_BODY_PX=350) [VERIFIED]

### Рішення

Додати **projection multiplier** для fill alpha коли `zone.tf_s > viewer_tf_s`. Cross-TF проєкції стають фоновими (40% opacity), рідні зони — яскравими (100%). Dotted border для проєкцій. Zero backend changes.

---

## 1. Контекст: Evidence Ledger

| Факт | Evidence | Джерело |
|------|----------|---------|
| `zone.tf_s` доступний у UI | Wire payload поле tf_s | [VERIFIED types.py:77] |
| `zone.context_layer` доступний | "institutional"/"intraday"/"local" | [VERIFIED types.py:80] |
| Fill alpha не залежить від tf_s | Формула: strength × proximity × dimMult × budgetOpacity | [VERIFIED OverlayRenderer.ts:596] |
| Border width вже залежить від context_layer | institutional=2.5, intraday=1.5, local=1 | [VERIFIED OverlayRenderer.ts:145] |
| Render order вже залежить від context_layer | institutional=1 (back), local=3 (front) | [VERIFIED OverlayRenderer.ts:137] |
| viewer_tf_s доступний у renderer | Передається при render call | [ASSUMED — verify: check render() signature] |

---

## 2. Рішення

### 2.1 Projection Detection

Зона є **проєкцією** якщо `zone.tf_s > viewer_tf_s`:

```typescript
function isProjection(zone: SmcZone, viewerTfS: number): boolean {
  return zone.tf_s > viewerTfS;
}
```

Приклади:
- H4 FVG (tf_s=14400) на M15 chart (viewer=900) → **projection** ✓
- H1 OB (tf_s=3600) на M15 chart (viewer=900) → **projection** ✓
- M15 OB (tf_s=900) на M15 chart (viewer=900) → **native** (не projection)
- M15 OB (tf_s=900) на M5 chart (viewer=300) → **projection** ✓

### 2.2 Projection Styling (3 зміни в renderZones)

#### Зміна 1: Fill alpha multiplier

**Де**: OverlayRenderer.ts:596 — де обчислюється final alpha.

```typescript
// ПІСЛЯ існуючого обчислення alpha:
// let alpha = strength * proximityMult * dimMult * budgetOpacity;

// ADR-0030-alt: projection fade
const projectionMult = isProjection(zone, this.viewerTfS) ? 0.35 : 1.0;
alpha *= projectionMult;
```

**Ефект**: H4 FVG на M15 chart = 35% від нормальної opacity. Рідна M15 зона = 100%.

#### Зміна 2: Border style (solid → dotted для проєкцій)

**Де**: OverlayRenderer.ts:604 — де малюється border.

```typescript
// ПІСЛЯ існуючого border rendering:
if (isProjection(zone, this.viewerTfS)) {
  ctx.setLineDash([4, 3]);  // dotted pattern
} else {
  ctx.setLineDash([]);       // solid (default)
}
// ... existing strokeRect / stroke call ...
ctx.setLineDash([]);  // reset after
```

**Ефект**: cross-TF зони мають пунктирну рамку. Рідні — суцільну.

#### Зміна 3: Grade badge suppression для проєкцій

**Де**: OverlayRenderer.ts:696 — де рендериться grade badge.

```typescript
// В renderGradeBadge або перед його викликом:
if (isProjection(zone, this.viewerTfS)) {
  // Проєкції не показують grade badge — це "рамка", не "точка дії"
  return;
}
```

**Ефект**: grade badge тільки на рідних зонах. Проєкції = фон без badge.

### 2.3 Config (опціональний, для tuning)

```json
{
  "smc": {
    "display": {
      "projection_opacity_mult": 0.35,
      "projection_border_dash": [4, 3]
    }
  }
}
```

Або hardcode з коментарем `// ADR-0030-alt: projection styling` якщо config overhead не виправданий для 2 параметрів. Рішення за Copilot.

---

## 3. Що НЕ змінюється

- Backend: zero changes. Wire payload вже має tf_s і context_layer.
- `engine.py`: zero changes.
- `DisplayBudget.ts`: zero changes. Budget cap працює однаково для проєкцій і рідних.
- `confluence.py`: zero changes. Scoring не залежить від viewer TF.
- Render order: вже правильний (institutional=back, local=front) [VERIFIED].
- Border width: вже диференційований по context_layer [VERIFIED].

---

## 4. P-Slices

### P-Φ2-1: Projection detection + fill alpha (~20 LOC)

- `isProjection()` helper
- Alpha multiplier у формулі fill opacity
- **Gate**: H4 FVG на M15 chart = dim (35% opacity). Рідна M15 зона = normal.
- **Rollback**: remove multiplier line

### P-Φ2-2: Border style dotted + badge suppression (~15 LOC)

- `setLineDash` для проєкцій
- Badge skip для проєкцій
- **Gate**: cross-TF зони мають dotted border, без badge. Рідні = solid + badge.
- **Rollback**: remove setLineDash + remove badge skip

### Total: ~35 LOC production. 0 нових файлів. 0 backend changes.

---

## 5. Acceptance Criteria

| AC | Given | When | Then |
|----|-------|------|------|
| AC-1 | H4 FVG (tf_s=14400) on M15 chart (viewer=900) | Render | Fill alpha × 0.35, dotted border, no grade badge |
| AC-2 | M15 OB (tf_s=900) on M15 chart (viewer=900) | Render | Full opacity, solid border, grade badge visible |
| AC-3 | H1 OB (tf_s=3600) on M5 chart (viewer=300) | Render | Projection styling (dim, dotted) |
| AC-4 | M15 OB (tf_s=900) on H4 chart (viewer=14400) | Render | This is LTF→HTF — should NOT appear (Φ0 budget / server filter handles this). If it does appear — projection styling |
| AC-5 | Switch TF M15→H1 | Same zone | Zone goes from "projection" to "native" (or vice versa) — styling updates |
| AC-6 | Focus mode, 2 native + 2 projection zones | Visual | Native zones dominate visually, projections = background context |

---

## 6. Invariant Compliance

| Інваріант | Вплив |
|-----------|-------|
| I0 | ✅ UI-only change. No core/ or runtime/ imports |
| S6 | ✅ Wire format unchanged. tf_s already in payload |
| D0 (ADR-0028) | ✅ Styling = presentation layer. Server eligibility unchanged |

---

## 7. Rollback

Remove `isProjection()` function + remove alpha multiplier + remove setLineDash + remove badge skip. ~35 LOC deletion. System returns to uniform zone styling.

---

## 8. Очікуваний ефект

**До** (M15 chart): H4 FVG (87pt) = яскравий червоний блок, домінує chart. Рідна M15 OB (3.5pt) = загублена на тлі.

**Після** (M15 chart): H4 FVG = dim фон з dotted рамкою, "рамка/контекст". Рідна M15 OB = яскрава з solid рамкою і grade badge. Трейдер одразу бачить: "велика тьмяна зона = HTF контекст, маленька яскрава = тут діяти".

На H4 chart: всі зони рідні → без змін.
На M3 chart: великі блоки (H1/H4 проєкції) стають фоновими.
