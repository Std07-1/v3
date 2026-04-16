# AMBITION LADDER — Trading Platform v3 + Архі

> **Призначення**: примусова функція якості. Кожен patch / ADR / feature має mapping на ladder rung.
> Без цього система деградує до "просто працює" — а ми будуємо **унікальне**.

**Архетип**: Master Craftsman Studio + Adversarial Lab (B+C гібрид).

> **Дві осі якості** (орто́гональні):
> - **Ambition (R0-R5, цей файл)** = якість **кожної зміни** (per-patch, short-term)
> - **Maturity (M0-M7)** = потужність **системи в цілому** → [SYSTEM_MATURITY_LADDER.md](SYSTEM_MATURITY_LADDER.md)
>
> Щоб піднятись M3→M4 треба серію R4+ patches. Дві осі працюють разом.

## TL;DR — коротко про кожен rung

| Rung | Назва | Коли | Acceptance |
|------|-------|------|------------|
| **R0** | Just Works | emergency hotfix (не default) | немає видимих багів |
| **R1** | Robust | edge cases handled | витримає 1h stress test |
| **R2** | Surgical | **DEFAULT patches** | R_REJECTOR PASS першим проходом |
| **R3** | Crafted | ADR-driven features | review без питань "що це робить?" |
| **R4** | Innovative | нові subsystems | можна показати у public talk |
| **R5** | Unique | methodology/governance innovations | коли скопіюють — їхній проєкт стане кращим |

---

## Філософія двох сил

### Master Craftsman (B)
- Кожна зміна = підпис майстра. Якість через ритуал, не процедуру.
- Friday review: щотижня — журнал що навчився / зламав / пишається
- Codex of taste: окремо документуємо "що таке гарний код **у цьому проєкті**"

### Adversarial Lab (C)
- Безпека через паранойю. Якщо ти сам себе не зламаєш — зламає ринок.
- Pre-mortem обов'язковий перед deploy: "що зламається?"
- Chaos days: раз/тиждень спроба зламати свій останній patch

**Поєднання**: Disciplined craftsman who hunts his own bugs.

---

## Ladder Rungs (R0 → R5)

Кожен patch / feature / ADR має self-rate'ну позицію. Якщо нижче ніж очікувано → переробляти.

### R0 — Just Works (мінімум; не приймається без причини)
- Compiles, tests pass, no diagnostics errors
- Робить заявлене
- **Acceptance criteria**: "немає видимих багів"
- ⚠️ Це НЕ default — кожен patch має ціль R2+

### R1 — Robust
- Edge cases handled (empty input, network fail, race condition)
- Degraded-but-loud (I5 compliance)
- Error messages корисні (не "Error: failed")
- **Acceptance criteria**: пройде adversarial 1-hour stress test

### R2 — Surgical (DEFAULT для patches)
- Min-diff. Один інваріант. Один rail. Один test.
- Self-Audit clean (CA1-CA10)
- Evidence quality HIGH (всі твердження з маркерами)
- Adjacent contracts перевірені (K4)
- **Acceptance criteria**: R_REJECTOR verdict = PASS на першому проході

### R3 — Crafted (для significant features / ADR-driven)
- Все з R2 +
- Documentation parity (changelog, ADR, AGENTS.md)
- Pre-mortem зроблено: "як це зламається у production?"
- Tests cover edge cases що знайдено у pre-mortem
- Code reads як прозовий текст (intent зрозумілий без коментарів)
- **Acceptance criteria**: інший агент може review без питань "що це робить?"

### R4 — Innovative (для нових subsystems / ADR-creating)
- Все з R3 +
- Розв'язує проблему якої ще не було у проєкті (не copy-paste з іншого місця)
- Має forcing function для якості (gate, exit gate, lint rule)
- Зменшує future toil (хтось через 6 місяців не страждатиме)
- Має ADR який пояснює "чому саме так, а не інакше" (≥2 alternatives розглянуто)
- **Acceptance criteria**: можна показати у public talk як "ось як ми це робимо"

