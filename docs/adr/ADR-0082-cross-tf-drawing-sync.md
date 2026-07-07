# ADR-0082: Cross-TF Drawing Sync — Per-Symbol Shared Store

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0082 |
| Статус | **Accepted** — модель узгоджена (owner co-design, 2026-07-06); реалізація нижче |
| Дата | 2026-07-06 |
| Автори | Станіслав (owner) + Opus 4.8 (RECON + implementation + live verification) |
| Суперсідить | **ADR-0007** (drawing persistence — per symbol+TF ключ → per-symbol) |
| Переформовує | **ADR-0074 ADR-B** (майбутній бекенд-namespace `drawings/{symbol}/{tf}/` → `drawings/{symbol}/`) |
| Поважає | ADR-0007 + ADR-0074 (drawings client-only), ADR-0079/0080 (surface-1/2) |
| Виправляє | стале посилання `drawings client-only (ADR-0005)` у ChartPane → правильно ADR-0007 |
| Зачіпає шари | `ui_v4/src/chart/drawings/DrawingsRenderer.ts`, `ui_v4/src/layout/ChartPane.svelte` |
| Initiative | `drawing_tools_v2` |

---

## Quality Axes

- **Ambition target**: **R3** — нова persistence-модель (per-symbol спільний стор + one-time міграція-злиття), а не крутилка наявної.
- **Maturity impact**: **M4 → M4** (тримає, уточнює модель) — фрагментований per-TF стор → когерентний per-symbol single-source; малювання = аналітичний намір трейдера, що не залежить від TF-масштабу. Знижень немає.

---

## Контекст

Малювання зберігались **per symbol+TF** (`v4_drawings_{symbol}_{tf}`, ADR-0007) — лінія, накреслена на H1, НЕ з'являлась на M15/D1. Це суперечить ментальній моделі трейдера: рівень 4300 або трендова — це **факт про ринок**, не про масштаб перегляду. Owner-вимога: малювання прив'язані по (час+ціна) мають бути на **всіх TF** одного символу; зміна/видалення на одному TF → на всіх.

**RECON-знахідки (grounded, не здогади):**

- **Рендер уже TF-agnostic** [VERIFIED DrawingsRenderer.ts]: `toX = timeScale().timeToCoordinate(t_ms)`, `toY = priceToCoordinate(price)` — мапять домен-координати на будь-який завантажений TF. hline (лише ціна) тягнеться на повну ширину; trend/rect рендеряться, коли обидва кінці мапляться. Отже блокує **лише ключ зберігання per-TF**, а не рендер.
- **ADR-0007 сам заклав дугу** [VERIFIED 0007:157]: у Deferred стоїть `📋 Visibility per TF — drawing_properties_v1`. Тобто автори бачили майбутнім **керування видимістю per-TF** (сховати на певних TF), не автоматичну ізоляцію. Наша модель = глобально-за-замовчуванням, а per-TF-hide — узгоджений наступний item.
- **Час уже сирий** [VERIFIED getSnappedPrice]: магніт снапить лише **ціну** до OHLC; час зберігається точним (`fromX`). I2 «Geometry of time» — про close_ms свічок, не про якорі малювань → конфлікту нема.
- **Client-only лишається** [VERIFIED 0007:71, ADR-0074:610]: бекенд noop; localStorage = єдине джерело. Стале посилання `ADR-0005` у ChartPane — виправляємо на ADR-0007.

---

## Рішення

### D1. Ключ зберігання = per-symbol
`v4_drawings_{symbol}_{tf}` → **`v4_drawings_{symbol}`**. Один спільний стор на символ, спільний усіма TF. `setStorageKey(symbol)` (сигнатура втрачає `tf`); обидва call-site у ChartPane (нормальний + replay full-frame) передають лише symbol.

### D2. TF-switch тримає малювання; symbol-switch перевантажує
`setStorageKey` робить **early-return, якщо ключ не змінився** (той самий символ = TF-switch): малювання лишаються в пам'яті, самі перемальовуються під новий TF через наявний `subscribeVisibleTimeRangeChange`. Ключ змінився (новий символ) → flush поточного + load нового. `setAll([])` (бекенд-порожньо) далі ігнорується — нічого не витирає.

### D3. Видалення/зміна пропагуються автоматично
Єдиний in-memory масив + per-symbol стор → ADD/DELETE/UPDATE на будь-якому TF мутують один стор і зберігаються під per-symbol ключ. Перемикання на інший TF того ж символу (early-return) показує вже оновлений стан. Нуль додаткової логіки синхронізації.

### D4. One-time міграція-злиття (об'єднання по id)
При першому завантаженні per-symbol ключа: зібрати всі legacy `v4_drawings_{symbol}_{tf}` ключі, **об'єднати їх Drawing[] по id** (UUID → нуль колізій), записати у per-symbol ключ, **видалити legacy**. Ідемпотентно (після міграції legacy зникли → наступні завантаження просто читають per-symbol). Не втрачає жодної раніше накресленої фігури.

### D5. Точний t_ms (без снапу до бару)
Якорі лишаються сирими `t_ms`. Точка з M1 (10:03:00) на H1 рендериться інтерпольовано між барами — консистентно на всіх TF. Магніт (ціна→OHLC) не змінюється.

