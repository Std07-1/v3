# ADR-0077: Drawing Toolbar Redesign V2 — Icon-Only Quiet Curtain + Polite Hover Labels + Hint Governance

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0077 |
| Статус | **IMPLEMENTED** (2026-07-05) |
| Дата | 2026-07-05 |
| Автори | Станіслав (owner, live co-design) + Opus 4.8 (implementation + live verification) |
| Розширює | ADR-0074 (Drawing Tools V1 — tool registry, DrawingsRenderer, keyboard store лишаються в силі) |
| Частково замінює | ADR-0074 §T3 (фіксована 156px/44px панель із завжди-видимими labels + collapse toggle) — замінено icon-only floating layout |
| Поважає | ADR-0070 (TR corner — галочки живуть у ☰ CommandRailOverflow), ADR-0065 rev2 (menu-toggle pattern), ADR-0072 (mobile — drawing tools лишаються схованими), ADR-0066 (visual tokens) |
| Зачіпає шари | `ui_v4/src/layout/DrawingToolbar.svelte` (переписано), `ui_v4/src/stores/uiHints.ts` (**NEW**), `ui_v4/src/App.svelte` (store-wiring + toggles + gated titles), `ui_v4/src/layout/CommandRailOverflow.svelte` (2 нові menu-toggles), `ui_v4/src/layout/{CommandRail,ReplayBar,StatusBar,ChartHud,ChartPane}.svelte` (title-gating) |
| Initiative | `drawing_tools_v2` (продовжує 0007/0008/0074) |

---

## Quality Axes

- **Ambition target**: **R2** — UX-редизайн наявного тулбара + один новий shared primitive (`uiHints` store). Не R3: не вводимо нову data-вузьку-талію; детач/перетягування (справжній R3-стрибок) відкладено в майбутню роботу нижче.
- **Maturity impact**: **M3 → M3.5** — елевація: (1) видалено мертвий collapse/expand механізм; (2) hint-setting централізовано у store замість prop-drilling / повтореної localStorage-ідіоми; (3) кожен компонент DrawingToolbar тримає один concern. Знижень немає.

---

## Контекст

ADR-0074 дав фіксовану ліву панель (glass-фон, collapse-toggle, завжди-видимі labels). Owner-фідбек (live-сесія 2026-07-05): панель **займає корисну площу графіка і заважає**. Ціль — інструменти, що ховаються коли не потрібні, а колись — детачабельні (кожен окремо перетягується). Плюс: підказки не мають набридати, але мусять лишатись доступними; і має бути майстер-вимикач усіх hover-підказок чарту.

Редизайн зроблено **інкрементально, кожен крок верифіковано наживо** на `127.0.0.1:8000` (ws_server роздає dist; `vite build --watch`; playwright — dispatch подій + читання computed-стилів/атрибутів). Детач (серце ідеї) — окремий майбутній ADR (потребує edge-magnetism, safe-zone clamp, персистенцію позицій, mobile guard).

---

## Рішення

### D1. Icon-only, без chrome панелі
`.drawing-toolbar` втратив background / border / backdrop-blur / box-shadow / padding. Лишились самі іконки, плаваючи на графіку. Форми іконок (inline Lucide SVG-константи) і `buttons[]` — без змін.

### D2. «Тиха» ліва завіса (proximity dim)
Контейнер: `opacity: var(--dim)`, керується одним rAF-throttled **capture-phase** `pointermove` на `window`. Модель — **вертикальна завіса, не радіус**: курсор при `x ≤ REACT_X` (будь-яка висота) → повна яскравість; правіше — лінійно тане до `PROX_GHOST` через `FADE_SPAN`. `REACT_X ≈ 118px` = перша вертикальна лінія сітки праворуч від іконок (виміряно). Правила: **притухла іконка = `pointer-events:none`** (клік проходить крізь неї на графік — знімає input-occlusion); **активний інструмент / magnet ніколи не гасне** (завжди видно що «заряджено»); на завантаженні до першого руху миші — повна яскравість (знаходимість); `:hover`/`:focus-within` завжди overrides ghost. Завіса **фіксована** — не рухається з чартом при пані/зумі.

