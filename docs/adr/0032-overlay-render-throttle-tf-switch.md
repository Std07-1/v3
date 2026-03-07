# ADR-0032: Overlay Render Throttle + TF Switch Stability

- **Статус**: Proposed
- **Дата**: 2026-03-07
- **Автор**: Agent (R_CHART_UX + R_PATCH_MASTER)
- **Initiative**: ui_perf_p1_p5

## Контекст і проблема

Два пов'язані UX-дефекти в rendering pipeline:

### P1: Overlay "стрибає" під час scroll/zoom

`OverlayRenderer.bindTriggers()` підписується на `subscribeCrosshairMove → renderNow()` синхронно
([OverlayRenderer.ts:319](../../ui_v4/src/chart/overlay/OverlayRenderer.ts)).
Це порушує власне правило з ADR-0024 §18.7:

> "НІКОЛИ не рендерити синхронно з range/zoom triggers!"

Конфлікт: під час scroll/zoom, LWC одночасно emit'ить:

1. `subscribeVisibleLogicalRangeChange` → `scheduleDoubleRaf()` (правильно, 2 RAF для settle)
2. `subscribeCrosshairMove` → `renderNow()` (синхронно з **STALE Y координатами**)

Результат: overlay рендериться 3 рази за 2 кадри — перший раз з stale Y (видимий "стрибок").

**Додатково**: `interaction.ts:handleWheel()` обробляє wheel events синхронно коли `getEffectivePriceRange()` != null
(нормальний шлях). На високочастотних trackpad (120Hz+) це створює cascade range changes без RAF throttle.

### P5: Chart "їде вліво" при зміні TF

`viewCache.ts` зберігає `LogicalRange` (bar indices, `{from: 50, to: 150}`). При зміні TF кількість
барів різко змінюється — ті ж індекси означають зовсім інший часовий діапазон.

Наприклад: M5→H1 при LogicalRange `{50, 150}`:

- M5: показує ~8 годин (100 × 5хв)
- H1: показує ~100 годин (100 × 1год)

Коли кешу немає (перший перехід на TF) → `fitContent()` показує ВСЕ, zoom-out максимальний.

Додатково: `engine.setData()` викликає `scrollToRealTime()` який негайно перезаписується
`fitContent()` або `setVisibleLogicalRange()` — 3 range changes за один sync block.

## Розглянуті варіанти

### P1: Throttle crosshairMove

| Варіант | Плюси | Мінуси |
|---------|-------|--------|
| **A: Skip crosshair render during double-RAF** | 3 LOC, мінімальний ризик, зберігає zero-lag crosshair за нормальних умов | Crosshair tooltip може бути на 1-2 frame застарілим під час scroll |
| B: Повний RAF throttle для crosshairMove | Однорідний підхід | +1 frame lag для crosshair tooltip завжди, навіть без scroll |
| C: RequestIdleCallback для crosshairMove | Найменше навантаження | Непередбачуваний timing, може бути 50-100ms lag |

### P1-B: Wheel throttle

| Варіант | Плюси | Мінуси |
|---------|-------|--------|
| **A: Завжди через pendingWheel + RAF** | Уніфікований шлях, max 60Hz zoom | Мінімальний +16ms input lag |
| B: Залишити sync + debounce onPriceRangeChanged | Менше змін | Не вирішує cascade range changes |

### P5: TF switch centering

| Варіант | Плюси | Мінуси |
|---------|-------|--------|
| **A: Зберігати center_timestamp замість LogicalRange** | Стабільне cross-TF switching, інтуїтивне | Потребує пошук найближчого бара в новому серіалі |
| B: Завжди scrollToRealTime() | Простий, завжди показує "зараз" | Трейдер втрачає позицію при перегляді історії |
| C: Зберігати і LogicalRange і center_ms | Найкраще з обох | Складніший кеш |

## Рішення

### P1-FIX: Guard crosshairMove + RAF-only wheel

**Crosshair** (варіант A): якщо double-RAF вже scheduled (`rafId !== null`), crosshairMove пропускається.
Двійний RAF доставить правильні координати через 2 кадри. Без scroll/zoom — zero-lag як і раніше.

```typescript
// OverlayRenderer.bindTriggers()
this.chartApi.subscribeCrosshairMove(() => {
    if (this.rafId !== null) return;  // double-RAF pending → skip stale render
    this.renderNow();
});
```

**Wheel** (варіант A): `handleWheel` завжди буферизує в `pendingWheel` і проходить через RAF.

### P5-FIX: Time-center preserving TF switch

Замість `LogicalRange` зберігати `center_ms` (timestamp центру видимої області) + `bars_visible` (кількість видимих барів як міру zoom-рівня).

При перемиканні TF:

1. `center_ms` знаходить найближчий бар в новому серіалі
2. `bars_visible` визначає zoom level (з clamp для нового TF)
3. `setVisibleLogicalRange({from, to})` центрує на знайденому барі

Видалити `scrollToRealTime()` з `engine.setData()` — caller завжди перезаписує.

## Наслідки

### Файли

| Файл | Зміна |
|------|-------|
| `ui_v4/src/chart/overlay/OverlayRenderer.ts` | crosshairMove guard (+2 LOC), `destroy()` method (+15 LOC) |
| `ui_v4/src/chart/interaction.ts` | wheel RAF unification (~10 LOC refactor) |
| `ui_v4/src/stores/viewCache.ts` | center_ms + bars_visible замість LogicalRange (~15 LOC) |
| `ui_v4/src/chart/engine.ts` | Remove `scrollToRealTime()` з setData (-2 LOC) |
| `ui_v4/src/layout/ChartPane.svelte` | center-based restore (~15 LOC) |

### Гейти

- I4 (один update-потік) — не порушено (render pipeline, не data flow)
- ADR-0024 §18.7 — **підсилено** (crosshair guard усуває останній sync-render-during-zoom loophole)
- ADR-0009 — сумісно (DrawingsRenderer має окремий render pipeline, не торкається)

### Вплив на mobile

- RAF-only wheel: на touch devices wheel events рідкісні (pinch-zoom використовує gesture API, не wheel)
- CrosshairMove guard: на touch devices crosshairMove рідше fire'ить → мінімальний ефект
- TF switch centering: critical для mobile (маленький екран → втрата позиції = втрата контексту)

## P-Slices

| Slice | Що | LOC | Verify |
|-------|---|-----|--------|
| P1 | CrosshairMove guard в bindTriggers | ~3 | Scroll + zoom 10s з cursor на chart → no overlay jump |
| P2 | Wheel RAF unification | ~10 | Trackpad zoom 120Hz → smooth, no stutter |
| P3 | OverlayRenderer.destroy() + ChartPane cleanup | ~15 | onDestroy → no dangling subscriptions |
| P4 | viewCache → center_ms + engine.setData cleanup | ~30 | M5→H1→M5: та ж часова зона залишається по центру |

## Rollback

```bash
git checkout -- ui_v4/src/chart/ ui_v4/src/stores/viewCache.ts ui_v4/src/layout/ChartPane.svelte
cd ui_v4 && npm run build
```
