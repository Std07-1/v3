# ADR-0074: Drawing Tools V1 — UX Fix + Tool Registry

## Метадані

| Поле           | Значення                                                                 |
| -------------- | ------------------------------------------------------------------------ |
| ID             | ADR-0074                                                                 |
| Статус         | **ACCEPTED** (rev 1.1 — 2026-05-12)                                      |
| Дата           | 2026-05-12                                                               |
| Автори         | Станіслав + Opus 4.7 (Discovery-based + R_REJECTOR review)               |
| Замінює        | —                                                                        |
| Розширює       | ADR-0007 (DrawingsRenderer baseline), ADR-0008 (Y-axis sync rail), ADR-0066 (visual identity tokens — потребує невеликого amendment §"Token additions"), ADR-0072 (mobile canonical layout — toolbar поважає left-side + bottom-LEFT regions) |
| Soft-blocks    | **ADR-B** (drawings persistence уніфікація — UDS namespace, прибирає dual-storage), **ADR-C** (SMC auto-zones як read-only overlay у drawing layer) |
| Зачіпає шари   | `ui_v4/src/chart/drawings/` (renderer + new tools/ subdir), `ui_v4/src/chart/interaction/geometry.ts` (hit-test constants), `ui_v4/src/layout/DrawingToolbar.svelte` (toolbar redesign), `ui_v4/src/App.svelte` (keyboard handler refactor), `ui_v4/src/stores/keyboard.svelte.ts` (новий store), `ui_v4/src/styles/tokens.css` (нові токени для drawing UX) |
| Initiative     | `drawing_tools_v1` (продовжує існуючу серію 0007/0008/0009)               |

---

## Quality Axes

- **Ambition target**: **R2** — usability fix з невеликим архітектурним апгрейдом (Tool Registry contract). Не R3, бо ми лише централізуємо вже наявний `if/else` по `d.type` у DrawingsRenderer — не вводимо новий primitive. Виходимо у R3 в ADR-B (persistence уніфікація) або ADR-C (SMC overlay reuse).
- **Maturity impact**: **M3 → M3.5** — часткова елевація. Закриваємо drift у трьох місцях: (1) inline switch по `DrawingType` → declarative registry, (2) hit-test magic numbers → CSS-vars з platform-aware значеннями, (3) hotkey handler з mixed abstraction → store з focus-guard. Повний M4 у ADR-B (єдина persistence-вузька талія для drawings).

---

## Контекст

### Поточний стан (verified, line-numbered)

Drawing engine у репо вже зрілий. Перепис = регрес у Maturity (X39).

| Файл | Стан | Що містить |
|---|---|---|
| `ui_v4/src/chart/drawings/DrawingsRenderer.ts:1-890` [VERIFIED] | 890 LOC, production-ready | Click-click state machine (TradingView-style), hit-test з AABB-reject, snap-to-OHLC (`SnapConfig` 254-293), theme-aware кольори (ADR-0007/0066), DPR rail + ResizeObserver, LWC y-axis sync (ADR-0008), null-fallback для coord-mapping, selection/hover/drag з handles, UiWarning throttle |
| `ui_v4/src/chart/drawings/CommandStack.ts:1-88` [VERIFIED] | 88 LOC | Undo/redo + WS roundtrip (зараз noop через `sendAction: () => {}` у ChartPane.svelte:205) |
| `ui_v4/src/chart/interaction/geometry.ts:1-53` [VERIFIED] | 53 LOC | `HIT_TOLERANCE_PX = 6`, `HANDLE_RADIUS_PX = 3.5`, `HANDLE_RADIUS_HOVER_PX = 5`, плюс `distToHLine/distToPoint/distToSegment/distToRectEdge` helpers |
| `ui_v4/src/layout/DrawingToolbar.svelte:1-164` [VERIFIED] | 164 LOC | 28×N column `position: absolute; left:0; top:80px`, 4 кнопки 22×22px з юнікод-гліфами `━ ╱ ▭ ✕`, title-attribute як єдиний label, magnet UI закоментовано (lines 66-79) |
| `ui_v4/src/types.ts:237-256` [VERIFIED] | Domain model | `DrawingType = 'hline'\|'trend'\|'rect'`; `DrawingPoint = { t_ms, price }` (I1-conformant intrinsically); `Drawing = { id, type, points[], meta? }`; `ActiveTool = ... \| 'eraser' \| null` |
| `ui_v4/src/App.svelte:239-285` [VERIFIED] | Keyboard handler | Hotkeys H/T/R/E + Esc + Ctrl+Z/Y вже існують; input-focus guard покриває `HTMLInputElement`/`HTMLTextAreaElement` (lines 246-250) але **НЕ `contenteditable`** |
| `runtime/ws/ws_server.py:444` [VERIFIED] | Backend WS | `"drawings": []` (порожній placeholder); жодного handler-а для `drawing_add/update/remove` actions — backend свідомо вимкнено (`ChartPane.svelte:205` коментар "drawings client-only") |

### Точкові UX failure points (відштовхуємось від цих 7 для acceptance criteria)

