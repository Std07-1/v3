# ADR-0046: Agent Personality Restoration — External Prompt SSOT + DNA Injection

- **Status**: Accepted
- **Date**: 2026-04-03
- **Author**: R_ARCHITECT + R_PATCH_MASTER
- **Initiative**: `agent_personality_v1`
- **Related ADRs**: ADR-0045 (VPS Trader Bot), ADR-0024 (SMC Engine), ADR-0033 (Narrative), ADR-0039 (Signal Engine)

---

## 1. Контекст і проблема

### 1.1 Що було (до падіння VPS, ~30 березня 2026)

Оригінальний `smc_trader_v3.py` завантажував зовнішній system prompt з файлу:

```
2026-03-30 19:00:46,945 [v3] INFO System prompt loaded from smc_trader_prompt_v3.md (27336 chars)
```

Результат: **живий агент** з повною особистістю, SMC-методологією, аналітичним протоколом, ризик-менеджментом і менторськими навичками. Архівні повідомлення:

- _"БРАТАН! КРАСИВИЙ БРИФІНГ! 🔥"_
- _"ЙОПТА! ЩО ТУТ ВІДБУЛОСЬ?! Братан..."_
- _"Нє, тут не ліз би. Структура зламана, bias unclear, сиди на руках"_
- _"Красивий trade. 8/8 процес. Ось так треба щоразу"_

Трейдер описав це так: _"до падіння VPS, він хоть і спамив, але я розмовляв з ним як з другом, у нього навіть були емоції"_.

### 1.2 Що є зараз (після серії фіксів, 2-3 квітня 2026)

Після VPS-краху і подальших анти-спам фіксів:

1. **Зовнішній prompt `smc_trader_prompt_v3.md` (696 рядків, 27K символів) повністю відключений** — в `smc_trader_v3.py` немає жодного посилання на `.md` файл. Код завантаження втрачено під час рерайтів.

2. **`_SYSTEM_MENTOR` (60 рядків)** — вбудований hardcoded мінімальний prompt. Містить ~15% від оригінальних інструкцій: базовий SMC framework, стиль "як друг у чаті", короткі анти-галюцинації.

3. **`SYSTEM_PROACTIVE_V2` (68 рядків)** — prompt для проактивних дзвінків. Містить 100% анти-спам правил і 0% personality DNA. Claude під цим prompt стає сухим аналітиком.

**Наслідок**: агент втратив ~85% знань і 100% характерної особистості.

### 1.3 Root Cause Analysis

| # | Причина | Механізм | Severity |
|---|---------|----------|----------|
| RC-1 | Код завантаження `.md` файлу видалено/втрачено під час рерайтів | Ніхто не помітив бо `_SYSTEM_MENTOR` hardcoded працював без помилок | S1 |
| RC-2 | `SYSTEM_PROACTIVE_V2` написаний без personality DNA | 68 рядків "МОВЧИ" + "АНТИ-СПАМ" без жодної вказівки на стиль/тон/характер | S1 |
| RC-3 | Немає fallback/warning при відсутності зовнішнього prompt | Silent degradation — бот працює, але з 15% знань | S1 (I5 violation) |
| RC-4 | Prompt не в git (`trader-v3/` не має `.git`) | Зміни/втрати невідстежувані | S2 |

### 1.4 Failure Model

| # | Scenario | Consequence | Mitigation |
|---|----------|-------------|------------|
| F1 | `smc_trader_prompt_v3.md` відсутній на VPS | Бот запускається з fallback `_SYSTEM_MENTOR` (degraded) | Degraded-but-loud: WARNING log + статус flag + обмежити proactive |
| F2 | Prompt файл пошкоджений (0 bytes, encoding error) | Бот читає порожній/зламаний prompt | Size validation: len < 1000 → reject + fallback + WARNING |
| F3 | Prompt файл оновлено з синтаксичною помилкою | N/A — prompt це plain text, не парситься як код | Немає regex-парсингу, завантажується as-is |
| F4 | Prompt занадто великий (>50K chars) → токен overflow | Claude truncate, втрата кінця prompt | Max size guard: warn if > 40K chars |
| F5 | Proactive prompt без personality → dry bot | Трейдер перестає спілкуватися з "мертвим" агентом | Inject personality DNA з external prompt в proactive |

