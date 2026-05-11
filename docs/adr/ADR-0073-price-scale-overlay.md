# ADR-0073: Price Scale Overlay — Власні canvas-label-и для Edge-to-Edge свічок

## Метадані

| Поле           | Значення                                                              |
| -------------- | --------------------------------------------------------------------- |
| ID             | ADR-0073                                                              |
| Статус         | **ACCEPTED** (rev 5 — option D minimal-style LWC native, 2026-05-12; rev 2/3/4 all retracted) |
| Дата           | 2026-05-11 (rev 2/3/4/5: 2026-05-12)                                  |
| Автори         | Станіслав + Opus 4.7 + Sonnet 4.6 review agents                       |
| Замінює        | —                                                                     |
| Розширює       | ADR-0024 §18.7 (правила overlay-рендеру LWC — double-RAF при range change); ADR-0066 (visual identity tokens); ADR-0072 (mobile canonical — позиція ☰ буде amend-нута). Виправлено rev 2: ADR-0042 викинуто (category error — це state-sync, не display-budget). |
| Зачіпає шари   | `ui_v4/src/chart/engine.ts` (приховуємо LWC right price scale), `ui_v4/src/chart/overlay/OverlayRenderer.ts` (3+1 нові методи рендеру + helper), `ui_v4/src/App.svelte` (оновлення позиції ☰ — strip зник) |

---

## Changelog rev 1 → rev 2 (2026-05-12)

**Тригер**: deep-review від 3 паралельних Sonnet 4.6 sub-agents (LWC API, OverlayRenderer infrastructure, ADR cross-refs) — критика збережена внизу файла як §"Audit log".

**BLOCKERS виправлено**:

- **B1** — `IPriceScaleApi.priceToCoordinate()` НЕ існує (LWC v5 `typings.d.ts:2160-2210`).
  Тільки `ISeriesApi.priceToCoordinate()` (`typings.d.ts:2264`). Pseudocode виправлено:
  всі виклики через `this.series.priceToCoordinate(price)`. Anti-drift checklist row оновлено.
- **B2** — `subscribeCrosshairMove(() => {...})` zero-arg lambda у OverlayRenderer.ts:357
  → `param.point.y` + price губляться. Spec оновлено: callback приймає
  `(param: MouseEventParams)`, зберігає `param.point?.y` та price; на
  `param.point === undefined` (crosshair залишає canvas) — clear stored state.

**HIGH виправлено**:

- **H1** — `_drawChip()` helper НЕ існує. Додано explicit signature у §"Технічний контракт".
- **H2** — `_formatPrice()` через `series.priceFormat.precision` ризиковано (candle series
  НЕ має explicit `priceFormat` у engine.ts:232-241). Замінено на
  `this.series.priceFormatter().format(price)` — symbol-aware, повертає `IPriceFormatter`
  (typings.d.ts:2257).
- **H3** — `ctx.fillStyle = 'var(--text-3)'` silently no-op (canvas НЕ читає CSS vars).
  Spec оновлено: hardcoded hex (`#6B6B80` для dark/black, `#4A4A55` для light — WCAG AA).
  Per-theme branch через існуючий `_isLightTheme` flag.

**LOW виправлено**:

- **L1** — drop "ADR-0042 (display budget patterns)" з §"Розширює"
  (ADR-0042 = Delta Frame State Synchronization, category error).
- **L2** — у §"Що ми втрачаємо": "ADR-0070 desktop TR corner" →
  **"ADR-0065 Phase 1 locks `right:64px`"** (per App.svelte:735 verbatim).
- **L3** — drop phantom ADR-0070 amendment з §"Cross-references" (ADR-0070 НЕ містить
  NarrativeSheet forward-ref; тільки ADR-0072 реально потребує update).
- **L4** — `priceLineVisible: false` ВЖЕ є у engine.ts:239. §"engine.ts" delta
  перефразовано: "залишити explicit `false`, retained — наш chip render-path вимагає
  щоб LWC default line не конфліктував з кастомним dashed connector (V1, не V1.5)".

**MEDIUM додано до §"Граничні випадки"**:

- **M1** — час-шкала rightmost label потенційно конфліктує з price-label стовпцем при
  `right:4px`. Прийнято стратегію: compute time-label width at render-time через
  `ctx.measureText` + dynamic padding; fallback `right: max(4px, timeLabel.width + 8px)`
  для bottom 30px.
- **M2** — `getChartAreaWidth()` (OverlayRenderer.ts:473-479) fallback `cssW - 65` clip
  коли scale прихований (`width()` повертає `0`/`null`). P2 audit step: переконатися що
  при `scale.width() === 0` повертається `cssW`, не `cssW - 65`.
- **M3** — `notifyPriceRangeChanged` (OverlayRenderer.ts:429-431) — додатковий trigger
  для Y-axis manual zoom (вже викликає `scheduleDoubleRaf`). Explicit note у §"Технічний
  контракт" для майбутніх maintainer-ів.

**A-gaps зі spec покрито**:

- **A3** — custom dashed price-line connector з last-value chip до lastBar X —
  **додано до V1** (~10 LOC), запобігає UX regression від `priceLineVisible: false`.
  Бонусна рекомендація власника.
- **A4** — touch crosshair vertical offset: `chipY = crosshair.y + (touchActive ? -20px : 0)`
  — chip не під пальцем.

**§"Відкриті питання" розв'язано** (rev 1 §Open Questions → rev 2 §"Розв'язані відповіді" нижче). Стара секція ще лишається для historical context, але всі 7 питань тепер мають locked answers у §Decision.

---

## Changelog rev 2 → rev 3 (2026-05-12)

**Тригер**: rev 2 implementation attempt (P2-P6 + hybrid pivot) розкрив дві
архітектурні правди:

1. **Pure overlay (`visible: false`) НЕ ламає LWC math** — `priceToCoordinate` /
   `coordinateToPrice` працюють і коли scale прихована (моє припущення в rev 2
   що ламає — було неправильним; реальний bug був у `dataByIndex(Math.floor(to))`
   що повертає null через `timeScale.rightOffset:3`).
2. **Y-zoom infrastructure ВЖЕ існує** у `interaction.ts` (state.manualRange +
   autoscaleProvider + applyWheelZoom + axisZoomState). Триггериться через
   `isPointerInPriceAxis()` що fallback-ує до `PRICE_AXIS_FALLBACK_WIDTH_PX = 60`
   коли LWC `priceScale.width()` повертає 0 (тобто коли scale прихована).
   **Тобто Y-zoom працює "з коробки" — ми просто визначаємо HIT ZONE.**

Owner-rejected rev 2 hybrid pivot ("ні підлаштовуємось") → переходимо до
**own-implementation V2** (повне володіння):

**Архітектурні зміни rev 3**:

- `rightPriceScale.visible: false` (повертається з rev 2 hybrid pivot)
- `PRICE_AXIS_FALLBACK_WIDTH_PX: 60 → 30` (per owner spec "правий 30px canvas").
  Y-zoom (wheel anchor + drag + pan) автоматично спрацьовує у цих 30px завдяки
  існуючому `isPointerInPriceAxis` fallback path.
- **Custom magnet** у `_drawCrosshairPriceLabel` (новий метод, rev 3 only): при
  `_magnetEnabled=true`, displayed price у chip snap-иться до найближчого з 4
  OHLC values (open / high / low / close) поточного видимого бара. Реалізація:
  ~15 LOC у OverlayRenderer. **НЕ використовує LWC `CrosshairMode.Magnet`** —
  той не працює без visible scale + має іншу логіку.
- `dataByIndex(Math.floor(logical.to))` → `dataByIndex(lastIndex, MismatchDirection.NearestLeft)`.
  Bug fix причини "chip без price" з rev 2. Виявлено перед rollback.
- `OVERLAY_STRIP_WIDTH_PX: 56 → 30` для синхронізації з Y-zoom hit zone.
  Labels still right-align to `cssW - 4` (можуть візуально виходити за 30px
  smartphone-формат для довгих BTC цін — це OK, labels float на overlay).

**Що rev 3 НЕ зачіпає** (rev 2 contracts лишаються):

- B1/B2/H1/H2/H3 fixes — всі залишаються (B1 series.priceToCoordinate, B2
  param capture, H1 _drawChip helper, H2 priceFormatter, H3 hardcoded hex)
- A3 dashed connector V1, A4 touch offset
- 2 toggles ("Crosshair price" + "Magnet") default OFF з localStorage
- Periodic labels density clamp(3,n,12)
- ☰ anchor через `--overlay-strip-width` CSS var (значення 30 тепер)

**LWC залишається**: candles render, grid render, volume render, crosshair lines
(vert/horz dashed), `priceToCoordinate` / `coordinateToPrice` math primitives.

**LWC більше НЕ використовується для**: магніт snap, Y-zoom drag, Y-zoom wheel
(вже не використовувався — `interaction.ts` володіє), price-scale labels,
last-value chip, price-line (всі ці — наш overlay).

---

## Changelog rev 3 → rev 4 (2026-05-12) — Hybrid Pivot BACK

**Тригер**: rev 3 own-implementation deploy attempt (build green локально) показав
що мої припущення про LWC behavior без емпіричної перевірки були неправильними.

**Підтверджені помилки rev 3**:

1. **Y-zoom drag НЕ працював** з `rightPriceScale.visible: false`. Причина:
   `interaction.ts:getPaneMetrics` коли LWC scale прихована — `priceScale.width()`
   повертає 0, але `paneSize().width` повертає **full canvas width** (LWC pane
   розширюється у місце прихованої scale). Result: `paneWidth = full canvas`,
   `axisLeft = paneWidth = right edge`, `axisRight = right edge` → `isPointerInPriceAxis`
   FALSE для будь-якої mouse позиції. Існуюча Y-zoom infrastructure ніколи не
   тригериться. `PRICE_AXIS_FALLBACK_WIDTH_PX` fallback працює тільки коли
   `paneSize.width === 0` (chart not ready) — після першого render мертвий.

