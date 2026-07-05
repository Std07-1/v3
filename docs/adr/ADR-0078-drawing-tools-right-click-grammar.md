# ADR-0078: Drawing Tools Right-Click Grammar — Left-Button Guard + Figure Context Menu (Delete / Color)

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0078 |
| Статус | **IMPLEMENTED** (2026-07-05) |
| Дата | 2026-07-05 |
| Автори | Станіслав (owner, live co-design) + Opus 4.8 (implementation + live verification) |
| Розширює | ADR-0074 (Drawing Tools V1 — registry/DrawingsRenderer/CommandStack лишаються в силі), ADR-0077 (V2 icon-only toolbar) |
| Поважає | ADR-0005 (drawings client-only — sendAction noop), ADR-0072 (mobile: drawing tools сховані) |
| Зачіпає шари | `ui_v4/src/chart/drawings/DrawingsRenderer.ts` (guard + contextmenu seam + public deleteById/recolorById), `ui_v4/src/types.ts` (**+DrawingContextRequest**), `ui_v4/src/layout/DrawingContextMenu.svelte` (**NEW**), `ui_v4/src/layout/ChartPane.svelte` (wiring + render поза interactionEl), `ui_v4/src/chart/drawings/tools/{Trend,HLine,Rect}Tool.ts` (color-preserving hover/select — D6) |
| Initiative | `drawing_tools_v2` (продовжує 0074/0077) |

---

## Quality Axes

- **Ambition target**: **R2** — нова взаємодія-verb (right-click) поверх наявного tool-registry + один малий UI-primitive (`DrawingContextMenu`). Без нової backend-поверхні (drawings client-only, ADR-0005). Детач/перетягування (справжній R3) лишається окремим майбутнім ADR.
- **Maturity impact**: **M3 → M3.5** — елевація: (1) закрито реальний баг (right-click під час draft мовчки комітив фігуру); (2) видалення/колір фігури тепер через ту саму undoable CommandStack, що й гумка/drag (нуль нового state-шляху); (3) contextmenu-логіка інкапсульована в renderer, UI-шар = dumb renderer меню (X28-сумісно). Знижень немає.

---

## Контекст

ADR-0074/0077 дали малювання (hline/trend/rect), гумку, drag-select і undo/redo через `CommandStack`. Проте `onPointerDownCapture` не фільтрував кнопку миші: **будь-яка** кнопка потрапляла у draw/select/drag-гілки. Наслідок — реальний баг: right-click під час активного draft потрапляв у `finishDraft()` і **комітив фігуру мимоволі** (dual-fire: браузер на right-click шле `pointerdown{button:2}` → `pointerup` → `contextmenu`; перший з них і робив коміт). Middle-click так само стартував draft/selection.

Owner-фідбек (live-сесія 2026-07-05): треба (а) прибрати цей баг, (б) дати праву кнопку як «граматику» — right-click під час draft **скасовує** незакомічену фігуру, а right-click на **закомічену** фігуру відкриває міні-меню «Видалити / Колір». Все верифіковано наживо реальним `page.mouse` (не синтетика — саме тому, що синтетичний лише-`contextmenu` не відтворює dual-fire, від якого і потрібен guard).

---

## Рішення

### D1. Load-bearing guard: лише ліва кнопка малює/вибирає/тягне
Перший рядок `onPointerDownCapture`:
```ts
if (e.button !== 0) return;
```
Touch/pen primary contact = `button 0` → проходить (mobile drag наявних фігур не ламається). Middle (1) / right (2) → ігноруються цим шляхом. Без guard right-click під час draft → `finishDraft()` → мимовільний коміт.

### D2. Contextmenu-listener у renderer (5-й capture-listener, той самий `interactionEl`)
Реєструється в `setupInteractionsCapture()`, знімається в `destroy()` — консистентно з pointer-листенерами. Пріоритет:
1. **Owner-рішення**: `e.preventDefault()` **безумовно** — right-click на чарті НІКОЛИ не показує native-меню браузера (консистентний pro-tool-філ, як TradingView).
2. `draft` активний → `cancelDraft()`, вихід (симетрія до Escape).
3. hit-test у фігуру → виділяємо її (`selectedId`) + шлемо `onContextMenu({id, screenX, screenY, color})` у UI-шар.
4. промах по порожньому → `onContextMenu(null)` (закриває будь-яке відкрите меню — `dismissOnOutside` реагує на click/Escape, але **не** на right-click).

