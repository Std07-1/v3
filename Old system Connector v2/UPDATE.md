# UPDATE.md

<!-- markdownlint-disable MD036 MD013 -->

Журнал змін (оновлюється **кожного разу**, коли я роблю будь-які правки у репозиторії).

## Формат запису (конвенція)

Кожен запис має містити:

- **Дата/час** (локально) + коротка назва зміни.
- **Що змінено**: 3–10 пунктів по суті.
- **Де**: ключові файли/модулі.
- **Тести/перевірка**: що саме запускалось і результат.
- **Примітки/ризики** (за потреби): що може вплинути на рантайм.

---

## 2025-12-29 06:30 — UI_v2 (Mobile v2): top-прозорі шторки + автозакриття + компактний TF біля FULL

**Що змінено**

- “⋮” у mobile-v2 залишено тільки для фільтрів; шторка фільтрів закривається при кліку/тапі по графіку або будь-де поза нею.
- Filters drawer у mobile-v2 перетворено на **top-sheet** з прозорим фоном у стилі tooltip:
  - позиціювання: `top: calc(48px + 6px)` + бокові відступи `8px`;
  - анімація: `translateY(-120%) → 0` (коротка, 0.16s);
  - фон/рамка: `rgba(22, 32, 74, 0.28)` + `rgba(91, 118, 183, 0.28)`;
  - `border-radius: 12px`, `backdrop-filter: blur(2px)`;
  - компактні падінги: header `8x10`, body `10`.
- Список фільтрів (layers) зроблено компактнішим: грід-розкладка в 2–3 колонки залежно від ширини екрана (`minmax(140px, 1fr)`), щоб шторка була мінімальної висоти.
- Додано компактну кнопку вибору таймфрейму **біля FULL** (overlay actions): кнопка показує активний TF (`1m/5m/1h/4h`) і відкриває окрему top-sheet зі швидким вибором.
- Вибір “Ліміт зон (1m)” прибрано з UI (desktop layer-menu та mobile drawer).

**Доробки (прозорість/компактність + summary sheet + overlay header)**

- Filters/TF/Summary top-sheet у mobile-v2 зроблено ще прозорішими: `rgba(..., 0.14)` (було 0.28).
- Header Filters став компактнішим: заголовок “Фільтри” прибрано, лишився тільки `✕`; padding зменшено до `6x8`.
- Body Filters ущільнено: padding `8`, gap `6`.
- Summary перероблено як top-sheet (аналогічний стилю filters tooltip) і більше не займає місце у layout; закриття — тільки повторним натиском `Σ` (не закривається кліком по графіку).
- Mobile chart header перенесено всередину чарта як overlay (`position:absolute`, без фону/бордера), щоб не з’їдати висоту viewport; висота графіка більше не віднімає overlay-header (але `--mobile-header-h` лишається SSOT для позиціювання шторок нижче шапки).
- Для overlay-header додано правий резерв під цінову шкалу: `right: calc(8px + 56px)`.
- Кнопки TF/FULL/S6 (з chart overlay actions) у mobile-v2 переносяться у правий ряд overlay-header поруч із `Σ`/`⋮` (DOM move, без дублювання), щоб вся взаємодія була “справа в один ряд”.
- У mobile-v2 в chart overlay не показуємо рядок стану на кшталт `SHORT · ↓0.91` (контекст уже доступний через S6).
- Щоб не впливати на desktop, резерв `padding-right: 56px` для `.chart-overlay-actions` застосовується тільки в mobile-v2 (під `@media (max-width: 768px)` + `body.mobile-v2`).
- Mobile v2: права цінова шкала зроблена компактнішою через `rightPriceScale.minimumWidth=44` (керується з UI, щоб не чіпати desktop).
- Mobile v2: safe-area під price scale тепер динамічний (`--price-scale-w` з `chart.priceScale("right").width()`), тому `Σ/S6/⋮/TF` не мають наїжджати на шкалу на вузьких екранах.
- Mobile v2: overlay header у 2 рядки — зверху `SYMBOL/PRICE` + (праворуч) `S6`/`Σ`, нижче під символом компактно `⋮` → `TF`; `FULL` прибрано.
- Mobile v2: у Filters прибрано кнопку закриття `✕` (закриття: tap поза шторкою або повторний `⋮`).
- Mobile v2: TF sheet тепер popover на рівні кнопки `TF` (позиціювання через `--tf-popover-left/top`), таймфрейми в 1 ряд; `1m` прибрано з цього швидкого списку.

**Уточнення (фікс перекриття шапки)**

- Top-sheet позиціонується нижче реальної мобільної шапки через CSS-змінну `--mobile-header-h`, яку виставляє JS на кожному перерахунку мобільного layout.
- Закритий стан top-sheet: `opacity: 0` + `pointer-events: none` (щоб шторка не була видимою і не блокувала тапи по графіку/шапці).

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js

**Тести/перевірка**

- VS Code diagnostics (`get_errors`) для index.html/styles.css/app.js → PASS (0 помилок).
- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

**Примітки/ризики**

- Автозакриття зроблено через `pointerdown` (capture) по всьому документу: потенційні edge-case’и можливі, якщо з’являться інші оверлеї/шторки; зараз логіка обмежена двома sheet (filters/tf).
- Поведінка “прозорості” узгоджена з існуючим tooltip стилем (SSOT по значеннях rgba), без введення нової кольорової палітри.

## 2025-12-22 — Bootstrap/UDS: стабілізація `base_dir`, щоб UI не стартував з 1 бару

**Що змінено**

- `config/datastore.yaml` тепер читається через абсолютний шлях (не залежить від CWD).
- В `load_datastore_cfg()` додано нормалізацію `base_dir`: відносні шляхи резолвляться в абсолютний каталог (пріоритет — корінь проєкту), щоб `UnifiedDataStore` гарантовано бачив дискові snapshot-и.
- Додано інфраструктурний запобіжник `AI_ONE_NAMESPACE`: дозволяє ізолювати локальні/dev запуски від прод Redis, щоб не перезаписувати ключі/канали `ai_one:*`.
- Додано простий перемикач профілю `AI_ONE_MODE=local|prod`:
  - `local` → дефолтний namespace стає `ai_one_local` (якщо `AI_ONE_NAMESPACE` не заданий);
  - `prod` → дефолтний namespace `ai_one`.
  Також у `local` дефолтний bind host для UI_v2 стає `127.0.0.1`, а у `prod` — `0.0.0.0` (явні `SMC_VIEWER_*_HOST` мають пріоритет).
- Додано таргетний тест на резолв відносного `base_dir`.

**Де**

- app/settings.py
- config/config.py
- tests/test_app_settings_datastore_cfg_paths.py
- tests/test_config_run_mode_namespace.py
- app/main.py

**Тести/перевірка**

- `python -m pytest -q tests/test_app_settings_datastore_cfg_paths.py` → passed.
- `python -m pytest -q tests/test_config_run_mode_namespace.py` → passed.

**Примітки/ризики**

- Якщо хтось навмисно тримав `base_dir` відносно CWD — тепер буде стабільний резолв (краще для прод/служб, де CWD непередбачуваний).

## 2025-12-21 — Stage6: довіра (UNCLEAR reasons) + анти-фліп (decay/override) + UI summary (stable/raw/pending)

**Що змінено**

- Stage6 тепер повертає «чесний» `UNCLEAR` з явним кодом причини (`unclear_reason`) замість умовного мовчання.
- Анти-фліп Stage6 перестав бути «липким»: додано decay до `UNCLEAR` після серії raw `UNCLEAR`, а також сильний override для швидкої зміни при сильних фактах.
- У UI_v2 summary відображається одночасно: stable (після анти-фліпу), raw (як зараз), pending (кандидат + лічильник) та top-3 `why` / `UNCLEAR reason`.
- Telemetry/analytics: додається агрегація причин `UNCLEAR` у publisher для подальшого моніторингу.
- QA-скрипт Stage6 розширено: звіт містить `UNCLEAR reasons` і колонку `reason` у журналі.

**Поточні результати дослідження (QA, XAUUSD, 5m primary)**

- Report: `reports/stage6_stats_xauusd_h60_v2.md`
- steps=500, warmup=220, horizon(1m)=60, tp/sl=1.0 ATR
- raw: `{'4_3': 197, 'UNCLEAR': 235, '4_2': 68}` (UNCLEAR=47.00%)
- stable: `{'4_3': 234, 'UNCLEAR': 77, '4_2': 189}` (UNCLEAR=15.40%), flips=26
- UNCLEAR reasons: `{'CONFLICT': 60, 'LOW_SCORE': 252}`
- winrate stable: 4_2=52.13%, 4_3=45.06% (пост-фактум TP/SL на 1m)

**Де**

- smc_core/stage6_scenario.py
- app/smc_state_manager.py
- UI/publish_smc_state.py
- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js
- tools/qa_stage6_scenario_stats.py

**Тести/перевірка**

- `pytest tests/test_smc_stage6_scenario.py tests/test_smc_stage6_hysteresis.py` → passed.

**Примітки/ризики**

- `stable` тепер може повертатися в `UNCLEAR` (decay) — це свідомий компроміс заради довіри та контролю “липкості”.
- QA-метрика winrate тут — лише sanity-check для напрямку на горизонті, не торговий бектест.

---

## 2025-12-21 — UI_v2 (Web): нижні панелі лише в debug + чарт займає всю робочу висоту (prod)

**Що змінено**

- Нижні таблиці (Structure Events / OTE / Pools / Zones) сховано у проді та показуються лише при `?debug_ui=1`.
- У режимі `view=chart` + `debug_ui=0` робочу висоту viewport віддано під чарт (без зайвих «нижніх блоків»).
- Додано `debug-ui` class на `<body>` як єдиний перемикач для debug UI (CSS + існуючі JS індикатори).
- Піднято cache-bust для `styles.css`/`app.js`/`chart_adapter.js`.
- Виправлено «пливе/розтягується» чарт на desktop у `view=chart` (prod): стабілізовано flex-ланцюжок та висоту контейнера, щоб `ResizeObserver` не потрапляв у feedback-loop.

**Додатково (UX OTE)**

- OTE зони тепер малюються як прямокутники з часовими межами: від моменту появи (актуальності) до моменту зникнення/неактуальності, а не лініями «на весь екран». Часовий цикл відстежується на фронті по стріму (бекенд віддає лише active).

**Додатково (HTF UX: 1h/4h)**

- На HTF (view TF >= 1h) 5m POI показуємо лише як refinement: або якщо 5m зона повністю лежить всередині будь-якої HTF POI, або як top-1 5m вище та top-1 5m нижче від поточної ціни. 1m/5m фільтри не змінювались.
- На HTF (view TF >= 1h) BOS/CHOCH також фільтруються по часу: показуємо лише ті, що узгоджені з HTF-свічкою (без «підтягування» 5m подій на HTF).

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-20 — UI_v2 (Web): POI/зони як canary під флагом + жорсткі ворота (no-repaint/TF-truth/антишум)

**Що змінено**

- Додано Gate 1 (No-repaint): `price_min/price_max/origin_time` зони фіксуються при першій появі; далі можуть змінюватися лише `state` і `filled_pct`.
- Додано Gate 2 (Truth по TF, жорстко): зони рендеряться лише якщо `origin_time` кратний TF і не пізніше останнього complete close-time для цього TF (евристика на основі останньої complete свічки на графіку).
- Додано Gate 3 (Антишум на 1m): на TF=1m показуємо тільки найближчі 5m POI (top-2 вище та top-2 нижче від поточної ціни; також лишаємо POI, якщо ціна всередині зони).
- Сховано технічні рядки в шапці під `?debug_ui=1` (active_zones / EXEC / TF health).
- Додано user-налаштування ліміту зон для TF=1m (щоб не засмічувати екран):
  - `Як зараз (2 + 2 + в зоні)` / `Ліміт 2 (1 + 1 + в зоні)` / `Без ліміту`.
  - Значення зберігається в localStorage (`smc_viewer_zone_limit_mode`) і синхронізоване між desktop menu та mobile drawer.

**Де**

- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/app.js
- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

**Примітки/ризики**

- Gate 2 є навмисно жорстким: якщо бекенд віддає `origin_time` не вирівняний по TF, зона буде прихована (краще drop, ніж репейнт/preview).

---

## 2025-12-20 — UI_v2 (Web): ліміт зон (статус) + liquidity pools як сегменти (prod-friendly)

**Контекст**

- Користувач повідомив, що перемикання режимів ліміту зон (near2/near1/all) **візуально не дає різниці** у реплеї.

**Що змінено**

