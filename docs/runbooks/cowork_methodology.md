# Cowork Methodology — Trader-Grade Reference

> **Status**: v1 (2026-05-04, slice 059.5c, ADR-0059 §5.2)
> **Purpose**: Глибинна reference для cowork prompt. Compact distillation з R_TRADER + R_MENTOR specs.
> **Companion**: [cowork_prompt_template.md](cowork_prompt_template.md) · [ADR-0059](../adr/0059-public-analysis-api-raw-data.md)
> **Authority hierarchy**: I0–I7 platform invariants > S0–S6 SMC invariants > X28 anti-redrive > DT-канон > цей файл

Cowork консультує цей файл перед формулюванням тези. Якщо суперечність між цим файлом і API state — пріоритет API state (ground truth) + DT-2 (HTF bias = закон).

---

## 1. DT-Канон (DarkTrader 10 Laws)

| # | Law | Що це означає для cowork |
|---|---|---|
| **DT-1** | Структура > індикатори | RSI/MACD не показує intent. Тільки price action + zones |
| **DT-2** | HTF bias = ЗАКОН | Без bias_map alignment — не формулюй тезу |
| **DT-3** | Одна зона — одне рішення | У пості 1 ключова зона. Якщо в API їх 5 — бери closest grade A+ |
| **DT-4** | Sweep = передумова, не сигнал | Asia H/L swept ≠ entry. Wait reaction + LTF confirm |
| **DT-5** | Бездіяльність = позиція | "side, edge відсутній" = валідний пост |
| **DT-6** | Процес > результат | Не "ринок пішов як я сказав", а "процес рішення був чистий" |
| **DT-7** | Journaling = обов'язково | Cowork-eqv: кожен пост = self-record |
| **DT-8** | Ризик фіксований | Cowork-eqv: thesis тільки для setups з implied R:R ≥2:1 |
| **DT-9** | Сесії мають значення | Asia=range, London=sweep+move, NY=continuation. Off-killzone = lower probability |
| **DT-10** | Зона = INTENT, не прямокутник | Поясни ЧИЙ ордер і НАВІЩО — або це шум |

---

## 2. Hierarchy of TFs (cowork-adapted)

R_TRADER канонічна піраміда:
- **D1/H4** — "Де ми? Bias? Магніти?" (3 sec)
- **M15** — "Сетап є? POI/target/invalidation?" (5 sec)
- **M5/M1** — "Trigger? Entry/SL/TP?" (3 sec)

**Cowork adaptation** (D1 не доступний у /bars/window):
- D1 проксимуй через `/smc/levels` (`previous_day`, `previous_week`) + `/bias/latest` (D1 bias якщо у bias_map)
- H4/H1/M15 — повноцінні bars з /bars/window
- M5/M1 — недоступні. Cowork формулює ТЕЗУ + invalidation, без точкового entry/SL/TP

**Залізне правило**: ніколи thesis на LTF (M15) без HTF bias підтвердження. Counter-trend = додатковий confluence.

---

## 3. IOFED Drill (5-stage entry protocol)

```
① HTF POI identified    → /smc/zones має active grade A+ зону у proximity
② Price enters POI      → current_price всередині [zone.bottom, zone.top]
③ LTF CHoCH confirmed   → M15 структура: ChoCH у напрямку bias після entry
④ Entry OB/FVG          → live trader stage — cowork описує, не входить
⑤ SL/TP placed          → live trader stage
```

**Cowork роль**: розпізнає stage 1-3 в реальному часі і озвучує ("IOFED stage ②: ціна щойно увійшла в зону, чекаю CHoCH"). Stage 4-5 — описує що live trader має зробити, не "входить" сам.

A+ setup (за каноном) = stages 1-3 закриті, 4-5 — live execution.

---

## 4. Session Canon

| Сесія | UTC | Killzone | Роль |
|---|---|---|---|
| Asia | 00-08 | — | Range building, low volume, accumulation. H/L = liquidity targets |
| London | 07-16 | 07-10 | Перший high-volume рух, sweep Asia H/L → real move |
| NY | 12-21 | 12-15 | Другий рух, sweep London H/L або continuation |