1. **Кнопки 22×22px** [DrawingToolbar.svelte:142-143] — нижче WCAG 2.1 AA Level (24×24 min target size). На retina mobile fingertip ≈6mm → недосяжно без перетягування погляду на toolbar.
2. **Unicode гліфи `━ ╱ ▭ ✕`** [DrawingToolbar.svelte:33-36] — placeholder-feel, неоднозначні (✕ = eraser? close? delete?). Брандфрейм не визначений.
3. **Лейбли тільки в `title=""`** [DrawingToolbar.svelte:57] — hover-only, на mobile ніколи не показуються. Новий користувач не знає, що робить кнопка.
4. **Hit-test 6px / handles 3.5px** [geometry.ts:4-6] — sub-pixel на retina mobile (на 3×DPR — 18 device-px tolerance, 10.5 device-px handle radius. Все ще нижче WCAG 44×44).
5. **Magnet UI закоментовано** [DrawingToolbar.svelte:66-79] — snap-to-OHLC logic у renderer-і preserved (`setMagnetEnabled` метод існує, `magnetEnabled` store у App.svelte:123), але trader не має доступу через UI. Малювати по open/close manually = мікрометричне завдання.
6. **Хоткеї T для Trend** [App.svelte:267-269] — конфліктний mnemonic (T часто = Text, Time). Pragma: `\` (графічна іконка трендової лінії) — без конфлікту і самодокументоване.
7. **Toolbar `left:0; top:80px`** [DrawingToolbar.svelte:87-88] — конфліктує з ChartHud у landscape mobile (<500px height), не layout-aware. ADR-0072 не локує left-side, але NarrativeSheet forward-ref (`ADR-0073 §"Що залишається з рев 2 amendments"`) фактично резервує bottom-CENTER/RIGHT під мобільну Архі-surface.

### Що нам каже research (Trader Tools Research, 2026-05-12, виконано в рамках цього ж initiative)

Бенчмарк TradingView / TabTrader / MT5 / Bybit / Altrady показав:

- **Drawing-on-touch alerts** покриті у TV тільки для 4 типів (lines, channels, rectangle, anchored VWAP) — це premium gap, але це **ADR-D territory**, не V1.
- **Magnet/snap-to-OHLC** — must-have у TV/TabTrader для нативного відчуття. У нас logic preserved, треба тільки повернути UI.
- **Mobile parity** — конкуренти не дають 100%-feature parity. Це наш потенційний edge, але V1 — це лише `parity` між нашим desktop і mobile, не competitive win.

Detail: див. `outputs/Trader_Tools_Research.docx` (preserved у session outputs).

### Чому не "архітектурний марафон"

Попередня версія ADR (rejected by owner) пропонувала створити `core/drawings/fsm/` з 7 FSM, Archi reader/writer, drawings alert engine, на 5000 LOC + 10 тижнів. Це було:

- **I0 violation** — `core/` не повинно містити user-input domain (S1: SMC = read-only deterministic; drawings = user-created stateful)
- **X35 inline-duplication-in-reverse** — створення `core/drawings/coords.py` поряд з наявним `core/buckets.py:resolve_anchor_offset_ms()`
- **Category error** — SMC auto-zones (deterministic detection з market data) лимали б у одну таксономію з трендовою лінією, яку малює користувач
- **F9 craftsmanship violation** — "молоток і цвяхи" pattern: усе виглядало як ADR overreach

V1 = **хірургічний UX fix**: 6 P-slices, ~750 LOC коду + ~150 LOC тестів, 1-2 тижні. Architecture upgrade у V1 — лише Tool Registry hook (відкритий до +5 tools у ADR-B без переписування).

---

## Розглянуті альтернативи

### V-A. Minimal patch без Tool Registry

**Плюси**: ~300 LOC, найшвидший ship. Розширює toolbar (більші кнопки + labels), бампає hit-test константи.

**Мінуси**: Inline `if/else` по `d.type` у `DrawingsRenderer.ts:374-443` (hit-test), `:772-832` (render), `:651-718` (drag) залишається. Додавання нового tool у ADR-B → редагувати 4 місця у одному файлі = SSOT in progress (X35 risk).

**Вердикт**: відхилено. У ADR-B буде +5 tools (h_ray, ray, channel, text, fib_retracement) — якщо не зробити Registry зараз, доведеться все одно його робити, плюс мігрувати 5 tools. Сума більша.

### V-B. ToolRegistry + 4 tools migrated (РЕКОМЕНДОВАНО, це ADR)

**Плюси**: Кожен tool = declarative модуль з 3 функціями (`render`, `hitTest`, `drawHandles`). `DrawingsRenderer` втрачає inline switch, стає orchestrator над `Map<DrawingType, ToolModule>`. Open/closed для +5 tools у ADR-B без редагування renderer-а. Cost у V1: ~+250 LOC.

**Мінуси**: Інтерфейс треба правильно зафіксувати (не overdesign per F9). Vitest mock canvas для unit testing ToolModule contract — додатковий tooling.

**Вердикт**: прийнято.

### V-C. Повний переписо v2 рантайму

**Плюси**: Можна "зробити правильно".

**Мінуси**: 890 LOC чистого working коду викидається. X39 Maturity regression (M3 → M2 короткостроково). 5-10 тижнів роботи. Не вирішує жодну конкретну UX-скаргу.

**Вердикт**: відхилено явно. Цей варіант = anti-pattern, який rejected ADR пропонував.

### V-D. Створити `core/drawings/` з FSM (rejected ADR proposal)

**Плюси**: "Симетрія з core/smc/".

**Мінуси**: I0 violation (core domain != user-input), F5 violation (Dependency Rule), X35 (duplicate anchor math поряд з `core/buckets.py`), категоріальна помилка (auto-detected zones ≠ user-created drawings).

**Вердикт**: явно відхилено. Це і є той ADR, який rejected.

---

## Рішення

Реалізувати **V-B**: 6 P-slices, кожен ≤150 LOC, з incremental verify між slices.

### 1. Tool Registry contract (Slice T1)

Новий каталог `ui_v4/src/chart/drawings/tools/` з 4 файлами + 1 index. Кожен tool — declarative модуль:

```typescript
// ui_v4/src/chart/drawings/tools/types.ts
import type { Drawing, DrawingPoint } from '../../../types';

