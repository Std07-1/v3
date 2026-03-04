# ADR-0024c: SMC Zone POI — Що показувати, де, коли і навіщо

- **Статус**: Implemented (Phase 2 complete)
- **Дата**: 2026-03-01 (initial), updated 2026-03-01
- **Автор**: AI Agent + трейдер (колаборація)
- **Initiative**: Потік C — SMC Engine (ADR-0024 sub-document)
- **Parent**: ADR-0024 (SMC Engine Architecture)
- **Siblings**: ADR-0024a (Self-Audit), ADR-0024b (Key Levels), ADR-0026 (Level Rendering Rules)

---

## 1. Контекст і проблема

### 1.1 Що таке зони в SMC

У Smart Money Concepts **зони** (Points of Interest, POI) — це _не_ індикатори й _не_ обсяги. Це **сліди інституційних ордерів**: місця, де великий гравець залишив "відбиток" у структурі ціни. Ціна має тенденцію **повертатися** до цих місць (rebalancing, order filling, mitigation).

Зона — це прямокутник `[start_ms → end_ms] × [low → high]`, що описує **цінову область**, де очікується реакція ціни.

### 1.2 Навіщо зони трейдеру

Зони відповідають на три питання SMC-аналізу:

| # | Питання | Що дає зона |
|---|---------|-------------|
| 1 | **Де входити?** | OB/FVG в discount = потенційний long entry; OB/FVG в premium = потенційний short entry |
| 2 | **Де очікувати реакцію?** | Unmitigated OB/FVG = price magnet; partially_filled = ще може дати реакцію |
| 3 | **Де модель інвалідована?** | Mitigation OB (close through) = модель зламана, bias змінюється |

**Без зон**: рівні (PDH/PDL) показують _де_ ціна була, але не показують _де_ інституційний інтерес. Свінги показують _що_ сталось, але не _де_ очікувати наступну реакцію.

**Із зонами**: трейдер бачить повну SMC-модель → структура (bias) + зони (entry) + рівні (targets/invalidation).

### 1.3 Проблема: "все підряд" vs "нічого"

Наліпити всі зони з усіх TF — це шум, а не аналіз. Сховати потенційно важливу зону — це пропущена можливість. Потрібен **аналітичний підхід**: показувати рівно те, що має значення для прийняття рішення на поточному TF.

---

## 2. Класифікація зон

### 2.1 Типи зон (поточна реалізація)

| Kind | Що це | Як утворюється | Стратегічне значення |
|------|-------|----------------|---------------------|
| **`ob_bull`** | Bullish Order Block | Остання bearish свічка перед bullish BOS/CHoCH | **Entry zone** для long: інституційні buy orders. Сильніший якщо створений CHoCH (reversal) |
| **`ob_bear`** | Bearish Order Block | Остання bullish свічка перед bearish BOS/CHoCH | **Entry zone** для short: інституційні sell orders |
| **`fvg_bull`** | Bullish Fair Value Gap | 3-свічковий патерн: gap між high[0] і low[2] | **Imbalance**: ціна ще не заповнила неефективність → price magnet, потенційний entry |
| **`fvg_bear`** | Bearish Fair Value Gap | 3-свічковий патерн: gap між low[0] і high[2] | **Imbalance**: аналогічно для short |
| **`premium`** | Premium Zone | Upper 50% range від SH до SL | **Context filter**: sell setups мають перевагу в premium |
| **`discount`** | Discount Zone | Lower 50% range від SH до SL | **Context filter**: buy setups мають перевагу в discount |

### 2.2 Статуси зон (lifecycle N1)

| Status | Значення для трейдера | Як рендерити |
|--------|----------------------|--------------|
| **`active`** | Ціна ще не торкалась → найсильніша зона, freshest reaction expected | Повна opacity |
| **`tested`** | Ціна вже торкнулась, але не пробила → bounce відбувся, реакція слабша при повторному тесті | Зменшена opacity |
| **`partially_filled`** | FVG: ціна увійшла, але не закрила повністю → може дати реакцію на unfilled portion | Напівпрозорий |
| **`breaker`** | OB пробитий → тепер працює як support/resistance в протилежному напрямку (reversal OB) | Інша стилізація (dashed border) |
| **`mitigated`** | Close вийшов за межу → зона відпрацювала, модель invalid | Не показувати (або ghost) |
| **`filled`** | FVG: повністю закритий → gap заповнено | Не показувати |
| **`fading`** | Strength < 0.15 через decay → зона стара, менш relevanta | Мінімальна opacity |
| **`expired`** | >500 барів → автоматичне видалення | Не показувати |

### 2.3 Чому `strength` — ключовий параметр

`strength` (0.0–1.0) кодує **якість** зони:

- **OB**: `impulse_range / ATR` → наскільки сильно ціна відштовхнулась від OB. Чим сильніший імпульс — тим більше інституційних ордерів
- **FVG**: `gap_size / (2 × ATR)` → наскільки великий gap. Великий gap = сильна неефективність
- **Decay**: з часом strength знижується (config: `decay_start_bars=30`, `decay_fast_bars=150`) — стара зона менш reliable

UI використовує strength для opacity: `fillAlpha = 0.04 + 0.11 × strength`, `borderAlpha = 0.10 + 0.40 × strength`.

---

## 3. Аналітична модель: Context Stack

### 3.1 Чому не "TF-матриця", а "стек контексту"

**Відкинута модель**: фіксована матриця "viewer TF → source TFs" (наприклад, M15 → M15 + M30). Ця модель помилкова, бо:

1. **ICT/SMC workflow — top-down з перескоком**, а не "драбинка" (+1 TF вгору). Трейдер дивиться D1 → знаходить bias → стрибає на H4 → знаходить institutional OB → йде на M15 → шукає entry в тому H4 OB. Він не проходить M30 як "проміжний контекст".

2. **M30 не додає інституційної ваги відносно M15.** Різниця 2× = майже однаковий рівень "гравців". Справжній контекст для M15 — це H4/D1, де видно institutional footprint.

3. **Зони існують у ціновому просторі**, а не в "TF-просторі". H4 OB на рівні 2820 — це POI в ЦІНІ. Він релевантний на будь-якому LTF, якщо ціна поруч.

4. **Трейдер на M5 без контексту — сліпий.** При фіксованій матриці "M5 → тільки M5" трейдер не бачить НІЧОГО з того, заради чого відкрив цей TF.

### 3.2 Context Stack — три шари рішень

