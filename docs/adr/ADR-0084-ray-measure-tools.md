# ADR-0084: Drawing Tools — Ray (Промінь) + Measure (Лінійка)

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0084 |
| Статус | **Implemented** — live-verified (owner: «Згоден. Го», 2026-07-07) |
| Дата | 2026-07-07 |
| Автори | Станіслав (owner) + Opus 4.8 |
| Будується на | ADR-0074 (Tool Registry — «+нові tools без редагування renderer»), ADR-0080 (flyout/styleable), ADR-0082 D6 + ADR-0083 (fractional мапінг + майбутнє) |
| Поважає | ADR-0007/0074 (client-only), ADR-0079 (delicate object) |
| Зачіпає шари | `ui_v4/src/chart/drawings/tools/{RayTool,MeasureTool}.ts` (NEW), `tools/{types,index}.ts`, `DrawingsRenderer.ts`, `DrawingToolbar.svelte`, `App.svelte`, `types.ts`, `stores/keyboard.svelte.ts` |
| Initiative | `drawing_tools_v2` |

---

## Quality Axes

- **Ambition target**: **R2** — два трейдерські інструменти щоденного вжитку через наявний registry-контракт (перше реальне використання open/closed дизайну ADR-0074).
- **Maturity impact**: **M4 → M4** — registry доведено ділом (нуль редагувань hit-test/drag машинерії); ephemeral-клас інструментів (measure) введено без розширення storage-моделі. Знижень немає.

---

## Контекст

Owner обрав із запропонованого: «промінь + лінійка» як перший крок (щоденна користь / малі зусилля); «жива лінія-сенсор → Арчі» — окрема наступна розмова. Tool Registry (ADR-0074) проектувався саме під це: новий tool = новий файл + рядок у реєстрі.

---

## Рішення

### D1. Ray — 2-точковий промінь
`type:'ray'`, click-click як trend; рендер = сегмент від p1 крізь p2, продовжений на `RAY_EXTEND_PX=10000` у напрямку p1→p2 (самодостатньо в render І hitTest — контракт ToolModule не міняється; canvas кліпає невидиме сам). **AABB кліпнутий до canvas** — інакше центр AABB (де живе delete-× ADR-0079) опинявся б за екраном. Повний styleable (колір/товщина/стиль/пресети flyout), персистентний, cross-TF, у майбутнє (D6/0083). Hotkey `y`, іконка arrow-up-right.

### D2. Measure — ephemeral лінійка (НЕ фігура)
`type:'measure'`, click-click; показує: **Δціна (знак), %, тривалість** (Δt з `t_ms` — людський формат г/хв/д; чесніше за «кількість барів», бо не залежить від TF). Рендер: діагональ + напівпрозора заливка напрямку (bull/bear токени) + лейбл з halo.
**Ephemeral-клас**: НІКОЛИ не комітиться — не в CommandStack, не в localStorage, не в hit-test/selection (живе лише як draft). 2-й клік «заморожує» результат (`measureFrozen`), наступний клік чистить і починає новий вимір, Escape/зміна інструмента — чистить. НЕ styleable (без flyout).

### D3. RenderContext + bull/bear (additive, optional)
`RenderContext.bullColor?/bearColor?` — renderer передає з наявного `roleColors` (тема-aware). Optional → нуль churn у наявних тестах/tools.

---

## Alternatives (розглянуто, відхилено)

1. **H-Ray окремим інструментом** — відхилено (зараз): ray покриває (горизонтальний ray = ray з рівними цінами); окрема іконка = зайвий шум рейки. Повернемось, якщо owner попросить.
2. **Measure як persisted фігура** — відхилено: лінійка = одноразове питання «скільки?», не аналітичний об'єкт; TV теж ephemeral. Persisted вимір захаращував би стор.
3. **«Кількість барів» у лейблі** — відхилено: bars залежать від TF (та сама відстань = 4 H1 = 16 M15); Δt універсальний і читається людиною.
4. **Продовження ray до межі canvas у render через cssW** — відхилено: hitTest не має cssW у контракті; RAY_EXTEND_PX однаковий в обох = геометрія консистентна без зміни контракту.

---

## Consequences

**Позитив:** промінь — найчастіший трейдерський інструмент після hline, тепер з усім стеком (flyout, пресети, cross-TF, майбутнє, undo); лінійка — миттєва відповідь «скільки і за скільки»; registry-контракт підтверджено ділом (renderer's state machine розширено 3 рядками: draft-гілки + measure-freeze).

**Межі:** measure не має магніту по X (лише ціна-снап як у всіх); ray у майбутнє тягнеться екстраполяцією (ADR-0083) — семантика та сама.

**Live-verified**: див. commit (ray: рендер до краю + hit за p2 / не-hit перед p1 + recolor через flyout + cross-TF; measure: label Δ/%/час, freeze→новий вимір, Escape чистить, localStorage порожній). Юніти: Ray + Measure specs у tools/.

---

## Rollback

Погранульно: видалити {Ray,Measure}Tool.ts + registry entries + toolbar кнопки + hotkeys + draft-гілки renderer (3 рядки) + типи. Storage не зачеплений (ray-фігури в сторі стануть невідомим типом — `getToolModule` поверне undefined → тихий skip рендеру; повне чищення = purge).

---

## Future work

- **Жива лінія-сенсор → Арчі** (owner-обране «найамбітніше»): намальована лінія → `price_cross` wake-умова (WakeEngine ADR-0048/0075). Окремий ADR + owner-бачення реакції Арчі.
- H-Ray, Fib retracement, текстова нотатка — за owner-запитом.
