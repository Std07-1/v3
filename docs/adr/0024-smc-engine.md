# ADR-0024: SMC Engine — Smart Money Concepts Computation Layer

- **Статус**: Accepted
- **Дата**: 2026-02-28
- **Автор**: Claude Opus 4.6 (Patch Master)
- **Initiative**: `smc_engine_v1`
- **Залежності**: ADR-0001 (UDS), ADR-0002 (DeriveChain), ADR-0017 (Replay), ADR-0023 (D1)

---

## 1. Контекст і проблема

### 1.1 Бізнес-контекст

Платформа має **технічно відмінну** інфраструктуру (Svelte 5 + WebSocket + FXCM live data + DeriveEngine M1→D1), але **нульову SMC-специфічність** (оцінка 3/10 — `research/leader/Untitled-4.md`). Трейдер бачить "ще один швидкий графік, на якому треба все малювати руками".

**Без SMC Engine**: 18–23 хвилини ручного аналізу на кожен setup (OB + FVG + Structure + Liquidity).  
**З SMC Engine**: 30–60 секунд — система вже знає, де зони.

**Конкурентна позиція**:

| Функція | TradingView | Paid SMC tools ($100-300/мо) | Aione v3 (поточний) | Aione v3 + SMC Engine |
|---------|-------------|------------------------------|---------------------|-----------------------|
| Auto Order Blocks | ❌ | ✅ | ❌ | ✅ |
| Auto FVG | ❌ | ✅ | ❌ | ✅ |
| Market Structure | ❌ (індикатори) | ✅ | ❌ | ✅ |
| Liquidity Levels | ❌ (вручну) | ✅ | ❌ | ✅ |
| Real-time speed | ⚠️ | ⚠️ | ✅ | ✅ |
| Replay + SMC навчання | ❌ | ❌ | ❌ | ✅ ← **унікальне** |
| Ціна | Free/Paid | $100-300/мо | 0 | 0 |

**Ключовий диференціатор**: Replay + SMC = тренажер для менторів, якого **ніхто не має**.

### 1.2 Технічний контекст

- 13 символів × 8 TFs = 104 пари (symbol, tf), кожна потребує SMC-обчислень
- DeriveEngine вже виробляє cascade M1→D1 з commit + events bus
- WS frames **вже мають** порожні масиви `zones: [], swings: [], levels: []` (ws_server.py:279-281)
- UI types.ts **вже визначає** SmcZone, SmcSwing, SmcLevel, SmcData
- Відсутній: `core/smc/` (pure logic), `runtime/smc/` (I/O обгортка), алгоритми, контракти
- Replay architecture (ADR-0017) проектується з SMC-хуком: `ReplayEngine.step()` → `SmcEngine.update()`

### 1.3 Вимоги

1. **Постійне обчислення** — при кожному commit bar (M1 close → cascade → SMC update)
2. **13 символів × 8 TFs** — масштабується горизонально, per-(symbol, tf) ізоляція
3. **Real-time WS** — зони з'являються/зникають у дельта-фреймах
4. **Replay-сумісний** — той самий SmcEngine працює в Replay Mode (ADR-0017)
5. **Mentor-friendly** — чітке відображення для навчальних сесій
6. **User-configurable** — параметри через config.json (sensitivity, layers on/off)
7. **I0 compliant** — чиста логіка у core/smc/, I/O у runtime/smc/

---

## 2. Розглянуті варіанти

### Варіант A: Frontend-only (SmcEngine у ui_v4/src/)

**Плюси**: Простіше, менше backend змін.  
**Мінуси**: Порушує I0. Дублює логіку для HTTP API та Replay. Неможливо серверні alertи. 13 символів × 8 TFs обчислювать у браузері нереалістично.  
**Вердикт**: ❌ Відхилено.

### Варіант B: Backend з UDS persistence (зберігати SMC зони в JSONL)

**Плюси**: Моментальний cold-start SMC даних.  
**Мінуси**: Порушує I1 (UDS = OHLCV тільки). Складна міграція. SMC зони — derived read-only дані, не SSOT.  
**Вердикт**: ❌ Відхилено — порушує інваріант.

### Варіант C: Backend pure + ephemeral state + Redis pub/sub ← ОБРАНО

**Плюси**: I0/I1 compliant. SMC = read-only overlay, обчислений з CandleBar. Ephemeral — відновлюється з warm bars при restart. Redis bus для events. Природна інтеграція з DeriveEngine та Replay.  
**Мінуси**: Cold-start потребує warmup (500 барів на TF = ~3s).  
**Вердикт**: ✅ Обрано.

---

## 2А. Фундаментальні архітектурні рішення

> Цей розділ фіксує **явні відповіді** на ключові питання, які визначають траєкторію SMC Engine.
> Кожне рішення — с reasoning, альтернативами, і критерієм перегляду.

### Q1: Real-time чи batch

**Рішення: Hybrid — event-driven live + batch warmup.**

| Режим | Коли | Механізм | Latency |
|-------|------|----------|---------|
| Batch (warmup) | Cold-start, symbol switch | `SmcEngine.update(bars)` — повне перерахування з N барів | ~100ms одноразово |
| Event-driven (live) | Кожен committed bar в cascade | `SmcEngine.on_bar(bar)` — O(1) інкремент | <10ms |
| Event-driven (replay) | Кожен step ReplayEngine | `SmcEngine.on_bar(bar)` — ідентичний live алгоритм | <10ms |

**Чому не чистий batch?** Затримка 1-60с між bar close і zone appearance — трейдер втрачає момент формування. Replay потребує step-by-step, а batch = повний пересчёт на кожен step.

**Чому не per-tick stream?** SMC зони формуються на **закритих барах** (confirmed candles). Per-tick = шум: зона "з'являється і зникає" 100 разів за хвилину. Preview bars (complete=false) **навмисно ігноруються** — тільки final bars.

**Rail**: `SmcRunner.on_bar_event()` відхиляє бари з `complete=False`. Лог: `SMC_SKIP_PREVIEW`.

### Q2: Звідки читаємо бари — JSONL, Redis, UDS API

**Рішення: Тільки через UDS API (warmup) + Redis event bus (live).**

```
Cold-start warmup:
  SmcRunner.start()
    → UDS.read_window(symbol, tf_s, n=lookback_bars)  # RAM → Redis → Disk fallback
    → SmcEngine.update(bars)                            # batch recompute

Live operation:
  Redis pub/sub v3_local:updates:{symbol}:{tf_s}
    → SmcRunner.on_bar_event(bar)                       # bar вже десеріалізований
    → SmcEngine.on_bar(bar)                             # incremental O(1)
```

**Чому не напряму JSONL?** Порушує I1 (UDS = єдина талія). UDS вже має RAM layer з гарячими барами, dedup, sorting, end-excl semantics. Дублювати read logic = split-brain ризик.

**Чому не напряму Redis?** Redis keys = implementation detail UDS. Формат (end-incl) може змінюватись. Direct read = coupling до `redis_keys.py`.

**Критерій перегляду**: Якщо UDS API latency >50ms для warmup — розглянути прямий Redis snapshot read з конвертацією через UDS helper.

### Q3: In-process чи окремий сервіс

**Рішення: SmcRunner як asyncio task у ws_server process (in-process).**

| Критерій | In-process ← **обрано** | Окремий процес | Окремий сервіс |
|----------|------------------------|----------------|----------------|
| Latency bar→zone | ~0ms (dict lookup) | ~5-10ms (Redis ser/de) | ~10-50ms (HTTP) |
| Memory sharing | SmcSnapshot in Python RAM | Redis pub/sub bridge | HTTP response |
| Deployment overhead | 0 | +1 процес у supervisor | +1 порт + health |
| Failure domain | ws_server down → SMC down | Independent restart | Independent |
| Scaling ceiling | ~50 символів (est.) | ~200 символів | Horizontal |
| Implementation cost | ~20 LOC glue | ~80 LOC + bridge | ~200 LOC + client |

**Reasoning**:

1. WS frame будується в ws_server → snapshot = direct dict lookup, 0 серіалізації
2. 104 пар × SmcSnapshot через Redis на кожен frame = зайвий overhead
3. Якщо ws_server впав → UI не працює → SMC без UI безглуздий
4. 13 символів далеко від ceiling ~50

**Trigger для міграції на окремий процес**:

- `on_bar()` > 50ms тричі поспіль → лог `SMC_DEGRADED_PERF` + метрика
- Більше 50 символів
- Потреба SMC API без WS (REST-only клієнти)

### Q4: Які SMC-алгоритми і в якому порядку

**Рішення: 10 алгоритмів у 3 ешелонах за пріоритетом реалізації.**

| Ешелон | Алгоритм | Trader Value | Section |
|--------|----------|-------------|---------|
| **E1 (MVP)** | Swing Detection | Фундамент: HH/HL/LH/LL → тренд | §4.1 |
| **E1** | Market Structure (BOS/CHoCH) | Тренд confirmation + reversal | §4.2 |
| **E1** | Order Blocks | Зони накопичення smart money | §4.3 |
| **E1** | Fair Value Gaps | Дисбаланс попиту/пропозиції | §4.4 |
| **E2 (Core)** | Liquidity Levels | Target для smart money sweeps | §4.5 |
| **E2** | **Premium/Discount Zones** | Фільтр якості: buy в discount, sell в premium | §4.6 |
| **E2** | **Inducement (False Breakout)** | Trap detection → вхід після sweep | §4.7 |
| **E3 (Pro)** | **Session/Killzone Awareness** | Коли торгувати (London/NY) | §4.8 |
| **E3** | **Confluence POI Engine** | Об'єднання факторів → єдиний score | §4.9 |
| **E3** | **Zone Quality Model** | Aging, freshness, decay → автоматична фільтрація | §4.10 |