### D3. Public seam без constructor-churn
`public onContextMenu: ((req: DrawingContextRequest | null) => void) | null = null` — settable field (не 7-й constructor-параметр). `hit-test` лишається **private**; renderer сам обробляє contextmenu і віддає UI лише готовий request. Плюс два public-методи, симетричні до `cancelDraft`:
- `deleteById(id)` → `commandStack.push({type:'DELETE', ...})` (та сама команда, що й гумка).
- `recolorById(id, color|null)` → `commandStack.push({type:'UPDATE', prev, next})` з `meta.color`; `null` = прибрати override (повернутись до кольору теми). Renderer уже рендерить `d.meta?.color ?? themeBaseColor`.

### D4. `DrawingContextMenu.svelte` — інлайн-свотчі (owner-вибір)
`position:fixed` у екранних координатах курсора, clamp у viewport (`MENU_W/MENU_H/GAP`). Рядок «Видалити» (trash-icon) + ряд 6 крапок: `Тема`(null), `Золото #D4A017`, `Червоний #EF5350`, `Зелений #26A69A`, `Синій #42A5F5`, `Оранж #FFA726`. Активний колір обведений кільцем; нейтраль = dashed-контур + колір теми. Dismiss через переюз `dismissOnOutside` (click-поза / touch / Escape). UI-шар = dumb renderer: лише викликає public deleteById/recolorById, істина лишається в renderer/CommandStack (X28).

---

### D5. Frosted-glass premium surface (owner live-review 2026-07-05)
Первинний вигляд меню (solid `#0d1117` box + flat circles) owner забракував як
generic + хардкодив dark-кольори (ламався б на light-темі). Переписано на
house-токени ADR-0066 (theme-aware dark/black/light): `color-mix(--card 76%)` +
`backdrop-filter: blur(18px) saturate(1.5)`, hairline-border від `--text-1`,
layered shadow + inset top-highlight, Inter (`--font-sans`), entry-анімація
(scale+fade 130ms). Палітра дзеркалить семантичні токени: `--accent`/`--bear`/
`--bull`/`--info`/`--warn` (concrete hex — canvas не читає CSS-vars). Active-свотч —
кільце нейтральним `--text-1` (не золотом), щоб не збігатись зі золотим свотчем.

### D6. Color-preserving hover/select — root-fix колізії кольору (owner «баг #1»)
Owner помітив: right-click на фігурі → лінія золота, а меню active-свотч = Тема
(«меню бреше»). Корінь глибший за виділення: `TrendTool/HLineTool/RectTool`
рендерили `isDraft||isHovered||isSelected → accentColor` (золото), тобто hover/select
**перефарбовував** фігуру, ховаючи справжній колір + колізуючи зі золотим свотчем.
Live round-trip (canvas `getImageData`) довів: `meta.color=#ED4554`, а полотно
`#d4a017`. Фікс: committed-фігури лишають свій колір (`baseColor = meta.color ??
theme`), стан hover/select = **glow (`shadowBlur`) + товщина**, не accent. Draft
(ще без кольору) лишається accent-пунктиром. Контекст-хендлер більше не виділяє
примусово — ціль природно під hover (glow), без lingering selection. Доведено:
при відкритому меню + курсор на лінії золото ВІДСУТНЄ (`getImageData`: лише
`#ed4554`), active-свотч = полотно.

## Alternatives (розглянуто, відхилено)