- Ліміт зон: додано `setViewTimeframe(tf)` у chart controller і прокинуто обраний TF з UI в `chart_adapter`, щоб Gate3 (антишум на 1m) не залежав лише від евристики `barTimeSpanSeconds` (у реплеї при «дірках» це могло вимкнути 1m-логіку).
- Додано cache-bust версію для `chart_adapter.js`/`app.js` у `index.html`, щоб уникати випадків зі старим JS у браузері.
- Liquidity pools: замість `createPriceLine(... lineVisible=true)` (лінії «на весь екран») зроблено рендер короткими горизонтальними сегментами біля правого краю + компактні бейджі на шкалі тільки для обраних рівнів.
- Tooltip: pools — прибрано префікс `Pool`, ціну перенесено на рядок нижче (під назвою). Додано hit-test по OTE (назва зверху, діапазон під нею). Пріоритет збережено: POI зона > pools/OTE.
- Tooltip: OTE — додано `role`, прибрано `Mid`, `Width` перейменовано на `Range`, позицію `Close` подано як «в зоні / нижче зони / вище зони».
- Pools: додано антишум-фільтр для рендера на чарті — не показуємо «слабкі» рівні (окрім PRIMARY і HTF targets), щоб не засмічувати екран.

**Статус / відкладено**

- Ліміт зон: попри зміни, на стороні користувача в реплеї ефект **ще не підтверджено**. Ймовірні причини:
  - у конкретному вікні даних недостатньо 5m POI-кандидатів, щоб різниця near1/near2 була помітна;
  - інші ворота/кластеризація/price-window дають однаковий набір зон.
  - Для повернення до теми: додати мінімальний debug-індикатор (лише при `debug_ui=1`) з поточними `zoneLimitMode` і `viewTimeframe`.

**Де**

- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/app.js
- UI_v2/web_client/index.html

**Тести/перевірка**

- Не запускалось (зміни у фронтенд JS; перевірка — ручна у браузері/реплеї).

---

## 2025-12-17 — UI_v2 (Web): override `fxcm_ws_base` для FXCM WebSocket (public/Cloudflare)

**Що змінено**

- Додано query-параметр `fxcm_ws_base` для явного задання базового URL FXCM WS (OHLCV/ticks/status).
- Це дозволяє підключати live-свічки через окремий тунель/домен, без вимоги same-origin path-проксі `/fxcm/*`.

**Де**

- UI_v2/web_client/app.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `13 passed`.

---

## 2025-12-19 — Stage0 завершено + Stage1 старт: реальні 5m/1h/4h у UnifiedDataStore (через contract)

**Що змінено**

- Зафіксовано завершення Етапу 0 (TF-правда + чесні гейти + телеметрія) як базовий інваріант рантайму.
- Стартовано Етап 1: розширено SMC contract-of-needs, щоб FXCM пайплайн фізично наповнював `UnifiedDataStore` таймфреймами `1h` та `4h` (разом з `1m/5m`).

**Де**

- app/settings.py
- config/datastore.yaml
- tests/test_smc_universe_cfg.py

**Тести/перевірка**

- `pytest -q tests/test_smc_universe_cfg.py` → passed.

---

## 2025-12-19 — Stage1/Data: збільшено ціль історії до 30 днів (SMC contract-of-needs)

**Що змінено**

- Піднято `min_history_bars` для `xauusd` до 30 днів: `43_200` (1m барів).
- Збільшено `RAM_BUFFER_MAX_BARS`, щоб кеш у RAM не обрізав 30d історію при догрузці/бекфілі.

**Де**

- config/datastore.yaml
- app/settings.py

**Тести/перевірка**

- `pytest -q tests/test_smc_universe_cfg.py tests/test_s3_warmup_requester.py tests/test_s2_history_state.py` → passed.

**Примітки/ризики**

- Вузьке місце зазвичай не RAM/диск, а час старту (кількість запитів), rate/timeout FXCM history API та сумарний обсяг кешу при багатьох символах.

---

## 2025-12-19 — UI_v2 (Web): прибрано «стрибки/розмазування» price-scale на 5m/1h + wheel-zoom стабілізовано

**Що змінено**

- Прибрано візуальне «розмазування/стриби по Y» на 5m/1h, яке проявлялось після перемикання TF та першої взаємодії (drag/scroll).
- Стабілізовано resize-поведінку: `scheduleChartResize()` тепер debounced (не більше 1 resize на кадр), щоб не було resize-thrash.
- Wheel-zoom/pan по правій шкалі ціни зроблено більш передбачуваним на різних браузерах/пристроях через нормалізацію `wheel.deltaMode` (pixels/lines/pages → pixels).
- Price-scale autoscale cache (`lastAutoRange`) тепер скидається при reset/новому датасеті/resize, щоб при старті manualRange не підхоплювався застарілий діапазон і не давав різкий «бам».
- Додатково: sessionRangeBox та rangeAreas більше НЕ впливають на autoscale (саме вони могли давати «підскоки» по Y під час горизонтального скролу), але під час manualRange синхронізуються з ним.
- Додатково: бекфіл «вліво» (розширення історії в минуле) більше НЕ тригерить resetManualPriceScale()/fitContent як “новий датасет”.

**Де**

- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/app.js
- UI_v2/web_client/styles.css

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

**Статус**

- За фідбеком користувача, «стрибки» по Y **все ще відтворюються** (тобто проблема не закрита); потрібна додаткова діагностика, можливий відкат.

**Примітки/ризики**

- Якщо «стрибок» лишиться саме під час горизонтального скролу (без ручного vertical-pan/zoom), наступний кандидат — autoscale внесок `sessionRangeBox` (логіка overlaps по visible range може різко змінювати діапазон).

---

## 2025-12-19 — UI_v2 (Web): `tick_count` як volume вимкнено (під флагом) + volume лише на закритті свічки

**Що змінено**

- Прибрано підміну/домішування `tick_count` у `volume` за замовчуванням.
- Volume-гістограма тепер оновлюється лише для закритих барів (`complete=true`) і лише з "чистого" FXCM `volume` (`volume/vol/v`).
- Додано query-параметр `fxcm_tickcount_volume=1`, який дозволяє fallback до `tick_count`/`ticks`/`intensity` (для діагностики або якщо FXCM систематично шле `volume=0`).

**Де**

- UI_v2/web_client/app.js

**Тести/перевірка**

- Не запускалось (зміни у фронтенд JS).

**Примітки/ризики**

- Якщо FXCM віддає `volume=0` навіть на закритих барах, гістограма буде нульовою (це тепер очікувана поведінка без флагу).

---

## 2025-12-19 — UI_v2 (Web): volume як у TradingView (tick volume + autoscale по видимому діапазону)

**Що змінено**

- Дефолт для FXCM volume повернуто до **tick volume** (`tick_count/ticks/intensity`) — це ближче до TradingView для FX/CFD.
  - Можна вимкнути: `?fxcm_tickcount_volume=0`.
- Нормалізовано рендер volume без «стелі»: шкала volume тепер підганяється під **видимий діапазон часу** (з headroom), без кліпінгу значень.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/README.md

---

## 2025-12-19 — UI_v2 (Web): drag по price-scale = zoom по Y (без time-pan) + повернуто vertical-pan

**Що змінено**

- Відновлено vertical-pan у pane (можна рухати графік вверх/вниз).
- Додано lock: під час drag по правій ціновій осі тимчасово вимикаємо `handleScroll.pressedMouseMove`, щоб:
  - drag по price-scale працював як вертикальний zoom/scale,
  - і не перетворювався на горизонтальний time-pan.
- При початку drag по price-scale скидаємо manual price-range (`manualRange`), щоб уникнути конфлікту з нативним scale (симптоми: «стрибок вверх» і вертикальне «розмазування»).
- Wheel по правій ціновій осі перехоплюється і масштабує по Y (через `manualRange`), блокуючи стандартний wheel-обробник, щоб не було time-pan/time-zoom «вбік».

**Відомо / відкладено**

- Інколи графік може «завмирати» під час звичайного pan/drag. `debug_chart=1` наразі не відловлює це стабільно; повернутися пізніше, коли буде репродакшн/дамп.

---

## 2025-12-20 — UI_v2 (Web): фіксація debug_chart + стабілізація взаємодій price-scale (wheel/drag) + `VOL src`

**Контекст**

- Проблема: «стрибки»/мікро-span та фальшиві аномалії в `debug_chart=1` під час `axis_wheel`/`axis_drag`/`applyManualRange`.
- Ціль: зробити поведінку більш TV-like (керований manual-range), і зробити діагностику відтворюваною без залежності від localStorage.

**Що змінено**

- Додано/стабілізовано `VOL src` у Summary (показує джерело volume: `volume|tick_count|ticks|intensity`; не миготить на live-барі).
- `debug_chart=1`: зроблено надійний дамп аномалій (console + глобальні змінні + sessionStorage fallback) і додано `window.__chartDebugDumpNow()`.
- Усунуто фальшивий кейс «ціна=0» у debug: `normalizeBar()` тепер строгіший (порожні строки не стають `0`; бари з невалідним OHLC/<=0 відкидаються), а `debugState.lastPrice` оновлюється лише валідною ціною.
- Перехоплено/унормовано взаємодії по price-axis:
  - `wheel` по правій осі масштабує manualRange (і блокує стандартний wheel);
  - `drag` по правій осі тепер керований: нативний scale бібліотеки тимчасово вимикається, ми масштабуємо manualRange детерміновано, щоб не «прибивало» span до `computeMinPriceSpan()`.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Локально: тільки перевірка на синтакс/помилки редактором (Node відсутній у середовищі).
- Ручна перевірка у браузері: `?debug_chart=1` + перевірка wheel/drag по правій осі.

**Примітки/ризики**

- Це зона високого UX-ризику: будь-яка зміна інтеракцій може відрізнятися між браузерами/тачпадами.
- Якщо «стрибки» лишаться — наступний кандидат: агресивність факторів `exp(intensity)` (потрібно підбирати під конкретний deltaY/тачпад), або конфлікт з іншими обробниками (capture/propagation).

---

## 2025-12-20 — UI_v2 (Web): Stage4 Zones/POI — трейдерський tooltip + zone labels (опційно)

**Що змінено**

