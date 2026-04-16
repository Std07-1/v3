# SYSTEM MATURITY LADDER — Trading Platform v3

> **Призначення**: north star для стратегічного планування. Відповідає на питання "наскільки ми близько до TradingView / інституційного рівня?".
> **Відрізняється** від [AMBITION_LADDER.md](AMBITION_LADDER.md):
> - **Ambition (R0-R5)** = якість **кожної зміни** (short-term, per-patch)
> - **Maturity (M0-M7)** = потужність **системи в цілому** (long-term, per-quarter)
>
> Щоб піднятись M3→M4 треба серію R4+ patches. Market Regime Engine не збереш з R2 коду.

---

## TL;DR

```
M0  Screener            ← примітив, "хтось інший мав би це зробити"
M1  Indicator analysis
M2  Chart + pipeline    ← ~2025 Q4
M3  Multi-TF SMC+score  ← СЬОГОДНІ (ADR-0024/0028/0029/0040)
M4  Market Regime Engine
M5  Market State Understanding
M6  Autonomous decision-making agent
M7  TradingView-grade product   ← north star
```

---

## Рівні системи

### M0 — Screener (примітив)
**Capability**: список символів + базові критерії (RSI > X, crossover).
**Architecture**: single-file script, CSV export, no real-time.
**Signal**: "я лише читаю дані, нічого не розумію".
**Typical systems**: stock screener на сайті брокера.

---

### M1 — Indicator Analysis System
**Capability**: OHLCV pipeline + TA indicators (MA, MACD, RSI, ATR) + alerts.
**Architecture**: окрема data layer, indicator engine, basic UI/notifications.
**Signal**: "бачу рух цін, але не розумію контекст".
**Gaps**: нема structure, нема multi-TF, false signals часті.

---

### M2 — Chart + Stable Pipeline
**Capability**: real-time candles на графіку + drawing tools + multi-TF switch + stable ingest.
**Architecture**: UDS-like narrow waist, preview/final separation, WebSocket UI, supervised processes.
**Signal**: "платформа працює 24/7, але аналіз = manual".
**v3 history**: ~2025 Q4 (ADR-0001 UDS, ADR-0011 WS).
**Typical systems**: OpenSource trading UI (TradingView-lite clones).

---

### M3 — Multi-TF SMC + Confluence Scoring **← СЬОГОДНІ**
**Capability**:
- Swings, structure (BOS/CHoCH), OB, FVG, liquidity, P/D — все автоматично per TF
- Multi-TF context (D1→H4→M15 cascade)
- Confluence scoring (8 factors → A+/A/B/C grade)
- Session & killzones
- Elimination engine (display budget, не показуємо все)
- TDA cascade (daily signal engine)

**Architecture**: `core/smc/` pure logic + `runtime/smc/` I/O wiring + S0-S6 invariants + config SSOT.
**Evidence**: ADR-0024 + 0028 + 0029 + 0035 + 0040 + 0047. 54 test files.
**Signal**: "система бачить те що бачить досвідчений SMC трейдер".
**Gaps що блокують M4**:
- Нема **market regime** (консолідація? тренд? distribution?)
- Нема **volatility regime** (VIX-like для XAU)
- Нема **correlation context** (DXY, US10Y vs XAU)
- Confluence — статичний, не залежить від режиму

---

### M4 — Market Regime Engine
**Capability**:
- Класифікація ринкового режиму: Trend / Range / Squeeze / Expansion / Distribution
- Volatility regime: Low / Normal / High / Extreme
- Adaptive scoring: ті ж confluences дають різний grade у різних режимах
- Session flow: що робив NY → що очікувати в Asia
- **Рекомендація**: "avoid trades", "aggressive entries", "half-size"

**Architecture**: новий `core/regime/` layer + runtime wiring + UI regime badge.
**Required ADRs**: ~3-5 (Regime detection methodology, Volatility classifier, Adaptive scoring weights, Correlation context).
**Required rungs**: всі R3+, хоча б один R4.
**Signal**: "система не просто бачить зони — вона розуміє в якому ми ринку".
**Estimated effort**: 2-4 квартали focused work.

---