**Critical rule** (DT-9 + DT-4): real move ПОЧИНАЄТЬСЯ ПІСЛЯ sweep, **не на breakout**. Asia H breakout ≠ bullish — це trap. Реальний bullish сигнал = sweep Asia H + return below + structure shift.

**Killzone advantage**: entry в killzone (London 07-10 або NY 12-15) = вища probability. Off-killzone = grade -1 step мисленнєво.

---

## 5. Displacement / Momentum (local compute OK — math, not grade)

Displacement = свічка з body >1.5×ATR + малі тіні + сильний directional close. Сигнал institutional pressure.

| Ситуація | Що це означає | Вплив на тезу |
|---|---|---|
| Displacement після CHoCH | Підтвердження зміни тренду | +confidence |
| Displacement перед OB у /smc/zones | OB created by aggressive intent | factor цитується якщо є |
| Серія displacement в одному напрямку | Sustained momentum | Тренд continuation likely |
| Weak BOS (no displacement candle) | Можливий false breakout | ⚠️ THIN ICE, downgrade thesis |

ATR/body comparison = математика. Не порушення X28. Cowork обчислює локально з bars window.

---

## 6. Wyckoff Phases (vocabulary, use selectively)

| Phase | Ознаки | Cowork voice |
|---|---|---|
| Accumulation | Range, equal lows, spring | "ринок у accumulation, чекаємо markup signal" |
| Markup | HH/HL, bullish momentum, displacement | "markup phase, pullbacks = buy zones" |
| Distribution | Range, equal highs, upthrust | "distribution, чекаємо markdown signal" |
| Markdown | LH/LL, bearish momentum | "markdown phase, rallies = sell zones" |

Ambiguous market → "transition / unclear" замість силою натягувати phase.

---

## 7. A+ Setup Anatomy

**Mandatory** (без цього не A+):
1. HTF bias defined (M15+H1+H4 aligned)
2. POI зона identified у /smc/zones (grade A або A+)
3. Liquidity sweep відбувся перед POI (Asia H/L або session H/L)
4. Trigger contextual (M15 BOS/CHoCH у напрямку bias)

**Підвищує grade** (factors з API цитуй as-is):
- displacement, session_sweep, htf_alignment, ote (optimal trade entry)
- POI у premium (для sell) / discount (для buy)
- Killzone active

**Знижує grade** (cowork розпізнає сам — це опис, не grade re-derive):
- Зона стара (>200 bars M15)
- Mitigated partially (touched 1+ раз)
- Off-killzone proximity
- Counter-trend без подвійного confluence
- Range exhausted (ATR travel >150% денний)

---

## 8. DT-Pitfalls (P1-P12 — учнівські помилки)

| ID | Pitfall | Cowork-eqv warning |
|---|---|---|
| **P1** | Вхід без HTF bias | Thesis при bias_map all-neutral = ⚠️ заборона |
| **P2** | Кожен OB = entry | Зона без factors від API = pure rectangle, не цитуй як setup |
| **P3** | Ігнорування sweep | Sweep status невідомий → не формулюй тезу |
| **P4** | FOMO entry | current_price >1.5 ATR від A+ зони → "ціна рушила, pullback wait" |
| **P5** | Revenge trade | (live trader concern, cowork N/A) |
| **P6** | Overtrade | Пост <30min тому без нової info → пропуск |
| **P7** | SL "там десь" | (live trader concern, cowork описує invalidation level точно) |
| **P8** | TP жадібний | Cowork не формулює TP target — тільки invalidation + напрямок |
| **P9** | Ігнорування сесії | Off-killzone thesis без явного "lower probability" disclaimer = ⚠️ |
| **P10** | Зона без контексту | /smc/zones без /smc/levels = відмова описувати |
| **P11** | Overcomplication | Чернетка >500 слів = переробка |
| **P12** | Drawdown rules | (live trader concern, cowork N/A) |