- Tooltip зроблено «чесним»: окремі секції `Свічка / Курсор / Hover Zone / Top POI`, hit-test по `cursor_price`.
- `Top POI` показуємо **тільки коли hover відсутній**, щоб не створювати плутанину “POI одна чи дві”.
- `filled`/`dist_atr`: якщо даних немає — показуємо `n/a` (не фейкові нулі).
- Додано `status` (FRESH/TOUCHED/MITIGATED/INVALIDATED) як короткий підсумок стану зони.
- Опційно: `?zone_labels=1` додає markers-підписи зон і не конфліктує з BOS/CHOCH (markers зливаються).

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_viewer_state_builder.py tests/test_ui_v2_viewer_state_server.py` → passed.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Не запускалось (зміни у фронтенд JS).

---

## 2025-12-19 — UI_v2 (Web): прибрано повторні «стрибки» autoscale через sessionRangeBox

**Що змінено**

- Вимкнено внесок `sessionRangeBox` (high/low бокс сесії) в autoscale price-scale: серія більше не розширює діапазон, тож при горизонтальному скролі/перемиканні TF не виникають різкі стрибки шкали через toggle overlaps.
- Режим manualRange (наш vertical-pan/zoom) не змінювався: у ньому `sessionRangeBox` і надалі синхронізується з `manualRange`, щоб не було «стеля/підлога».

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

---

## 2025-12-19 — UI_v2 (Web): прибрано «посіріння» свічок від одиночних volume-спайків після рестартів

**Що змінено**

- `computeRecentMaxVolume()` тепер використовує квантиль (анти-спайк) замість max, щоб один “битий” tick_count/volume не робив більшість свічок майже прозорими.
- Якщо у вікні volume майже завжди відсутній/0 (типово для історії з `UnifiedDataStore`) — volume-based opacity вимикається (повертаємо `recentMax=0`), щоб не створювати хибний сигнал “дані погані”.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

---

## 2025-12-19 — UI_v2 (Web): прибрано стрибки autoscale через rangeAreas (BaselineSeries у setRanges)

**Що змінено**

- `rangeAreas` (box-діапазони) більше не впливають на autoscale price-scale (повертаємо `null` в autoscale-режимі).
- При активному `manualRange` ці серії синхронізуються з manualRange, щоб не було «стеля/підлога» при vertical-pan/zoom.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

---

## 2025-12-19 — UI_v2 (Web): бекфіл більше не скидає viewport/price-scale (прибрано «стрибає при рухах»)

**Що змінено**

- Зміна `firstTime` (розширення історії вліво під час бекфілу/догріву) більше не трактується як «новий датасет».
- Скидання `fitContent()` + `manualRange` робимо лише при реальному reset/shrink датасету або при зміні TF (визначаємо по кроку барів).

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

---

## 2025-12-19 — UI_v2 (Web): стабільна нормалізація volume при підкачці історії + live

**Що змінено**

- Прибрано «стрибки» масштабу volume на live-оновленнях: `volumeScaleMax` більше не підвищується від накопичення live-свічки/одиночних спайків.
- Значення volume для відображення тепер кепляться квантильним максимумом датасету (очікувана нормалізація), щоб нові піки не «вбивали» історію.
- Додано авто-калібрування одиниць volume між історією (HTTP `/ohlcv`) та live/WS (часто `tick_count`): це прибирає ситуацію, коли нові свічки виглядають «огромєнними» порівняно з історією.
- Зафіксовано інваріант рендера: history (candles+volume) — лише `complete=true`, live (liveCandles+liveVolume) — лише `complete=false`; при приході закритого бару live-overlay на тому ж `time` скидається, щоб не було подвійного бруска.
- Для калібрування використовуються окремі `history*` масиви (не мутуються WS-апдейтами), щоб scale не «ламається на продовженні».
- Для live volume при тому ж `time` використовується `liveVolume.update()` замість `setData()` на кожному тіку (менше фліку).

**Де**

- UI_v2/web_client/chart_adapter.js

**Примітки/ризики**

- Дуже рідкісні екстремальні піки volume можуть виглядати «обрізаними» (це свідомий cap для стабільного масштабу).

---

## 2025-12-19 — UI_v2 (Web): інваріант “volume ≠ якість/compute” + hysteresis шкали volume

**Що змінено**

- Додано явний канон у коді: прозорість/opacity — лише стиль, не індикатор якості даних чи “чи щось пораховано”.
- Для complete=true свічок введено clamp альфи `0.78..1.0`, щоб графік ніколи не ставав напівневидимим.
- Для `volumeScaleMax` додано hysteresis: шкала рахується по історії та оновлюється лише при reset датасету або появі нового complete-бару (правий край), без впливу live-апдейтів і backfill.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): volume відносно історії (або live, якщо історія=0) без “все на максимумі”

**Що змінено**

- Зафіксовано фіксовану шкалу volume `0..volumeScaleMax` через `autoscaleInfoProvider` для `volume` та `liveVolume`, щоб прибрати «мигання» масштабу від live-оновлень/visible-range.
- Якщо історія майже без позитивного volume (типово для UDS), шкала бере robust cap з live (p98 по `recentVolumes`) з hysteresis; це робить нові volume-бруски пропорційними (не «під лінієчку на максимумах»).
- Інваріант збережено: live не має права “підбивати” шкалу, якщо історичний volume вже валідний.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): прибрано флік volume при live-оновленнях (не піднімаємо scale на кожному тіку)

**Що змінено**

- Прибрано анти-патерн: `volumeScaleMax` більше не росте від кожного live-тіку (це робило кожен новий volume “максимумом” і викликало постійне миготіння масштабу).
- Live volume для того ж `time` тепер оновлюється через `liveVolume.update()` замість `setData()` на кожному тіку.
- Додано фіксований autoscale для volume-серій `0..volumeScaleMax` та cap відображення, щоб піки не ламали масштаб.
- Для випадку `history volume≈0` scale підтягується рідко (hysteresis) і максимум 1 раз на свічку (bump), щоб live не був постійно clipped.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): калібрування unit-scale для live volume (tick_count) відносно історії

**Що змінено**

- Додано одноразове калібрування `volumeUnitScale` між history volume (HTTP `/ohlcv`) та live/WS (часто `tick_count`), щоб нові бруски не виглядали “гігантами” і не упирались у `capVolumeForDisplay()`.
- Калібрування спрацьовує лише при явному роз'їзді одиниць (≈4×) і має clamp-рейку, щоб не ловити випадковий спайк.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): live tick_count більше не “роздуває” нові volume (калібрування не фіксується завчасно)

**Що змінено**

- Виправлено логіку калібрування `volumeUnitScale`: більше не фіксуємо scale=1 на старті свічки (коли tick_count ще малий), а калібруємо лише при очевидному роз'їзді від `historyVolumeScaleMax`.
- Виправлено `isNewTime` у `setLiveBar()`: визначається ДО оновлення `lastLiveBar`, тому `liveVolume.setData()`/`liveVolume.update()` працюють стабільно і без зайвого фліку на межі свічок.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): прибрано “залипання” volume в максимумі при зміні режиму активності

**Що змінено**

- Виправлено регрес: коли історія має валідний volume, `volumeScaleMax` раніше фіксувався і не зростав зовсім → при підвищенні активності праві бруски кліпились в cap і виглядали як кілька однаково «максимальних».
- Додано рейку: `setLiveBar()` може підняти `volumeScaleMax` **не частіше 1 разу на свічку** і лише при явному кліпінгу (поріг 1.30×), без підняття на кожному тіку.
- Додано інваріант: основна адаптація масштабу робиться **лише на новому закритому барі** через robust p98 по `recentVolumes` + hysteresis (без мигання від live/visible-range).

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): live volume приведено до одиниць історії (overlap-калібрування)

**Що змінено**

- Замість спроб «вгадати» одиниці по max/quantile, додано точну калібровку `volumeUnitScale` по overlap: якщо є бар з тим самим `time` у history (HTTP `/ohlcv`) і в live/WS, беремо співвідношення `histVol / wsVol` як семпл.
- `volumeUnitScale` тепер береться як медіана останніх семплів (до 21), що прибирає “гігантів” і не вимагає підкручувати історичні volume.
- Скидання семплів при новому датасеті (TF/символ/ресет), щоб не тягнути scale між різними інструментами.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

---

## 2025-12-19 — UI_v2 (Web): ретроспектива інциденту volume (флік/«гіганти»/зникнення) + відкат

**Контекст**

- Симптоми в прод/рантаймі (за фідбеком користувача):
  - нові volume-бруски на правому краю часто виглядають як «одразу в потолок» і повторюються як максимуми;
  - інколи гістограма volume «падає вниз/зникає»;
  - періодично повертаються «скачки» масштабу.
- Важливо: ці симптоми виглядають як «биті дані», але переважно були UI-наслідком масштабу/кліпінгу/змішаних одиниць (history volume vs live tick_count).

**Що пробували (хронологічно, на рівні логіки)**

- Anti-spike для opacity:
  - `computeRecentMaxVolume()` переведено з `max` на квантиль, і вимикали volume-based opacity, якщо позитивного volume мало.
  - Мета: прибрати «посіріння/напівневидимість» свічок після рестартів.

- Фіксована шкала volume:
  - `autoscaleInfoProvider` для `volume`/`liveVolume` зафіксував діапазон `0..volumeScaleMax`.
  - Мета: прибрати миготіння масштабу від live-оновлень та visible-range.

- Заборона підняття scale на кожному тіку:
  - `volumeScaleMax` перестали піднімати від live-накопичення всередині свічки.
  - `liveVolume` для того ж `time` переведено з `setData()` на `update()`.
  - Мета: зняти «флік» і ефект «кожна нова свічка = максимум».

- Калібрування одиниць history↔WS:
  - Додано `volumeUnitScale`, щоб приводити live/WS (часто `tick_count`) до масштабу history.
  - Спочатку калібрували по порівнянню з `historyVolumeScaleMax`, потім уточнили, щоб не фіксувати scale=1 «завчасно».
  - Далі додано більш правильний підхід: overlap-калібрування по тому ж `time` (семпли `histVol/wsVol`, медіана семплів).
  - Мета: прибрати «гігантів» нових брусків на фоні історії.

- Боротьба з кліпінгом у cap:
  - Додано контрольований `bump` масштабу (не частіше 1 разу на свічку), коли live явно впирається в cap.
  - Додано оновлення масштабу лише на новому закритому барі через robust p98 + hysteresis.
  - Виправлено порядок у `updateLastBar()`, щоб новий великий `vol` потрапляв у `recentVolumes` ДО перерахунку масштабу.
  - Мета: прибрати «кілька однаково максимальних» брусків праворуч.

**Що пішло не так (чому інцидент не вважаємо закритим)**

- Частина покращень проходила тести та виглядала правильно на синтетичних сценаріях, але в реальному рантаймі:
  - «скачки в потолок» повертались (ймовірно через поєднання: різні одиниці + кліпінг + момент перерахунку cap);
  - у певні моменти користувач бачив «volume зник/впав вниз».
- Користувач повідомив, що після кількох ітерацій стало гірше і він зробив відкат до попереднього стану UI.

**Статус**

- НЕ ЗАКРИТО як “готово до релізу”: проблема volume-UX у рантаймі (за фідбеком користувача) лишилась.
- Зафіксовано рішення користувача: відкат UI до попередньої поведінки (конкретний SHA/тег не зафіксовано у цьому журналі).

**Нотатки для наступної спроби (без коду в цьому записі)**

- Діагностика має починатися з 1 скріна/лог-оверлею `?debug=1`: `wsVolRaw`, `volScaled`, `volumeUnitScale`, `volumeScaleMax`, `historyVolAtTime`, щоб відрізнити:
  - «дані стрибнули» (WS прислав батч/накопичення),
  - від «UI стрибнув» (перерахунок масштабу/кліпінг/ресет датасету).

---

## 2025-12-19 — Runtime/Data: піднято ліміт RAMBuffer для довшої історії (під 30d 1m)

**Що змінено**

- Збільшено `RAM_BUFFER_MAX_BARS` з ~30k до 60k (per symbol/timeframe), щоб безболісно тримати до ~30 днів 1m-барів у RAM, якщо contract-of-needs/бекфіл це попросить.

**Де**

- app/settings.py

**Тести/перевірка**

- `pytest -q tests/test_smc_universe_cfg.py` → passed.

**Примітки/ризики**

- Це лише «рейка» для кешу: фактична глибина історії визначається `smc_universe.fxcm_contract.symbols[].min_history_bars` у config/datastore.yaml та можливостями FXCM history API (rate/timeout).

---

## 2025-12-19 — Stage1/Data: contract-of-needs переведено на 30d історії (XAUUSD)

**Що змінено**

- `smc_universe.fxcm_contract` для `xauusd` переведено з 14d (20160) на 30d (43200) у «1m барах».

**Де**

- config/datastore.yaml

**Тести/перевірка**

- `pytest -q tests/test_smc_universe_cfg.py` → passed.
- `python -m tools.tf_coverage_report --symbol xauusd --tfs 1m 5m 1h 4h --window-minutes 43200` → зараз показує `BAD` на локальних snapshot'ах (це очікувано, доки бекфіл/догрів не закриє 30d без гепів).

**Примітки/ризики**

- Етап 1 вважаємо «DONE» лише коли coverage для контрольного вікна по 5m/1h/4h (і бажано 1m) стає `OK` (gaps=0, offgrid=0) у реальних snapshot-файлах UDS.

---

## 2025-12-19 — S3 warmup requester: поступове нарощування історії (без одномоментного 14d)

**Що змінено**

- Змінено політику `prefetch_history`: requester більше не просить одразу всю контрактну глибину (напр. 14d), а нарощує `request_bars` поступово від поточного `bars_count` у `UnifiedDataStore`.
- Крок нарощування = `SMC_RUNTIME_PARAMS.limit` (типово 300; для bootstrap можна поставити 50), тому можна стартувати з live + малого вікна і поступово дорощувати історію у фоні.
- Прогрес не зберігається окремо: після рестарту requester бере актуальний `bars_count` з UDS і продовжує з нього.

**Де**

- app/fxcm_warmup_requester.py

**Тести/перевірка**

- (потрібно прогнати локально) `pytest -q tests/test_s3_warmup_requester.py`

---

## 2025-12-18 — Deploy/VPS: HTTPS origin (443) для Cloudflare + стійкість до падіння Redis

**Що змінено**

- Додано origin HTTPS (443) у nginx-конфіг для same-origin proxy під Cloudflare Origin CA; шляхи/WS upgrade не змінені.
- Додано самовідновлення при тимчасовій втраті Redis/мережі: FXCM лістенери та UI_v2 раннери тепер роблять reconnect з exponential backoff замість падіння процесу.
- Додано тести на reconnect-цикл (імітація Redis down → повторна підписка).

**Де**

- deploy/nginx/smc_ui_v2.conf
- data/fxcm_ingestor.py
- data/fxcm_price_stream.py
- data/fxcm_status_listener.py
- app/main.py
- tests/test_redis_reconnect_loops.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_redis_reconnect_loops.py tests/test_smc_pipeline_integration.py tests/test_app_smc_producer_pipeline_local.py tests/test_app_smc_producer_history_gate.py tests/test_ui_v2_viewer_state_builder.py` → `20 passed`.

**Примітки/ризики**

- `curl -I` робить HEAD і може давати `405` (це очікувано); для smoke-check використовувати GET.
- Якщо Cloudflare DNS вказує на tunnel (`*.cfargotunnel.com`), а тунель не активний — буде `502` навіть при живому origin.