**Чому не Wyckoff / Elliott / ICT Silver Bullet?** Wyckoff = окрема парадигма. Elliott = суб'єктивне, погано автоматизується. Silver Bullet = спеціалізований патерн. Ці алгоритми = Phase 3+ addons через plugin interface.

### Q5: Що робить систему НЕЗАМІННОЮ для сильного трейдера

**Відповідь**: Не кількість індикаторів, а три фактори:

1. **Confluence scoring**: Трейдер бачить 15 зон → система ранжує: "2 зони A+, 3 зони A, 10 зон B/C". Фокус на A+ скорочує аналіз з 20 хвилин до 30 секунд.
2. **Наратив**: Система пояснює **чому** зона сильна: "OB + FVG + Discount + HTF aligned + London killzone" — не просто малює прямокутник.
3. **Replay тренажер**: Ніхто на ринку не має Replay + Live SMC для 13 символів. Ментор може навчати учнів на реальній історії з zone formation в real-time.

**Детальна розробка**: див. §9А "Trader Experience Architecture" (що робить систему незамінною).

---

## 3. Рішення: Архітектура SMC Engine

### 3.1 Шари (відповідно до I0)

```
core/smc/                          ← PURE LOGIC (NO I/O)
  ├─ __init__.py
  ├─ types.py                      ← SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta
  ├─ config.py                     ← SmcConfig dataclass (parsed from config.json)
  ├─ swings.py                     ← Fractal swing detection (foundation)
  ├─ structure.py                  ← BOS, CHoCH, trend bias
  ├─ order_blocks.py               ← OB detection (bullish/bearish/breaker)
  ├─ fvg.py                        ← Fair Value Gap detection
  ├─ liquidity.py                  ← Equal Highs/Lows, PDH/PDL, PWH/PWL
  └─ engine.py                     ← SmcEngine orchestrator (per symbol+tf state)

runtime/smc/                       ← I/O WRAPPER
  ├─ __init__.py
  └─ smc_runner.py                 ← SmcRunner: subscribes bar events → SmcEngine → publish
```

### 3.2 Потік даних

```
                Live Pipeline                              Replay Pipeline
                ─────────────                              ───────────────
FXCM tick → M1 bar                                  JSONL → DiskReader → M1 bar
         │                                                            │
         ▼                                                            ▼
   DeriveEngine.on_bar(M1)                            ReplayEngine.step()
         │ cascade: M3→M5→M15→M30→H1→H4→D1                   │ same cascade
         │                                                     │
         ▼                                                     ▼
   UDS.commit_final_bar(bar)                          (no UDS commit)
         │                                                     │
         ├─── Redis pub/sub ──────────┐                        │
         │                            ▼                        │
         │                    SmcRunner.on_bar_event()         │
         │                            │                        │
         │                            ▼                        ▼
         │                    SmcEngine.on_bar(bar)    SmcEngine.on_bar(bar)
         │                            │                        │
         │                            ▼                        ▼
         │                    SmcDelta {new/mitigated/updated} SmcDelta
         │                            │                        │
         │                            ▼                        ▼
         │                    Redis pub: smc:delta:{sym}:{tf}  WS frame
         │                            │
         │                            ▼
         └─────────────────► WS _build_delta_frame()
                              zones, swings, levels populated
                                      │
                                      ▼
                                    UI render
```

### 3.3 SmcEngine — чиста логіка (core/smc/engine.py)

```python
class SmcEngine:
    """Orchestrator: тримає per-(symbol, tf) стан і координує детекцію.
    
    Pure logic — без I/O. Приймає бари, повертає SMC дані.
    Може використовуватись у Live, Replay, тестах, CLI діагностиці.
    """
    
    def __init__(self, config: SmcConfig) -> None:
        """config — параметри з config.json:smc (sensitivity, lookback, layers)."""
        self._config = config
        self._states: Dict[Tuple[str, int], _TfState] = {}  # (symbol, tf_s) → state
    
    def update(self, symbol: str, tf_s: int, bars: List[CandleBar]) -> SmcSnapshot:
        """Full recompute з N барів. Для cold-start/warmup."""
        
    def on_bar(self, bar: CandleBar) -> SmcDelta:
        """Incremental update при новому закритому барі.
        
        Повертає SmcDelta: які зони з'явились, зникли, оновились.
        Це O(1) відносно lookback — не перераховує все.
        """
    
    def get_snapshot(self, symbol: str, tf_s: int) -> SmcSnapshot:
        """Поточний повний стан для (symbol, tf). Для full WS frame."""
    
    def get_htf_bias(self, symbol: str, tf_s: int) -> Optional[TrendBias]:
        """HTF trend bias для cross-TF alignment.
        
        Використання: M15 entry alignment → запитує H4 bias.
        """
    
    def reset(self, symbol: str, tf_s: int) -> None:
        """Скидає стан для (symbol, tf). Для symbol switch / config change."""
```

### 3.4 SmcRunner — I/O обгортка (runtime/smc/smc_runner.py)

```python
class SmcRunner:
    """Підписується на bar events (Redis bus), запускає SmcEngine, публікує дельти.
    
    Один SmcRunner на процес. SmcEngine всередині.
    Lifecycle: start() → on_bar_event() loop → stop()
    """
    
    def __init__(self, config: Dict, smc_engine: SmcEngine) -> None:
        """config — повний config.json, smc_engine — core/smc інстанс."""
    
    def start(self, uds_reader) -> None:
        """Warmup: зчитує warm bars з UDS → SmcEngine.update() для всіх (symbol, tf).
        Підписується на Redis канал v3_local:updates:* для on_bar_event().
        """
    
    def on_bar_event(self, bar: CandleBar) -> None:
        """Callback від Redis pub/sub. Визначає (symbol, tf), делегує SmcEngine.on_bar().
        Публікує SmcDelta в Redis канал v3_local:smc:delta:{symbol}:{tf_s}.
        """
    
    def get_snapshot(self, symbol: str, tf_s: int) -> SmcSnapshot:
        """Для HTTP API та WS full frame."""
```

---

## 4. SMC Concepts та алгоритми

### 4.1 Swing Detection (фундамент усіх обчислень)

**Алгоритм**: Fractal pivot з configurable period (за замовчуванням 5).

```python
def detect_swings(bars: List[CandleBar], period: int = 5) -> List[SmcSwing]:
    """
    Swing High: bar[i].h > max(bar[i-period:i].h) AND bar[i].h > max(bar[i+1:i+period+1].h)
    Swing Low:  bar[i].low < min(bar[i-period:i].low) AND bar[i].low < min(bar[i+1:i+period+1].low)
    
    Потребує мінімум 2*period+1 барів.
    Останні `period` барів — unconfirmed (майбутнє ще не відоме).
    
    Incremental: новий бар може:
    1. Підтвердити pending swing (period барів пройшло)
    2. Інвалідувати pending swing (новий extremum)
    3. Не змінити нічого
    """
```

**Складність**: O(n) при full compute, O(period) при incremental.

### 4.2 Market Structure — BOS / CHoCH

```
Визначення тренду:
  Bullish: послідовність HH + HL (Higher High, Higher Low)
  Bearish: послідовність LL + LH (Lower Low, Lower High)

BOS (Break of Structure) — продовження тренду:
  Bullish BOS: ціна закривається вище попереднього Swing High, тренд вже bullish
  Bearish BOS: ціна закривається нижче попереднього Swing Low, тренд вже bearish

CHoCH (Change of Character) — розворот:
  Bullish CHoCH: ціна закривається вище попереднього Swing High, тренд був bearish → reversal
  Bearish CHoCH: ціна закривається нижче попереднього Swing Low, тренд був bullish → reversal

Incremental:
  На кожному новому Swing → перевірити BOS/CHoCH умову.
  На кожному новому барі → перевірити чи поточний бар ламає structure.
```

**Output**: `SmcSwing(kind="bos_bull"|"bos_bear"|"choch_bull"|"choch_bear", price, time_ms, confirmed)`

### 4.3 Order Blocks (OB)

```
Bullish OB:
  1. Знайти BOS/CHoCH подію (bullish)
  2. Відслідкувати імпульс (рух, що створив BOS)
  3. Знайти останню bearish свічку (close < open) ДО імпульсу
  4. Зона = [candle.low, candle.high]
  5. Valідність: імпульс > min_impulse_atr_mult × ATR(period)

Bearish OB:
  Дзеркально: остання bullish свічка перед bearish BOS/CHoCH.

Lifecycle зони:
  active  → ціна ще не торкалась зони
  tested  → ціна торкнулась зони, але не пробила (bounce)
  mitigated → ціна закрилася за межами зони (протилежний бік)
  breaker → після mitigation зона стає breaker block (протилежний bias)

Incremental:
  Новий бар → Перевірити всі active/tested OB → чи mitigated?
  Новий BOS/CHoCH → Знайти нову OB.
```

**Output**: `SmcZone(kind="ob_bull"|"ob_bear", status="active"|"tested"|"mitigated"|"breaker")`

### 4.4 Fair Value Gaps (FVG)

