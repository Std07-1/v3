# ADR-0080: Drawing Tools — Surface-2 Style Flyout (Semantic Color Roles + Live-Preview)

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0080 |
| Статус | **Implemented** — гранульовані ряди (колір/товщина/стиль) в обох режимах, live-verified (owner live co-design, 2026-07-06) |
| Дата | 2026-07-06 |
| Автори | Станіслав (owner, live co-design) + Opus 4.8 (implementation + live verification) |
| Розширює | ADR-0074 (Drawing Tools V1), ADR-0078 (right-click грамат.), ADR-0079 (delicate object — поверхня-1) |
| Поважає | ADR-0005 (drawings client-only), ADR-0066 (house-токени) |
| Замінює частину | ADR-0078 `DrawingContextMenu` (колірні крапки поглинуто цим flyout; файл + `recolorById` видалено) |
| Зачіпає шари | `ui_v4/src/chart/drawings/{DrawingsRenderer.ts, colorRoles.ts, lineStyles.ts, tools/*Tool.ts}`, `ui_v4/src/layout/{DrawingStyleFlyout.svelte, DrawingToolbar.svelte, ChartPane.svelte}`, `ui_v4/src/App.svelte`, `ui_v4/src/types.ts` |
| Initiative | `drawing_tools_v2` |

---

## Quality Axes

- **Ambition target**: **R3** — нова конфіг-поверхня з **семантичною моделлю кольору** (роль→токен, theme-aware), а не сирим hex; live-preview на справжньому об'єкті (чого TradingView не робить).
- **Maturity impact**: **M3.5 → M4** — колір як роль резолвиться під тему; один узагальнений meta-patch шлях (`previewMeta`/`updateMetaById`) для колір/товщина/стиль; SSOT-модулі (`colorRoles`, `lineStyles`) з юніт-тестами; мертвий код (`DrawingContextMenu`, `recolorById`) прибрано. Знижень немає.

---

## Контекст

ADR-0079 зафіксувала **дві розведені поверхні** інструментів малювання: (1) на об'єкті — делікатне пряме маніпулювання наведенням, БЕЗ налаштувань; (2) конфігурація (колір/товщина/стиль) — окремо, у лівому тулборі. Ця ADR реалізує **поверхню-2**.

Метод — той самий, що в ADR-0078/0079: **не мокапи, а жива реальність** (owner live co-design). Обговорення словами → мала реальна зміна в живому чарті → live-verify (Playwright: реальний `page.mouse`, canvas `getImageData` як ground-truth, `localStorage`) → тюнинг. Owner-принцип: панель — тимчасова тиха присутність, що **не краде увагу від чарту**.

**Owner-затверджені рішення моделі** (перед кодом):

- **Колір = семантична РОЛЬ**, не concrete hex. Роль резолвиться у токен-хекс при рендері → theme-aware + заголовок-«смисл» тривіальний.
- Включаємо: **live-preview на об'єкті** (найсильніше), пам'ять-per-tool + наміри-перші пресети (наступні фази).
- Порядок збірки: оболонка → колір → товщина → стиль.
- **Об'єктний вхід**: right-click на самій фігурі відкриває цей flyout з live-preview + «Видалити», **поглинаючи** колірні крапки ADR-0078 (єдина багата поверхня recolor).

---

## Рішення (поверхня-2 — style flyout)

### D1. Один компонент, два режими
`DrawingStyleFlyout.svelte` монтується двічі:
- **tool-режим** (App.svelte) — відкривається right-click на іконці інструмента в тулборі; керує **дефолтом** цього інструмента (нові фігури). Без live, без Delete.
- **object-режим** (ChartPane.svelte) — відкривається right-click на **фігурі**; live-preview на ній + рядок «Видалити». Заміняє монтування `DrawingContextMenu` (ADR-0078).

Заголовок = **смисл поточного кольору** (нейтраль/акцент/бик/…), тонований у той колір, + жива лінія-зразок. Frosted-glass на house-токенах ADR-0066 (theme-aware). Owner live-тюн «не пляма»: `--card` **46 %** alpha (тримається на `blur(22px)`, не на щільному фоні), тонка тінь, шрифт 450 + opacity 0.88, тонкі бари/chips.

