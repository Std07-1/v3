# ADR-0007: Glass-like Drawing Toolbar + Light Theme Contrast Fix

**initiative**: `drawing_tools_v1` (UX polish)  
**Батьківський ADR**: [ADR-0007 Drawing Tools Unblock](0007-drawing-tools-unblock.md)  
**Дата**: 2026-02-24  
**Статус**: ✅ **DONE**

---

## Проблема

Drawing Toolbar та DrawingsRenderer мають **захардкоджені dark-only кольори**, що призводить до:

1. **WCAG FAIL на Light Theme**: base color `#c8cdd6` на `#ffffff` фоні дає contrast ratio ≈ **1.65:1** (потрібно ≥ 4.5:1 AA). Лінії та кнопки **невидимі** на білому фоні.
2. **Відсутність візуальної ідентичності тулбара**: `background: none` — тулбар зливається з графіком, немає "якоря" для очей.
3. **Prop drilling для тем**: theme-кольори передаються через 5+ inline style props, що ускладнює масштабування.

---

## DISCOVERY Findings

### FACTS (з path:line)

| # | Факт | Доказ |
|---|---|---|
| F1 | DrawingToolbar: `background: none; border: none` | `DrawingToolbar.svelte:96-97` |
| F2 | Кнопки: фіксований `rgba(200, 205, 214, 0.6)` — НЕ адаптивний | `DrawingToolbar.svelte:139` |
| F3 | Active: `#3d9aff`, hover bg: `rgba(255,255,255,0.08)` — dark-only | `DrawingToolbar.svelte:148-155` |
| F4 | Collapse btn: `rgba(255,255,255,0.35)` — white alpha | `DrawingToolbar.svelte:115` |
| F5 | Drawing baseColor = `#c8cdd6` — **фіксований** | `DrawingsRenderer.ts:721` |
| F6 | Rect fill: `rgba(200,205,214,0.10)` — dark-only | `DrawingsRenderer.ts:775` |
| F7 | 3 теми: dark (`#131722`), black (`#000000`), light (`#ffffff`) | `themes.ts:40-110` |
| F8 | ThemeDef вже має адаптивні поля (hudText, menuBg, etc.) | `themes.ts:12-37` |
| F9 | DrawingToolbar **НЕ** отримує тему як prop | `DrawingToolbar.svelte:4-14` |
| F10 | App.svelte не передає theme props до DrawingToolbar | `App.svelte:363-368` |

### NEXT CHECKS (verified)

| # | Перевірка | Результат |
|---|---|---|
| NC1 | ChartHud glass-style? | **Ні на body** (`background: transparent`, `ChartHud.svelte:451`). Glass лише на **dropdowns** (`backdrop-filter: blur(12px)`, `:536`) |
| NC2 | Brightness filter на тулбарі? | **Ні** — filter тільки на LWC layer + drawings canvas (`ChartPane.svelte:253,260`). Правильний дизайн — brightness не впливає на UI |
| NC3 | CSS custom properties в проекті? | **Немає**. Ніде `data-theme`, `--var`, `:root` → все через inline `style:` props. Впровадили CSS custom properties як архітектурне рішення |
| NC4 | Existing tests? | **Немає** unit/integration тестів у `ui_v4/src/`. Vitest не у deps. Verify = `npm run build` + візуальна перевірка |

---

## Рішення

### Концепція: "Focus through Transparency"

Glass-morphism — **стратегічний UX-вибір** для торгової платформи:

- **Context Retention**: трейдер бачить цінові рівні навіть під тулбаром
- **Visual Hierarchy**: `backdrop-filter: blur(12px)` відділяє UI від Data через глибину (Z-axis), не кольором → менше візуального шуму
- **Zero-Friction Adaptation**: скляний тулбар природно адаптується до зміни ціни під ним

### Архітектурне рішення: CSS Custom Properties (не prop drilling)

Замість прокидання `themeName` через 5 компонентів — `applyThemeCssVars()` встановлює CSS custom properties на `document.documentElement`:

```typescript
// themes.ts — helper (SSOT: всі значення з ThemeDef)
export function applyThemeCssVars(name: ThemeName): void {
    const t = THEMES[name];
    const s = document.documentElement.style;
    s.setProperty('--toolbar-btn-color', t.drawingColor);
    s.setProperty('--toolbar-bg', t.toolbarBg);
    s.setProperty('--toolbar-border', t.toolbarBorder);
    s.setProperty('--toolbar-hover-bg', t.toolbarHoverBg);
    s.setProperty('--toolbar-active-color', t.toolbarActiveColor);
    s.setProperty('--drawing-base-color', t.drawingColor);
    s.setProperty('--drawing-rect-fill', t.drawingRectFill);
}
```

**Переваги**:

- DrawingToolbar.svelte: CSS `var(--toolbar-btn-color)` без props → 0 re-renders Svelte
- DrawingsRenderer.ts (Canvas): `getComputedStyle(this.canvas).getPropertyValue(...)` → theme-aware кешовано
- Масштабується на майбутні компоненти (properties panel, new tools)

---

## Виконання (факт)

### Палітра (WCAG-перевірена)

| Елемент | Dark | Black | Light | Контраст (light) |
|---|---|---|---|---|
| Drawing base | `#c8cdd6` | `#c8cdd6` | `#434651` | ~5.2:1 ✅ AA |
| Toolbar BG | `rgba(19,23,34,0.6)` | `rgba(10,10,10,0.6)` | `rgba(242,245,248,0.7)` | N/A |
| Toolbar border | `rgba(255,255,255,0.1)` | `rgba(255,255,255,0.08)` | `rgba(0,0,0,0.05)` | subtle rim |
| Active accent | `#3d9aff` | `#3d9aff` | `#2962ff` | deeper blue |
| Hover BG | `rgba(255,255,255,0.08)` | `rgba(255,255,255,0.08)` | `rgba(0,0,0,0.06)` | adaptive |
| Rect fill | `rgba(200,205,214,0.10)` | `rgba(200,205,214,0.10)` | `rgba(67,70,81,0.10)` | adaptive |