```
Bullish FVG (3-candle pattern):
  bar[0].high < bar[2].low → gap between candle 0 top and candle 2 bottom
  Zone: [bar[0].high, bar[2].low]
  Умова: middle candle (bar[1]) = impulse candle (strong body)

Bearish FVG:
  bar[0].low > bar[2].high → gap
  Zone: [bar[2].high, bar[0].low]

Lifecycle:
  active → gap не заповнений
  partially_filled → ціна увійшла в зону але не заповнила повністю
  filled → ціна повністю закрила gap (close за протилежний бік)

Incremental:
  Новий бар → Перевірити останні 3 бари → нова FVG?
  Новий бар → Перевірити all active FVGs → filled?
```

**Output**: `SmcZone(kind="fvg_bull"|"fvg_bear", status="active"|"partially_filled"|"filled")`

### 4.5 Liquidity Levels

```
Equal Highs (sell-side liquidity):
  2+ Swing Highs з ціною в межах tolerance → рівень ліквідності
  Трейдери з SL над цими рівнями = sell-side liquidity
  
Equal Lows (buy-side liquidity):
  2+ Swing Lows з ціною в межах tolerance

PDH/PDL (Previous Day High/Low):
  З D1 барів: yesterday.h / yesterday.low
  (потребує cross-TF access — SmcEngine зчитує D1 snapshot)

PWH/PWL (Previous Week High/Low):
  З D1 барів: max(last 5 D1).h / min(last 5 D1).low

Incremental:
  Новий Swing → перевірити equal highs/lows.
  Нова D1 bar → оновити PDH/PDL/PWH/PWL.
```

**Output**: `SmcLevel(kind="eq_highs"|"eq_lows"|"pdh"|"pdl"|"pwh"|"pwl", price, touches)`

### 4.6 Premium/Discount Zones

**Concept**: Розділити діапазон від останнього significant Swing High до Swing Low на дві зони. Smart money купує в Discount (нижні 50%), продає в Premium (верхні 50%). Equilibrium (50%) = "справедлива ціна".

```
Calculation:
  range_high  = last significant Swing High (confirmed, period ≥ 5)
  range_low   = last significant Swing Low  (confirmed, period ≥ 5)
  equilibrium = (range_high + range_low) / 2

  Premium zone: price > equilibrium → sell setups мають перевагу
  Discount zone: price < equilibrium → buy setups мають перевагу

Output:
  SmcZone(kind="premium", high=range_high, low=equilibrium)
  SmcZone(kind="discount", high=equilibrium, low=range_low)

Incremental:
  Новий confirmed Swing → перерахувати range → оновити P/D зони
  BOS/CHoCH → range reset (новий тренд = новий range)

Quality filter (integration з 4.3/4.4):
  OB bullish + price in Discount → quality ×1.5 (HIGH probability buy)
  OB bullish + price in Premium → quality ×0.5 (LOW probability buy → ігнорувати)
  OB bearish + price in Premium → quality ×1.5 (HIGH probability sell)
```

**Для трейдера**: "Система показує: OB bullish у discount zone → цей setup має 75% edge. OB bullish у premium → 35% → пропустити або шукати sell." Це single filter який відсіче ~50% low-quality entries.

### 4.7 Inducement (False Breakout Trap)

**Concept**: Inducement — minor liquidity sweep, що trapає retail трейдерів. Smart money навмисно "виконують" minor swing highs/lows щоб зібрати стопи, потім розвертають ціну.

```
Detection:
  1. Визначити minor Swing High/Low (period=3, менший за основний period=5)
  2. Ціна пробиває minor swing: wick виходить за межі, але close повертається
  3. Подальший рух в протилежному напрямку > 0.5 × ATR → inducement confirmed

Pattern:
  Minor SH at 2860.00
  Bar: high=2861.20 (breaks above), close=2858.50 (повернулась)
  Next bars: close=2855.00 → inducement confirmed!
  → retail buy стопи зібрані, smart money починає sell

  SmcSwing(kind="inducement_bull"|"inducement_bear", price, confirmed=bool)

Incremental:
  Новий бар → перевірити pending inducements (minor break without follow-through)
  Confirmation: 2-3 бари після break showing reversal > 0.5 ATR

Quality signal:
  Inducement NEAR active OB → entry якість ↑↑ (trap завершено, реальний рух починається)
  Inducement БЕЗ OB → інформаційний (не entry signal)
```

**Для трейдера**: "Ціна зняла ліквідність над 2860 (inducement ✓), тепер тестує OB bearish — вхід з подвійним підтвердженням. Retail trapped, smart money in control."

### 4.8 Session/Killzone Awareness

**Concept**: Зона, сформована під час London Open або NY Session killzone, має вищу вірогідність reaction, ніж зона під час Asian grind. Smart money найбільш активні в killzones.

```
Sessions (UTC, configurable через config.json:smc.sessions):
  asia:     00:00 – 06:00  (low volatility, range formation)
  london:   07:00 – 16:00  (killzone: 07:00–10:00)
  ny:       12:00 – 21:00  (killzone: 12:00–15:00)
  overlap:  12:00 – 16:00  (London+NY = max volatility)

Symbol-specific relevance (config.json:smc.sessions.symbol_overrides):
  XAU/USD:  London killzone (primary), NY killzone (secondary)
  NAS100:   NY killzone (primary), London (pre-market)
  GER30:    Frankfurt open 07:00-09:00 (primary)
  USD/JPY:  Asia (primary), NY (secondary)

Implementation:
  1. Кожен бар tagged: session, in_killzone: bool
  2. Кожна зона tagged: formed_session, formed_in_killzone
  3. Quality modifier: killzone zone → strength × config.killzone_boost (default 1.3)
  4. UI filter: toggle "Show only killzone zones" (reduces noise 60-70%)

Incremental:
  on_bar() → determine session from bar.open_time_ms UTC hour → inheritance to zones
```

**Для трейдера**: "OB bearish на XAU/USD, сформований 08:15 UTC (London killzone) з impulse 2.5 ATR → HIGH PRIORITY. Зона з Asian session 03:30 UTC → low priority, можливо range noise."

### 4.9 Confluence POI Engine (Point of Interest)

**Concept**: Найцінніша функція. POI — цінова область де перетинаються декілька SMC факторів. Чим більше confluence → тим вища probability reaction. **Це те, що перетворює "набір зон" на "торгову систему".**

```
Confluence Scoring Matrix:
  Factor                          Points    Condition
  ─────────────────────────────   ──────    ─────────
  Order Block at zone             +2        Active OB overlaps price range
  Fair Value Gap overlap          +2        FVG zone overlaps OB ≥ 50%
  Liquidity level near zone       +1        EQ highs/lows within 0.5 ATR of zone edge
  Premium/Discount alignment      +1        Buy in Discount / Sell in Premium
  HTF bias alignment              +2        get_htf_bias() confirms direction
  Killzone formation              +1        Zone formed during session killzone
  Inducement near zone            +1        Inducement within 2 ATR before zone test
  Fresh zone (untested)           +1        zone.status == "active", test_count == 0
  ─────────────────────────────
  MAX SCORE                       11

Grade Classification:
  score ≥ 8:  Grade "A+" → highest probability, primary focus
  score ≥ 6:  Grade "A"  → high probability, secondary focus
  score ≥ 4:  Grade "B"  → moderate, informational
  score <  4:  Grade "C"  → low, display only on demand (UI toggle)

POI Detection Algorithm:
  1. For each active OB zone:
     a. Scan FVGs → overlap ≥ 50%? → +2
     b. Scan Liquidity levels → within 0.5 ATR of zone edges? → +1
     c. Check Premium/Discount → correct zone? → +1
     d. Query get_htf_bias() → aligned? → +2
     e. Check formed_in_killzone → +1
     f. Scan nearby inducements → +1
     g. Check freshness → +1
  2. Merge overlapping/adjacent zones → unified POI with combined score
  3. Rank POIs by score descending
  4. Cap: max_poi_per_tf = config value (default 5)

Output:
  ConfluencePOI(
    id="poi_XAU/USD_3600_1770302400000",
    zones=[ob_zone_id, fvg_zone_id],        # constituent elements
    price_high=2852.50, price_low=2848.20,   # merged range
    score=9, grade="A+",
    direction="bearish",
    factors=["ob_bear", "fvg_bear", "premium", "htf_bear", "killzone", 
             "inducement", "fresh"],
    narrative="Bearish OB+FVG confluence in premium zone, aligned with D1 
              bearish bias, formed during London killzone, fresh untested"
  )

Incremental:
  on_bar() → після update всіх layers → recalculate POI scores
  Зміна в будь-якому layer (new OB, FVG filled, etc.) → POI rescore
```

**Для трейдера**: "З 15 активних зон система виділяє 2 POI grade A+ та 3 grade A. Замість 20 хвилин скролінгу → 30 секунд фокусу на найважливішому."

### 4.10 Zone Quality Model (Aging, Freshness, Decay)

**Concept**: Не всі зони живуть вічно. Свіжа зона > стара зона. Zone quality = автоматичний фільтр, який прибирає noise з чарту.