2. **Periodic labels likely НЕ render** з `visible: false`. Моє rev 3 changelog
   стверджував "LWC math primitives працюють регардлес of scale visibility (чиста
   математика)" — це було **припущення без empіричної перевірки**. Owner
   тестував: "не видно нічого". Likely `getVisibleRange()` повертає null коли
   scale hidden → `_drawPriceLabels` early-returns.

**Pivot BACK to hybrid** (per owner direction "пробуємо B"):

- `rightPriceScale.visible: false` → `visible: true`
- Додано `textColor: 'rgba(0, 0, 0, 0)'` (LWC labels invisible)
- Додано `borderVisible: false` (без вертикальної лінії)
- Додано `ticksVisible: false` (без tick marks)
- Додано `minimumWidth: 30` (точне sync з `OVERLAY_STRIP_WIDTH_PX` + `PRICE_AXIS_FALLBACK_WIDTH_PX`)

**Тепер працює**:

- Y-zoom (wheel/drag/pan) через нормальний path `isPointerInPriceAxis`:
  `priceScaleWidth = 30 > 0` → axis detection coverage 30px справа
- `priceToCoordinate` / `coordinateToPrice` повертають валідні значення
- `getVisibleRange()` повертає валідний діапазон → periodic labels render

**Наш overlay лишається повним**: periodic labels, lastValue chip + dashed
connector, crosshair chip toggle, own magnet snap (4 OHLC), gradient backdrop
hint. Рендериться поверх invisible LWC strip.

**LWC magnet** (`CrosshairMode.Magnet`) — НЕ використовуємо. Наш magnet toggle
у overflow menu drive-ить own implementation у `_drawCrosshairPriceLabel`.
`engine.ts setCrosshairMagnet` метод лишається в коді як unused (можливо для
майбутнього use case), але ChartPane $effect calls `overlayRenderer.setMagnetEnabled`.

**Архітектурний lesson**: pure overlay vs LWC integration — НЕ можна реалізувати
без переписування LWC math primitives. Hybrid `visible:true + invisible text` —
це РЕАЛІСТИЧНА архітектура, не compromise. Pure approach виявився не "elegantna
purity", а "wishful thinking без empirical testing". rev 4 — чесне визнання.

---

## Changelog rev 4 → rev 5 (2026-05-12) — Option D Minimal-Style Final

**Тригер**: rev 4 hybrid attempt теж дав візуальні артефакти що owner не сприйняв
("свічок не видно у scale зоні" — LWC reservує 30px незалежно від textColor).
Спроба додати власний overlay (rev 2/3/4) на додачу до LWC викликала ланцюг
проблем (Y-zoom не працював, magnet не реактивував, crosshair toggle не
applyOptions-нувся). Owner direction: "повний rollback" → потім ідея "option D
minimal-style" (мінімально стилізувати LWC native, без overlay).

**Що ship-иться у rev 5** (final, 5 LOC NET від pre-ADR-0073 у engine.ts):

```typescript
layout: {
  ...,
  fontSize: isMobile ? 10 : 12,    // mobile compactness
},
crosshair: {
  ...,
  horzLine: {
    ...,
    labelVisible: false,             // crosshair price chip OFF (owner pref)
  },
},
rightPriceScale: {
  borderVisible: true,               // restored (false ламає crosshair chip rendering quirk)
  ticksVisible: false,
  autoScale: true,
  minimumWidth: 30,                  // компактна вузька strip (vs default 56)
  scaleMargins: ...,
},
```

**Що ВИДАЛЕНО** (rev 2/3/4 attempts retracted):

- OverlayRenderer custom price-label methods (`_drawPriceLabels`,
  `_drawLastValueChip`, `_drawCrosshairPriceLabel`, `_drawPriceStripBackdrop`)
- `_drawChip` helper, `_computeNiceInterval`, `_enumerateLabelPrices`
- Own magnet snap (4 OHLC) state + setter
- Crosshair chip toggle UI + state + localStorage + ChartPane $effect wires
- `OVERLAY_STRIP_WIDTH_PX` constant + `--overlay-strip-width` CSS var
- `setCrosshairLabelVisible` engine method (LWC `applyOptions` після init не
  реактивує `labelVisible` — quirk не вирішений; toggle мав би shake-ефект)
- `setCrosshairMagnet` engine method (LWC native не використовуємо)
- `MismatchDirection` import + `dataByIndex(NearestLeft)` (overlay немає →
  не потрібно)
- Gradient backdrop hint у правому 30px

**Що залишається з рев 2 amendments**:

- ADR-0072 NarrativeSheet forward-ref `0073→0074` (мобільна Архі-surface
  тепер ADR-0074, ще не написана). Не реверту — це окрема правда.
- README_DEV.md surface contracts row для ADR-0073 — зміст оновити пізніше
  з actual ship (5 LOC engine.ts diff, не 200+ overlay).
- `interaction.ts` `PRICE_AXIS_FALLBACK_WIDTH_PX` = 30 (sync з minimumWidth:30
  для кращого Y-zoom hit-zone fallback path).

**Console hygiene** (rev 5 окремий tweak): `interaction.ts` debug `dbg()`
тепер gated через `window.__priceAxisDebug` flag (default OFF). Раніше logs
друкувались always-on — owner повідомив spam у DevTools. Для майбутніх debug:
у DevTools `window.__priceAxisDebug = true` → reload → logs знову.

**Stash зберігаємо** (`git stash@{0}`) — повний код P2-P6 + toggles + rev 3/4
attempts. Якщо колись виявиться що power-user хоче власний overlay (е.g.,
адаптивні chip-и для multi-symbol comparison) — recover через `git stash pop`.

**Final architectural ledger**:

- Trader-facing: LWC native chip (теал/червоний), LWC native periodic labels
  (~10-12px depending on viewport), LWC native crosshair lines, LWC native Y-zoom
  drag/wheel, LWC native magnet (default Normal, no toggle UI).
- Лиш одна зміна chrome: `crosshair.horzLine.labelVisible: false` (chip що
  follow-ить курсор приховано — owner pref).
- Mobile: smaller font (10px) автоматично робить scale ~30-40% компактнішим.

---

## Quality Axes

- **Ambition target**: R3 — конкретний UX gain (~44px чарт-площі повертається на всіх viewport-ах = 10-12% на mobile portrait, 4-5% на desktop), визначений V1 scope (periodic price labels + last-value chip + crosshair price label), явна обробка edge-кейсів для theme/symbol/range/log-mode.
- **Maturity impact**: M3 → M4 — фіксує premium UI primitive (edge-to-edge свічки) з детермінованим контрактом рендеру; усуває "wasted strip" архітектурний борг який ADR-0070/0072 змушені були оминати.

---

## Контекст

### Архітектурна правда (виявлена 2026-05-11 під час owner exploration)

LWC chart canvas розділений на ДВІ обмежені зони, які користувач бачить як ОДНУ візуальну поверхню:

```
┌──────────────────────────────────────┬───────────────┐
│  CHART PANE                          │ PRICE SCALE   │
│  (LWC native render)                 │ STRIP (~44px) │
│  ─ candles ✓                         │ ─ candles ✗    │
│  ─ grid ✓                            │ ─ price labels │
│  ─ volume ✓                          │ ─ ticks       │
│  ─ LWC last-value line ✓             │ ─ last-value chip │
└──────────────────────────────────────┴───────────────┘
                                       ↑
                                       │ LWC сюди свічки не малює.
                                       │ Це і є "wasted area".
```

Тимчасом наш `OverlayRenderer.ts` малює в ОКРЕМИЙ canvas з `position:absolute; inset:0; z-index:10` — на повну ширину viewport, поверх LWC. Саме тому CHoCH/EQH/FVG markers можуть з'являтись ПОВЕРХ price scale strip-зони: вони НЕ обмежені LWC, вони — наш overlay.

**Питання власника яке вивело на цю істину**: "Чому ми бачимо наші лейбли а свічки не бачимо?"

Відповідь: candles — LWC-native, обмежені chart pane. Наш overlay — на повну ширину, але рендерить тільки structure markers (CHoCH, EQH, контури зон). Ніщо не малює candles в strip.

### Що ми втрачаємо не вирішивши це

- ADR-0065 Phase 1 locks `right:64px` desktop (per App.svelte:735 verbatim:
  "ADR-0065 Phase 1: right:64px — clears LWC price scale (~54px) + 10px gap") —
  ☰ + CommandRail форсовані на цей offset щоб обійти strip.
- ADR-0072 mobile: ☰ at right:44 впритул до стіни strip-у, з explicit re-measurement protocol.
- ~44px змарновано на КОЖНОМУ viewport — суттєво на mobile (10-12% горизонтальної площі).
- "Boxy" відчуття: чарт виглядає як widget з окремою axis-колонкою, замість "premium
  edge-to-edge price action".
- Майбутні символи з довшими цінами (BTC at $100k+) auto-розширять strip → ризик колізії з ☰.

### Контекст індустрії (чесно)

- TradingView Pro "max area" режим взагалі ховає price scale (читаєш ціни тільки через crosshair) — інший підхід
- Bookmap, деякі custom Binance dashboard-и: candles edge-to-edge з overlay labels — ближче до того що ми хочемо
- Native LWC v5 НЕ має built-in "overlay mode" для right price scale — потребує custom rendering

---

## Розглянуті альтернативи

### A. Лишити LWC scale як є (статус-кво)

