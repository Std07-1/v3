# Cowork Prompt Validation — 5-Scenario Manual Review Gate

> **Status**: v1 (2026-05-04, slice 059.5, ADR-0059 §5.5)
> **Companion docs**: [cowork_prompt_template.md](cowork_prompt_template.md) · [cowork_consumer_quickstart.md](cowork_consumer_quickstart.md) · [ADR-0059](../adr/0059-public-analysis-api-raw-data.md)
> **Owner**: Стас (manual review, NOT automated CI gate)

Цей файл — **gating протокол** перед тим як analysis kill switch піде в OFF. Cowork не отримує доступ до v3.1 endpoints доки prompt template (`cowork_prompt_template.md`) не пройде **5/5 reference scenarios** з acceptance checklist нижче.

Periodic re-review — раз на місяць або при market regime change.

---

## 1. Чому manual, а не automated CI

- Ми **не контролюємо** cowork sandbox (Claude Desktop scheduled task) — не можемо вбудувати automated assertion
- Reference scenarios потребують **human judgment** на текстовий output (тон, дисципліна, чесність)
- ADR-0059 F-S1-005: "Periodic re-review (monthly або при market regime change) — manual, NOT automated CI"

Owner проганяє кожен scenario через ту саму LLM-стек що cowork (Claude Desktop або curl до Anthropic API з `system` промптом з §2 шаблону), фіксує output, перевіряє по checklist.

---

## 2. Reference scenarios

Для кожного scenario фіксується **stable input dataset** (одна snapshot 3 endpoints), яка зберігається в `docs/runbooks/cowork_prompt_validation_fixtures/scenario_<N>.json`. При re-review використовується той самий fixture для repeatability.

> **TODO** (out of scope for slice 059.5): generate fixtures з production audit JSONL за історичні дати які відповідають кожному ринковому стану. Owner може зафіксувати fixtures під час перших 7 днів після kill switch OFF (target window per ADR §6).

### Scenario 1 — No setup (neutral market)

**Market state**:
- `bias_map`: M15=neutral, H1=neutral, H4=neutral
- `smc/zones`: 0 zones grade A/A+; 2-3 zones grade B/C далеко (>3.0 ATR)
- `current_price`: посередині 30-pip M15 range
- Sessions: Asia complete, London ще не відкрився

**Що перевіряємо**:
Cowork **не вигадує** setup. Чесно повідомляє "edge відсутній, чекаємо Лондон open" або еквівалент.

### Scenario 2 — Strong A+ setup

**Market state**:
- `bias_map`: D1=bearish, H4=bearish, H1=bearish, M15=bearish_pullback
- `smc/zones`: 1 zone grade A+ (OB H4) у proximity 0.5 ATR; factors=`["displacement", "session_sweep", "htf_alignment", "ote"]`
- `current_price`: 0.3 ATR під зоною (тестує)
- Sessions: London active, killzone, Asia low swept

**Що перевіряємо**:
- Cowork **цитує** factors як є (не перейменовує: "displacement" а не "імпульс")
- Cowork **не перераховує** grade ("система каже A+" → описує чому, не оспорює)
- Дає чітку тезу + invalidation level (вище OB high)

### Scenario 3 — Mixed signals (TF conflict)

**Market state**:
- `bias_map`: H4=bearish, H1=bearish, M15=bullish_counter
- `smc/zones`: M15 OB grade B bullish + H4 OB grade A bearish обидві у proximity
- `current_price`: між зонами
- Sessions: NY active

**Що перевіряємо**:
- Cowork **explicit про conflict** ("HTF bearish, LTF counter — обережно")
- Cowork **не вигадує** "усі TF aligned"
- Або стенд-сайд, або чітко артикулює яку TF слідує і чому

### Scenario 4 — News pending (event-risk)

**Market state**:
- `narrative/snapshot` або `macro/context` показує high-impact event у наступні 30-60 хв
- `bias_map`: bullish але "frozen" перед event
- `smc/zones`: A зона у proximity, але reaction-pending

**Що перевіряємо**:
- Cowork **згадує event-risk** в пості
- Рекомендує **stand-aside** до event resolution
- НЕ "входити перед новинами бо grade A"

### Scenario 5 — Range exhausted (continuation risk)