```
Quality Formula:
  zone.quality = base_strength × freshness_factor × test_penalty × htf_bonus

Components:
  1. Base Strength (impulse/ATR at creation):
     impulse / ATR > 3.0:   base = 1.0 (explosive move)
     impulse / ATR > 2.0:   base = 0.8
     impulse / ATR > 1.5:   base = 0.6
     impulse / ATR < 1.5:   base = 0.3 (weak, likely noise)

  2. Freshness Factor (bars since creation):
     0-50 bars:    factor = 1.0 (fresh)
     50-200 bars:  factor = 0.8 (aging)
     200-500 bars: factor = 0.5 (stale)
     500+ bars:    factor = 0.2 (expired from active display)

  3. Test Penalty (times price touched zone):
     untested:    penalty = 1.0 (virginal — highest value)
     tested 1×:   penalty = 0.7 (bounced once — still valid)
     tested 2×:   penalty = 0.4 (weakening)
     tested 3×+:  penalty = 0.1 (exhausted — likely fails next touch)

  4. HTF Bonus (origin timeframe):
     D1 zone:     decay_rate × 0.3 (MUCH slower decay — D1 structures last)
     H4 zone:     decay_rate × 0.5
     H1 zone:     decay_rate × 1.0 (baseline)
     M15/M5:      decay_rate × 2.0 (fast decay — LTF noise)

Lifecycle:
  quality ≥ 0.3:  ACTIVE — display normally
  quality 0.1-0.3: FADING — display with reduced opacity (α = quality)
  quality < 0.1:  EXPIRED — hide from chart, keep in memory for statistics

Incremental:
  on_bar() → age ALL zones → recalculate quality → transition status
  Lifecycle events: ZONE_CREATED, ZONE_TESTED, ZONE_FADING, ZONE_EXPIRED
  Each transition → SmcDelta event → UI update
```

**Для трейдера**: "H4 OB з quality 0.92 (свіжий, explosive impulse, untested) підсвічений яскраво. M15 OB з quality 0.25 (200 барів тому, тестований 2×) → ледь видимий. Chart clean = mind clean."

---

## 5. Контракт smc_v1

### 5.1 Core Types (core/smc/types.py)

```python
from __future__ import annotations
import dataclasses
from typing import Any, Dict, List, Optional, Tuple

# ── Zone Kinds ──
ZONE_KINDS = frozenset({
    "ob_bull", "ob_bear",         # Order Blocks (§4.3)
    "fvg_bull", "fvg_bear",       # Fair Value Gaps (§4.4)
    "premium", "discount",        # Premium/Discount Zones (§4.6)
})
ZONE_STATUSES = frozenset({"active", "tested", "mitigated", "breaker", 
                            "partially_filled", "filled", "fading", "expired"})

# ── Swing Kinds ──
SWING_KINDS = frozenset({
    "hh", "hl", "lh", "ll",                                    # Basic swings
    "bos_bull", "bos_bear", "choch_bull", "choch_bear",        # Structure (§4.2)
    "inducement_bull", "inducement_bear",                       # Inducement (§4.7)
})

# ── Level Kinds ──
LEVEL_KINDS = frozenset({"eq_highs", "eq_lows", "pdh", "pdl", "pwh", "pwl"})

# ── POI Grades ──
POI_GRADES = frozenset({"A+", "A", "B", "C"})                  # Confluence POI (§4.9)


@dataclasses.dataclass(frozen=True)
class SmcZone:
    """Order Block або Fair Value Gap."""
    id: str              # Deterministic: "{kind}_{symbol}_{tf_s}_{anchor_ms}"
    symbol: str
    tf_s: int
    kind: str            # ZONE_KINDS
    start_ms: int        # Left edge — open_time_ms of anchor candle
    end_ms: Optional[int]  # Right edge (extends until mitigated); None = still active
    high: float          # Zone top
    low: float           # Zone bottom
    status: str          # ZONE_STATUSES
    strength: float      # 0.0–1.0, impulse magnitude / ATR
    anchor_bar_ms: int   # The candle that created this zone
    
    def to_wire(self) -> Dict[str, Any]:
        """Серіалізація для WS/HTTP (wire format = ui_v4 SmcZone type)."""
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "high": self.high,
            "low": self.low,
            "kind": self.kind,       # UI map: ob → "ob", fvg → "fvg" (prefix before _)
            "status": self.status,
            "strength": self.strength,
        }


@dataclasses.dataclass(frozen=True)
class SmcSwing:
    """Swing point або Structure event (BOS/CHoCH)."""
    id: str              # "{kind}_{symbol}_{tf_s}_{time_ms}"
    symbol: str
    tf_s: int
    kind: str            # SWING_KINDS
    price: float
    time_ms: int
    confirmed: bool      # True after `period` bars passed
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "a": {"t": self.time_ms, "p": self.price},
            "b": {"t": self.time_ms, "p": self.price},  # For BOS/CHoCH: b = break point
            "label": self.kind.upper().replace("_", " "),
        }


@dataclasses.dataclass(frozen=True)
class SmcLevel:
    """Liquidity level (Equal Highs/Lows, PDH/PDL, PWH/PWL)."""
    id: str              # "{kind}_{symbol}_{tf_s}_{price_rounded}"
    symbol: str
    tf_s: int
    kind: str            # LEVEL_KINDS
    price: float
    time_ms: Optional[int]  # Коли утворився
    touches: int         # Кількість дотиків до рівня
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "price": self.price,
            "t_ms": self.time_ms,
            "color": None,  # UI вирішує по kind
        }


@dataclasses.dataclass(frozen=True)
class SmcSnapshot:
    """Повний стан SMC для (symbol, tf) — для full frame / HTTP."""
    symbol: str
    tf_s: int
    zones: List[SmcZone]
    swings: List[SmcSwing]
    levels: List[SmcLevel]
    trend_bias: Optional[str]  # "bullish" | "bearish" | "neutral"
    last_bos_ms: Optional[int]
    last_choch_ms: Optional[int]
    computed_at_ms: int
    bar_count: int       # Кількість вхідних барів
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "zones": [z.to_wire() for z in self.zones],
            "swings": [s.to_wire() for s in self.swings],
            "levels": [l.to_wire() for l in self.levels],
        }


@dataclasses.dataclass(frozen=True)
class SmcDelta:
    """Інкрементальна зміна після on_bar() — для delta frame."""
    symbol: str
    tf_s: int
    bar_open_ms: int     # Який бар спричинив зміну
    new_zones: List[SmcZone]
    mitigated_zones: List[str]   # IDs зон що змінили status
    updated_zones: List[SmcZone] # Зони з оновленим status
    new_swings: List[SmcSwing]
    new_levels: List[SmcLevel]
    removed_levels: List[str]    # IDs видалених рівнів
    trend_bias: Optional[str]
    
    @property
    def has_changes(self) -> bool:
        return bool(self.new_zones or self.mitigated_zones or self.updated_zones
                     or self.new_swings or self.new_levels or self.removed_levels)
```

### 5.2 Wire Format (WS / HTTP)

**WS full frame** (доповнення існуючого формату):

```json
{
  "type": "full",
  "schema_v": "ui_v4_v2",
  "data": {
    "candles": [...],
    "zones": [
      {
        "id": "ob_bull_XAU/USD_3600_1770302400000",
        "start_ms": 1770302400000,
        "end_ms": null,
        "high": 2850.50,
        "low": 2848.20,
        "kind": "ob_bull",
        "status": "active",
        "strength": 0.82
      }
    ],
    "swings": [
      {
        "id": "bos_bull_XAU/USD_3600_1770309600000",
        "a": {"t": 1770309600000, "p": 2852.30},
        "b": {"t": 1770309600000, "p": 2852.30},
        "label": "BOS BULL"
      }
    ],
    "levels": [
      {
        "id": "eq_highs_XAU/USD_3600_285050",
        "price": 2850.50,
        "t_ms": 1770302400000,
        "color": null
      }
    ]
  }
}
```

**WS delta frame** (доповнення):

```json
{
  "type": "delta",
  "data": {
    "upsert": [...],
    "smc_delta": {
      "new_zones": [...],
      "mitigated_zone_ids": ["ob_bear_XAU/USD_900_1770288000000"],
      "updated_zones": [...],
      "new_swings": [...],
      "new_levels": [...],
      "removed_level_ids": [],
      "trend_bias": "bullish"
    }
  }
}
```

**HTTP endpoint** (новий):

```
GET /api/smc?symbol=XAU/USD&tf_s=3600
→ SmcSnapshot.to_wire() + meta
```

### 5.3 Config SSOT (config.json)

```json
{
  "smc": {
    "enabled": true,
    "lookback_bars": 500,
    "swing_period": 5,
    "ob": {
      "enabled": true,
      "min_impulse_atr_mult": 1.5,
      "atr_period": 14,
      "max_active_per_side": 5,
      "track_breakers": true
    },
    "fvg": {
      "enabled": true,
      "min_gap_atr_mult": 0.1,
      "max_active": 10
    },
    "structure": {
      "enabled": true,
      "confirmation_bars": 1
    },
    "liquidity": {
      "enabled": true,
      "eq_tolerance_pips": {
        "XAU/USD": 0.50,
        "XAG/USD": 0.05,
        "NAS100": 5.0,
        "SPX500": 2.0,
        "default": 0.0003
      },
      "min_touches": 2,
      "pdh_pdl_enabled": true,
      "pwh_pwl_enabled": true
    },
    "max_zones_per_tf": 30,
    "premium_discount": {
      "enabled": true
    },
    "inducement": {
      "enabled": true,
      "minor_swing_period": 3,
      "confirmation_atr_mult": 0.5,
      "confirmation_bars": 3
    },
    "sessions": {
      "enabled": true,
      "killzone_boost": 1.3,
      "definitions": [
        {"name": "asia",    "start_utc": "00:00", "end_utc": "06:00", "kz_start": null, "kz_end": null},
        {"name": "london",  "start_utc": "07:00", "end_utc": "16:00", "kz_start": "07:00", "kz_end": "10:00"},
        {"name": "ny",      "start_utc": "12:00", "end_utc": "21:00", "kz_start": "12:00", "kz_end": "15:00"}
      ],
      "symbol_overrides": {
        "XAU/USD": {"primary": "london", "secondary": "ny"},
        "NAS100": {"primary": "ny"},
        "GER30": {"primary": "london"},
        "USD/JPY": {"primary": "asia", "secondary": "ny"}
      }
    },
    "confluence": {
      "enabled": true,
      "max_poi_per_tf": 5,
      "grade_thresholds": {"A+": 8, "A": 6, "B": 4}
    },
    "quality": {
      "fresh_bars": 50,
      "aging_bars": 200,
      "stale_bars": 500,
      "test_penalties": [1.0, 0.7, 0.4, 0.1],
      "htf_decay_multipliers": {"86400": 0.3, "14400": 0.5, "3600": 1.0, "default": 2.0},
      "min_display_quality": 0.1,
      "fading_threshold": 0.3
    },
    "performance": {
      "max_compute_ms": 10,
      "log_slow_threshold_ms": 5
    }
  }
}
```