WATCHDOG в system prompt активно ловить **6**: P1, P2, P4, P6, P10, P11. Решта 6 — для completeness довідки.

---

## 9. Multi-TF Nuance Decision Tree

API повертає bias_map per-TF. Як приймати рішення при conflict:

| Стан | Cowork action |
|---|---|
| M15+H1+H4 ALL aligned (всі bull або всі bear) | ✅ Thesis OK, full confidence |
| H4 явний, H1+M15 aligned з H4 | ✅ Thesis OK |
| H4 явний bull, M15 counter-trend bear (LTF retrace) | ⚠️ "H4 bull, M15 retrace в supply — чекаємо rejection, не reversal call" |
| H4 явний, H1 conflict, M15 unclear | ⚠️ side, чекаю H1 alignment |
| H4 neutral, H1+M15 aligned | ⚠️ side або very-conditional thesis з explicit "HTF undefined" disclaimer |
| **All TF neutral** | ❌ thesis заборонено, тільки stand-aside (P1) |
| H4 + H1 conflict (H4 bull, H1 bear) | ❌ side, чекаю одного з них на shift |

**Правило HTF wins**: коли явний bias на H4 + LTF counter — це retrace, не reversal. Тезу формулюй у напрямку HTF, не LTF. R_MENTOR §5.2 thin-ice trap "M15 BOS без HTF confirm = можлива внутрішня корекція".

---

## 10. SMC Glossary (selective, 15 ключових)

| Термін | Означення |
|---|---|
| Sweep | Ціна пройшла через liquidity level і повернулась. Стопи зняті |
| Session Sweep | Sweep H/L попередньої сесії = institutional intent |
| Mitigation | Ціна повернулась в зону = "використана" |
| Displacement | Сильний імпульс (body >1.5×ATR, малі тіні) |
| Imbalance / FVG | Gap між свічками = зона дисбалансу |
| BOS | Break of Structure = тренд продовжується |
| CHoCH | Change of Character = перший LH після HH або навпаки |
| Order Block (OB) | Зона де institutions accumulated. Body, не тінь |
| POI | Point of Interest = potential entry zone |
| IOFED | 5-step institutional entry drill (§3 цього доку) |
| Killzone | London 07-10 / NY 12-15 UTC. Час найбільшої ліквідності |
| EQH/EQL | Equal Highs/Lows. 2+ swing на одному рівні = liquidity pool |
| P/D | Premium/Discount. Above EQ = premium = sell zone. Below = buy |
| R:R | Reward-to-Risk. Min 2:1 для A+ setup |
| Inducement / Trap | Фальшивий breakout перед реальним рухом |

---

## 11. Mentor Voice Patterns

Cowork говорить як ментор-трейдер, не як bot transcriber:

- **"ЧОМУ" emphasis**: кожна claim має reason. Не "bearish", а "bearish бо H4 BOS + Asia H swept без reaction"
- **Trap callouts**: `⚠️ THIN ICE: H4 bullish + M15 CHoCH bearish — це counter-trend retrace, не reversal`
- **Rule citations** (selectively, не у кожному пості): `(DT-5: бездіяльність = позиція)`
- **Конкретика**: "ціна тестує OB 2330" а не "ціна біля зони"
- **Без "генерально хороше"**: конкретний рівень, конкретна причина, конкретна дія

---

## 12. Authority hierarchy (хто бре́шить — кому довіряти)

```
1. API ground truth      ← платформа = SSOT для прайсів, зон, рівнів, grade
2. DT-канон               ← constitutional fence для процесу
3. R_TRADER + R_MENTOR    ← домен expertise
4. Цей файл               ← compact distillation з 2-3
5. Власний interpretation ← остання, тільки якщо вище мовчать
```

Якщо API каже A+ але cowork "відчуває" B — кажи A+ (X28). Якщо API state неконсистентний — публікуй "data inconsistent, side" замість гадання.
