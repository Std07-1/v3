# ADR-0085: Арчі на чарті — Read-Only Agent Layer (лінії-будильники + теза)

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0085 |
| Статус | **Proposed** — RECON завершений, контракт і слайси специфіковані; чекає owner-go на імплементацію |
| Дата | 2026-07-07 |
| Автори | Станіслав (owner, vision) + Opus 4.8 (архітектор: RECON + специфікація) |
| Будується на | ADR-0048/0049 (WakeEngine + NarrativeEnricher — ЗАДЕПЛОЄНІ), ADR-0075 (bar-close kinds), ADR-0084 (tools registry — патерни рендера) |
| Поважає | I7 (Autonomy-First: платформа читає, НІКОЛИ не пише за Арчі), S1 (read-only overlay, не пише UDS), X28 (UI dumb renderer — числа лише з бекенда), X31 (зміни бота = окремий trader-v3 ADR), ADR-0066 (токени), ADR-0081 (hints gating), Clean Chart Doctrine |
| Companion (X31) | **trader-v3 ADR-08x «Numeric Thesis Contract»** — бот пише числові поля поруч із текстом тези (див. §7). БЕЗ нього шар працює на wake-умовах |
| Зачіпає шари | `runtime/smc/wake_engine.py`, `runtime/ws/ws_server.py`, `runtime/smc/narrative_enricher.py`, `config.json`, `ui_v4/src/{types.ts, app/frameRouter*, layout/ChartPane.svelte, chart/archi/ArchiLayerRenderer.ts (NEW)}` |
| Initiative | `archi_chart_v1` |

---

## Quality Axes

- **Ambition target**: **R3** — новий клас поверхні: голос агента стає ПРОСТОРОВИМ (рівні/зони на полотні), не лише текстовим (панель). Це перший крок owner-vision «чарт = спільне полотно людини й агента» (заміна воркспейсу чартом — окремий майбутній ADR, §10).
- **Maturity impact**: **M4 → M4.5** — закривається розрив «Арчі каже про рівні, але їх не видно там, де дивиться трейдер»; контракт wake-умов вперше експонується в UI (досі — лише bot-канал).

---

## 1. Контекст і owner-vision

Owner: «не тривожимо Арчі, а готуємо ґрунт взаємодії Арчі з чартом. Планую щоб його тезиси й сценарії ми могли бачити на чарті.. можливо замінимо його воркспейс чартом».

Ключова асиметрія сьогодні: трейдер дивиться на **чарт**, Арчі говорить у **панель/Telegram/ГОРН**. Його рівні («чекаю пробою 4380», «інвалідація вище 4427») — це просторові твердження, які живуть як текст. Цей ADR кладе їх ТУДИ, де вони мають сенс — на цінову вісь.

Принцип: **Арчі не тривожимо взагалі.** Уся v1 читає те, що вже тече (або що бот уже вміє писати за наявним контрактом). Жодного нового навантаження на бюджет бота.

---

## 2. RECON — що РЕАЛЬНО є (verified 2026-07-07, live)

### 2.1 Дані Арчі, доступні платформі

