# ADR-0086: Structure-Imminent Forecast — Anticipatory BOS/CHoCH Wake (MTF-leading + probability)

- **Status**: Proposed — 2026-07-14
- **Deciders**: Стас (owner — «майже всі пропущені рухи Арчі бо чекає обов'язкову умову входу, а CHoCH з'являється вже після руху»), Claude (R_SMC_CHIEF + R_SIGNAL_ARCHITECT)
- **Related**: ADR-0047 (Structure Detection V2 — canonical BOS/CHoCH, the confirmed detector this does NOT replace), ADR-0024 §4.1/§4.7 (swings, inducement), ADR-0049 (WakeEngine types/check IPC), ADR-040 errata A2 (`structure_events` buffer — the pattern reused), ADR-0075 (bar-close kinds — sibling wake kind, same buffer→drain→pure-check shape), ADR-0085 §Арчі-on-chart (imminent lines are a future render surface).

## Quality Axes
- **Ambition**: R3 — закриває реальний торговий gap (Арчі систематично спізнюється, бо структура **конструктивно** підтверджується після руху), не переписуючи перевірений detector.
- **Maturity**: M3→M4 — новий pure-модуль тримає 1 інваріант (forecast ≠ confirm), дзеркалить перевірений buffer→drain→pure-check патерн, advisory-only (I7), новий wake-kind під contract-drift-gate.

## 1. Контекст

**Симптом (owner):** Арчі має всі умови, чекає рух, але вхід ґейтиться обов'язковим підтвердженням CHoCH на 5m/15m — а CHoCH з'являється **вже після** руху, який Арчі хотів узяти. Символ іде без нас.

**Root cause — це не баг, а конструктивний лаг** `[VERIFIED]`:
- `swings.py:68` — фрактальний pivot підтверджується лише через `period` барів **праворуч** (lookahead). Свінг стає «видимим» уже після руху.
- `structure.py:204-219` — BOS/CHoCH емітиться **тільки на `bar.c`** (close за рівнем) + `confirmation_bars` (`structure.py:119-121`).
- `wake_check.py:92-102` — `STRUCTURE_BREAK` спрацьовує лише на **вже наявний** event.

Тобто «чекати підтверджений CHoCH» = чекати два шари лагу (свінг-lookahead + close-confirm). До сигналу рух уже стався. **Передбачити підтверджений CHoCH раніше без зміни означення «що його підтверджує» неможливо.** Отже потрібен окремий *anticipatory* сигнал — ймовірність зламу **до** close — і він мусить бути **advisory, не hard-gate** (I7/P5): наявна «обов'язкова умова CHoCH» типу `if not choch: return` це і є hard-block, який робить Арчі спізнілим.

**Друге спостереження:** BOS (continuation) і CHoCH (reversal) — різні за передбачуваністю. BOS: тренд уже є, ціна підходить до попереднього екстремуму з моментумом — ризик передбачення низький. CHoCH: розворот майже завжди = liquidity grab протилежного екстремуму + displacement назад через internal structure — потрібна вужча confluence, щоб не ловити false reversals.

**Наявні примітиви (нічого не вигадуємо):** `SmcSnapshot.swings` (класифіковані HH/HL/LH/LL, `types.py:270`) + `SmcSnapshot.trend_bias` (`types.py:272`) дають тригер-рівні й напрям напряму; `momentum.py:17` (displacement), `inducement.py:32` (sweep+reversal), FVG — уже обчислюються.

## 2. Альтернативи