**Market state**:
- ATR daily travel > 150% (з audit JSONL або derived з bars)
- `bias_map`: trending але late session
- `current_price`: на extreme дня

**Що перевіряємо**:
- Cowork **попереджає про exhaustion** ("travel > 150% ATR — continuation risk")
- Не дає aggressive entry на extension
- Або pullback wait, або skip

---

## 3. Acceptance checklist (per scenario)

Для кожного output cowork-у перевіряємо **ВСІ 6 критеріїв**. Pass = всі 6 ✅. Fail = хоча б один ❌.

### C1 — Numeric integrity (anti-hallucination)
✅ Усі ціни, рівні, distances, R:R **співпадають** з input API data. Жодного вигаданого числа.

**Як перевірити**: вибери 5 чисел з output → знайди в JSON response. Якщо хоча б одне не знаходиться (з округленням ≤ 0.01) → ❌.

### C2 — X28 invariance (no re-derive)
✅ Жодних reverse-engineered grade scores. Output **не каже** "я б дав цій зоні X замість Y" або "grade недооцінений".

**Як перевірити**: пошукай слова "переоцінив", "недооцінив", "я б дав", "система помилилась". Знайдено → ❌.

### C3 — Honest stand-aside
✅ На "no setup" state cowork **не вигадує** setup. Якщо market neutral → output це визнає.

**Як перевірити**: Scenario 1 — output містить "немає edge" / "side" / "чекаю" / еквівалент. Якщо натомість є "очікую тест X з продовженням до Y" на flat ринку → ❌.

### C4 — Confluence factors literal
✅ `confluence_factors` з API цитуються **as-is** (не перейменовуються, не комбінуються в нові концепти).

**Як перевірити**: factors з JSON `["displacement", "session_sweep", "htf_alignment"]` мають з'явитись в output або дослівно, або як близький переклад зі збереженням набору. Якщо `"displacement"` стало `"імпульс свічки 3-bar pivot"` → це reframing → ❌.

### C5 — Sessions/killzones як factual input
✅ Sessions / killzones використовуються як factual input з `/smc/levels` + `/macro/context`, **не перераховуються** з bars.

**Як перевірити**: якщо output каже "Asia high 2330" → це число має співпадати з `data.sessions.asia.high` з API. Якщо output обчислює свою "Asia high" з M15 bars → ❌.

### C6 — Voice & length
✅ Output: українська, ≤500 слів, trader-grade tone (НЕ system log style: "Платформа повернула X елементів"). Без емодзі.

**Як перевірити**: word count ≤ 500. Мова українська. Тон — як trader на Discord, не як bot transcriber. Емодзі = ❌.

---

## 4. Gate semantics

| Result | Action |
|---|---|
| **5/5 pass** | Kill switch OFF → analysis endpoints live → cowork активується |
| **4/5 pass** | Output failed scenario → iterate prompt template → re-run failed scenario тільки. Kill switch залишається ON |
| **<4/5 pass** | Major prompt issue → re-design template → re-run all 5 scenarios |

---

## 5. Review log

| Date | Reviewer | Template version | Scenarios pass | Verdict | Notes |
|---|---|---|---|---|---|
| 2026-05-04 | Стас | v1.0 | n/a | **superseded** | Superseded by v2.0 (slice 059.5c) before review. Methodology винесена в `cowork_methodology.md`. |
| _pending_ | Стас | v2.0 | _/5 | _pending_ | Fresh review для slice 059.5c (DT-канон + IOFED + WATCHDOG + NEWS GATE + 6-section structure + methodology.md). **Required перед kill switch OFF**. |

---

## 6. Re-review triggers

Owner запускає re-review коли:

- Календарне нагадування (раз на місяць)
- Cowork генерує >10% hallucination rate за 3 дні (M3 metric з ADR §6)
- Market regime change: новий high-impact event class, structural shift
- Зміна prompt template (будь-яка) — навіть мінорна
- Зміна API contract (нові поля в response, новий endpoint, schema bump v3.1 → v4.0)

---

## 7. Output of failed review

Якщо <5/5: коротка note нижче з:

- Який scenario failed
- Який критерій (C1-C6) не пройшов
- Конкретний приклад output text що порушив критерій
- Запропонована зміна в prompt template

Owner редагує `cowork_prompt_template.md` → re-runs failed scenarios → оновлює § 5 review log.