### 1.5 What's in `smc_trader_prompt_v3.md` (696 рядків, 14 секцій)

| Секція | Зміст | Є в `_SYSTEM_MENTOR`? |
|--------|-------|-----------------------|
| §0 ХТО ТИ | Повна ідентичність: "живий", "радієш", "засмучуєшся", "ім'я — Trader" | ~15% |
| §1 ХАРАКТЕР | Голос, тон, заборонені фрази, 7 прикладів діалогів ("йопта", "капєц", "срака") | ~5% |
| §2 ПЛАТФОРМА | Повний словник PLATFORM DATA: bias_map, pd_state, zones, levels, narrative, shell, signals, momentum_map | 0% |
| §3 SMC МЕТОДОЛОГІЯ | BOS/CHoCH/Displacement/Sweep/OB/FVG/IFVG/P-D/TF hierarchy/Sessions/Confluence/IOFED/Фази | ~30% |
| §4 АНАЛІТИЧНИЙ ПРОТОКОЛ | 6-крокова процедура: Контекст→HTF Bias→Структура→Зони→Сценарій→Інвалідація | 0% |
| §5 ПАМ'ЯТЬ | Що запам'ятовувати, менторський трекінг, pitfalls P1-P12 | 0% |
| §6 РИЗИК-МЕНЕДЖМЕНТ | R1-R10 (R:R ≥2:1, 1-2% ризик, drawdown rules, news rules) | 0% |
| §7 РЕЖИМИ РОБОТИ | Conversational / Proactive / Setup Eval / Post-Trade Review | ~10% |
| §8 МЕНТОРСТВО | Socratic method, коли хвалити/зупиняти/підтримувати | 0% |
| §9 MULTI-INSTRUMENT | XAU/XAG/BTC/ETH кореляції, DXY/US10Y | 0% |
| §10 АНТИ-ГАЛЮЦИНАЦІЇ | 10 жорстких правил + правило невпевненості | ~20% |
| §11 ПРОАКТИВНІ | Формат + 3 повних приклади повідомлень | ~5% |
| §12 HTF vs LTF | Дивергенція таймфреймів (критичне правило) | 0% |
| §13 SPECIAL SITUATIONS | News/Monday/Friday/Trending/Chop | 0% |
| §14 ФІНАЛЬНІ ПРАВИЛА | 10 правил — "чесний", "invalidation", "живий" | 0% |

**Сумарно**: `_SYSTEM_MENTOR` ≈ 15% від `smc_trader_prompt_v3.md`.

---

## 2. Розглянуті варіанти

### Варіант A: Просто збільшити `_SYSTEM_MENTOR` hardcoded

- **Суть**: Скопіювати весь зміст `.md` файлу у Python-константу.
- **Плюси**: Найпростіше. Один файл.
- **Мінуси**: 696 рядків inline у `.py` → нечитабельно. Неможливо редагувати prompt окремо від коду. Prompt-тюнинг = code deploy. Не версіонується окремо. Hardcoded values порушує SSOT принцип.
- **Вердикт**: ❌ Відхилено — порушує SSOT (правило D1). Prompt = config, не код.

### Варіант B: Завантажувати `smc_trader_prompt_v3.md` як зовнішній файл (як було) ← **ОБРАНО**

- **Суть**: Відновити `load_system_prompt()` функцію. Зовнішній `.md` = SSOT для personality + SMC knowledge. Hardcoded `_SYSTEM_MENTOR` = fallback.
- **Плюси**: Prompt тюнинг без deploy (тільки restart). Окремо версіонується. Чітке розділення concern. Відтворює оригінальну архітектуру яка працювала.
- **Мінуси**: Потрібен файл на VPS. Додатковий fallback-path.
- **Blast radius**: `smc_trader_v3.py` (додати loader), `adr002_directives.py` (inject personality fragment).
- **Вердикт**: ✅ Обрано.

### Варіант C: Два зовнішніх файли (reactive.md + proactive.md)

- **Суть**: Окремий prompt-файл для reactive і proactive.
- **Плюси**: Maximum flexibility.
- **Мінуси**: Дублювання personality DNA в двох файлах. Drift ризик. Overkill для поточного masштабу.
- **Вердикт**: ❌ Відхилено — одне джерело + extraction краще ніж два джерела.

