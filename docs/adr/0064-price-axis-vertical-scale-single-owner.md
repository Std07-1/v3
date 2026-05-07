# ADR-0064: Price-Axis Vertical Scale — Single Owner (LWC vs autoscaleInfoProvider)

**initiative**: `chart_interaction_stability_v1`
**Дата**: 2026-05-06
**Статус**: ✅ **Implemented**
**Зачеплені файли**: `ui_v4/src/chart/engine.ts`, `ui_v4/src/chart/interaction.ts`
**Бандл після фіксу**: `index-CHIl9YXP.js`

---

## Quality Axes

- Ambition target: **R2** (точковий fix critical UI bug + повернення зниклої функції з власним handler)
- Maturity impact: **M3 → M3** (consolidates — усуває split-brain власника vertical scale, тримає інваріант "один власник = одна правда")

---

## Контекст / Симптоми

Користувач відтворював баг:

1. ЛКМ затиснута на price-axis → невелике вертикальне потягування → відпуск
2. Після цього chart "замерзав": горизонтальний pan мишею не працював
3. Wheel-on-axis після бага викликав **горизонтальний** zoom замість вертикального
4. Самостійно "відмерзав" через якийсь час або після dblclick reset

З логів `[PriceAxis] *` (always-on debug probes у `interaction.ts`) видно:

- До бага: `wheel.handle inAxis:true` спрацьовує нормально → vertical zoom через `state.manualRange`
- Під час drag по axis: `pointerdown inAxis:true` → LWC обробляє drag **внутрішньо** (на власному canvas), наш `pointermove` не бачить руху
- Після відпуску: pan по pane виставляє `panActive:true, manualRange:{...}`, але chart візуально не реагує

## Root cause

**Split-brain власників vertical price scale**:

- LWC config: `handleScale.axisPressedMouseMove.price = true` → LWC при drag по price-axis встановлює власний (internal) priceRange на `priceScale('right')`, обходячи autoScale
- Наш код: `series.applyOptions({ autoscaleInfoProvider })` повертає `state.manualRange` якщо він є, інакше — auto-range
- Кожен render frame LWC і наш provider **сваряться за range** (LWC виставляє своє, ми перетираємо своїм або повертаємо auto) → видимий "freeze"
- Wheel-on-axis після бага продовжує викликати наш `applyWheelZoom`, але результат миттєво перетирається LWC internal scale → виглядає ніби wheel не діє по вертикалі (а LWC сам бере горизонтальний scroll бо `mouseWheel:true`)

Інваріант I3 (**Final > Preview / NoMix**) у дусі ADR розширюється на UI: **для одного домену стану — один власник**. Тут vertical price scale мав двох власників.

## Розглянуті альтернативи

| # | Варіант | Плюси | Мінуси | Verdict |
|---|---|---|---|---|
| A | Вимкнути LWC `axisPressedMouseMove.price=false`, написати власний axis-drag handler (~30 LOC) | Один власник = `state.manualRange`. Eliminate bug class | Втрачаємо LWC defaults — треба підтримувати власну логіку | ✅ **Прийнято** |
| B | Залишити LWC native, після кожного drag-end синхронізувати наш `state.manualRange` з LWC priceScale.priceRange() | Менше нашого коду | Гонка під час drag (split-brain зберігається), складна синхронізація, baseline drift на FPS spikes | ❌ |
| C | Взагалі прибрати наш `autoscaleInfoProvider`, делегувати все LWC | Простіше | Ламає custom Y-zoom через wheel-on-axis, dblclick reset, `manualRange` для замірів і drawings (ADR-0008/0009) | ❌ |

## Рішення

### 1. `engine.ts` — вимкнути LWC native price-axis drag

```ts
handleScale: {
  // price=false: LWC native price-axis drag conflicts with our custom autoscaleInfoProvider
  // (state.manualRange) and freezes chart. We own vertical scale via wheel-on-axis +
  // pane drag → state.manualRange. See interaction.ts.
  axisPressedMouseMove: { time: true, price: false },
  axisDoubleClickReset: { time: true, price: true },
  mouseWheel: true,
  pinch: true,
},
```

