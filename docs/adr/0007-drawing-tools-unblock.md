# ADR-0006: Drawing Tools v1 — Unblocking DrawingsRenderer

**initiative**: `drawing_tools_v1`  
**Дата**: 2026-02-23 — 2026-02-24  
**Статус**: IMPLEMENTED (PATCH 1–4 + persistence + CPU opt)

---

## Проблема

Drawing tools (4 інструменти: hline, trend, rect, eraser) + `DrawingsRenderer` (765→834 LOC) + `CommandStack` (89 LOC) + `geometry.ts` (53 LOC) повністю написані, але вимкнені коментарями `// DISABLED: trading tools deferred (audit T1)` у App.svelte та ChartPane.svelte. Потрібно:

1. Зняти блокування і відновити роботу 4 інструментів
2. Виправити UX-баги (click model, rendering lag)
3. Додати persistence (drawings + symbol/TF + toolbar state)
4. Оптимізувати CPU ws_server

---

## Виконані PATCHі

### PATCH 1: Розблокування DrawingsRenderer (~65 LOC diff) ✅

**App.svelte** (7 blocks): розкоментовано import DrawingToolbar, ActiveTool type, activeTool state, drawing hotkeys (T/H/R/E/Esc/Ctrl+Z/Y), `<DrawingToolbar>` компонент, activeTool prop до ChartPane.

**ChartPane.svelte** (10 blocks): розкоментовано import DrawingsRenderer, activeTool prop, canvas ref, DrawingsRenderer lifecycle (init з `() => {}` noop sendAction), setTool effect, drawing frame handling, destroy, canvas element з brightness filter sync.

**Ключове рішення**: `sendAction: () => {}` noop — drawings client-only, жодних WS повідомлень на бекенд.

### PATCH 3: magnetEnabled (DEFERRED) ⏸️

Додано `setMagnetEnabled()`, snap radius 30px, localStorage `v4_magnet_enabled`, hotkey G, кнопка 🧲 в toolbar. **Вимкнено**: кнопка закоментована в шаблоні, hotkey закоментований. Snap логіка збережена в коді для майбутнього повернення.

### PATCH 4: Bug Fixes (~35 LOC diff) ✅

**Fix 1 — Click-Click State Machine**: Перероблено trend/rect з click-drag-release на TradingView-стиль click-move-click:

- `onPointerDownCapture`: якщо draft існує → commit (2-й клік), інакше → create (1-й клік). Видалено `setPointerCapture`.
- `onPointerUpCapture`: видалено commit draft на pointer-up.
- `handleToolPointerDown` (hline): видалено `setTool(null)` — інструмент лишається активним.
- `finishDraft`: видалено `setTool(null)` — continuous drawing, Escape для виходу.

**Fix 2 — Sync Render (X-axis)**: `subscribeVisibleTimeRangeChange` → `renderSync()` замість `scheduleRender()`. Новий метод `renderSync()`: скасовує pending rAF + синхронний `forceRender()`.

### PATCH 4.1: Y-axis Lag + Snap Visual (~25 LOC) ✅

- Додано `wheel` + `dblclick` listeners на `interactionEl` → `renderSync()` для Y-zoom/Y-reset.
- Snap radius збільшено 12px → 30px.
- Візуальний snap індикатор: зелений кружок `#00e676` на OHLC snap point (рендериться тільки при активному draft + magnet ON).

### PATCH 2: Floating Toolbar UI ✅

- **Position**: `absolute`, `left: 0`, `top: 80px` — плаває над графіком, не посуває свічки.
- **Background**: прибрано (було glass → `background: none`, `border: none`).
- **Розмір**: 28px width (collapsed: 16px), кнопки 22×22px.
- **Collapse**: `‹`/`›` toggle, стан зберігається в localStorage `v4_toolbar_collapsed`.
- **Українські підписи**: "Горизонтальна лінія [H]", "Трендова лінія [T]", "Прямокутник [R]", "Видалити [E]".
- Moved `<DrawingToolbar>` inside `chart-wrapper` div (з `main-content` flex flow).

---

## Persistence (~40 LOC) ✅

### Drawing Persistence

| Параметр | Значення |
|---|---|
| **Key** | `v4_drawings_{symbol}_{tf}` (per symbol+TF pair) |
| **Save** | Після кожного ADD/DELETE/UPDATE через `applyLocally()` |
| **Load** | При ініціалізації DrawingsRenderer + при зміні symbol/TF (`setStorageKey()`) |
| **Server sync** | `setAll([])` ігнорується (drawings client-only) |
| **Error handling** | Silent catch (quota, private mode, corrupted JSON) |
| **Баг виправлений** | `applyLocally` мав early returns перед `saveToStorage()` — переписано на if/else if/else |

### Symbol/TF Persistence