### D2. Колір = семантична роль (SSOT `colorRoles.ts`)
6 ролей: `neutral`(`--drawing-base-color`) · `accent`(`--accent`) · `bull`(`--bull`) · `bear`(`--bear`) · `info`(`--info`) · `warn`(`--warn`). Один список `DRAWING_COLOR_ROLES` ітерують **обидва** споживачі — палітра flyout (CSS `var(--*)`) і canvas-рендер (`buildRoleColorMap()` через `getComputedStyle`, бо canvas не читає `var()` напряму). D15.2: єдине джерело → UI-палітра й полотно не розсинхронюються. `meta.colorRole` перекриває legacy `meta.color` (hex-fallback для старих фігур); `neutral` = база теми → **не пишемо** meta (theme-aware, legacy-сумісно). Палітра — **лінії-бари**, не кружки (правдиво до об'єкта).

### D3. Товщина + стиль
- **Товщина**: chips 1–4px, прев'ю лінії у **поточному кольорі** (правдиве комбо). `meta.lineWidth` вже читався рендером — додано лише UI.
- **Стиль** (SSOT `lineStyles.ts`): chips solid/dashed/dotted. `meta.lineStyle` → `ctx.setLineDash` у всіх 3 `tool.render` через спільний `dashPattern(style, lineWidth)` (візерунок масштабується товщиною). **dotted = canonical `[0, gap]` + `lineCap='round'`** — нульовий штрих із круглим капом дає круглу крапку; наївний `[w, w*2]` зливався в суцільну на antialiasing (спіймано live getImageData).

### D4. Live-preview + undoable commit (узагальнений meta-patch)
Один шлях для колір/товщина/стиль (F9 — не дублювати):
- `previewMeta(id, patch|null)` — **transient** накладання patch на фігуру БЕЗ commit; `previewOrig` тримає оригінал meta (при першому дотику), `patch=null` → точний відкат. Наведення ролі/товщини/стилю у flyout → жива фігура на чарті міняється одразу; вихід з ряду (`onmouseleave`) → відкат.
- `updateMetaById(id, patch)` — **undoable** UPDATE через `CommandStack`; спершу знімає активний preview (commit від ОРИГІНАЛУ, не від прев'ю-стану), потім пушить. `colorRole` у patch перекриває legacy hex (`applyMetaPatch`).

Абстрактний зразок у заголовку лишається лише коли об'єкт НЕ вибрано (= дефолт інструмента); на об'єкті працює правдивий live-preview.

### D5. Об'єктний вхід поглинає ADR-0078
Right-click на фігурі → renderer `onContextMenu({id, colorRole, lineWidth, lineStyle, screen…})` → ChartPane object-flyout. `DrawingContextMenu.svelte` + renderer `recolorById` **видалено** (dead code, F9). «Видалити» переїхало у flyout (той самий undoable `deleteById`, що гумка/× ADR-0079).

### D6. Dismiss-граматика (own window-capture)
Перший `pointerdown` **поза** flyout (ліва АБО права кнопка) закриває. Якщо клік прийшовся по `.chart-container` — **поглинається** (`preventDefault`+`stopPropagation` на window-capture, що спрацьовує ДО capture-хендлерів малювання на `interactionEl`) → жодного draft/commit/pan «зайвим» кліком («я ще не готовий» — owner). Клік по іншому UI (тулбар/HUD) проходить далі — перемкнути інструмент одним кліком. Escape закриває (stopPropagation, не летить у draft-cancel). Замінив `dismissOnOutside` у flyout (той реагує лише на `click` і не вміє поглинати `pointerdown`).

---

## Alternatives (розглянуто, відхилено)

1. **Concrete hex замість ролі** — відхилено owner-ом: не theme-aware (золото на light інше), заголовок-«смисл» потребує зворотного hex→роль матчингу (крихко). → семантична роль.
2. **Object-меню лишити окремим** (ADR-0078 крапки на фігурі + окремий тулбар-flyout) — відхилено: два recolor-шляхи дублюються; owner обрав об'єднати в одну багату поверхню з live-preview.
3. **`dismissOnOutside` як у ADR-0078** — недостатньо: реагує лише на `click` (post-`pointerdown`), тож лівий клік по чарту вже стартував draft ПІД меню. → власний `pointerdown`-capture з поглинанням.
4. **Кружки-свотчі** (як ADR-0078) — відхилено: лінія-бар правдивіша до фігури.
5. **Абстрактний міні-зразок завжди** — відхилено: на вибраному об'єкті правдивий live-preview сильніший і швидший feedback.
6. **Окремі `previewLineWidth`/`reweightById`** — відхилено (copy-paste): узагальнено в `previewMeta`/`updateMetaById` з `DrawingMetaPatch`.

---

## Consequences

**Позитив:** одна тиха поверхня для колір/товщина/стиль; правдивий live-preview на справжньому об'єкті (клік=закріпив undoable, вихід=відкотив); колір theme-aware через роль; SSOT-модулі з тестами; клік по чарту при відкритому flyout не малює; мертвий код прибрано; client-only + undoable (ADR-0005).

**Межі / попереду:**
- **Пресети «наміри-перші»** (Теза/Рівень/Нотатка/Увага = готова трійця над рядами) — наступна фаза; клік-пресета + мапінг за нею.
- **Пам'ять-per-tool «останні»** — за пресетами.
- Mobile (`pointer:coarse`) — тулбар прихований, flyout desktop-first.
- Light-тема: перевірити контраст frosted-fallback'ів (adversarial review track).

**Live-verified** (реальний `page.mouse` / `getImageData` `canvas.drawings-layer` / `localStorage`):
- Колір: bull-дефолт → hline рендериться чистою зеленою лінією (avgG=204, 929px).
- Object: neutral → hover bear = червона 926px (transient) → вихід = відкат до neutral 929px → click = commit bear (`meta.colorRole='bear'`, count=1, не дублікат) → Ctrl+Z undo = neutral → Ctrl+Y redo = bear → Delete = count 0.
- Товщина: 1px база → hover/commit 4px товща + `meta.lineWidth=4`.
- Стиль (у спокої, без hover-glow): solid=1 перехід, dashed=67 штрихів, dotted=194 крапки + stored.
- Dismiss: ліва по чарту = закрив + 0 намальовано, наступний клік = +1 (інструмент живий), права = закрив + 0; object-режим так само.
- svelte-check 0 errors, vitest 43/43 (tools 28 + colorRoles 8 + lineStyles… — суміш).

---

## Константи (крутилки)

| Константа | Значення | Файл |
| --- | --- | --- |
| `FLYOUT_W` | 188 px | DrawingStyleFlyout.svelte |
| `flyoutH` | 154 (tool) / 190 (object) px | DrawingStyleFlyout.svelte |
| `--card` alpha (frosted) | 46 % | DrawingStyleFlyout.svelte |
| `backdrop-filter` | `blur(22px) saturate(1.4)` | DrawingStyleFlyout.svelte |
| товщини | `[1,2,3,4]` px | DrawingStyleFlyout.svelte |
| `dashPattern` dashed | `[w*4, w*3]` | lineStyles.ts |
| `dashPattern` dotted | `[0, w*2.4]` + `lineCap:round` | lineStyles.ts |
| `v4_drawing_defaults` | localStorage key (per-tool дефолт) | App.svelte |

---

## Rollback

Погранульно: **стиль** → прибрати `dashPattern` виклики у 3 tools (назад `setLineDash([])`) + style-ряд у flyout; **товщина** → прибрати width-ряд + `lineWidth` з `defaultMeta`/patch; **колір** → повернути `d.meta?.color ?? themeBaseColor` у `drawItem`, прибрати `resolveColor`/`roleColors`; **object-режим** → відновити `DrawingContextMenu` + `recolorById` (git revert); **dismiss** → повернути `dismissOnOutside`. Кожне незалежне; модель `meta.colorRole`/`lineStyle` additive (старі фігури без цих полів рендеряться як завжди).

---

## Future work

- **Пресети «наміри-перші»**: іменована трійця (Теза/Рівень/Нотатка/Увага) = готовий колір+товщина+стиль над гранульованими рядами; клік-пресета застосовує все разом (дефолт + live). Пресет-мапінг узгодити з owner.
- **Пам'ять-per-tool «останні»**: рядок нещодавніх комбінацій на 1 тап.
- **Cross-TF sync** + **draw-into-future** (ADR-0079 §Future) — архітектурна зміна storage.