Замість матриці — **динамічний стек з 3 шарів**. Кожен шар має обмежений бюджет слотів і чіткий критерій відбору.

#### Три рівні прийняття рішень у SMC

| Рівень | TF | Що дає | Горизонт |
|--------|----|--------|----------|
| **Macro / Institutional** | D1 + H4 | Bias + головні POI де "сидять інституції" | Дні–тижні |
| **Intraday Context** | H1 (+ H4) | Session bias, intraday reaction zones | Години–день |
| **Entry / Execution** | M15 / M5 / M3 | Точний entry в межах HTF POI | Хвилини–години |

M30 не є окремим рівнем — він лише алгоритмічна ланка derive chain. Трейдер не приймає рішення "рівня M30".

#### Context Stack: що бачить трейдер

```
┌─────────────────────────────────────────────────────────────────────┐
│  ШАРИ CONTEXT STACK (для viewer TF = M15/M5/M3)                    │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ LAYER 1: Institutional Anchor                                 │  │
│  │ Source: D1 або H4 (D1 має пріоритет)                          │  │
│  │ Budget: 1 зона                                                │  │
│  │ Відбір: nearest to price → TF weight (D1>H4) → strength      │  │
│  │ Стиль: найжирніша рамка, TF-prefix label, prominent fill     │  │
│  │ Мета: "Де сидять інституції? Де головна реакція?"             │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ LAYER 2: Intraday Context                                     │  │
│  │ Source: H1                                                     │  │
│  │ Budget: 0–1 зона                                              │  │
│  │ Відбір: nearest to price → strength; тільки якщо ≠ L1 зоні   │  │
│  │ Стиль: середня рамка, TF-prefix, medium fill                 │  │
│  │ Мета: "Де intraday реакція? Уточнення institutional POI"     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ LAYER 3: Local Entry POI                                      │  │
│  │ Source: viewer TF (M15 / M5 / M3)                             │  │
│  │ Budget: 0–2 зони                                              │  │
│  │ Відбір: strength × proximity ranking                          │  │
│  │ Стиль: стандартний (як зараз — kind-color + strength-opacity) │  │
│  │ Мета: "Де саме входити? Entry refinement"                     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ BACKGROUND: Premium / Discount                                │  │
│  │ Source: viewer TF                                              │  │
│  │ Budget: always (1 premium + 1 discount)                       │  │
│  │ Стиль: фонова заливка, мінімальний alpha, під усіма POI      │  │
│  │ Мета: "Ціна в premium чи discount? Context filter"            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  TOTAL VISIBLE: max 4 POI зони + P/D фон = ЧИСТИЙ CHART           │
└─────────────────────────────────────────────────────────────────────┘
```

**Бюджет strict**: 1 + 1 + 2 = max 4 POI зони. Це не "обмеження через ліню", це **дизайн для прийняття рішень**: трейдер який бачить 4 зони — діє. Трейдер який бачить 12 зон — паралізований.

#### Context Stack per viewer TF

| Viewer TF | L1: Institutional (1) | L2: Intraday (0–1) | L3: Local (0–2) | P/D |
|-----------|-----------------------|---------------------|------------------|-----|
| **M3/M5** | D1 або H4 zone (nearest) | H1 zone (якщо є) | M3/M5 OB/FVG | viewer TF |
| **M15** | D1 або H4 zone | H1 zone (якщо є) | M15 OB/FVG | viewer TF |
| **M30** | D1 або H4 zone | H1 zone (якщо є) | M30 OB/FVG | viewer TF |
| **H1** | H4 zone (або D1) | — (H1 IS intraday) | H1 OB/FVG | viewer TF |
| **H4** | D1 zone (якщо є) | — | H4 OB/FVG | viewer TF |
| **D1** | — (D1 IS institutional) | — | D1 OB/FVG | viewer TF |

#### Правило асиметрії: HTF → LTF, ніколи навпаки

HTF зони **проектуються вниз** на будь-який LTF chart (бо institutional POI = ціновий рівень, він живе в price space). LTF зони **не піднімаються вгору** (M15 OB на H4 chart = шум, занадто дрібний масштаб).

### 3.3 Критерії відбору зони в кожен шар

#### L1 — Institutional Anchor Selection

1. Pool: всі active/tested зони з D1 + H4 snapshots
2. Proximity gate: `|zone_mid - last_price| < proximity_atr_mult × ATR` (на viewer TF)
3. Rank: `tf_weight × 2 + strength × 1 + proximity_score × 1` де `tf_weight: D1=1.0, H4=0.7`
4. Take: top 1. Якщо pool порожній → L1 = empty (ок, не завжди є institutional zone поруч)

#### L2 — Intraday Context Selection

1. Pool: всі active/tested зони з H1 snapshot
2. Proximity gate: той самий radius
3. Dedup: якщо H1 зона перекривається з L1 зоною >50% по ціні → skip (redundant)
4. Rank: `strength × proximity_score`
5. Take: top 0–1

#### L3 — Local Entry Selection

1. Pool: всі active/tested/partially_filled зони з viewer-TF snapshot
2. Proximity gate: той самий radius (але на viewer TF ATR — дрібніший)
3. Rank: `strength × 0.6 + proximity_score × 0.4` (quality over distance)
4. Take: top 0–2

#### POI Grade (Phase 2–3 extension)

POI Grade = composite score, який **уточнює** ranking всередині кожного шару:

| Grade | Умова | Приклад |
|-------|-------|---------|
| **A+** | OB на CHoCH (reversal) + в discount/premium aligned + HTF bias збігається | Bearish CHoCH OB в premium при bearish H4 bias |
| **A** | OB на BOS + в правильній P/D зоні | Bullish BOS OB в discount |
| **B** | FVG active + в правильній P/D зоні | Bullish FVG в discount |
| **C** | OB/FVG без alignment з P/D | Bullish OB в нейтральній зоні |

**Phase 1**: grade не обчислюється; ranking = strength × proximity (достатньо для MVP).
**Phase 2–3**: grade інтегрується в ranking formula кожного шару.

### 3.4 Що НЕ показувати

| Що | Чому |
|----|------|
| Mitigated/filled зони | Модель відпрацювала, зона не дасть нової реакції |
| Зони далеко від ціни (>5×ATR) | Не актуальні для поточного рішення |
| LTF зони на HTF chart | Шум. M15 OB занадто дрібний для H4 масштабу. Асиметрія: тільки HTF→LTF |
| Зони з висотою >5×ATR | Некоректна детекція або market event — не reliable |
| Expired зони (>500 bars) | Стара зона = інституційні ордери вже виконані |
| FVG менший за min_gap_atr_mult | Noise — маленький gap часто заповнюється випадково |
| >4 POI зон одночасно | Cognitive overload. 4 POI = рішення. 12 POI = параліз |