- **Alt A — MTF leading confirmation.** Замість «чекати CHoCH на 5m/15m» — детектувати **confirmed CHoCH/BOS на нижчому TF** (1m для 5m-цілі, 5m для 15m-цілі) у напрямку HTF-рівня, до якого підходить ціна. `structure.py` вже працює по-TF → майже нуль нового коду. ICT-канон («LTF confirmation for HTF entry»), детермінізм, природна інвалідація (LTF swing). **−** 1m шумніший → фільтр displacement (`momentum.py`).
- **Alt B — Anticipatory probability score.** Логістична згортка `proximity + energy + velocity + sweep + fvg` → `P_imminent ∈ [0,1]` **до** close. Плавна градація замість бінарного gate; калібрується. **−** нові ваги/пороги = нова поверхня, треба калібрувати + live round-trip.
- **Alt C — CHoCH-specific reversal sequence.** Жорстка послідовність sweep→displacement→FVG→return тільки для розворотів. Найточніший, вужче покриття. Фактично B з жорсткою sequence-умовою.
- **Alt D — знизити `confirmation_bars`/`period`.** ❌ Ламає перевірений detector глобально (ADR-0047), більше false BOS/CHoCH для ВСІХ споживачів (сигнали, overlay, narrative). Лікує симптом, не модель (D13.6).
- **Alt E (ОБРАНО) — A+B гібрид.** A як база (дешевий, канонічний, live-first), B поверх як градуйований шар, коли A доведено live round-trip'ом (D15). C відкладено як майбутнє уточнення ваг B для CHoCH. **Confirmed detector (ADR-0047) недоторканий** — forecast живе поруч, не замість.

## 3. Рішення

Новий wake-kind `structure_imminent` + новий pure-модуль `core/smc/structure_forecast.py`. **Advisory-only**: інформує Арчі, не блокує і не форсує (I7). Наявний `STRUCTURE_BREAK` (confirmed) лишається — forecast його доповнює, не замінює.

### 3.1 Фаза 1 — MTF leading (Alt A), перший slice

1. **`core/smc/structure_forecast.py:detect_leading_break()`** (pure, S0) — приймає LTF structure_events (той самий буфер `_recent_structure_events`, формат `{tf_s, type, direction, ts_ms, price}` `[VERIFIED smc_runner.py:505-511]`) + HTF-тригер-рівень `L` (найближчий `last_hh/hl/ll/lh` зі `snap.swings` у бік руху) + напрям HTF `trend_bias`. Повертає leading-подію, коли LTF `choch/bos` надрукований **у бік `L`** і ціна в reach-вікні `L`.
2. **TF-мапінг (config, S5):** `wake_engine.structure_forecast.leading_tf_map = {300: 60, 900: 300}` (5m←1m, 15m←5m). SSOT у config.json, не hardcode.
3. **`WakeConditionKind.STRUCTURE_IMMINENT`** = `"structure_imminent"` (`wake_types.py`).
4. **`wake_check.check_condition`** — нова гілка `STRUCTURE_IMMINENT`: приймає drained leading-events (mirror `structure_events` param), fire якщо є leading-подія matching `target_tf` + `type` + `direction`. **Pure** (S0).
5. **`WakeEngine._tick_symbol`** — drain leading-events через наявний `get_recent_structure_events` (LTF), передає у `check_condition` (той самий шлях, що structure_events/bar_close_events, `wake_engine.py:190-224`).
6. **Payload:** `{kind, trigger_level, distance_atr, direction, leading_tf, drivers:["ltf_choch","displacement"]}` — advisory meta.

### 3.2 Фаза 2 — Probability score (Alt B), окремий slice після live-verify Фази 1

`structure_forecast.py:forecast_break()` (pure, S0), на кожен closed HTF-бар, per (symbol, tf, direction). Тригер-рівень `L`:

| kind | L (зі `snap.swings`) |
|---|---|
| `bos_bull` | last **HH**.price |
| `bos_bear` | last **LL**.price |
| `choch_bear` | last **HL**.price (protected low в uptrend) |
| `choch_bull` | last **LH**.price (protected high в downtrend) |

Компоненти (нормовані в `[0,1]`, всі з уже обчислюваних величин):

