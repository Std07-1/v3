# ADR-0028: SMC Clean Chart Strategy — від Detection Engine до Trading Terminal

- **Статус**: Proposed
- **Дата**: 2026-03-04
- **Автор**: Chief Strategist + System Architect
- **Initiative**: SMC-VIS (SMC Visualization Pipeline)
- **Залежності**: ADR-0024 (SMC Engine), ADR-0024a (Self-Audit), ADR-0024c (Zone POI Rendering), ADR-0026 (Overlay Levels)
- **Скоупи**: core/smc/, ui_v4/src/, config.json:smc

---

## 0. Executive Summary

### Проблема

Система має працюючий SMC Detection Engine (ADR-0024): свінги, BOS/CHoCH, FVG, OB, ліквідність, Premium/Discount — все обчислюється і рендериться. Але **відсутній шар відбору** — все що знайдено, все й показано. Результат:

- **H4**: прийнятний (мало елементів за природою TF)
- **H1**: на межі — cross-TF проєкції починають конкурувати з рідними зонами
- **M15**: **непридатний для торгівлі** — множинні зони перекриваються, лейбли конкурують, трейдер не може відповісти на питання "що робити?" за 3 секунди

Це типова проблема будь-якої аналітичної системи: **Detection ≠ Presentation**. Двигун знаходить 40 зон — трейдеру потрібні 4.

### Рішення

Чотирифазна стратегія трансформації з **Display Filter Layer** як фундаментом:

| Фаза | Назва | Суть | Головний ефект |
|------|-------|------|----------------|
| **Φ0** | Elimination Engine | Mitigation + Proximity + TTL + Budget — presentation filter | M15 стає читабельним |
| **Φ1** | OB Doctrine | Order Block detection за SMC-каноном + Wick-OB + FVG refinement | "Кожна зона = торговий сенс" |
| **Φ2** | TF Sovereignty | Cross-TF projection з семантикою шарів (рамка / робоча / тригер) | TF-ієрархія працює |
| **Φ3** | Scenario Product | Alignment Banner + Grade System + Narrative Output | "3 секунди до рішення" |

### Ключове обмеження

Фази **строго послідовні**. Φ1 без Φ0 = нові OB утоплять графік ще більше. Φ2 без Φ0+Φ1 = cross-TF проєкції без фільтрації = хаос. Φ3 без Φ0+Φ1+Φ2 = сценарії на основі шуму.

### Контракт з трейдером (exit criteria ADR-0028)

По завершенні всіх фаз:

1. ≤12 SMC об'єктів на chart у Focus mode (будь-який TF, будь-який символ)
2. Bias + POI + Target + Invalidation видно за 3 секунди
3. Мітиговані зони зникають (не залишаються "привидами")
4. Чарт не стає кладовищем з часом (TTL + decay)
5. M15 chart читається так само легко, як H4
6. "Нічого не робимо" — визначений стан, не баг

---

## 1. Контекст і проблема

### 1.1 Поточний стан (evidence зі скрінів 2026-03-04)

**XAU/USD H4** (скрін 1):
- ~6–8 FVG-зон (зелені bullish, червоні bearish)
- Поодинокі BOS-лейбли
- Зони не перекриваються, є повітря між ними
- **Вердикт**: 7/10, прийнятний для торгівлі, але без grade/priority

**XAU/USD H1** (скрін 2):
- ~12–15 елементів (FVG зони + BOS/CHoCH лейбли + великий червоний блок 5180–5380)
- Cross-TF проєкції (H4 FVG) видні поруч з H1 зонами
- Правий край — кластер зон, що конкурують
- **Вердикт**: 5/10, на межі, трейдер може розібратися за 10–15 секунд, не за 3

**XAU/USD M15** (скрін 3):
- 20+ червоних зон, що перекриваються та зливаються в суцільне поле
- Множинні CHoCH/BOS лейбли конкурують за простір
- Cross-TF зони (H1, H4) накладаються на M15 рідні зони
- Зелені зони знизу + червоні зверху = весь графік в кольорових прямокутниках
- **Вердикт**: 2/10, непридатний для торгівлі, порушує Clean Chart Doctrine (§1.1, §1.2)

### 1.2 Root cause analysis

| Причина | Опис | Severity |
|---------|------|----------|
| **Відсутній mitigation lifecycle** | Зона, через яку ціна пройшла, залишається на графіку з тією ж візуальною вагою | S1 |
| **Відсутній proximity filter** | Зона за 500 пунктів від ціни має таку ж видимість, як зона за 20 пунктів | S1 |
| **Відсутній TTL/decay** | Зона створена 300 барів тому виглядає ідентично до свіжої | S1 |
| **Відсутній display budget** | Якщо engine знайшов 40 зон — 40 і показано | S1 |
| **Cross-TF без семантики** | H4 зона на M15 chart має ту ж візуальну вагу, що й M15 зона | S2 |
| **Відсутній grade/strength sorting** | Слабка зона з strength=0.2 і сильна з strength=0.9 — однакові | S2 |
| **OB detection не за SMC-каноном** | Потрібен контекст (зняття ліквідності, поглинання, межі тіло/тінь) | S2 |

### 1.3 Чому це blocking

Без фільтрації presentation layer — **кожен новий алгоритм погіршує ситуацію**. Додамо Breaker → +N зон. Додамо Mitigation Block → +N зон. Додамо Inducement → +N маркерів. Графік деградує експоненційно.

**Висновок**: Φ0 (Elimination Engine) — це не "nice-to-have presentation polish", а **архітектурний prerequisite** для будь-якого подальшого розвитку SMC.

---

## 2. Розглянуті варіанти

### Варіант A: "Кнопка вимкнення" (toggle per element type)

Дати трейдеру кнопки: OB on/off, FVG on/off, BOS on/off, cross-TF on/off.