1. **Contextmenu-логіка у Svelte + public `performHitTest`** — відхилено: leaks draft-state + hit-test + більше public-поверхні, і два власники contextmenu-логіки. → listener у renderer (owns interactionEl + hit-test + commandStack), один callback назовні (D2/D3).
2. **Підменю «Колір ▸»** замість інлайн-свотчів — відхилено owner-ом: 2 кліки до кольору проти 1. → інлайн (D4). (Підменю краще масштабується під товщину/lock — якщо додаватимемо, тоді revisit.)
3. **Пропускати native-меню на порожньому чарті** — відхилено owner-ом: непослідовно (інколи app-меню, інколи браузерне). → безумовний preventDefault на чарті (D2). Мінус (dev-inspect правим кліком по чарту) прийнято; F12 лишається.
4. **7-й constructor-параметр для callback** — відхилено: churn call-site + обов'язковість. → optional settable field (D3).
5. **Окреме поле кольору поза `meta`** — відхилено: `Drawing.meta.color` уже існує і рендериться. → переюз через UPDATE (D3).
6. **Колізію кольору фіксити лише в межах меню** (ctxMenuId-гейт у render) — відхилено: stateful, fragile (re-hover при русі до свотча знову золотив), лікує симптом. → color-preserving hover/select у джерелі (D6) — колізія структурно неможлива скрізь.
7. **Клірити hovered+hoverDirty у контекст-хендлері** (targeted) — відхилено: `scheduleRender` все одно перераховував hover назад із `lastCursor` → золото поверталось. → D6.

---

## Consequences

**Позитив:** закрито реальний баг мимовільного коміту; права кнопка = передбачувана граматика (cancel / menu); видалення+колір через наявну undoable CommandStack (Ctrl+Z відкочує обидва — live-verified); нуль нової backend-поверхні; contextmenu інкапсульовано, меню = dumb renderer.

**Ризики / межі:**
- **Native-меню задушене на всьому чарті** — right-click-inspect по канвасу недоступний (owner-рішення D2; F12 лишається).
- **Mobile**: drawing tools сховані (`@media`, ADR-0072); contextmenu — desktop-first. Touch long-press-меню — поза scope.
- Палітра — фіксовані 6; довільний колір (picker) не в scope.
- Right-click **виділяє** фігуру (`selectedId`) — лишається виділеною після dismiss без дії (як звичайний select).

**Live-verified (реальний `page.mouse`, 0 console-errors):** guard (лівий 2-клік малює / правий у draft НЕ комітить — доведено контролем: ті самі коорд. з лівим 2-м кліком комітять); меню на фігурі (6 свотчів + delete); recolor (UPDATE → `meta.color`) + revert-to-theme; delete (→0); dismiss ×3 (action / Escape / click-поза); undo ×2 (delete-restore + recolor-revert); порожній right-click → без меню; native задушено (`defaultPrevented=true` ×4).

---

## Константи (крутилки для майбутнього maintainer)

| Група | Константа | Значення | Файл |
| --- | --- | --- | --- |
| Позиція меню | `MENU_W` / `MENU_H` / `GAP` | 172 / 84 / 8 px | DrawingContextMenu.svelte |
| Палітра (token-mirror) | `PALETTE` | Тема(null)/#D4A017(--accent)/#ED4554(--bear)/#22CC8F(--bull)/#5487FF(--info)/#FFA726(--warn) | DrawingContextMenu.svelte |
| Glass | blur / `--card` opacity | 18px / 76% | DrawingContextMenu.svelte |
| Highlight (D6) | `shadowBlur` / `lineWidth+` | 6 / +1 px | {Trend,HLine,Rect}Tool.ts |
| Guard | `e.button !== 0` | лише ЛКМ | DrawingsRenderer.ts `onPointerDownCapture` |

---

## Rollback

Погранульно, все reversible:
- **Guard (D1)**: видалити `if (e.button !== 0) return;` → повернення до старої (баґової) поведінки. Guard самодостатній.
- **Меню (D2-D4)**: видалити contextmenu-listener + `onContextMenu`/`deleteById`/`recolorById` у renderer, `DrawingContextMenu.svelte`, wiring у ChartPane, `DrawingContextRequest` у types. Guard можна лишити окремо (він закриває баг незалежно від меню).
- `meta.color`-значення на фігурах — сумісні назад (рендер `?? themeBaseColor`); жодної міграції.

---

## Майбутня робота

- **P3 Детач/перетягування** (серце ідеї owner-а, окремий ADR) — див. ADR-0077 §Майбутня робота.
- Товщина лінії / lock через те саме меню (`meta.lineWidth` / `meta.locked` уже в типі) — тоді, ймовірно, перехід на підменю (Alt-2).
- Touch long-press context menu (mobile).