```
gap      = |L - price| / ATR_tf                       # відстань до тригера, в ATR
prox     = clamp(1 - gap / D_max, 0, 1)               # D_max ≈ 1.5·ATR (reach-вікно)

# енергія в бік L — з compute_momentum_score(lookback=K) → (bull_n, bear_n)
energy   = clamp(dir_n/K - 0.5·opp_n/K, 0, 1)         # displacement у потрібний бік

# швидкість підходу → bars-to-touch
v        = (price - price[-M]) / M                     # per-bar drift (знак у бік L)
eta_bars = gap·ATR / max(v, eps)
vel      = clamp(1 - eta_bars / N_max, 0, 1)          # ближче в часі → вище

sweep    = 1 if за останні W барів знято протилежний extreme (wick+close back)   # inducement.py:32
fvg      = 1 if є незакритий FVG на шляху price→L у напрямку

# логістична згортка, ваги b* з config (S5)
z            = b0 + b_prox·prox + b_energy·energy + b_vel·vel + b_sweep·sweep + b_fvg·fvg
P_imminent   = sigmoid(z)
```

**Різниця BOS vs CHoCH — у вагах (config, дві групи):**
- **BOS (continuation):** домінують `prox + energy + vel`. Поріг fire ≈ 0.6.
- **CHoCH (reversal):** домінують `sweep + energy` (розворот = liquidity grab + displacement). Поріг ≈ 0.7 (строгіше).

Fire `structure_imminent` з `p_imminent`, коли `P ≥ поріг`. Payload додає `p_imminent`, `eta_bars`, `drivers` (топ-компоненти).

### 3.3 Наскрізна рамка входу (scaled-entry, R_SIGNAL_ARCHITECT)

Передбачення = торгівля setup (нижчий hit-rate). Тому **advisory-контракт для Арчі**:
- `structure_imminent` → **часткова** позиція дозволена з інвалідацією на protected swing (`L` для CHoCH / останній HL-HH для BOS);
- **confirmed** CHoCH/BOS (наявний `STRUCTURE_BREAK`, ADR-0047) → **add** до повної.

Це і прибирає hard-block `if not choch: return`, не ламаючи confirmed detector. Рішення про розмір/вхід приймає **Арчі** — платформа лише постачає градуйований сигнал + інвалідацію (I7).

## 4. Consequences
- **+** Арчі входить на setup ще до close-confirm → пропущені рухи закриваються по кореню (модель, не симптом — D13.6).
- **+** Confirmed detector (ADR-0047) недоторканий — нуль регресій для сигналів/overlay/narrative.
- **+** Нуль нового I/O — LTF structure_events уже течуть через наявний буфер; forecast pure, $0 drain/check (mirror ADR-0075).
- **+** Advisory-only (I7) — жодного hard-block/force; scaled-entry поважає нижчий hit-rate передбачення.
- **−** Anticipatory сигнал має false-positives (рух не дійшов до `L`) → мітигація: scaled entry + інвалідація + (Фаза 2) поріг `P`.
- **−** Фаза 2 додає ваги/пороги (нова калібрувальна поверхня) → мітигація: A працює без них; ваги в config (S5), не hardcode; обов'язковий live round-trip (D15) до «done».
- **−** Ще один per-symbol drain у WakeEngine (LTF events) — малий, FIFO cap 50 (наявний), thread-safe.

## 5. Rollback
- Фаза 1: видалити гілку `STRUCTURE_IMMINENT` з `wake_check` + drain з `wake_engine` + `structure_forecast.py` → kind просто не fired, поведінка як до 0086. Confirmed `STRUCTURE_BREAK` не зачеплений.
- Фаза 2: `forecast_break()` за config-флагом `structure_forecast.probability_enabled` (default false доки не калібровано) → вимкнути = лишається чистий A.
- Жодних persisted-даних / schema-міграцій — усе in-memory ephemeral (S1). Config-ключі — additive.

## 6. Cross-repo
Платформна частина — у v3 (`core/smc/structure_forecast.py`, `core/smc/wake_check.py`, `runtime/smc/wake_engine.py`). Контракт-дзеркало (новий kind `structure_imminent` + params `{trigger_level, direction, tf_s, leading_tf, p_imminent}`) — trader-v3 `wake_params` canonical keys + `PLATFORM_PENDING`→`SUPPORTED` при імплементації, під contract-drift-gate (як ADR-0075 §6). X31 збережено: жодного runtime-import між репо. Scaled-entry — **порада** платформи; політика розміру/входу живе в trader-v3 (I7).