### 3.5 Майбутнє розширення: Manual Override (варіант C)

Context Stack — це **розумний default**. Але досвідчений трейдер може хотіти перевизначити:

- **Toggle per HTF**: кнопки `[H4]` `[H1]` `[D1]` для ручного включення/виключення HTF зон
- **Override stack budget**: "покажи мені ВСІ H4 зони, не тільки 1"
- **Pin zone**: зафіксувати конкретну зону як anchor незалежно від proximity

Це **Phase 4** — після стабілізації Context Stack як default behavior.

---

## 4. Рішення: Zone Rendering Pipeline

### 4.1 Архітектура (backend → UI)

```
┌─ Backend: per-TF computation (engine.py) ────────────────────┐
│  1. detect_order_blocks() → ob_bull/ob_bear                  │
│  2. detect_fvg()          → fvg_bull/fvg_bear                │
│  3. detect_premium_discount() → premium/discount             │
│  4. _update_zone_lifecycle()  → merge/mitigate/decay/cap     │
│  5. _filter_for_display()     → proximity + height + cap     │
└──────────────────────────────────────────────────────────────┘
  ↓ SmcSnapshot per (symbol, tf_s) — stored in _states dict
  
┌─ Phase 2: Context Stack assembly (smc_runner.py) ────────────┐
│  6. get_snapshot_with_context_stack(symbol, viewer_tf_s):     │
│     L1: 1 zone from D1/H4 (nearest, tf_weight, strength)    │
│     L2: 0-1 zone from H1 (if ≠ L1, nearby)                  │
│     L3: 0-2 zones from viewer_tf (strength × proximity)      │
│     P/D: premium + discount from viewer_tf                   │
│     → merged SmcSnapshot with context_layer attribution      │
└──────────────────────────────────────────────────────────────┘
  ↓ SmcSnapshot.zones (max 4 POI + P/D) → WS frame
  
┌─ UI (OverlayRenderer.ts: renderZones) ──────────────────────┐
│  7. Render order: P/D (bg) → L1 (institutional) → L2 → L3   │
│  8. Layer styling: L1=thick border, L2=medium, L3=standard   │
│  9. Color: zoneColor(kind) — bull/bear aware                 │
│  10. Opacity: strength-based fill/border alpha               │
│  11. Labels (Phase 2): kind + TF + status                    │
└──────────────────────────────────────────────────────────────┘
```

> **Поточний стан (Phase 2 active)**: всі кроки 1–11 реалізовані.
> Context Stack (крок 6) активний: `config.json → smc.context_stack.enabled = true`.
> Трейдер бачить max 4 POI (1 institutional + 1 intraday + 2 local) + P/D background.

```

### 4.2 Backend: Filter Pipeline (поточна реалізація)

**Крок 4 — Zone Lifecycle (`_update_zone_lifecycle`):**

1. **Merge fresh** → active_zones (fresh wins over stale)
2. **FVG eviction** → D-02: якщо FVG зникає з fresh → зона видалена (no resurrection)
3. **Mitigation check** → R-04: за CLOSE, не за wick (conservative: wick touch ≠ mitigation)
4. **Age decay** → strength × 0.97 (gentle) або 0.92 (aggressive після decay_fast_bars)
5. **Expired** → >500 bars → delete
6. **Cap** → `max_zones_per_tf` (default 10), sorted by `_zone_rank` (strongest active first)

**Крок 5 — Display Filter (`_filter_for_display`):**

1. **Proximity** → `abs(price - zone_mid) < proximity_atr_mult × ATR` (15.0 × ATR — wide enough for cross-TF)
2. **Height guard** → FVG/OB: `(high - low) < max_zone_height_atr_mult × ATR` (4.0 × ATR). P/D exempt (naturally wide)
3. **Rank + cap** → `max_display_zones` (default 8) after `_zone_rank` sort

### 4.3 UI: Rendering Rules

#### Z1: Kind-aware кольори

| Kind | Color | Семантика |
|------|-------|-----------|
| `ob_bull` | `#26a69a` (teal) | Bullish institutional zone |
| `ob_bear` | `#ef5350` (red) | Bearish institutional zone |
| `fvg_bull` | `#2ecc71` (green) | Bullish imbalance |
| `fvg_bear` | `#e74c3c` (red) | Bearish imbalance |
| `premium` | `#cc3333` (dark red) | Sell context zone |
| `discount` | `#3399cc` (blue) | Buy context zone |

#### Z2: Strength-based opacity

```

fillAlpha   = 0.04 + 0.11 × strength    // [0.04 .. 0.15]
borderAlpha = 0.10 + 0.40 × strength    // [0.10 .. 0.50]

```

Сильна зона (strength=1.0): виразний fill, чітка рамка.
Згасаюча зона (strength=0.15): ледь помітна, treyдер розуміє що зона стара.

#### Z3: X-extent — від формації до правого краю

Зона малюється від `start_ms` (бар що створив зону) до `end_ms` (бар де статус змінився) або до правого краю chart якщо `end_ms == null` (active зона).

- Якщо `start_ms` зліва за видимою областю → зона починається з x=0
- Якщо `end_ms` правіше видимої області або null → зона закінчується на правому краю chart

#### Z4: Митигована зона — правий край фіксований

Коли зона стає `mitigated`/`filled`, `end_ms` встановлюється = `last_bar.open_time_ms`. Зона "заморожується" і більше не росте вправо.

#### Z5: Premium/Discount — фонова заливка з overlay

P/D зони — це не "entry zones", а **контекстна інформація** (яка половина range?). Тому вони:

- Малюються **під** OB/FVG (render order: premium/discount → ob → fvg)  
- Мають **мінімальний** fill alpha навіть при strength=1.0 (фонова підказка, не перекриває PA)
- **end_ms = null** завжди (поки swing range валідний)

---

## 5. Що реалізовано (Phase 1 + Phase 2)

### 5.1 Phase 1: Zone Detection + Lifecycle + Display (DONE)