### D6. Fractional time-мапінг (addendum 2026-07-07, після owner-репорту)
Перша реалізація впала на межі, яку ADR позначив як «рендер-факт»: LWC `timeToCoordinate(t)` повертає **null для t, якого нема серед барів TF** — trend/rect з якорями M15 (10:15) зникали на H1 (бар лише 10:00). Початковий verify пройшов на hline (час ігнорують) — дірка верифікації визнана; тест тепер = trend з не-H1 якорями. Фікс: `timeMap.ts` (pure, 9 юнітів) — t → **дробовий logical-індекс** (бінарний пошук сусідніх барів + інтерполяція; екстраполяція за краями за кроком крайньої пари — заодно відкриває draw-into-future) → `logicalToCoordinate`. Інверсія (`fromX`) — симетрична (sub-bar точність якоря). Кеш часів барів: guard length+lastTime O(1), rebuild лише на зміну даних. Вузька талія: лише `toX`/`fromX`, tools не чіпались. **Межа «обидва кінці в діапазоні» з Consequences ЗНЯТА.**

### D7. Cross-tab конвергенція (addendum 2026-07-07)
Дві вкладки ділять per-symbol ключ, але тримають незалежні in-memory копії — save зі «застарілої» мовчки перетирав фігури іншої (last-writer-wins; спіймано live). `storage`-event (шлеться лише іншим вкладкам) → приймаємо зовнішній стан → вікно розбіжності ~мс. Gesture-safe: drag/preview шукають фігуру по id щокроку — зникла ззовні → м'який no-op.

### Інцидент-нотатка (S1, changelog 20260707-001)
Розслідування owner-репорту «підвисає і стрибає» виявило **системний S1 поза drawings**: `index.html` віддавався без `Cache-Control` → евристичний HTTP-кеш браузера → сусідні навігації виконували РІЗНІ builds (мікс per-TF/per-symbol сторів = «зникаючі» фігури; при мертвому сервері PWA-SW віддавав оболонку = «підвисає»). Фікс у serving-шарі: `Cache-Control: no-cache` для index.html + sw.js (ETag → дешевий 304), sw.js v2 (`{cache:'no-cache'}` navigation fetch + `skipWaiting` — лікує отруєні клієнти). Це захищає КОЖЕН майбутній деплой, не лише drawings.

---

## Alternatives (розглянуто, відхилено)

1. **Лишити per-TF + додати «показати на всіх TF» галочку per-drawing** — відхилено: складніша модель (per-drawing visibility-set), суперечить owner-інтенту «за замовчуванням на всіх». Глобально-за-замовчуванням + майбутній per-TF-hide простіше й відповідає ADR-0007:157.
2. **Тільки hline глобальний, trend/rect per-TF** — відхилено owner-ом: намір «на всіх TF» стосується всіх типів. Обмеження «обидва кінці в діапазоні» для trend/rect — рендер-факт, не причина розділяти модель.
3. **Снап часу до бару TF при малюванні** — відхилено: ламає крос-TF консистентність (та сама фігура снапилась би до різних барів на різних TF); точний t_ms правдивіший.
4. **Почати чисто (викинути legacy per-TF)** — відхилено: втрата роботи трейдера. Злиття по UUID безпечне.
5. **Бекенд-стор одразу (ADR-B)** — поза scope: client-only лишається (ADR-0007/0074). Ця ADR лише переформовує майбутній namespace на per-symbol.

---

## Consequences

**Позитив:** малювання = крос-TF факт про ринок (одна лінія на всіх масштабах); видалення/зміна пропагуються самі (single-source); нуль рендер-переписування (рендер уже TF-agnostic); міграція зберігає наявні фігури; модель когерентніша (per-symbol замість фрагментованого per-TF); переформовує ADR-B чистіше.

**Межі / попереду:**
- ~~trend/rect потребують обидва кінці в завантаженому діапазоні~~ — **ЗНЯТО D6** (fractional мапінг з екстраполяцією; якір рендериться на будь-якому TF незалежно від сітки барів).
- **«Сховати на цьому TF»** (per-TF visibility override) — узгоджений наступний item (ADR-0007:157 дуга), окремий ADR.
- **draw-into-future** (фігури праворуч від останньої свічки) — суміжний, окремий ADR (ADR-0079 §Future).
- Cross-symbol НЕ шариться (за дизайном — лінія XAU/USD не на NAS100).

**Rollback:** повернути `setStorageKey(symbol, tf)` + per-TF ключ (git revert). Legacy per-TF ключі після міграції видалені — rollback повернеться до per-symbol ключа як єдиного (не втратить дані, але знову ізолює по TF лише нові). Тобто rollback безпечний для даних, лише відновлює ізоляцію.

---

## Константи / ключі

| Ключ | Було | Стало |
| --- | --- | --- |
| Drawing store | `v4_drawings_{symbol}_{tf}` | `v4_drawings_{symbol}` |
| `setStorageKey` | `(symbol, tf)` | `(symbol)` |
| legacy scan | — | `startsWith('v4_drawings_' + symbol + '_')` → merge+remove |

---

## Future work

- **Per-TF visibility override** («сховати на цьому TF») — ADR-0007:157 дуга; окремий ADR.
- **draw-into-future** — фігури за межами останньої свічки (whitespace/future timestamps).
- **Backend persistence (ADR-B)** — namespace тепер `drawings/{symbol}/` (per-symbol), multi-device sync, conflict resolution.