**Плюси**: нуль роботи, нуль maintenance, LWC сам володіє всіма edge-кейсами.
**Мінуси**: ~44px змарновано назавжди, boxy feel, складність dodge у ADR-0070/0072 залишається.
**Вердикт**: відхилено — обговорювалось у ADR-0072 розмові; власник підтвердив "хочу спробувати" overlay підхід.

### B. Зменшити лише `minimumWidth` (Шлях 1 з раннього обговорення)

**Плюси**: ~10 LOC, low risk, LWC продовжує обробляти labels.
**Мінуси**: Маргінальний gain (~10px тільки на XAU, BTC лишається як є). ☰ retune потрібен. Dynamic sizing між символами — окрема складність (Static safe vs Dynamic ☰).
**Вердикт**: відхилено після аналізу — не варто amend ADR-0072 заради маргінального покращення.

### C. Custom canvas overlay для price labels (ЦЕ ADR)

**Плюси**: Повна ширина chart pane для свічок. Edge-to-edge premium feel. Перевикористовує існуючу OverlayRenderer інфраструктуру (priceToCoordinate, theme awareness, double-RAF throttling). Незалежний від LWC scale-width drift між символами.
**Мінуси**: ~100 LOC custom rendering. Maintenance cost: LWC v6 може зсунути `priceToCoordinate` API. Edge-кейси (crosshair price label, lastValue chip) тепер наші.
**Вердикт**: прийнято (це ADR).

### D. Custom DOM overlay (HTML div-и поверх canvas)

**Плюси**: Браузер обробляє text rendering (краще hinting, sub-pixel). Native CSS animation можлива.
**Мінуси**: 30+ DOM nodes per frame на scroll = layout thrash. Потрібна RAF координація з власним RAF LWC. CSS variable theme switching додає reflow cost.
**Вердикт**: відхилено на користь canvas — overlay canvas вже існує, перевикористати замість вводити DOM overlay layer.

### E. Замінити LWC власним chart engine

**Плюси**: Повний контроль.
**Мінуси**: 6+ місяців роботи, багаторічний maintenance, regression risk на КОЖНУ існуючу feature.
**Вердикт**: відхилено — out of scope, не виправдано.

---

## Рішення

Реалізувати **варіант C**: приховати LWC right price scale (`rightPriceScale.visible: false`), рендерити periodic price labels + last-value chip + crosshair price label через три нові методи в OverlayRenderer.ts.

### Візуальна специфікація (ЗАФІКСОВАНО — див. §"Заборонені патерни" перед зміною)

#### Periodic price labels

```
┌────────────────────────────────────────────────────┐
│                                              4720  │ ← right-aligned
│                                                    │
│                                              4715  │ ← ~6-8 labels
│                                                    │   на visible range
│   [candles заповнюють весь pane горизонтально]     │
│                                              4710  │
│                                                    │
│                                              4705  │
│                                              ───── │
│                                              4700  │ ← time scale нижче
└────────────────────────────────────────────────────┘
```