| Компонент | Статус | Файл | LOC |
|-----------|--------|------|-----|
| OB detection (BOS/CHoCH-based) | ✅ | `core/smc/order_blocks.py` | 196 |
| FVG detection (3-candle gap) | ✅ | `core/smc/fvg.py` | 150 |
| Premium/Discount (swing range) | ✅ | `core/smc/premium_discount.py` | 114 |
| Zone lifecycle (N1: merge → mitigate → decay → cap) | ✅ | `core/smc/engine.py` | 614 |
| Display filter (proximity + height + cap) | ✅ | `core/smc/engine.py` | — |
| UI rendering (kind-aware color + strength opacity) | ✅ | `ui_v4/.../OverlayRenderer.ts` | 657 |
| Toggle OB/FVG visibility | ✅ | `ChartPane.svelte` | — |

### 5.2 Phase 2: Context Stack + Zone Labels (DONE — 2026-03-01)

| Компонент | Статус | Файл | LOC |
|-----------|--------|------|-----|
| Context Stack engine | ✅ | `core/smc/context_stack.py` | 201 |
| `get_snapshot_with_context_stack()` | ✅ | `core/smc/engine.py` | — |
| WS runner integration | ✅ | `runtime/smc/smc_runner.py` | 235 |
| Zone labels (all zones: kind + TF prefix) | ✅ | `OverlayRenderer.ts` | — |
| Layer-aware styling (border width per layer) | ✅ | `OverlayRenderer.ts` | — |
| Layer-aware render order (P/D → L1 → L2 → L3) | ✅ | `OverlayRenderer.ts` | — |
| Wire format: `tf_s` + `context_layer` on SmcZone | ✅ | `types.py` + `types.ts` | — |
| Config: `context_stack` section in `config.json` | ✅ | `config.json` | — |

#### Phase 2 Bug Fixes (proximity tuning)

| RC | Проблема | Фікс | Файли |
|----|----------|------|-------|
| RC1 | `proximity_atr_mult=5.0` → M1 radius=11.8pt, M15=43.9pt → навіть Discount зона на dist=13.1 відфільтрована (різниця 1.3pt!) | `5.0 → 15.0` | `config.json` |
| RC2 | `collect_htf_zones()` повторно фільтрував HTF зони viewer ATR (H4 OB at dist=95pt killed by M15 radius=43.9pt, хоча зона пройшла власний H4 filter з radius=199pt) | Видалено proximity gate з `_select_poi_zones` — HTF зони вже пройшли `_filter_for_display` | `context_stack.py` |
| RC3 | `_proximity_score` нормалізував через ATR — безглузде cross-TF (M1 ATR=2.37 → score=0 для всіх HTF зон) | Price-relative % (5274/95pt = 1.8% → score 0.64, vs ATR-relative = 0) | `context_stack.py` |

### 5.3 Phase 3: POI Grade + Scoring (PLANNED)

POI Grade інтегрується в ranking formula Context Stack:

1. Config: `smc.poi_scoring.enabled`, `smc.poi_scoring.weights`
2. Score = weighted sum: `w_kind × kind_score + w_alignment × pd_score + w_freshness × age_score`
3. Grade = threshold-based: `A+ ≥ 0.85`, `A ≥ 0.70`, `B ≥ 0.50`, `C < 0.50`
4. Grade badge в кутку зони: `[A+]`, `[A]`, `[B]`, `[C]`
5. Grade-first display: A+ завжди видно навіть якщо далеко від ціни

### 5.4 Phase 4: Manual Override + Zone Interaction (FUTURE)

**Manual Override** (§3.5):
- Toggle per HTF: кнопки `[H4]` `[H1]` `[D1]` для ручного включення/виключення HTF зон
- Override stack budget: "покажи мені ВСІ H4 зони, не тільки 1"
- Pin zone: зафіксувати конкретну зону як anchor

**Zone Interaction**:
- Hover → показує деталі (anchor bar, strength history, n тестів)
- Click → zoom to formation
- Context menu → mark as invalidated (manual override)

---

## 6. Інваріанти (Zone Rendering)

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **Z1** | Kind-aware colors (bull=teal/green, bear=red) | `zoneColor()` в OverlayRenderer.ts |
| **Z2** | Strength-based opacity (0.04–0.15 fill, 0.10–0.50 border) | Inline formula, clamped `[0..1]` |
| **Z3** | Active зони тягнуться до правого краю chart | `end_ms == null → xRight = width` |
| **Z4** | Mitigated зони мають фіксований end_ms | `_update_zone_lifecycle` ставить `end_ms=bar.open_time_ms` |
| **Z5** | Premium/Discount під OB/FVG (render order) | `renderZones()` render order: premium/discount → OB → FVG |
| **Z6** | Context Stack budget: max 4 POI (1 institutional + 1 intraday + 2 local) | Context Stack selection in engine |
| **Z7** | Mitigation by CLOSE, not by wick | `last_bar.c < z.low` (R-04 in lifecycle), NOT `last_bar.low` |
| **Z8** | FVG eviction: якщо зникає з fresh → delete (no ghost resurrection) | D-02 in lifecycle |
| **Z9** | Zone height guard: `(high-low) < max_zone_height_atr_mult × ATR` | P/D exempt; OB/FVG filtered |
| **Z10** | hide_mitigated = configurable (default false) | `config.smc.hide_mitigated` |

---

## 7. Config SSOT (поточний стан)

```jsonc
// config.json → smc
{
  // ── Zone Detection ──
  "ob": {
    "enabled": true,
    "min_impulse_atr_mult": 1.5,    // Мінімальна сила імпульсу (× ATR)
    "atr_period": 14,
    "max_active_per_side": 5,        // Max bull + max bear OBs
    "track_breakers": true           // OB→breaker transition
  },
  "fvg": {
    "enabled": true,
    "min_gap_atr_mult": 0.3,         // Мінімальний gap розмір (× ATR)
    "max_active": 10                  // Max FVG зон
  },
  "premium_discount": {
    "enabled": true
  },

  // ── Zone Lifecycle ──
  "max_zones_per_tf": 10,            // Hard cap per (symbol, tf)
  "max_zone_height_atr_mult": 4.0,   // Max zone height (× ATR)
  "hide_mitigated": true,            // Сховати mitigated зони (production: true)
  "decay_start_bars": 30,             // Start strength decay after N bars
  "decay_fast_bars": 150,             // Switch to aggressive decay

  // ── Display Filter ──
  "display": {
    "proximity_atr_mult": 15.0,      // Show zones within N×ATR of price
                                      //   M1:  15×2.37 = 35.5pt
                                      //   M15: 15×8.78 = 131.7pt
                                      //   H1:  15×20.5 = 307pt
                                      //   H4:  15×39.9 = 599pt
    "max_display_zones": 8,           // Hard cap per TF
    "max_display_swings": 20,
    "max_display_levels": 6
  },

  // ── Context Stack (Phase 2, ACTIVE) ──
  "context_stack": {
    "enabled": true,                  // true = Context Stack routing
    "institutional_budget": 1,        // Max zones from D1+H4 (L1 layer)
    "intraday_budget": 1,             // Max zones from H1 (L2 layer)
    "local_budget": 2                 // Max zones from viewer TF (L3 layer)
  }

  // ── POI Scoring (Phase 3, PLANNED) ──
  // "poi_scoring": {
  //   "enabled": false,
  //   "weights": { "kind": 0.3, "alignment": 0.4, "freshness": 0.3 },
  //   "grade_thresholds": { "A+": 0.85, "A": 0.70, "B": 0.50 }
  // }
}
```