/** Контекст рендеру, що передається tool-у. Renderer-агностичний. */
export interface RenderContext {
  ctx: CanvasRenderingContext2D;
  toX: (t_ms: number) => number | null;
  toY: (price: number) => number | null;
  baseColor: string;
  accentColor: string;
  rectFill: string;
  isDraft: boolean;
  isHovered: boolean;
  isSelected: boolean;
  cssW: number;
  cssH: number;
}

/** AABB для hit-test rejection (повертається з render для cache). */
export interface ScreenAabb {
  minX: number; minY: number; maxX: number; maxY: number;
}

/** Hit-test result — null якщо тул не пройшов, distance в screen-px інакше. */
export type HitTestResult =
  | { hit: true; distance: number; handleIdx: number | null }
  | { hit: false };

/** Контракт одного drawing tool. */
export interface ToolModule {
  /** Унікальний type discriminator у Drawing.type. */
  readonly id: 'hline' | 'trend' | 'rect';

  /** Скільки точок очікує draft state machine (1 = instant, 2 = click-click). */
  readonly pointsRequired: 1 | 2;

  /** UI metadata. */
  readonly label: string;       // "Горизонтальна лінія"
  readonly icon: string;        // Lucide icon name: "minus", "trending-up", "square"
  readonly hotkey: string;      // single char or "\\"

  /** Render drawing у вказаному контексті. Повертає AABB для cache. */
  render(d: Drawing, ctx: RenderContext): ScreenAabb | null;

  /** Hit-test проти drawing з cursor (x, y) у CSS-px. */
  hitTest(d: Drawing, cursorX: number, cursorY: number, tolerance: number,
    toX: (t: number) => number | null, toY: (p: number) => number | null): HitTestResult;

  /** Render handles (called separately, after main render pass). */
  drawHandles(d: Drawing, ctx: RenderContext, handleRadius: number, handleRadiusHover: number,
    hoveredHandleIdx: number | null): void;
}
```

`DrawingsRenderer` тримає `private toolRegistry: Map<DrawingType, ToolModule>` ініціалізований у constructor-і. Inline switch замінюється на:

```typescript
// Замість 12 if-блоків
const tool = this.toolRegistry.get(d.type);
if (!tool) return;  // safety, не повинно відбутись після migration
const aabb = tool.render(d, ctx);
if (aabb) this.aabbById.set(d.id, aabb);
```

**Чому НЕ `serialize/deserialize` per-tool**: `Drawing` уже JSON-serializable через domain model (`{ type, points, meta }`). Per-tool сериалізація — YAGNI до ADR-C, де можливі stateful primitives (Elliott waves з computed sub-points).

### 2. Hit-test токени через CSS-vars (Slice T2)

`ui_v4/src/styles/tokens.css` додаємо:

```css
:root {
  /* drawing UX — V1 */
  --drawing-hit-tolerance-px: 10;
  --drawing-handle-radius-px: 5;
  --drawing-handle-radius-hover-px: 8;
  --drawing-toolbar-btn-size: 32px;
  --drawing-toolbar-icon-size: 18px;
  --drawing-toolbar-gap: 4px;
  --drawing-toolbar-label-offset: 8px;
}

@media (pointer: coarse) {
  :root {
    --drawing-hit-tolerance-px: 16;
    --drawing-handle-radius-px: 7;
    --drawing-handle-radius-hover-px: 10;
    --drawing-toolbar-btn-size: 44px;
    --drawing-toolbar-icon-size: 22px;
    --drawing-toolbar-gap: 6px;
  }
}
```

Геометрія: button 44×44 + tolerance 16px = ефективна tap-zone ~76×76 device-px (на 1×DPR mobile). Handle radius 7px = 14×14 visible × hover 10px = 20×20 visible. Це **WCAG 2.1 AA target 44×44 виконується по факту, не лише номінально** — owner-вимога.

Renderer кешує значення з `getComputedStyle()` у `onMount` + `refreshThemeColors()` (вже існуючий хук, виклик з App.svelte при зміні теми). Hit-test loop читає з cached fields, не з DOM (X37 mixed abstraction; getComputedStyle тригерить browser style recalc per call).

```typescript
// у DrawingsRenderer constructor
private hitTolerancePx = 10;
private handleRadiusPx = 5;
private handleRadiusHoverPx = 8;