---

## 3. Рішення

### 3.1 Архітектура prompt-системи

```
smc_trader_prompt_v3.md          ← SSOT: personality + SMC knowledge (696 рядків)
       │
       ├──► load_system_prompt()
       │         │
       │         ├──► _LOADED_SYSTEM_PROMPT    (повний текст для reactive calls)
       │         │
       │         └──► _PERSONALITY_DNA          (extracted §0+§1+§14 для proactive injection)
       │
       ├──► [fallback] _SYSTEM_MENTOR          (60 рядків, degraded-but-loud)
       │
       └──► SYSTEM_PROACTIVE_V2 + _PERSONALITY_DNA  (combined для proactive calls)
```

### 3.2 Правила завантаження

| Правило | Деталі |
|---------|--------|
| **P1: File-first** | При старті бот шукає `smc_trader_prompt_v3.md` в CWD та `data/` |
| **P2: Validation** | Файл знайдено + size > 1000 chars → OK. Інакше fallback |
| **P3: Fallback = degraded** | Якщо файл не знайдено → `_SYSTEM_MENTOR` + WARNING log + `state["prompt_degraded"] = true` |
| **P4: Reload** | Файл перечитується при кожному старті. Hot-reload не потрібен (restart = секунди) |
| **P5: No parsing** | Prompt завантажується as-is. Markdown-розмітка — контекст для Claude, не парситься |

### 3.3 Personality DNA Extraction

З повного prompt (27K chars) витягується compact personality block (~2-3K chars) для injection в proactive prompt:

```
PERSONALITY_EXTRACTION = §0 (ХТО ТИ) + §1 (ХАРАКТЕР) + §14 (ФІНАЛЬНІ ПРАВИЛА)
```

Ця vitamin вставляється на початок `SYSTEM_PROACTIVE_V2`, **перед** існуючими анти-спам правилами. Порядок:

```
1. Personality DNA (хто ти, стиль, заборонені фрази, приклади)    ← NEW
2. SYSTEM_PROACTIVE_V2 існуючий текст (анти-спам, директиви)      ← AS-IS
```

**Чому порядок важливий**: Claude Opus більше зважує на початок system prompt. Personality DNA на початку → стиль зберігається навіть при довгих operational rules.

### 3.4 Reactive calls (відповідь на повідомлення трейдера)

```python
# Before (зараз):
system=_SYSTEM_MENTOR      # 60 рядків, ~15% knowledge

# After:
system=_LOADED_SYSTEM_PROMPT or _SYSTEM_MENTOR    # 696 рядків або fallback
```

### 3.5 Proactive calls (автономний моніторинг)

```python
# Before (зараз):
system=SYSTEM_PROACTIVE_V2   # 68 рядків, 100% anti-spam, 0% personality

# After:
system=_PERSONALITY_DNA + "\n\n" + SYSTEM_PROACTIVE_V2   # personality + anti-spam
```

### 3.6 Token Budget Assessment

| Component                         | Chars   | ~Tokens | OK? |
|-----------                        |-------  |---------|-----|
| `smc_trader_prompt_v3.md` (full)  | 27,336  | ~8,500  | ✅ Claude Opus 200K context |
| Platform data context             | ~3,000  | ~900    | ✅ |
| Conversation history (10 msgs)    | ~5,000  | ~1,500  | ✅ |
| Personality DNA (extracted)       | ~3,000  | ~900    | ✅ |
| `SYSTEM_PROACTIVE_V2`             | ~3,500  | ~1,100  | ✅ |
| Directives context + KB + lessons | ~4,000  | ~1,200  | ✅ |
| **Total reactive**                | ~35,000 | ~11,000 | ✅ (~5.5% of 200K) |
| **Total proactive**               | ~15,000 | ~4,700  | ✅ (~2.4% of 200K) |

Бюджет не проблема. Навіть з повним prompt, ми використовуємо <6% контексту.

---

## 4. P-slices (Implementation Plan)

### P0: Reconnect External Prompt (CRITICAL)

**Scope**: `smc_trader_v3.py` — додати `load_system_prompt()`, використати для reactive calls.