---

## 8. Wire Format (S6)

### SmcZone → WS frame

```typescript
interface SmcZone {
  id: string;        // "{kind}_{symbol}_{tf_s}_{anchor_bar_ms}"
  start_ms: number;  // Left edge (anchor candle open_time_ms)
  end_ms?: number;   // Right edge; undefined = active (extends to right)
  high: number;      // Zone top
  low: number;       // Zone bottom
  kind: string;      // 'ob_bull'|'ob_bear'|'fvg_bull'|'fvg_bear'|'premium'|'discount'
  status?: string;   // 'active'|'tested'|'mitigated'|'partially_filled'|'filled'
  strength?: number; // 0.0–1.0 (impulse quality + age decay)
  tf_s?: number;     // Origin TF (Phase 2: for cross-TF zone identification)
  context_layer?: string; // Phase 2: 'institutional'|'intraday'|'local' (Context Stack layer)
}
```

### SmcDelta → zone changes

```typescript
{
  new_zones: SmcZone[];          // Нові зони
  mitigated_zone_ids: string[];  // IDs зон що стали mitigated + зникли з display
  updated_zones: SmcZone[];      // Зони з оновленим статусом
}
```

---

## 9. Відкинуті підходи

| # | Підхід | Причина відмови |
|---|--------|-----------------|
| 1 | **Показувати всі зони з усіх TF без фільтру** | Шум. 20+ зон = параліз рішення. Context Stack обмежує до 4 POI ||
| 2 | **Фіксована TF-матриця (M15→M30, H1→H4)** | Не відповідає реальному ICT workflow. Трейдер стрибає D1→H4→M15, а не йде по драбинці. M30 не додає інституційної ваги. Context Stack краще |
| 3 | **Ховати зони на мітигації** | Трейдер хоче бачити ЩО сталось (учбовий режим), не тільки що актуальне. `hide_mitigated` = configurable |
| 4 | **Grade scoring в Phase 1** | Overengineering для MVP. Strength + proximity = достатньо для Phase 1 |
| 5 | **Zone labels в Phase 1** | Зони самоочевидні за кольором/формою. Labels = Phase 2 (після стабілізації) |
| 6 | **Mitigation by wick** | R-04: wick touch ≠ institutional close. Close = commitment. Wick = liquidity sweep, зона може ще працювати |
| 7 | **LTF зони на HTF chart** | M15 OB на H4 chart = noise. Зони проектуються тільки вниз (HTF→LTF), ніколи вгору |

---

## 10. Glossary (для онбордингу)

| Термін | Визначення |
|--------|------------|
| **Order Block (OB)** | Остання протилежна свічка перед BOS/CHoCH. Зона де інституційний гравець залишив ордери |
| **Fair Value Gap (FVG)** | 3-свічковий gap (неефективність). Ціна "заборгувала" заповнити цю область |
| **Mitigation** | Ціна ЗАКРИЛАСЬ за протилежною межею зони → інституційні ордери виконані |
| **Premium/Discount** | Верхня/нижня половина поточного swing range. Premium = дорого (sell), Discount = дешево (buy) |
| **Equilibrium (EQ)** | 50% рівень swing range = "справедлива ціна" |
| **BOS (Break of Structure)** | Ціна пробила попередній swing → тренд підтверджений (continuation) |
| **CHoCH (Change of Character)** | Ціна пробила протилежний swing → потенційний розворот (reversal) |
| **Inducement** | Фальшивий пробій (wick за minor level, close повертається) → trap для retail |
| **Breaker** | OB що був пробитий → тепер працює як S/R в протилежному напрямку |
| **Strength** | 0.0–1.0 score: impulse quality (при створенні) × age decay (з часом) |
| **POI Grade** | A+/A/B/C quality score на основі kind + alignment + freshness (Phase 3) |
| **Context Stack** | 3-шарова модель відображення: Institutional (D1/H4) + Intraday (H1) + Local (viewer TF). Max 4 POI |

---

## 11. Наслідки

### Реалізовано (Phase 1 + Phase 2)

**Backend — `core/smc/` (2478 LOC total, 13 Python files):**

| Файл | LOC | Призначення |
|------|-----|-------------|
| `engine.py` | 614 | Оркестратор: `_compute_snapshot()`, lifecycle, display filter, Context Stack merge |
| `types.py` | 212 | SmcZone, SmcSnapshot, SmcDelta, SmcSwing, SmcLevel — wire format |
| `config.py` | 210 | SmcConfig dataclass з усіма секціями (SSOT parsing) |
| `context_stack.py` | 201 | Context Stack: `collect_htf_zones()` L1+L2, `tag_local_zones()` L3, price-relative ranking |
| `order_blocks.py` | 196 | OB detection: BOS/CHoCH → last opposite candle, impulse strength |
| `structure.py` | 169 | Structure events: BOS/CHoCH detection, trend bias |
| `key_levels.py` | 165 | PDH/PDL, HOD/LOD, session levels per TF |
| `inducement.py` | 164 | Inducement (false breakout traps) detection |
| `fvg.py` | 150 | FVG detection: 3-candle gap, fill tracking |
| `liquidity.py` | 142 | Equal Highs / Equal Lows (liquidity pools) |
| `premium_discount.py` | 114 | P/D zones from last swing range |
| `swings.py` | 94 | Raw swing detection + ATR computation |
| `__init__.py` | 47 | Module exports |

**Runtime integration — `runtime/smc/smc_runner.py` (235 LOC):**

