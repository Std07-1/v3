# ADR-0032: Overlay Render Throttle + TF Switch Stability

- **Статус**: Implemented
- **Дата**: 2026-03-07 (proposed) → 2026-03-08 (implemented)
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

## Implementation Record (2026-03-08)

Всі 4 P-Slices реалізовані. Build: 170 modules, 303 KB, 0 errors.

### Slice P1: CrosshairMove guard — `OverlayRenderer.ts:bindTriggers()`

**Що**: `subscribeCrosshairMove()` callback тепер перевіряє `if (this.rafId !== null) return;` перед `renderNow()`.

**Чому**: LWC під час zoom/scroll одночасно emit'ить і `crosshairMove`, і `visibleRangeChange`. Другий коректно йде через double-RAF, але перший викликає `renderNow()` синхронно з **stale Y координатами** (LWC ще не завершив price scale auto-fit). Overlay рендериться 3 рази за 2 кадри — перший з неправильними Y → видимий "стрибок".

**Вплив на TF**: однаковий для всіх TF. На LTF (M1/M3/M5) ефект помітніший через:

- більшу кількість overlay елементів (M5 native compute з ADR-0032-companion config change)
- часті дрібні scroll/zoom при аналізі entry-зон
- менший діапазон цін → stale Y зміщення видиміше пропорційно

### Slice P2: Wheel RAF unification — `interaction.ts:handleWheel()`

**Що**: всі wheel events (Y-zoom на price axis + Shift-pan) завжди буферизуються в `pendingWheel` + обробляються через `requestAnimationFrame`. Зберігається resolved `mode: 'pan' | 'zoom'`.

**До**: нормальний шлях (коли `getEffectivePriceRange()` != null) виконував `applyWheelPan/Zoom` синхронно. На trackpad 120Hz+ це створювало cascade range changes: кожен wheel event → `applyManualRange()` → `requestPriceScaleSync()` → LWC layout pass. При 120 events/sec це 120 layout passes замість 60.

**Після**: max 60 layout passes/sec (1 per frame). Останній wheel event перезаписує `pendingWheel` → coalescing натуральний.

**Вплив на TF**:

| TF | Ефект | Чому |
|----|-------|------|
| M1/M3/M5 | **Критичний** | Трейдер часто zoom-in/out для пін-point entry → найбільше wheel events |
| M15/M30 | Помітний | Аналіз зон вимагає zoom до конкретного OB → trackpad cascade |
| H1/H4 | Мінімальний | Менше bar density → менше scroll |
| D1 | Незначний | Зазвичай fitContent() без ручного zoom Y |

### Slice P3: OverlayRenderer.destroy() — cleanup

**Що**: Новий метод `destroy()` — `cancelAnimationFrame(rafId)` + `tooltipEl.remove()`. Викликається з `ChartPane.onDestroy()`.

**Чому**: Без cleanup:

1. Pending RAF callback виконується після unmount → `this.chartApi` already disposed → silent exception → порожній overlay при наступному mount
2. Tooltip DOM element залишається orphaned в DOM → memory leak
3. На TF switch (ChartPane re-render) кожен switch додавав +1 orphan tooltip

**Вплив на TF**: пропорційний частоті TF switch. Трейдер типово перемикає M5→M15→H1→H4 кілька разів за сесію. Без destroy за 2 години = 20-40 orphan DOM елементів.

### Slice P4: viewCache center_ms — `viewCache.ts` + `engine.ts` + `ChartPane.svelte`

#### 4a. viewCache.ts — `ViewSnapshot {center_ms, bars_visible}` замість `LogicalRange`

**Що**: Кеш зберігає `center_ms` (timestamp центру видимої області) + `bars_visible` (кількість видимих барів як zoom level) замість `LogicalRange` (bar indices `{from, to}`).

**Чому**: `LogicalRange` — це **індекси в масиві барів**, не часові координати. При зміні TF масив барів кардинально інший:

| Перехід | LogicalRange {50, 150} | Часовий діапазон |
|---------|------------------------|------------------|
| M1 (tf=60s) | 100 барів | 1 година 40 хв |
| M3 (tf=180s) | 100 барів | 5 годин |
| M5 (tf=300s) | 100 барів | 8 годин 20 хв |
| M15 (tf=900s) | 100 барів | 25 годин |
| M30 (tf=1800s) | 100 барів | 50 годин |
| H1 (tf=3600s) | 100 барів | 100 годин (~4 дні) |
| H4 (tf=14400s) | 100 барів | 400 годин (~17 днів) |
| D1 (tf=86400s) | 100 барів | 100 днів |