**Плюси**: Простий UI, швидка реалізація (1–2 дні).
**Мінуси**: Перекладає відповідальність на трейдера. Трейдер не знає, які 4 з 40 зон важливі — він знає лише "забагато" і вимкне все. Порушує S6 ("Один екран — одна задача") і V3 ("Показуємо бо вони зараз потрібні, а не бо ми їх знайшли").

**Вердикт**: ❌ Не вирішує проблему, маскує симптом.

### Варіант B: "Тільки N найновіших зон"

Обмежити кількість зон до N (наприклад, 5 на TF). Показувати тільки найновіші.

**Плюси**: Простий, гарантує бюджет.
**Мінуси**: Новизна ≠ якість. Стара H4 A+ зона цінніша за нову M15 C зону. Ігнорує strength, proximity, mitigation status.

**Вердикт**: ❌ Неправильний критерій відбору.

### Варіант C: "Display Filter Pipeline" (обрано)

Побудувати pipeline фільтрації між SmcEngine output і renderer:

```
SmcEngine (compute) → [mitigation] → [proximity] → [TTL/decay] → [strength sort] → [budget cap] → Renderer (display)
```

Кожен крок pipeline — окремий, тестований, config-driven фільтр. Engine обчислює max_zones_per_tf (10, ADR-0024). Display показує focus_budget.zones_per_side (2, Chief Strategist §1.2).

**Плюси**: Архітектурно чистий (compute ≠ display). Config-driven. Кожен фільтр тестується ізольовано. Додавання нових алгоритмів (Breaker, Mitigation Block) не деградує display — вони проходять той самий pipeline.
**Мінуси**: Більше коду ніж варіанти A/B. Потрібна інтеграція з існуючим rendering pipeline.

**Вердикт**: ✅ Обрано. Масштабується. Відповідає архітектурі (core = compute, ui = present).

---

## 3. Рішення: чотири фази

---

### ═══ Φ0: ELIMINATION ENGINE ═══

> **Мета**: прибрати з екрану все, що не потрібно ЗАРАЗ.
> **Метрика**: M15 chart ≤ 12 видимих SMC об'єктів у Focus mode.
> **Залежності**: ADR-0024c (zone lifecycle), config.json:smc.display

#### 3.0.1 Архітектура Display Filter Pipeline

```
                    SmcEngine output
                         │
                    ┌─────▼─────┐
                    │  F1: Miti-│  Зона мітигована? → remove або fade
                    │  gation   │  (close пробив зону → mitigation)
                    └─────┬─────┘
                    ┌─────▼─────┐
                    │  F2: Proxi│  Далі ніж N×ATR від ціни? → hide
                    │  mity     │  (операційно нерелевантна)
                    └─────┬─────┘
                    ┌─────▼─────┐
                    │  F3: TTL/ │  Старша за TTL? → fade → hide
                    │  Decay    │  (графік не стає кладовищем)
                    └─────┬─────┘
                    ┌─────▼─────┐
                    │  F4: Stren│  strength < min_display? → hide
                    │  gth Gate │  (шум не проходить)
                    └─────┬─────┘
                    ┌─────▼─────┐
                    │  F5: Budge│  Більше budget? → відсікти найслабші
                    │  t Cap    │  (≤2 per side у Focus)
                    └─────┬─────┘
                         │
                    Filtered zones → Renderer
```

#### 3.0.2 F1: Mitigation Detection

**Визначення**: зона вважається **мітигованою**, коли CLOSE бару пробиває протилежну межу зони.

| Тип зони | Напрям | Mitigation умова |
|----------|--------|-----------------|
| Bullish OB / Bullish FVG | demand (buy) | `bar.close < zone.low` |
| Bearish OB / Bearish FVG | supply (sell) | `bar.close > zone.high` |

**Чому CLOSE, а не wick**: тінь (wick) може "проколоти" зону без реального пробою. SMC-канон (документ 1, §3.4): мітигація = ціна пройшла ЧЕРЕЗ зону, не торкнулась її.

**Поведінка після мітигації**:
- Зона отримує `mitigated_at_bar_index` та `mitigated_at_ms`
- У **Focus mode**: зона fade до opacity 0.08 протягом `ttl_bars.mitigated_zone` (default 20 барів), потім hide
- У **Research mode**: зона залишається як "ghost" (dash border, opacity 0.05) до кінця TTL
- У **Debug mode**: зона залишається завжди з лейблом `MIT @{bar_index}`

**Edge cases**:
- Зона мітигована на M15, але ще не на H1 → статус per-TF (мітигація на рідному TF)
- Часткова мітигація (close всередині зони, не за межею) → **НЕ мітигована**, але strength decay × 0.8
- Gap через зону (open > zone.high для bearish) → **мітигована** (gap = aggressive move through)

#### 3.0.3 F2: Proximity Filter

**Визначення**: зона вважається **операційно нерелевантною**, якщо вона далі за `proximity_atr_mult × ATR(14)` від поточної ціни.

```python
distance = abs(zone.mid_price - current_price)
atr = ATR(14, current_tf)
threshold = config.smc.display.proximity_atr_mult * atr  # default 3.0

if distance > threshold and zone.grade not in ("A+", "A"):
    zone.display_state = HIDDEN
```

**Виключення**: зони з grade A+ або A **не приховуються proximity filter'ом** — вони занадто цінні, щоб ховати через відстань. Їхня opacity зменшується: `opacity *= max(0.3, 1.0 - distance / (threshold * 2))`.

**ATR source**: береться з `core/smc/atr.py` (вже реалізовано в ADR-0024). Per-symbol, per-TF.

#### 3.0.4 F3: TTL / Decay

Кожна зона має **вік у барах** з моменту створення. TTL визначає lifecycle:

| TF зони | Grade | TTL (бари) | Fade start | Після TTL |
|---------|-------|-----------|------------|-----------|
| M1–M5 | будь-який | 60 | 40 (opacity × 0.5) | hide |
| M15 | B/C | 100 | 70 (opacity × 0.5) | hide |
| M15 | A+/A | 200 | 150 (opacity × 0.7) | fade to 0.15, keep |
| H1 | B/C | 200 | 150 (opacity × 0.5) | hide |
| H1 | A+/A | 400 | 300 (opacity × 0.7) | fade to 0.15, keep |
| H4 | будь-який | 500 | 400 (opacity × 0.7) | fade to 0.2, keep |
| D1 | будь-який | 1000 | 800 (opacity × 0.7) | fade to 0.2, keep |

**Decay formula**:
```python
age_bars = current_bar_index - zone.created_at_bar_index
ttl = config.smc.display.ttl_bars[zone.tf][zone.grade_bucket]
fade_start = ttl * config.smc.display.fade_start_pct  # default 0.7

if age_bars < fade_start:
    opacity_mult = 1.0
elif age_bars < ttl:
    progress = (age_bars - fade_start) / (ttl - fade_start)
    opacity_mult = 1.0 - progress * 0.7  # від 1.0 до 0.3
else:
    opacity_mult = 0.0  # hide (або keep at min для A+/A HTF)
```

**Структурні лейбли (BOS/CHoCH)**: TTL = 50 барів. Fade при 40+. Hide при 50+. Завжди показувати останні 2 (незалежно від TTL).

#### 3.0.5 F4: Strength Gate

Зони зі `strength < min_display_strength` (default 0.3) — не показуються у Focus mode.

**Strength mapping до opacity** (Chief Strategist §4.5):
```
strength ≥ 0.8  →  opacity × 1.0    "Explosive"
strength 0.5–0.8 →  opacity × 0.7    "Solid"
strength 0.3–0.5 →  opacity × 0.4    "Weak" (тільки якщо в budget)
strength < 0.3  →  HIDDEN (Focus)    "Noise"
```

#### 3.0.6 F5: Budget Cap

Після проходження F1–F4, зони що залишились — **сортуються** і **відсікаються** до бюджету:

**Sort key** (descending priority):
1. `grade` (A+ > A > B > C)
2. `strength` (0.9 > 0.5 > 0.3)
3. `proximity` (ближче до ціни = вище)
4. `freshness` (новіша = вище, при рівних інших)

**Budget (Focus mode)**:
```
zones_per_side: 2        (max 2 supply + 2 demand)
liquidity_per_side: 2    (max 2 BSL + 2 SSL)
structure_labels: 2      (останній BOS/ChoCH + 1 попередній)
total_max: 12            (жорсткий cap на ВСЕ)
```

**Budget (Research mode)**:
```
zones_per_side: 6
liquidity_per_side: 5
structure_labels: 8
total_max: 30
```

#### 3.0.7 Config schema (Φ0)

```json
{
  "smc": {
    "display": {
      "mode_default": "focus",
      "focus_budget": {
        "zones_per_side": 2,
        "liquidity_per_side": 2,
        "structure_labels": 2,
        "total_max": 12
      },
      "research_budget": {
        "zones_per_side": 6,
        "liquidity_per_side": 5,
        "structure_labels": 8,
        "total_max": 30
      },
      "proximity_atr_mult": 3.0,
      "min_display_quality": 0.3,
      "min_display_strength": 0.3,
      "ttl_bars": {
        "m1_m5": { "default": 60 },
        "m15":   { "bc": 100, "a_plus_a": 200 },
        "h1":    { "bc": 200, "a_plus_a": 400 },
        "h4":    { "default": 500 },
        "d1":    { "default": 1000 },
        "structure_label": 50,
        "mitigated_zone": 20
      },
      "fade_start_pct": 0.7,
      "mitigation_rule": "close_through"
    }
  }
}
```

#### 3.0.8 Placement (Dependency Rule)

| Компонент | Шар | Файл | Обґрунтування |
|-----------|-----|------|---------------|
| Mitigation detection logic | `core/smc/` | `zone_lifecycle.py` (новий) | Pure logic: bar.close vs zone bounds. Без I/O |
| TTL/Decay calculator | `core/smc/` | `zone_lifecycle.py` | Pure: age_bars → opacity_mult |
| Proximity calculator | `core/smc/` | `zone_lifecycle.py` | Pure: distance / ATR → visibility |
| Budget sorter | `core/smc/` | `zone_lifecycle.py` | Pure: sort + slice |
| Display Filter orchestrator | `ui_v4/src/` | `smc/DisplayFilter.ts` | Applies core logic, passes to renderer |
| Config parsing | `core/smc/` | `config_types.py` (extend) | Dataclass for display config |

**I0 compliance**: core/smc/zone_lifecycle.py = pure functions, zero I/O. UI calls core functions.

#### 3.0.9 Acceptance Criteria (Φ0)

| AC | Given | When | Then | Test |
|----|-------|------|------|------|
| AC-Φ0-1 | M15 chart, XAU/USD, active market | Count visible SMC objects | ≤ 12 (Focus) | Automated count |
| AC-Φ0-2 | Zone fully traversed by close | Next render | Zone fades → disappears within 20 bars | Visual + unit test |
| AC-Φ0-3 | Zone > 3×ATR from price, grade B | Render | Zone hidden | Unit test |
| AC-Φ0-4 | Zone age > TTL | Render | Zone hidden (Focus) or ghost (Research) | Unit test |
| AC-Φ0-5 | 10 zones pass F1–F4, budget = 2/side | Render | Only top-4 visible (2 supply + 2 demand) | Unit test |
| AC-Φ0-6 | Switch to Research mode | Render | Budget expands to 6/side, more zones visible | Visual |

#### 3.0.10 Очікуваний ефект (Φ0)

На базі скріну M15: з ~20+ видимих зон → **4–6 залишаться** (2 supply + 2 demand + ~2 структурних лейбли). Це одне — прибере ~70% візуального шуму без зміни жодного алгоритму.

#### 3.0.11 Ризики (Φ0)