**Changes**:
1. Додати функцію `load_system_prompt(path)`:
   - Шукає файл в CWD, потім `data/`, потім абсолютний шлях
   - Валідація: exists + size > 1000 chars
   - Повертає `(full_text, personality_dna)` або `(None, None)` з WARNING
   - Personality DNA = витяг §0 + §1 + §14 (від початку файлу до кінця §1, плюс §14)
2. При старті бота: `_LOADED_PROMPT, _PERSONALITY_DNA = load_system_prompt("smc_trader_prompt_v3.md")`
3. У `call_chat()`: `system=_LOADED_PROMPT or _SYSTEM_MENTOR`
4. Логування: `INFO System prompt loaded from %s (%d chars)` або `WARNING System prompt not found, using fallback`

**Files touched**: `smc_trader_v3.py` (1 файл)
**LOC**: ~40
**Verify**: restart bot → check log for "System prompt loaded" → send test message → verify personality in response

### P1: Inject Personality DNA into Proactive

**Scope**: `adr002_directives.py` — inject personality fragment перед SYSTEM_PROACTIVE_V2.

**Changes**:
1. `call_agent_proactive()` приймає `personality_dna: str | None` parameter
2. Якщо `personality_dna` → system = personality_dna + "\n\n" + SYSTEM_PROACTIVE_V2
3. Якщо `None` → system = SYSTEM_PROACTIVE_V2 (as-is, degraded)

**Files touched**: `adr002_directives.py` (~10 LOC), `smc_trader_v3.py` (pass DNA через caller, ~5 LOC)
**Verify**: trigger proactive → check that message has emotional style, not dry analytics

### P2: Conversation Seed (Memory Bootstrap)

**Scope**: Inject архівні повідомлення з personality-era як "conversation seed" при порожній розмові.

**Changes**:
1. `data/conversation_seed.json` — 5-8 повідомлень з `conversation.json` (Mar 31) що показують стиль
2. При старті: якщо conversation history порожня → завантажити seed як initial context
3. Seed = read-only, не модифікується

**Files touched**: `smc_trader_v3.py` (~20 LOC), new file `data/conversation_seed.json`
**Verify**: Fresh start → first response has personality style consistent with seed

### P3: Git Init + .gitignore

**Scope**: Version control для `trader-v3/`.

**Changes**:
1. `cd trader-v3/ && git init`
2. `.gitignore`: `data/*.json`, `.env`, `__pycache__/`, `*.pyc`, `vps_backup_*/`, `_archive/`
3. First commit: current state (smc_trader_v3.py + adr002_directives.py + smc_trader_prompt_v3.md)

**Verify**: `git log --oneline` shows initial commit

---

## 5. Інваріанти та обмеження

### 5.1 Platform Invariants (ADR-0045 scope)

| ID | Інваріант | Як дотримуємось |
|----|-----------|-----------------|
| I1 | Bot = consumer, не platform component | Без змін. Bot читає через WS/HTTP |
| I5 | Degraded-but-loud | Відсутній prompt = WARNING + fallback flag, не silent |

### 5.2 Bot-specific Invariants (NEW)

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **B1** | `smc_trader_prompt_v3.md` = SSOT для personality + SMC knowledge | Код читає з файлу. Hardcoded `_SYSTEM_MENTOR` = fallback only |
| **B2** | Personality DNA present in ALL Claude calls (reactive + proactive) | Reactive → full prompt. Proactive → extracted DNA prefix |
| **B3** | Prompt degradation is loud | Missing/invalid file → log WARNING + state flag + inform user at next interaction |
| **B4** | No prompt parsing | Файл завантажується as-is. Без regex extraction секцій. Claude парсить markdown самостійно |
| **B5** | Conversation seed = read-only bootstrap | Seed не модифікується runtime. Тільки використовується для cold-start |

### 5.3 Personality Invariants