### D3. Притиснуто до стінки + вирівняно по клітинках сітки
`left:2, top:72`; кожна іконка рівно `36×36` = одна клітинка (виміряний крок горизонтальної сітки чарту = 36px); `gap:0`; верхи кнопок на лініях сітки `[72,108,…]`. Роздільник біля магніту прибрано.

### D4. Підсвітка самих іконок (без боксів)
Прибрано per-button background-плитки. Активна = золота іконка + золоте `drop-shadow` сяйво; hover = яскравіший штрих + світле сяйво; спокій = симетричний темний halo для читабельності над свічками. (Це ж усунуло «спотворення» — раніше золотий бокс активної іконки при ghost виглядав брудною плямою.)

### D5. Видалено collapse/expand механізм (мертвий код)
Оскільки resting-стан = icon-only, гілка розгортання стала недосяжною. Викинуто: стан `collapsed`, `toggleCollapsed`, collapse-кнопку, `.collapsed`/`:not(.collapsed)` CSS, localStorage `v4_toolbar_collapsed`, старі inline `.tool-label`/`.tool-hotkey`.

### D6. Per-icon «ввічливий» hover-підпис (стейт-машина)
Native `title` замінено кастомною плашкою `.tool-tip` (назва + `<kbd>` hotkey), керованою JS-стейт-машиною per-іконка:
- Показ №1: hover → `TIP_DELAY` затримка → показ `TIP_SHOW` → зникає (навіть якщо курсор ще на іконці).
- Показ №2 (dwell): курсор нерухомо `TIP_DWELL` → показ `TIP_SHOW_LONG`. (`pointermove` скидає dwell — «тримає курсор на місці».)
- Показ №3+: тиша — «побачив, досить».
- Скидання: залишив іконку на `TIP_RESET` → лічильник чиститься, наступний hover — знову з №1.
- **Re-summon = dwell, НЕ клік** (свідомо: клік по іконці вибирає інструмент; по «Гумка» = вмикав би стирання лише щоб перечитати підпис).
- `alwaysShowHints=true` (галочка, D8) → байпас: показ завжди на hover, тримається до leave (learning mode).

### D7. Меню-галочка «Малювання» (вкл/вимк інструментів)
Toggle у ☰-меню → `{#if drawingToolsEnabled}<DrawingToolbar/>{/if}` в App. Persist `v4_drawing_tools`, **дефолт ON**.

### D8. Меню-галочка «Підказки» + shared `uiHints` store
Майстер-вимикач усіх hover-підказок контролів. Setting винесено у `stores/uiHints.ts` (writable, persist `v4_show_hints`, **дефолт OFF**, `$hintsOn` + `toggleHints()`) — уникає prop-drilling крізь 8 компонентів і централізує ідіому. Керує: (a) drawing-підписами (`alwaysShowHints`); (b) **34 native `title`-тултіпами** контролів, кожен гейтиться `title={$hintsOn ? "…" : undefined}` (`undefined` чисто прибирає атрибут).
- **ON** → усі підказки показуються; **OFF** (дефолт) → native-тултіпи сховані, drawing-підписи лишаються «ввічливими».
- Розподіл 34: ChartHud 15 · ChartPane SMC-кнопки 7 · CommandRail 3 · ReplayBar 5 · StatusBar 1 · App 2 · Overflow brightness 1.

**Свідомі виключення (лишаються завжди-он):**
- `OhlcvTooltip` (O/H/L/C/V свічки під курсором) — **функціонал, не підказка** (owner-рішення).
- SMC-зони/свінги (`OverlayRenderer` canvas-тултіп) + narrative `.narr-tooltip` — не обрано owner-ом у scope.
- `BrandWatermark` `title` — це **проп у спільний `<Brand>`**, який має дефолт `"AI · ONE v3"` і вживає значення ще як `alt`. Передача `undefined` тултіп НЕ прибирає (Brand підставляє дефолт); правильний фікс = чіпати спільний Brand-компонент — непропорційно заради дрібної підказки на лого.

---

## Alternatives (розглянуто, відхилено)