| Ризик | Probability | Impact | Mitigation |
|-------|-------------|--------|------------|
| Трейдер не бачить зону яку хотів | MEDIUM | Trust loss | Research mode = fallback, можна побачити все |
| ATR spike → proximity threshold занадто широкий | LOW | More zones visible | ATR smoothing (EMA-based) |
| Mitigation false positive (gap через зону) | LOW | Missing zone | Gap = valid mitigation (канон) |
| Budget cap ховає A+ зону | IMPOSSIBLE | — | A+ завжди проходить proximity filter |

---

### ═══ Φ1: OB DOCTRINE ═══

> **Мета**: кожна зона на графіку = торговий сенс, а не "тут був паттерн".
> **Метрика**: 0 зон без confluence context. Кожна видима зона має grade ≥ B.
> **Залежності**: Φ0 (без Elimination Engine нові OB утоплять графік)

#### 3.1.1 Контекст: SMC-канон Order Block

З верифікованого документа (Doc 1 §3.1, Doc 2 §1):

**Order Block = не просто "свічка перед імпульсом"**. OB = контекстний елемент з чекліста:

1. **Контекст/POI** — OB шукається в зоні інтересу (краще зі старшого TF)
2. **Зняття ліквідності** — перед/всередині OB формується ліквідність, яку знімають (значуще на H4+)
3. **Поглинання / зміна потоку** — є імпульсний рух після OB
4. **Межі** — залежать від наявності імбалансу:
   - Є імбаланс після поглинання → тінь враховується відповідно до правил
   - Немає імбалансу → тіло → екстремум

**Що це означає для алгоритму**: поточний OB detector (ADR-0024) знаходить "свічку перед імпульсом". Φ1 додає **контекстну валідацію** — зона набирає confluence score тільки при наявності підтверджувальних факторів.

#### 3.1.2 OB Confluence Scoring

Замість бінарного "є OB / немає OB" — кожен кандидат OB набирає **confluence score** (0–11):

| Factor | Points | Умова | Evidence (Doc 1) |
|--------|--------|-------|-------------------|
| Liquidity sweep before OB | +2 | Зняття ліквідності перед/всередині OB | §3.1 "перед OB формується ліквідність, її знімають" |
| Imbalance after engulfing | +2 | FVG утворився після поглинання | §3.1 "межі: є імбаланс → тінь" |
| HTF POI alignment | +2 | OB знаходиться всередині зони старшого TF | §5.4 "старший TF дає рамку/орієнтир" |
| Extremum position | +1 | OB на екстремумі range, не посередині | §3.1 "OB на екстремумах, не посередині діапазону" |
| Strong impulse | +1 | ATR(impulse) > 1.5 × ATR(14) | Implied: "поглинання / логіка зміни потоку" |
| Discount/Premium alignment | +1 | Bullish OB в discount, Bearish OB в premium | §3.1 "discount-market починається від початку OB" |
| Structure confirmation | +1 | BOS/ChoCH підтверджує напрям OB | §2.2 "зовнішня структура важливіша за внутрішню" |
| TF significance | +1 | OB знайдено на H4+ (а не M1/M5) | §2.1 "на H4+ достатньо зняття навіть однієї свічки" |

**Grade mapping**:
```
Score 8–11 → A+  (primary, яскравий)
Score 6–7  → A   (secondary, нормальний)
Score 4–5  → B   (Research only)
Score 0–3  → C   (Debug only)
```

#### 3.1.3 Wick-OB (тінь свічки як локальний OB)

З верифікованого документа (Doc 1 §3.2, Doc 2 §1.5):

**Правило**: тінь, що знімає ліквідність = OB на молодшому TF.

**Алгоритм**:
```
1. Свічка має тінь > 50% від загального range свічки
2. Тінь знімає попередній swing (high або low)
3. Marking:
   - Тіло bearish (close < open) → zone = тіло свічки (open→close)
   - Тіло bullish (close > open) → zone = тільки тінь (high→open або close→low)
4. Перевірка на молодшому TF: чи є відповідна структура (OB/BOS)
```

**Marking-правило пояснення** (Doc 2 §2): "якщо тіло чорне (bearish) — падіння стартує від open → відмічаємо тіло. Якщо тіло зелене (bullish) — реальна зона продажу в тіні → відмічаємо тільки тінь."

**Confluence**: Wick-OB отримує base score 3 + додаткові фактори. Якщо тінь НЕ знімає ліквідність → **не є OB** (реакція може бути через імбаланс, часто ~0.5).

#### 3.1.4 FVG Refinement

FVG detection вже реалізований (ADR-0024). Φ1 додає:

**Strength scoring для FVG**:
- Gap size відносно ATR: `gap_atr_ratio = (high_candle1 - low_candle3) / ATR(14)`
- `ratio > 1.5` → strength 0.9 (explosive gap)
- `ratio 0.8–1.5` → strength 0.6 (solid)
- `ratio 0.3–0.8` → strength 0.3 (minimal)
- `ratio < 0.3` → strength 0.1 (noise → hidden by F4)

**Partial fill tracking**:
- Ціна зайшла в FVG але не закрила → FVG stale (strength × 0.7)
- Ціна закрила FVG > 50% → FVG weak (strength × 0.4)
- Ціна закрила FVG > 100% → mitigated (F1 takes over)

#### 3.1.5 BOS/CHoCH Refinement

Вже реалізовано (ADR-0024). Φ1 додає:

**TTL enforcement**: max 2 видимих лейбли (останній + попередній). Старіші → fade → hide.

**Impulse validation**: BOS без імпульсу (range(BOS candle) < 0.5 × ATR) → CHoCH downgrade (не показувати у Focus якщо не підтверджений).

#### 3.1.6 Навчальний режим (Doc 1 §6)

**Рекомендація з документа**: на поточному етапі працювати тільки з **OB + Imbalance (FVG)**, без Breaker/Mitigation block.

**Реалізація**: Breaker і Mitigation block detection **залишаються в engine** (ADR-0024), але не рендеряться у Focus/Research. Config flag:

```json
{
  "smc": {
    "display": {
      "enabled_zone_types": ["ob", "fvg"],
      "disabled_zone_types": ["breaker", "mitigation_block"]
    }
  }
}
```

Коли Breaker/Mitigation block будуть верифіковані окремим ADR → додаються до `enabled_zone_types`.

#### 3.1.7 Acceptance Criteria (Φ1)

| AC | Given | When | Then |
|----|-------|------|------|
| AC-Φ1-1 | OB без зняття ліквідності, без HTF POI | Grade calculation | Grade ≤ B (не видно у Focus) |
| AC-Φ1-2 | OB зі зняттям ліквідності + FVG + HTF alignment | Grade calculation | Grade A+ (score ≥ 8) |
| AC-Φ1-3 | Wick що знімає ліквідність, bearish тіло | Zone marking | Zone = тіло (open→close), не тінь |
| AC-Φ1-4 | Wick що НЕ знімає ліквідність | Zone marking | Не створюється як OB |
| AC-Φ1-5 | OB посередині range (не на екстремумі) | Extremum check | Extremum factor = 0, grade знижено |
| AC-Φ1-6 | FVG gap < 0.3×ATR | Strength scoring | strength ≤ 0.1, hidden by F4 |

---

### ═══ Φ2: TF SOVEREIGNTY ═══

> **Мета**: кожен TF chart має свій "характер" — HTF = карта, Structure = навігатор, Execution = адреса.
> **Метрика**: на H4 chart — 0 елементів нижче H1. На M5 chart — тільки проєкція POI + trigger.
> **Залежності**: Φ0 + Φ1 (без фільтрації cross-TF проєкції = хаос²)

#### 3.2.1 TF Role Model

| Рівень | TF | Роль | Що показувати | Що НЕ показувати |
|--------|-----|------|---------------|------------------|
| **HTF Context** | H4 / D1 | "Де ми в структурі дня/тижня" | H4/D1 зони (A+/A), великі пули ліквідності (PDH/PDL, PWH/PWL), загальний bias (BOS/CHoCH), Premium/Discount overlay | M15 і нижче зони, мікроструктура, inducement |
| **Structure** | M15 | "Яка структура і де POI" | Активний range, BOS/CHoCH (останні 2), 1–2 POI зони per side, 2 liquidity targets per side, HTF зони як **фонові рамки** | Всі 15 зон, стара структура, Grade C/B зони у Focus |
| **Execution** | M5 / M1 | "Є trigger чи ні" | Проєкція POI з M15, найближчий target, invalidation, останній M5 CHoCH/BOS як trigger | Будь-які інші зони, рівні, структура, P/D |

#### 3.2.2 Cross-TF Projection Semantics

Зараз: H4 зона на M15 chart = та ж візуальна вага що й M15 зона. Це неправильно.

**Нова семантика**:

| Зона на chart | Рідна TF зона | HTF зона (проєкція) |
|---------------|----------------|---------------------|
| **Візуальна роль** | "Точна адреса" — конкретний рівень дії | "Рамка" — загальна область інтересу |
| **Opacity** | Повна (згідно grade) | × 0.4 (фоновий шар) |
| **Border** | Solid, 2px | Dotted, 1px |
| **Fill** | Gradient (яскравий) | Flat (приглушений) |
| **Label** | `OB` / `FVG` + grade badge | `H4 OB` / `D1 FVG` (TF prefix) |
| **Z-index** | Передній план | Задній план (за рідними зонами) |

**Правило**: рідна зона всередині HTF проєкції → **HTF fade ще більше** (opacity × 0.2), щоб не конкурувати. Це вирішує проблему "два прямокутники один на одному".

#### 3.2.3 Element Visibility Matrix

| Елемент | HTF chart (H4/D1) | Structure chart (M15) | Execution chart (M5/M1) |
|---------|-------------------|-----------------------|--------------------------|
| D1 OB/FVG | ✅ primary | ❌ | ❌ |
| H4 OB/FVG | ✅ primary | ✅ projection (фон) | ❌ |
| H1 OB/FVG | ❌ | ✅ projection (фон) | ❌ |
| M15 OB/FVG | ❌ | ✅ primary | ✅ projected POI |
| M5 structure | ❌ | ❌ | ✅ trigger only |
| PDH/PDL/PWH/PWL | ✅ | ✅ | ✅ (target only) |
| Premium/Discount | ✅ background | ✅ if range valid | ❌ |
| BOS/ChoCH | ✅ D1/H4 | ✅ M15 | ✅ M5 trigger only |
| Inducement | ❌ | ✅ if near POI | ✅ if near POI |

#### 3.2.4 Реалізація

**UI-side routing**: `DisplayFilter.ts` отримує `chart_tf` і `zone.source_tf` → застосовує visibility matrix:

```typescript
function isVisible(zone: SmcZone, chartTf: number): DisplayMode {
  const role = getTfRole(chartTf);  // htf | structure | execution
  const zoneRole = getTfRole(zone.source_tf);

  if (role === 'execution' && zoneRole !== 'structure') return 'hidden';
  if (role === 'htf' && zoneRole === 'execution') return 'hidden';
  if (role === 'structure' && zoneRole === 'htf') return 'projection';
  if (zone.source_tf === chartTf) return 'primary';
  // ... etc
}
```

**Config**:
```json
{
  "smc": {
    "tf_roles": {
      "htf_context": [14400, 86400],
      "structure": [900],
      "execution": [300, 60]
    },
    "cross_tf_projection": {
      "opacity_mult": 0.4,
      "border_style": "dotted",
      "border_width": 1,
      "label_prefix": true,
      "fade_when_native_overlaps": 0.2
    }
  }
}
```

#### 3.2.5 Acceptance Criteria (Φ2)

