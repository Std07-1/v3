# ADR-0008: Drawing Sync Render — Y-axis Lag + Draft Freeze Fix

**initiative**: `drawing_tools_v1` (stability)  
**Батьківський ADR**: [ADR-0007 Drawing Tools Unblock](0007-drawing-tools-unblock.md)  
**Пов'язаний**: [ADR-0008 Glass Toolbar](0008-glass-toolbar-light-theme.md)  
**Дата**: 2026-02-24  
**Статус**: ✅ **DONE**

---

БУЛО (баг):                           СТАЛО (фікс):
interaction.ts                        interaction.ts
  applyManualRange()                    applyManualRange()
  └→ requestPriceScaleSync()            └→ requestPriceScaleSync()
     └→ setVisibleLogicalRange(same)      └→ onPriceRangeChanged()
        └→ timeRangeChange                   └→ scheduleRender() [rAF]
           └→ renderSync()                      ↓
              └→ priceToCoordinate()       LWC rAF → layout update
                 └→ STALE Y! ❌             ↓
                                       Our rAF → forceRender()
                                         └→ priceToCoordinate()
                                            └→ CORRECT Y ✅

---

## Проблема

Два пов'язані баги в DrawingsRenderer:

### Баг 1: "Ефект доганяння" при вертикальному русі

При Y-pan (перетягування графіка вгору/вниз) та Y-zoom (колесо на price axis) **малювання відстають на 1 кадр** і "доганяють" ціну з видимою затримкою. При горизонтальному скролі все гладко.

**Відчуття**: об'єкти "пливуть" за графіком, як ніби прикріплені на гумці.

### Баг 2: "Застигання" draft під час малювання

Під час малювання trend/rect (після 1-го кліку, до 2-го) draft-об'єкт **може застигнути** — курсор рухається далі, а лінія/прямокутник залишається на місці. Через кілька секунд або рух назад — "відмерзає".

**Відчуття**: малювання "заїдає", ніби тормозить.

---

## DISCOVERY Findings

### FACTS (з path:line)

| # | Факт | Доказ |
|---|---|---|
| F1 | `subscribeVisibleTimeRangeChange` → `renderSync()` — слухає тільки **X-axis** (горизонтальні зміни) | `DrawingsRenderer.ts:147` |
| F2 | `wheel` listener на interactionEl (рядок 150) — має слухати Y-zoom, **але НЕ спрацьовує**: `interaction.ts:handleWheel` робить `stopImmediatePropagation()` на рядку 275, подія не дістається до DrawingsRenderer | `DrawingsRenderer.ts:150`, `interaction.ts:275` |
| F3 | Y-pan (`movePan`) → `applyManualRange` → `requestPriceScaleSync()` — хак через `setVisibleLogicalRange(same_value)`. DrawingsRenderer отримує renderSync через timeRangeChange, **але `priceToCoordinate()` повертає стару Y-координату** — LWC ще не оновив internal layout | `interaction.ts:167-177, 349-381` |
| F4 | Y-zoom (`applyWheelZoom`) → `applyManualRange` → та сама проблема: рендер малювань тригериться синхронно, але LWC ще працює з **попереднім** price range | `interaction.ts:225-245` |
| F5 | LWC v5 **НЕ має** `subscribePriceRangeChange` API — немає прямого callback для Y-axis змін | LWC docs, API surface |
| F6 | `updateDraft` (рядок 585-599): якщо `fromX(x)` або `fromY(y)` повертає `null`, draft **НЕ оновлюється** — ранній return без fallback | `DrawingsRenderer.ts:591-594` |
| F7 | `coordinateToTime(x)` повертає `null` коли coordinate за межами видимих барів — це нормально при русі курсору до країв екрана, але draft при цьому "застигає" | LWC API: `coordinateToTime()` → `null` if no data at coordinate |
| F8 | `coordinateToPrice(y)` повертає `null` рідше (тільки при повній відсутності серії), але `fromX` — частий джерело null | `DrawingsRenderer.ts:230-234, 236-240` |
| F9 | `handleDragMove` (body drag, рядок 616-671) має таку саму проблему з null — рядок 653-656, 661-664 | `DrawingsRenderer.ts:653-664` |

### Діагностична схема

```
┌──────────────────────────────────────────────────────────────┐
│  interaction.ts                                              │
│                                                              │
│  handleWheel → stopImmediatePropagation() ─── ╳ ──────────┐ │
│               applyWheelZoom                               │ │
│               └→ applyManualRange                          │ │
│                  └→ requestPriceScaleSync ─── (1) ──────┐  │ │
│                                                         │  │ │
│  movePan → applyManualRange                             │  │ │
│            └→ requestPriceScaleSync ──────── (1) ──────┤  │ │
│                                                         │  │ │
└────────────────────────────────────────────────────────┤──┤─┘
                                                         │  │
┌────────────────────────────────────────────────────────┤──┤─┐
│  DrawingsRenderer.ts                                   │  │ │
│                                                        │  │ │
│  (1) setVisibleLogicalRange(same) ──→ timeRangeChange  │  │ │
│      ──→ renderSync() ──→ forceRender()                │  │ │
│          ──→ priceToCoordinate(price) ──→ STALE Y! ◄───┘  │ │
│                                                            │ │
│  wheel listener ◄──── event NEVER arrives ◄────────────────┘ │
│                                                              │
│  Real layout update happens in NEXT rAF ──→ 1 frame lag     │
└──────────────────────────────────────────────────────────────┘
```