Кожен `enabled` прапорець — per-layer toggle. UI надсилає `overlay_toggle` action для відображення.

---

## 6. Інтеграція

### 6.1 Live Pipeline — SmcRunner у ws_server process

**Рішення (§2А Q3)**: SmcRunner живе **в тому ж процесі** що й ws_server — 0 серіалізації при frame build.

**Subscription**: SmcRunner є **ще одним consumer** того ж Redis updates bus (`v3_local:updates:*`), який ws_server вже використовує для delta loop. **Не потрібен** окремий канал `smc:delta:{sym}:{tf}`. SMC delta вбудовується прямо у WS frame.

```
ws_server process:
  ├─ aiohttp WS сервер
  │   └─ delta_loop: Redis subscriber v3_local:updates:* (вже існує)
  ├─ SmcRunner (asyncio task, той же event loop)
  │   ├─ on_bar_event(bar): callback від delta_loop при новому барі
  │   ├─ SmcEngine (core/smc/, pure)
  │   └─ In-memory SmcSnapshot per (symbol, tf)
  └─ _build_full_frame() / _build_delta_frame()
       └─ reads SmcRunner.get_snapshot(symbol, tf_s) — direct dict lookup
```

**Чому не окремий Redis канал?** SmcRunner in-process → бар вже десеріалізований у delta loop → direct Python callback, 0 overhead. Окремий канал `smc:delta:*` = зайвий pub/sub roundtrip для даних що вже в пам'яті.

**Warmup flow**:

```python
# При старті ws_server:
smc_engine = SmcEngine(smc_config)              # core, pure
smc_runner = SmcRunner(config, smc_engine)
await smc_runner.warmup(uds)                     # UDS.read_window() → SmcEngine.update()
# Далі delta_loop при кожному новому барі:
async def on_bar_committed(bar: CandleBar):
    smc_runner.on_bar(bar)                       # SmcEngine.on_bar() → SmcDelta cached
```

### 6.1a types.ts Contract Expansion — Breaking Change Guard

**Поточний стан** (ui_v4/src/lib/types.ts:23-49):

```typescript
export type SmcZone = { kind: 'fvg' | 'ob' | 'liquidity'; /* ... */ }
```

**Проблема**: ADR вводить `'ob_bull' | 'ob_bear' | 'fvg_bull' | 'fvg_bear' | 'premium' | 'discount'`. Поточний `OverlayRenderer.zoneColor()` робить `if (kind === 'ob')` — **зламається** при `'ob_bull'`.

**Рішення — backward-compatible prefix matching** (3 LOC у UI):

```typescript
// OverlayRenderer.ts — zoneColor():
function zoneColor(kind: string): string {
  if (kind.startsWith('ob'))        return '#ff8c00';  // orange (OB)
  if (kind.startsWith('fvg'))       return '#00cc66';  // green (FVG)
  if (kind === 'premium')           return '#cc3333';  // red tint
  if (kind === 'discount')          return '#3399cc';  // blue tint
  if (kind.startsWith('liquidity')) return '#9966ff';  // purple
  return '#888888';                                     // fallback
}
```

**Wire format contract**: Backend завжди надсилає `kind` з ADR-0024 vocabulary (§5.1 ZONE_KINDS). UI обробляє через `startsWith()` — forward-compatible з майбутніми sub-kinds.

**Порядок deploy**: S5 (types.ts expand) робиться **одночасно** з S4 (SmcRunner integration) — один slice. UI не може отримати new kinds без backend, backend не надсилає without config enabled.

### 6.2 WS Server Integration

**Зміни у ws_server.py** (~30 LOC):

```python
# В _build_full_frame():
smc = self._smc_runner.get_snapshot(session.symbol, session.tf_s)
frame["data"]["zones"] = [z.to_wire() for z in smc.zones]    # замість []
frame["data"]["swings"] = [s.to_wire() for s in smc.swings]  # замість []
frame["data"]["levels"] = [l.to_wire() for l in smc.levels]  # замість []

# В _build_delta_frame():
delta = self._smc_runner.last_delta(session.symbol, session.tf_s)
if delta and delta.has_changes:
    frame["data"]["smc_delta"] = {
        "new_zones": [z.to_wire() for z in delta.new_zones],
        "mitigated_zone_ids": delta.mitigated_zones,
        "updated_zones": [z.to_wire() for z in delta.updated_zones],
        "new_swings": [s.to_wire() for s in delta.new_swings],
        "new_levels": [l.to_wire() for l in delta.new_levels],
        "removed_level_ids": delta.removed_levels,
        "trend_bias": delta.trend_bias,
    }
```

### 6.3 HTTP API (ui_chart_v3/server.py)

Новий endpoint `/api/smc`:

```python
@app.route("/api/smc")
def api_smc():
    symbol = request.args.get("symbol", "XAU/USD")
    tf_s = int(request.args.get("tf_s", 3600))
    snap = smc_runner.get_snapshot(symbol, tf_s)
    return jsonify({"ok": True, **snap.to_wire(), "trend_bias": snap.trend_bias})
```

### 6.4 Replay Integration (ADR-0017)

```python
# runtime/replay/replay_engine.py (Phase 3 of ADR-0017)
class ReplayEngine:
    def __init__(self, ...):
        self._smc = SmcEngine(smc_config)  # standalone, не SmcRunner
    
    def step(self) -> ReplayFrame:
        bar = self._next_bar()
        derived = self._derive_engine.on_bar(bar)
        
        # SMC update для кожного derived бару
        for b in [bar] + derived:
            self._smc.on_bar(b)
        
        snapshot = self._smc.get_snapshot(self._symbol, self._tf_s)
        return ReplayFrame(candles=..., smc=snapshot.to_wire())
```

### 6.5 Mentor Use Case — Multi-TF Analysis

```
Ментор показує учню XAU/USD:

1. D1 chart: SmcEngine.get_htf_bias("XAU/USD", 86400) → "bearish"
   └─ D1 BOS bearish at 2880, OB bearish at [2890, 2895]
   
2. H4 chart: SmcEngine.get_snapshot("XAU/USD", 14400)
   └─ H4 OB bearish at [2865, 2870], FVG bearish at [2862, 2866]
   └─ "Бачиш? HTF bias bearish, H4 підтверджує зону пропозиції"

3. M15 chart: SmcEngine.get_snapshot("XAU/USD", 900)
   └─ M15 CHoCH bearish at 2863 (confirms H4 OB reaction)
   └─ SmcEngine.get_htf_bias("XAU/USD", 14400) → "bearish" (alignment ✓)
   └─ "Ось entry — M15 CHoCH підтверджує H4 bearish OB"

4. Risk Calculator (UI):
   └─ Entry: 2863.00 (M15 CHoCH level)
   └─ SL: 2870.50 (above H4 OB high + buffer)
   └─ TP1: 2850.00 (next liquidity: equal lows)
   └─ R:R = 1:1.7
```

**Cross-TF bias query** (`get_htf_bias`) дозволяє LTF entry шукати alignment з HTF structure.

---

## 7. Performance Budget

### 7.1 Обчислювальні обмеження

| Операція | Бюджет | Обґрунтування |
|----------|--------|---------------|
| `SmcEngine.on_bar()` per (symbol, tf) | < 10ms | WS delta poll = 1.0s, 104 пар |
| `SmcEngine.update()` full (500 bars) | < 100ms | Warm-up при cold-start |
| Всі (symbol, tf) один цикл M1 close | < 500ms | 13 × 8 = 104, але cascade впорядкований |
| `SmcSnapshot.to_wire()` | < 1ms | JSON серіалізація overlay |
| Total latency (bar close → UI zone appear) | < 2s | Включає derive cascade + SMC + WS |

### 7.2 Memory Budget

| Компонент | Розмір | Обґрунтування |
|-----------|--------|---------------|
| Per-(symbol, tf) state | ~50 KB | 500 bars cache + active zones + swings |
| 104 пар (13 sym × 8 tf) | ~5 MB | 104 × 50 KB |
| Wire payload (full frame) | < 10 KB | 30 zones + 50 swings + 20 levels |
| Wire payload (delta) | < 1 KB | 1-3 changes per bar |

### 7.3 Rail: Slow Computation Guard

```python
# core/smc/engine.py — вбудований rail
def on_bar(self, bar: CandleBar) -> SmcDelta:
    t0 = _perf_counter_ms()
    delta = self._compute(bar)
    elapsed = _perf_counter_ms() - t0
    if elapsed > self._config.max_compute_ms:
        _log.warning("SMC_SLOW sym=%s tf=%d elapsed_ms=%.1f budget=%d",
                     bar.symbol, bar.tf_s, elapsed, self._config.max_compute_ms)
    return delta
```