---

## 2025-12-18 — Cleanup: прибрано невикористані compat-модулі та дубль nginx-конфіга

**Що змінено**

- Видалено невикористані thin-compat wrappers (0 імпортів у repo), щоб не вводили в оману й не дублювали SSOT.
- Видалено дубль nginx-конфіга; SSOT для same-origin проксі — `deploy/nginx/smc_ui_v2.conf`.
- Доки/посилання вирівняно під канонічний конфіг.

**Де**

- UI_v2/schemas.py (видалено)
- data/fxcm_schema.py (видалено)
- deploy/nginx/aione-smc.conf (видалено)
- deploy/cloudflare_tunnel/README.md
- UPDATE.md

**Тести/перевірка**

- `python tools/audit_repo_report.py` → OK
- `pytest tests/test_redis_reconnect_loops.py tests/test_ui_v2_smc_viewer_broadcaster.py tests/test_fxcm_schema_and_ingestor_contract.py` → `16 passed`

---

## 2025-12-18 — Dev: локальна ізоляція (VPS FXCM → local Redis relay) для безпечної розробки

**Що змінено**

- Додано інструмент `tools/redis_fxcm_relay.py`: читає FXCM Pub/Sub з remote Redis (зазвичай через SSH-тунель до VPS) і перепубліковує в local Redis на тих самих каналах.
- Додано шаблон `.env.local.example` для локального профілю (127.0.0.1 + локальні порти), щоб локальний SMC/UI не писав у VPS Redis і не “змішував” потоки.
- Hotfix: виправлено синтаксичну помилку в раннері UI_v2 HTTP-сервера (щоб локальний запуск `app.main` знову стартував).

**Де**

- tools/redis_fxcm_relay.py
- .env.local.example
- app/main.py

**Тести/перевірка**

