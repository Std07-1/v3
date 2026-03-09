# UI Spec — Premium Trader-First Shell

- Статус: Working spec
- Дата: 2026-03-09
- Scope: `ui_v4` visual system, HUD, top shell, interaction language
- Non-goals: зміна SMC логіки, зміна UDS/API контрактів, новий transport
- Релевантні ADR: 0027, 0031, 0032, 0033, 0035

## 1. Мета

Цей документ фіксує, що саме відділяє поточний `ui_v4` від award-level подачі,
і як перевести інтерфейс із рівня `well-made pro terminal` на рівень
`category icon for traders` без поломки торгової логіки.

Головний принцип: у трейдера головний герой не HUD, а `price action`.
Усе навколо має не конкурувати з графіком, а переводити шум у рішення.

Формула продукту:

`context -> thesis -> action -> confidence`

Не `more widgets`, а `clearer conviction`.

## 2. Зафіксований поточний стан

На момент цього spec live shell читається так:

- Верхній HUD зараз поданий як один ряд: `symbol · tf · price · stale/live · favorite · bias pills · trade badge`.
- `Replay`, brightness, tools і UTC винесені окремо, але все ще читаються як набір utility-елементів.
- Bias, session, narrative і mode уже присутні функціонально, але не зібрані в одну сильну thesis-ієрархію.
- Відчуття інтерфейсу ближче до хорошого desk-terminal, ніж до преміального trading product.

Це сильна база для execution UI, але ще не category-defining identity.

## 3. North Star

### 3.1. Product position

Інтерфейс має відчуватися як `tactical editorial terminal`:

- достатньо строгий для активного трейдера;
- достатньо виразний, щоб мати власний силует;
- достатньо стриманий, щоб витримувати довгі сесії;
- достатньо преміальний, щоб рішення виглядали дорожчими за просто `pills + glass`.

### 3.2. Core laws

1. Chart is sacred.
2. Thesis over telemetry.
3. Premium = restraint, not ornament.
4. Motion follows regime change, not vanity.
5. Memory comes from 1-2 signature interactions, not from 20 flourishes.

## 4. Чого бракує до award-level

## 4.1. Власної візуальної мови

### Чому цього бракує

Зараз інтерфейс якісний, але візуально його можна описати як
`акуратний pro trading shell`. Це ще не мова, яку можна впізнати за силуетом.
Плашки, сепаратори, inline utility-патерни і спосіб групування інформації
не створюють унікального DNA.

### Що пропоную

Побудувати `Tactical Editorial` мову з трьох опор:

- один сильний shell-ритм замість багатьох дрібних utility-островів;
- одна signature-група для thesis/state/action;
- один преміальний матеріал для тактичних поверхонь.

### Як це виглядатиме

- Верхній рядок перестає бути набором окремих pills.
- Замість цього з'являється одна цільна `thesis bar` з чотирма сегментами:
  `Bias`, `State`, `POI`, `Action`.
- Service controls відходять у другий план: tools, replay, diagnostics,
  freshness, brightness більше не формують головну композицію.
- Візуальні мотиви стають повторюваними: одна геометрія радіусів,
  одна система тонких контурів, один тип підсвітки стану, одна система notch-акцентів.

### До

`XAU/USD · M30 · 5094.71 · Stale · bias pills · TRADE triggered`

### Після

`Bearish HTF / Inside supply / Waiting CHoCH / No entry yet`

Це вже не просто shell, а продукт, який говорить власним голосом.

## 4.2. Типографічного характеру

### Чому цього бракує

Функціонально текст є, але немає editorial direction.
Зараз більшість важливих речей читається одним тоном:
utility labels, mode, bias, service state, tactical meaning.
Через це інтерфейс виглядає робочим, але не авторським.

### Що пропоную

Ввести три типографічні ролі:

- `Editorial`: для thesis і market state.
- `Tactical UI`: для labels, pills, toggles, controls.
- `Numeric`: для price, time, volatility, percentages.

### Як це виглядатиме

- Thesis формулюється короткою людською фразою в більш характерному стилі.
- Ціна та UTC мають суху, точну, майже instrument-grade типографіку.
- Secondary service labels втрачають вагу і контраст.
- Uppercase використовується не всюди, а лише для режимів і state calls.

### До

Усі тексти поводяться як одна технічна система підписів.

### Після

Трейдер з першого погляду розрізняє:

- що є висновком;
- що є станом ринку;
- що є інструментальним control layer;
- що є довідковою телеметрією.

## 4.3. Матеріальності й глибини

### Чому цього бракує

Зараз багато що подано як `рядок інтерфейсу`.
Компоненти коректні, але простір між ними ще не працює як матеріал.
Мало відчуття, що shell має передній, середній і задній плани.

### Що пропоную

Перейти від `line of pills` до `layered tactical surfaces`.

### Як це виглядатиме