| # | Джерело | Формат | Числа? | Стан live | Де зараз в UI |
|---|---------|--------|--------|-----------|----------------|
| A | **Bot wake conditions** — Redis STRING `{ns}:wake:conditions:{sym_safe}` = JSON list | `{kind, params, reason, source:"bot", created_at_ms}`; params: `price_cross {level: float, direction}`, `price_zone_touch {zone_high, zone_low, tolerance_atr}`, `candle_close {level, direction, tf_s}` (ADR-0075), session/silence/scheduled (не-чартові) | **ТАК** [VERIFIED wake_engine.py:363-391, wake_types.py:53-60] | ключ зараз відсутній (Арчі не ставив цього тижня), але контракт задеплоєний і працював (події в `wake:events` живі — price_zone_touch з цінами) | **НІДЕ** — розрив: WakeEngine тримає `_bot_conditions` у пам'яті (кеш 30с) [VERIFIED :81], у wire НЕ віддає |
| B | **Thesis** — Redis HASH `{ns}:thesis:{sym_safe}` → enricher (кеш 15с) → `narrative.archi_thesis` | `thesis, conviction, key_level, invalidation` — **все РЯДКИ** («PDL 4650 — main target…») + `freshness, updated_at_ms` [VERIFIED narrative_enricher.py:103-110, wake_types.py:157-170] | **НІ** (рядки) | hash зараз ПОРОЖНІЙ (бот давно не писав; enricher graceful skip) | NarrativePanel вже рендерить картку тези (conviction/freshness/🎯 key_level) [VERIFIED NarrativePanel.svelte:221-244] |
| C | **Presence** — WakeEngine → `narrative.archi_presence` | status/focus/next_wake/conditions/accumulator | частково | живе | NarrativePanel + agentState |
| D | **Scenarios** — `narrative.scenarios[].zone_id` | zone_id → зв'язка з SMC-зонами | ref | живе | NarrativePanel текстом |
| E | **Wake events** — Redis LIST `{ns}:wake:events` | ts_ms, symbol, kind, reason, price, meta | ТАК | **ЖИВИЙ** (перевірено: 3 свіжі price_zone_touch) | ніде |

### 2.2 Wiring-факти

- Enrichment відбувається у збірці frame: `frame["narrative"] = enricher.enrich_narrative(..., tier="free")` [VERIFIED ws_server.py:745-750]; tier-hook уже існує (ADR-0049 §5, FeatureTier enum).
- Платформні auto_wake умови (source="platform") — генеруються з SMC-зон щотіка; це ДУБЛЬ того, що вже намальовано як SMC-зони → **на Арчі-шар НЕ тягнути** (фільтр `source == "bot"` — тільки голос Арчі).
- UI canvas-стек у ChartPane: `overlay-layer` (SMC) → `drawings-layer` (user) [VERIFIED ChartPane.svelte:623-628]. Патерни pane-clip/теми/rAF — готові в DrawingsRenderer (ADR-0084 batch).
- Трап відомий: Redis-сідінг ТІЛЬКИ `python -X utf8` heredoc (CP1251 отрута — memory).

### 2.3 Висновок RECON

Шар v1 можна поставити **без жодної зміни бота**: джерело A (числові будильники) + джерело B (текстова картка вже в панелі — на чарті НЕ дублюємо текст, чекаємо числових полів §7). Єдиний platform-gap — експонувати A у wire.

---

## 3. Рішення (архітектура v1)

### D1. Wire-контракт: нове top-level поле `frame.archi_chart`

НЕ в narrative (narrative збирається на full/complete-bar; лінії мають бути стабільно присутні в кожному full frame і оновлюватись у delta як levels). Формат:

```jsonc
"archi_chart": {
  "conditions": [
    // ЛИШЕ source=="bot", ЛИШЕ чартові kinds:
    { "kind": "price_cross",      "level": 4380.0, "direction": "above",
      "reason": "чекаю пробою 4380 → підтвердження bull", "created_at_ms": 1781... },
    { "kind": "price_zone_touch", "zone_high": 4427.0, "zone_low": 4415.0,
      "reason": "supply-зона...", "created_at_ms": ... },
    { "kind": "candle_close",     "level": 4311.0, "direction": "below", "tf_s": 3600,
      "reason": "закриття H1 нижче 4311 = зміна структури", "created_at_ms": ... }
  ],
  // §7: з companion trader-v3 ADR; ДО нього — відсутні (graceful)
  "key_level_price": 4380.0,        // optional
  "invalidation_price": 4427.0,     // optional
  "thesis_updated_at_ms": 1781...   // optional (для freshness-стилю ліній тези)
}
```

Правила серіалізації (ws_server):
- Джерело: `wake_engine.get_bot_conditions(symbol)` — **НОВИЙ публічний getter** (читає `_bot_conditions` кеш; нуль нових Redis-звернень — кеш уже оновлюється кожні 30с).
- Фільтр: `source=="bot"` І kind ∈ {price_cross, price_zone_touch, candle_close} (session/silence/scheduled — не-чартові, у wire не йдуть).
- `reason` обрізається до 140 символів (wire-гігієна).
- Поле відсутнє повністю, якщо умов нема І числових полів тези нема (нуль шуму у frame).
- Tier-hook: як thesis (зараз `"free"` прохід; поле — у списку premium-gated ADR-0049 §5 на майбутнє).