| Властивість | Значення | Причина |
|-------------|----------|---------|
| Позиція | правий край canvas, `right: 4px` від canvas edge | Muscle memory — те саме місце де були LWC labels |
| Вирівнювання | right-justified text | Візуально сканується вниз як чиста колонка |
| Шрифт | успадковує з LWC layout (`fontSize` mobile:10, desktop:12) | Узгоджується з time scale labels |
| Колір | `--text-3` (mid-grey #6b6b80 dark, інверсно на light) | Видно але не конкурує зі свічками |
| Фон | **transparent** (НЕ chip) | Edge-to-edge feel; text читається на chart bg |
| Text shadow | `0 1px 2px rgba(0,0,0,0.6)` на dark темах | Читабельність поверх candle bodies |
| Tick density | 6-8 labels на visible range (auto) | Стандартна LWC density поведінка |
| Tick rounding | округлення до "nice" intervals: 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000... | Узгоджується з LWC default (не показуємо 4717.32 як label) |
| Decimal places | з `series.priceFormat` (XAU: 2, BTC: 2, ETH: 2) | Symbol-aware |

**Алгоритм — "nice" interval picker**:

```
visible_price_range = visibleRange.high - visibleRange.low
target_label_count  = isMobile ? 5 : 8  // density
raw_interval        = visible_price_range / target_label_count
nice_interval       = round_to_nice(raw_interval)  // 1/2/5/10/20/50...
labels = [round_down(low / nice_interval) * nice_interval ... high, step nice_interval]
```

#### Last-value chip (поточна ціна)

```
                                              ┌──────┐
                                              │4715.93│ ← teal (price up) або red (price down)
                                              └──────┘
                                                ▲
                                                │ y position = chart.priceScale.priceToCoordinate(lastPrice)
```

| Властивість | Значення | Причина |
|-------------|----------|---------|
| Позиція | правий край, вертикально вирівняний з поточною ціною | Узгоджується з LWC default behavior |
| Фон | series upColor (#26a69a teal) якщо last bar bullish, downColor (#ef5350 red) якщо bearish | Візуальна підказка напрямку |
| Колір тексту | white на dark bg, derived з контрасту фону | WCAG AA |
| Padding | 3px горизонталь, 1px вертикаль | Компактний, чіткий chip |
| Border-radius | 2px | Subtle softening, не pill-shape |
| z-order | TOP overlay (малюється ОСТАННІМ) | Завжди видимий |

#### Crosshair price label (на hover)

```
                                              ┌──────┐
              [trader hovering chart] ─ ─ ─ ─│4712.45│ ← тільки на hover
                                              └──────┘
                                                ▲
                                                │ слідує за mouse Y
```

| Властивість | Значення | Причина |
|-------------|----------|---------|
| Позиція | правий край, вертикально вирівняний з crosshair Y | Узгоджується з LWC default |
| Фон | semi-transparent dark (`rgba(13,17,23,0.85)`) | Читабельний, м'який vs lastValue chip |
| Колір тексту | `--text-1` (white-ish #e6edf3) | Високий контраст |
| Видимий коли | LWC `crosshairMove` event має валідний `point.y` | Hover/touch only |
| Прихований коли | crosshair залишає canvas | Стандартна interaction |

### Технічний контракт — де що живе

#### `engine.ts` (існуючий файл, ~2 LOC зміна — L4 fix)

```typescript
rightPriceScale: {
  visible: false,           // ЗМІНЕНО: було true
  // borderVisible / ticksVisible / scaleMargins / minimumWidth — НЕАКТУАЛЬНО тепер
},

// Series:
this.series = this.chart.addSeries(CandlestickSeries, {
  ...,
  lastValueVisible: false,  // ЗМІНЕНО: було true (рендеримо власний chip)
  priceLineVisible: false,  // ВЖЕ false у engine.ts:239 — retained explicit (не "implicit-true")
                            // bo наш кастомний dashed connector (V1, додано per A3) має бути
                            // SOLE owner правої price-line; LWC default not конфліктує.
});
```

`scaleMargins` ЛИШАЄТЬСЯ для контролю вертикальної позиції свічок (top/bottom padding в pane).
Volume series (priceScaleId: '') незачеплений — окремий overlay scale.

#### `OverlayRenderer.ts` (існуючий файл, ~100 LOC нового коду — rev 2 expanded)

**4 нові private методи + 1 helper**, викликаються з існуючого `render()` pipeline.

Імпорт типів (rev 2): додати `MouseEventParams` зі `lightweight-charts` для B2 fix.

```typescript
// ───────────────────────────────────────────────────────────────────────
// Helper (H1 — explicit signature)
// ───────────────────────────────────────────────────────────────────────
private _drawChip(
  ctx: CanvasRenderingContext2D,
  text: string,
  rightX: number,            // абсолютна X-координата правого краю chip-у
  y: number,                 // вертикальний центр
  bg: string | null,         // null = без background (для periodic labels)
  fg: string,                // foreground (hex hardcoded, НЕ CSS var)
  opts?: { padX?: number; padY?: number; radius?: number }
): void {
  // Pattern взято з OverlayRenderer.ts:1015-1036 / 1115-1128 inline pills.
  // padX:3 padY:1 radius:2 defaults. text right-aligned to rightX.
}

// ───────────────────────────────────────────────────────────────────────
// Метод 1 — periodic price labels (B1 fix: priceToCoordinate ON SERIES)
// ───────────────────────────────────────────────────────────────────────
private _drawPriceLabels(ctx: CanvasRenderingContext2D): void {
  const scale = this.chartApi.priceScale('right');
  const range = scale.getVisibleRange();      // IRange<number> | null
  if (!range) return;                          // first-paint guard (A5)
  const interval = this._computeNiceInterval(range);
  const labels = this._enumerateLabelPrices(range, interval);
  // H2: symbol-aware formatting via series.priceFormatter()
  const fmt = this.series.priceFormatter();
  // H3: hardcoded hex per ADR-0066 token; per-theme branch.
  const color = this._isLightTheme ? '#4A4A55' : '#6B6B80';
  for (const price of labels) {
    // B1 fix: priceToCoordinate EXISTS ONLY ON ISeriesApi (typings.d.ts:2264).
    // IPriceScaleApi has NO такого методу (typings.d.ts:2160-2210).
    const y = this.series.priceToCoordinate(price);
    if (y === null) continue;
    // M1: padding для time-scale rightmost label collision avoidance.
    // rightX = cssW - 4 — but if bottom 30px (CANVAS_SAFE_BOTTOM_Y zone),
    // expand padding to clear time-label width measured at render-time.
    this._drawChip(ctx, fmt.format(price), this.cssW - 4, y, null, color);
  }
}

// ───────────────────────────────────────────────────────────────────────
// Метод 2 — last-value chip + dashed connector (V1 includes A3 connector)
// ───────────────────────────────────────────────────────────────────────
private _drawLastValueChip(
  ctx: CanvasRenderingContext2D,
  lastPrice: number,
  isBullish: boolean,
  lastBarX: number,            // x-координата правого боку last bar
): void {
  // B1 fix: series.priceToCoordinate, not scale.
  const y = this.series.priceToCoordinate(lastPrice);
  if (y === null) return;
  const bg = isBullish ? '#26a69a' : '#ef5350';
  const fmt = this.series.priceFormatter();
  // A3: dashed connector from lastBarX to chip — preserves "ось де ціна
  // перетинає графік" feedback that LWC's built-in price line gave us.
  ctx.save();
  ctx.strokeStyle = bg;
  ctx.globalAlpha = 0.45;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(lastBarX, y);
  ctx.lineTo(this.cssW - 4, y);
  ctx.stroke();
  ctx.restore();
  this._drawChip(ctx, fmt.format(lastPrice), this.cssW - 4, y, bg, '#ffffff');
}

// ───────────────────────────────────────────────────────────────────────
// Метод 3 — crosshair price chip (B2 fix: param captured)
// ───────────────────────────────────────────────────────────────────────
private _crosshairPrice: number | null = null;
private _crosshairY: number | null = null;
private _crosshairTouchActive: boolean = false;

private _drawCrosshairPriceLabel(ctx: CanvasRenderingContext2D): void {
  if (this._crosshairY === null || this._crosshairPrice === null) return;
  // A2: if crosshair Y is within chipHeight of last-value chip Y → suppress
  // last-value (crosshair wins). Handled in render() ordering.
  const fmt = this.series.priceFormatter();
  // A4: touch offset to dodge fingertip.
  const yAdj = this._crosshairY + (this._crosshairTouchActive ? -20 : 0);
  this._drawChip(
    ctx,
    fmt.format(this._crosshairPrice),
    this.cssW - 4,
    yAdj,
    'rgba(13,17,23,0.85)',
    '#E6EDF3',
  );
}
```

**B2 — `subscribeCrosshairMove` callback fix у setupSubscriptions()**:

```typescript
// OverlayRenderer.ts:357 — replace zero-arg lambda with capturing handler.
this.chartApi.subscribeCrosshairMove((param: MouseEventParams) => {
  if (!param.point) {
    this._crosshairPrice = null;
    this._crosshairY = null;
    this._crosshairTouchActive = false;
  } else {
    this._crosshairY = param.point.y;
    // Option A: coordinateToPrice (тип BarPrice | null)
    this._crosshairPrice = this.series.coordinateToPrice(param.point.y);
    // Option B (alternative): param.seriesData.get(this.series)?.value
    // A4 touch detection: sourceEvent.pointerType === 'touch' if available
    this._crosshairTouchActive = (param as any).sourceEvent?.pointerType === 'touch';
  }
  if (this.rafId !== null) return;
  this.renderNow();   // ADR-0024 §18.7: crosshair = sync render
});
```

**Wiring у render pipeline** (існуючий патерн):

- `render()` вже ітерує через шари (zones, levels, swings, fog тощо).
- Додати `_drawPriceLabels(ctx)` ПІСЛЯ всього іншого (щоб labels були зверху).
- Додати `_drawLastValueChip(ctx, ...)` ПІСЛЯ labels (завжди видимий, з dashed connector).
- Додати `_drawCrosshairPriceLabel(ctx)` САМИМ ОСТАННІМ (топовий шар; A2 collision = suppress last-value коли |Δy| < chipHeight).
- `subscribeCrosshairMove` оновлено (B2) → `renderNow()` triggers redraw on hover.
- Існуючий `scheduleDoubleRaf()` на `visibleLogicalRangeChange` тригерить redraw на zoom/pan → labels автоматично перепозиціонуються.
- M3 note: Y-axis manual zoom вже wired через `notifyPriceRangeChanged` (OverlayRenderer.ts:429-431) — це теж викликає `scheduleDoubleRaf()`. Майбутні maintainer-и НЕ ламають цей шлях.

#### `App.svelte` (існуючий — зміна позиції ☰, ~3 LOC)

ADR-0072 §"☰ geometry" локнуто на `right:44px` (впритул до LWC strip width 40px + 4 gap). Зі strip-ом прибраним:

```css
@media (max-width: 640px) {
  .top-right-bar {
    /* ADR-0073 §"Migration": LWC right price scale прихований через
       rightPriceScale.visible:false → немає strip-у який треба обходити.
       ☰ переїжджає в реальний viewport corner з малим breathing-ом. */
    right: calc(8px + var(--safe-right, 0px));   /* БУЛО: 44px */
    top: calc(2px + var(--safe-top, 0px));        /* без змін */
    ...
  }
}
@media (orientation: landscape) and (max-height: 500px) {
  .top-right-bar {
    right: calc(8px + var(--safe-right, 0px));    /* БУЛО: успадковано 64px */
    top: calc(1px + var(--safe-top, 0px));        /* без змін */
    padding: 6px 12px;                            /* без змін */
  }
}
```

Desktop теж виграє (більше не треба dodge 64px), але desktop overflow positioning у не-mobile media queries — лишимо для follow-up commit якщо desktop user попросить.

---

## Граничні випадки (locked behavior)

| Кейс | Поведінка |
|------|-----------|
| Theme switch (dark↔black↔light) | Labels re-render з новим кольором через `_isLightTheme` flag (існуючий патерн в OverlayRenderer) |
| Symbol switch (XAU→BTC→ETH) | `priceFormat` з нового series визначає кількість десяткових; labels регенеруються через існуючий range-change subscriber |
| Visible range change (zoom/pan) | `subscribeVisibleLogicalRangeChange` вже wired → `scheduleDoubleRaf()` → labels перепозиціонуються |
| Visible price range collapse (price freeze) | Один label по центру; dedupe якщо interval округлюється до 0 |
| Дуже narrow range (sub-cent рухи) | Більше десяткових через `priceFormat.precision` |
| Дуже wide range (multi-year zoom) | Грубіший `nice_interval` (1000, 5000) — алгоритм скейлиться natural |
| Log scale mode | Labels все ще через `priceToCoordinate` (LWC handles math); `nice_interval` алгоритм використовує log spacing |
| Percentage scale mode | Format labels як percent через `series.priceFormat` |
| LWC range не готовий (initial mount) | `getVisibleRange()` повертає null → skip render, retry на наступному RAF |
| Crosshair залишає canvas | Crosshair chip приховується через існуючий `crosshairMove` no-`point.y` шлях |
| Mobile portrait vs landscape | Density через `isMobile`: 5 labels portrait, 8 landscape (більше вертикальної кімнати). **Guard (rev 2)**: `clamp(3, computed, 12)` — захист від collapsed range (<3 unreadable) і extremely-wide zoom (>12 overcrowded). |
| Price line at last value (dashed connector) | (rev 2 A3 — V1, не V1.5) `priceLineVisible:false` у engine.ts ВЖЕ є. `_drawLastValueChip` сам малює dashed line `[4,4]` від lastBar X до chip Y (45% alpha bg color). Чому V1: без connector chip візуально disconnects від ціни на чарті — UX regression. ~10 LOC, не варто переносити. |
| Light theme text-shadow (rev 2 A1) | На dark/black: `text-shadow: 0 1px 2px rgba(0,0,0,0.6)` для legibility поверх candle bodies. На light: `text-shadow: none` (інверсна тінь на світлому bg не дає edge separation, тільки blur effect). Per-theme через `_isLightTheme` branch. |
| Crosshair chip vs last-value chip Y collision (rev 2 A2) | Crosshair chip — TOP z-priority (малюється LAST у render order). Last-value chip suppressed коли `abs(crosshairY - lastValueY) < chipHeight` (~14px). На non-collision стані обидва chip-и видимі. |
| First-paint flash (rev 2 A5) | `getVisibleRange()` повертає null під час LWC cold-mount (2-5 frames до того як layout settles після першого data batch). Documented behavior: chart має hidden LWC scale + no overlay labels у цьому вікні — користувач НЕ бачить right-edge price reference на ~50-80ms. Прийнято. V1.5 optional: 1-frame fade-in коли labels appear. |
| Mobile touch crosshair (rev 2 A4) | На `touch` source event: `chipY = crosshairY - 20px` — chip не під пальцем. Detect через `(param as any).sourceEvent?.pointerType === 'touch'`. На mouse source: zero offset. |
| Y-axis manual zoom (rev 2 M3) | Trigger `notifyPriceRangeChanged` (OverlayRenderer.ts:429-431) ВЖЕ wired до `scheduleDoubleRaf()`. Labels автоматично перепозиціонуються. Майбутні maintainer-и НЕ ламають цей шлях — це третій triggers (поряд з `crosshairMove` + `visibleLogicalRangeChange`). |
| Time-scale rightmost label collision (rev 2 M1) | `timeScale.rightOffset:3 × barSpacing:8` ≈ 24px від правого краю. Може накладатись на price-label стовпець при `right:4`. Mitigation: на bottom 30px (`CANVAS_SAFE_BOTTOM_Y`) — або skip price label у цьому Y-range, або компенсувати padding до `right: max(4, timeLabel.width + 8)` через `ctx.measureText`. P3 visual verify виявить що краще. |
| Accessibility (rev 2 A7 note) | Canvas-drawn labels invisible to screen readers. LWC native labels теж no a11y → parity, не regression. V2 enhancement: `aria-live` summary div поза chart з "поточна ціна {price}, видимий діапазон {low}-{high}". Out of scope V1. |

---

## Заборонені патерни (anti-drift checklist)

Майбутня зміна що пропонує будь-яке з цього → STOP, прочитай цю секцію:

| Пропозиція | Чому заборонено |
|------------|-----------------|
| Обчислити `priceToCoordinate` самостійно (наприклад linear interpolation `low..high`) | LWC володіє price-to-pixel mapping, включно з log/percentage modes. Use `this.series.priceToCoordinate(price)`. |
| Викликати `chart.priceScale('right').priceToCoordinate(price)` | (rev 2 B1) **МЕТОД НЕ ІСНУЄ В API.** `IPriceScaleApi` (typings.d.ts:2160-2210) має ТІЛЬКИ `applyOptions / options / width / setVisibleRange / getVisibleRange / setAutoScale`. `priceToCoordinate` живе ТІЛЬКИ на `ISeriesApi` (typings.d.ts:2264). Перевірено проти `lightweight-charts@5.1.0`. Завжди `this.series.priceToCoordinate(price)` — і коли scale прихований через `visible:false`, метод продовжує працювати (verified: pane height завжди обчислюється). |
| Викликати `subscribeCrosshairMove(() => ...)` zero-arg lambda | (rev 2 B2 fix) Param губиться, crosshair price label не отримає Y/price. **Завжди `(param: MouseEventParams) => {...}`** з `param.point?.y` + `series.coordinateToPrice(y)` або `param.seriesData.get(this.series)?.value`. На `param.point === undefined` — clear stored state. |
| Hardcode price format (наприклад `.toFixed(2)`) | (rev 2 H2 fix) Use `this.series.priceFormatter().format(price)` — повертає `IPriceFormatter`, symbol-aware. **НЕ** `series.priceFormat.precision` (candle series у engine.ts:232-241 НЕ має explicit `priceFormat`; LWC auto-detect не reliable). |
| `ctx.fillStyle = 'var(--text-3)'` чи будь-яка інша CSS var у canvas API | (rev 2 H3 fix) Canvas НЕ читає CSS vars — silently no-op. **Hardcode hex з ADR-0066 tokens.css**: `#6B6B80` для dark/black, `#4A4A55` для light (WCAG AA). Per-theme branch через `_isLightTheme`. |
| Рендерити labels на фіксованих Y intervals замість фіксованих PRICE intervals | Pan/zoom не пересуватиме labels з ціною → wrong. Завжди price-based. |
| Додати chip background до PERIODIC labels (тільки lastValue/crosshair мають chip-и) | Periodic labels — minimal-chrome. Chip-и зарезервовані для "active" data points (current price, hover). |
| Skip throttling — render every frame | Use existing `scheduleDoubleRaf()`. LWC range-change вже throttled. |
| Render labels ПІД ЧАС першого paint LWC (visible range ще нема) | Повертає null y → треба skip і retry, не render at y=0. (A5 first-paint guard) |
| Re-enable `rightPriceScale.visible: true` залишивши наш overlay | Дві шкали render = візуальний double-up. Hidden — це контракт. |
| Use DOM divs замість canvas | Performance regression на scroll. Canvas only. |
| Прибрати safe-area-Y guard для labels (CANVAS_SAFE_TOP/BOTTOM) | Labels можуть рендеритись під HUD або нижче time scale → візуальний бардак. Існуючі guards застосовуються. |
| Прибрати dashed connector з `_drawLastValueChip` (A3 V1 contract) | (rev 2 A3) Без connector chip "плаває" на правому краї, втрачає прив'язку до price-line у чарті. Це UX regression від поточної LWC priceLineVisible. ~10 LOC у V1 — НЕ переносимо у V1.5. |

---

## Слайси реалізації (rev 2 — оновлені після deep-review)

| # | Slice | Файли | LOC | Залежності |
|---|-------|-------|-----|------------|
| P1 | Це ADR (rev 2) + ADR-0072 amendment + index.md + README_DEV update | `docs/adr/ADR-0073-*.md`, `docs/adr/ADR-0072-*.md`, `docs/adr/index.md`, `ui_v4/README_DEV.md` | ~50 (docs) | none |
| P2 | engine.ts: `rightPriceScale.visible:false` + `lastValueVisible:false` (priceLineVisible вже false — retain). **Audit M2**: `getChartAreaWidth()` fallback при `width()===0` повертає `cssW` (не `cssW-65`) | `ui_v4/src/chart/engine.ts`, `ui_v4/src/chart/overlay/OverlayRenderer.ts` (audit only) | ~5 + audit | P1 accepted |
| P3 | OverlayRenderer.ts: `_drawPriceLabels` (periodic labels) + `_computeNiceInterval` + `_enumerateLabelPrices` + `_drawChip` helper (H1). B1 fix: `series.priceToCoordinate`. H2 fix: `series.priceFormatter().format()`. H3 fix: hardcoded hex per `_isLightTheme`. Density clamp(3,n,12). | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~70 | P2 |
| P3.5 | **Vitest cases (rev 2 A6)**: `_computeNiceInterval` (100-unit→10/20, 0.5-cent→0.05/0.1, 100k-unit→10k), `_enumerateLabelPrices` (clamp, range alignment) | `ui_v4/src/chart/overlay/__tests__/OverlayRenderer.priceLabels.spec.ts` (NEW) | ~50 | P3 |
| P4 | OverlayRenderer.ts: `_drawLastValueChip` (~25 LOC — chip render + bg/fg + Y position) + **dashed connector (rev 2 A3 V1)** з lastBar X до chip Y (~10 LOC — `ctx.setLineDash([4,4])` + stroke). teal/red bg per direction. Total ~35 LOC (rev 1 було ~25 без connector). | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | **~35 (25+10)** | P3 |
| P5 | OverlayRenderer.ts: `_drawCrosshairPriceLabel` + **B2 fix** у `subscribeCrosshairMove` (capture param). **A2 collision** suppress last-value коли `abs(Δy) < 14px`. **A4 touch offset** -20px. | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~30 | P4 |
| P6 | App.svelte: позиція ☰ (right:44→8 mobile + landscape) | `ui_v4/src/App.svelte` | ~5 | P2 |
| P7 | Visual verification across symbols/themes/viewports (21 manual smoke cases + unit tests pass) | manual + automated | — | P3.5, P4, P5, P6 |
| P8 | ADR-0073 status flip PROPOSED rev 2 → ACCEPTED | `docs/adr/ADR-0073-*.md`, `docs/adr/index.md` | ~2 | P7 |

**Sequential strict**: P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8.

**Параллелізм**: P3/P4/P5 могли б parallel-edit але краще верифікувати кожен ізольовано бо вони шарять `_drawChip` helper і theme branches.

**Загальний scope**: ~155 LOC коду + ~50 LOC docs = реалістично 1 робоча сесія.

---

## Тест-кейси (manual smoke per P7 + vitest per P3.5)

### Unit tests (rev 2 A6 — P3.5 slice, vitest)

`ui_v4/src/chart/overlay/__tests__/OverlayRenderer.priceLabels.spec.ts`:

- **`_computeNiceInterval`**:
  - range 100-unit → interval ∈ {10, 20}
  - range 0.5-cent → interval ∈ {0.05, 0.1}
  - range 100000-unit → interval ∈ {10000, 20000}
  - target_count clamp(3, n, 12) — collapsed range returns ≥3, wide range returns ≤12
- **`_enumerateLabelPrices`**:
  - аligns to round multiples (low=4717.32, interval=5 → first label 4720)
  - не виходить за межі visible range
  - стабільний порядок (low → high)
- **`series.priceFormatter()` integration mock**: формат для XAU 2dec, BTC 2dec, ETH 2dec

### Per-symbol verification (manual)

1. **XAU/USD M15** → labels at 4720, 4715, 4710, 4705... (5-unit interval)
2. **BTCUSDT M15** → labels at 85000, 84500, 84000... (500 or 1000-unit interval)
3. **ETHUSDT M15** → labels at 2330, 2320, 2310...
4. **XAG/USD M15** → labels at 35.50, 35.00, 34.50... (0.50-unit interval, 2 decimals)

### Per-zoom verification (pinch на mobile / wheel на desktop)

5. **Zoom in tight** (100-pip range) → labels at 4720.0, 4719.5, 4719.0...
6. **Zoom out wide** (10000-pip range) → labels at 5000, 4500, 4000...

### Per-theme verification (rev 2 H3 hardcoded hex)

7. **Dark theme** → labels у `#6B6B80` (mid-grey), читабельні поверх свічок, text-shadow `0 1px 2px rgba(0,0,0,0.6)`
8. **Black theme** → labels у тому ж `#6B6B80`, легкий contrast bump
9. **Light theme** → labels у `#4A4A55` (WCAG AA-compliant) на white bg, text-shadow: none

### Per-viewport verification

10. **Mobile portrait** (320-414px) → 5 labels видимі, density adjusted, clamp(3,5,12)
11. **Mobile landscape** (720-932px) → 8 labels видимі, last-value chip aligned right
12. **Tablet portrait** (768x1024) → 8 labels, ☰ inherits desktop position
13. **Desktop** (1920x1080) → 8 labels, edge-to-edge свічки, ☰ at desktop position

### Interaction verification (rev 2 B2/A2/A4)

14. **Mouse hover chart** → crosshair chip слідує за Y position з поточною ціною (no offset)
15. **Touch chart (mobile)** → crosshair chip Y зміщений на -20px (A4 touch offset)
16. **Crosshair near last-value** → last-value chip suppressed коли `abs(Δy) < 14px` (A2)
17. **Leave chart** → crosshair chip зникає, last-value chip знову видимий
18. **Switch symbol** → labels регенеруються одразу (within 1 frame)
19. **Switch TF** → labels регенеруються
20. **Live tick update** → last-value chip Y position + dashed connector оновлюються; color flips на зміну напрямку
21. **Y-axis manual zoom (drag)** → labels перепозиціонуються через `notifyPriceRangeChanged` → `scheduleDoubleRaf` (M3)

### Regression verification

22. **CHoCH/EQH/FVG markers** → все ще render correctly (нема double-z-order conflict)
23. **Drawings (trend lines, rects)** → все ще render над свічками, нижче price labels
24. **No-data state** → labels приховані (no range), нема JS errors
25. **First-paint (A5)** → під час cold mount labels відсутні ~50-80ms поки `getVisibleRange()` returns null; потім з'являються — НЕ y=0 flash

---

## Cross-references та forward-refs

### ADR-0072 amendment потрібен (це ADR ship-иться як PROPOSED → на acceptance, ми amend ADR-0072)

ADR-0072 §"Empirical measurements" lock на "price scale = 40px, ☰ at right:44" стає obsolete коли ADR-0073 ship-иться. Amendment:

> **Rev 2 (2026-05-NN, на ADR-0073 acceptance)**: §"Empirical measurements" superseded by ADR-0073 §"Migration". LWC right price scale hidden, ☰ переїжджає на right:8px. Re-measurement protocol більше не застосовується.

**До тих пір поки ADR-0073 НЕ ship-иться, ADR-0072 лишається authoritative.** Це ADR cross-references але не pre-amend.

### ADR-0072 NarrativeSheet forward-ref update (rev 2 L3 fix)

**ADR-0070 ВЗАГАЛІ НЕ містить NarrativeSheet/ADR-007[34] forward-ref** —
це було перевірено Sonnet 4.6 review agent проти origin/main:1e0b5af.
Початковий rev 1 план amend-нути ADR-0070 — phantom (нічого нема amend-нути).

Реально треба update тільки у:

- `ADR-0072-mobile-canonical-layout.md` §Notes / §"Future": ADR-0073 → ADR-0074
  (ADR-0072 має 3 forward-refs у §Notes, forbidden-patterns table, footer — всі shift на 0074)

Це update їде з commit цього ADR (одна логічна зміна: ADR-0073 з'являється,
NarrativeSheet bump-иться на 0074).

### docs/adr/index.md

Новий entry для ADR-0073 PROPOSED.

### ui_v4/README_DEV.md

Surface contracts table: додати ADR-0073 row "Price scale rendering — custom overlay (replaces LWC right strip when accepted)".

---

## Rollback (per slice)

1. **Тільки P8**: status PROPOSED ← ACCEPTED. Code лишається.
2. **Тільки P6**: revert ☰ position (назад до right:44). Code лишається. Visual: ☰ floats у chart area at right:44 без strip за ним (виглядає трохи disconnected, але functional).
3. **Тільки P5**: revert crosshair chip. Hover втрачає price feedback. Trader втрачає interactive price-read.
4. **Тільки P4**: revert last-value chip. Поточна ціна не видима на правому краї — тільки в ChartHud row 1.
5. **Тільки P3**: revert periodic labels. Catastrophic — взагалі немає price reference.
6. **P3+P4+P5+P6+P2 (повний)**: revert engine.ts (re-enable rightPriceScale.visible) + revert OverlayRenderer methods + revert ☰ position. Стан повертається до ADR-0072 реальності.
7. **Тільки P1**: revert docs. ADR-0073 marked SUPERSEDED з причиною.

**Emergency** (stuck PWA users на старому SW з broken overlay): bump SW_VERSION (per ADR-0071) → forces cache eviction → all clients picks up reverted code.

---

## Нотатки

### Чому НЕ incremental ship (наприклад periodic labels first, chip later)

LWC `rightPriceScale.visible: false` бінарний — коли false, last-value chip і price line ТАКОЖ зникають. Shipping P3 (periodic labels) без P4 (last-value chip) лишає trader без поточної ціни на правому краї. Confusing.

Strict order: P3 → P4 → P5 в одній сесії. P6 (☰ переїзд) може ship-итись трохи пізніше але ідеально та сама сесія для visual coherence.

### Чому mobile + desktop обидва, не mobile-only

Premium feel — universal. Desktop trader виграє від edge-to-edge теж. Splitting "mobile gets overlay, desktop keeps LWC" подвоює test surface і створює дві візуальні мови. Один контракт, один render path.

### Чому `text-shadow` замість background label

Background defeats the "edge-to-edge" purpose (ми б re-вводили strip-like appearance для кожного label). Text shadow дає достатньо edge separation коли label трапляється поверх candle wick, без приховування свічки.

### Performance budget

> **Note (rev 2 L1 follow-up)**: self-contained estimate, **NOT** extending
> external framework. ADR-0042 (Delta Frame State Synchronization) НЕ
> регулює render-perf budget — ця секція стоїть на своїх ногах. Не шукай
> ADR-0042 marker для цього бюджету.

- LWC redraws на власному RAF (~60 fps на idle, on-demand на interaction)
- Наш overlay redraws через `scheduleDoubleRaf` на range/crosshair events (НЕ кожен frame)
- Per-redraw cost (V1 з A3 connector): 8 labels × (text measure ~0.05ms та fillText ~0.1ms), плюс 1 chip ~0.3ms, плюс 1 dashed line stroke ~0.1ms — разом **~1.6ms**
- Well within 16ms RAF budget

### Майбутні V2 enhancements (НЕ в scope)

- Animated label transitions на range change (fade in/out)
- Multi-line labels для wide ranges (наприклад "4720 [+0.5%]")
- Custom price-line styling (dashed, gradient)
- Per-symbol label color (XAU gold tint, BTC orange tint)
- Floating price banner вгорі з більшим шрифтом на mobile portrait
- Touch-and-hold custom price marker (drag для read price)

V1 ship-ить чистий baseline. V2 ідеї живуть у backlog.

### Що це НЕ робить

- НЕ прибирає НИЖНЮ time scale — це окремо, керується через `timeScale.visible`. Time scale залишається (price/time — два anchor-и які trader потребує).
- НЕ змінює candle rendering — LWC продовжує володіти свічками, тільки їхній HORIZONTAL EXTENT змінюється (тепер full pane width).
- НЕ змінює zone/level/swing rendering — всі наші overlay primitives unaffected.
- НЕ вводить нових залежностей — pure additions до existing OverlayRenderer.

---

## Розв'язані відповіді на Open Questions (rev 2)

Усі 7 питань rev 1 відповіли власник + Sonnet 4.6 review. Locked answers:

| # | Питання | Locked answer (rev 2) | Обґрунтування |
|---|---------|-----------------------|---------------|
| 1 | Density portrait/landscape | **5/8 з `clamp(3, computed, 12)`** | 5/8 sensible default. Guard: <3 unreadable, >12 overcrowded. V2 → viewport-height-aware (`floor(visibleHeight/80px)`). |
| 2 | Color periodic labels | **`#6B6B80` dark/black; `#4A4A55` light (WCAG AA)** | Hardcoded hex (H3 — canvas не читає CSS vars). На light `#6B6B80` дає ~3:1 контраст — fail WCAG AA (4.5:1) → `#4A4A55` (~7:1). Per-theme через `_isLightTheme`. |
| 3 | Last-value chip bg | **Teal/red (LWC default)** | Industry-standard, muscle memory. ADR-0066 gold accent — для brand chrome, не price action. Revisit якщо власник попросить gold-everywhere identity-update. |
| 4 | Crosshair chip mobile | **ON з vertical offset -20px на touch** | Mobile trader потребує price feedback. Hover-only = асиметричний UX. Detect через `(param as any).sourceEvent?.pointerType === 'touch'`. |
| 5 | Time scale (transparent) | **Окрема ADR (out of scope)** | ADR-0073 правильно каже "НЕ прибирає time scale". Заходити сюди = scope creep. Окрема ADR-0075+ коли власник дозріє. |
| 6 | Light theme V1 | **Ship V1 з full light support** | `_isLightTheme` flag вже wired у OverlayRenderer. 3 theme-branches у 3 нових методах = ~12 LOC extra. Скіп → broken contrast у light → bug report → follow-up patch. Більше витрат ніж зробити одразу. |
| 7 | Desktop V1 | **V1 everywhere (mobile + tablet + desktop)** | Mobile-only ⇒ два візуальних режими ⇒ подвоюємо test surface ⇒ inconsistency. ADR §Notes сам це ловить — "premium feel = universal". |

**Бонус (rev 2 A3)**: dashed connector з last-value chip до lastBar X — **додано до V1**
(не V1.5). ~10 LOC. Без connector chip візуально "плаває" — UX regression від поточного
LWC `priceLineVisible`. Прийнято per owner suggestion після review.

---

## Audit log — Sonnet 4.6 deep review (2026-05-11/12, preserved verbatim)

Нижче — повна копія deep-review що тригернула rev 2. Збережено as-is для historical
reference: майбутні агенти бачать яким саме шляхом ADR пройшов через критику.

# ADR-0073 Price Scale Overlay — Deep Review

## Context

Owner asked for a rigorous critique of `docs/adr/ADR-0073-price-scale-overlay.md` (PROPOSED, committed on `origin/main`). The ADR proposes hiding LWC's right price scale (`rightPriceScale.visible: false`) and rendering periodic price labels + a last-value chip + a crosshair price label from `OverlayRenderer.ts` instead — to reclaim ~44px for edge-to-edge candles.

Verified against `origin/main @ 1e0b5af`, lightweight-charts `5.1.0`, by three Sonnet 4.6 sub-agents:
1. LWC v5 API surface (typings + runtime) + engine.ts state
2. OverlayRenderer + ChartPane infrastructure
3. Cross-references to ADR-0024 / 0042 / 0066 / 0070 / 0071 / 0072 + App.svelte BEFORE state

Initial Haiku-driven sweep was discarded — it was reading the local working tree (~35 commits behind origin/main) and falsely reported ADRs 0066/0070/0072 as nonexistent. All findings below come from `git show origin/main:<path>` reads.

---

## Verdict

**Two blockers, three high, three medium, four low.** The ADR is architecturally sound (visual spec is tight, slice plan is sensible, rollback semantics are real). But as written it **will not compile** because of one LWC API misuse, and **will not have working crosshair labels** because of one missing event-param capture. Both are surgical fixes in the spec, not architectural pivots.

Status recommendation: keep PROPOSED, issue **rev 2** addressing B1/B2/H1–H3 before flipping to ACCEPTED. M1–M3 and L1–L4 can be addressed during BUILD slices.

---

## Blockers — must fix in ADR rev 2 before any code is written

### B1 — `IPriceScaleApi.priceToCoordinate()` does not exist

**Severity: BLOCKER** (TypeScript compile error + runtime TypeError).

ADR pseudocode lines 211–215, 223, and anti-drift checklist line 296 call:

```ts
const scale = this.chartApi.priceScale('right');
const y = scale.priceToCoordinate(price);   // ← does not exist
```

Verified in `lightweight-charts/dist/typings.d.ts:2188` — `IPriceScaleApi` exposes only `applyOptions / options / width / setVisibleRange / getVisibleRange / setAutoScale`. `priceToCoordinate` exists only on `ISeriesApi:2264`.

**Fix:** replace all three occurrences with `this.series.priceToCoordinate(price)`. Verified that `series.priceToCoordinate` continues to work when `rightPriceScale.visible: false` (LWC source: scale always receives pane height; `_internal_priceToCoordinate` only fails on `height === 0`).

Also fix the anti-drift checklist row that reads "завжди викликати `chart.priceScale('right').priceToCoordinate(price)`" — same correction.

### B2 — `subscribeCrosshairMove` callback drops the param

**Severity: BLOCKER** for `_drawCrosshairPriceLabel`.

`OverlayRenderer.ts:357-360`:
```ts
this.chartApi.subscribeCrosshairMove(() => {
  if (this.rafId !== null) return;
  this.renderNow();
});
```

Zero-arg lambda — `param.point.y` and `param.seriesData` are never captured. ADR's `_drawCrosshairPriceLabel(ctx, y, price)` cannot get a Y or a price out of thin air.

**Fix in rev 2:** change the subscription to accept `param: MouseEventParams`, store `param.point?.y` and the hovered price (via `param.seriesData.get(this.series)` or `chart.priceScale('right').coordinateToPrice(param.point.y)` — pick one and document). On `param.point === undefined` (crosshair left the canvas), clear stored Y so the chip stops rendering.

Note this matches ADR-0024 §18.7 — crosshair triggers sync `renderNow()`, not double-RAF.

---

## High — resolve in spec, then implement in BUILD

### H1 — `_drawChip()` helper does not exist

ADR pseudocode calls `this._drawChip(ctx, text, y, bg, fg)` in two methods but the helper isn't defined. OverlayRenderer renders pills inline in `renderZones / renderLevels / renderSwings` (~3 sites). Closest analogue: BOS/CHoCH pill at `OverlayRenderer.ts:1115-1128`.

**Action:** ADR rev 2 should specify the helper signature explicitly:

```ts
private _drawChip(
  ctx: CanvasRenderingContext2D,
  text: string,
  rightX: number,    // canvas right edge minus margin
  y: number,         // vertical center
  bg: string | null, // null = no background (periodic labels)
  fg: string,
  opts?: { padX?: number; padY?: number; radius?: number; align?: 'right'|'center' }
): void;
```

Defaults: `padX:3, padY:1, radius:2, align:'right'`. The existing inline pill style at L1015-L1036 shows the alpha/fillRect/textBaseline pattern to match.

### H2 — Price formatter does not exist; ADR's `series.priceFormat.precision` path is fragile

No `_formatPrice` exists in OverlayRenderer or engine.ts. The candle series has no explicit `priceFormat` configured — LWC uses an auto-detected default. `series.priceFormat.precision` is therefore not a reliable read.

**Two options for rev 2:**
1. **Preferred** — use `series.priceFormatter()` (LWC v5 returns an `IPriceFormatter` with `.format(price)`). Symbol-aware automatically.
2. Add explicit `priceFormat: { type: 'price', precision, minMove }` to the candle series in engine.ts (~3 LOC), driven by per-symbol config; then read `series.options().priceFormat.precision`.

Option 1 is one line in the helper, zero changes to engine.ts. Pick (1) unless there's a reason to control precision from outside LWC.

### H3 — `var(--text-3)` does not work on canvas `fillStyle`

Verified: `--text-1/2/3` exist in `ui_v4/src/styles/tokens.css` (`--text-3: #6B6B80` on dark/black/light). But `ctx.fillStyle = 'var(--text-3)'` silently produces no color. ADR pseudocode/spec implies CSS-var usage; existing OverlayRenderer code uses hardcoded hex literals (`#141720`, `#c8cad0`).

**Fix in rev 2:** either hardcode the hex value (`#6B6B80`) with a `// ADR-0066 --text-3` comment, or read once via `getComputedStyle(this.canvas).getPropertyValue('--text-3').trim()` and cache on `setLightTheme()`. Add this as an anti-drift row: "Hardcode hex from ADR-0066 tokens; never `var(--*)` in `ctx.fillStyle`."

---

## Medium — handle during BUILD, document in rev 2

### M1 — Time-scale rightmost label collides with price-label column

`timeScale.rightOffset: 3 × barSpacing: 8 ≈ 24px` from right. The rightmost time tick label (e.g. "14:00") is centered on the bar X and spans roughly ±20px → its right edge sits at approximately `cssW - 4px`. ADR places price labels at `right: 4px`. Same pixel column.

The overlay canvas is at z-index 10 above LWC (z-index 1), so the price label visually wins — but the time label is still there underneath and may bleed at edges, especially at the bottom-right corner where the lowest periodic price label meets the rightmost time tick.

**Options:**
- Move price labels to `right: 70px` (clear of time-tick zone). Loses some edge-to-edge feel.
- Accept overlay; add an explicit canvas-clear band at the bottom 30px (already `CANVAS_SAFE_BOTTOM_Y = 30`) so the lowest periodic label isn't drawn there.
- Compute the rightmost time-label width from canvas measure at render-time and pad accordingly.

Pick one in rev 2 and add to the edge-cases table.

### M2 — `getChartAreaWidth()` fallback when scale is hidden

`OverlayRenderer.ts:473-479` computes available chart width as `cssW - priceScale('right').width()`, with `cssW - 65` as fallback when `.width()` returns null. When the scale is hidden, `width()` may return `0` (typical) — then labels intended at canvas right edge render correctly. But the 65px fallback path needs explicit verification once `visible: false` ships.

**Fix in rev 2:** add an integration test in the smoke list — "labels render at canvas right edge, not inset by 65px" — and audit `getChartAreaWidth()` for the `width() === 0` case during P2.

### M3 — Y-axis manual zoom not wired to label re-render

ADR-0024 §18.7 lists four triggers that need double-RAF; one is `notifyPriceRangeChanged()` (Y-axis manual zoom from `interaction.ts`). ADR-0073 inherits the existing `subscribeVisibleLogicalRangeChange` wiring, which covers time-driven range changes. But pure Y-only price-axis drag is wired via a different path (`notifyPriceRangeChanged()` at `OverlayRenderer.ts:429-431`) that already calls `scheduleDoubleRaf()` — so labels DO get re-rendered. ✓ Actually handled by existing infra; **rev 2 just needs to explicitly note** that `notifyPriceRangeChanged` is one of the triggers, so future maintainers don't break it.

---

## Low — documentation / citation fixes

### L1 — "Розширює ADR-0042 (display budget patterns)" is a category error

ADR-0042 is **Delta Frame State Synchronization** — about full/delta frame parity and zone-grade propagation. The phrase "DisplayBudget" appears there only in the context of zone *filtering* (grade C → filtered), not render-perf budgets. The "<2ms per redraw" claim in ADR-0073 §"Performance budget" is free-floating and does not extend ADR-0042's framework.

**Fix:** drop ADR-0042 from the "Розширює" list, or replace with a more honest citation (ADR-0024 §18.7 already covered).

### L2 — `right: 64px` desktop is ADR-0065 inheritance, not ADR-0070

Per `App.svelte:735` (verbatim comment): `"ADR-0065 Phase 1: right:64px — clears LWC price scale (~54px) + 10px gap"`. ADR-0070 governs *what* sits in the TR corner (NarrativePanel + CommandRail scope/data contracts), not the pixel value.

**Fix:** in ADR-0073 §"Що ми втрачаємо", change "ADR-0070 desktop TR corner: ☰ + CommandRail форсовані на right:64px" → "ADR-0065 Phase 1 locks `right:64px` to clear LWC price scale (~54px)". Update §"Cross-references" amendment plan accordingly.

### L3 — ADR-0070 amendment plan is phantom

ADR-0073 §"Cross-references" claims it must update ADR-0070 §Notes to renumber "ADR-0073 NarrativeSheet → ADR-0074". But ADR-0070 contains **no** forward-reference to NarrativeSheet or ADR-0073. Only ADR-0072 genuinely has those forward-refs (in three places: §Notes, forbidden-patterns table, and footer).

**Fix:** drop the ADR-0070 amendment from the plan. Keep the ADR-0072 amendment.

### L4 — `priceLineVisible` "було implicit-true" is wrong

`engine.ts:194-202` already sets `priceLineVisible: false` explicitly. The default in LWC `seriesOptionsDefaults` is indeed `true`, but the codebase already overrides it. ADR-0073's "ЗМІНЕНО: було implicit-true" framing is misleading — the change is a **no-op**.

**Fix:** in the §"engine.ts" delta table, remove `priceLineVisible` row or relabel as "already `false`, retained — needed when our chip ships so LWC's default line doesn't conflict with our custom dashed-line proposal (V1.5)".

---

## Things ADR-0073 got right (preserve in rev 2)

- ✓ `IPriceScaleApi.getVisibleRange(): IRange<number> | null` — field names `{from, to}` are correct.
- ✓ `scaleMargins.top/bottom` continue to control candle vertical positioning when scale is hidden (verified in LWC source — `_internal_internalHeight()` computed regardless of `visible`).
- ✓ `series.priceToCoordinate()` continues to return correct Y when scale is hidden (after B1 correction).
- ✓ ADR-0024 §18.7 double-RAF rule citation — accurate.
- ✓ `lastValueVisible: true → false` change is real and necessary (currently explicit `true` in engine.ts).
- ✓ Volume series unaffected — independent `priceScaleId: ''` overlay, separate scaleMargins.
- ✓ DrawingsRenderer (z-index 20) unaffected — uses `series.priceToCoordinate`, not direct scale.
- ✓ App.svelte BEFORE state matches:
  - Portrait `@media (max-width: 640px)`: `right: calc(44px + var(--safe-right, 0px))` ✓
  - Landscape: inherits desktop `right: 64px` ✓ (block doesn't override `right`)
- ✓ ADR-0071 `SW_VERSION` rollback mechanism is real (file scoped, documented bump procedure).
- ✓ ChartHud row 1 renders `lastPrice` — rollback claim valid.
- ✓ Overlay canvas `inset:0, z-index:10, pointer-events:none` — full-width coverage confirmed.
- ✓ Existing infrastructure (scheduleDoubleRaf, _isLightTheme, CANVAS_SAFE_*, subscribeVisibleLogicalRangeChange) all present and clean to plug into.
- ✓ Slice ordering rationale (P2 + P3 + P4 must ship in one session) is correct — partial ship breaks last-price visibility.
- ✓ Mobile + desktop both in V1 — single visual language, avoids doubled test surface.
- ✓ Performance budget (<2ms for 8 labels + 1 chip) is plausible (canvas text measure ~0.05ms × 8 + fillText ~0.1ms × 8 + chip ~0.3ms = ~1.5ms; well within RAF budget).

---

## Additional gaps ADR did not address

### A1 — Light-theme text-shadow inversion not specified

Spec table for periodic labels says `text-shadow: 0 1px 2px rgba(0,0,0,0.6)` "on dark themes" but doesn't define light-theme variant. On light theme a dark shadow on dark text is invisible; need either inverse `rgba(255,255,255,0.6)` or `text-shadow: none`. Open question 6 covers this implicitly but the spec table should be deterministic.

### A2 — Last-value chip / crosshair chip Y collision

When the user hovers exactly at the last bar's price, both chips render at the same Y. ADR doesn't specify ordering. **Recommended:** crosshair chip wins (z-priority: TOP); last-value chip suppressed when `|crosshair.y - lastValue.y| < chipHeight`. Add to edge-cases table.

### A3 — Last-value chip floats without connector to price line

ADR sets `priceLineVisible: false` and proposes the chip alone on the right edge. Without a dashed line from chip Y across the chart to the last bar, the chip visually disconnects from the price action. ADR §"Optional V1.5" mentions a custom dashed line but defers — strongly recommend including it in V1 (~10 LOC) to preserve the existing "ось де поточна ціна перетинає графік" visual cue. Otherwise this ships as a UX regression that has to be patched.

### A4 — Mobile crosshair chip behavior under finger

On touch-and-hold, crosshair fires at the finger's Y position. ADR's chip renders at right edge aligned with crosshair Y — likely under the user's finger and unreadable. Open question 4 surfaces this but offers no mitigation. **Recommended:** on `touchstart`, vertically offset chip by -20px from raw crosshair Y so it clears the typical fingertip. Detect touch vs mouse via `crosshair.event.sourceEvent` if LWC surfaces it, or via a `touchstart`/`mousemove` flag in ChartPane.

### A5 — First-paint flash before initial range arrives

When `getVisibleRange() === null`, the ADR says "skip render, retry on next RAF". On cold mount this can take 2–5 frames (LWC layout settles after first data batch). During this window the chart has hidden LWC scale and no overlay labels — user sees zero right-edge price reference. Acceptable cost but should be documented; optionally add a 1-frame fade-in once labels first appear.

### A6 — No automated tests

ADR §"Tests" has 21 manual smoke cases and zero unit tests. The `_computeNiceInterval` algorithm is pure math, ideal for vitest:
- 100-unit range → 10 or 20 step
- 0.5-cent range → 0.05 or 0.1 step
- 100000-unit range → 10000 step
Add ~10 unit-test cases for `_computeNiceInterval` and `_enumerateLabelPrices` in P3.

### A7 — Accessibility

Canvas-drawn labels are invisible to screen readers. LWC's native labels also have no a11y, so this is parity, not regression — but worth a one-line note. V2 could add an `aria-live` summary div with current price + visible range.

### A8 — `getChartAreaWidth()` audit for `width() === 0` path

Mentioned in M2 but worth restating: when LWC scale is hidden, `priceScale('right').width()` returns either `0` or `null` (verify during P2). The current fallback `cssW - 65` would clip labels 65px inside the right edge — exactly the opposite of what we want. Audit + adjust `getChartAreaWidth()` in P2.

---

## Answers to the 7 Open Questions

| # | Question | Recommended answer |
|---|---|---|
| 1 | Density 5 portrait / 8 landscape | **Default 5/8 for V1**, but add `clamp(3, computed, 12)` guard for collapsed and extreme-wide ranges. Move to viewport-height-aware (`floor(visibleHeight/80px)`) in V2. |
| 2 | Color `--text-3` (mid-grey #6B6B80) | **OK for all themes** — `--text-3` is `#6B6B80` on dark/black/light (verified). On light, contrast is borderline (~3.5:1) — acceptable for labels but if WCAG AA is required, use `#4A4A55` on light. Must hardcode the hex (canvas can't read CSS vars — see H3). |
| 3 | Last-value chip teal/red | **Teal/red OK for V1** — matches LWC/TradingView muscle memory. ADR-0066 gold accent applies to brand chrome, not price action signals. Revisit only if owner asks. |
| 4 | Crosshair chip on mobile | **ON with vertical offset** — `chipY = crosshairY + (isTouch ? -20px : 0)` so it's not under the fingertip. Hover-only on desktop only would be asymmetric — mobile traders need price-at-touch feedback too. |
| 5 | Time scale (transparent style) | **Separate ADR** — out of scope for 0073. Don't bundle. |
| 6 | Light theme V1 | **Ship with light support in V1.** `_isLightTheme` flag already wired in OverlayRenderer; 3 theme-branches in 3 new methods = ~12 LOC. Skipping creates a known-broken state that ships as a follow-up patch later. |
| 7 | Desktop V1 everywhere | **V1 everywhere (mobile + tablet + desktop).** ADR's own §Notes is correct: mobile-only doubles test surface and creates two visual languages. |

**Bonus recommendation (not in the 7):** include a custom dashed price line from last-value chip across the chart in V1 (gap A3 above). ~10 LOC, prevents UX regression from `priceLineVisible: false`.

---

## Critical files referenced

| Path (on origin/main) | Why it matters |
|---|---|
| `docs/adr/ADR-0073-price-scale-overlay.md` | The doc being reviewed (PROPOSED, identical to upload) |
| `ui_v4/src/chart/engine.ts:169-202` | `rightPriceScale` + candle series config — P2 target |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts:357-410` | Subscriptions + scheduleDoubleRaf — B2 fix lives here |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts:473-479` | `getChartAreaWidth()` — M2 audit |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts:514-537` | `render()` pipeline — insertion point for new draws |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts:1015-1036, 1115-1128` | Existing inline pill style — pattern for H1 `_drawChip` |
| `ui_v4/src/components/ChartPane.svelte` | Layer mount + z-index stack |
| `ui_v4/src/styles/tokens.css` | `--text-3 = #6B6B80` source — hardcode this hex |
| `ui_v4/src/App.svelte:731-735, 845-871` | Current `.top-right-bar` positioning (P6 BEFORE) |
| `ui_v4/src/layout/ChartHud.svelte:35, 299` | `lastPrice` rendered in row 1 — rollback claim source |
| `docs/adr/0024-smc-engine.md:1975-2043` | §18.7 double-RAF SSOT |
| `docs/adr/ADR-0066-visual-identity-system.md` | Visual tokens, IMPLEMENTED rev 5 |
| `docs/adr/ADR-0072-mobile-canonical-layout.md` | Real source of mobile 44px lock; only ADR that needs forward-ref amendment |
| `node_modules/lightweight-charts/dist/typings.d.ts:2160-2201, 2264` | `IPriceScaleApi` (no `priceToCoordinate`) vs `ISeriesApi` (has it) — B1 source |

---

## Verification

To validate this critique end-to-end:
1. Apply B1 fix in a scratch branch — confirm TypeScript compile passes when `_drawPriceLabels` uses `this.series.priceToCoordinate`. Expected: build green.
2. Apply B2 fix — log `param.point.y` and `param.seriesData.get(this.series)` to confirm crosshair data flows. Expected: hover prints valid Y + price.
3. Wire H3 — hardcode `#6B6B80` for label color; visual-check on all three themes. Expected: labels visible on dark/black; degraded but acceptable on light.
4. Manually run the 21 smoke cases in ADR §"Тест-кейси" after P3-P6 ship.
5. Add `_computeNiceInterval` vitest cases per A6 — expect all pass.
6. Visual-diff bottom-right corner before/after P2 to verify M1 collision behavior; decide on right: 4px vs right: 70px based on actual screenshots.
