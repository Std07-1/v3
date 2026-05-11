# ADR-0073: Price Scale Overlay — Власні canvas-label-и для Edge-to-Edge свічок

## Метадані

| Поле           | Значення                                                              |
| -------------- | --------------------------------------------------------------------- |
| ID             | ADR-0073                                                              |
| Статус         | PROPOSED                                                              |
| Дата           | 2026-05-11                                                            |
| Автори         | Станіслав + Opus 4.7                                                  |
| Замінює        | —                                                                     |
| Розширює       | ADR-0024 §18.7 (правила overlay-рендеру LWC — double-RAF при range change); ADR-0042 (display budget patterns); ADR-0066 (visual identity tokens); ADR-0072 (mobile canonical — позиція ☰ буде amend-нута) |
| Зачіпає шари   | `ui_v4/src/chart/engine.ts` (приховуємо LWC right price scale), `ui_v4/src/chart/overlay/OverlayRenderer.ts` (3 нові методи рендеру), `ui_v4/src/App.svelte` (оновлення позиції ☰ — strip зник) |

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

- ADR-0070 desktop TR corner: ☰ + CommandRail форсовані на right:64px щоб обійти strip
- ADR-0072 mobile: ☰ at right:44 впритул до стіни strip-у, з explicit re-measurement protocol
- ~44px змарновано на КОЖНОМУ viewport — суттєво на mobile (10-12% горизонтальної площі)
- "Boxy" відчуття: чарт виглядає як widget з окремою axis-колонкою, замість "premium edge-to-edge price action"
- Майбутні символи з довшими цінами (BTC at $100k+) auto-розширять strip → ризик колізії з ☰

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

#### `engine.ts` (існуючий файл, ~3 LOC зміна)

```typescript
rightPriceScale: {
  visible: false,           // ЗМІНЕНО: було true
  // borderVisible / ticksVisible / scaleMargins / minimumWidth — НЕАКТУАЛЬНО тепер
},

// Series:
this.series = this.chart.addSeries(CandlestickSeries, {
  ...,
  lastValueVisible: false,  // ЗМІНЕНО: було true (рендеримо власний chip)
  priceLineVisible: false,  // ЗМІНЕНО: було implicit-true (рендеримо власну лінію якщо потрібно)
});
```

`scaleMargins` ЛИШАЄТЬСЯ для контролю вертикальної позиції свічок (top/bottom padding в pane).

#### `OverlayRenderer.ts` (існуючий файл, ~80 LOC нового коду)

Три нові private методи, викликаються з існуючого `render()` pipeline:

```typescript
// Метод 1: periodic labels
private _drawPriceLabels(ctx: CanvasRenderingContext2D): void {
  const scale = this.chartApi.priceScale('right');
  const range = scale.getVisibleRange();      // {from, to}
  if (!range) return;
  const interval = this._computeNiceInterval(range);
  const labels = this._enumerateLabelPrices(range, interval);
  for (const price of labels) {
    const y = scale.priceToCoordinate(price);
    if (y === null) continue;
    this._drawLabel(ctx, this._formatPrice(price), y);
  }
}

// Метод 2: last-value chip
private _drawLastValueChip(ctx: CanvasRenderingContext2D, lastPrice: number, isBullish: boolean): void {
  const y = this.chartApi.priceScale('right').priceToCoordinate(lastPrice);
  if (y === null) return;
  const bg = isBullish ? '#26a69a' : '#ef5350';
  this._drawChip(ctx, this._formatPrice(lastPrice), y, bg, '#ffffff');
}

// Метод 3: crosshair price chip (викликається з crosshairMove handler)
private _drawCrosshairPriceLabel(ctx: CanvasRenderingContext2D, y: number, price: number): void {
  this._drawChip(ctx, this._formatPrice(price), y, 'rgba(13,17,23,0.85)', '#e6edf3');
}
```

**Wiring у render pipeline** (існуючий патерн):

- `render()` вже ітерує через шари (zones, levels, swings, fog тощо)
- Додати `_drawPriceLabels(ctx)` ПІСЛЯ всього іншого (щоб labels були зверху)
- Додати `_drawLastValueChip(ctx, ...)` ПІСЛЯ labels (завжди видимий)
- `crosshairMove` handler вже існує — розширити щоб теж викликав `_drawCrosshairPriceLabel`
- Існуючий `scheduleDoubleRaf()` на `visibleLogicalRangeChange` тригерить redraw → labels автоматично перепозиціонуються

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
| Mobile portrait vs landscape | Density через `isMobile`: 5 labels portrait, 8 landscape (більше вертикальної кімнати) |
| Price line at last value (LWC горизонтальна dashed line) | Зараз ship-иться через LWC `series.priceLineVisible:true`. З нашим chip rendering — set `priceLineVisible:false` (ми володіємо). Optional V1.5: додати custom dashed line що малюється `_drawLastValueChip` для візуальної безперервності з chip y |

