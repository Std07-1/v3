# UI v4 — README (SSOT інструкція для Copilot)
**Мета:** з нуля відтворити UI v4 (Svelte) як "SMC Terminal" преміум‑класу, строго за 3 SSOT‑документами: **Master + Addendum v2 (consolidated) + Addendum v3 (consolidated)**.

> Цей README — єдина точка входу для Copilot. Будь‑які приклади коду з Master трактуються як *скелет*, а правила/рейки з v2/v3 — як **обов’язкові override**.

---

## 1) Вхідні SSOT‑документи (читати в цьому порядку)
1. **Master Guide**: `UI v4 _v3.pdf` — структура проєкту, компоненти, WS flow, ChartPane/ChartEngine, базові payload‑типи.
2. **Addendum v2 (consolidated)**: `UI_v4_Addendum_v2_consolidated.pdf` — продуктивність/UX rails: DPR setTransform, RAF latest‑wins, Snap-to-OHLC, Hotkeys, UUID‑id, CommandStack/Undo/Redo, warnings, replay semantics, reconnect.
3. **Addendum v3 (consolidated)**: `UI_v4_Addendum_v3_consolidated.md` — Selection Engine: hit-testing (screen px), handles/body drag, smart drag math, точний eraser, інтеграція з CommandStack.

---

## 2) Правило пріоритетів (ОВЕРРАЙДИ)
- **Base**: Master Guide.
- **Override**: v2 перезаписує все, що стосується: DPR, RAF, Drawings interaction, Snap, Hotkeys, UUID, Undo/Redo, warnings, replay, reconnect.
- **Override+**: v3 перезаписує/доповнює Drawings interaction: selection/hit/drag/eraser.

Якщо знайдена суперечність: **v3 > v2 > Master**.

---

## 3) Non‑goals (щоб не роздувати scope)
- Не робимо “клон TradingView”: без Pine, без marketplace, без десятків типів графіків.
- Не додаємо нові доменні фічі SMC — UI лише відображає те, що приходить у кадрах.
- Немає multi-select (v3: один selectedId).
- Немає rotate/складних трансформацій drawing — лише точки/тіло.

---

## 4) Ключові інваріанти (Rails)
### 4.1. Canvas/DPR
- Заборонено `ctx.scale()` у життєвому циклі resize.
- Дозволено тільки: `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)`.
- Вся геометрія та hit‑testing — у **CSS px**.

### 4.2. RAF scheduler (latest-wins)
- Максимум **1 render на кадр** для overlay та drawings.
- CrosshairMove/RangeChange/Resize/DataUpdate — все колапситься в один RAF.

### 4.3. Час і одиниці
- Домен (WS, drawing points): `t_ms` (Unix milliseconds).
- LWC: `t_sec = t_ms / 1000` (UTCTimestamp seconds).
- Нормалізація `coordinateToTime()` для number/BusinessDay.

### 4.4. Coordinate converters (null safety)
- `timeToCoordinate`, `coordinateToTime`, `priceToCoordinate`, `coordinateToPrice` можуть повертати `null`.
- При `null`: **abort interaction**, додати warning (degraded‑but‑loud), без silent fallback.

### 4.5. Drawings ID та Undo/Redo
- `drawing.id` генерується на клієнті (UUID) і є канонічним.
- CommandStack працює з реальними id одразу.
- Жодних temp_id, жодних “Undo удавано спрацював”.

### 4.6. Pointer events
- Використовувати Pointer Events + `setPointerCapture()` під час drag.
- У hover НЕ ламати LWC (мінімум preventDefault/stopPropagation).

---

## 5) Мінімальний deliverables checklist (що має бути в кінці)
- Працюючий графік (LWC) + switching символ/TF.
- WS підключення + full frame на connect + delta updates.
- Overlay canvas (SMC шари) з RAF‑колапсом.
- Drawings: створення (toolbar), snap-to-OHLC, hotkeys.
- CommandStack: undo/redo для add/update/delete.
- Selection engine: hover/select, handles, body drag, точний eraser.
- StatusBar: OHLC, latency, cursor, warnings.
- Scrollback (підвантаження вліво) з single-inflight + debounce + dedupe.

---

## 6) Файлова структура (рекомендована, узгоджена з Master)
> Імена можуть відрізнятись, але **суть шарів** має зберегтись.