- Warmup: reads bars from UDS, feeds to engine per TF
- Live: `on_bar()` callback → engine.update() → SmcDelta for WS frame
- `get_snapshot()` calls `engine.get_snapshot_with_context_stack()` (Phase 2)

**Frontend — `ui_v4/src/chart/overlay/OverlayRenderer.ts` (657 LOC):**

- Zone rendering: kind-aware colors, strength-based opacity, layer-aware border width
- Zone labels: `"{TF} {Kind}"` format (e.g., `H4 OB▲`, `M15 FVG▼`, `Premium`, `Discount`)
- Render order: P/D (background) → institutional → intraday → local
- Level rendering: per-kind styles (LEVEL_STYLES dict), merge on physical overlap, LINE_PX=120
- Swing rendering: diamond markers, bull/bear colors
- Layer isolation: toggles per kind (OB/FVG) independent of levels/swings

**Config — `config.json:smc`:**

- Context Stack: `enabled=true`, `institutional_budget=1`, `intraday_budget=1`, `local_budget=2`
- Display: `proximity_atr_mult=15.0`, `max_display_zones=8`, `hide_mitigated=true`

**Тести**: 6 test files covering detection, lifecycle, runner, liquidity, inducement

**Wire format**: `SmcZone` в `core/smc/types.py` ↔ `SmcZone` в `ui_v4/src/lib/types.ts`:

- Phase 2 additions: `tf_s: number` (always), `context_layer?: string` (Phase 2)

### Поточний стан / обмеження (2026-03-01)

1. **Статичний ринок**: при закритому ринку бачимо останній snapshot. Зони що могли б бути відкриті/мітиговані при русі ціни — залишаються "заморожені". Replay mode (ADR-0017) дасть можливість перевірити поведінку на історичних даних.

2. **Візуальна подача зон**: активні зони тягнуться від формації до правого краю chart. При вузьких LTF (M3/M5) і далеких HTF зонах це створює широкі горизонтальні смуги. Потенційні покращення:
   - Zone fadeout (opacity reduces з відстанню від формації)
   - Max visual width (обрізка зони до N барів вправо)
   - Градієнтна заливка (solid → transparent зліва направо)

3. **D1 обмеження**: лише 8 D1 барів в SSOT → недостатньо для swing detection (потрібно ≥20). D1 зони з'являться після накопичення історії.

4. **FVG detection**: на LTF (M1–M15) FVG=0 через порівняно спокійний ринок (gap size < 0.3×ATR). FVG краще працюють при різких рухах.

5. **OB все mitigated на LTF**: 500 барів M1 = ~8 годин. Більшість OB створених 4–8 годин тому вже мітиговані. Це правильна поведінка — shows fresh zones only.

## 12. Rollback

Phase 1 (поточна): вимкнути через `config.json → smc.ob.enabled=false`, `smc.fvg.enabled=false`, `smc.premium_discount.enabled=false`.

Повне відключення: `config.json → smc.enabled=false`.

## 13. Пов'язані ADR

- **ADR-0024**: SMC Engine Architecture (S0–S6, zone lifecycle N1)
- **ADR-0024a**: SMC Self-Audit (F1–F12, R-04 mitigation rule)
- **ADR-0024b**: Key Levels (horizontal anchors — levels complement zones)
- **ADR-0026**: Overlay Level Rendering Rules (L1–L6 — рендер рівнів, sibling pattern)

---

## 14. Zone Quality Tuning — Horizon 1 (2026-03-02)

> Replay testing виявило 5 системних дефектів S2–S3 які разом створюють ефект
> "технічно правильно, візуально марно". Horizon 1 — мінімальні зміни з максимальним
> візуальним ефектом, без зміни алгоритмічної архітектури.

### 14.0.1 Дефекти та фікси

| ID | Дефект | Severity | Fix |
|----|--------|----------|-----|
| **Q4** | OB zone = full candle range (wicks) → надто вузька, легко мітигується | S2 | `high=max(o,c)`, `low=min(o,c)` — body only |
| **Q1** | Кожен BOS = OB з однаковим strength → CHoCH (reversal) не виділяється | S2 | CHoCH OB отримує `×1.5` strength boost |
| **Q2** | Decay = flat 30 bars start для всіх TF → H4/D1 зони гаснуть за дні | S2 | TF-aware profiles: H4 `×4` slower, D1 `×10` slower; expire 2000/5000 bars |
| **Q3** | FVG min_gap=0.3×ATR → 0 FVG на H1+ при спокійному ринку | S3 | H1+: threshold `×0.5` (effective 0.15×ATR) |

### 14.0.2 Q4: OB Zone = Body (open-close)

**Before**: `high=ob_bar.h, low=ob_bar.low` → zone включає wicks.  
**After**: `high=max(ob_bar.o, ob_bar.c), low=min(ob_bar.o, ob_bar.c)` → body only.

Ефект:
- Zone на 30-60% вужча (M15 XAU: ~$3-5 замість ~$5-10)
- Mitigation потребує close через body, не через wick → зона тримається довше
- Відповідає ICT підходу: "institutional orders sit at the body"

### 14.0.3 Q1: CHoCH OB Strength Boost

CHoCH (Change of Character) = **reversal signal** → OB на розвороті більш значущий ніж continuation BOS.

```python
is_choch = swing.kind in ("choch_bull", "choch_bear")
if is_choch:
    strength = min(1.0, strength * 1.5)
```

Візуально: CHoCH OB = яскравіший fill + товщий border. BOS OB = бліклий.

### 14.0.4 Q2: TF-aware Decay Profiles

| TF | decay_start × | decay_fast × | expire_bars | Ефект |
|----|---------------|-------------|-------------|-------|
| M1–H1 | 1.0 | 1.0 | 500 | Default: ~8 годин M1, ~21 день H1 |
| H4 | 4.0 | 4.0 | 2000 | Start decay після 120 bars (20 днів). Expire ~333 днів |
| D1 | 10.0 | 10.0 | 5000 | Start decay після 300 bars (300 днів). Практично не expire |

```python
_TF_DECAY_PROFILE = {
    14400: (4.0, 4.0, 2000),   # H4
    86400: (10.0, 10.0, 5000), # D1
}
```

### 14.0.5 Q3: FVG Threshold Relaxation for HTF

```python
if tf_s >= 3600:  # H1+
    gap_mult *= 0.5   # effective: 0.3 × 0.5 = 0.15 × ATR
```

Ефект: FVG з'являються на H1/H4 при менших gaps. На HTF кожен FVG більш значущий (institutional level), тому нижчий threshold виправданий.