refreshThemeColors(): void {  // existing method, extended
  const s = getComputedStyle(this.canvas);
  this.themeBaseColor = ...;  // existing
  this.hitTolerancePx = parseFloat(s.getPropertyValue('--drawing-hit-tolerance-px')) || 10;
  this.handleRadiusPx = parseFloat(s.getPropertyValue('--drawing-handle-radius-px')) || 5;
  this.handleRadiusHoverPx = parseFloat(s.getPropertyValue('--drawing-handle-radius-hover-px')) || 8;
}
```

`geometry.ts` залишає чисті pure-функції; константи `HIT_TOLERANCE_PX/HANDLE_RADIUS_PX/HANDLE_RADIUS_HOVER_PX` стають **fallback defaults** (зворотна сумісність), а renderer передає runtime значення з cached CSS-var.

### 3. Toolbar visual redesign (Slice T3)

`DrawingToolbar.svelte` переписаний на:

```
┌─────────────────────────────────────┐
│  ●  Курсор          [Esc]           │  ← row 1
│  ━  Горизонталь     [H]             │  ← row 2 (selected = gold underline)
│  \  Трендова        [\]             │
│  ▭  Прямокутник     [R]             │
│  ✕  Гумка           [E]             │
│  ─                                  │  ← divider
│  🧲 Magnet          [G]             │  ← row 7 (re-enabled)
└─────────────────────────────────────┘
```

Desktop: vertical column, button 32×32 + label поряд (текст видно завжди, не лише в tooltip). Hotkey як subtle hint справа `<kbd>`. Width ~140px collapsed → 28px на collapse (як зараз) — користувач керує.

Mobile (≤640px portrait): **bottom-LEFT vertical stack** з 5 кнопок 44×44 розміщений `bottom: 16px; left: 8px`. Зайняв ~220px screen-height (50-55% від screen-width на ~6-inch device). Залишає bottom-center/right для майбутнього NarrativeSheet (ADR-0074 forward-ref у ADR-0073 §"Що залишається з рев 2 amendments" — той NarrativeSheet ADR ще не написаний, але це **ADR-0075+ territory**, не цей).

Mobile landscape (`(orientation:landscape) and (max-height:500px)`) — toolbar складається в icon-only mode 32×32, без labels, top-left vertical (як desktop, ширина 36px). Це не порушує ADR-0072 (toolbar = left-edge, ADR-0072 локає тільки top-right).

Іконки: **Lucide** (`lucide-svelte` пакет). Якщо не у bundle — Heroicons або Material-Icons-Svelte як fallback. Конкретний пакет — рішення під час T3, з verify chrome bundle-size impact (≤8KB net). Unicode гліфи `━ ╱ ▭ ✕` повністю прибрані.

### 4. Mobile layout (інтегровано в Slice T3, окремий verify-step T3-mobile)

`@media` rules у toolbar component. Без зайвого JS — pure CSS conditional. ADR-0072 reads:

> Locks mobile geometry NOT covered by 0065/0068/0069/0070: ☰ position empirical (right:44px transparent — NO backdrop "пятно")

DrawingToolbar = left-edge, **не зачіпає right-side area**, ADR-0072 jurisdiction не порушується.

### 5. Magnet UI re-enable (Slice T4)

Прибрати `<!-- DEFERRED: ... -->` блок з `DrawingToolbar.svelte:66-79`. Props extend:

```typescript
const { activeTool, onSelectTool, magnetEnabled, onToggleMagnet } = $props();
```

Wire через `App.svelte:541`:

```svelte
<DrawingToolbar
  {activeTool}
  {magnetEnabled}
  onSelectTool={(t) => (activeTool = t)}
  onToggleMagnet={() => { magnetEnabled = !magnetEnabled; saveMagnet(magnetEnabled); }}
/>
```

`App.svelte` already має `magnetEnabled = $state(loadMagnet())` (line 123) та коментар `// DEFERRED: magnet hotkey disabled` (lines 283-284) — розблоковуємо обидва (hotkey `G`).

**Verify**: snap-to-OHLC візуально дає green dot indicator при близькості (`DrawingsRenderer.ts:838-849` — `lastSnap` indicator already implemented).

### 6. Keyboard store з focus-guard (Slice T5)

Винести `handleGlobalKeydown` з `App.svelte:239-285` (47 LOC mixed-abstraction-levels — X37 risk) у новий store `ui_v4/src/stores/keyboard.svelte.ts`:

```typescript
// ui_v4/src/stores/keyboard.svelte.ts
import type { ActiveTool } from '../types';

type Action =
  | { type: 'set_tool'; tool: ActiveTool }
  | { type: 'cancel_draft' }
  | { type: 'toggle_magnet' }
  | { type: 'undo' }
  | { type: 'redo' }
  | { type: 'open_diagnostics' };

export type KeyboardHandler = (a: Action) => void;

/** Перевіряє, чи активний елемент = input-подібний (текст, число, contenteditable). */
function isTextInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  if (el instanceof HTMLInputElement) return true;
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  // <select> теж глобально не повинен крадти Esc/Enter
  if (el instanceof HTMLSelectElement) return true;
  return false;
}

/** Чистий mapper key → action. Renderer-агностичний, легко тестується. */
export function mapKeyToAction(e: KeyboardEvent): Action | null {
  if (e.ctrlKey && e.shiftKey && e.key === 'D') return { type: 'open_diagnostics' };
  if (e.ctrlKey && e.key === 'z') return { type: 'undo' };
  if (e.ctrlKey && e.key === 'y') return { type: 'redo' };
  if (e.key === 'Escape') return { type: 'cancel_draft' };

  // Drawing hotkeys — guarded
  if (isTextInputFocused()) return null;

  const k = e.key.toLowerCase();
  if (k === 'h') return { type: 'set_tool', tool: 'hline' };
  if (k === '\\') return { type: 'set_tool', tool: 'trend' };  // owner spec: \ замість T
  if (k === 'r') return { type: 'set_tool', tool: 'rect' };
  if (k === 'e') return { type: 'set_tool', tool: 'eraser' };
  if (k === 'g') return { type: 'toggle_magnet' };  // owner spec: G для magnet
  return null;
}

/** Реєструє window listener і повертає cleanup. */
export function setupKeyboard(handler: KeyboardHandler): () => void {
  const onKey = (e: KeyboardEvent) => {
    const action = mapKeyToAction(e);
    if (!action) return;
    // preventDefault лише для Ctrl-combos і Escape
    if (e.ctrlKey || e.key === 'Escape') e.preventDefault();
    handler(action);
  };
  window.addEventListener('keydown', onKey);
  return () => window.removeEventListener('keydown', onKey);
}
```