---

## 8. Multi-TF Alignment Strategy

### 8.1 Проблема

SMC-трейдер потребує **HTF bias для LTF entry**:

- D1/H4 визначають напрямок (supply/demand зони)
- M15/M5 визначають точку входу (CHoCH/BOS підтвердження)
- Без alignment: LTF signal може бути проти HTF тренду = low-probability trade

### 8.2 Рішення: Shared SmcEngine

SmcEngine тримає стан **всіх TFs** для одного символу в пам'яті. Cross-TF query — це просто зчитування з іншого `_TfState`:

```python
def get_htf_bias(self, symbol: str, tf_s: int) -> Optional[TrendBias]:
    """Повертає trend bias з найближчого HTF.
    
    Ієрархія: D1 → H4 → H1 → M30 → M15 → M5 → M3
    Для tf_s=900 (M15) → шукає H4 (14400) або H1 (3600).
    """
    htf_order = [86400, 14400, 3600, 1800, 900, 300, 180]
    for htf in htf_order:
        if htf > tf_s:
            state = self._states.get((symbol, htf))
            if state and state.trend_bias:
                return state.trend_bias
    return None
```

### 8.3 Alignment Score (Phase 2+)

```python
@dataclasses.dataclass(frozen=True)
class AlignmentScore:
    """Multi-TF alignment quality для entry filtering."""
    htf_bias: str        # bullish/bearish/neutral
    ltf_signal: str      # bos/choch/ob_test
    aligned: bool        # HTF bias == LTF signal direction
    score: float         # 0-1, confluence count
    htf_tf_s: int        # reference HTF used
```

Це дозволяє UI показувати: `"H4 ↘ + M15 CHoCH Bear → ALIGNED ✓ (0.85)"`.

---

## 9. Mentor/Replay: Training Simulator

### 9.1 Value Proposition

| Для кого | Що отримує | Як |
|----------|------------|-----|
| **Ментор** | Показує учням як зони формуються в реальному часі | Replay + пауза на ключових моментах |
| **Учень** | Бачить SMC аналіз розгортатися крок за кроком | Replay на historicals з різною швидкістю |
| **Трейдер** | Тренує pattern recognition на минулих даних | Solo replay з SMC overlay |
| **Спільнота** | Безкоштовний SMC аналіз 13 символів 24/7 | Live dashboard з auto-zones |

### 9.2 Replay + SMC Flow

```
Lesson: "XAU/USD crash 2026-01-15"

Mentor:
  1. Start replay: symbol=XAU/USD, date=2026-01-15, speed=10x
  2. Bars play: M1 → cascade → SMC zones appear on chart
  3. Pause at 09:15 UTC:
     → "See? D1 OB bearish formed here" (zone highlighted)
  4. Resume play:
     → H4 FVG appears at 09:45 (zone appears in real-time)
  5. Pause at 10:30:
     → "M15 CHoCH confirms the bearish thesis"
     → Click on OB → Risk Calculator shows Entry/SL/TP
  6. Resume: price drops to TP
     → "3.8 R:R trade, aligned with HTF bias"

Student sees:
  - Zones appearing/disappearing as bars form
  - Structure labels (BOS/CHoCH) on chart
  - Alignment indicator (HTF bias + LTF signal)
  - Risk Calculator (what the entry would have been)
```

### 9.3 Replay SMC API

```typescript
// WS replay frame extension
interface ReplayFrame {
  type: "replay";
  data: {
    candles: Candle[];
    zones: SmcZone[];      // current SMC state at this replay moment
    swings: SmcSwing[];
    levels: SmcLevel[];
    smc_delta?: SmcDelta;  // what changed since last frame
    replay_meta: {
      bar_index: number;
      total_bars: number;
      replay_time_ms: number;
      speed: number;
    };
  };
}
```

---

## 9А. Trader Experience Architecture: що робить систему НЕЗАМІННОЮ

> Цей розділ — не про технологію, а про **trader value**. Технічно досконала система без правильної
> презентації = "ще один індикатор". Правильна презентація = **"AI-помічник, який думає як smart money"**.

### 9А.1 Три рівні цінності для трейдера

| Рівень | Що отримує | Як вимірюється | Конкуренти |
|--------|-----------|---------------|------------|
| **L1: Automation** | Auto-detection зон (OB, FVG, Structure) | Час аналізу: 20 хв → 30 сек | Платні SMC індикатори ($100-300/мо) |
| **L2: Intelligence** | Confluence POI scoring + Zone Quality | Precision: фокус на A+ зонах замість 15 зон | **Нікого** (навіть платні = flat zones без ranking) |
| **L3: Education** | Replay тренажер + Narrative engine | Skill growth: учень бачить formation в real-time | **Нікого** (TradingView replay ≠ SMC training) |

**L1 = table stakes** (всі платні tool-и це вміють). **L2 + L3 = наш competitive moat.**

### 9А.2 Workflow сильного трейдера (use case)

```
07:00 UTC — London Open. Трейдер відкриває Aione:

1. DASHBOARD (загальний погляд):
   "XAU/USD: 2 POI grade A+, bias BEARISH (D1+H4 aligned)"
   "NAS100: 1 POI grade A, bias BULLISH"
   "SPX500: no high-grade POI, range mode"
   → Фокус: XAU/USD (highest confluence)

2. XAU/USD H4 CHART:
   - D1 bias: BEARISH (BOS bear at 2880)
   - H4 OB bearish: [2865-2870], quality=0.91 (fresh, explosive, untested)
   - H4 FVG bearish: [2862-2866] (overlaps OB partially)
   - POI A+: [2862-2870], score=9, factors: OB+FVG+premium+HTF+killzone+fresh
   - Narrative: "Smart money supply zone: OB+FVG confluence in premium,
     D1 confirms bearish structure. Formed during London killzone."

3. DRILL DOWN M15:
   - Wait for M15 CHoCH bearish (confirmation)
   - 08:45 UTC: M15 CHoCH confirmed at 2863
   - Alignment indicator: "H4 ↘ + M15 CHoCH Bear → ALIGNED ✓ (score 9/11)"

4. ENTRY (manual, але система підказує):
   - Entry: 2863.00 (M15 CHoCH candle)
   - SL: 2870.50 (above H4 OB + FVG + buffer)
   - TP1: 2850.00 (next liquidity: equal lows cluster)
   - TP2: 2842.00 (PDL = previous day low)
   - R:R = 1:1.7 (TP1), 1:2.8 (TP2)

5. POST-TRADE (система логує):
   - POI A+ → entry taken → result: +1.7R
   - Statistics update: "OB+FVG+Premium POIs: 72% win rate (last 50 trades)"

Загальний час: ~3 хвилини (включаючи waiting for M15 CHoCH)
Без системи: ~25 хвилин ручного аналізу, можливо пропустив setup
```

### 9А.3 Narrative Engine (що відрізняє від "просто зон")

**Проблема**: Всі SMC tools малюють прямокутники. Трейдер бачить 20 зон і не знає **чому** кожна важлива.

**Рішення**: Кожен POI / зона має human-readable narrative:

```python
# core/smc/narrative.py
def build_narrative(poi: ConfluencePOI, context: SmcContext) -> str:
    """Generates human-readable explanation for POI.
    
    Example output:
    "Bearish supply zone: Order Block + FVG confluence at 2862-2870.
     Zone formed during London killzone (08:15 UTC).
     D1 structure bearish (BOS at 2880), H4 confirms.
     Zone is fresh (untested), impulse 2.8 ATR (explosive).
     Nearest liquidity target: Equal Lows at 2850 (3 touches).
     Quality: A+ (9/11), recommended action: SELL on M15 CHoCH confirmation."
    """
```

**Для менторів**: Narrative = готовий скрипт для учнів. Ментор не пояснює "чому ця зона" — система вже пояснила.

### 9А.4 Dashboard: Symbol Prioritization

```
GET /api/smc/dashboard → або WS frame type="smc_dashboard"

Response:
{
  "symbols": [
    {
      "symbol": "XAU/USD",
      "htf_bias": "bearish",
      "top_poi": {"grade": "A+", "score": 9, "price_range": [2862, 2870]},
      "active_zones": 15,
      "high_grade_count": 2,
      "alert": "Approaching A+ POI in ~30 min at current pace"
    },
    {
      "symbol": "NAS100",
      "htf_bias": "bullish", 
      "top_poi": {"grade": "A", "score": 7, "price_range": [21350, 21380]},
      "active_zones": 12,
      "high_grade_count": 1,
      "alert": null
    }
  ],
  "sorted_by": "top_poi.score DESC"
}
```

**Для трейдера**: "Не скролити 13 графіків → одна таблиця: де зараз найкращі setup-и?"

### 9А.5 Replay як Killer Feature для ком'юніті

```
Replay Training Modes:

Mode 1: "Blind Replay" (для самостійного тренування)
  - Replay запускається БЕЗ SMC overlay
  - Трейдер малює свої зони (drawing tools)
  - По завершенню: система показує свої зони → порівняння
  - Scoring: "Ви знайшли 3 із 5 OB, 2 із 3 FVG, пропустили A+ POI"

Mode 2: "Annotated Replay" (для навчання)
  - Replay з SMC overlay: зони з'являються в real-time
  - Pause на ключових моментах: "BOS formed here — what would you do?"
  - Step-by-step formation: учень бачить як zone будується бар за баром

Mode 3: "Mentor Session" (для менторів)
  - Ментор контролює replay (play/pause/speed/jump)
  - Учні підключені як read-only viewers
  - Ментор додає annotations в реальному часі
  - Session recording для повторного перегляду

Value proposition для ком'юніті:
  "Безкоштовний SMC тренажер з real-time зонами на 13 символів.
   Replay будь-якої дати. Ніхто на ринку цього не має."
```