### 14.0.6 Horizon 2 (planned)

| # | Що | Опис |
|---|----|------|
| S1 | Multi-candle OB | OB = 1-3 свічки перед імпульсом (не тільки остання) |
| S2 | OB quality scoring | CHoCH > BOS > weak BOS. Displacement depth = ×strength |
| S3 | Swing significance filter | Minor swings `range < 1.5×ATR` → не генерувати BOS/OB |
| S4 | Inverse FVG (IFVG) | Partially-filled FVG remainder shown as separate zone |
| S5 | Graduated mitigation | `tested → deep_test (>50% body) → mitigated (close through)` |

---

## 15. Implementation Notes (технічна деталізація алгоритмів)

> Цей розділ описує **як саме** працюють алгоритми в коді. Для розуміння **навіщо** — див. §1–§3.

### 15.1 Повний pipeline обчислення (один `on_bar()` виклик)

```
on_bar(bar: CandleBar) → SmcSnapshot + SmcDelta
  │
  ├── 1. append(bar) до deque (lookback=500)
  ├── 2. _compute_snapshot(symbol, tf_s, bars, state):
  │       │
  │       ├── ATR = compute_atr(bars, period=14)                      [swings.py]
  │       │     → EMA of true_range over 14 bars
  │       │
  │       ├── E1.1: raw_swings = detect_raw_swings(bars, period=5)    [swings.py]
  │       │     → Zigzag: local high/low over ±5 bars window
  │       │
  │       ├── E1.2: classified = classify_swings(raw_swings)          [structure.py]
  │       │     → HH, HL, LH, LL classification + confirmed flag
  │       │     → detect_structure_events(classified, bars)
  │       │     → BOS (continuation) / CHoCH (reversal) events
  │       │     → trend_bias: "bullish" | "bearish" | null
  │       │
  │       ├── E1.3: ob_zones = detect_order_blocks(                   [order_blocks.py]
  │       │           bars, struct_events, config, atr)
  │       │     → Для кожного BOS/CHoCH:
  │       │       a) impulse_range = max(h) - min(low) за 20 барів до події
  │       │       b) impulse_strength = impulse_range / ATR
  │       │       c) Фільтр: impulse_strength >= min_impulse_atr_mult (1.5)
  │       │       d) OB = остання ПРОТИЛЕЖНА свічка перед імпульсом
  │       │       e) strength = impulse_strength / (min_impulse × 3), capped at 1.0
  │       │       f) _update_ob_status: active→tested→mitigated по CLOSE (R-04)
  │       │
  │       ├── E1.4: fvg_zones = detect_fvg(bars, config, atr)        [fvg.py]
  │       │     → Для кожної трійки (b0, b1, b2):
  │       │       Bullish: b0.high < b2.low → gap = b2.low - b0.high
  │       │       Bearish: b0.low > b2.high → gap = b0.low - b2.high
  │       │       Фільтр: gap >= min_gap_atr_mult × ATR (0.3)
  │       │       strength = gap / (2 × ATR), capped at 1.0
  │       │       _update_fvg_status: active→partially_filled→filled
  │       │
  │       ├── E2.6: pd_zones = detect_premium_discount(               [premium_discount.py]
  │       │           classified, bars, config)
  │       │     → Знаходить останній confirmed SH та SL
  │       │     → Equilibrium = (SH + SL) / 2
  │       │     → Premium: [equilibrium, SH] — верхня половина
  │       │     → Discount: [SL, equilibrium] — нижня половина
  │       │     → strength = 1.0 (P/D не згасають)
  │       │
  │       ├── N1: _update_zone_lifecycle(                              [engine.py]
  │       │         fresh, active_zones, last_bar, config, tf_s)
  │       │     → 1. Merge fresh → active_zones (fresh wins)
  │       │     → 2. D-02: FVG not in fresh_ids → evict (no resurrection)
  │       │     → 3. R-04: mitigation by bar.CLOSE, not wick
  │       │         bull zone: bar.c < zone.low → mitigated
  │       │         bear zone: bar.c > zone.high → mitigated
  │       │     → 4. Age decay: >30 bars → ×0.97; >150 bars → ×0.92
  │       │     → 5. >500 bars → expired, delete
  │       │     → 6. hide_mitigated=true → remove mitigated from result
  │       │     → 7. Cap: max_zones_per_tf (10), sorted by _zone_rank
  │       │
  │       ├── Levels: detect_liquidity_levels + compute_key_levels     [liquidity.py, key_levels.py]
  │       ├── Inducement: detect_inducement                            [inducement.py]
  │       │
  │       └── D1: _filter_for_display(snap, bars, config, atr)        [engine.py]
  │             → Proximity: |price - zone_mid| < 15.0 × ATR
  │             → Height guard: (high - low) < 4.0 × ATR (OB/FVG only; P/D exempt)
  │             → Rank + cap: max_display_zones (8)
  │
  ├── 3. diff(prev_snapshot, new_snapshot) → SmcDelta
  └── 4. store snapshot + delta в _TfState
```

### 15.2 Context Stack assembly (`get_snapshot_with_context_stack`)

```
get_snapshot_with_context_stack(symbol, viewer_tf_s)
  │
  ├── base = get_snapshot(symbol, viewer_tf_s)  // вже з display filter
  │
  ├── HTF Key Levels (ADR-0024b):
  │     collect_htf_levels() → D1/H4/H1 рівні (PDH/PDL, session H/L)
  │     → merged до base.levels
  │
  ├── Context Stack Zones (ADR-0024c):
  │     ├── ATR = compute_atr(viewer_bars, period=14)
  │     │
  │     ├── collect_htf_zones(get_snapshot, symbol, viewer_tf, price, atr):
  │     │     │
  │     │     ├── L1 pool: D1/H4 snapshots → _select_poi_zones():
  │     │     │     → filter: OB/FVG only (no P/D), active/tested/partially_filled
  │     │     │     → (proximity gate REMOVED — RC2 fix)
  │     │     │     → rank: tf_weight×2 + strength + proximity_score
  │     │     │       (D1 weight=1.0, H4=0.7; proximity = price-relative %)
  │     │     │     → take: top 1 → tag context_layer="institutional"
  │     │     │
  │     │     └── L2 pool: H1 snapshot → same filter:
  │     │           → dedup: skip if >50% price overlap with any L1 zone
  │     │           → rank: strength×0.6 + proximity×0.4
  │     │           → take: top 0-1 → tag context_layer="intraday"
  │     │
  │     └── Local zones: tag_local_zones(base.zones):
  │           → OB/FVG → context_layer="local"
  │           → P/D → no tag (background)
  │
  └── merged = htf_zones + local_zones → SmcSnapshot.zones
```