### Root Cause Analysis

**Баг 1 (Y-lag)**: `priceToCoordinate()` повертає координату на основі **поточного** internal state LWC. Коли ми робимо `setVisibleLogicalRange()` як хак для requestPriceScaleSync, LWC **планує** перемалювання на наступний rAF, але `priceToCoordinate()` ще працює зі **старим** price range. DrawingsRenderer рендерить синхронно, отримує стару Y → малювання на 1 фрейм відстають.

**Баг 2 (draft freeze)**: `coordinateToTime(x)` повертає `null` на координатах поза діапазоном видимих барів. Курсор може бути на краю графіка або в зоні правого відступу. `updateDraft` робить ранній return без оновлення → draft застигає.

---

## Рішення

### PATCH A: Notify + rAF render для Y-axis (~20 LOC)

**Підхід**: Замість спроби рендерити синхронно (коли priceToCoordinate ще стара), рендерити через **rAF після requestPriceScaleSync**. Це гарантує, що LWC оновив internal layout.

**Зміни:**

1. **`interaction.ts`**: додати callback `onPriceRangeChanged` в параметри `setupPriceScaleInteractions`. Викликати його в `applyManualRange` після `requestPriceScaleSync()`.

2. **`DrawingsRenderer.ts`**:
   - Додати публічний метод `notifyPriceRangeChanged()` — `scheduleRender()` (через rAF, що гарантує що LWC вже оновив layout).
   - Видалити `wheel` listener (рядок 150) — він ніколи не спрацьовує через `stopImmediatePropagation`.
   - Видалити `dblclick` listener (рядок 151) — він покривається через timeRangeChange.

3. **`ChartPane.svelte`**: передати `drawingsRenderer.notifyPriceRangeChanged` як callback у `setupPriceScaleInteractions`.

**Чому rAF а не sync**: LWC оновлює price layout в своєму рАФ. Якщо ми рендеримо sync до LWC, координати стальні. rAF гарантує черговість: LWC rAF → наш rAF → актуальні координати.

### PATCH B: Draft clamp при null координатах (~15 LOC)

**Підхід**: Якщо `fromX` або `fromY` повертає `null` під час:

- `updateDraft`: зберегти **останнє відоме значення** (fallback до `draft.points[1]`)
- `handleDragMove`: зберегти **поточну позицію** (не рухати точку)

**Зміни:**

1. **`DrawingsRenderer.ts:updateDraft`**: замість раннього return при null — fallback до попередніх координат draft.
2. **`DrawingsRenderer.ts:handleDragMove`**: при null нової координати — skip цю точку (не рухати).

---

## Файли та очікуваний обсяг

| Файл | Зміна | LOC |
|---|---|---|
| `DrawingsRenderer.ts` | +`notifyPriceRangeChanged()`, -wheel/-dblclick listeners, +draft clamp | ~25 |
| `interaction.ts` | +callback param, +виклик в `applyManualRange` | ~8 |
| `ChartPane.svelte` | +передача callback | ~3 |
| **Разом** | | **~36** |

---

## Верифікація

| Перевірка | Метод | Очікуваний результат |
|---|---|---|
| Y-pan sync | Перетягнути графік Shift+Wheel або drag по price axis — слідкувати за hline | Лінія рухається **синхронно** з графіком, без lag |
| Y-zoom sync | Колесо на price axis — слідкувати за trend/rect | Об'єкти масштабуються плавно, без "гумки" |
| X-scroll sync | Звичайний горизонтальний скрол | Без регресії — все як раніше |
| Draft edge | Почати малювати trend, рухати курсор за правий/лівий край барів | Draft продовжує слідкувати (clamp), не застигає |
| Draft normal | Малювання trend/rect у центрі графіка | Без регресії |
| Body drag edge | Тягнути trend до краю екрана | Не застигає, не крашиться |
| Build | `npm run build` | 0 errors |

---

## Rollback

Файли: `DrawingsRenderer.ts`, `interaction.ts`, `ChartPane.svelte`.
Процедура: `git revert <commit>`.

---

## CHANGELOG entries

```jsonl
{"id":"20260224-092","area":"ui_v4","initiative":"drawing_tools_v1","summary":"ADR-0008 PATCH A: Y-axis sync render via notifyPriceRangeChanged callback","scope":"ui","files":["DrawingsRenderer.ts","interaction.ts","ChartPane.svelte"]}
{"id":"20260224-093","area":"ui_v4","initiative":"drawing_tools_v1","summary":"ADR-0008 PATCH B: Draft/drag clamp at null coordinates — prevents freeze","scope":"ui","files":["DrawingsRenderer.ts"]}
```

---

## Product Value

**Stability: Predictable Drawing Behavior**

- **Zero visual lag**: малювання рухаються синхронно з графіком на обох осях — прибирає відчуття "дешевого" UI.
- **No freeze UX**: draft ніколи не "заклинює" — курсор завжди відповідає очікуванню трейдера.
- **Edge resilience**: робота на краях графіка стабільна — трейдеру не потрібно боятися рухати cursor за межі видимих барів.