`App.svelte` стає:

```svelte
<script lang="ts">
  import { setupKeyboard } from './stores/keyboard.svelte';
  // ...
  onMount(() => {
    const cleanup = setupKeyboard((action) => {
      switch (action.type) {
        case 'set_tool': activeTool = activeTool === action.tool ? null : action.tool; break;
        case 'cancel_draft': activeTool = null; chartPaneRef?.cancelDraft(); break;
        case 'toggle_magnet': magnetEnabled = !magnetEnabled; saveMagnet(magnetEnabled); break;
        case 'undo': chartPaneRef?.undo(); break;
        case 'redo': chartPaneRef?.redo(); break;
        case 'open_diagnostics': openDiagnostics(); break;
      }
    });
    return cleanup;
  });
</script>
```

Pure `mapKeyToAction()` тестується через Vitest unit-tests з mock `KeyboardEvent` об'єктами + `document.activeElement` shadow.

---

## Технічний контракт (зведено)

### Файлова мапа

```
ui_v4/src/
├── chart/
│   ├── drawings/
│   │   ├── DrawingsRenderer.ts   [MODIFIED ~+80 LOC, -120 LOC inline switches]
│   │   ├── CommandStack.ts        [unchanged]
│   │   └── tools/                 [NEW dir]
│   │       ├── types.ts           [NEW ~80 LOC]
│   │       ├── HLineTool.ts       [NEW ~90 LOC]
│   │       ├── TrendTool.ts       [NEW ~90 LOC]
│   │       ├── RectTool.ts        [NEW ~100 LOC]
│   │       ├── EraserTool.ts      [NEW ~30 LOC — це не drawing-rendering, але реєстр потрібно для UI consistency]
│   │       └── index.ts           [NEW ~20 LOC — Map<DrawingType, ToolModule> assembly]
│   └── interaction/
│       └── geometry.ts            [unchanged — pure helpers лишаються]
├── layout/
│   └── DrawingToolbar.svelte      [REWRITE ~150 LOC]
├── stores/
│   └── keyboard.svelte.ts          [NEW ~80 LOC]
├── styles/
│   └── tokens.css                  [+25 LOC drawing tokens]
└── App.svelte                      [MODIFIED ~-50 LOC, +15 LOC — keyboard handler refactor]
```

### Net diff

```
+ ~750 LOC (production code, new files)
+ ~150 LOC (Vitest unit + integration tests)
- ~170 LOC (inline switches у DrawingsRenderer; DEFERRED block у DrawingToolbar; handleGlobalKeydown у App)
─────────
NET: ~+730 LOC
```

Розподіл по slices — кожен ≤150 LOC у бюджеті A3:

| Slice | Назва | Net LOC | Тестова verify |
|---|---|---|---|
| **T1** | Tool Registry contract + 4 модулі + integration у DrawingsRenderer | ~+250 | Vitest unit per ToolModule + integration test з mock canvas — 4 drawings render → AABB correct |
| **T2** | CSS-vars hit-test tokens + cached refresh у renderer | ~+50 | Unit test: токен зміна via `refreshThemeColors()` → hit-test з новими значеннями (mock getComputedStyle) |
| **T3** | DrawingToolbar redesign desktop + Lucide іконки + media queries для mobile | ~+150 (включаючи tokens.css extension) | Visual + manual checklist; Vitest snapshot тоkens.css |
| **T4** | Magnet UI re-enable + props wiring + hotkey G | ~+30 | Manual: magnet toggle, snap-on-draft → green dot indicator |
| **T5** | Keyboard store з focus-guard + App.svelte refactor | ~+45 (з ~-50 cleanup) | Unit test `mapKeyToAction()`: 12 keys × 2 focus states |
| **T6** | Acceptance test harness (Vitest unit + integration + manual matrix doc) | ~+150 | Test run = 0 failures, 100% coverage на ToolModule contract |

Кожен slice = окремий PR/commit з incremental verify. Між slices — пер-slice rollback fence: T2 не залежить від T1, T4 не залежить від T3, etc. Тільки T6 залежить від T1-T5.

### API contracts (зведено)