- Smoke: імпорт `python -c "import tools.redis_fxcm_relay"` → OK.
- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py` → passed.

---

## 2025-12-18 — Docs: матриця deploy-режимів (SSOT) у README

**Що змінено**

- Додано короткий список режимів деплою з явним SSOT-набором файлів (VPS A-record, VPS Tunnel, Windows/Docker Tunnel).

**Де**

- README.md

**Тести/перевірка**

- Не застосовується (лише документація).

---

## 2025-12-18 — Docs: повне актуалізування README під поточний рантайм

**Що змінено**

- Опис приведено до реальності: `app.main` = SMC-only пайплайн + UI_v2; FXCM дані приходять через Redis від зовнішнього конектора.
- Прибрано застарілі згадки (`WSWorker`, `stage1/`, `.env.example`, `app/thresholds.py`) і вирівняно секції запуску/конфігу/деплою.
- Додано явні SSOT-посилання на docs та runbooks, щоб README не дублював детальні інструкції.

**Де**

- README.md

**Тести/перевірка**

- Не застосовується (лише документація).

---

## 2025-12-18 — Docs: UI_v2 README під SMC-only + VPS quickstart

**Що змінено**

- Оновлено `UI_v2/README.md`: прибрано Stage1-формулювання та згадки про видалений `schemas` модуль; контракти прив’язані до SSOT `core/contracts/*`.
- У README додано короткий операційний quickstart для VPS (Ubuntu: systemd + nginx + Redis) з посиланням на SSOT deploy-файли.

**Де**

- UI_v2/README.md
- README.md

**Тести/перевірка**

- Не застосовується (лише документація).

---

## 2025-12-18 — Stage0: TF-правда (SSOT) + чесні гейти compute для 5m

**Що змінено**

- Додано SSOT TF-план `SMC_TF_PLAN`: `tf_exec=1m`, `tf_structure=5m`, `tf_context=(1h,4h)`.
- Вирівняно runtime-дефолти: `tf_primary := 5m`, `tfs_extra := (1m,1h,4h)`; `15m` не використовуємо на цьому етапі.
- Додано Stage0 гейти в побудові `SmcHint`: якщо немає 5m або замало/протух хвіст — SMC-core compute пропускається, але UI отримує стабільний `smc_hint.meta` з `gates` + `tf_plan` + `telemetry` + (`history_state/age_ms/last_ts/lag_ms`).

**Де**

- config/config.py
- app/smc_producer.py
- smc_core/input_adapter.py
- tests/test_smc_tf_truth_primary_present.py

**Тести/перевірка**

- `pytest -q tests/test_smc_tf_truth_primary_present.py` → passed.

---

## 2025-12-17 — Deploy: same-origin reverse-proxy (Cloudflare Tunnel → nginx → 8080/8081)

**Що змінено**

- Додано мінімальний nginx-конфіг для same-origin: `/` → `8080`, `/smc-viewer/stream` (WS) → `8081`.
- Додано коротку інструкцію під Cloudflare Tunnel (Public Hostname → nginx:80) і smoke-check.

**Де**

- deploy/nginx/smc_ui_v2.conf
- deploy/cloudflare_tunnel/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py` → `5 passed`.

---

## 2025-12-17 — Prod domain: `aione-smc.com` + nginx (Docker/Windows) на 80 + smoke-test

**Що змінено**

- Нормалізовано домен у доках під `https://aione-smc.com` (альтернатива: `https://www.aione-smc.com`).
- Для Windows зроблено docker-nginx "primary": `deploy/viewer_public/docker-compose.yml` тепер слухає `80:80`.
- У docker nginx конфігу додано `server_name aione-smc.com www.aione-smc.com;` і вимкнено буферизацію для WS `/smc-viewer/stream`.
- Додано PowerShell smoke-test для same-origin (HTTP `/` + JSON snapshot).
- README: додано коротку прод-інструкцію "Cloudflare Tunnel → nginx (Docker) → UI_v2".

**Де**

- deploy/viewer_public/docker-compose.yml
- deploy/viewer_public/nginx.conf
- deploy/nginx/smc_ui_v2.conf
- deploy/cloudflare_tunnel/README.md
- tools/smoke_same_origin.ps1
- README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `13 passed`.

---

## 2025-12-17 — UI_v2 (Web): FXCM live WS за замовчуванням на `aione-smc.com` (same-origin `/fxcm/*`)

**Що змінено**

- На прод-доменах `aione-smc.com`/`www.aione-smc.com` FXCM live WS тепер увімкнений за замовчуванням (без query-параметрів).
- same-origin для FXCM (`/fxcm/*`) вважається увімкненим за замовчуванням на прод-домені; порт `:8082` використовується лише в локальному dev.
- Додано ручний стоп: `fxcm_ws=0` вимикає live навіть у проді.
- Додано захист від “вічного молотіння”: для OHLCV/ticks reconnect зупиняється після 3 невдалих спроб.
- Доки: додано smoke-check для WS `wss://aione-smc.com/fxcm/ohlcv?...` і приклад cloudflared ingress YAML для apex+www одним тунелем.

**Де**

- UI_v2/web_client/app.js
- deploy/viewer_public/nginx.conf
- README.md
- deploy/viewer_public/README.md
- deploy/cloudflare_tunnel/README.md
- deploy/cloudflare_tunnel/cloudflared.ingress.example.yml

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `13 passed`.

---

## 2025-12-17 — Docs: Windows named tunnel (короткий runbook) + актуалізація web-доків

**Що змінено**

- Додано короткий runbook для Windows named tunnel + швидкий дебаг 502 у форматі “3 команди”.
- Оновлено web-інструкції/посилання: прибрано застарілі згадки Quick Tunnel/`:8088`, приклади приведено до `aione-smc.com` і локального `127.0.0.1:80`.

**Де**

- docs/runbook_cloudflare_named_tunnel_windows.md
- docs/runbook_tradingview_like_live_public_domain.md
- docs/README.md
- docs/_inventory.md
- docs/stage1_pipeline.md
- deploy/viewer_public/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `13 passed`.

---

## 2025-12-17 — Deploy: вимкнено контейнерний cloudflared (лишаємо лише Windows service)

**Що змінено**

- Сервіс `cloudflared` у docker-compose закоментований, щоб випадково не стартував у Docker.
- Канонічний шлях для прод-домену: Windows service Cloudflared → nginx у Docker на `:80` (same-origin reverse-proxy).

**Де**

- deploy/viewer_public/docker-compose.yml

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `13 passed`.

## 2025-12-16 — Dev process: зафіксовано правило «зміна → тест → UPDATE → відповідь»

**Що змінено**

- Додано надважливе правило робочого процесу в пам’ять Copilot: будь-які правки виконуються лише в порядку **зміна → тест → UPDATE → відповідь у чаті**.
- Уточнено, що оновлюємо `UPDATE_CORE.md`, якщо файл існує, інакше — `UPDATE.md`.

**Де**

- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_types.py` → `1 passed`.

## 2025-12-16 — UI_v2 (Web): hotfix — виправлено падіння `chart_adapter.js` (Unexpected identifier 'window')

**Що змінено**

- Виправлено кінець IIFE у `chart_adapter.js`: прибрано зайву `}`, через яку браузер падав з `Uncaught SyntaxError: Unexpected identifier 'window'` і `chart_adapter` не завантажувався.
- Додано cache-busting query параметри в `index.html` для `chart_adapter.js`/`app.js`, щоб Cloudflare/браузер не тримали застарілий бандл.

**Де**

- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/index.html

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

**Що важливо зберегти при відкаті/повторі**

- Cache-busting для фронтенд-скриптів: без `?v=...` Cloudflare/браузер може віддавати старий `chart_adapter.js`, і тоді stacktrace/номери рядків не відповідають реальній версії.
- Інваріант `chart_adapter.js`: експорт `window.createChartController = createChartController;` має бути в кінці IIFE; зайва/зникла `}` легко дає `Unexpected identifier 'window'`.
- Симптом `ReferenceError: setBars is not defined` майже завжди означає, що виконання/`return { setBars, ... }` відбулося поза `createChartController()` (поламана область видимості через дужки або «випавший» фрагмент коду).
- Перед дебагом WS (Cloudflare tunnel) спочатку перевіряти, що `chart_adapter.js` реально завантажився і ініціалізував `window.createChartController`.

**Чек-ліст повтору після відкату (рекомендовано)**

- Виставити cache-busting (`?v=...`) або зробити hard refresh (`Ctrl+F5`) з вимкненим кешем у DevTools.
- У консолі браузера перевірити: `typeof window.createChartController === 'function'`.
- Якщо `chart_adapter` не завантажився — спочатку фіксити синтаксис/структуру IIFE, а не WS.
- Лише після цього перевіряти WS: чи відкривається `/fxcm/ohlcv?...` локально (без Cloudflare), і вже потім — через tunnel.

## 2025-12-16 — UI_v2 (Web): антишум оверлеїв (viewport-aware) + BOS/CHOCH стабільний рендер

**Що змінено**

- Events (BOS/CHOCH): стабільний рендер — marker+короткий текст + трикутник-підсвітка та axis label (без горизонтальної лінії).
- Events (BOS/CHOCH): snap часу події до найближчої існуючої свічки (через binary search по таймам датасету), щоб markers гарантовано з’являлись.
- Events (BOS/CHOCH): shape визначається по kind (CHOCH vs BOS), direction впливає на позицію marker (above/below).
- Pools/Zones/OTE: додано адаптивний «антишум» без прив’язки до конкретних цін:
  - фільтр по viewport price-range з margin;
  - кластеризація по біну (binSize від span/refPrice);
  - budgets по TF (через `barTimeSpanSeconds`).
- Оверлеї підлаштовуються під zoom/scroll: кешуються останні payload-и та перерендерюються на `visibleLogicalRange` change без мережевих запитів.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): декластеризація рівнів + стабільні BOS/CHOCH

**Що змінено**

- Виправлено `safeUnixSeconds()` для ISO-рядків (через `Date.parse()`), щоб події/діапазони не зникали через `NaN` у time.
- Для liquidity pools у фронтенді підхоплено `strength` та `touches` (якщо є у payload), без змін бекенду.
- BOS/CHOCH: маркери стали детермінованими (case-insensitive), position/shape залежать від `direction`, CHOCH не плутається з BOS.
- BOS/CHOCH: додано snap часу події до найближчої існуючої свічки (із відсіканням, якщо занадто далеко від барів).
- BOS/CHOCH: вимкнено рендер «трикутників» (overlay), залишено лише текстові markers над свічкою.
- BOS: маркери уніфіковано в синій колір (щоб не виглядали як «червоні квадратики» біля тексту).
- BOS: зафіксовано як “OK” у такому вигляді — лише напис `BOS` над свічкою + синій marker (без будь-яких трикутників/overlay).
- Виправлено «самоплив» графіка вправо: при `setBars()` viewport зберігається, якщо користувач не знаходиться на правому краї (follow).
- Зменшено «випадкові стрибки»/вертикальне розтягування під час перегляду: `setBars()` більше не скидає ручний price-range на кожному polling-оновленні (скидання лише при реальному reset датасету).
- Додатково прибрано «подвійне масштабування» по wheel: wheel по price-axis тепер перехоплюється у capture-фазі та гаситься (`stopImmediatePropagation`), щоб lightweight-charts не застосовував власний scale паралельно з нашим manualRange.
- Стабілізовано wheel-скейл по ціні: детекція price-axis має fallback, якщо `priceScale("right").width()` тимчасово повертає 0 (наприклад під час resize).
- Додано декластеризацію liquidity pools/zones перед рендером:
  - pools: дедуп близьких рівнів, ліміт локальних ліній (≤6), 2 ключові рівні з axisLabel, «глобальні» рівні лише як axisLabel.
  - zones: фільтр по вікну фокусу, ліміт ≤3, тонкі зони рендеряться як один рівень.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): палітри для «A по даних» зон (NY/Tokyo/London)

**Що змінено**

- Додано палітри для data-driven high/low box («A по даних») залежно від активної сесії:
  - New York — зелений
  - Tokyo — синій
  - London — оранжевий
- Колір застосовується як **заливка** (без ліній) через BaselineSeries options.
- Виправлено autoscale для high/low box: тепер правий price scale враховує `low/high`, щоб зона не виглядала «напівзаповненою».

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): сесії (UTC) як блоки + персист шарів + Baseline для range box

**Що змінено**

- Додано шар **«Сесії (UTC)»** (Asia/London/New York) з перемикачем у меню «Шари» та в мобільному drawer.
- Перероблено рендер сесій: замість «полос» (histogram по кожному бару) малюємо **суцільні блоки** на окремій шкалі 0..1 (не залежить від ціни інструмента).
- Додано “A по даних” для поточної сесії: UI бере `high/low` з `fxcm:status.session.symbols[]` (per Symbol/TF) і малює **бокс між low↔high** (без ліній) через BaselineSeries.
- Додано WS endpoint `/fxcm/status` у FXCM WS-міст, щоб web UI міг отримувати `fxcm:status` у public/same-origin режимі.
- Виправлено “box” для діапазонів у структурі: `setRanges()` переведено з 2×AreaSeries на **BaselineSeries**, щоб зона була між `min↔max`, а не «до нуля».
- Виправлено керування шаром сесій: `setSessionsEnabled()` експортується з chartController та застосовується одразу після ініціалізації графіка.
- Додано персист `layersVisibility` у `localStorage`, щоб перемикачі шарів (включно з сесіями) **не збивались після рестарту/рефреша**.

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js
- UI_v2/fxcm_ohlcv_ws_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): «A по даних» без ліній + один шар сесій + фікс vertical-pan

**Що змінено**

- Для «A по даних» (high/low box) часові межі сесії знову рахуються **по фіксованому UTC-розкладу** (Asia/London/NY), а не з `fxcm:status.current_open_utc/current_close_utc`.
- Прибрано накладання «двох версій» сесій: старий кольоровий фон Asia/London/NY вимкнено; лишився лише data-driven high/low box під тим самим перемикачем.
- Прибрано горизонтальні лінії у high/low box: вимкнено baseline/series lines (`baseLineVisible=false`, `lineVisible=false`, прозорі line colors як страховка).
- Виправлено проблему «стеля/підлога» при вертикальному drag по графіку: синхронізовано `autoscaleInfoProvider` для `candles/liveCandles/sessionRangeBox`, щоб manual range не “склеювався” з автоскейлом інших серій.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py` → `11 passed`.

---

## 2025-12-13 — S2/S3: `fxcm:commands` + стабільний payload + тести requester-а

**Що змінено**

- Зафіксовано дефолтний канал команд для FXCM-конектора: `fxcm:commands` (без fallback на `ai_one:admin:commands`).
- Уніфіковано S2-логіку в pure-функцію `classify_history()` (insufficient/stale_tail) та вирівняно ключ `last_open_time_ms`.
- Оновлено S3 requester: стабільна JSON-схема команди з блоками `s2{...}` та `fxcm_status{...}`, INFO-лог у заданому форматі.
- Додано/закріплено reset “active issue”: при переході history_state в `ok` requester очищає rate-limit,
  щоб при наступному погіршенні можна було одразу знову відправити команду.
- Додано мінімальну документацію контракту S2/S3.

**Де**

- config/config.py
- app/fxcm_history_state.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- tests/test_s2_history_state.py
- tests/test_s3_warmup_requester.py
- docs/uds_smc_update_2025-12-13.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_s2_history_state.py tests/test_s3_warmup_requester.py` → `8 passed`.

---

## 2025-12-14 — Public Viewer (UI_v2) на ПК: same-origin фронт + Docker nginx allowlist + tunnel

**Що змінено**

- У UI_v2 фронтенді прибрано жорсткі дефолти `127.0.0.1:8080/8081/8082`: тепер HTTP працює через `window.location.origin`, WS — через `ws://|wss://` + `window.location.host` (same-origin).
- FXCM dev WS міст (8082) вимкнений за замовчуванням у публічному режимі, щоб не було нескінченних reconnect’ів; дозволяється лише на `localhost/127.0.0.1` або з явним `?fxcm_ws=1`.
- Додано периметр для публічного доступу без VPS: `deploy/viewer_public/` (Docker Compose) з `nginx` allowlist + rate-limit та `cloudflared` tunnel.
- У nginx allowlist додано статику за розширеннями (js/css/…); API/WS прокситься лише по потрібних маршрутах; усе інше → 404.
- Для WS proxy додано `proxy_read_timeout`/`proxy_send_timeout` 3600s; також приховується `Access-Control-Allow-Origin` з бекенду (same-origin).
- Додано короткий Troubleshooting у runbook (найчастіші фейли: `0.0.0.0`, статика allowlist, WS upgrade, token).
- Виправлено nginx конфіг на формат `conf.d/default.conf` (замість main `nginx.conf`), щоб уникнути restart-loop контейнера.
- Переведено `cloudflared` на Cloudflare Quick Tunnel без домену/токена (публічний URL `https://*.trycloudflare.com` береться з логів).
- Уточнено `UI_v2/web_client/README.md`: FXCM WS міст (8082) — локальний dev-інтерфейс і не має використовуватись у публічному режимі.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md
- deploy/viewer_public/docker-compose.yml
- deploy/viewer_public/nginx.conf
- deploy/viewer_public/.env.template
- deploy/viewer_public/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-14 — Документація: синхронізація FXCM контрактів (channels/payload/HMAC/commands)

**Що змінено**

- Уточнено контракт `fxcm:ohlcv`: додано `source` (опційно), описано `complete/synthetic` як опційні бар-поля та правило: UDS зберігає лише complete.
- Зафіксовано правило HMAC: `sig` рахується/перевіряється лише по `{"symbol","tf","bars"}` (root-поля на кшталт `source` не входять у підпис).
- Додано/уточнено огляд каналів `fxcm:status`, `fxcm:price_tik` та `fxcm:commands` (включно з `fxcm_set_universe` як частиною контракту конектора).
- Прибрано двозначність щодо cadence `fxcm:price_tik`: це cadence конектора, а не «таймер оновлення UI».

---

## 2025-12-14 — UI_v2 (Web): десктоп-полірування статусів/графіка

**Що змінено**

- Розділено «транспортний» статус (WS) та стан ринку FX (`market_state`) у два окремі pill-и, щоб уникнути суперечливих повідомлень.
- Прибрано подвійні рамки в зоні графіка: контейнер графіка більше не малює внутрішній бордер.
- Прибрано зайві відступи у non-fullscreen: `card-chart` без padding, щоб графік займав максимум площі в межах єдиної рамки.
- Прибрано «0» бейдж на шкалі обʼєму: `lastValueVisible/priceLineVisible` вимкнені для histogram series.
- Зменшено правий «порожній» відступ у time scale: `rightOffset=0`; `fitContent()` виконується лише один раз на новий датасет.
- Виправлено ситуацію, коли поточна ціна показувалась як `-`: додано fallback на close останньої complete-свічки.
- Додано hover-підказку по свічці (ціна close + обсяг) з затримкою ~1с, щоб дивитись обсяги без шуму на осях.
- Повернуто очікувану поведінку для поля «Ціна»: якщо ціни в payload немає, показуємо порожньо (а не `-`).

---

## 2025-12-15 — SMC pipeline: узгодження FX market_state з ticks_alive

**Що змінено**

- Якщо `fxcm:status` дає суперечливу комбінацію `market=closed` + `price_state=ok` (ticks alive), SMC більше не переходить у `IDLE fxcm_market_closed` при свіжому статусі.
- У console status bar у такій ситуації показуємо `market=open`, щоб не вводити в оману (за умови, що конектор не `down`).

**Де**

- app/smc_producer.py
- app/console_status_bar.py
- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py`.

---

## 2025-12-15 — SMC: `ohlcv!=ok` не блокує цикл (live price працює при delayed/lag)

**Що змінено**

- SMC idle-gate більше не блокує цикл при `market=open` + `price=ok`, навіть якщо `ohlcv=delayed/lag/down`.
- `ohlcv` у `fxcm:status` трактуємо як діагностику: фіксуємо причину як `fxcm_ohlcv_<state>_ignored`, але продовжуємо цикл, щоб оновлювати `current_price` з `fxcm:price_tik`.

**Де**

- app/smc_producer.py
- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py` → `4 passed`.

---

## 2025-12-15 — Legacy viewer (UI_V2_ENABLED=0): live price з `fxcm:price_tik`

**Що змінено**

- Experimental viewer (SMC Viewer · Extended) тепер додатково підписується на `fxcm:price_tik` і оновлює `Price` між SMC снапшотами.
- Для тикових апдейтів використовуємо останній збережений SMC asset/meta і лише підміняємо поле `viewer_state.price` на `mid` з тика.

**Де**

- UI/ui_consumer_experimental_entry.py
- tests/test_ui_consumer_entry.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_consumer_entry.py` → `4 passed`.

---

## 2025-12-15 — UI_v2: web-only стек + окремий перемикач debug viewer

**Що змінено**

- `UI_V2_ENABLED` тепер керує лише UI_v2 web-стеком (HTTP/WS) і не використовується як «перемикач типів viewer».
- Прибрано автозапуск `UI_v2.debug_viewer_v2` з пайплайна (UI_v2 стає чисто веб-шаром).
- Додано окремий ENV-прапорець `DEBUG_VIEWER_ENABLED=1|0` для запуску console viewer `SMC Viewer · Extended` незалежно від `UI_V2_ENABLED`.

**Де**

- app/main.py
- tests/test_app_main_ui_toggle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_main_ui_toggle.py` → `2 passed`.

---

## 2025-12-15 — UI_v2: прибирання debug/rich артефактів (prod cleanup)

**Що змінено**

- Прибрано з `UI_v2` консольні/дев-модулі: `debug_viewer_v2.py`, `rich_viewer.py`, `rich_viewer_extended.py`.
- Видалено застарілі конфіг-поля `UI_V2_DEBUG_VIEWER_ENABLED` та `UI_V2_DEBUG_VIEWER_SYMBOLS`.
- Видалено тести, що були привʼязані до rich/debug viewer.
- Оновлено документацію `UI_v2` під web-only роль.

**Де**

- UI_v2/**init**.py
- UI_v2/README.md
- config/config.py
- tests/test_ui_v2_debug_viewer_v2.py (видалено)
- tests/test_ui_v2_rich_viewer_extended.py (видалено)

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_main_ui_toggle.py tests/test_ui_v2_viewer_state_builder.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_smc_viewer_broadcaster.py tests/test_ui_v2_smc_viewer_broadcaster_metrics.py tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_ohlcv_provider.py` → `22 passed`.

---

## 2025-12-15 — UI_v2 (Web): realtime `complete=false` свічки + лаг по live freshness + флаг `fxcm_apply_complete`

**Що змінено**

- У web UI “Лаг (с)” тепер показує **свіжість live-стріму** (ticks/OHLCV), якщо FXCM WS увімкнено й live події приходять; інакше — fallback на `meta.fxcm.lag_seconds`.
- Додано флаг `fxcm_apply_complete=1|0` (query param) для керування тим, чи треба **одразу** прибирати live overlay при приході `complete=true`.
- Тиковий WS також вважаємо “live” (впливає на live-індикатор і лаг), щоб не зависати у `LIVE: OFF`, якщо OHLCV live тимчасово тихий.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): FXCM OHLCV WS сумісність `timeframe` + volume від `tick_count`

**Що змінено**

- FXCM WS міст для `/fxcm/ohlcv` тепер приймає `timeframe` як синонім `tf`, щоб не “губити” повідомлення з Redis `fxcm:ohlcv`, якщо конектор шле іншу назву поля.
- У web UI для live OHLCV обсяг/інтенсивність беремо з `volume`, а якщо його немає — з `tick_count` (fallback), щоб гістограма обсягів реально малювалась.

**Де**

- UI_v2/fxcm_ohlcv_ws_server.py
- UI_v2/web_client/app.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): діагностика WS close (code/reason) + стійкість до Redis hiccups

**Що змінено**

- У браузері (app.js) лог для `WS onclose` тепер показує `code/wasClean/reason`, щоб швидко відрізняти 1011 (internal) від handshake/мережевих розривів.
- WS сервер `ViewerStateWsServer` став більш стійким до тимчасових винятків Redis/pubsub: не валимо весь handler 1011 при разовому `get_message()`/send фейлі.

**Де**

- UI_v2/web_client/app.js
- UI_v2/viewer_state_ws_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): /favicon.ico без 404 (No Content)

**Що змінено**

- HTTP сервер UI_v2 тепер відповідає `204 No Content` на `GET /favicon.ico`, щоб браузер не засмічував консоль 404-ками в публічному режимі.

**Де**

- UI_v2/viewer_state_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): live price від ticks + volume fallback при `volume=0`

**Що змінено**

- Виявлено реальний кейс FXCM: у `fxcm:ohlcv` live-бар може мати `volume=0.0` і водночас `tick_count>0`.
  У UI тепер беремо **перше додатне** значення серед `volume/tick_count/...`, щоб гістограма обсягів не була завжди нульова.
- Додано додатковий fallback: якщо FXCM live-бар не містить volume/tick_count, UI накопичує локальний `tick_count` з тикового WS і підставляє його як інтенсивність (щоб не було миготіння і щоб volume було на 5m).
- Стабілізовано видимість volume-гістограми при масштабуванні: для histogram більше не використовується volume-залежна прозорість (бруски не «провалюються» в майже невидимі).
- Стабілізовано volume при горизонтальному скролі: autoscale volume-шкали тепер фіксується по глобальному max обсягу (не по видимому фрагменту).
- Уточнено autoscale volume: max для шкали береться по всьому датасету з robust-кепом по квантилю (p98), щоб одиночні спайки не сплющували решту обсягів.
- Ціна у summary/мобільному UI тепер оновлюється від тикового WS (`/fxcm/ticks`) і вважається “свіжою” до `FXCM_LIVE_STALE_MS`.
- Live overlay (candles) тепер показує live price на шкалі/лейблі синхронно зі свічкою (а не лише по закритій свічці).
- Тимчасово додано поле у шапку summary: `VOL src` (показує `tick_count` або `volume`) для швидкої діагностики.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — SMC: толерантність `stale_tail` у вихідні + requester/порти UI_v2

**Що змінено**

- Додано helper `_history_ok_for_compute(..., allow_stale_tail)` і дозволено `stale_tail` як OK лише коли фід деградований: `market!=open` або `ohlcv_state in {delayed, down}`; додано прапорець `meta.s2_stale_tail_expected`.
- У S3 requester: для `stale_tail` на `1m` відправляємо `fxcm_warmup` (а не `fxcm_backfill`), щоб не слати команду, яку конектор може не підтримувати.
- У `app.main`: якщо порти UI_v2 (HTTP/WS/FXCM WS) зайняті, пайплайн більше не завершується — логуються попередження і процес продовжує працювати.
- Додано утиліту діагностики `tools.debug_fxcm_channels` (NUMSUB + лічильники повідомлень за заданий інтервал).
- UI_v2: виправлено побудову WS base URL для dev-режиму (HTTP на :8080 → WS на :8081), додано fallback для `file://`, та підтримку відкриття UI через приватну LAN IP (RFC1918) без вимкнення FXCM dev WS.
- FXCM інжестор: `fxcm:status.ohlcv=down` більше не блокує запис; якщо конектор надсилає лише `complete=false`, інжестор фіналізує попередній live-бар при появі нового `open_time` і пише його в UDS (щоб UI мав історію свічок).

**Де**

- app/smc_producer.py
- app/fxcm_warmup_requester.py
- app/main.py
- config/config.py
- tools/debug_fxcm_channels.py
- data/fxcm_ingestor.py
- UI_v2/web_client/app.js
- tests/test_app_smc_producer_history_gate.py
- tests/test_s3_warmup_requester.py
- tests/test_fxcm_schema_and_ingestor_contract.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_history_gate.py tests/test_s3_warmup_requester.py tests/test_app_console_status_bar.py tests/test_app_smc_producer_fxcm_idle.py` → `17 passed`.
- Запущено таргетно: `pytest tests/test_fxcm_schema_and_ingestor_contract.py tests/test_ingestor.py tests/test_fxcm_ingestor_universe_filter.py` → `21 passed`.

---

## 2025-12-15 — FXCM контракт: тести complete/synthetic + HMAC extra fields + gap-check (--hours)

**Що змінено**

- Уточнено/розширено контрактні тести FXCM інжестора: live-бар (`complete=false`) не пишеться в UDS; synthetic з `complete=true` пишеться.
- Додано тест на forward-compatibility підпису: HMAC лишається валідним при появі додаткових/невідомих полів усередині `bars[*]`.
- QA gap-check: після звірки репозиторію виявлено, що вже існує універсальна утиліта `tools/uds_ohlcv_gap_check.py` (UDS + режим `--snapshot-file`).
  Щоб не дублювати функціонал, додано зручний режим `--hours` (останні N годин від кінця історії) саме в існуючу утиліту.
  Дублюючий `tools/qa_check_1m_gaps.py` прибрано.

**Де**

- tests/test_fxcm_schema_and_ingestor_contract.py
- tests/test_ingestor.py
- tools/uds_ohlcv_gap_check.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_fxcm_schema_and_ingestor_contract.py tests/test_ingestor.py` → `21 passed`.

---

## 2025-12-15 — SMC: прибрано S2-блокування (always-on) + S3 requester просить останні 300 барів

**Що змінено**

- Прибрано жорсткий S2-gate в `smc_producer`: цикл більше не робить early-continue з `cycle_reason=smc_insufficient_data` через `insufficient/stale_tail`.
- `process_smc_batch` переведено в деградований режим: якщо OHLCV немає/замало,
  все одно публікуємо `current_price` з тика (`price_stream`) і прозорий `signal`
  (`SMC_NO_OHLCV` / `SMC_WARMUP`), щоб UI не був порожнім.
- Вирівняно логіку lookback: `smc_producer` тримає `min_bars/target_bars` у межах
  `SMC_RUNTIME_PARAMS.limit` (типово 300) і не «висить» на великих `contract_min_bars`.
- S3 requester більше не намагається витягувати великі обʼєми історії по контракту:
  тепер для команд warmup/backfill просить «останні N барів» (N береться з
  `SMC_RUNTIME_PARAMS.limit`, дефолт 300) і додає поле `lookback_bars`
  (залишено `lookback_minutes` для сумісності).
- Оновлено тести S3 requester під нову семантику (300 барів).

**Де**

- app/smc_producer.py
- app/fxcm_warmup_requester.py
- tests/test_s3_warmup_requester.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_s3_warmup_requester.py tests/test_s2_history_state.py` → `8 passed`.

**Примітки/ризики**

- Це змінює поведінку “готовності”: тепер SMC працює always-on і може публікувати
  деградовані стани без OHLCV. Для повних SMC hints все одно потрібна історія
  (її має забезпечити конектор/UDS).

---

## 2025-12-15 — Логи/консоль: прибрано RichHandler + вимкнено status bar

**Що змінено**

- Прибрано Rich-based логування (`RichHandler`) у ключових модулях (Data/UI/SMC core helpers) — залишились прості стандартні логи через `logging.StreamHandler()`.
- Rich Live console status bar тоді було вимкнено (no-op), але у хвилі D1 (2025-12-17) модуль `app/console_status_bar.py` повністю видалено.
- Lightweight shim `app/rich_console.py` (і `utils/rich_console.py`) у хвилі D1 (2025-12-17) повністю видалено.

**Де**

- app/console_status_bar.py
- app/rich_console.py
- data/fxcm_ingestor.py
- data/fxcm_price_stream.py
- data/unified_store.py
- UI/publish_smc_state.py
- UI/ui_consumer_experimental_entry.py
- smc_structure/event_history.py
- smc_zones/breaker_detector.py
- smc_zones/fvg_detector.py
- smc_zones/orderblock_detector.py
- utils/utils.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py tests/test_ingestor.py` → `16 passed`.

---

## 2025-12-14 — Документація: UI_v2 + FXCM (live-bar, volume, джерело істини)

**Що змінено**

- Зафіксовано нюанс UI_v2: volume-серія є, але в основному UI-шляху live-бар з FXCM WS будується без `volume`, тому live-volume може бути нульовим; dev стенд (`chart_demo.js`) передає `volume`.
- Додано явні посилання на “джерело істини” контрактів у цьому репо: (історично: `data/fxcm_schema.py`) + `tests/test_fxcm_schema_and_ingestor_contract.py`.
  - Примітка (2025-12-18): нині SSOT — `core/contracts/fxcm_channels.py` + `core/contracts/fxcm_validate.py`.
- Додано посилання в кореневий README на `docs/fxcm_contract_audit.md`, щоб не перечитувати код під час звірки інтеграції.

**Де**

- UI_v2/web_client/README.md
- docs/fxcm_tick_agg_update_2025-12-13.md
- docs/fxcm_contract_audit.md
- docs/fxcm_integration.md
- README.md

**Тести/перевірка**

- Не запускалось (зміни лише в документації).

---

## 2025-12-14 — Документація: актуалізація `stage1_pipeline.md` під SMC-only runtime

**Що змінено**

- Переписано `docs/stage1_pipeline.md` як довідник реального `app.main` пайплайна (SMC-only): прибрано застарілий Stage1 моніторинг (`AssetMonitorStage1`, `screening_producer`) та `_await_fxcm_history()`.
- Додано посилання на джерело істини FXCM-контрактів у цьому репо: (історично: `data/fxcm_schema.py`) + `tests/test_fxcm_schema_and_ingestor_contract.py`.
  - Примітка (2025-12-18): нині SSOT — `core/contracts/fxcm_channels.py` + `core/contracts/fxcm_validate.py`.
- Оновлено діагностику: актуальні log-теги та канали (`fxcm:*`, `ui.metrics`).

**Де**

- docs/stage1_pipeline.md

**Тести/перевірка**

- Не запускалось (зміни лише в документації).

---

## 2025-12-14 — UI_v2: live-volume для FXCM live-барів + опційний same-origin WS

**Що змінено**

- `UI_v2/web_client/app.js`: у `handleOhlcvWsPayload()` live-бар тепер прокидає `volume` у `setLiveBar(...)`, щоб live-volume histogram міг малюватися, якщо `bar.volume` присутній у повідомленні.
- `UI_v2/web_client/app.js`: додано прапор `?fxcm_ws_same_origin=1` для підключення до FXCM WS у same-origin (коли `/fxcm/*` прокситься через nginx), замість жорсткого `:8082`.
- `UI_v2/web_client/app.js`: додано легкий індикатор `LIVE: ON/OFF` (ON якщо бачили `complete=false` за останні ~5s).
- Документація: уточнено runbook і описано мінімальний шлях доставки live-барів у прод-режимі через reverse-proxy.
- Додано окремий runbook: `docs/runbook_tradingview_like_live_public_domain.md`.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md
- deploy/viewer_public/nginx.conf
- deploy/viewer_public/README.md

**Тести/перевірка**

- Не запускалось (JS/UI зміни + документація).

---

## 2025-12-14 — UI_v2: мобільний 2-екранний режим (Overview/Chart) без reconnect

**Що змінено**

- Додано mobile-first UX: 2 екрани **Overview** та **Chart** з нижньою навігацією (bottom-nav) і drawer “Фільтри”.
- Головна вимога збережена: **один** WS/HTTP пайплайн і **один** чарт — перемикання екранів робиться лише через show/hide + перенос існуючого `.card-chart` між слотами та `scheduleChartResize()`.
- У шапці Overview додано компактні поля (symbol/price/Δ%) та дубль індикатора WS-статусу.
- В Overview додано компактний список останніх BOS/CHOCH (до 5) без нових джерел даних (береться з поточного viewer_state).
- Drawer синхронізує шари (events/pools/ote/zones) і таймфрейм (1m/5m) з існуючими desktop-контролами.
- У Chart-екрані на мобілці приховано важкі таблиці/панелі для максимально "чистого" графіка (керування — через drawer).
- У Chart-екрані на мобілці прибрано рамку/фон контейнера графіка (мінімалістичний вигляд).
- У desktop-шапці зроблено компактніший блок керування ("Символ/Таймфрейм") і прибрано кнопки "Оновити snapshot" та "Перепідключити".
- У desktop-шапці відформатовано `payload ts` у зрозумілий локальний формат (DD.MM HH:MM:SS).
- Summary зроблено компактнішим; зменшено відступи/проміжки між основними блоками (щільніше компонування аж до «майже впритул»).
- Зменшено відступ між desktop-шапкою та Summary (щільність як між блоками).
- Summary додатково ущільнено приблизно до ~50% від попереднього розміру (padding/gap/типографіка/бейджі).
- У Summary прибрано заголовок (без "Коротко"), лейбли залишено короткими українськими.
- У блоці Price Chart фільтри шарів (BOS/CHOCH, Pools, OTE, Zones) сховано під кнопку-стрілочку прямо на графіку (верхній кут).
- У блоці Price Chart контроль "Висота" перенесено на графік: тонкий вертикальний слайдер зліва без підписів.
- У блоці графіка прибрано заголовок "Price Chart", щоб звільнити місце під полотно.
- Додано іконку fullscreen поруч із кнопкою шарів; стандартизовано розміри оверлей-іконок:
  кнопки 32×32, іконки 16×16.
- Блок "OHLCV Debug" приховано (не займає місце), бо він більше не потрібен користувачу.
- Панелі Structure Events / OTE Zones / Liquidity Pools / Zones: контент більше не вилазить за межі картки;
  таблиці та заголовки зроблено компактнішими (A/B/C типографіка).
- Прибрано візуальне дублювання «двох паличок» біля контролу висоти: лівий бордер контейнера графіка сховано.
- Виправлено відображення контролу висоти: замість «подвійного» вертикального range у деяких браузерах використано стабільний rotate-варіант.
- Підсилено видимість вертикального слайдера висоти (контраст треку/повзунка + легка підкладка/hover), щоб було зрозуміло що це контроль.
- Вертикальний слайдер висоти: зроблено трохи сірішим і зміщено ближче до низу (прив'язка по `bottom`, без виходу за рамку).
- Вертикальний слайдер висоти: піднято трохи вище та прибрано фон/рамку контейнера (прозорий фон; видно лише шкалу й бігунок).
- UI_v2 (chart): додано невеликий нижній padding контейнера графіка, щоб не обрізалась нижня time scale.
- UI_v2 (fullscreen): виправлено «пливе графік» через лейаут — у fullscreen повністю приховано контроль висоти
  і дозволено контейнеру чарта рости в flex (через flex-обгортку), щоб не було обрізання/дрейфу.
- UI_v2 (fullscreen/desktop): режим `.card-chart--fullscreen` зроблено edge-to-edge: `inset:0`, без рамок/паддінгів/box-shadow,
  прибрано подвійну рамку (border/radius) у внутрішньому контейнері графіка.
- UI_v2 (mobile): переведено макет на flex-колонку (шапка → чарт (flex:1) → bottom-nav), щоб чарт реально займав екран
  і не було великої «порожнечі» під ним; зменшено висоту mobile header та bottom-nav.
- UI_v2 (mobile): висоту для евристики `--mobile-chart-height` беремо з `visualViewport.height` (fallback `innerHeight`),
  щоб на Android адресний рядок менше ламав розрахунки.
- UI_v2 (mobile/chart): прибрано «пливе вниз» — `#chart-slot` зроблено flex:1, бо `.card-chart` переноситься всередину слота;
  також прибрано `transition: height` у chart-контейнері на мобілці.
- UI_v2 (mobile/chart): зафіксовано канонічний фікс «пливе вниз» через `visualViewport` → `--app-vh` та px-висоту
  `--mobile-chart-height` (підписки на `visualViewport.resize/scroll`), щоб прибрати дрейф при зміні адресного рядка/toolbar.
- Підкручено тему графіка (фон/сітка/шкали/кросхейр/кольори свічок і volume) у бік «TV-like», без буквального 1:1 копіювання.

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js

**Тести/перевірка**

- Не запускалось (UI зміни). Ручна перевірка: відкриття `/`, перемикання Overview↔Chart без перепідключень та з коректним resize чарта.

---

## 2025-12-13 — Rich status bar: S2/S3 поля + індикатор конектора (conn)

**Що змінено**

- У Rich status bar додано рядок `conn`: показує свіжість `fxcm:status` (age) та стан `ok/lag/down` з підсвіткою.
- Додано рядок `s2`: лічильники проблем історії (insufficient/stale_tail/unknown) + активний символ/стан ("поточна тема").
- Додано рядок `s3`: індикатор requester-а (on/off), канал, лічильники, та остання відправлена команда (type/symbol/tf/reason/age).
- SMC producer тепер кладе S2 summary у `meta`, щоб status bar показував це навіть коли SMC у WARMUP/IDLE.

**Де**

- app/console_status_bar.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- tests/test_app_console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `5 passed`.

## 2025-12-13 — Shared Rich Console + FXCM WS bridge (UI_v2) + розширення тестів

**Що змінено**

- Уніфіковано Rich Console: замінили локальні `Console(stderr=True)` на спільний singleton (`get_rich_console()`),
  щоб прибрати артефакти Rich Live/логів у PowerShell/VS Code.
- Додано shim `app/rich_console.py` для сумісності імпортів (канонічний console в `utils/rich_console.py`).
- Додано WS-проксі для FXCM у UI_v2: трансляція `fxcm:ohlcv` та `fxcm:price_tik` у браузер (`/fxcm/ohlcv`, `/fxcm/ticks`).
- Посилено/уточнено юніт-тести: WS query parsing, роздача статики (content-type + path traversal),
  стабільність publish при короткій недоступності Redis, точність тестів логування/пайплайн-мети.

**Де**

- utils/rich_console.py
- app/rich_console.py
- data/unified_store.py
- data/fxcm_price_stream.py
- smc_structure/event_history.py
- smc_zones/breaker_detector.py
- smc_zones/fvg_detector.py
- smc_zones/orderblock_detector.py
- UI_v2/fxcm_ohlcv_ws_server.py
- tests/test_utils_rich_console.py
- tests/test_ui_v2_fxcm_ws_server.py
- tests/test_ui_v2_static_http.py
- tests/test_publish_smc_state.py
- tests/test_app_main_universe_fast_symbols.py
- tests/test_app_smc_producer_pipeline_meta.py

**Тести/перевірка**

- Запущено таргетно:
  `pytest tests/test_utils_rich_console.py tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py`
  `tests/test_publish_smc_state.py tests/test_app_main_universe_fast_symbols.py tests/test_app_smc_producer_pipeline_meta.py`
  → `19 passed`.

---

## 2025-12-13 — IDLE режим SMC по `fxcm:status` ("система чекає/спить, але статус видно")

**Що змінено**

- Додано політику "IDLE" для SMC-циклу: коли ринок закритий або фід деградований, важкі обчислення SMC пропускаються.
- При IDLE система **залишається живою** й продовжує публікувати стан/метадані (щоб UI/оператор бачив статус), а цикл робить `sleep`.
- Додано причини (reason) для прозорості: окремо для `market=closed`, `price!=ok`, `ohlcv!=ok`, а також "ok".

**Де**

- app/smc_producer.py

**Тести/перевірка**

- Запущено таргетні тести пайплайн-метаданих/локальної логіки SMC producer: `11 passed` (файл(и): `tests/test_app_smc_producer_pipeline_meta.py`, `tests/test_app_smc_producer_pipeline_local.py`).

**Примітки/ризики**

- Це **не** стоп процесу: лише гейтінг важких циклів. Слухач `fxcm:status` та публікація стану мають залишатися активними.

---

## 2025-12-13 — Rich Live status bar у консолі для SMC пайплайна

**Що змінено**

- Додано консольний "живий" status bar (Rich Live), який оновлюється в одному рядку та не конфліктує з логами RichHandler у PowerShell/VS Code.
- Status bar читає SMC snapshot із Redis (`REDIS_SNAPSHOT_KEY_SMC`) і показує базові стани: `pipeline_state`, FXCM market/price/ohlcv та Redis up/down.
- Додано перемикач через ENV: `SMC_CONSOLE_STATUS_BAR=0` вимикає панель.
- Додано TTY-перевірку **саме по stderr** (бо і Live, і RichHandler пишуть у stderr) + ранній вихід без polling, якщо stderr не TTY.
- У `app.main` використовується **спільний** `Console(stderr=True)` для RichHandler і Live (менше шансів на "затирання" логів).
- Додано явне `redirect_stderr=True` у Rich Live та `force_terminal=True` для спільного Console, щоб панель перерисовувалась на місці (без дублювання блоків) і логи гарантовано друкувались над нею.

**Де**

- app/console_status_bar.py
- app/main.py

**Тести/перевірка**

- Додано тести побудови snapshot: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py tests/test_app_console_status_bar.py` → `6 passed`.
- Додатково перевірено: `pytest tests/test_app_console_status_bar.py` → `3 passed`.

---

## 2025-12-13 — Гейтінг запису OHLCV в UDS по статусу ринку/фіду (без падіння процесу)

**Що змінено**

- Повернуто/закріплено поведінку: ingest-процес не завершується, але **не пише** OHLCV в UDS при `market=closed` або коли `price/ohlcv != ok`.
- При "status unknown" (cold-start) ingest дозволений (щоб система стартувала незалежно від порядку подій).

**Де**

- data/fxcm_ingestor.py

**Тести/перевірка**

- Оновлено контрактні тести на кейс `market=closed` → очікуємо **0 записів в UDS** (skip-write).
- Запускались таргетні pytest-тести для контракту інгесту (див. наступний запис про файл тестів).

---

## 2025-12-13 — Розширення Rich status bar: pipeline/cycle + age snapshot

**Що змінено**

- Розширено консольний status bar: тепер показує не лише mode/market/ticks/redis, а й ключові метрики пайплайна.
- Додано: вік останнього SMC snapshot (age), pipeline ready/total/pct, capacity (processed/skipped), cycle seq/duration.
- Додано: компактний блок стану FXCM (proc/price/ohlcv) у вигляді одного рядка.
- Ліміт інформаційних рядків піднято до 8 (залишається один Panel у Live, без спаму логами).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено/додано тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: FXCM session (name/state + to_close/to_open)

**Що змінено**

- Додано відображення FXCM session: `session_name:session_state` + таймери `to_close`/`to_open` (якщо доступні).
- Ліміт рядків у панелі збільшено до 10, щоб не відсікати вже додані поля.

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: підсвітка FXCM session state

**Що змінено**

- Додано підсвітку `session_state` у рядку `sess` (open→green, closed→yellow, error→red), таймери `to_close/to_open` — cyan.
- Зміна лише в рендері (payload/snapshot без змін).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: явний стан SMC (RUN/IDLE/WARMUP) + poll замість sleep

**Що змінено**

- Додано рядок `smc`, який явно показує стан: `RUN` (SMC рахує), `IDLE` (гейтінг по FXCM), `WARMUP` (недостатньо даних), `WAIT` (невідомо/очікування), + причина.
- Рядок `sleep` перейменовано на `poll` і виправлено формат: тепер для малих інтервалів показує `ms` замість округлення до `0s`.
- Це прибирає плутанину “sleep 0s” і відповідає на питання «SMC зараз спить чи працює».

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: uptime (робочий час) у рядку `cycle`

**Що змінено**

- У рядок `cycle` додано “робочий час” (uptime) процесу з відступом: `up=...`.
- Формат показує дні, коли тривалість перевищує 23:59 (наприклад `2d 03h12m`).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `passed`.

---

## 2025-12-13 — Rich status bar: sess узгоджено з `market=closed`

**Що змінено**

- Якщо `market=closed`, рядок `sess` більше не показує `open` (примусово `CLOSED`, якщо не error-стан).
- При `market=closed` не показуємо `to_close` (щоб не вводило в оману), лишаємо `to_open`.

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `passed`.

---

## 2025-12-13 — Rich status bar: підсвітка FXCM proc/price/ohlcv + менше шуму

**Що змінено**

- У рядку `fxcm` додано підсвітку станів `proc/price/ohlcv`: ok→green, stale/lag→yellow, down/error→red.
- Рядок `lag` більше не показується як `0s` (показуємо лише якщо lag > 0).
- У рядку `smc` підсвічено причину `fxcm_market_closed` (щоб швидко читалось при IDLE).
- Зміни лише у рендері (payload/snapshot без змін).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Redis FXCM OHLCV ingest: дефолт без лог-спаму

**Що змінено**

- Для інжестора `fxcm:ohlcv` піднято дефолт `log_every_n`: тепер без явного налаштування не логуються кожні 1–2 бари.
- Це зменшує шум у консолі та I/O навантаження при великому universe.

**Де**

- data/fxcm_ingestor.py

**Тести/перевірка**

- Логічна зміна дефолту (поведінка інжесту даних не змінюється). За потреби можна прогнати контракт: `pytest tests/test_fxcm_schema_and_ingestor_contract.py`.

---

## 2025-12-13 — Контракт/схеми FXCM повідомлень + юніт-тести контракту

**Що змінено**

- Додано модуль зі схемами/валідацією для FXCM payload:
  - OHLCV бари (`fxcm:ohlcv`), включно з підтримкою `complete` та forward-compatible extra полів.
  - Тіки (`fxcm:price_tik`).
  - Статус (`fxcm:status`).
- Закріплено контракт інгесту: в UDS потрапляють лише **complete=true** бари; додаткові (мікроструктурні) поля не мають «просочуватись» у канонічний OHLCV у UDS.

**Де**

- (історично) data/fxcm_schema.py
- data/fxcm_ingestor.py
- tests/test_fxcm_schema_and_ingestor_contract.py

> Примітка (2025-12-18): `data/fxcm_schema.py` видалено; SSOT перенесено у `core/contracts/fxcm_channels.py` + `core/contracts/fxcm_validate.py`.

**Тести/перевірка**

- Додано/оновлено `tests/test_fxcm_schema_and_ingestor_contract.py` (валідація схем + поведінка інгесту на невалідних/неповних барах, гейтінг по статусу).

---

## 2025-12-13 — Юніт-тести idle-рішень SMC (детермінована перевірка reason)

**Що змінено**

- Додано окремий тестовий файл, який перевіряє рішення "бігти/не бігти" для SMC-циклу на базі `fxcm:status`.
- Перевіряються кейси:
  - `market=closed` → IDLE (`fxcm_market_closed`)
  - `market=open`, `price=ok`, `ohlcv=ok` → RUN (`fxcm_ok`)
  - `price=stale` → IDLE (`fxcm_price_stale`)
  - `ohlcv=lag` → IDLE (`fxcm_ohlcv_lag`)

**Де**

- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- `pytest tests/test_app_smc_producer_fxcm_idle.py` → `4 passed`.

---

## 2025-12-xx — (історично в цій сесії) UI live-стрімінг, tick-апдейти, та стійкість до рестартів Redis

> Примітка: цей блок зафіксовано ретроспективно зі стислою деталізацією; точні команди тестів/прогони не відновлюю без логів.

**Що змінено**

- UI_v2 почав отримувати live OHLCV і/або тіки через WS-проксі з Redis (оновлення графіка без ручного refresh).
- Додано частіші оновлення свічки через агрегування тіків між close барами.
- Прибрано потребу в окремому статичному сервері: web-клієнт `UI_v2/web_client` віддається з бекенду.
- Додано backoff/reconnect для pubsub-споживачів, щоб пайплайн не падав при рестарті Redis.

**Де**

- UI_v2/fxcm_ohlcv_ws_server.py
- UI_v2/viewer_state_server.py
- UI_v2/web_client/*
- UI_v2/smc_viewer_broadcaster.py
- UI/publish_smc_state.py

**Тести/перевірка**

- Додавались таргетні тести для критичних змін (деталі — у відповідних тестових файлах у `tests/`).

---

## 2025-12-13 — Tick-agg адаптація: soft-валидація барів + dev chart (volume panel, opacity)

**Що змінено**

- `fxcm:ohlcv` schema: додано per-bar soft-валидацію — некоректні бари відкидаються, а відсутність `complete/synthetic` не вважається помилкою.
- Dev chart playground: додано volume histogram під свічками та opacity/насиченість свічок від нормованого volume (max за останні N барів).
- Gap-check інструмент: додано режим `--snapshot-file` для перевірки пропусків по локальному jsonl snapshot без Redis/UDS.
- Додано коротку документацію про перехід конектора на tick-agg і правила трактування `complete/synthetic`.

**Де**

- (історично) data/fxcm_schema.py
- tests/test_fxcm_schema_and_ingestor_contract.py
- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/chart_demo.js
- tools/uds_ohlcv_gap_check.py
- docs/fxcm_tick_agg_update_2025-12-13.md

> Примітка (2025-12-18): `data/fxcm_schema.py` видалено; soft-валидація тепер у `core/contracts/fxcm_validate.py`.

**Тести/перевірка**

- Оновлено тести контракту схем: `pytest tests/test_fxcm_schema_and_ingestor_contract.py`.

---

## 2025-12-13 — UI_v2 web_client: README з арх-описом (порти/ендпойнти/Redis/CORS/безпека)

**Що змінено**

- Розширено README для UI_v2 web client як “контекст-дамп” для архітектора.
- Додано опис стеку UI_v2 у рамках `python -m app.main`: broadcaster, HTTP (статика+REST), WS viewer_state, FXCM WS міст.
- Зафіксовано дефолтні порти/ENV-параметри та точні endpoints.
- Додано перелік Redis ключів/каналів (SMC snapshot/state → viewer snapshot/channel; FXCM dev канали).
- Додано зауваження щодо CORS (`Access-Control-Allow-Origin: *`) та відсутності auth/TLS + рекомендації для прод.

**Де**

- UI_v2/web_client/README.md

**Тести/перевірка**

- Без змін у коді рантайму; лише документація.

---

## 2025-12-13 — S2 history_state (insufficient/stale_tail) + S3 warmup/backfill requester (Redis commands)

**Що змінено**

- Додано S2-логіку класифікації історії в UDS для (symbol, tf): `ok | insufficient | stale_tail`.
- У `smc_producer` додано перевірку `stale_tail`: актив із протухлим хвостом не вважається ready; у stats додається блок `history_state/needs_warmup/needs_backfill`.
- Додано S3 воркер requester, який (за флагом) періодично проходить по whitelist з `fxcm_contract` і публікує команди `fxcm_warmup` / `fxcm_backfill` у Redis канал (дефолт `ai_one:admin:commands`) з rate-limit.
- Додано конфіг для S2/S3 у `config.config` (без керування через ENV): enable/poll/cooldown/channel/stale_k.

**Де**

- app/fxcm_history_state.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- app/main.py

**Тести/перевірка**

- Додано юніт-тести S2: `tests/test_s2_history_state.py`.
- Додано юніт-тести S3 requester: `tests/test_s3_warmup_requester.py`.

---

## 2025-12-19 — UI_v2 (Web): прибрано «пам’ять на сесію» чарта; стабілізація жестів

**Що змінено**

- Відкотили/прибрали session-only «пам’ять» позиції/масштабу чарта (через `sessionStorage`) як зайву та таку, що провокувала UX-регресії.
- Стабілізовано жести: вертикальний pan (manual price-range) керується явною активацією (price-axis або `Shift`), а drag по price-axis блокується на час нашого vertical-pan, щоб прибрати «стрибок» при кліку+русі по шкалі.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Немає JS-юнiт-тестів у репо.
- Python: `12 passed` (таргетні тести UI_v2).

---

## 2025-12-19 — UI_v2 (Web): спроба price-scale UX як TradingView (axis-only vertical-pan + `autoOffset`) — НЕ СПРАЦЮВАЛО

**Що змінено**

- Vertical-pan по Y зроблено з запуском лише з правої цінової осі (price axis), а не з усієї області графіка.
- Під час vertical-pan прибрано вимикання drag бібліотеки та прибрано `stopPropagation` у move-обробнику (ідея: горизонтальний drag лишається «рідним»).
- Додано TV-like поведінку: якщо `manualRange` не активний, вертикальний drag по осі змінює `priceScaleState.autoOffset`, а autoscale лишається autoscale (діапазон зсувається через autoscaleInfoProvider).
- Double-click по осі скидає і `manualRange`, і `autoOffset` (повертає чистий autoscale).

**Додатково**

- Прибрано `stopPropagation/stopImmediatePropagation` у wheel-хендлері (залишено `preventDefault`), щоб не «глушити» події.
- Локальна перевірка JS у VS Code: синтаксичних помилок у файлі не виявлено.

**Де**

- UI_v2/web_client/chart_adapter.js

**Статус**

- За фідбеком користувача: vertical-pan «зламався» (неможливо нормально рухати графік вверх/вниз), а «стрибки/розмазування» по Y лишились.
- Потрібен відкат (див. наступний запис) і повернення до діагностики першопричини без UX-регресій.

---

## 2025-12-19 — UI_v2 (Web): ВІДКАТ price-scale UX патчу (vertical-pan/autoOffset)

**Що змінено**

- Відкотили зміни у взаємодії з price-scale, які вводили `autoOffset` та обмежували vertical-pan лише до price-axis.
- Причина: регресія UX — користувач не міг рухати графік вверх/вниз як раніше, а «стрибки/розмазування» все одно відтворювались.
- Повернули vertical-pan через `manualRange` у старому режимі (старт по pane), та відновили wheel stopPropagation для уникнення подвійного масштабування.

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest -q tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

**Статус**

- Тема «стрибки/розмазування» по Y НЕ закрита; потрібна окрема діагностика без ламання базових жестів.

---

## 2025-12-19 — UI_v2 (Web): price-scale UX (мінімальний DIFF): autoOffset vertical-pan + manualRange тільки для zoom + wheel handled-only

**Що змінено**

- Додано `priceScaleState.autoOffset`: вертикальний pan (drag) у autoscale-режимі тепер зсуває діапазон через `autoOffset`, не вмикаючи «липкий» `manualRange`.
- `manualRange` тепер використовується як «свідомий» режим саме для wheel-zoom (і для pan у manualRange, якщо він вже активний через zoom).
- Wheel-події тепер глушаться (`preventDefault/stopPropagation`) **лише якщо** наш код реально обробив подію; це прибирає кейс, коли wheel «вмирає» через заглушення без фактичного zoom/pan.
- Додано kill-switch `?ps_legacy_vertical_pan=1`: повертає legacy поведінку vertical-pan через `manualRange`.
- Double-click по price-axis скидає і `manualRange`, і `autoOffset` (повертає чистий autoscale).

**Де**

- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → `12 passed`.

**Примітки/ризики**

- Це точкова UX-зміна взаємодії зі шкалою ціни; якщо з’являться нові регресії — швидкий відкат через `ps_legacy_vertical_pan=1`.

---

## 2025-12-19 — UI_v2 (Web): спроба прибрати «подвійне масштабування» через вимкнення library price-scale (wheel/axis-drag) — ВІДКАТ користувачем

**Контекст / гіпотеза**

- Під час wheel-zoom по price-axis інколи з’являється «стрибнуло/розмазало».
- Гіпотеза: відбувається «подвійне масштабування» — lightweight-charts встигає застосувати свій price-scale wheel/axis scale, і паралельно наша логіка ще раз застосовує zoom/pan через `manualRange`/`autoOffset`.

**Що пробували (експериментально)**

- Вимкнути вбудоване масштабування price-scale у lightweight-charts (на рівні chart options):
  - `handleScale.mouseWheel = false`
  - `handleScale.axisPressedMouseMove.price = false`

**Фактичний результат (фідбек користувача)**

- Зламалось масштабування по горизонталі (time-scale UX регресував).
- По Y зсув/масштабування при wheel по price-axis все ще могло «стрибнути/розмазати», але слабше і з відчутним зсувом вниз.

**Статус**

- Користувач відкотив зміни.
- Тема «стрибки/розмазування» по price-scale при wheel по осі ціни лишається відкритою.

**Наслідки / нотатки**

- Потенційно причина не лише в дублюванні wheel-scale, а й у порядку/фазі обробки подій (capture/bubble) або в тому, що вимкнення price-scale scale зачіпає очікувану поведінку time-scale.

## Нагадування (обов’язково далі)

- Кожна нова правка в коді → **новий запис** сюди.
- Кожна нова правка → **таргетні тести** + запис у секції "Тести/перевірка" з результатом.

---

## 2025-12-20 — UI_v2 (Web): Stage5 execution-стрілочки на свічках (tooltip-only)

**Що змінено**

- Додано рендер Stage5 `execution_events` як стрілочки `arrowUp/arrowDown` на відповідній свічці (без текстових лейблів на графіку).
- Деталі події показуємо лише в tooltip при hover на барі зі стрілкою (щоб не засмічувати графік).
- Зроблено “липку” поведінку маркерів: якщо подія була в одному снапшоті, стрілка лишається на свічці й не зникає в наступних апдейтах.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/index.html
- UI_v2/viewer_state_builder.py

**Тести/перевірка**

- Немає JS-юнiт-тестів у репо.
- Python (таргетно): `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_fxcm_ws_server.py` → passed.

**Примітки/ризики**

- Видимість стрілок залежить від того, чи доходять `execution_events` у viewer_state (режим replay/TF/вікно можуть давати “порожньо” навіть при наявності подій в інших режимах).