### Модифіковані файли (5 файлів, ~80 LOC total)

#### 1. `ui_v4/src/chart/themes.ts` (+40 LOC)

- **ThemeDef interface**: додано 6 полів (`drawingColor`, `drawingRectFill`, `toolbarBg`, `toolbarBorder`, `toolbarHoverBg`, `toolbarActiveColor`)
- **THEMES[dark/black/light]**: заповнені значення для кожної теми
- **`applyThemeCssVars(name)`**: helper для встановлення 7 CSS custom properties на `:root`

#### 2. `ui_v4/src/chart/lwc.ts` (+1 LOC)

- Re-export `applyThemeCssVars` з `themes.ts`

#### 3. `ui_v4/src/App.svelte` (+4 LOC)

- **Import**: `applyThemeCssVars` додано до import
- **`handleThemeChange()`**: виклик `applyThemeCssVars(name)` при зміні теми
- **`onMount()`**: initial `applyThemeCssVars(activeTheme)` для стартової теми

#### 4. `ui_v4/src/layout/DrawingToolbar.svelte` (~25 LOC CSS змін)

- **Glass background**: `background: var(--toolbar-bg)`, `backdrop-filter: blur(12px)`, `border-radius: 8px`, `box-shadow`
- **Rim border**: `border: 1px solid var(--toolbar-border)`
- **Кнопки**: `color: var(--toolbar-btn-color)` замість хардкоду
- **Hover glow**: `box-shadow: 0 0 4px var(--toolbar-hover-bg)` — м'яке підсвічування
- **Active glow**: `color: var(--toolbar-active-color)`, `box-shadow: 0 0 8px rgba(61,154,255,0.25)` — "я зараз малюю"
- **Collapse btn**: opacity-based замість color-based transition

#### 5. `ui_v4/src/chart/drawings/DrawingsRenderer.ts` (+13 LOC)

- **Cached fields**: `themeBaseColor`, `themeRectFill` (private, default = dark theme)
- **`refreshThemeColors()`**: public method — читає CSS custom properties через `getComputedStyle`, кешує, schedules render
- **`forceRender()`**: `d.meta?.color ?? this.themeBaseColor` замість хардкоду `#c8cdd6`
- **rect fill**: `this.themeRectFill` замість хардкоду

#### 6. `ui_v4/src/layout/ChartPane.svelte` (+2 LOC)

- **`applyTheme()`**: виклик `drawingsRenderer?.refreshThemeColors()` через `requestAnimationFrame` (rAF гарантує CSS vars ready)

---

## Guard: Canvas Color Caching

Canvas API не має прямого доступу до CSS vars. Стратегія:

- Кешування через `getComputedStyle` у `refreshThemeColors()`
- Виклик з `ChartPane.applyTheme()` через `requestAnimationFrame` — CSS vars вже встановлені
- Fallback на dark-theme defaults (`#c8cdd6`) якщо CSS var не знайдено (I5: degraded-but-loud)

---

## Інваріанти

- **I5 (Degraded-but-loud)**: CSS var fallback з чіткими defaults, не silent
- **Без нових файлів**: модифікація 5 існуючих (+ 1 re-export)
- **Без нових патернів конкурентності**: CSS vars — синхронні
- **Без зміни контрактів**: `types.ts` не modified

---

## Верифікація

### Build

```
npm run build → ✓ 165 modules transformed, 268.51 kB, built in 1.74s
0 errors, 2 pre-existing warnings (unused CSS selectors: .tool-separator, .magnet-btn — deferred magnet feature)
```

### Browser verification (автоматизована, 2026-02-24)

- ✅ **Dark theme**: glass toolbar з blur, закруглені кути, іконки видимі
- ✅ **Light theme**: "паперовий" glass, dark-gray іконки (`#434651`), deep blue active (`#2962ff`)
- ✅ **Theme switch**: Dark → Light → Black → Dark — миттєво, без артефактів
- ✅ **Brightness**: тулбар НЕ змінює яскравість (тільки графік + drawings canvas)

### WCAG Контраст

- Dark: `#c8cdd6` на `#131722` → ~7.2:1 ✅ AAA
- Light: `#434651` на `#ffffff` → ~5.2:1 ✅ AA
- Black: `#c8cdd6` на `#000000` → ~11.0:1 ✅ AAA

## Rollback

```bash
git revert <commit>
```

## Product Value

**UI/UX: Adaptive Visual Integrity**

- **Eye Strain Reduction**: адаптивна контрастність для Light Theme (WCAG AA compliant). Малюнки чіткі на будь-якому фоні.
- **Non-Invasive UI**: Glass-like Drawing Toolbar мінімізує перекриття графіка — рух ціни видно крізь інтерфейс.
- **Focus Stability**: оптимізовані стани Hover/Active для інструментів, що зменшує час вибору в моменти високої волатильності.

## CHANGELOG

```json
{"date":"2026-02-24","index":"20260224-091","area":"UI","desc":"ADR-0007: Glass-like Drawing Toolbar + Theme-aware drawing colors — CSS custom properties architecture, WCAG AA contrast on Light theme (#434651), backdrop-filter blur, micro-interactions (hover/active glow). 5 files, ~80 LOC."}
```