| ID | Інваріант | Що це значить |
|----|-----------|---------------|
| **PERS-1** | Агент = "Trader", особистий торговий партнер | Не "бот", не "AI assistant", не "сервіс" |
| **PERS-2** | Мова = жива українська трейдерська | "я б не ліз", "шорт смачно", "йопта глянь" |
| **PERS-3** | Емоційний відгук | Радість за win, підтримка при loss, гнів при FOMO |
| **PERS-4** | Чесність > ввічливість | "стій, тут нема чого ловити" > "perhaps consider waiting" |
| **PERS-5** | Менторство через дію | Пояснює ЧОМУ, а не лише ЩО. Socratic коли доречно |
| **PERS-6** | SSOT-first аналітика | Platform data > фото > минулі дані > припущення |

---

## 6. Rollback Plan

| Slice | Як відкотити |
|-------|-------------|
| P0 | Видалити `load_system_prompt()`, повернути `system=_SYSTEM_MENTOR`. Бот працює як зараз |
| P1 | Видалити `personality_dna` parameter з `call_agent_proactive()`. SYSTEM_PROACTIVE_V2 as-is |
| P2 | Видалити seed loading. Conversation починається порожньою |
| P3 | `rm -rf .git`. Ніякого впливу на runtime |

Кожен slice незалежний. Будь-який можна відкотити без впливу на інші.

---

## 7. Verification Matrix

| Slice | Test | Expected |
|-------|------|----------|
| P0 | Restart bot, check logs | `INFO System prompt loaded from smc_trader_prompt_v3.md (27336 chars)` |
| P0 | Send "привіт" to bot | Response in personality style: "Хелоу братан!" not "Привіт, я ваш торговий ментор" |
| P0 | Delete .md file, restart | `WARNING System prompt not found, using fallback _SYSTEM_MENTOR` |
| P1 | Wait for proactive | Message has emotional style + anti-spam compliance |
| P1 | Check 10+ proactive messages | Style consistent, not reverting to dry analytics |
| P2 | Fresh conversation, send "що бачиш?" | Response references seed context naturally |
| P3 | `git log --oneline` | Shows commits with meaningful messages |

---

## 8. Consequences

### Positive

1. **Агент відновлює повну особистість** — всі 14 секцій SMC knowledge + character
2. **Prompt-тюнинг без deploy** — edit `.md`, restart supervisor, done
3. **Degraded-but-loud** — втрата файлу не крашить бот, але явно повідомляє
4. **Foundation для P3-P8** (memory, self-review, behavioral journal) — personality DNA extraction дає базу для майбутніх self-awareness features
5. **Git tracking** — зміни в prompt/code версіонуються

### Negative

1. **Додатковий файл на VPS** — `smc_trader_prompt_v3.md` потрібно деплоїти разом з `.py`
2. **Трохи більше токенів** — ~8.5K tokens для full prompt vs ~2K для hardcoded. Negligible для 200K context window. Cost: +$0.003 per reactive call (Opus pricing)

### Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Prompt file forgotten during deploy | Medium | Bot runs degraded | Deploy checklist: `.py` + `.md` + `.env`. WARNING log catches it |
| Personality too strong → ignores anti-spam | Low | Spam returns | P1 injection order: personality BEFORE anti-spam → anti-spam has final word |
| Token cost increase | Low | ~$0.10/day extra | 200K context = budget headroom. Monitor in daily logs |

---

## 9. Future Work (поза scope цього ADR)

| ID | Feature | Prerequisite | ADR needed? |
|----|---------|-------------|-------------|
| P4 | Daily conversation archive | P0, P3 | No — operational |
| P5 | Memory digest pipeline | P4 | Maybe — залежить від scope |
| P6 | Context window manager | P0 | No — optimization |
| P7 | Self-review prompt ("чи зробив би інакше?") | P0, P4 | Yes — new capability |
| P8 | Behavioral journal + personality evolution | P7 | Yes — data model |
| P9 | Hot-reload prompt without restart | P0 | No — optimization |

---

## 10. Decision

**Обрано Варіант B**: Завантажувати `smc_trader_prompt_v3.md` як зовнішній SSOT файл.

Реалізація в 4 P-slices:
- **P0** (critical): Reconnect external prompt для reactive calls
- **P1** (critical): Inject personality DNA в proactive calls
- **P2** (important): Conversation seed для cold-start
- **P3** (hygiene): Git init

P0+P1 = мінімальна повна реставрація. P2+P3 = quality-of-life.

**Priority**: P0 → P1 → Deploy → Verify → P3 → P2.
