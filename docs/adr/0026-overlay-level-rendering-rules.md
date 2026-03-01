# ADR-0026: Overlay Level Rendering Rules

- **Статус**: Implemented
- **Дата**: 2026-03-01
- **Автор**: AI-agent (Claude Opus 4.6)
- **Initiative**: `smc_engine_v1`

## Контекст і проблема

SMC Key Levels (ADR-0024b) рендеряться як горизонтальні лінії з підписами на canvas overlay (`OverlayRenderer.ts`). Під час ітеративної розробки виникли три групи UX-проблем:

1. **Зникаючі підписи**: collision-avoidance логіка (Y-only, потім bounding-box AABB) приховувала підписи для ліній, що візуально не перетинаються. Трейдер бачив лінію без пояснення — неприйнятно.

2. **Надлишкові підписи**: коли два рівні з різних TF збігаються за ціною (наприклад, PDH і Prev H4 Hi на одному рівні), з'являлись два окремі підписи один на одному — візуальний шум.

3. **Лінії на весь екран**: full-width lines заважали читати PA (price action). Рішення: короткі formation-attached лінії (120px) + sticky notch (20px) для off-screen формацій.

### Відкинуті підходи

| # | Підхід | Результат | Причина відмови |
|---|--------|-----------|-----------------|
| 1 | Full-width lines | Відхилено трейдером | Заважає читати PA |
| 2 | Y-only collision hide | Ховав підписи для ліній на різних X | Неправильна 1D модель |
| 3 | AABB bounding-box hide | Все ще ховав підписи при щільних рівнях | Ніколи не ховати — об'єднувати |
| 4 | Merge за MERGE_GAP_PX (12→6) | "PDH+Prev 4H Hi" — потворний текст, merge надмірно агресивний | Об'єднувались рівні що візуально НЕ збігаються |
| 5 | Merge з X-overlap check (60px) | Все одно зайвий merge через спільний X у sticky notch | Потрібна фізична ідентичність, не proximity |

## Рішення

### Правило: "Об'єднуй тільки те, що фізично лежить одне на одному"

**Принцип**: підписи НІКОЛИ не ховаються. Підписи об'єднуються (`"PDH | Prev H4 Hi"`) тоді і тільки тоді, коли лінії **фізично торкаються** — тобто їхні Y-координати різняться на ≤1px **і** їхні X-відрізки перетинаються.

### Деталі алгоритму `renderLevels()`

Алгоритм складається з 5 послідовних кроків:

#### Крок 1: Збір видимих рівнів

```
for кожного SmcLevel:
  y = priceToCoordinate(level.price)
  if y поза екраном (margin 10%) → skip
  dist = abs(price - midPrice) / rangeH   // для proximity alpha
  style = LEVEL_STYLES[kind] ?? _default
  
  if formation X visible → xStart = formation X, xEnd = xStart + LINE_PX (120px)
  else → sticky notch: xStart = chartW - NOTCH_PX (20px), xEnd = chartW
```

#### Крок 2: Priority sort + cap

```
D1_KINDS = { pdh, pdl, dh, dl }
sort: D1 kinds first, then by proximity to midPrice
visible = top MAX_LEVELS (12)
```

#### Крок 3: Рендер ліній

Усі лінії завжди малюються. Параметри:

- `alpha = style.alpha * proxAlpha` (proxAlpha: 0.4–1.0 залежно від dist)
- `lineWidth`: sticky → style.width × 0.5; formation → style.width × 0.7
- Dash pattern з `LEVEL_STYLES`

#### Крок 4: Групування (фізичне накладання)

```
LINE_Y_MERGE = 1px

for кожного visible item:
  for кожної існуючої групи:
    if |item.y - group.y| ≤ LINE_Y_MERGE AND x-ranges overlap:
      → merge into group (average Y, union X)
      break
  else:
    → new group
```

**Ключова умова merge**: `|Y₁ - Y₂| ≤ 1px` **AND** `xStart₁ < xEnd₂ && xEnd₁ > xStart₂`

Це гарантує:

- Лінії на різних цінах **не** об'єднуються (навіть якщо підписи б перекрились)
- Лінії на одній ціні, але в різних частинах екрану (formation зліва, sticky notch справа) **не** об'єднуються
- Тільки лінії, що **візуально невідрізнені** одна від одної → одна мітка

#### Крок 5: Рендер підписів