### D2. UI: третій canvas `archi-layer` + `ArchiLayerRenderer`

- **Позиція в стеку**: між `overlay-layer` (SMC) і `drawings-layer` (user): SMC (контекст) → **Арчі (агент)** → рука трейдера (найвищий пріоритет). `pointer-events: none`, hit-test у v1 відсутній (read-only шар; hover — P5).
- **Клас** `ui_v4/src/chart/archi/ArchiLayerRenderer.ts` — свідомо ОКРЕМИЙ від DrawingsRenderer (інший життєвий цикл: дані з frame, не localStorage; нуль команд/undo; нуль interaction). Переюз патернів: DPR-rail (ResizeObserver), `subscribeVisibleTimeRangeChange → renderSync`, pane-clip (paneW/H з `priceScale('right').width()` + `timeScale().height()` — ADR-0084 batch), theme-refresh через CSS vars.
- **Координати**: `price → y` через `seriesApi.priceToCoordinate` (лише ціни — час не потрібен для рівнів; created_at_ms НЕ якір, лінії full-width як hline). Зона (zone_touch) = напівпрозора смуга `[zone_low..zone_high]` на всю ширину пани.

### D3. Візуальна мова шару (delicate, Clean Chart)

| Елемент | Стиль v1 (owner live-tune очікується) |
|---|---|
| `price_cross` рівень | тонка (1px) лінія `--info` (#5487FF), **dash-dot** `[7,3,2,3]` — «сигнальний дріт», відрізняється і від SMC-levels (свої стилі), і від user-hline (suцільна/користувацькі стилі) |
| `candle_close` рівень | те саме + маркер TF у лейблі («H1») |
| `price_zone_touch` | заливка `--info` 6% + межі dash-dot 40% альфи |
| `key_level_price` (теза, §7) | 1.4px `--info` **solid** — головний рівень тези |
| `invalidation_price` (теза, §7) | 1.2px `--bear` **dashed** — «тут теза мертва» |
| Лейбл | праворуч біля пани: `⏰ 4380 · Арчі` (шрифт 10px mono, halo-тінь як delete-×); повний reason — hover-tooltip P5, hints-gated (ADR-0081) |
| Freshness | вік від `created_at_ms`/`thesis_updated_at_ms`: <1г повна альфа, 1-4г ×0.7, >4г ×0.45 — старі будильники «тьмяніють», нічого не миготить |
| Budget | max `ui.archi_chart.max_lines` (default 6) найсвіжіших; зайві — не рендеряться (лог debug once) |

Іконографія: «⏰» для будильників (природна метафора wake), «◆» для рівнів тези. Токен `--info` v1 — НЕ вводимо новий токен без owner-смаку; кандидат на live-tune (можливо окремий `--archi`).

### D4. Config SSOT (config.json)

```jsonc
"ui": { "archi_chart": { "enabled": true, "max_lines": 6, "zone_fill_alpha": 0.06 } }
```
`enabled:false` → ws_server не серіалізує поле взагалі (сервер-сайд вимикач, wire чистий). UI додатково має ☰-toggle «Арчі на чарті» (localStorage `v4_archi_chart`, default ON) — клієнтський show/hide БЕЗ перерахунку (P6).

### D5. Життєвий цикл даних у UI

- `frameRouter`: passthrough `frame.archi_chart` → `currentFrame` (як levels/narrative — нуль трансформацій, X28).
- ChartPane `$effect`: full frame АБО delta з полем → `archiRenderer.setData(frame.archi_chart)`; відсутнє поле у delta → тримати попереднє (як zones); відсутнє у FULL → очистити шар (символ без умов).
- Redis TTL-ефект: Арчі зняв умови → кеш WakeEngine оновиться ≤30с → wire без поля → шар зникає сам. Нуль ручного інвалідування.

### D6. Деградації (I5, degraded-but-loud)

| Випадок | Поведінка |
|---|---|
| Ключів у Redis нема (зараз) | поле відсутнє, шар порожній — НЕ помилка (Арчі просто не ставив будильників) |
| Corrupt JSON у wake:conditions | WakeEngine вже ковтає per-item (`continue` на ValueError [VERIFIED :377]) — шар показує валідні; ws-лог debug |
| `level` поза видимим діапазоном цін | лінія коректно поза екраном (LWC priceToCoordinate; та сама семантика, що user-hline — ADR-0084 урок «M15=0») |
| Числові поля тези відсутні (до §7) | рендеряться лише wake-умови; картка тези лишається в NarrativePanel |
| enabled:false у config | поля нема у wire; UI-toggle схований |

---

## 4. Slices (порядок імплементації, кожен ≤150 LOC, verify живцем)

| # | Зміст | Файли | LOC | Verify (живцем, обов'язково) |
|---|-------|-------|-----|------------------------------|
| **P1** | `WakeEngine.get_bot_conditions(symbol)` (getter кешу, фільтр source/kind) + серіалізація `frame.archi_chart` у ws_server (full + delta) + config knob `ui.archi_chart` | wake_engine.py (+15), ws_server.py (+35), config.json (+4) | ≤60 | seed Redis (`python -X utf8`!): 2 price_cross + 1 zone_touch для XAU/USD → браузер: tap WS onmessage (page.evaluate hook) → `frame.archi_chart.conditions.length==3`, числа точні; зняти ключ → поле зникає ≤30с |
| **P2** | UI: `types.ts` (+ArchiChartData), frameRouter passthrough, ChartPane третій canvas + wiring, `ArchiLayerRenderer` скелет: price_cross лінії + zone_touch смуги, pane-clip, DPR, теми | types.ts, frameRouter, ChartPane.svelte, chart/archi/ArchiLayerRenderer.ts (NEW) | ≤150 (за потреби P2a/P2b) | getImageData: лінія на y(4380) — звірка з user-hline на тому ж рівні (піксельний y-збіг); зона = смуга правильної висоти; cross-TF (H1↔M15 — ті самі ціни); лінії НЕ на шкалах (pane-clip) |
| **P3** | Лейбли (`⏰ 4380 · Арчі`) + freshness-альфа + display budget (max_lines) | ArchiLayerRenderer | ≤80 | скрін: лейбл читабельний dark+light; seed старий created_at_ms → тьмяніша лінія; 8 умов → рендеряться 6 найсвіжіших |
| **P4** | Числові поля тези: enricher passthrough `key_level_price`/`invalidation_price`/`thesis_updated_at_ms` (optional з hash) → wire → стилі D3 | narrative_enricher.py (+10), ws_server (+8), ArchiLayerRenderer (+40) | ≤60 | seed hash із числами → key solid-info + invalidation dashed-bear на точних y; ВИДАЛИТИ числа → лінії зникають, wake-лінії лишаються (degrade) |
| **P5** | Hover-reason: маленький hit-test по лініях шару (у межах 6px) → існуючий `.smc-tooltip` DOM-механізм (ADR-0081-gated «Підказки») з повним reason | ArchiLayerRenderer (+50), OverlayRenderer seam АБО власний tooltip-елемент | ≤70 | hover лінії при hints ON → reason; hints OFF → тиша |
| **P6** | ☰-toggle «Арчі на чарті» (localStorage, default ON) + CommandRailOverflow пункт | App.svelte, CommandRailOverflow, ChartPane | ≤40 | toggle OFF → шар зникає миттєво; ON → повертається; persist через reload |
| **P7** *(optional)* | Сценарій-зв'язка: `scenarios[].zone_id` → «Арчі-рамка» навколо відповідної SMC-зони (delicate інфо-обвід) | ArchiLayerRenderer + zone-lookup | ≤60 | сценарій із zone_id → зона отримує інфо-обвід; без сценаріїв — нічого |

Паралелізм: P1 незалежний (можна верифікувати curl-ом ДО UI); P2 після P1; P3-P6 послідовно по P2; P4 вимагає лише enricher (незалежно від P3). P7 — після P2, за owner-настроєм.

---

## 5. Verify-інфраструктура (для імплементатора)

**Seed-скрипт** (клади в scratchpad, НЕ в репо; `python -X utf8` — CP1251-трап!):
```python
import redis, json, time
r = redis.Redis(host='127.0.0.1', port=6379, db=1)
now = int(time.time()*1000)
conds = [
  {"kind":"price_cross","params":{"level":4380.0,"direction":"above"},
   "reason":"чекаю пробою 4380 — підтвердження бичачої структури","source":"bot","created_at_ms":now},
  {"kind":"price_cross","params":{"level":4285.0,"direction":"below"},
   "reason":"пробій 4285 вниз = злам тези","source":"bot","created_at_ms":now-3*3600_000},
  {"kind":"price_zone_touch","params":{"zone_high":4427.0,"zone_low":4415.0,"tolerance_atr":0.5},
   "reason":"supply-зона H4 — реакція очікувана","source":"bot","created_at_ms":now},
]
r.set('v3_local:wake:conditions:XAU_USD', json.dumps(conds, ensure_ascii=False))
r.hset('v3_local:thesis:XAU_USD', mapping={
  "thesis":"Чекаю sweep 4285 → відкат у supply 4415-4427","conviction":"medium",
  "key_level":"4380 — тригер підтвердження","invalidation":"вище 4427 теза мертва",
  "key_level_price":"4380.0","invalidation_price":"4427.0",   # P4 contract
  "updated_at_ms":str(now)})
```
- WS-tap для P1: `page.evaluate` — обгорнути `WebSocket.prototype` АБО простіше: після P2 дивитись рендер; для чистого P1 — тимчасовий `console.log` gate у frameRouter (прибрати в тому ж slice).
- Піксельні перевірки: патерни ADR-0082-0084 (drawings-layer сканери) — 1:1 переносяться (клас canvas `archi-layer`).
- Прибирання після verify: `DEL v3_local:wake:conditions:XAU_USD` + `DEL v3_local:thesis:XAU_USD` (це НЕ прод-стан Арчі — він зараз цих ключів не тримає; засіяне = тест-дані).

---

## 6. Інваріанти й заборони (перечитати перед кожним slice)

1. **I7**: платформа ЧИТАЄ. Жодного запису в thesis/conditions. Жодної інтерпретації («якщо рівень пробитий — перефарбувати») — рівень пробито = Арчі сам зніме умову, шар віддзеркалить.
2. **X28**: UI НЕ парсить рядки (`key_level:"PDL 4650"` НЕ регекспиться у 4650!). Числа приходять числами (D1/§7) або ліній нема.
3. **S1/ephemeral**: ArchiLayerRenderer не пише НІКУДИ (ні UDS, ні localStorage-стор малювань). Джерело істини = wire frame.
4. **X31**: зміни бота (числові поля тези) — ТІЛЬКИ через companion trader-v3 ADR. Цей ADR лише специфікує optional-поля, які платформа ВМІЄ прийняти.
5. **Clean Chart**: budget max_lines; нуль анімацій/миготінь; freshness — статична альфа, не пульс.
6. **User understanding**: лейбл ЗАВЖДИ містить «Арчі» — трейдер ніколи не плутає агентські лінії зі своїми чи з SMC.
7. **Розділення шарів**: Арчі-лінії НЕ потрапляють у user-стор (`v4_drawings_*`), не мають undo, не editable. Двобічність (Арчі-об'єкти як editable drawings) — окремий майбутній ADR (§10), НЕ v1.

---

## 7. Companion: trader-v3 «Numeric Thesis Contract» (окремий ADR, X31)

Платформа з P4 приймає optional-поля в thesis hash. Бот-сторона (ОКРЕМИЙ trader-v3 ADR, писати в `trader-v3/docs/adr/`):
- `emit_directives`/thesis-writer додає до HSET: `key_level_price` (float-string), `invalidation_price` (float-string) — ЯКЩО Арчі назвав конкретні числа (він і так їх називає в wake-умовах; часто key_level тези == рівень будильника).
- Нуль нових Claude-викликів: поля пишуться тим самим записом, що й текстова теза.
- I8-дискипліна: промпт НЕ міняти без owner-go (поля можуть заповнюватись механічно з wake-params, без просьби до Арчі).
- До появи companion: шар живе на wake-умовах — уже цінно.

---

## 8. Alternatives (розглянуто, відхилено)

1. **Парсити key_level-рядок у фронті** («PDL 4650» → 4650) — X28-порушення, крихко (Арчі пише вільним текстом), відхилено категорично.
2. **Рендерити в OverlayRenderer (SMC-шар)** — змішує голоси: SMC = машинна аналітика, Арчі = агент. Окремий canvas = окремий життєвий цикл (frame-driven), чиста ізоляція, дешевий rollback.
3. **Рендерити як user-drawings (спільний стор)** — ламає модель власності (undo/erase/cross-tab торкалися б агентських ліній), плутає persistence (localStorage vs wire). Двобічність — свідомо пізніше (§10).
4. **Тягнути auto_wake (platform) умови на шар** — це дубль SMC-зон, шум; шар = ТІЛЬКИ голос Арчі (source=="bot").
5. **Пушити wake-умови через narrative** — narrative оновлюється на complete-bar; будильники хочуть жити в кожному frame стабільно; top-level поле простіше і чистіше tier-gate-иться.
6. **Новий Redis-канал для чарта** — зайвий: усе вже в пам'яті WakeEngine (кеш 30с), нуль нових I/O.

---

## 9. Consequences

**Позитив:** рівні Арчі видно там, де дивиться трейдер; нуль навантаження на бота (v1 = тільки читання наявного); контракт wake-умов вперше працює на людину, не лише на бота; ізольований шар = тривіальний rollback; ґрунт для «спільного полотна».

**Ризики/межі:**
- Зараз Redis-ключі порожні (Арчі не ставив будильників цього тижня) → шар після деплою може бути порожній до наступної активності Арчі. Це ЧЕСНО (нема даних — нема ліній); verify — на seed-даних.
- reason-рядки українською в wire — розмір frame: +≤1KB на 6 умов (прийнятно).
- Лінії Арчі можуть збігтися з user-hline на тому ж рівні — visual: різні стилі (dash-dot info vs user-стиль) + лейбл «Арчі»; повний конфлікт-дизайн — live-tune з owner.

**Rollback:** P1 — прибрати серіалізацію поля (wire чистий, UI ігнорує невідоме поле); P2+ — прибрати canvas + renderer (файл ізольований); config `enabled:false` — миттєвий продакшн-вимикач без деплою UI.

---

## 10. Future (за межами v1 — окремі ADR)

- **Двобічність**: Арчі «бачить» user-drawings (платформа шле снапшот у контекст бота через наявний канал) і коментує; його нотатки як read-only об'єкти з `source:"archi"`.
- **Жива лінія-сенсор** (ADR-0084 §Future): user-hline → кнопка «розбудити Арчі при перетині» → запис price_cross у wake:conditions (це ЗАПИС — потребує окремого рішення про право платформи писати в bot-простір, I7-делікатно: пишемо як `source:"user"`).
- **Wake-event маркери** (джерело E): «тут Арчі прокинувся» на осі часу.
- **Workspace → Chart**: ГОРН/воркспейс вбудовує чарт як центральну поверхню; панелі стають супутниками полотна. Великий ADR після обкатки v1.

---

## 11. Відкриті питання owner-у (перед P2/P3)

1. **Колір голосу Арчі**: v1 = `--info` (синій). Чи хочеш ОКРЕМИЙ токен `--archi` (наприклад, фіолет ГОРН-у) — щоб Арчі мав власний упізнаваний колір скрізь?
2. **Default toggle**: «Арчі на чарті» ON чи OFF за замовчуванням?
3. **P7 (рамка сценарію на SMC-зоні)** — включати у v1 чи відкласти?