`time: true` залишається — LWC продовжує власний time-axis drag (горизонтальний pan через axis).

### 2. `interaction.ts` — власний axis-drag vertical zoom (~30 LOC)

Новий стан `axisZoomState` в `setupPriceScaleInteractions()`:

```ts
const axisZoomState = { active: false, startY: 0, startRange: null, pointerId: null };
const AXIS_ZOOM_INTENSITY = 0.005; // per px
```

`handlePointerDown`: якщо `inAxis` — зберігаємо стартовий range, активуємо axisZoomState, `event.preventDefault()`.

`handlePointerMove`: якщо `axisZoomState.active` — обчислюємо scale через `Math.exp(dy * AXIS_ZOOM_INTENSITY)`, anchor = midpoint поточного діапазону:

```ts
const scale = Math.exp(dy * AXIS_ZOOM_INTENSITY);
const mid  = (startRange.min + startRange.max) / 2;
const half = (startRange.max - startRange.min) / 2;
applyManualRange({ min: mid - half * scale, max: mid + half * scale });
```

Drag down (dy>0) → `scale > 1` → range expand → zoom out
Drag up (dy<0) → `scale < 1` → range contract → zoom in

`handlePointerUp`: скидає `axisZoomState`, виходимо.

## Інваріанти / правила

- **One Owner Rule** для vertical price scale: тільки `state.manualRange` (через `autoscaleInfoProvider`) — SSOT
- LWC більше не керує price-axis interaction зовсім (drag вимкнено, dblclick reset залишено)
- Усі шляхи зміни Y-діапазону йдуть через `applyManualRange()`:
  1. wheel-on-axis (`applyWheelZoom`)
  2. shift+wheel in pane (`applyWheelPan`)
  3. drag in pane after activation 6px (`movePan`)
  4. **NEW**: drag on price-axis (`axisZoomState` handler)
  5. dblclick on axis → reset (`state.manualRange = null`)

## Verify

- ✅ Ctrl+Shift+R → drag down on price-axis → range expand (zoom out)
- ✅ Drag up → range contract (zoom in)
- ✅ Wheel-on-axis після drag працює (no freeze)
- ✅ Pan по pane мишею працює без блокування
- ✅ Dblclick on axis скидає до auto-range
- ✅ `[PriceAxis]` debug probes показують `axisZoom.start` при drag start, `axisZoom:true` при release

## Consequences

- + Eliminate класу багів split-brain Y-scale
- + Symmetric: всі Y-операції йдуть через один codepath
- + Інтенсивність zoom налаштовується одним константним числом (`AXIS_ZOOM_INTENSITY = 0.005`)
- − Втратили LWC defaults для axis drag (треба підтримувати ~30 LOC власного коду)
- − Anchor at midpoint (LWC native використовував позицію курсору) — якщо знадобиться anchor-at-cursor, додати в `axisZoomState.startAnchor`

## Rollback

1. У `engine.ts`: повернути `axisPressedMouseMove: { time: true, price: true }`
2. У `interaction.ts`: видалити блок `axisZoomState` + його гілки в `handlePointerDown/Move/Up`
3. Rebuild → новий хеш

## Debug probes

`[PriceAxis] *` console.log probes у `interaction.ts` залишені **завжди увімкнені** (не gated через localStorage/flag) — за домовленістю з власником "на майбутнє". Якщо потім вирішимо прибрати — змінити `dbg()` на no-op або gate через `__DEV__`.

## Зв'язки

- Зачіпає UI invariant: **один власник домену стану** (розширення I3 на UI state)
- Не порушує: I0 (dependency rule), I1 (UDS), I2 (time geometry), I4 (update flow), I5 (degraded-loud)
- Споріднені: ADR-0008 (drawing sync render fix — теж торкається `applyManualRange`), ADR-0009 indexed as drawing-sync (notation mismatch у тому файлі — `# ADR-0008` у `0009-*.md`, окремий cleanup)