- Chart залишається найспокійнішим і найчистішим шаром.
- Thesis bar стає тонкою, дорогою, майже інструментальною пластиною.
- Secondary strips мають слабший контраст і тонший border-energy.
- Простір між блоками зростає; дрібне дроблення зникає.
- Замість простого glass-ефекту вводиться відчуття товщини:
  soft shadow, edge light, controlled blur, subtle grain або paper-noise,
  але дуже дозовано.

### До

Елементи сидять поруч і борються за один план уваги.

### Після

Є чіткий ритм:

- first plane: thesis and chart;
- second plane: tactical context;
- third plane: service and diagnostics.

## 4.4. Signature interaction

### Чому цього бракує

Інтерфейс зараз радше правильний, ніж пам'ятний.
Він не має 1-2 взаємодій, за якими трейдер одразу скаже:
`це саме цей продукт`.

### Що пропоную

Закріпити дві signature interaction:

1. `TF switch as time-compression event`.
2. `Thesis reveal / focus mode`.

### Як це виглядатиме

#### 1. TF switch

Перемикання TF має відчуватися не як заміна label,
а як зміна масштабу мислення:

- короткий compression/expansion ripple у shell;
- перепозиціонування thesis strip без дешевого slide-show;
- акцент на тому, що змінився не просто таймфрейм, а контекст рішення.

#### 2. Thesis reveal

Натиск або hover на state/thesis відкриває компактне пояснення:

- чому зараз `WAIT`;
- де POI;
- що буде trigger;
- що invalidates setup.

Це не modal і не side panel. Це короткий tactical reveal.

### До

TF, bias і state перемикаються коректно, але без сильної пам'яті про дію.

### Після

Користувач запам'ятовує продукт через відчуття `режиму`, а не через окремі кнопки.

## 4.5. Преміального motion-language

### Чому цього бракує

Зараз motion переважно утилітарний. Для award-level потрібен motion,
який підкреслює зміну market regime, focus depth і decision urgency.

### Що пропоную

Створити `market-aware motion language`:

- `WAIT` рухається повільно і стримано;
- `PREPARE` має зібраність і напругу;
- `READY` стає точнішим і контрастнішим;
- `TRIGGERED` відчувається як зафіксований execution event,
  а не як черговий badge.

### Як це виглядатиме

- зміна режиму впливає на thickness, glow, contrast, cadence;
- replay scrub відчувається як `precision instrument`, а не як generic range slider;
- focus mode прибирає зайве майже безшумно, але дуже відчутно.

### До

Мікроанімація як функціональна реакція.

### Після

Рух стає частиною продуктового класу і допомагає читати стан ринку.

## 5. Що я б змінив як трейдер

## 5.1. Переробити HUD у thesis bar

### Чому

Трейдеру потрібні не `pills`, а короткий торговий висновок.

### Пропозиція

Зібрати верхню лінію в такий порядок:

- `Bias`
- `State`
- `POI`
- `Action`

Приклад:

`Bearish HTF / Inside supply / Waiting CHoCH / No entry yet`

### До

Розсипаний ряд utility-станів.

### Після

Один короткий tactical sentence, який можна прочитати за секунду.

## 5.2. Прибрати службовий шум з першого плану

### Чому

Diagnostics, health, stale, brightness, replay, service toggles важливі,
але вони не повинні конкурувати з рішенням.

### Пропозиція

Відправити їх у secondary shell:

- верхній правий сервісний cluster;
- collapsible diag shelf;
- context reveal only when needed.

### До

Service layer читається майже на тому ж рівні, що й trade thesis.

### Після

Trade layer домінує, service layer допомагає, але не шумить.

## 5.3. Посилити ієрархію режимів

### Чому

WAIT, PREPARE, READY, TRIGGERED вже існують логічно,
але не мають достатньо сильного візуального авторитету.

### Пропозиція

Зробити mode system основною режисурою shell:

- `WAIT` — low energy, quiet contrast;
- `PREPARE` — tighter edge, alert tone;
- `READY` — high clarity, directional emphasis;
- `TRIGGERED` — sealed event, strong confidence mark.

### До

Mode є, але не керує архітектурою уваги.

### Після

Mode стає головним читачем urgency.

## 5.4. Зробити bias/session/narrative дорожчими

### Чому

Зараз вони сприймаються як utility-плашки,
хоча це ядро multi-timeframe interpretation.

### Пропозиція

Сформувати компактний `tactical strip` під thesis bar:

- bias cluster;
- current session / killzone;
- one-line narrative cue.

### До

Кілька дрібних маркерів без спільного матеріалу.

### Після

Одна дорога tactical surface з чіткою роллю.

## 5.5. Прибрати дроблення через крапки

### Чому

Сепаратори `·` працюють як terminal shorthand, але множать відчуття дрібності.

### Пропозиція

Замінити їх на:

- відступ;
- сегментацію матеріалом;
- легкі vertical dividers;
- notch alignment або typographic contrast.