---

## Заборонені патерни (anti-drift checklist)

Майбутня зміна що пропонує будь-яке з цього → STOP, прочитай цю секцію:

| Пропозиція | Чому заборонено |
|------------|-----------------|
| Обчислити `priceToCoordinate` самостійно (наприклад linear interpolation `low..high`) | LWC володіє price-to-pixel mapping, включно з log/percentage modes. Завжди викликати `chart.priceScale('right').priceToCoordinate(price)`. |
| Hardcode price format (наприклад `.toFixed(2)`) | Use `series.priceFormat.precision` — symbol-aware (BTC=2, JPY=3 тощо). |
| Рендерити labels на фіксованих Y intervals замість фіксованих PRICE intervals | Pan/zoom не пересуватиме labels з ціною → wrong. Завжди price-based. |
| Додати chip background до PERIODIC labels (тільки lastValue/crosshair мають chip-и) | Periodic labels — minimal-chrome. Chip-и зарезервовані для "active" data points (current price, hover). |
| Skip throttling — render every frame | Use existing `scheduleDoubleRaf()`. LWC range-change вже throttled. |
| Render labels ПІД ЧАС першого paint LWC (visible range ще нема) | Повертає null y → треба skip і retry, не render at y=0. |
| Re-enable `rightPriceScale.visible: true` залишивши наш overlay | Дві шкали render = візуальний double-up. Hidden — це контракт. |
| Use DOM divs замість canvas | Performance regression на scroll. Canvas only. |
| Hardcode label color (наприклад `#9b9bb0`) | Use theme tokens через `_isLightTheme` гілку. |
| Прибрати safe-area-Y guard для labels (CANVAS_SAFE_TOP/BOTTOM) | Labels можуть рендеритись під HUD або нижче time scale → візуальний бардак. Існуючі guards застосовуються. |

---

## Слайси реалізації

| # | Slice | Файли | LOC | Залежності |
|---|-------|-------|-----|------------|
| P1 | Це ADR + ADR-0072 amendment + index.md + README_DEV update | `docs/adr/ADR-0073-*.md`, `docs/adr/ADR-0072-*.md`, `docs/adr/index.md`, `ui_v4/README_DEV.md` | ~50 (docs) | none |
| P2 | engine.ts: hide rightPriceScale + lastValueVisible | `ui_v4/src/chart/engine.ts` | ~5 | P1 accepted |
| P3 | OverlayRenderer.ts: `_drawPriceLabels` (periodic labels + nice-interval algorithm + formatter) | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~50 | P2 |
| P4 | OverlayRenderer.ts: `_drawLastValueChip` (current-price marker) | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~25 | P3 |
| P5 | OverlayRenderer.ts: `_drawCrosshairPriceLabel` (hover feedback) | `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~20 | P4 |
| P6 | App.svelte: оновлення позиції ☰ (right:44 → right:8 mobile + landscape) | `ui_v4/src/App.svelte` | ~5 | P2 |
| P7 | Visual verification + screenshot capture | manual | — | P3-P6 |
| P8 | ADR-0073 status flip PROPOSED → ACCEPTED | `docs/adr/ADR-0073-*.md`, `docs/adr/index.md` | ~2 | P7 |

**Sequential strict**: P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8.

**Параллелізм**: P3/P4/P5 могли б parallel-edit але краще верифікувати кожен ізольовано бо вони шарять `_drawChip` helper і theme branches.

**Загальний scope**: ~155 LOC коду + ~50 LOC docs = реалістично 1 робоча сесія.

---

## Тест-кейси (manual smoke per P7)

### Per-symbol verification

1. **XAU/USD M15** → labels at 4720, 4715, 4710, 4705... (5-unit interval)
2. **BTCUSDT M15** → labels at 85000, 84500, 84000... (500 or 1000-unit interval)
3. **ETHUSDT M15** → labels at 2330, 2320, 2310...
4. **XAG/USD M15** → labels at 35.50, 35.00, 34.50... (0.50-unit interval, 2 decimals)

### Per-zoom verification (pinch на mobile / wheel на desktop)

5. **Zoom in tight** (100-pip range) → labels at 4720.0, 4719.5, 4719.0...
6. **Zoom out wide** (10000-pip range) → labels at 5000, 4500, 4000...

### Per-theme verification

7. **Dark theme** → labels у `#6b6b80` (mid-grey), читабельні поверх свічок
8. **Black theme** → labels у тому ж grey, легкий contrast bump
9. **Light theme** → labels у dark grey на white bg, contrast OK

