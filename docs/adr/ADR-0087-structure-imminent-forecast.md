# ADR-0087: Structure Imminent Forecast — anticipatory wake-kind `structure_imminent`

- **Status**: Accepted (owner «ок, спробуємо… го» 2026-07-15)
- **Date**: 2026-07-14 · **Rev 2**: 2026-07-15
- **Rev 2 (owner-уточнення)**: (1) **CHoCH = основна ціль прогнозу** («метою є
  передбачати choch до того, як він фактично з'явиться»), BOS — вторинна,
  вимкнена за замовчуванням (`targets: ["choch"]`); (2) пара 1m→5m **вилучена**
  (шум: base rate M1-подій + M5 не є thesis-TF + CPU-ціна 60s у compute_tfs);
  (3) Фаза 2 (score) — **умовна**, go/no-go за телеметрією Фази 1;
  (4) додано intrabar-тригер **`pending`** (§2.2b) — серцевина альтернативи B
  без зламу контракту; (5) умови **авто-армуються платформою** (паттерн
  auto_wake) — v1 працює без жодної зміни бота.
- **Owner**: Стас (діагноз + рішення A+B) · Claude (RECON, специфікація)
- **Related**: ADR-0047 (Structure Detection V2 — недоторканий), ADR-0049 (WakeEngine IPC),
  ADR-0075 (bar-close kinds — паттерн додавання kind), trader-v3 ADR-034 (Wake Conditions),
  trader-v3 ADR-078 (wake contract)
- **Tag**: `wake_engine_v1`

---

## 1. Контекст і проблема

### 1.1 Діагноз: пропущені рухи — не баг, а конструктивний лаг

Production-спостереження (owner): Арчі систематично «спізнюється» на рухи, які
ґейтяться підтвердженим CHoCH/BOS. Це не дефект детектора — це його означення:

1. **Свінг підтверджується тільки заднім числом.** Fractal pivot вимагає `period`
   барів праворуч: confirmed range = `range(period, n - period)`
   `[VERIFIED core/smc/swings.py:68; docstring :59-60 «Останні period барів — unconfirmed»]`.
   Config: `swing_period=5` default, `3` для M5/M15 через `tf_overrides`
   `[VERIFIED config.json smc.swing_period, smc.tf_overrides]`.
2. **Злам друкується тільки на закритому барі + confirmation.**
   `if not bar.complete: continue` `[VERIFIED core/smc/structure.py:196-197]`;
   кандидати BOS/CHoCH — по `bar.c` проти protected swing
   `[VERIFIED structure.py:203-241]`; далі multi-bar confirmation
   `confirm_count >= confirmation_bars` `[VERIFIED structure.py:243-272]`,
   `confirmation_bars=1` `[VERIFIED config.json smc.structure]`.
3. **Wake-шлях успадковує лаг як hard-block.** Kind `structure_break` фаєриться
   виключно з підтверджених подій: `if not structure_events: return False`
   `[VERIFIED core/smc/wake_check.py:92-102]`. Немає події — немає пробудження.

Арифметика лагу для M15: свінг-опора підтверджена мінімум 3 бари тому (45 хв),
перетин рівня стає подією лише на close бару (до 15 хв після фактичного
перетину), плюс confirmation. **Структурна подія завжди друкується ПІСЛЯ руху,
який вона мала б ґейтити.** Будильник, що дзвонить після події, з'їдає сенс
wake-бюджету.

### 1.2 Чому це I7/P5-проблема, хоч код нікого явно не блокує

I7 (Autonomy-First) порушується тут не явним `if blocked: return`, а відсутністю
anticipatory-сигналу як такого: Арчі **фізично не може** попросити «розбуди мене
ПЕРЕД зламом» — такого kind не існує. Рішення «коли Арчі бачить ринок» приймає
геометрія детектора, а не Арчі. Ефект ідентичний hard-gate (P5): пробудження
структурно спізнене.

### 1.3 Чому не можна «просто швидший confirm»

Передбачити *підтверджений* CHoCH раніше, не змінивши означення confirm,
неможливо — це тавтологія. Зменшення `period`/`confirmation_bars` = зміна
означення: більше шуму, ламається калібрування ADR-0047, а лаг лишається
структурно (свінг усе одно підтверджується заднім числом). Отже потрібен
**окремий anticipatory-сигнал** — advisory, поруч із confirmed, не замість.

---

## 2. Рішення: wake-kind `structure_imminent` — гібрид A+B

Новий тип wake-умови: «ймовірний CHoCH/BOS на TF X наближається». Прогноз,
не факт. Двофазне впровадження: Фаза 1 дає цінність майже без коду, Фаза 2
підвищує precision.

### 2.1 W-правила (межі рішення)

| # | Правило |
|---|---------|
| W0 | **Confirmed detector (ADR-0047) недоторканий.** Нуль змін у `swings.py`/`structure.py`, нуль змін семантики `structure_break`. `structure_imminent` — додатковий kind поруч. |
| W1 | **Advisory, не hard-gate** (I7). Подія будить Арчі і дає контекст; рішення (входити/чекати/ігнорувати) — його. Жодної код-політики поверх. |
| W2 | **Pure + $0** (S0/I0). Перевірка в `core/smc` без I/O; всі вхідні дані вже в пам'яті SmcRunner (price, ATR, swings, structure events, FVG, momentum), тік кожні 2s у delta_loop. |
| W3 | **False positive = інформація, не помилка.** Imminent без наступного confirm — це failed break / liquidity trap, самостійно цінний сигнал для learning journal Арчі. |

### 2.2 Фаза 1 — MTF leading (майже нуль коду)

**Тригер `mtf`**: підтверджений CHoCH/BOS на нижчому TF у бік protected swing
вищого TF, коли ціна в межах `prox_atr` від цього рівня.

Логіка: LTF-структура веде HTF-структуру. M5 CHoCH bull за 0.4 ATR до M15 LH —
класичний передвісник M15 CHoCH. Все вже порахованo: `structure.py` працює
per-TF, події буферизуються per-symbol із `tf_s`
`[VERIFIED runtime/smc/smc_runner.py:575-588 get_recent_structure_events]`.

**Protected levels — деривація без дотику до детектора (W0).** Стан
`last_hh/hl/ll/lh` — локальні змінні `detect_structure_events`, назовні не
експортуються (return = 4-кортеж; розширення зламало б ~25 call-sites у
тестах). Натомість: `snapshot.swings` вже містить classified swings + structure
events разом, відсортовані за `time_ms` `[VERIFIED core/smc/engine.py:989-990]`
— armed-рівні відновлюються чистим реплеєм у новому модулі
`core/smc/structure_forecast.py` (`derive_armed_levels`): swing kind
`hh/hl/ll/lh` армує рівень, подія `bos_bull/choch_bear/bos_bear/choch_bull`
консюмить відповідний (дзеркало `[VERIFIED structure.py:170-178]`); tie-break
при рівних `time_ms` — swing перед подією (дзеркало
`[VERIFIED structure.py:182-197]`: while-арм до перевірки зламу). Gating
цільових зламів по `trend_bias` — дзеркало `[VERIFIED structure.py:203-241]`.
Це свідомий mirror-debt (D15.2) — закритий consistency-тестом проти реального
детектора; тригер на перегляд у §10.

**Пари TF** (з наявного `compute_tfs = [300, 900, 3600, 14400, 86400]`
`[VERIFIED config.json smc.compute_tfs]`):

| leading → target | Статус |
|---|---|
| 300 → 900 (5m→15m) | ✅ v1, з коробки |
| 900 → 3600 (15m→1h) | ✅ v1, з коробки |
| 60 → 300 (1m→5m) | ❌ **не планується** (rev 2): base rate M1-подій → alarm fatigue; M5 не є thesis-TF (нема споживача прогнозу); 60s у compute_tfs = CPU/lookback-ціна. Повернення можливе лише якщо телеметрія Фази 1 покаже потребу |

**Авто-армування (rev 2)**: умови генерує платформа кожен tick — новий чистий
генератор `generate_imminent_conditions()` поруч із
`generate_platform_conditions()` (паттерн ADR-0049 Strategy B, `source:
"platform"`). Рівень резолвиться в момент генерації → умова несе готові числа,
а життєвий цикл природний: рівень consumed підтвердженою подією або ціна
пішла з arm-радіуса → умова зникає сама. Бот НЕ потребує змін для v1; бот-задані
`structure_imminent`-умови — companion trader-v3 ADR.

**Params contract (resolved, rev 2)**:

```json
{"kind": "structure_imminent",
 "params": {"tf_s": 900, "leading_tf_s": 300, "target": "choch_bear",
            "level": 4712.4, "direction": "below", "protected_swing": "hl",
            "prox_atr": 1.0, "window_s": 1800, "atr_tf": 8.4}}
```

Semantics `mtf`: ціна в межах `prox_atr × atr_tf` від `level`, і в буфері є
підтверджена LTF-подія (`tf_s == leading_tf_s`) свіжіша за `window_s`, напрям
якої збігається з напрямом зламу (`direction`).

### 2.2b Фаза 1 — тригер `pending`: пробій у процесі (rev 2)

Другий тригер тієї ж умови: **ціна перетнула `level` intrabar** у напрямі
зламу (`above` → `price >= level`, `below` → `price <= level`), а підтвердженої
події ще нема. Це «злам відбувається ПРЯМО ЗАРАЗ, чекаємо close +
confirmation» — зрізає найгострішу частину лагу (до 15 хв M15-бару + confirm)
навіть без LTF-події. Серцевина альтернативи B (§6) без зламу контракту
«events = confirmed»: жодна preview-подія не публікується, сигнал живе лише у
wake-шляху. Відміна від `price_cross`: Арчі не армує рівень сам — платформа
виводить його зі структурного стану, meta несе структурну семантику
(`protected_swing`, `target`). Фактичний тригер (`pending`/`mtf`) зазначається
у meta події.

### 2.3 Фаза 2 — probability score `P_imminent` (умовна, rev 2)

**Go/no-go за телеметрією Фази 1.** Якщо false-positive rate Фази 1 прийнятний
для Арчі й owner — score не будується взагалі; якщо шумить — телеметрія
(вектори факторів у meta) дає ваги з даних, не з голови. Рішення про запуск
Фази 2 = окреме owner-погодження після спостереження.

Поверх бінарних тригерів — калібрований скор:

```
P_imminent = sigmoid( k · Π fᵢ^wᵢ − b )        fᵢ ∈ [0,1]
```

Мультиплікативне ядро (log-домен = зважена сума логів) — навмисне: семантика
**AND** — кожен фактор необхідний, один нульовий фактор гасить прогноз. Фактори:

| fᵢ | Зміст | Джерело (все в пам'яті SmcRunner) |
|---|---|---|
| `prox` | близькість до protected swing, нормована ATR | price, swings, ATR |
| `energy` | імпульсність підходу (тіла барів vs ATR) | momentum detector |
| `vel` | швидкість наближення до рівня (ATR/bar за N барів) | bars window |
| `sweep` | свіжий liquidity sweep у зоні підходу | liquidity/inducement |
| `fvg` | свіжий FVG у напрямі зламу | fvg detector |

**Різні ваги для BOS і CHoCH**: BOS = continuation (енергія/швидкість важать
більше), CHoCH = reversal (sweep/inducement важать більше, бо розворот без
зняття ліквідності — найчастіший false break).

**Пороги**: `0.6` для BOS / `0.7` для CHoCH — reversal шумніший, ціна false
wake вища.

**Anti-flicker (уточнено errata E2)**: подія фаєриться раз на ключ
`(tf_s, level, direction, phase)` у межах `event_cooldown_s.structure_imminent`.
`phase` у ключі принциповий: `mtf` («наближається») і `pending» («перетин
прямо зараз») — окремі бюджети, інакше рання mtf-подія гасила б ескалацію до
pending на той самий рівень — найцінніший сигнал у канонічній послідовності.
Максимум 2 події на рівень за cooldown-вікно. Супресії лічаться
(`_imminent_suppressed`, debug-лог) — не сліпа зона.

**Калібрування — telemetry-first**: кожен fired imminent пише вектор факторів +
outcome (`confirmed_within_n_bars` / `failed`) у meta події → ваги тюняться з
даних, не з голови. До накопичення телеметрії ваги = стартові рівні (1.0).

### 2.4 Event contract + scaled-entry playbook (advisory)

`WakeEvent.meta` — блок `fired_imminent` (паттерн `fired_zones` ADR-037):

```json
{"fired_imminent": [
   {"target": "choch_bull", "tf_s": 900, "leading_tf_s": 300,
    "level": 4712.4, "protected_swing": "lh", "direction": "above",
    "atr_tf": 8.4, "phase": "pending"}]}
```

`phase`: `"pending"` (§2.2b, перетин рівня) | `"mtf"` (§2.2, LTF leading) |
`"prob"` (Фаза 2, якщо буде). Фаза 2 додасть `p_score` + `features`.

`reason` людською мовою (йде в prompt Арчі):
«M5 CHoCH bull у бік M15 LH 4712 (0.4 ATR) — можливий M15 CHoCH bull».

**Scaled-entry** (рекомендація у промпті/DNA Арчі, НЕ код-політика — I7):
- `structure_imminent` → часткова позиція, інвалідація за protected swing
  (рівень відомий з meta — це і є природний SL прогнозу);
- `structure_break` (confirmed) → add до повного розміру.

Бот-сторона (усвідомлення нового kind у промпті, playbook, запис умов) —
**companion ADR у `trader-v3/docs/adr/`** (X31: цей документ не специфікує код бота).

---

## 3. Інваріанти

| Інваріант | Як дотримано |
|---|---|
| I0 | перевірка в `core/smc`, не імпортує runtime |
| S0/S1 | pure functions, zero I/O; WakeEngine read-only, нічого не пише в UDS |
| I5 | відсутній getter у SmcRunner → warning + kind повертає False (паттерн ADR-0075, `[VERIFIED wake_engine.py:194-200]`), не silent |
| I7 | advisory: жодного блокування/форсування рішень Арчі; false positive віддається як інформація (W3) |
| X28 | якщо imminent колись рендериться в UI (archi-layer ADR-0085) — тільки бекенд-значення, нуль перерахунків у фронті |
| X31 | бот-сторона = окремий trader-v3 ADR |

---

## 4. Config (config.json SSOT)

```json
"wake_engine": {
  "event_cooldown_s": { "structure_imminent": 900 },
  "structure_imminent": {
    "enabled": true,
    "targets": ["choch"],
    "tf_pairs": [[300, 900], [900, 3600]],
    "prox_atr": 1.0,
    "arm_radius_atr": 2.0,
    "window_s": 1800,
    "max_conditions": 4
  }
}
```

`targets: ["choch"]` — CHoCH-first (rev 2, мета owner); BOS вмикається
додаванням `"bos"`. `arm_radius_atr` — радіус, у якому умова взагалі
генерується (ширший за `prox_atr`, щоб `pending` жив і за рівнем). Секція
Фази 2 (`thresholds`/`weights`) додається лише при go (§2.3). Жодних hardcoded
порогів/ваг у коді.

---

## 5. Implementation Plan (P-slices)

| # | Задача | Файли | LOC | Verify |
|---|--------|-------|-----|--------|
| P1 | `WakeConditionKind.STRUCTURE_IMMINENT` + params-doc | `core/smc/wake_types.py` | ≤15 | import + enum test |
| P2 | `core/smc/structure_forecast.py`: `derive_armed_levels` (реплей) + `generate_imminent_conditions` (авто-арм, CHoCH-first) + гілка `structure_imminent` у `check_condition` (тригери `pending`/`mtf`) | NEW + `core/smc/wake_check.py` + tests | ≤150 | pytest: consistency-тест реплею проти реального детектора; pending above/below; mtf aligned/стala/проти напряму; targets-фільтр; arm-радіус |
| P3 | SmcRunner thin-getter `get_raw_snapshot(symbol, tf_s)` (raw engine snapshot — БЕЗ display HTF-ін'єкції, яка зіпсувала б реплей) | `runtime/smc/smc_runner.py` | ≤20 | unit: pass-through + None до warmup |
| P4 | WakeEngine wiring: generate → merge → check → meta `fired_imminent` + dedup-ключ `(tf,direction,level)` + config | `runtime/smc/wake_engine.py`, `config.json` | ≤50 | pytest existing suite зелений; **live round-trip (D15.1)** після deploy: реальна умова → запис у `wake:events` |
| P5 | Фаза 2 (умовна, після go §2.3): `core/smc/imminence_score.py` — pure `P_imminent` | NEW + tests | ≤100 | pytest: монотонність по фактору; нульовий фактор гасить скор |
| P6 | Фаза 2 wiring + telemetry features у meta | `runtime/smc/wake_engine.py` | ≤40 | live round-trip: p_score + features у meta |
| P7 | Companion trader-v3 ADR: prompt-awareness kind + scaled-entry playbook + бот-задані imminent-умови | `trader-v3/docs/adr/` | doc | окремий документ (X31) |

Послідовність: P1→P2→P3→P4 (Фаза 1) → deploy + спостереження → go/no-go →
P5→P6 (Фаза 2, якщо go). P7 після P4.

---

## 6. Alternatives Considered

| # | Альтернатива | Чому відхилено |
|---|---|---|
| A | Зменшити `swing_period`/`confirmation_bars` (швидший confirm) | Зміна означення confirm: більше шуму, ламає калібрування ADR-0047; лаг лишається структурно (свінг підтверджується заднім числом завжди) |
| B | Emit unconfirmed preview-подій зі `structure.py` | Ламає контракт «events = confirmed» для всіх downstream (narrative, signals, UI, wake) — split semantics, найдорожчий вид плутанини. **Rev 2: серцевина B поглинена тригером `pending` (§2.2b)** — «злам у процесі» живе лише у wake-шляху, жодна preview-подія не публікується |
| C | Тільки MTF leading (чистий варіант A) | Binary і шумний у флеті (LTF ламається постійно); прийнято як Фаза 1, але без score не масштабується |
| D | Тільки probability score (чистий варіант B) | Cold-start калібрування з нуля, непрозоро для Арчі й owner; без MTF-якоря score легко самообманюється |
| E | Бот сам «вгадує» злам через частіший polling/аналіз | $$ Claude calls + 30s poll проти 2s delta_loop платформи — суперечить економіці ADR-0049 ($0 checks на платформі) |

Рішення = **C+D як фази одного механізму**: MTF-якір дає негайну цінність і
опору для score; score додає precision там, де binary шумить.

---

## 7. Consequences

**Плюси**:
- Арчі отримує право прокинутись ДО руху — вперше anticipatory-сигнал у wake
  quartet; scaled-entry стає можливим (часткова до confirm, add після).
- Confirmed-шлях недоторканий: надійність ADR-0047 не розмінюється на швидкість.
- Failed imminent = безкоштовна розмітка liquidity traps для learning journal.

**Мінуси / ціна**:
- False positives неминучі — це властивість прогнозу, не дефект (W3); бюджет
  шуму контролюється порогами + cooldown + anti-flicker.
- Ваги Фази 2 вимагають телеметрії до тюнінгу — перші тижні score працює на
  стартових вагах (чесно позначається у meta як `"calibration": "default"`).
- +1 kind у контракті wake (trader-v3 ADR-078 companion-оновлення).

---

## 8. Rollback

- **М'який**: `wake_engine.structure_imminent.enabled = false` — одна зміна
  config + restart; kind перестає фаєритись, wake quartet і confirmed-шлях
  недоторкані.
- **Повний**: прибрати гілку в `wake_check.py`, enum-значення та getter —
  зміни строго additive, зворотних залежностей нуль.

---

## 9. Quality Axes

- **Ambition target**: R3 (anticipatory-механізм поверх зрілого детектора,
  новий клас сигналу для агента).
- **Maturity impact**: M4→M4 (additive pure-модулі з тестами, існуючі шляхи
  не змінюються; підйом до M4.5 — після live-калібрування Фази 2).

---

## 10. Відкриті питання (не блокують v1)

1. Форма ядра score: мультиплікативна (AND) vs зважена сума (компенсаторна) —
   вирішується телеметрією Фази 2, контракт meta однаковий.
2. Показ imminent-рівнів на archi-layer чарту (ADR-0085) — окреме UI-рішення
   після обкатки (X28-сумісне: рівень уже в wire-придатному вигляді).
3. **Mirror-debt `derive_armed_levels` (D15.2)**: реплей дублює consumption- і
   gating-семантику детектора. Закрито consistency-тестом; тригер на перегляд —
   якщо `detect_structure_events` колись розширюватиме return (напр., при
   наступному ADR по structure), armed-рівні перевести на прямий експорт і
   видалити реплей.
4. Wire-контракт бота: чи пропустить `wake_reader` невідомий kind
   `structure_imminent` (whitelist trader-v3 ADR-078)? ✅ **Закрито
   2026-07-15**: whitelist'а нема — парсер вимагає лише поле `"kind"`
   `[VERIFIED trader-v3/bot/transport/wake_reader.py:84]`,
   `format_wake_prompt` рендерить будь-який kind + reason.

---

## 11. Errata

### E1 (2026-07-15): publish-голодування — imminent ніколи не публікувався

**Симптом**: за перші 21h на проді — 0 подій `structure_imminent` при ~15
confirmed CHoCH на M15/H1. Read-only replay на живих барах: **17/17** CHoCH
мали б `pending`-fire до підтвердження.

**Root cause**: P4-wiring поклався на наявний publish-шлях WakeEngine, який
публікує МАКСИМУМ ОДИН event на tick з `kind = fired[0]`, а при
dedup-cooldown першої умови робить `return` на весь tick. Ціна майже завжди
сидить у/біля зон → `price_zone_touch` попереду списку (169 подій за 21h) →
imminent або деградував до непомітного `meta.fired_imminent` у чужій події
(бот рендерить лише kind+reason), або гасився early-return'ом. Провал класу
D15: pure-логіка була ідеально протестована, wiring — ні.

**Fix**: окремий publish-блок 6a для `structure_imminent` ДО general-блоку:
власний dedup-ключ `(tf, direction, level)`, cooldown зі спільного
`event_cooldown_s`, скидання accumulator + `last_wake_ts` при публікації.
Подія тепер завжди виходить із `kind="structure_imminent"` — як і обіцяв
контракт §2.4. Регресія закрита трьома інтеграційними тестами `_tick_symbol`
(zone_touch поруч; zone у cooldown-return; власний cooldown imminent) —
першими інтеграційними тестами publish-шляху взагалі.

**Урок (у скарбничку D15)**: «событие у meta чужої події» ≠ «подія
опублікована». Для LLM-споживача видимість = kind+reason; усе інше — тиша.

### E2 (2026-07-15): знахідки adversarial-ревʼю фікса E1 (bug-hunter)

Ревʼю E1-диффа: S0/S1 введених нема; 1×S2 + 4×S3, усі закриті тим самим
патчем:

- **D-01 (S2)**: dedup-ключ без `phase` гасив ескалацію mtf→pending на той
  самий рівень протягом cooldown → `phase` додано в ключ (§2.3 уточнено).
- **D-02 (S3)**: `_event_dedup` без eviction (imminent множить ключі
  per-level) → prune стейлу >24h при >1024 ключів.
- **D-03 (S3)**: тиха cooldown-супресія → лічильник `_imminent_suppressed`
  + debug-лог.
- **D-04 (S3)**: асиметрія meta imminent- vs загальної події → додано
  `accumulator_score`/`conditions_total`/`conditions_fired`. Surfacing
  `fired_imminent` у промпт Арчі — досі companion P7 (бот рендерить лише
  kind+reason).
- **D-05 (S3)**: `accumulator_score` у meta загальної події брався ПІСЛЯ
  6a-reset (показував 0) → знімок score на початку tick.