### M5 — Market State Understanding
**Capability**:
- Narrative engine: "що відбувається прямо зараз" + "що може статись далі"
- Scenario tree: bull case / bear case / chop case з weighted probabilities
- News/fundamental context integration (economic calendar, macro events)
- Cross-asset reasoning (SPX ↓ + DXY ↑ + gold ↑ = risk-off, impact на XAU differs)
- Self-calibration: система порівнює прогнози з фактом, оновлює weights

**Architecture**: `core/narrative/` + `core/scenarios/` + external data adapters + feedback loop.
**Required ADRs**: ~5-8.
**Signal**: "система формулює thesis на рівні junior/mid trader".
**Gaps що блокують M6**: рішення приймає людина, система лише пропонує.

---

### M6 — Autonomous Decision-Making Agent
**Capability**:
- Agent (Архі-like) приймає рішення: wait / scan / alert / enter / exit / size
- Transparent reasoning: кожне рішення має explanation + confidence
- Self-governed budget (API cost, risk budget, position budget)
- Discipline enforcement: agent refuses overtrading, session-break respect
- Mentorship loop: agent пояснює власні помилки після trade review

**Architecture**: trader-v3 + platform tight integration (Wake Engine ADR-0049/039 — вже заготовлено), directives layer, observation router.
**Invariant**: I7 Autonomy-First (ADR-024 trader-v3) — код = advisory, agent = decision-maker.
**Required ADRs**: багато, включно з safety (kill switch, hard cap) і cross-repo governance.
**Signal**: "людина може відключитись на день — агент працює безпечно".

---

### M7 — TradingView-Grade Product
**Capability**:
- Все з M6 +
- UI якість: premium, smooth, Awwwards-grade (ADR-0036 shell — початок цього шляху)
- Ecosystem: саab-able indicators / strategies / alerts (якщо relevant)
- Performance: ms latency end-to-end, 60fps rendering
- Reliability: 99.9% uptime, graceful degradation
- **Differentiator**: те в чому ми **кращі** за TV (наприклад — autonomous agent, якого у TV немає)

**Signal**: "можна показати людині з індустрії — вона поважатиме".
**Це не фінальна станція** — M8+ теж існує (TV теж не ідеал).

---

## Де ми зараз

**Current: M3 (achieved), орієнтир M4 (Market Regime Engine)**

✅ M0-M3 = foundation complete
⏳ M4 = наступна велика ціль, потребує окремого roadmap
🎯 M5-M7 = long-term vision (рік+)

---

## Governance interlock

### Кожен ADR має вказувати
```markdown
## Maturity Impact
- Current system rung: M3
- This ADR targets: M3 (consolidates) / M4 (elevates)
- Required to reach M4: [X] ADRs remaining
```

### R_ARCHITECT mandate
- ADRs що не рухають систему вгору = **maintenance** (acceptable, але не strategic)
- ADRs що рухають систему вгору = **strategic** (пріоритет планування)
- Якщо за квартал 0 strategic ADRs → **maturity drought** → R_ELEVATOR audit

### R_ELEVATOR monthly audit — додана метрика
```
Maturity movement цього місяця:
- Strategic ADRs: N (які рухають M3→M4)
- Maintenance ADRs: N
- Ratio: X% strategic (healthy: 20-40%)
```

---

## Anti-patterns

- ❌ "Додамо ще один indicator" без зв'язку з M4 path → M3 plateau
- ❌ Рухатись M3→M4→M5 одночасно → focus collapse
- ❌ Скіпати M4 щоб одразу M5 (narrative без regime = галюцинація)
- ❌ M7 без M6 (красивий UI без розумного agent = порожня обгортка)
- ❌ Claim'ити "ми на M4" без M4 ADR-артефактів → self-deception

---

## Порівняння з TradingView

Неочевидна правда: **TradingView — це M2+M7**, без M3-M6.
- M2 (chart + pipeline) ✅ світовий рівень
- M7 (product polish) ✅ світовий рівень
- M3-M6 (intelligence) — їх НЕМАЄ. TV = графік для людини, не агент.

**Наш шлях ≠ копіювати TV**. Наш шлях = **M2 достатньо добре + M3-M6 глибоко + M7 selective**. Це те що робить нас унікальними.

"I'm low-level vs TV" — **тільки на осі M2 і M7** (polish). На осі M3-M6 ми вже попереду більшості.

---

**Reviewer**: Owner.
**Sync checkpoint**: ADR-0049.
**Next review**: monthly R_ELEVATOR audit.