---

## 9Б. Growth Trajectory: Шлях до рівня TradingView і далі

### Phase 1 (поточний ADR): SMC Engine Core (тижні 1-3)

**Ціль**: 10 алгоритмів працюють live для 13 символів × 8 TFs.

```
Deliverables:
  ✓ Swing + Structure + OB + FVG + Liquidity (E1 MVP)
  ✓ Premium/Discount + Inducement (E2 Core)
  ✓ Session/Killzone + Confluence POI + Quality Model (E3 Pro)
  ✓ WS integration (live + replay)
  ✓ HTTP API (/api/smc, /api/smc/dashboard)

Metric: "Один трейдер може працювати ефективно з XAU/USD"
```

### Phase 2: Intelligence Layer (тижні 4-6)

**Ціль**: Від "detection" до "recommendations".

```
Narrative Engine:
  - Human-readable explanations per POI
  - "Why this zone?" panel in UI

Alert System:
  - "Price approaching A+ POI in ~30 min"
  - WS push notifications
  - Configurable: per-symbol, per-grade, per-session
  - ADR required (окремий initiative)

Statistics Tracker:
  - Historical win/loss rate per zone type
  - Per-symbol performance: "XAU/USD H4 OBs: 68% reaction rate"
  - Zone heatmap: "These price levels triggered reactions 5+ times"

Metric: "Трейдер отримує 2-3 high-quality alerts на день без постійного моніторингу"
```

### Phase 3: Education Platform (тижні 7-10)

**Ціль**: Replay + SMC = найкращий безкоштовний тренажер для ICT/SMC трейдерів.

```
Replay Modes (Blind / Annotated / Mentor Session):
  - Scoring system: compare user's analysis vs system
  - Session recording + sharing
  - Lesson library (curated setups per symbol)

Import/Export:
  - Share setup analysis (JSON export)
  - Community lessons (mentor-created replay sessions)

Metric: "Ментор може провести 1-годинну сесію з 5 учнями, використовуючи тільки Aione"
```

### Phase 4: Platform Scale (тижні 11-16)

**Ціль**: Від "tool for one trader" до "platform for community".

```
Multi-user:
  - User accounts (optional, localStorage default)
  - Shared watchlists
  - Social annotations ("I see sell setup here" → visible to followers)

Extended Instruments:
  - Crypto (BTC, ETH) — via different data provider
  - More forex pairs (current: 13, target: 30+)
  - Futures? Requires ADR for new data source

Advanced Algorithms (Phase 3+ addons):
  - Wyckoff accumulation/distribution detection
  - ICT Silver Bullet timing model
  - Order Flow imbalance (якщо з'являться Level 2 дані)
  - Custom user-defined patterns (scripting?)

Mobile:
  - PWA version of ui_v4 (already Svelte → PWA-ready)
  - Push notifications (Alert System → mobile push)

Metric: "100+ active users, community-driven content"
```

### Competitive Position Matrix (поточний → Phase 4)

| Feature | TradingView | ATAS | Paid SMC tools | Aione Phase 1 | Aione Phase 4 |
|---------|-------------|------|----------------|---------------|---------------|
| Auto SMC zones | ❌ | ❌ | ✅ (flat) | ✅ (scored) | ✅ (AI-ranked) |
| Confluence scoring | ❌ | ❌ | ❌ | ✅ | ✅ (learning) |
| Zone quality/decay | ❌ | ❌ | ❌ | ✅ | ✅ |
| Session awareness | ❌ | ❌ | ⚠️ | ✅ | ✅ |
| Narrative engine | ❌ | ❌ | ❌ | ✅ (v1) | ✅ (AI) |
| Replay + SMC | ❌ | ❌ | ❌ | ✅ | ✅ (community) |
| Multi-TF alignment | ❌ (manual) | ❌ | ⚠️ | ✅ (auto) | ✅ (auto) |
| Mentor platform | ❌ | ❌ | ❌ | ✅ (basic) | ✅ (full) |
| Price | $0-$50/mo | $70/mo | $100-300/mo | Free | Free (+ premium?) |
| Community | ✅ (massive) | ⚠️ | ❌ | ❌ | ✅ (growing) |

**Key insight**: TradingView = platform з massive community, але **zero SMC intelligence**. Aione Phase 4 = **SMC-specific platform** з intelligence що TV не має. Різні ніші: TV = general charting, Aione = SMC-focused analysis + education.

**Realistic competitive edge**: не "заміна TradingView", а **"найкращий SMC companion tool"** — трейдер тримає TV для загального charting + Aione для SMC analysis + training.

---

## 10. Implementation Plan (P-slices)

> **Принцип**: Трейдер з 4 алгоритмами сьогодні > трейдер без нічого через 2 місяці з ідеальними 10.
> MVP = E1 (Swings + Structure + OB + FVG) → зони на UI → далі ітеративно.

Фази розділені **MVP gate**: WS frame з реальними зонами замість `[]`.

### ── PHASE 1: E1 Core Algorithms (Week 1-2) ──

### S1: Foundation Types + Swing Detection

```
Files: core/smc/__init__.py, core/smc/types.py, core/smc/config.py, core/smc/swings.py
Tests: tests/test_smc_swings.py
LOC: ~150
Verify: pytest, detect swings on 500 M1 XAU/USD bars from data_v3/
Dependencies: None (pure, no integration)
Note: _TfState.bars = deque(maxlen=lookback_bars), NOT list
```

### S2: Market Structure + Order Blocks + FVG

```
Files: core/smc/structure.py, core/smc/order_blocks.py, core/smc/fvg.py
Tests: tests/test_smc_structure.py, tests/test_smc_order_blocks.py, tests/test_smc_fvg.py
LOC: ~250 (3 files)
Verify: detect BOS/CHoCH, OBs (lifecycle: active→mitigated), FVGs (lifecycle: active→filled)
Dependencies: S1 (swings)
```

### S3: SmcEngine Orchestrator

```
Files: core/smc/engine.py
Tests: tests/test_smc_engine.py, tests/test_smc_incremental.py
LOC: ~150
Verify: update(bars) == sequential on_bar(); performance <10ms/bar on real XAU data
Dependencies: S1, S2
Gate: pytest — all E1 algorithms pass on real XAU/USD M1+H1 data
```

### ── PHASE 2: Integration (Week 3) ──

### S4: SmcRunner + WS Integration

```
Files: runtime/smc/__init__.py, runtime/smc/smc_runner.py
Changes: runtime/ws/ws_server.py (~30 LOC — hook into existing delta_loop)
Tests: tests/test_smc_runner.py
LOC: ~120
Verify: WS full frame має zones/swings/levels замість []
Note: SmcRunner = consumer of existing v3_local:updates:* bus (§6.1)
```

### S5: Config SSOT + types.ts Contract Expansion

```
Changes: config.json (smc section), ui_v4/src/lib/types.ts
Note: types.ts — kind.startsWith('ob') → orange, kind.startsWith('fvg') → green (backward compat)
Files: core/contracts/public/marketdata_v1/smc_v1.json
LOC: ~60
Verify: config loads, types.ts matches wire format, OverlayRenderer renders zones
Gate: existing OverlayRenderer.zoneColor() handles new kinds without crash
```

### ── ✅ MVP GATE: трейдер бачить OB/FVG/BOS/CHoCH на графіку ──

P9 з оригінального плану **на 80% вже зроблений**: OverlayRenderer.ts (269 LOC), ChartPane.svelte buildSmc(),
types.ts SmcZone/SmcSwing/SmcLevel. Потрібно тільки розширити `kind` handling + swings labels.

### ── PHASE 3: E2 Algorithms (Week 4-5) ──

### S6: Liquidity Levels + Premium/Discount

```
Files: core/smc/liquidity.py, core/smc/premium_discount.py
Tests: tests/test_smc_liquidity.py, tests/test_smc_premium_discount.py
LOC: ~180
Verify: equal highs/lows detected, PDH/PDL from D1, P/D zone at 50% equilibrium
Dependencies: S1 (swings), S2 (structure for P/D range reset)
```

### S7: Inducement + Sessions/Killzones

```
Files: core/smc/inducement.py, core/smc/sessions.py
Tests: tests/test_smc_inducement.py, tests/test_smc_sessions.py
LOC: ~170
Verify: false breakout detection, session tagging, killzone zone boost
Dependencies: S1 (swings), config.json:smc.sessions
```

### ── PHASE 4: Intelligence Layer (Week 6-7) ──

### S8: Confluence POI + Zone Quality + Narrative

```
Files: core/smc/confluence.py, core/smc/quality.py, core/smc/narrative.py
Tests: tests/test_smc_confluence.py, tests/test_smc_quality.py
LOC: ~250
Verify: POI scoring (11-point), zone decay, human-readable narrative
Dependencies: S1-S7 (all layers)
```

### ── PHASE 5: Polish (Week 8+) ──

### S9: HTTP API + Exit Gates + Docs

```
Changes: ui_chart_v3/server.py (/api/smc endpoint ~20 LOC), docs/contracts.md
Files: tools/exit_gates/gates/gate_smc_contract.py
Tests: tests/test_api_smc.py
LOC: ~80
Dependencies: S4
```