| Параметр | Значення |
|---|---|
| **Key** | `v4_last_pair` (JSON: `{symbol, tf}`) |
| **Save** | При кожному switch через ChartHud |
| **Restore** | One-shot на першому full frame: якщо збережена пара ≠ дефолтній → skip frame + switchSymbolTf |
| **Flash fix** | Перший full frame з дефолтним symbol/TF НЕ рендериться, відразу шлеться switch |

### Toolbar Collapse Persistence

| Параметр | Значення |
|---|---|
| **Key** | `v4_toolbar_collapsed` (`'1'` / `'0'`) |

---

## ws_server CPU Optimization ✅

**Проблема**: ws_server (WS push) споживав 32-79% CPU при маніпуляціях vs ui_chart_v3 (HTTP poll) 0%. Пікові спайки до 94%.

**Root Cause**:

1. `_delta_loop` кожну 1.0s створювала thread через `run_in_executor(None, ...)` → default ThreadPoolExecutor (до 32 тредів)
2. При switch, cancel() не зупиняє blocking I/O в executor — треди накопичуються
3. JSON серіалізація великих frames (300+ candles)

**Фікси**:

| Зміна | Файл | Ефект |
|---|---|---|
| `DEFAULT_DELTA_POLL_S: 1.0 → 2.0` | ws_server.py:42 | -50% polling frequency |
| `ThreadPoolExecutor(max_workers=2)` | ws_server.py:797 | Обмеження thread explosion |
| Dedicated `_uds_executor` | ws_server.py:376,394 | Всі UDS I/O через 2-thread pool |

**Результат**:

| Метрика | До | Після |
|---|---|---|
| Idle CPU | 4.8-6.1% | 2.2-3.0% |
| Peak CPU | 91.4% (тримається) | 94.1% (швидко скидується) |
| Threads | 42-44 | 21-36 |

---

## Верифікація

```
$ npm run build
✓ 165 modules transformed
dist/assets/index-C6JLwVfF.js   267.00 kB │ gzip: 85.75 kB
✓ built in 2.87s
```

Bundle: 264.44 KB (PATCH 1) → 267.00 KB (final) = **+2.56 KB** total.

---

## Що працює

- ✅ 4 інструменти: Horizontal Line (H), Trend Line (T), Rectangle (R), Eraser (E)
- ✅ Click-click UX: TradingView-style (клік → рух → клік)
- ✅ Continuous drawing: інструмент лишається активним, Escape для виходу
- ✅ Hotkeys: T/H/R/E/Esc/Ctrl+Z/Ctrl+Y
- ✅ Undo/Redo через CommandStack
- ✅ Selection + drag: вибір, перетягування, handle resize
- ✅ Sync render: drawings рухаються синхронно з графіком (X + Y axis)
- ✅ Drawing persistence per symbol+TF (localStorage)
- ✅ Symbol/TF persistence (без flash default)
- ✅ Toolbar collapse persistence
- ✅ Floating toolbar: не посуває свічки, collapse, українські підписи
- ✅ Brightness sync: drawings canvas отримує той самий filter: brightness()
- ✅ WS safe: noop sendAction, жодних drawing WS повідомлень
- ✅ ws_server CPU: 2.2-3.0% idle (delta_poll 2s + 2-thread pool)

## Deferred / Known Issues

- ⏸️ Magnet (snap-to-OHLC): код збережений, UI вимкнено — потребує debug
- ⚠️ ~~Light theme: base color `#c8cdd6` має низький контраст на білому~~ → ✅ **DONE** ([ADR-0008](0008-glass-toolbar-light-theme.md): `#434651` WCAG AA, glass toolbar, CSS custom properties)
- ⚠️ ws_server peak CPU ~94%: burst при switch (300+ bars read). Потрібен event-driven (Варіант B)
- 📋 Drawing properties: текст, колір, стиль, ширина лінії — окремий initiative `drawing_properties_v1`
- 📋 Per-drawing delete icon (замість Eraser mode) — `drawing_properties_v1`
- 📋 Visibility per TF — `drawing_properties_v1`
- 📋 New tools: ray, fib_retracement, channel, pitchfork, measure — окремий initiative

## Rollback

```bash
git revert <commit>  # кожен PATCH — окремий revert
```

## localStorage Keys (drawing_tools_v1)

| Key | Формат | Опис |
|---|---|---|
| `v4_drawings_{symbol}_{tf}` | JSON Drawing[] | Drawings per symbol+TF pair |
| `v4_last_pair` | JSON {symbol, tf} | Остання обрана пара |
| `v4_toolbar_collapsed` | `'1'` / `'0'` | Стан collapse toolbar |
| `v4_magnet_enabled` | `'1'` / `'0'` | Магніт (deferred) |