### До

`A · B · C · D`

### Після

Плавний ритм блоків без terminal-noise.

## 5.6. Зробити chart святішим простором

### Чому

У трейдера головне полотно — графік.
HUD має підсилювати chart, а не жити як окремий dashboard.

### Пропозиція

Зменшити контраст і кількість верхніх оболонок,
дати більше повітря навколо chart viewport,
посилити frame only where it helps orientation.

### До

Shell і chart існують поруч.

### Після

Shell підкреслює chart і зникає, коли не потрібен.

## 6. Перші дві ініціативи

## 6.1. Trader-first redesign для HUD і верхнього shell

### Ціль

Перетворити верхній шар із utility HUD на decisive trader shell.

### Scope

- `ui_v4/src/layout/ChartHud.svelte`
- `ui_v4/src/layout/ChartPane.svelte`
- `ui_v4/src/App.svelte`
- theme tokens у `ui_v4/src/chart/themes.ts` або еквівалентному shell-layer

### Що змінюємо

1. Формуємо `thesis bar` як primary layer.
2. Виносимо service controls у quieter secondary cluster.
3. Збираємо bias/session/narrative у компактний tactical strip.
4. Перепаковуємо mode states у сильну WAIT/PREPARE/READY/TRIGGERED систему.
5. Зменшуємо dot-separated fragmentation.

### До

- utility-first top row;
- trade message розкладений по кількох зонах;
- state відчувається як badge, не як command surface.

### Після

- thesis-first shell;
- один погляд = один висновок;
- chart отримує більше повітря і авторитету.

### Acceptance criteria

- верхній shell читається за 1-2 секунди;
- thesis можна озвучити однією фразою;
- service layer не домінує над trade layer;
- mode розрізняється без читання дрібного тексту.

## 6.2. Awwwards-grade art direction pass для всієї visual system

### Ціль

Дати продукту власний premium silhouette без шкоди для trading clarity.

### Scope

- typography stack;
- palette and tonal hierarchy;
- shell materials;
- spacing rhythm;
- motion language;
- replay/focus/TF signature interactions.

### Що змінюємо

1. Вводимо editorial typography pair.
2. Визначаємо 4-6 канонічних surface tones.
3. Стандартизуємо border, glow, blur, shadow discipline.
4. Визначаємо 2 signature interactions.
5. Визначаємо motion states для regime changes.

### Базовий візуальний напрям

- Base: deep graphite / warm stone / restrained metal accents.
- Accent logic: bullish і bearish кольори лишаються функціональними,
  але shell не будується навколо їхнього шуму.
- Typography direction:
  editorial grotesk + precise numeric mono/tabular layer.
- Material:
  thin frosted plate, subtle edge light, almost invisible grain.

### До

Сильна engineering-естетика без достатнього авторського жесту.

### Після

Інтерфейс, який можна впізнати по верхньому shell,
ритму типографіки і способу переходу між market regimes.

### Acceptance criteria

- продукт не виглядає як generic trading shell;
- visual language повторюється послідовно у всіх core surfaces;
- motion відчувається дорого, але не відволікає;
- design не шкодить швидкості читання chart.

## 7. Design tokens direction

Це не фінальний token sheet, а напрям для першого pass.

### Typography

- Editorial: виразний grotesk або restrained serif-sans pair для thesis.
- Tactical: нейтральний sans для controls.
- Numeric: tabular mono або narrow numeric face для price/time.

### Spacing

- Менше дрібних зазорів 4-6 px.
- Більше ритму 8 / 12 / 16 / 24.
- Більше повітря між первинним і вторинним шарами.

### Radius and stroke

- Один канонічний radius family.
- Один secondary radius family.
- Один stroke intensity ladder для quiet / active / urgent.

### Motion

- 140-180ms: quick tactical feedback.
- 220-280ms: shell state transitions.
- 320-420ms: replay/focus/thesis reveal.

## 8. Practical before/after summary

### Before

- `pro terminal`
- utility-first HUD
- fragmented hierarchy
- good live trading shell
- little product memory

### After

- `premium trader cockpit`
- thesis-first shell
- chart-dominant composition
- signature TF/focus/reveal interactions
- strong editorial identity with restrained motion

## 9. Рекомендована послідовність execution

1. `HUD + top shell` structural redesign.
2. `Typography + spacing + token pass`.
3. `Mode system` visual escalation.
4. `Signature interaction` for TF switch + thesis reveal.
5. `Replay/focus` premium motion pass.
6. Final visual QA in browser on desktop and mobile widths.

## 10. Критерій успіху

Після цього pass трейдер має відчути не просто `зручний інтерфейс`, а продукт,
який:

- швидше формулює ринковий висновок;
- менше втомлює за довгу сесію;
- має власний смак і власний силует;
- залишає chart у ролі головного героя;
- запам'ятовується 1-2 взаємодіями, а не десятком дрібних деталей.