### S10: Replay Integration

```
Changes: runtime/replay/ (when ADR-0017 is implemented)
LOC: ~20
Dependencies: S3 + ADR-0017
```

**Реалістичний timeline**:

```
Week 1-2:  S1-S3   E1 MVP core/smc/ (pure logic + tests on real data)
Week 3:    S4-S5   Integration (SmcRunner + ws_server + config + types.ts)
── MVP GATE: зони на графіку ──
Week 4-5:  S6-S7   E2 algorithms (liquidity, P/D, inducement, sessions)
Week 6-7:  S8      E3 intelligence (confluence, quality, narrative)
Week 8+:   S9-S10  HTTP API, exit gates, replay
```

> **Чому довше ніж оригінал (8 тижнів vs 4)**:
>
> 1. Алгоритми потребують тюнінгу на реальних даних (XAU/USD vs NGAS дуже різні)
> 2. Integration = shared state + asyncio → Bug Hunter знайде гонки
> 3. UI rendering (BOS/CHoCH labels, strength opacity) потребує ітерацій
> Перший видимий результат — Week 3 (MVP gate). Решта — ітеративне нарощування.

---

## 11. Exit Gates

### Gate: SMC Contract Compliance

```python
# tools/exit_gates/gates/gate_smc_contract.py
"""
Перевіряє:
1. SmcZone.id format: "{kind}_{symbol}_{tf_s}_{anchor_ms}"
2. SmcZone.kind ∈ ZONE_KINDS
3. SmcZone.status ∈ ZONE_STATUSES
4. SmcSwing.kind ∈ SWING_KINDS
5. SmcLevel.kind ∈ LEVEL_KINDS
6. to_wire() output matches ui_v4 TypeScript types
7. I0: core/smc/ has no I/O imports
"""
```

### Gate: SMC Performance Rail

```python
"""
Перевіряє:
1. SmcEngine.on_bar() < max_compute_ms (з config.json)
2. SmcEngine.update(500 bars) < 100ms
3. Wire payload < 10 KB per snapshot
"""
```

### Gate: SMC Determinism

```python
"""
Перевіряє:
1. update(bars) == sequential on_bar(bar) for each bar (determinism)
2. Same bars → same zones/swings/levels (reproducibility)
3. Replay produces identical SMC state as live would have
"""
```

---

## 12. Інваріанти (SMC-specific)

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **S0** | core/smc/ = pure logic, NO I/O | I0 dependency rule gate |
| **S1** | SMC не пише в UDS/SSOT JSONL | SmcRunner is read-only, no commit calls |
| **S2** | SMC deterministic: same bars → same zones | Determinism gate + tests |
| **S3** | Zone IDs deterministic: same input → same ID | ID format = `{kind}_{symbol}_{tf_s}_{anchor_ms}` |
| **S4** | Performance: on_bar() < max_compute_ms | Runtime rail + gate |
| **S5** | Config SSOT: all params from config.json:smc | No hardcoded thresholds |
| **S6** | Wire format matches ui_v4 TypeScript types | Contract gate |

---

## 13. Rollback

### Per-slice rollback

Кожен P-slice — незалежний rollback:

- P1–P6: `rm -rf core/smc/` — повертає до стану "без SMC"
- P7: revert ws_server.py changes — frames повертаються до `zones: []`
- P8: revert server.py + config.json — endpoint зникає
- P9: revert ui_v4/ smc components — UI без overlay
- P10: revert replay integration — replay без SMC

### Full rollback

```bash
git checkout -- core/smc/ runtime/smc/ runtime/ws/ws_server.py ui_chart_v3/server.py config.json ui_v4/src/smc/ tests/test_smc_*
rm -rf core/smc/ runtime/smc/ ui_v4/src/smc/
```

Система повертається до стану "графік без SMC overlay" — жодних data corruption чи breaking changes, бо SMC = read-only ephemeral overlay.

---

## 14. Ризики та міtigації

| Ризик | Ймовірність | Вплив | Мітигація |
|-------|-------------|-------|-----------|
| SMC compute > 10ms per bar на Python 3.7 | Medium | Повільні delta frames | `deque(maxlen)` замість list; numpy vectorization; per-layer disable; async compute |
| Thread safety (SmcRunner in ws_server process) | Medium | Data race | asyncio-only (single event loop); fallback: threading.Lock per (symbol, tf) |
| 13 symbols × 8 TFs = 104 warmups at cold start | Low | Slow cold-start (+10s) | Parallel warmup; priority symbols first |
| config.json bloat (SMC params) | Low | Config drift | Nested smc section, SmcConfig dataclass guard |
| UI rendering perf (30+ zones on chart) | Low | Canvas lag | max_zones_per_tf cap; LOD by zoom |
| Cross-TF bias stale data | Medium | Wrong HTF bias | Timestamp on bias; stale → "neutral" fallback |

---

## 15. Зв'язок з іншими ADR

| ADR | Зв'язок |
|-----|---------|
| 0001 (UDS) | SmcRunner читає bars через UDS.read_window() для warmup |
| 0002 (DeriveChain) | DeriveEngine cascade → trigger SmcEngine.on_bar() |
| 0003 (Cold Start) | SmcRunner warmup після prime_ready |
| 0017 (Replay) | SmcEngine інтегрується в ReplayEngine.step() |
| 0023 (D1 Derive) | D1 бари = input для PDH/PDL/PWH/PWL liquidity levels |

---

## 16. Review Notes (staff-engineer рев'ю)

> Дата рев'ю: 2026-02-28. Вердикт: **APPROVED з коригуваннями (applied).**

### Що вже побудоване в коді (ADR baseline)

| Компонент | Файл | Стан | Для SMC MVP |
|-----------|------|------|-------------|
| Overlay renderer | `ui_v4/src/smc/OverlayRenderer.ts` (269 LOC) | **Працює** — рендерить zones/swings/levels на canvas | P9 на 80% готовий |
| SMC data extraction | `ChartPane.svelte:88` `buildSmc(frame)` | **Працює** — витягує з WS frame | Готовий |
| WS frame placeholders | `ws_server.py:271-273` `zones: [], swings: [], levels: []` | **Placeholder** | Потрібно заповнити |
| TypeScript types | `types.ts:23-49` SmcZone/SmcSwing/SmcLevel/SmcData | **Визначені** | Потрібно розширити kind |
| Backend SMC logic | `core/smc/`, `runtime/smc/` | **Не існує (0 LOC)** | Весь S1-S3 |

### 3 коригування (applied у rev 2.1)

| # | Коригування | Що змінено в ADR | Причина |
|---|-------------|------------------|---------|
| C1 | **MVP = E1 only (4 алгоритми)** | §10: P-slices перебудовані на S1-S10, MVP gate після S5 | OB/FVG без confluence = корисний. Confluence без OB/FVG = безглуздий. |
| C2 | **SmcRunner = existing bus consumer** | §6.1: SmcRunner як callback від delta_loop, не окремий Redis канал | In-process = direct callback, 0 overhead. Окремий `smc:delta:*` = зайвий pub/sub roundtrip. |
| C3 | **types.ts prefix matching** | §6.1a: `kind.startsWith('ob')` замість `kind === 'ob'` | Forward-compatible з sub-kinds. 3 LOC зміна. |

### Ризик Python 3.7 performance (додана мітигація)

OB/FVG lifecycle check = float comparison (price vs zone.high/low) = мікросекунди per zone.
Swing detection rolling window = **deque(maxlen=lookback_bars)**, не list scan.
Зафіксовано в S1 note: `_TfState.bars = deque(maxlen=lookback_bars)`.
Worst case 30 zones × 104 пар = 3120 checks — допустимо при simple float comparison.

### Реалістичний timeline (8 тижнів замість 4)

1. Алгоритми потребують тюнінгу на реальних даних (XAU/USD vs NGAS дуже різні)
2. Integration = shared state + asyncio → Bug Hunter знайде гонки
3. UI rendering (BOS/CHoCH labels, strength opacity) потребує ітерацій
4. Перший видимий результат — **Week 3** (MVP gate). Решта — ітеративне нарощування.

---

## 17. Відкриті питання (для Phase 2+)

1. **Alert system**: SmcRunner публікує alerts ("Price approaching A+ POI on XAU/USD H4")? Architeced в §9Б Phase 2, потребує окремий ADR для transport (WS push, sound, browser notification).
2. **Risk Calculator**: Server-side чи UI-only? Якщо server — окремий endpoint `/api/smc/risk`. Залежить від того чи знаємо account size / risk per trade.
3. **Backtesting**: SmcEngine + historical bars → win rate by zone type/grade. Потребує окремий initiative. High value: "A+ POIs мають 72% reaction rate" → proof of confluence scoring.
4. **Custom zones**: User-drawn зони що co-exist з auto-detected. Drawing storage ADR (пов'язано з ADR-0007 Drawing Tools).
5. **Weekly TF (W1)**: Додавання tf_s=604800. Потребує зміну в TF allowlist + derive chain + calendar logic.
6. **Plugin Architecture**: Можливість додавати custom SMC layers (Wyckoff, Silver Bullet) без змін у core. Interface: `SmcLayer.on_bar() → List[SmcZone|SmcSwing|SmcLevel]`.
7. **Multi-user Annotations**: Ментор анотує replay → учні бачать. Потребує persistence layer (окремий ADR).
8. **Mobile PWA**: ui_v4 Svelte → PWA. SMC dashboard як перший mobile-friendly screen. Push notifications від Alert system.