### Per-viewport verification

10. **Mobile portrait** (320-414px) → 5 labels видимі, density adjusted
11. **Mobile landscape** (720-932px) → 8 labels видимі, last-value chip aligned right
12. **Tablet portrait** (768x1024) → 8 labels, ☰ inherits desktop position
13. **Desktop** (1920x1080) → 8 labels, edge-to-edge свічки, ☰ at desktop position

### Interaction verification

14. **Hover/touch chart** → crosshair chip слідує за Y position з поточною ціною
15. **Leave chart** → crosshair chip зникає
16. **Switch symbol** → labels регенеруються одразу (within 1 frame)
17. **Switch TF** → labels регенеруються
18. **Live tick update** → last-value chip Y position оновлюється, color flips на зміну напрямку

### Regression verification

19. **CHoCH/EQH/FVG markers** → все ще render correctly (нема double-z-order conflict)
20. **Drawings (trend lines, rects)** → все ще render над свічками, нижче price labels
21. **No-data state** → labels приховані (no range), нема JS errors

---

## Cross-references та forward-refs

### ADR-0072 amendment потрібен (це ADR ship-иться як PROPOSED → на acceptance, ми amend ADR-0072)

ADR-0072 §"Empirical measurements" lock на "price scale = 40px, ☰ at right:44" стає obsolete коли ADR-0073 ship-иться. Amendment:

> **Rev 2 (2026-05-NN, на ADR-0073 acceptance)**: §"Empirical measurements" superseded by ADR-0073 §"Migration". LWC right price scale hidden, ☰ переїжджає на right:8px. Re-measurement protocol більше не застосовується.

**До тих пір поки ADR-0073 НЕ ship-иться, ADR-0072 лишається authoritative.** Це ADR cross-references але не pre-amend.

### ADR-0070 forward-ref update (NarrativeSheet)

ADR-0070 §Notes згадував "Future: ADR-0072 NarrativeSheet (mobile Архі-surface)" — вже зсунуто в ADR-0072 на ADR-0073. Тепер ADR-0073 зайнято ЦІЄЮ price-scale роботою, тому NarrativeSheet зсувається на **ADR-0074** (і лишається forward-only, ще не написаний).

Update потрібен у:
- `ADR-0070-tr-corner-canonical.md` §Notes / §"Future": ADR-0073 → ADR-0074
- `ADR-0072-mobile-canonical-layout.md` §Notes / §"Future": ADR-0073 → ADR-0074

Ці update-и їдуть з commit цього ADR (одна логічна зміна: ADR-0073 з'являється, NarrativeSheet bump-иться на 0074).

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

- LWC redraws на власному RAF (~60 fps на idle, on-demand на interaction)
- Наш overlay redraws через `scheduleDoubleRaf` на range/crosshair events (НЕ кожен frame)
- Per-redraw cost: 8 labels × (text measure ~0.05ms + fillText ~0.1ms) + 1 chip = <2ms
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

## Відкриті питання для огляду власника

Перед стартом P1 → P2 → ..., власник має підтвердити:

1. **Density**: 5 labels portrait / 8 landscape — OK чи інакше (3-10)?
2. **Color**: `--text-3` (mid-grey) для periodic labels — OK чи use `--text-2` (lighter)?
3. **Last-value chip**: keep teal/red bg (LWC default style) чи use neutral dark + colored text?
4. **Crosshair chip**: keep on для mobile touch (might feel cluttered) чи hover-only (= desktop)?
5. **Time scale**: keep visible as-is чи теж "transparent" via shorter font? (covered separately if yes — out of THIS ADR scope)
6. **Light theme**: ship з light support immediately (потребує separate label color tokens) чи land light support у follow-up?
7. **Desktop**: ship overlay everywhere V1, чи mobile-only V1 / desktop V1.5?

Відповіді власника на ці йдуть у §Decision перед status flips PROPOSED → ACCEPTED.
