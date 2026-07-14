# ADR-0087: Structure Imminent Forecast — anticipatory wake-kind `structure_imminent`

- **Status**: Proposed
- **Date**: 2026-07-14
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

**Тригер**: підтверджений CHoCH/BOS на нижчому TF у бік protected swing вищого
TF, коли ціна в межах `prox_atr` від цього рівня.

Логіка: LTF-структура веде HTF-структуру. M5 CHoCH bull за 0.4 ATR до M15 LH —
класичний передвісник M15 CHoCH. Все вже порахованo: `structure.py` працює
per-TF, події буферизуються per-symbol із `tf_s`
`[VERIFIED runtime/smc/smc_runner.py:575-588 get_recent_structure_events]`,
protected swings (`last_hh/hl/ll/lh`) — стан детектора кожного TF.

**Пари TF** (з наявного `compute_tfs = [300, 900, 3600, 14400, 86400]`
`[VERIFIED config.json smc.compute_tfs]`):

| leading → target | Статус |
|---|---|
| 300 → 900 (5m→15m) | ✅ v1, з коробки |
| 900 → 3600 (15m→1h) | ✅ v1, з коробки |
| 60 → 300 (1m→5m) | ⚠️ gated: `60` НЕ в `compute_tfs` — окремий config-крок з оцінкою CPU/lookback-бюджету; не блокує v1 |

**Params contract**:

```json
{"kind": "structure_imminent",
 "params": {"tf_s": 900, "leading_tf_s": 300, "target": "choch", "prox_atr": 1.0}}
```

`target`: `"choch" | "bos" | null` (null = будь-який). Semantics: протягом вікна
свіжості LTF-події (`imminent_window_s`) ціна в межах `prox_atr × ATR(tf_s)` від
protected swing target-TF, і напрям LTF-події збігається з напрямом потенційного
зламу.

### 2.3 Фаза 2 — probability score `P_imminent`

Поверх бінарного MTF-тригера — калібрований скор:

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

**Anti-flicker**: подія фаєриться раз на ключ `(tf_s, level, direction)` до
інвалідації (ціна пішла геть > `invalidate_atr`) або до confirmed-події; поверх
цього — стандартний `event_cooldown_s.structure_imminent` (dedup-механіка
`[VERIFIED runtime/smc/wake_engine.py:240-247]`).

**Калібрування — telemetry-first**: кожен fired imminent пише вектор факторів +
outcome (`confirmed_within_n_bars` / `failed`) у meta події → ваги тюняться з
даних, не з голови. До накопичення телеметрії ваги = стартові рівні (1.0).

### 2.4 Event contract + scaled-entry playbook (advisory)

`WakeEvent.meta`:

```json
{"target": "choch_bull", "tf_s": 900, "leading_tf_s": 300,
 "level": 4712.4, "protected_swing": "lh", "p_score": 0.74,
 "phase": "mtf", "features": {"prox": 0.9, "energy": 0.7, "...": "..."}}
```

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
    "tf_pairs": [[300, 900], [900, 3600]],
    "prox_atr": 1.0,
    "imminent_window_s": 1800,
    "invalidate_atr": 2.0,
    "thresholds": { "bos": 0.6, "choch": 0.7 },
    "weights": {
      "bos":   { "prox": 1.0, "energy": 1.0, "vel": 1.0, "sweep": 0.5, "fvg": 0.5 },
      "choch": { "prox": 1.0, "energy": 0.7, "vel": 0.7, "sweep": 1.0, "fvg": 0.7 }
    }
  }
}
```

Жодних hardcoded порогів/ваг у коді.

---

## 5. Implementation Plan (P-slices)

| # | Задача | Файли | LOC | Verify |
|---|--------|-------|-----|--------|
| P1 | `WakeConditionKind.STRUCTURE_IMMINENT` + params-doc | `core/smc/wake_types.py` | ≤15 | import + enum test |
| P2 | Pure check Фази 1 (MTF leading): нова гілка в `check_condition` + вхід `protected_levels` | `core/smc/wake_check.py` + tests | ≤80 | pytest: leading event + prox → True; без події / далеко / проти напряму → False |
| P3 | SmcRunner getter `get_protected_swing_levels(symbol)` (дзеркало `get_recent_structure_events`) | `runtime/smc/smc_runner.py` | ≤40 | unit: рівні відповідають стану детектора |
| P4 | WakeEngine wiring Фази 1 + config cooldown | `runtime/smc/wake_engine.py`, `config.json` | ≤30 | **live round-trip (D15.1)**: seed LTF-подію → запис у `wake:events` |
| P5 | Фаза 2: `core/smc/imminence_score.py` — pure `P_imminent` (features + sigmoid) | NEW + tests | ≤100 | pytest: монотонність по кожному фактору; нульовий фактор гасить скор |
| P6 | WakeEngine wiring Фази 2 + anti-flicker ключ + telemetry meta | `runtime/smc/wake_engine.py` | ≤40 | live round-trip: p_score + features у meta події |
| P7 | Companion trader-v3 ADR: prompt-awareness kind + scaled-entry playbook | `trader-v3/docs/adr/` | doc | окремий документ (X31) |

Послідовність: P1→P2→P3→P4 (Фаза 1, sequential) → deploy + спостереження →
P5→P6 (Фаза 2). P7 паралельно після P4. 1m→5m (60s у `compute_tfs`) — окремий
gated крок після оцінки CPU, поза цим планом.

---

## 6. Alternatives Considered

| # | Альтернатива | Чому відхилено |
|---|---|---|
| A | Зменшити `swing_period`/`confirmation_bars` (швидший confirm) | Зміна означення confirm: більше шуму, ламає калібрування ADR-0047; лаг лишається структурно (свінг підтверджується заднім числом завжди) |
| B | Emit unconfirmed preview-подій зі `structure.py` | Ламає контракт «events = confirmed» для всіх downstream (narrative, signals, UI, wake) — split semantics, найдорожчий вид плутанини |
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
   після обкатки (X28-сумісне: рівень+score вже в wire-придатному вигляді).
3. 60s у `compute_tfs` для пари 1m→5m — потребує заміру CPU/lookback на VPS.