```typescript
// stores/keyboard.svelte.ts
export type Action = ...;  // discriminated union
export function mapKeyToAction(e: KeyboardEvent): Action | null;
export function setupKeyboard(handler: KeyboardHandler): () => void;

// chart/drawings/tools/types.ts
export interface ToolModule { id; pointsRequired; label; icon; hotkey;
  render(d, ctx): ScreenAabb | null;
  hitTest(d, x, y, tolerance, toX, toY): HitTestResult;
  drawHandles(d, ctx, r, rHover, hoveredIdx): void;
}
export interface RenderContext { ctx; toX; toY; baseColor; accentColor; rectFill; isDraft; isHovered; isSelected; cssW; cssH; }

// chart/drawings/tools/index.ts
export const TOOL_REGISTRY: Map<DrawingType, ToolModule>;

// chart/drawings/DrawingsRenderer.ts (modified)
// Extends existing refreshThemeColors() to cache hit-test tokens
// No public API change
```

---

## Acceptance criteria

### Vitest unit/integration

| Test file | Покриває |
|---|---|
| `src/chart/drawings/tools/types.test.ts` | ToolModule contract — type-checks (compile-time) + 1 sanity test |
| `src/chart/drawings/tools/HLineTool.test.ts` | render returns AABB; hitTest at line y returns hit; hitTest 20px away returns no-hit |
| `src/chart/drawings/tools/TrendTool.test.ts` | hitTest на segment endpoints + midpoint; AABB обмежує два endpoints |
| `src/chart/drawings/tools/RectTool.test.ts` | hitTest на ребрах = hit; hitTest всередині rect = no-hit (по contract distToRectEdge у geometry.ts:36-52) |
| `src/stores/keyboard.test.ts` | mapKeyToAction для 12 keys × 2 focus states (input focused / not) + Ctrl-combos + Escape |
| `src/chart/drawings/integration.test.ts` | DrawingsRenderer з mock canvas: add 3 drawings → renderAll() → check AABBs cached + handles drawn for selected |

Coverage gate: 100% lines на нові tool-modules + keyboard store; 80% на DrawingsRenderer (renderer існуючий, не вимагаємо overspec).

### Manual acceptance matrix (записана у ADR §"Verification" + PR description)