| AC | Given | When | Then |
|----|-------|------|------|
| AC-Φ2-1 | H4 chart, Focus mode | Count M15/M5 elements | = 0 |
| AC-Φ2-2 | M15 chart, H4 OB projected | Visual inspection | Dotted border, 40% opacity, background z-index |
| AC-Φ2-3 | M5 chart, Focus mode | Count non-projected elements | ≤ 5 (POI + target + invalidation + trigger + banner) |
| AC-Φ2-4 | M15 chart, native FVG overlaps H4 OB projection | Visual inspection | H4 OB fades to 20% opacity |
| AC-Φ2-5 | H4 chart, Premium zone visible | Check M5 chart | Premium zone absent |

---

### ═══ Φ3: SCENARIO PRODUCT ═══

> **Мета**: система не просто показує зони — вона формує **торговий сценарій**.
> **Метрика**: трейдер відкриває M15 chart → за 3 секунди знає bias + POI + target + invalidation.
> **Залежності**: Φ0 + Φ1 + Φ2 (сценарій на основі шуму = гірше ніж без сценарію)

#### 3.3.1 Alignment Banner

Завжди видимий на Structure TF (M15) і Execution TF (M5/M1):

**Aligned state** (D1 + H4 + M15 bias однаковий):
```
╔══════════════════════════════════════════════════╗
║ D1 BEARISH ↘ │ H4 BEARISH ↘ │ ALIGNED ✓        ║
║ POI: OB+FVG @5180-5195 │ Grade A+ (9/11)        ║
║ Target: EQ Lows 5080 │ Invalidation: 5200        ║
╚══════════════════════════════════════════════════╝
```

**Conflicting state** (D1 ≠ H4 або H4 ≠ M15):
```
╔══════════════════════════════════════════════════╗
║ D1 BEARISH ↘ │ H4 BULLISH ↗ │ CONFLICTING ⚠    ║
║ POI: OB @5250-5260 │ Grade B (HTF misalign -2)  ║
║ Reduced confidence — wait for resolution          ║
╚══════════════════════════════════════════════════╝
```

**Wait state** (немає POI ≥ A в proximity):
```
╔══════════════════════════════════════════════════╗
║ D1 BEARISH ↘ │ H4 NEUTRAL ─ │ WAIT ⏸           ║
║ No active scenario. Wait for structure.           ║
╚══════════════════════════════════════════════════╝
```

**Правило**: `WAIT` або `CONFLICTING` → жоден entry signal на Execution TF не валідний. Система **активно стримує** від входу.

#### 3.3.2 Bias Determination Algorithm

```python
def determine_bias(tf_s: int, structure: MarketStructure) -> Bias:
    last_bos = structure.last_confirmed_bos
    last_choch = structure.last_confirmed_choch

    if last_choch and last_choch.bar_index > last_bos.bar_index:
        # CHoCH = зміна характеру, нова тенденція
        return Bias.BULLISH if last_choch.direction == 'up' else Bias.BEARISH
    elif last_bos:
        # BOS = продовження тенденції
        return Bias.BULLISH if last_bos.direction == 'up' else Bias.BEARISH
    else:
        return Bias.NEUTRAL

def determine_alignment(d1_bias, h4_bias, m15_bias) -> Alignment:
    if d1_bias == h4_bias == m15_bias and d1_bias != Bias.NEUTRAL:
        return Alignment.ALIGNED
    elif d1_bias == Bias.NEUTRAL or h4_bias == Bias.NEUTRAL:
        return Alignment.WAIT
    elif d1_bias != h4_bias:
        return Alignment.CONFLICTING
    else:  # d1==h4 but m15 differs
        return Alignment.WAIT  # чекаємо на M15 resolution
```

#### 3.3.3 Scenario Object

```python
@dataclass
class TradingScenario:
    symbol: str
    alignment: Alignment           # ALIGNED | CONFLICTING | WAIT
    bias: Bias                     # BULLISH | BEARISH | NEUTRAL
    bias_sources: Dict[int, Bias]  # {86400: BEARISH, 14400: BEARISH, 900: BEARISH}

    primary_poi: Optional[SmcZone]      # найкращий POI (highest grade)
    poi_grade: str                       # "A+" / "A" / "B"
    poi_confluence_score: int            # 0-11
    poi_confluence_breakdown: List[str]  # ["liq_sweep +2", "fvg_after +2", ...]

    target: Optional[LiquidityLevel]     # найближчий target по напряму bias
    invalidation: Optional[float]        # ціна де сценарій скасовується

    state: ScenarioState  # ACTIVE | APPROACHING | TRIGGERED | EXPIRED | WAIT
```

**State machine**:
```
WAIT → ACTIVE (коли з'являється POI ≥ A + alignment)
ACTIVE → APPROACHING (ціна < 2×ATR від POI)
APPROACHING → TRIGGERED (ціна торкнулась POI + M5 ChoCH/BOS trigger)
APPROACHING → EXPIRED (ціна пройшла invalidation)
TRIGGERED → EXPIRED (після entry execution)
```

#### 3.3.4 "Нічого не робимо" як продукт

Це **найважливіший output** системи (Chief Strategist §5.3). Більшість часу правильна відповідь = бездіяльність.

**Сигнали "не торгуємо"**:
- Alignment = WAIT або CONFLICTING
- Жоден POI ≥ grade A в proximity
- Structure = ranging (no clear BOS/ChoCH)
- Ринок закритий

**UI реакція**: чарт чистий (тільки свічки + мінімальна структура). Banner = нейтральний сірий: `"No active scenario. Wait for structure."`

#### 3.3.5 Acceptance Criteria (Φ3)

| AC | Given | When | Then |
|----|-------|------|------|
| AC-Φ3-1 | M15 chart, aligned bias, A+ POI in proximity | 3-second test | bias + POI + target + invalidation видно за 3 секунди |
| AC-Φ3-2 | D1 BEAR + H4 BULL | Banner | "CONFLICTING ⚠" |
| AC-Φ3-3 | No POI ≥ A, alignment WAIT | Chart | Clean — тільки свічки + banner "Wait for structure" |
| AC-Φ3-4 | Counter-trend scenario (M15 bull, HTF bear) | POI scoring | htf_alignment factor = 0, grade reduced by 2 |
| AC-Φ3-5 | M5 chart, active scenario from M15 | Element count | ≤ 5 objects total |

