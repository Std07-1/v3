# ADR-0030-alt: TF Sovereignty — Cross-TF Projection Styling

- **Статус**: Proposed → **Accepted** (B-1 blocker + N-1/N-2 fixed)
- **Дата**: 2026-03-06
- **Reviewer**: R_BUG_HUNTER (APPROVED після B-1 fix)
- **Mode**: PATCH (~25 LOC production)
- **Scope**: `OverlayRenderer.ts` + `ChartPane.svelte` wiring. Zero backend.

### Errata (applied after Bug Hunter review)

| # | Defect | Sev | Fix |
|---|--------|-----|-----|
| B-1 | `viewerTfS` не існує в OverlayRenderer — ані поле, ані параметр | S0 | Додати `private viewerTfS: number` + `setViewerTfS(n)` + wiring у `ChartPane.svelte` при TF switch |
| N-1 | LOC суперечність (Mode=~60, P-Slices=~35) | S3 | Corrected: ~25 LOC production |
| N-2 | AC-4 тестує неможливий стан (LTF→HTF injection не існує) | S3 | AC-4 → "N/A by design — LTF zones never injected into HTF chart" |

---

## Рішення (коротко)

Зона є **проєкцією** якщо `zone.tf_s > viewerTfS`. Три візуальних зміни:

1. **Fill alpha × 0.35** — проєкція = dim фон
2. **Dotted border** — `setLineDash([4, 3])`
3. **Badge suppression** — grade badge тільки на рідних зонах

**Prerequisite** (B-1): OverlayRenderer потребує `viewerTfS`. Додається setter `setViewerTfS(n: number)` + виклик з `ChartPane.svelte` при кожному TF switch.

Wire payload вже має `zone.tf_s` (origin TF) [VERIFIED types.py:77]. Backend changes = zero.

---

## Acceptance Criteria

| AC | Then |
|----|------|
| AC-1 | H4 FVG on M15 chart → dim (×0.35), dotted border, no badge |
| AC-2 | M15 OB on M15 chart → full opacity, solid border, badge visible |
| AC-3 | H1 OB on M5 chart → projection styling |
| AC-4 | ~~LTF on HTF~~ → N/A by design (never injected) |
| AC-5 | TF switch → styling updates (projection↔native) |

---

# PATCH PLAN: ADR-0030-alt

> **Для**: Copilot
> **Scope**: 2 P-slices, ~25 LOC production

---

## ═══ P-Φ2-1: viewerTfS wiring + projection detection + alpha ═══

### Файл 1: `OverlayRenderer.ts`

**ДОДАТИ** поле і setter:

```typescript
// Поле (серед інших private полів):
private viewerTfS: number = 900;  // default M15

// Setter:
public setViewerTfS(tfS: number): void {
  this.viewerTfS = tfS;
}

// Helper:
private isProjection(zone: any): boolean {
  return (zone.tf_s || 0) > this.viewerTfS;
}
```

**ЗНАЙТИ**: місце де обчислюється fill alpha (~рядок 596, де `strength × proximity × dimMult × budgetOpacity`).

**ДОДАТИ** після існуючого alpha обчислення:

```typescript
// ADR-0030-alt: projection fade
const projMult = this.isProjection(zone) ? 0.35 : 1.0;
alpha *= projMult;
```

### Файл 2: `ChartPane.svelte` (або де TF switch відбувається)

**ЗНАЙТИ**: місце де `chartEngine.setTfS(tfS)` викликається при TF switch (~рядок 238 або аналог).

**ДОДАТИ** поруч:

```typescript
overlayRenderer.setViewerTfS(tfS);
```

**УВАГА**: перевір як `overlayRenderer` доступний у scope ChartPane. Може бути через store, prop, або direct reference.

### Gate: AC-1, AC-2

```
VERIFY:
1. M15 chart → H4 FVG = dim (помітно тьмяніша за рідну M15 зону)
2. M15 chart → M15 OB = full opacity (без змін)
3. Switch M15→H4 → та сама H4 FVG стає native (full opacity)
```

### Rollback
Remove viewerTfS field + setter + projMult line. Remove ChartPane wiring.

---

## ═══ P-Φ2-2: Dotted border + badge suppression ═══

### Файл: `OverlayRenderer.ts`

**ЗНАЙТИ**: border rendering (~рядок 604, де `strokeRect` або `stroke`).

**ДОДАТИ** перед stroke:

```typescript
// ADR-0030-alt: projection border
if (this.isProjection(zone)) {
  ctx.setLineDash([4, 3]);
}
```

**ДОДАТИ** після stroke:

```typescript
ctx.setLineDash([]);  // reset
```

**ЗНАЙТИ**: grade badge rendering (~рядок 696, `renderGradeBadge` або grade badge call).

**ДОДАТИ** guard:

```typescript
// ADR-0030-alt: no badge on projections
if (this.isProjection(zone)) return;  // або skip badge block
```

### Gate: AC-1, AC-3, AC-5

```
VERIFY:
1. Cross-TF зони = dotted border, без badge
2. Рідні зони = solid border, badge visible
3. TF switch updates styling correctly
```

### Rollback
Remove setLineDash lines + remove badge guard.

---

## Порядок

```
1. P-Φ2-1 → commit → verify (alpha dim works)
2. P-Φ2-2 → commit → verify (dotted border + no badge on projections)
3. Visual check: M15, M5, M3, H1, H4 — projections dim, natives bright
```