| # | Test | Pass criteria |
|---|------|---------------|
| 1 | Tap-accuracy mobile | 10 tap-ів на handle на iPhone 13 / Mid-tier Android — 9/10 hits (90%) |
| 2 | Hover toolbar desktop | Label видно поряд з іконкою без hover (always-visible) |
| 3 | Magnet enable + draw trendline | Green dot indicator з'являється при close-to-OHLC |
| 4 | Hotkey H → hline → click | Інструмент активний, draft з'являється на 1st click, commit на 2nd |
| 5 | Hotkey `\` → trend → Escape | Draft зникає, activeTool = null |
| 6 | Hotkey під час `<input>` focus | Hotkey не активує tool (focus-guard works) |
| 7 | Draw 3 drawings → reload page | Усі 3 drawings відновлюються з localStorage (`v4_drawings_{symbol}_{tf}` ключ) |
| 8 | Mobile landscape (<500px height) | Toolbar складається в icon-only, не перекриває chart |
| 9 | Undo/redo (Ctrl+Z, Ctrl+Y) | CommandStack працює як до ADR (regression net) |
| 10 | FPS при N=200 drawings | 58+ FPS на Chrome desktop (Vitest perf benchmark) |

### Verify-матриця (правило A7, medium scope)

| Розмір | Перевірки |
|--------|-----------|
| medium | Vitest test suite green; manual matrix 10/10 pass; bundle size delta ≤ +25KB (Lucide icons); no diagnostics errors на touched files (K3 gate); smoke UI з drawings на 2 TF (M5, H4) |

---

## Rollback per slice

| Slice | Rollback команда |
|---|---|
| T1 | `git revert <T1 commit>` — повертає inline switches у DrawingsRenderer; tools/ subdir видаляється; localStorage drawings не торкнуті |
| T2 | `git revert <T2 commit>` — повертає constants HIT_TOLERANCE_PX=6 у geometry.ts; tokens.css drawing-* tokens видаляються |
| T3 | `git revert <T3 commit>` — повертає 22×22 unicode toolbar; tokens.css mobile media queries видаляються |
| T4 | Один-рядковий revert у DrawingToolbar.svelte (`<!-- DEFERRED -->` блок назад) + App.svelte hotkey G назад в comment |
| T5 | `git revert <T5 commit>` — keyboard.svelte.ts видаляється; App.svelte:239-285 повертається |
| T6 | Видалити test files; рантайм не зачеплений |

Cross-slice rollback не потрібен — кожен slice samодостатній.

---

## Consequences

### Positive

- **WCAG 2.1 AA по факту**: tap target 44×44 mobile, 32×32 desktop. Закриває скаргу "tools відлякують".
- **F9 craftsmanship**: inline switches замінені на declarative registry; magic numbers замінені на CSS-vars (X36 compliance); mixed abstraction у handleGlobalKeydown → дві ясні фази (mapper + handler).
- **Open/closed для +5 tools у ADR-B**: ToolModule contract фіксований, додавання нового tool = новий файл + index entry, БЕЗ редагування renderer-а.
- **Existing engine respected**: 890 LOC DrawingsRenderer лишається working, тільки переписуємо inline switches. Maturity не падає (X39 OK).
- **Magnet user-accessible**: знімаємо deferred block, snap-to-OHLC доступний trader-у.

### Negative

- **+8KB bundle (Lucide icons subset)**: tree-shaken до ~4 іконок (minus, trending-up, square, x, magnet) — менше +8KB net (verified у T3). Якщо більше — fallback на inline SVG.
- **Tool Registry overhead для 4 tools = slight overengineering з V1 точки зору**: виправдовується ADR-B (де додаються +5 tools). Якщо ADR-B не відбудеться у 3-місячному вікні — Registry стане dead weight (low-prob risk).
- **Keyboard store додає файл**: одна нова межа у `stores/`. Виправдано: 47 LOC mixed-abstraction handler у App.svelte вже X37 в action.

### Neutral

- **Backend persistence (drawing_add/update/remove) лишається noop**: `ChartPane.svelte:205` коментар "drawings client-only (ADR-0005)" зберігається. Уніфікація → ADR-B.
- **SMC auto-zones reuse**: не зачіпається. ADR-C territory.

---

## Cross-references

- **Розширює**: ADR-0007 (DrawingsRenderer baseline, V1 unblock 2026-02-23), ADR-0008 (Y-axis sync rail), ADR-0009 (sync render fix для drawings)
- **Розширює (visual identity)**: ADR-0066 — `tokens.css` extension. Параллельний 1-line amendment у ADR-0066 з note "Drawing UX tokens added rev N+1, see ADR-0074". **Не блокує ADR-0074 ship**.
- **Поважає**: ADR-0072 (mobile canonical layout — left-side не локований, toolbar поза jurisdiction), ADR-0073 (LWC overlay rules — drawings не зачіпають price scale strip)
- **Soft-блокує**: **ADR-B** (drawings persistence уніфікація — UDS namespace `drawings/{symbol}/{tf}/`, прибере noop `sendAction` у ChartPane.svelte:205), **ADR-C** (SMC auto-zones як read-only overlay у тому ж drawing-layer через `kind: 'auto_smc'` + `editable: false` flag — reuses ToolModule render path БЕЗ створення FSM)
- **Cited у Research**: `outputs/Trader_Tools_Research.docx` §4 (Universal Alert Engine — ADR-D pillar A), §8 (Premium thesis pillars B-E)

---

## Out of scope (свідома відмова — V1)

- **+5 нових drawing tools** (h_ray, ray, channel, text, fib_retracement) — **ADR-B territory**. Tool Registry уже готовий приймати їх.
- **Drawing-based alerts** (alert-on-touch для Fib/trend/rectangle) — **ADR-D territory** (Universal Alert Engine з Research).
- **SMC auto-zones reuse у drawing-layer** — **ADR-C territory**.
- **Backend persistence для drawings** (UDS namespace, conflict resolution, multi-device sync) — **ADR-B territory**.
- **Custom DOM-based drawing tools** (text annotation з contenteditable) — defer; canvas-based label досить для V1.
- **Drawing share/export** (JSON export, image snapshot з drawings) — defer.
- **Drawing templates** (preset стилів — color/lineWidth) — defer; `meta.color`/`meta.lineWidth` уже у domain model, UI пізніше.

---

## Forbidden patterns (anti-drift)

> Що НЕ робити у T1-T6, і що не приймати у code review.

| ID | Заборонено | Чому |
|---|---|---|
| FP1 | Створювати `core/drawings/` | I0 (Dependency Rule); user-input domain ≠ core/ |
| FP2 | Дублювати `resolve_anchor_offset_ms()` у frontend | X35; `t_ms` у Drawing уже canonical |
| FP3 | Inline `if (d.type === 'hline') ... else if (d.type === 'trend')` у renderer | Тому що це і прибираємо у T1; додавання нового — через registry |
| FP4 | Magic numbers у hit-test (`6`, `3.5`, `5`) | X36; читати з cached CSS-var |
| FP5 | Hotkey handler з mixed abstraction (Ctrl-combos + tool select + diagnostics) у одному if-ланцюгу | X37; розділ на mapper + handler |
| FP6 | Unicode гліфи `━ ╱ ▭ ✕` як іконки tool | Visual placeholder regression; X39 |
| FP7 | Right-side toolbar position | Зайнято ADR-0072 (☰ та CommandRail) |
| FP8 | Bottom-CENTER / bottom-RIGHT toolbar position | Резерв NarrativeSheet (ADR-0075+ forward-ref у ADR-0073 §"Що залишається з рев 2 amendments") |
| FP9 | `localStorage` доступ напряму з нових файлів (поза існуючого `DrawingsRenderer` storage layer) | Single ownership — DrawingsRenderer власник local persistence до ADR-B |
| FP10 | `getComputedStyle()` у hit-test loop | Browser style recalc per call → 60Hz performance regression |

---

## Open questions — RESOLVED (rev 1.1, 2026-05-12)

| ID | Питання | Resolution |
|---|---|---|
| OQ1 | Lucide vs Heroicons vs inline SVG? | **Lucide first** — T3 prototype gate ≤+8KB net bundle (tree-shaken 4-5 icons очікувано <8KB). Fallback inline SVG (5×~400B = 2KB total) якщо Lucide перевищує. |
| OQ2 | Чи додаємо `cursor` як 5-й tool у Toolbar? | **Yes** — UI-only button для "no tool" mode. Clarity для нових користувачів; ~10 LOC у T3. |
| OQ3 | Mobile bottom-left позиціонування — `bottom: 16px; left: 8px` чи `bottom: env(safe-area-inset-bottom)`? | **`env(safe-area-inset-bottom)`** для PWA (ADR-0071) compliance — iOS notch + Android gesture-bar. |
| OQ4 | Магнет default state у V1 (зараз OFF) — змінити на ON для UX? | **OFF** (backward compat). Існуючі users не отримують сюрпризу; ADR-B з telemetry може переключити пізніше. |

---

## Implementation phasing

| Slice | Залежності | Estimate (focused dev hours) |
|---|---|---|
| T1 | — | 8-12 (architecture-heavy) |
| T2 | T1 (cached vars читаються у new renderer code) | 2-3 |
| T3 | T2 (CSS tokens) | 6-8 (visual polish + Lucide integration) |
| T4 | T3 (toolbar готовий приймати props) | 1-2 (re-enable) |
| T5 | — (паралельний T1-T4) | 3-4 |
| T6 | T1-T5 | 4-6 |
| **Total** | — | **24-35 hours = 3-5 робочих днів** |

Mostly conservative — двотижневий бюджет дає buffer на code review + iteration.

---

## Audit log

### Discovery findings (2026-05-12, грунтований на mounted repo, не з памʼяті)

- **D1**: `DrawingsRenderer.ts` 890 LOC — production-ready engine. Inline switches у 3 місцях: hit-test (lines 414-433), render (lines 788-832), drag (lines 686-704). Це і централізуємо у Registry.
- **D2**: `CommandStack.ts` 88 LOC — undo/redo + WS sync wired, але `sendAction: () => {}` noop у ChartPane.svelte:205. Backend свідомо вимкнено — підтверджує ADR-B необхідність.
- **D3**: `HIT_TOLERANCE_PX = 6` у `geometry.ts:4` — нижче WCAG 2.1 AA. Підвищуємо до 10 desktop / 16 mobile у T2.
- **D4**: `DrawingToolbar.svelte:142-143` — buttons 22×22 (нижче WCAG 24×24 min, далеко від 44×44 AAA target).
- **D5**: `App.svelte:239-285` — handleGlobalKeydown 47 LOC, mixed abstraction (Ctrl-combos + drawing + diagnostics + magnet defer). X37 candidate.
- **D6**: `App.svelte:246-250` input-focus guard покриває HTMLInputElement/HTMLTextAreaElement але **НЕ contenteditable**. Edge case: якщо ADR-C введе text-annotation з contenteditable, hotkey "h" з'їсть набраний "h". Fix у T5 store через `el.isContentEditable`.
- **D7**: `runtime/ws/ws_server.py:444` — drawings виключно empty array у WS frames; жодного handler-а у `runtime/` — підтверджено grep. localStorage = єдина persistence.
- **D8**: `ui_v4/package.json` має Vitest 2.0; Playwright відсутній. Test stack для T6 = Vitest only.
- **D9**: `App.svelte:123` `magnetEnabled` store вже існує і має loadMagnet/saveMagnet з localStorage; UI заблоковано тільки у DrawingToolbar — T4 одностайно прості 30 LOC.

### ADR review checklist (R_REJECTOR / R_BUG_HUNTER critique-prep)

Перед transition Proposed→Accepted прогнати:

- [ ] Чи `core/drawings/` зустрічається у файлах ADR? **NO** — only mention у Forbidden patterns FP1 + explicit reject у V-D
- [ ] Чи Quality Axes відповідають reality (R2 conservative, M3→M3.5 partial)? **YES**
- [ ] Чи кожна заявка в Decision підтверджена evidence marker з line number? **YES** — D1-D9 у Audit log
- [ ] Чи Rollback per slice реалістичний (single revert)? **YES** — кожен slice окремий PR
- [ ] Чи ADR-B/ADR-C/ADR-D properly soft-blocked, не уявно blocked? **YES** — Out of Scope explicit
- [ ] Чи Bundle impact замірений (≤ +25KB net)? **CONDITIONAL** — Lucide measurement у T3 prototype gate
- [ ] Чи Verify-матриця відповідає A7 (medium scope, 2 перевірки)? **YES** — Vitest + manual matrix

---

## Маркери

- `[VERIFIED path:line]` — line numbers відповідають фактичному стану файлів у репо
  станом на 2026-05-12 commit `33b383b` (mounted: `C:\Users\vikto\aione-context\v3`).
  Spot-checked у rev 1.1: App.svelte:123/239/246/267/283, ChartPane.svelte:205,
  ws_server.py:444, DrawingsRenderer.ts:890, geometry.ts:52 (ADR cited 53 — off
  by 1 trailing newline, no semantic drift).
- `[INFERRED]` — логічний висновок з verified-фактів
- `[ASSUMED]` — none (всі факти прив'язані до коду)
- `[UNKNOWN]` — none (OQ1-OQ4 resolved у rev 1.1)

Якщо при імплементації виявиться mismatch line numbers → STOP, перечитати файл, оновити ADR у rev 2 з changelog row.

---

## Changelog

| Rev | Дата | Зміни |
|---|---|---|
| 1 | 2026-05-12 | Initial draft (PROPOSED). Discovery-based 6-slice plan з ToolModule contract. |
| **1.1** | **2026-05-12** | **ACCEPTED** після R_REJECTOR review: HEAD hash filled (`33b383b`), OQ1-OQ4 resolved (Lucide ≤+8KB / cursor as 5th tool / env() safe-area / magnet default OFF), authors extended. No contract changes. |