Одні й ті ж індекси `{50, 150}` показують від 1.5 годин (M1) до 100 днів (D1). Трейдер дивився на конкретну **часову зону** (наприклад, London session 2026-03-07 09:00 UTC) — `center_ms` це зберігає. `LogicalRange` — ні.

#### 4b. engine.ts — видалення `scrollToRealTime()` з `setData()`

**Що**: `setData()` більше не викликає `scrollToRealTime()` всередині. Caller (ChartPane) завжди викликає один з: `setVisibleLogicalRange()`, `scrollToRealTime()`, або `fitContent()`.

**Чому**: Було 3 range changes за один sync block:

1. `setData()` → LWC `series.setData()` → implicit range change
2. `setData()` → `scrollToRealTime()` (inside)
3. ChartPane → `setVisibleLogicalRange()` або `fitContent()` (after setData)

Кожен range change trigger'ить `subscribeVisibleLogicalRangeChange` → `scheduleDoubleRaf()` → overlay render. Результат: 3 overlay renders за 1 TF switch замість 1.

#### 4c. ChartPane.svelte — center-based save + binary search restore

**Save** (перед switch):

```
centerIdx = round((logRange.from + logRange.to) / 2)
center_ms = candles[clamp(centerIdx)].t_ms
bars_visible = logRange.to - logRange.from
```

**Restore** (після setData):

1. Binary search в новому масиві барів для `center_ms` → `best` index
2. `half = max(10, bars_visible / 2)` — мінімум 10 барів видимих (UX guard)
3. `setVisibleLogicalRange({from: best - half, to: best + half})`

**Fallback** (перший візит на TF): `scrollToRealTime()` — показує останні бари. Це краще за `fitContent()` який zoom-out'ить максимально (показує все від першого бара). Трейдер зазвичай хоче бачити "зараз", а не місяць тому.

**Вплив на TF**:

| Перехід | До (LogicalRange) | Після (center_ms) |
|---------|-------------------|--------------------|
| M5 → H1 | Індекси 50-150 = 8 годин M5 → 100 годин H1. **Повний зсув.** | Та сама London session по центру |
| H1 → M15 | Індекси 50-150 = 100 годин H1 → 25 годин M15. **Zoom-in різкий.** | Та сама область, zoom адаптований |
| M5 → D1 | Ті ж індекси = 100 днів замість 8 годин | Той самий день по центру |
| H4 → M1 | Ті ж індекси = 1.7 годин замість 17 днів | Той самий момент ±margins |
| First visit (будь-який TF) | `fitContent()` — zoom максимально out | `scrollToRealTime()` — останні бари |

### Супутні зміни (M1/M3/M5 rendering, не ADR-0032 але пов'язано)

Під час цієї ж сесії були виявлені та виправлені проблеми рендерингу на LTF:

1. **BOS/CHoCH label offset** — `yOffBase = Math.max(3, Math.round(8 * mScale))` замість фіксованих ±10px. На M1/M5 mScale < 0.5 → offset був непропорційно великий, мітки "висіли в повітрі" далеко від свічок.

2. **Fractal offset** — `fOff = Math.max(4, ...)` замість `Math.max(8, ...)`. На M1/M5 при zoom-out фрактали зникали за межі candle bounding box.

3. **Zone label height threshold** — `h > 3` замість `h > 6`. На M1/M5 зони можуть бути 3-6px висотою (малий price range × маленький candle-per-pixel). Без зниження порогу — зони видимі але без міток, що порушує Z3 (ADR-0024c: зони без grade).

4. **M5 native SMC compute** — `config.json:smc.compute_tfs` розширено з `[900, 3600, 14400, 86400]` на `[300, 900, 3600, 14400, 86400]`. `engine.py` cross-TF mappings оновлені:
   - `_VIEWER_TO_BASE`: M1/M3/M5 → M5 (було → M15)
   - `_STRUCTURE_NEXT_TF`: M5 → +M15
   - `_FVG_DISPLAY_TFS`: M5 → M5+M15+H1
   - `_STRUCTURE_TFS`: +300
   - `_KEY_LEVEL_ALLOW[300]`: D1+H4+H1 (включно з current h1_h/h1_l)

   **Root cause**: M1/M3/M5 viewers показували тільки M15 projections — жодних native swings, BOS/CHoCH, OB поточного масштабу. `tf_overrides["300"]` налаштовані м'якше (swing_period=3, impulse ATR mult=0.8, gap ATR mult=0.1) щоб генерувати достатню кількість елементів на шумному LTF.

## Rollback

```bash
git checkout -- ui_v4/src/chart/ ui_v4/src/stores/viewCache.ts ui_v4/src/layout/ChartPane.svelte
cd ui_v4 && npm run build
```