---

## 4. Порядок імплементації

### 4.1 Timeline

```
Φ0: Elimination Engine
├── Φ0-P1: Mitigation detection (zone_lifecycle.py)         ~1 день
├── Φ0-P2: Proximity + TTL + Strength filter                ~1 день
├── Φ0-P3: Budget cap + sort + DisplayFilter.ts             ~1 день
├── Φ0-P4: Config schema + integration + tests              ~1 день
└── Φ0-GATE: AC-Φ0-1..6 pass                               ← checkpoint

Φ1: OB Doctrine
├── Φ1-P1: OB confluence scoring engine                     ~2 дні
├── Φ1-P2: Wick-OB detection + marking rules                ~1 день
├── Φ1-P3: FVG strength scoring + partial fill              ~1 день
├── Φ1-P4: BOS/CHoCH TTL + impulse validation               ~1 день
├── Φ1-P5: enabled_zone_types config + tests                ~1 день
└── Φ1-GATE: AC-Φ1-1..6 pass                               ← checkpoint

Φ2: TF Sovereignty
├── Φ2-P1: TF role model (config + routing)                 ~1 день
├── Φ2-P2: Cross-TF projection semantics (opacity/style)   ~1 день
├── Φ2-P3: Visibility matrix enforcement                    ~1 день
├── Φ2-P4: Overlap detection (HTF fade when native)         ~1 день
└── Φ2-GATE: AC-Φ2-1..5 pass                               ← checkpoint

Φ3: Scenario Product
├── Φ3-P1: Bias determination + alignment algorithm         ~1 день
├── Φ3-P2: Alignment Banner (UI component)                  ~1-2 дні
├── Φ3-P3: Scenario state machine                           ~2 дні
├── Φ3-P4: "Wait for structure" as product state            ~1 день
└── Φ3-GATE: AC-Φ3-1..5 pass                               ← checkpoint
```

### 4.2 Gate Rules

**Кожна фаза — окремий gate.** Фаза не вважається завершеною, поки всі її AC не пройдені. Наступна фаза не стартує до проходження gate попередньої.

**Кожен P-slice — один PATCH.** ≤150 LOC, ≤1 новий файл, changelog запис, self-check 10/10.

### 4.3 Що НЕ входить в ADR-0028

| Елемент | Чому виключений | Коли |
|---------|-----------------|------|
| Breaker Block rendering | Навчальний режим (§3.1.6) | Окремий ADR після Φ1 |
| Mitigation Block rendering | Навчальний режим | Окремий ADR після Φ1 |
| Risk Calculator (click on OB → SL/TP) | Interaction layer, не visualization | Окремий ADR після Φ3 |
| Session markers (killzones) | Research mode feature | Окремий PATCH |
| Smart Alerts | Notification system | Окремий ADR |
| One-Click Trade | Trading interaction | Набагато пізніше |

---

## 5. Наслідки

### 5.1 Що змінюється

| Область | Зміна |
|---------|-------|
| `core/smc/` | Новий файл `zone_lifecycle.py` (pure functions: mitigation, TTL, proximity, budget) |
| `core/smc/` | Розширення `types.py` (SmcZone += mitigated_at, display_state, confluence_score) |
| `core/smc/` | Розширення OB detector (confluence scoring, wick-OB) |
| `ui_v4/src/smc/` | Новий `DisplayFilter.ts` (applies core logic, passes to renderer) |
| `ui_v4/src/smc/` | Розширення `OverlayRenderer.ts` (cross-TF projection styles, opacity mapping) |
| `ui_v4/src/` | Новий компонент `AlignmentBanner` (Φ3) |
| `config.json` | Розширення `smc.display` (budget, TTL, proximity, tf_roles, colors) |
| Tests | ~20–30 нових unit tests (zone_lifecycle, confluence scoring, display filter) |

### 5.2 Що НЕ змінюється

- SmcEngine compute pipeline (ADR-0024) — engine продовжує обчислювати всі зони
- UDS / data pipeline — zero changes
- Derive chain — zero changes
- Існуючі exit gates — zero regression

### 5.3 Вплив на інваріанти

| Інваріант | Вплив | Аналіз |
|-----------|-------|--------|
| I0 (Dependency Rule) | ✅ OK | zone_lifecycle.py = pure (core/smc/), DisplayFilter.ts = UI layer |
| I1 (UDS narrow waist) | ✅ OK | Display filter не торкається UDS |
| I2 (Time geometry) | ✅ OK | TTL в барах, не в часі — інваріант часу не порушений |
| I5 (Degraded-but-loud) | ✅ OK | Приховування зони ≠ degradation (це design intent) |
| S0–S6 (SMC) | ✅ OK | Compute budget не змінюється, тільки display budget |

---

## 6. Rollback

### Per-phase rollback

| Фаза | Rollback |
|------|----------|
| Φ0 | Видалити `zone_lifecycle.py`, `DisplayFilter.ts`. Повернути direct rendering (всі зони). Config: видалити `smc.display` extensions |
| Φ1 | Повернути OB detector до pre-Φ1 (confluence scoring = off). Config: `enabled_zone_types: ["ob", "fvg", "breaker", "mitigation_block"]` |
| Φ2 | Видалити TF visibility matrix. Всі зони рендеряться на всіх TF (поточна поведінка) |
| Φ3 | Видалити AlignmentBanner component. Видалити scenario state machine. Зони без narrative |

### Full rollback

Видалити всі зміни ADR-0028. Повернути rendering до стану "покажи все що знайшов" (pre-ADR-0028). Жодних змін у compute pipeline.

---

## 7. SMC Інваріанти (доповнення до S0–S6)

ADR-0028 вводить **display-level інваріанти** (D-серія):