### R5 — Unique (для governance / methodology innovations)
- Все з R4 +
- Інновація на рівні **процесу мислення**, не тільки коду
- Не існує у такому вигляді в інших AI-driven проєктах
- Стає building block для майбутніх R5 інновацій
- Відповідає user vision: "**унікальними, іноваційними, єдиними в цьому напрямку**"
- **Acceptance criteria**: коли інший проєкт скопіює це — він стане кращим

---

## Examples з v3 history

| Item | Rung | Why |
|------|------|-----|
| ADR-0014 (UDS Split-Brain) | R3 | Crafted: pre-mortem + 4 alternatives + tests |
| ADR-0024 (SMC Engine) | R4 | Innovative: pure logic + S0-S6 + own contract layer |
| ADR-0024 + S0-S6 invariants | R5 | Unique: methodology — domain-specific invariants як SSOT |
| trader-v3 ADR-024 (I7 Autonomy) | R5 | Unique: "AI-agent has rights" як архітектурний інваріант |
| Tier 1-3 governance (this elevation) | R4 | Innovative: instructions + skills + role specs as governance code |
| Random bugfix `bar.l → bar.low` | R2 | Surgical: правильний default для більшості |

---

## Self-rating protocol (перед "done")

```markdown
## Ambition Self-Rating
- Target rung: R3 (Crafted)
- Achieved rung: R3 ✅ / R2 ⚠️ (нижче) / R4 🚀 (вище)
- Justification: <чому саме цей rung>
- If below target: <план підвищення або обґрунтування того що target був завищений>
```

---

## Forcing functions (Adversarial Lab side)

### Pre-mortem (обов'язковий для R3+)
Перед merge / deploy задай питання:
1. Як це зламається у production?
2. Який worst-case data input?
3. Що буде якщо downstream system fail'не?
4. Що буде якщо я через 3 місяці забуду як це працює?
5. Що скаже R_REJECTOR через рік?

Записати у PR description / ADR section "Failure Modes".

### Chaos challenge (для R4+)
Раз на 2 тижні — взяти власний останній R4+ patch і:
1. Спробувати написати test який його зламає
2. Якщо зламав → fix + новий exit gate
3. Якщо не зламав → confidence vote +1

### Friday review (для craftsman side)
Раз на тиждень створити `.github/journal/YYYY-WW.md`:
- Що навчився цього тижня
- Що зламав і як виправив
- Чим пишаюся
- Що б переробив

---

## Як використовувати

### Для R_PATCH_MASTER
- У DESIGN gate: оголоси target rung
- У SELF-AUDIT gate: self-rate
- Якщо achieved < target → не closing slice, повертайся до DESIGN

### Для R_ARCHITECT
- ADR-driven changes завжди мають ціль R3+
- Нові subsystems — R4
- Methodology innovations (типу цього файлу) — R5

### Для R_REJECTOR
- Окремо перевіряє чи self-rating обґрунтований
- Якщо patch self-rate'd R3 але має R1 quality → REJECT з поясненням

### Для R_ELEVATOR (нова meta-роль)
- Раз на місяць audit'ує: який % patches досяг target rung?
- Якщо <70% — system drift, повернути дисципліну
- Якщо >90% R2 і нічого вище — стагнація, треба forcing function для R4+

---

## Anti-patterns (R-rung killers)

- ❌ "Це працює, чого ще треба?" → R0 mindset, не приймається
- ❌ "Я зробив R3, але adjacent contracts не перевірив" → насправді R2
- ❌ "Це R5 бо складно" → ні, R5 = унікально, не складно
- ❌ Self-rate'ть всі patches як R3 без обґрунтування → CA failure
- ❌ Цілитись завжди R5 → перфекціонізм паралізує velocity

---

## Ladder evolution

Цей файл сам має ціль **R5**. Якщо колись копіюєте його у інший проєкт і він робить ваш проєкт кращим — **mission accomplished**.

Reviewer: Owner.
Sync checkpoint: ADR-0049 (2026-04-16).