- `src/ws/` — transport, action creators, message router
- `src/chart/` — ChartEngine (LWC), data adapters, series registry
- `src/chart/overlay/` — OverlayRenderer (canvas layer)
- `src/chart/drawings/` — DrawingsRenderer (canvas layer), tools, CommandStack
- `src/chart/interaction/` — geometry.ts (v3), selection/hit-testing
- `src/layout/` — Toolbar, StatusBar, Panels
- `src/stores/` — Svelte stores (symbol/tf/connection/replay/meta)

---

## 7) Покроковий план: 0–5 Slices (Contract-first)
Кожен slice:
- мінімальний диф,
- тести (1–3),
- exit gates,
- без “тихих” фолбеків.

### Slice 0 — Bootstrap skeleton + SSOT contracts
**Ціль:** проєкт збирається, є layout, є типи контрактів, без бізнес-логіки.
- Створити структуру папок.
- Додати `types.ts`: RenderFrame, Candle, Overlay, Drawing, WsAction.
- Додати `README_DEV.md` з командами run/build.
**Exit gates:** `npm run dev` стартує; типи компілюються; lint/tsc проходять.

### Slice 1 — WS Transport + Frame Router (full/delta)
**Ціль:** стабільний зв'язок з бекендом і парсинг кадрів.
- WebSocket client: reconnect, backoff, “full frame on connect”.
- Router: `frame_type` (full/delta/replay/drawing_ack), dispatch у stores.
- Fail-fast на невідомі типи кадрів (warning + drop).
**Exit gates:** connect/reconnect працює; full frame застосовується; delta додаються; warnings видимі.

### Slice 2 — ChartEngine (LWC) + Bars pipeline + Scrollback rail
**Ціль:** графік показує історію, вміє append і prepend.
- LWC chart + candle series.
- `setData` для full, `update` для live (якщо у вас бар‑update підходить).
- Scrollback: range-change -> debounce -> single inflight -> prepend.
**Exit gates:** переключення symbol/tf; scrollback без “рваного” графіка; без дубляжу запитів.

### Slice 3 — OverlayRenderer (SMC layers) + RAF колапс
**Ціль:** overlay canvas поверх LWC, стабільний 60fps.
- Canvas layer + DPR setTransform.
- RAF scheduler latest-wins.
- Тригери: dataUpdate + rangeChange + crosshairMove.
**Exit gates:** hover/crosshair не фризить; 1 render/кадр; ресайз не псує товщину ліній.

### Slice 4 — Drawings v2: tools + snap + hotkeys + CommandStack
**Ціль:** малювання нових об’єктів і справжній Undo/Redo.
- Toolbar: activeTool.
- Draft/commit: pointer events; id=UUID.
- Snap-to-OHLC (dataByIndex або time->bar map) з radius_px.
- Hotkeys: H/T/R/Esc, Ctrl+Z/Y.
- CommandStack: add/update/delete.
**Exit gates:** drawing add/update/remove через WS; undo/redo коректні; snap точний; warnings при null.

### Slice 5 — Drawings v3: selection/hit-testing/drag-edit/eraser
**Ціль:** CAD‑рівень взаємодії.
- geometry.ts: distToSegment, point, rect-edge.
- AABB pre-filter.
- Selection: hovered/selectedId, handleIdx.
- Drag handle/body: pixel delta -> domain conversions.
- Eraser: та ж hit‑логіка.
**Exit gates:** selection стабільний; handles тягнуться; body drag зберігає кут; eraser точний; undo/redo на move/delete.

---

## 8) Мінімальні тести (рекомендовано)
- `geometry.test.ts`: distToSegment/point/rect-edge (edge cases: zero-length, outside).
- `scheduler.test.ts`: RAF latest-wins (один кадр).
- `snap.test.ts`: snap picks nearest OHLC у межах radius.

---

## 9) Ручна QA-перевірка (1 хв)
- Ресайз 10 разів → товщина/координати стабільні.
- Водіння курсором → немає фризів.
- Намалювати trend → select → drag handle → undo/redo.
- Body drag → кут на екрані не змінюється.
- Eraser видаляє лише те, у що “влучили”.
- Scrollback: 3 рази вліво → без дублікатів/дір.

---

## 10) Stop-rules
- Якщо для реалізації треба змінити контракт кадру — спершу оновлюємо `types.ts` і router, потім код.
- Якщо щось “не вписується” — не робимо silent fallback; додаємо warning і відмовляємось від interaction.