1. **Re-summon підпису кліком** (як owner спершу сформулював) — відхилено: клік по іконці вибирає інструмент (гумка = деструктив) лише щоб перечитати підпис. → dwell (D6).
2. **Prop-drilling `hintsEnabled` крізь 8 компонентів** замість store — відхилено: 8 нових props + декларацій, повторює localStorage-ідіому (M-регрес). → shared `uiHints` store (D8).
3. **Радіус-proximity** (distance до rect) замість вертикальної завіси — відхилено: залежить від висоти, менш передбачувано; owner обрав завісу. → D2.
4. **Динамічне трекання сітки** (реактивний крок клітинки під зум) — відкладено: складніше, і крок LWC-сітки фактично стабільний. Прийнято статичний під поточний зум (D3, див. ризик нижче).
5. **Full rewrite DrawingsRenderer** — відхилено (X39 Maturity-регрес): renderer/registry/keyboard з ADR-0074 не чіпано.

---

## Consequences

**Позитив:** графік більше не затуляється (тихо коли не треба + click-through); підказки не набридають, але навчають; icon-only + сяйво = чистіший вигляд; менше коду (мертвий collapse видалено); setting централізовано (легше підтримувати); нуль нової backend-поверхні.

**Ризики / межі:**
- **D3 статичне вирівнювання**: клітинки прив'язані до поточного кроку сітки (36px). Сильна зміна масштабу цінової шкали → крок сітки поїде, іконки лишаться на місці, перестануть ідеально центруватись. Динамічне трекання — майбутня робота.
- **BrandWatermark** тултіп не під галочкою (виключення вище).
- **Mobile**: drawing tools лишаються схованими (`@media`, ADR-0072) — детач при мобільному вимагатиме окремого touch-guard (не тут).
- Дефолт «Підказки» OFF → нові native-тултіпи контролів сховані за замовчуванням; owner-таргет = досвідчений трейдер, галочка вмикає для навчання.

---

## Константи (крутилки для майбутнього maintainer)

| Група | Константа | Значення | Файл |
| --- | --- | --- | --- |
| Proximity | `REACT_X` / `FADE_SPAN` / `PROX_GHOST` | 118 / 90 / 0.18 | DrawingToolbar.svelte |
| Клітинка | left / top / `--drawing-cell-size` | 2 / 72 / 36px | DrawingToolbar.svelte |
| Tooltip | `TIP_DELAY` / `TIP_SHOW` / `TIP_SHOW_LONG` / `TIP_DWELL` / `TIP_RESET` / `TIP_MAX` | 450 / 1500 / 3000 / 600 / 4000 / 2 | DrawingToolbar.svelte |
| Persist | `v4_drawing_tools` (def ON) · `v4_show_hints` (def OFF) · `v4_magnet_enabled` | localStorage | App.svelte / uiHints.ts |

---

## Rollback

Погранульно, все reversible:
- «Малювання» / «Підказки» галочки: setting keys окремі; видалити menu-items у CommandRailOverflow + prop-и в App → повернення до безумовної поведінки.
- Title-gating: `title={$hintsOn ? "X" : undefined}` → `title="X"` (native завжди-он).
- Тулбар-вигляд: git revert DrawingToolbar.svelte до ADR-0074 T3 (панель).
- `uiHints.ts` — новий файл, видалення безпечне після зняття imports.

---

## Майбутня робота

- **P3 Детач/перетягування** (серце ідеї, окремий ADR): кожна іконка тягнеться вільно + soft edge-magnetism + hard clamp з Canvas Safe Zones / FP7-CommandRail / FP8-NarrativeSheet; per-icon `{x,y}` у новому UI-pref ключі (НЕ через DrawingsRenderer = FP9); гніздо-launcher для знаходимості; **mobile coarse-pointer guard обов'язковий у тому ж слайсі** (інакше вільні chips втечуть з-під `@media display:none`).
- Динамічне трекання кроку сітки (D3 ризик).
- Гострий баг для P1-right-click (окремо): зараз right-click під час draw КОМІТИТЬ фігуру — `if(e.button!==0)return` guard у `onPointerDownCapture` (load-bearing).