```
for кожної групи:
  text = uniqueLabels.join(' | ')
  color = primary item color (перший в групі = найвищий пріоритет)
  position = right of group xEnd, або left of xStart якщо не вміщується
  draw: background pill (#141720, alpha 0.55) + text
```

**Жоден підпис не ховається** — кожна група завжди має видиму мітку.

### X-позиціонування ліній

| Стан формації | Тип лінії | xStart | xEnd |
|---------------|-----------|--------|------|
| Формація видима на екрані | Formation-attached | `toX(t_ms)` | `xStart + 120px` |
| Формація зліва за екраном | Sticky notch | `chartW - 20px` | `chartW` |
| Формація без `t_ms` | Sticky notch | `chartW - 20px` | `chartW` |

### Per-kind стилізація (LEVEL_STYLES)

| TF шар | Кольорова група | Kinds |
|--------|-----------------|-------|
| D1 | Orange `#ff9800` / `#ffb74d` | `pdh`, `pdl`, `dh`, `dl` |
| H4 | Purple `#ab47bc` / `#ce93d8` | `p_h4_h`, `p_h4_l`, `h4_h`, `h4_l` |
| H1 | Blue `#42a5f5` / `#90caf9` | `p_h1_h`, `p_h1_l`, `h1_h`, `h1_l` |
| M30 | Teal `#26a69a` / `#80cbc4` | `p_m30_h`, `p_m30_l`, `m30_h`, `m30_l` |
| M15 | Cyan `#00bcd4` / `#80deea` | `p_m15_h`, `p_m15_l`, `m15_h`, `m15_l` |
| EQ | Red/Green `#e91e63` / `#4caf50` | `eq_highs`, `eq_lows` |

"Previous" kinds: dashed 6-3, товщина 1.5, alpha 0.7–0.85.
"Current" kinds: dashed 3-2, товщина 1.0, alpha 0.5–0.7.

### Zoom sync (від ADR-0024 §18.7)

Level rendering виконується всередині `renderFrame()`, який викликається:

- `visibleTimeRangeChange` → double-RAF (2 кадри для LWC price scale auto-fit)
- `visibleLogicalRangeChange` → double-RAF
- `patch()` / resize → single RAF
- Y-axis manual change → double-RAF callback від ChartPane

НІКОЛИ не рендерити синхронно з range/zoom triggers (stale Y).

### Toggle кнопки

4 toggle buttons у `ChartPane.svelte`: OB, FVG, SW, LVL.
LVL toggle контролює видимість levels. Per-kind active colors: OB=`#e67e22`, FVG=`#2ecc71`, SW=`#ef5350`, LVL=`#ff9800`.
Z-index: 36 (вищий за top-right-bar = 35).

## Інваріанти (Level Rendering)

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **L1** | Підписи ніколи не ховаються | Немає `continue`/skip для labels — кожна група отримує мітку |
| **L2** | Merge тільки при фізичному overlap | `|Y₁-Y₂| ≤ 1px AND X-ranges intersect` — жорстка умова |
| **L3** | Лінії НІКОЛИ не full-width | `LINE_PX = 120` або `NOTCH_PX = 20`, не `chartW` |
| **L4** | D1 kinds мають пріоритет | Sort: D1_KINDS first, then by distance to midPrice |
| **L5** | Max 12 видимих рівнів | `scored.slice(0, MAX_LEVELS)` після priority sort |
| **L6** | Per-kind кольори з LEVEL_STYLES | SSOT dict на початку файлу; fallback `_default` |

## Наслідки

- **Файли**: `ui_v4/src/chart/overlay/OverlayRenderer.ts` (~540 LOC)
- **Контракти**: `SmcLevel { id, price, kind, t_ms?, source_tf_s? }` (з `core/smc/types.py`)
- **Тести**: візуальна верифікація (canvas rendering не unit-testable без headless)
- **Performance**: grouping = O(N²) де N ≤ 12 → negligible

## Rollback

1. Видалити кроки 4–5 з `renderLevels()`
2. Замінити на простий label render без collision/merge
3. Build: `cd ui_v4 && npx vite build`

## Пов'язані ADR

- **ADR-0024**: SMC Engine Architecture (S0–S6, §18.7 render rule)
- **ADR-0024a**: SMC Self-Audit & Hardening (F1–F12)
- **ADR-0024b**: SMC Key Levels — Horizontal Anchors (per-kind styling, key_levels.py)
- **ADR-0007**: Drawing Tools Unblock (DrawingsRenderer — окремий canvas layer)
- **ADR-0009**: Drawing Sync Render Fix (Y-lag, double-RAF pattern origin)