### 15.3 Proximity Score: ATR-relative vs Price-relative

**Проблема (RC3)**:  
Стара формула: `score = 1 - |zone_mid - price| / (5 × ATR)`.  
На viewer M1 (ATR=2.37): H4 OB на dist=95pt → `95 / 11.85 = 8.0` → `1 - 8.0 = -7.0` → clamped to 0.  
Всі HTF зони отримують score=0 → ranking зламаний.

**Рішення (price-relative %)**:  

```python
dist_pct = |zone_mid - price| / price × 100  # у відсотках
score = max(0, 1.0 - dist_pct / 5.0)         # 0% = 1.0, 5% = 0.0
```

Приклад: H4 OB at 5172 vs price 5274 → dist=102pt → `102/5274 = 1.9%` → `1 - 0.39 = 0.61`.  
Масштабується природно: XAU/USD ~5200 → 5% radius = 260pt = десятки H4 барів.

### 15.4 Zone Rank (deterministic sort)

```python
_zone_rank(z) = (-z.strength, status_order, -z.anchor_bar_ms, z.id)
```

- Найсильніша зона — перша
- При рівній strength: active > tested > partially_filled > breaker > mitigated
- При рівному status: найновіша зона першa
- Tiebreaker: zone ID (deterministic)

### 15.5 Zone Lifecycle State Machine

```
                    ┌──────────────────────────┐
                    │       detect_*()          │
                    │   (OB/FVG/P-D detection)  │
                    └────────────┬─────────────┘
                                 ▼
                           ┌──────────┐
                    ┌──────│  active   │──────┐
                    │      └──────────┘      │
                    │           │ bar enters  │ bar.c crosses opposite edge
                    │           ▼             │ (R-04: CLOSE, not wick)
                    │    ┌──────────┐         │
                    │    │  tested  │─────────┤
                    │    └──────────┘         │
                    │                         ▼
     ┌──────────────┼───────────────┬──────────────┐
     │ FVG only:    │ >500 bars:    │              │
     ▼              ▼               ▼              │
┌──────────┐  ┌──────────┐  ┌──────────────┐      │
│ partially│  │  expired  │  │  mitigated   │      │
│  _filled │  │  (delete) │  │ (end_ms set) │      │
└────┬─────┘  └──────────┘  └──────────────┘      │
     │                                             │
     │ bar.c closes through                        │
     ▼                                             │
┌──────────┐                                       │
│  filled  │◄──────────────────────────────────────┘
│(FVG only)│       (FVG not in fresh → D-02 evict)
└──────────┘
```

**Decay curve**: strength ×0.97 per bar після 30 барів; ×0.92 per bar після 150 барів.  
При strength < 0.15 → зона візуально ледь помітна (opacity ~0.06 fill).

### 15.6 UI Rendering Pipeline (`OverlayRenderer.ts`)

```
renderZones(zones, series, chartW, chartH):
  │
  ├── 1. Sort by render order:
  │       P/D (background) → institutional → intraday → local
  │  
  ├── 2. For each zone:
  │     ├── xLeft  = timeToCoordinate(start_ms / 1000)
  │     │            (clamp to 0 if off-screen left)
  │     ├── xRight = timeToCoordinate(end_ms / 1000)  // if end_ms set
  │     │            OR chartW                          // if active (end_ms null)
  │     │            (NOT width — excludes price scale area)
  │     │
  │     ├── yTop   = priceToCoordinate(high)
  │     ├── yBot   = priceToCoordinate(low)
  │     │
  │     ├── Color  = zoneColor(kind):
  │     │     ob_bull/fvg_bull → teal/green
  │     │     ob_bear/fvg_bear → red
  │     │     premium → dark red, discount → blue
  │     │
  │     ├── Alpha  = f(strength):
  │     │     fillAlpha   = 0.04 + 0.11 × strength    [0.04..0.15]
  │     │     borderAlpha = 0.10 + 0.40 × strength    [0.10..0.50]
  │     │     P/D override: fillAlpha×0.35, borderAlpha×0.35
  │     │
  │     ├── Border = f(context_layer):
  │     │     institutional → 2.5px
  │     │     intraday     → 1.5px
  │     │     local        → 1.0px
  │     │     P/D          → 0.5px
  │     │
  │     └── Label:
  │           text = "{TF} {Kind}" (e.g. "H4 OB▲", "M15 FVG▼")
  │           P/D:  fontSize=8, alpha=0.35
  │           POI:  fontSize=9, alpha=0.55
  │           Position: top-left corner of zone
  │
  └── 3. Canvas operations:
        ctx.fillRect(xLeft, yTop, xRight-xLeft, yBot-yTop)
        ctx.strokeRect(...)
        ctx.fillText(label, xLeft+4, yTop+12)
```

### 15.7 Wire Format Flow

```
Backend (Python)                    Frontend (TypeScript)
──────────────────                  ─────────────────────
core/smc/types.py                   ui_v4/src/lib/types.ts
  SmcZone dataclass        ←S6→      SmcZone interface
  SmcSnapshot dataclass    ←S6→      SmcSnapshot interface
  SmcDelta dataclass       ←S6→      SmcDelta interface

runtime/smc/smc_runner.py           ui_v4/src/chart/overlay/
  get_snapshot() →                   OverlayRenderer.ts
    JSON serialize →                   parse zones/levels/swings →
      WS frame (port 8000) →            renderZones() / renderLevels()
```

**SmcZone fields** (Phase 2):

```
id:             "{kind}_{symbol}_{tf_s}_{anchor_bar_ms}"
symbol, tf_s:   origin (завжди)
kind:           "ob_bull"|"ob_bear"|"fvg_bull"|"fvg_bear"|"premium"|"discount"
start_ms:       left edge (formation bar open_time_ms)
end_ms:         right edge (mitigation bar) або null (active)
high, low:      price range
status:         "active"|"tested"|"partially_filled"|"mitigated"|"filled"
strength:       0.0–1.0 (detection quality × age decay)
anchor_bar_ms:  formation bar ms (for age calc)
context_layer:  "institutional"|"intraday"|"local"|undefined(P/D)  ← Phase 2
tf_s:           source TF seconds (60–86400)                       ← Phase 2
```