| # | Інваріант | Enforcement |
|---|-----------|-------------|
| D0 | **Compute ≠ Display**: engine обчислює N зон, UI показує ≤ budget. Зміна budget не впливає на compute | DisplayFilter.ts — єдина точка фільтрації |
| D1 | **Mitigation = CLOSE, not wick**: мітигація лише коли close пробиває протилежну межу | zone_lifecycle.py:is_mitigated() + unit test |
| D2 | **TTL = закон**: зона старша за TTL = hidden у Focus. Без виключень (крім A+/A HTF = fade, not hide) | zone_lifecycle.py:apply_ttl() + unit test |
| D3 | **Budget = cap**: >budget зон на chart = баг S1, не "feature" | DisplayFilter.ts:apply_budget() + unit test |
| D4 | **TF Sovereignty**: елемент нижчого TF на HTF chart = баг S1 | DisplayFilter.ts:apply_tf_visibility() + unit test |
| D5 | **Grade > everything**: A+ зона завжди видна (якщо не mitigated). Grade C зона ніколи не видна у Focus | Budget sort + gate test |

---

## 8. Відкриті питання (для обговорення)

| # | Питання | Варіанти | Рекомендація |
|---|---------|----------|--------------|
| Q1 | Де саме ставити DisplayFilter — server-side (Python) чи client-side (TS)? | A: Python (менше даних по wire) B: TS (інтерактивний Research↔Focus toggle) | **B** — toggle повинен бути миттєвим, без round-trip |
| Q2 | Чи потрібен M30 як окремий TF role? | A: M30 = Structure (як M15) B: M30 = between Structure/HTF | **B** — M30 = secondary structure, показує H1 зони як projections |
| Q3 | Як обробляти "стару H4 A+ зону" — ціна пішла далеко, але зона все ще theoretically valid? | A: TTL все одно прибере B: Proximity все одно прибере C: A+ exempt від обох | **Гібрид**: proximity fade (opacity знижується з відстанню), але не hide повністю |
| Q4 | Alignment: H4 neutral + D1 bearish = WAIT чи BEARISH? | A: WAIT (немає confirmation) B: BEARISH (D1 override) | **A** — WAIT. Без H4 confirmation entry ризикований |

---

## Appendix A: Зв'язок з існуючими ADR

```
ADR-0024  (SMC Engine Architecture)
    ├── ADR-0024a (Self-Audit: F1-F12 fixes)
    ├── ADR-0024b (Key Levels: PDH/PDL/PWH/PWL)
    ├── ADR-0024c (Zone POI Rendering: Z1-Z10)
    │       └── ADR-0028 Φ0 EXTENDS zone lifecycle rules
    └── ADR-0028 (THIS: Clean Chart Strategy)
            ├── Φ0: Elimination Engine (display filter)
            ├── Φ1: OB Doctrine (confluence scoring)
            ├── Φ2: TF Sovereignty (cross-TF projection)
            └── Φ3: Scenario Product (alignment + narrative)

ADR-0026  (Overlay Level Rendering: L1-L6)
    └── ADR-0028 Φ2 USES level rendering rules for liquidity
```

## Appendix B: Повний Display Filter Pipeline (pseudo-code)

```python
def filter_for_display(
    all_zones: List[SmcZone],
    all_levels: List[LiquidityLevel],
    all_structure: List[StructureLabel],
    current_price: float,
    current_bar_index: int,
    chart_tf: int,
    atr: float,
    config: SmcDisplayConfig,
    mode: DisplayMode,  # focus | research | debug
) -> DisplayPayload:

    if mode == 'debug':
        return DisplayPayload(zones=all_zones, levels=all_levels, structure=all_structure)

    budget = config.focus_budget if mode == 'focus' else config.research_budget

    # === ZONES ===
    zones = all_zones

    # F0: Type gate
    zones = [z for z in zones if z.kind in config.enabled_zone_types]

    # F1: Mitigation
    zones = [z for z in zones if not is_fully_mitigated(z, current_bar_index, config)]
    # (partially mitigated = apply fade, keep in pipeline)

    # F2: TF Visibility
    zones = [z for z in zones if is_tf_visible(z.source_tf, chart_tf, config.tf_roles)]

    # F3: Proximity
    for z in zones:
        z.display_opacity = calculate_proximity_opacity(z, current_price, atr, config)
    zones = [z for z in zones if z.display_opacity > 0 or z.grade in ('A+', 'A')]

    # F4: TTL / Decay
    for z in zones:
        z.display_opacity *= calculate_ttl_multiplier(z, current_bar_index, config)
    zones = [z for z in zones if z.display_opacity > 0.01]

    # F5: Strength gate
    if mode == 'focus':
        zones = [z for z in zones if z.strength >= config.min_display_strength]

    # F6: Strength → Opacity
    for z in zones:
        z.display_opacity *= strength_to_opacity(z.strength)

    # F7: Cross-TF projection styling
    for z in zones:
        if z.source_tf != chart_tf:
            z.is_projection = True
            z.display_opacity *= config.cross_tf_projection.opacity_mult

    # F8: Sort + Budget cap
    supply = sorted([z for z in zones if z.side == 'supply'], key=zone_sort_key, reverse=True)
    demand = sorted([z for z in zones if z.side == 'demand'], key=zone_sort_key, reverse=True)
    zones = supply[:budget.zones_per_side] + demand[:budget.zones_per_side]

    # === LEVELS ===
    levels = apply_level_filters(all_levels, chart_tf, current_price, budget, config)

    # === STRUCTURE ===
    structure = apply_structure_filters(all_structure, chart_tf, current_bar_index, budget, config)

    # === BUDGET ENFORCEMENT ===
    total = len(zones) + len(levels) + len(structure)
    if total > budget.total_max:
        # Trim weakest elements until under budget
        zones, levels, structure = enforce_total_budget(zones, levels, structure, budget.total_max)

    return DisplayPayload(zones=zones, levels=levels, structure=structure)
```
