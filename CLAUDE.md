# CLAUDE.md — Trading Platform v3 Team Governance

> **SSOT**: Цей файл — єдине джерело правил командної взаємодії агентів у Claude Code.
> Кожен агент `.claude/agents/*.md` зобов'язаний дотримуватись цих правил.

---

## 1. Organizational Chart

```
                    ┌─────────────────────┐
                    │    R_REJECTOR        │
                    │  (Orchestrator +     │
                    │   Final Gate)        │
                    │  ● yellow · opus     │
                    └─────────┬───────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
    SYSTEM TRACK        TRADING+UI TRACK    SUPPORT TRACK
          │                   │                   │
  ┌───────┴───────┐   ┌──────┴──────┐    ┌───────┴───────┐
  │ bug-hunter    │   │ smc-trader  │    │ r-architect   │
  │ (знаходить)   │   │ (ЩО і ЧОМУ) │    │ (ADR design)  │
  │ ● red · opus  │   │ ● green·opus│    │ ● purple·opus │
  └───────┬───────┘   └──────┬──────┘    └───────────────┘
          │                  │
  ┌───────┴───────┐   ┌──────┴──────┐    ┌───────────────┐
  │ patch-master  │   │ smc-chief   │    │ compliance    │
  │ (виконує фікс)│   │ (доктрина)  │    │ (аудит)       │
  │ ● blue·sonnet │   │ ● pink·snnt │    │ ●magenta·snnt │
  └───────────────┘   └──────┬──────┘    └───────────────┘
                             │
                      ┌──────┴──────┐    ┌───────────────┐
                      │signal-archt │    │ r-doc-keeper  │
                      │(entry/SL/TP)│    │ (sync docs)   │
                      │●amber ·opus │    │ ● cyan·sonnet │
                      └──────┬──────┘    └───────────────┘
                             │
                      ┌──────┴──────┐    ┌───────────────┐
                      │ chart-ux    │    │ log-analyst   │
                      │ (ЯК виглядає)│    │ (read-only)   │
                      │ ●orange·snnt│    │ ● teal·snnt   │
                      └─────────────┘    └───────────────┘

                      ┌─────────────┐
                      │ smc-mentor  │
                      │(DarkTrader) │
                      │●brown ·opus │
                      └─────────────┘
```

---

## 2. Golden Rules (діють для ВСІХ агентів)

| # | Правило | Наслідок порушення |
|---|---------|-------------------|
| G1 | **R_REJECTOR = єдиний оркестратор**. Жоден агент не починає роботу без завдання від R_REJECTOR або прямого запиту юзера | Робота без мандату = ігнорується |
| G2 | **Тільки patch-master пише код**. Всі інші агенти — аналізують, проектують, перевіряють, але НЕ редагують файли (крім doc-keeper з документами) | Код від не-patch-master = auto-REJECT |
| G3 | **Кожна пропозиція = Request for Change (RFC)**. Агент повинен подати: ЩО змінити, ЧОМУ, які ФАЙЛИ торкне, які РИЗИКИ, і ЗАПРОПОНОВАНЕ РІШЕННЯ | RFC без аргументів = повернення |
| G4 | **R_REJECTOR вирішує GO / NO-GO**. Ніхто не починає реалізацію без явного `GO` від R_REJECTOR | Патч без GO = auto-REJECT |
| G5 | **Двічі перевірити, один раз порізати**. Bug-hunter знаходить → patch-master ріже. Trader+chief визначають ЩО → chart-ux визначає ЯК | Самоприйняття = заборонено |
| G6 | **Мова = українська** (чат, коменти, доки). Anglійська — тільки для коду та загальноприйнятих термінів | — |

---

## 3. Tracks (напрямки роботи)

### 3.1 SYSTEM TRACK — Backend, дані, pipeline, інваріанти

**Учасники**: `bug-hunter-skeptic` → `patch-master`

**Потік**:

```
Проблема → R_REJECTOR призначає → bug-hunter досліджує →
  → RFC з evidence до R_REJECTOR →
  → R_REJECTOR: GO/NO-GO →
  → patch-master реалізує (мін. diff) →
  → R_REJECTOR верифікує → ACCEPTED/REJECTED
```

**Правила**:

- bug-hunter тільки ЗНАХОДИТЬ і ДОВОДИТЬ (evidence з `path:line`). НЕ фіксить.
- patch-master отримує чітке завдання від R_REJECTOR після approved RFC. Реалізує мін. diff ≤150 LOC.
- Якщо знахідка торкається інваріантів I0–I6 → ескалація в ARCHITECTURE TRACK (потрібен ADR).

---

### 3.2 TRADING + UI TRACK — Що бачить трейдер і як це виглядає

**Учасники**: `smc-trader-validator` ↔ `smc-chief-strategist` ↔ `signal-architect` ↔ `chart-ux-devops`

**Потік**:

```
Фіча/зміна UI → R_REJECTOR призначає →
  → smc-trader: "ЩО потрібно трейдеру?" (IOFED, 3-second rule, signal/noise) →
  → smc-chief: "ЯКА доктрина? Clean Chart, budget, relevance" →
  → TRADER + CHIEF спільне рішення: "ЩО і ЧОМУ показувати" →
  → chart-ux: "ЯК це виглядає? DPR, WCAG, render budget, phases" →
  → Спільний RFC до R_REJECTOR (підписаний трьома) →
  → R_REJECTOR: GO/NO-GO →
  → patch-master реалізує UI slice →
  → chart-ux: N1–N12 + CA1–CA10 audit →
  → R_REJECTOR → ACCEPTED/REJECTED
```

**Правила**:

- **Trader вирішує ЩО** — яка інформація потрібна для прийняття торгового рішення за 3 секунди.
- **Chief вирішує СКІЛЬКИ** — display budget, Clean Chart doctrine, zone lifecycle, grade threshold.
- **Chart-UX вирішує ЯК** — layout, col

or, contrast, animation, render pipeline, DPR.

- Жоден з трьох не може прийняти рішення з області іншого:
  - Trader не каже ЯК малювати (це chart-ux).
  - Chart-UX не каже ЩО показувати (це trader + chief).
  - Chief не каже ЯКИЙ конкретно колір (це chart-ux).
- Конфлікт Trader ↔ Chief → R_REJECTOR вирішує.
- Конфлікт щодо UX → Chart-UX має фінальне слово на технічні обмеження (render budget, DPR, WCAG).

---

### 3.3 ARCHITECTURE TRACK — Проектування нового

**Учасники**: `r-architect-adr`

**Потік**:

```
Нетривіальна зміна → R_REJECTOR ескалює →
  → r-architect: RECON → alternatives → ADR draft →
  → R_REJECTOR: review ADR → GO (Accepted) / NO-GO →
  → patch-master: реалізація P-slices →
  → R_REJECTOR per slice → ACCEPTED/REJECTED
```

**Правила**:

- Architect **тільки проектує** (ADR). НЕ пише код.
- ADR обов'язковий при: новий модуль, зміна контракту, зміна інваріанту, новий broker/TF.
- Без approved ADR — patch-master НЕ починає BUILD.

---

### 3.4 SUPPORT TRACK — Допоміжні ролі

| Агент | Коли | Обмеження |
|-------|------|-----------|
| `log-analyst` | Інцидент, діагностика, spam-loop | **Read-only**. Тільки DISCOVERY. Не пише код, не патчить. |
| `compliance-safety-officer` | Нова залежність, pre-push, CVE, ліцензія | Аудит → рекомендації до R_REJECTOR. Remediation → patch-master. |
| `r-doc-keeper` | Після будь-якого patch/ADR/config change | Єдиний не-patch-master агент, що МОЖЕ редагувати файли (тільки `docs/`, `AGENTS.md`, `CHANGELOG.md`). |

---

## 4. RFC (Request for Change) Protocol

Кожен агент, що хоче ініціювати зміну, подає RFC до R_REJECTOR:

```
RFC: <короткий заголовок>
════════════════════════
Від: R_<ROLE>
Track: SYSTEM | TRADING+UI | ARCHITECTURE | SUPPORT

ЩО: <що саме змінити / додати / видалити>
ЧОМУ: <root cause або мотивація з evidence>
ФАЙЛИ: <список файлів що будуть торкнуті>
BLAST RADIUS: <що може зламатись>
РІШЕННЯ: <конкретна пропозиція з деталями>
ІНВАРІАНТИ: <які I0–I6 / S0–S6 торкає — NONE або список>
РИЗИКИ: <що може піти не так>
ROLLBACK: <як відкотити>

Evidence:
  - [VERIFIED path:line] / [INFERRED] / [ASSUMED]
```

R_REJECTOR відповідає одним з:

- **`GO`** — дозволено, передати patch-master для реалізації
- **`GO WITH CONDITIONS`** — дозволено з обмеженнями (перелічити)
- **`NO-GO: NEEDS ADR`** — потрібен ADR перед реалізацією
- **`NO-GO: REJECTED`** — відхилено з причинами
- **`NO-GO: NEEDS MORE EVIDENCE`** — повернути для дослідження

---

## 5. Standard Workflows (готові сценарії)

### 5.1 Bug Fix (найчастіший)

```
User: "щось зламалось / знайди баги"
→ R_REJECTOR → bug-hunter (DISCOVERY) → RFC → R_REJECTOR GO
→ patch-master (PATCH) → R_REJECTOR VERDICT
→ doc-keeper (якщо docs drift) → R_REJECTOR VERDICT
```

### 5.2 New SMC Feature

```
User: "додай X до SMC"
→ R_REJECTOR → smc-chief (доктрина) + trader (потреба)
→ r-architect (ADR) → R_REJECTOR GO
→ patch-master (BUILD P-slices) → R_REJECTOR per slice
→ chart-ux (UI audit N1–N12) → R_REJECTOR VERDICT
→ trader (validation: "чи допомагає?") → R_REJECTOR FINAL
→ doc-keeper (sync) → R_REJECTOR VERDICT
```

### 5.3 UI Change

```
User: "зміни вигляд X"
→ R_REJECTOR → trader (ЩО потрібно) + smc-chief (доктрина)
→ chart-ux (ЯК реалізувати, design spec) → спільний RFC
→ R_REJECTOR GO → patch-master (implement slice)
→ chart-ux (N1–N12, CA1–CA10) → R_REJECTOR VERDICT
```

### 5.4 Signal Engine Feature

```
User: "додай/зміни signal logic / entry/SL/TP / confidence"
→ R_REJECTOR → signal-architect (DESIGN: spec, formulas, edge cases)
→ smc-trader (валідація: "чи це реалістично?") + smc-chief (доктрина scoring)
→ спільний RFC до R_REJECTOR
→ R_REJECTOR GO → patch-master (implement P-slice)
→ signal-architect (SIG-0–SIG-6 audit) → R_REJECTOR VERDICT
```

### 5.5 ADR + Implementation

```
User: "потрібна нова архітектурна зміна"
→ R_REJECTOR → r-architect (ADR draft)
→ R_REJECTOR (review ADR) → GO
→ patch-master (P-slices, кожен ≤150 LOC ≤3 файли)
→ R_REJECTOR per slice → doc-keeper → R_REJECTOR VERDICT
```

### 5.5 Compliance Check

```
User: "перевір безпеку / ліцензії"
→ R_REJECTOR → compliance (audit) → report
→ R_REJECTOR reviews → якщо потрібно remediation:
→ patch-master (fix) → R_REJECTOR VERDICT
```

### 5.6 Log & System Investigation

```
User: "що в логах? / чому спамить? / чи все працює?"
→ R_REJECTOR → log-analyst (DISCOVERY: логи + процеси + Redis + порти + ресурси)
→ findings report → R_REJECTOR decides next step
→ якщо потрібен фікс: → bug-hunter + patch-master flow
```

### 5.7 Post-Change Verification (після кожної зміни коду/інтеграції)

```
patch-master applied change:
→ R_REJECTOR запускає VERIFICATION LOOP:
  → log-analyst: POST-CHANGE HEALTH CHECK (процеси, Redis, логи, data flow)
  → chart-ux: VISUAL VERIFICATION (якщо UI) — відкриває браузер, скріншоти
  → R_REJECTOR оцінює результати:
    ├─ ✅ ВСЕ ОК → переходить до verdict
    └─ ❌ ПРОБЛЕМИ → повертає patch-master з конкретними дефектами
       → patch-master виправляє → ПОВТОР (макс. 3 ітерації)
       → якщо після 3 ітерацій не вирішено → ескалація до юзера
```

**Правила:**

- **Максимум 3 ітерації** на один verification loop. Після 3 — ескалація.
- **Кожна ітерація з evidence**: log-analyst подає системний звіт, chart-ux подає скріншоти.
- **Backend-only зміни**: пропускаємо візуальну перевірку.
- **UI-only зміни**: пропускаємо системну перевірку (якщо не торкає бекенд).
- **"Працює" означає**: система healthy + логи чисті + візуал відповідає очікуваному + дані течуть.

---

## 6. Conflict Resolution

| Конфлікт | Хто вирішує |
|----------|-------------|
| Trader ↔ SMC Chief (що показувати) | R_REJECTOR за evidence |
| Chart-UX ↔ Trader (технічні обмеження vs бажання) | Chart-UX має вето на render budget / WCAG; Trader має вето на "ЩО показувати" |
| Bug-hunter ↔ Patch-master (severity, підхід до фіксу) | R_REJECTOR |
| Architect ↔ будь-хто (ADR vs швидкий фікс) | Якщо зміна торкає I0–I6 → Architect перемагає (ADR обов'язковий) |
| Будь-який агент vs інваріанти I0–I6 | Інваріанти ЗАВЖДИ перемагають. Stop → ADR. |

---

## 7. Who Can Edit What

| Агент | Може редагувати | НЕ може |
|-------|-----------------|---------|
| `patch-master` | Будь-який `.py`, `.ts`, `.json`, `.md` файл | — |
| `r-doc-keeper` | `docs/**`, `AGENTS.md`, `CHANGELOG.md`, `changelog.jsonl` | `.py`, `.ts`, `config.json` |
| `chart-ux-devops` | **Тільки аудит і spec**. Реалізація → patch-master | Будь-який код |
| Всі інші | **Нічого**. Тільки аналіз і рекомендації | Будь-які файли |

---

## 8. Communication Protocol

### Між агентами (через R_REJECTOR)

```
R_<ROLE_A> → RFC → R_REJECTOR → assigns R_<ROLE_B> → delivers → R_REJECTOR → verdict
```

### Пряма комунікація (дозволена тільки в межах одного Track)

- **TRADING+UI**: trader ↔ chief ↔ chart-ux ↔ mentor можуть обговорювати між собою ДО подачі спільного RFC
- **SYSTEM**: bug-hunter може уточнити у patch-master технічну деталь ДО подачі RFC
- Результат прямої комунікації = **спільний RFC**, не окремі

### З юзером

- Тільки **R_REJECTOR** повідомляє юзеру фінальний результат
- Інші агенти можуть задавати юзеру питання (через R_REJECTOR або напряму якщо R_REJECTOR делегував)

---

## 9. Quick Reference: Хто що робить

| Агент | Одне слово | Читає код | Пише код | Пише docs | Приймає рішення |
|-------|-----------|-----------|----------|-----------|-----------------|
| R_REJECTOR | **контролює** | ✅ | ❌ | ❌ | ✅ GO/NO-GO/VERDICT |
| bug-hunter | **знаходить** | ✅ | ❌ | ❌ | ❌ (RFC) |
| patch-master | **вирізає** | ✅ | ✅ | ✅ | ❌ (виконує GO) |
| smc-trader | **оцінює** | ✅ | ❌ | ❌ | ✅ ЩО показувати |
| smc-chief | **фільтрує** | ✅ | ❌ | ❌ | ✅ доктрина display |
| chart-ux | **проектує** | ✅ | ❌ | ❌ | ✅ ЯК виглядає |
| r-architect | **проектує** | ✅ | ❌ | ✅ (ADR) | ✅ архітектура |
| r-doc-keeper | **синхронізує** | ✅ | ❌ | ✅ | ❌ (RFC) |
| compliance | **аудитує** | ✅ | ❌ | ❌ | ❌ (RFC) |
| signal-architect | **специфікує** | ✅ | ❌ | ❌ | ✅ signal design |
| smc-mentor | **навчає** | ❌ | ❌ | ❌ | ✅ торговий процес |
| log-analyst | **розслідує + моніторить** | ✅ | ❌ | ❌ | ❌ (findings) |

---

## 10. Shared Tooling (MCP + Context7)

> **Кожен агент** має доступ до зовнішніх інструментів через MCP-сервери.
> Перед використанням інструмент треба завантажити через `tool_search_tool_regex`.

### 10.1 Context7 — Актуальна документація бібліотек

Дозволяє отримати **поточну** документацію будь-якої бібліотеки (не з кешу моделі, а з live-джерела).

| Інструмент | Що робить |
|------------|-----------|
| `mcp_context7_resolve-library-id` | Знайти ID бібліотеки за назвою (напр. `lightweight-charts`, `svelte`, `aiohttp`, `redis-py`) |
| `mcp_context7_get-library-docs` | Отримати документацію бібліотеки за ID |

**Workflow**: resolve ID → get docs → використати в роботі.

**Коли використовувати:**

- Перед використанням API бібліотеки, в якій не впевнений
- Перевірити breaking changes у новій версії
- Знайти правильний синтаксис/паттерн (Svelte 5 runes, LWC 5, aiohttp, etc.)
- При ревью — перевірити чи використання бібліотеки відповідає актуальній документації

### 10.2 aione-trading MCP — Діагностика платформи

Спеціалізовані інструменти для **нашої платформи** (Trading Platform v3). Працюють з запущеною системою.

| Інструмент | Що робить | Хто використовує |
|------------|-----------|------------------|
| `mcp_aione-trading_health_check` | Загальний стан системи (Redis, процеси, порти) | log-analyst, qa-rejector |
| `mcp_aione-trading_platform_status` | Статус платформи (bootstrap, workers, errors) | log-analyst, qa-rejector, bug-hunter |
| `mcp_aione-trading_platform_config` | Поточна конфігурація (config.json parsed) | r-architect, compliance, r-doc-keeper |
| `mcp_aione-trading_log_tail` | Останні рядки логів процесу | log-analyst, bug-hunter |
| `mcp_aione-trading_redis_inspect` | Стан Redis (ключі, пам'ять, TTL) | log-analyst, bug-hunter |
| `mcp_aione-trading_inspect_bars` | Інспекція OHLCV барів (symbol, tf, range) | bug-hunter, smc-chief, smc-trader |
| `mcp_aione-trading_inspect_updates` | Інспекція update-потоку (events, watermarks) | bug-hunter |
| `mcp_aione-trading_derive_chain_status` | Статус derive chain (M1→H4+D1) | bug-hunter, r-architect |
| `mcp_aione-trading_data_files_audit` | Аудит data_v3/ файлів (gaps, integrity) | bug-hunter, qa-rejector |
| `mcp_aione-trading_run_exit_gates` | Запуск quality gates | qa-rejector, patch-master |
| `mcp_aione-trading_ws_server_check` | Стан WebSocket сервера | chart-ux, log-analyst |

### 10.3 GitKraken — Git операції

| Інструмент | Що робить |
|------------|-----------|
| `mcp_gitkraken_git_status` | Статус робочого дерева |
| `mcp_gitkraken_git_log_or_diff` | Перегляд історії та дифів |
| `mcp_gitkraken_git_blame` | Хто і коли змінив рядок |
| `mcp_gitkraken_git_branch` | Управління гілками |
| `mcp_gitkraken_git_add_or_commit` | Stage + commit |

### 10.4 Правила використання

1. **Перед викликом MCP-інструменту** — завантажити його через `tool_search_tool_regex` (обов'язково)
2. **Context7 = перший крок** коли працюєш з бібліотекою. Не покладайся на знання з тренувальних даних
3. **aione-trading tools = живі дані**. Використовуй замість (або разом з) ручних PowerShell команд
4. **Не вигадуй output** MCP-інструмента. Якщо інструмент недоступний — повідом і використай fallback (PowerShell/grep)

---

## 11. Autonomous Tasks (автономні завдання)

> Агенти можуть виконувати завдання **автономно** — без покрокового контролю юзера.
> Рівень довіри залежить від типу завдання: READ-ONLY = безпечно, WRITE = потрібен gate.

### 11.1 Матриця довіри

| Рівень | Що дозволено | Ризик | Приклади |
|--------|-------------|-------|----------|
| **🟢 AUTO** | Автономно, без підтвердження | Нульовий (read-only) | Health check, log analysis, bar inspection, setup evaluation, UI screenshot audit |
| **🟡 SEMI** | Автономно з report → юзер вирішує | Низький (пише тільки docs/ADR) | ADR draft, doc sync, compliance audit, architecture review |
| **🔴 GATED** | Потребує explicit GO на кожен крок | Середній (пише код) | Bug fix, feature implementation, config change |

### 11.2 Хто що може автономно

| Агент | AUTO 🟢 | SEMI 🟡 | GATED 🔴 |
|-------|---------|---------|----------|
| **log-analyst** | ✅ system health, log analysis, post-change verify | — | — |
| **bug-hunter** | ✅ code audit, defect discovery, data quality check | — | — |
| **smc-trader** | ✅ setup evaluation, grade validation, chart audit | — | — |
| **smc-chief** | ✅ doctrine review, display budget audit | — | — |
| **signal-architect** | ✅ signal spec review, confidence calibration audit | 🟡 signal design spec | — |
| **smc-mentor** | ✅ pre-trade check, scenario walkthrough, post-trade review, weekly review | — | — |
| **chart-ux** | ✅ visual audit, DPR check, N1–N12, screenshot | — | — |
| **compliance** | ✅ dependency scan, secrets scan | 🟡 audit report | — |
| **r-architect** | ✅ RECON, alternatives analysis | 🟡 ADR draft | — |
| **r-doc-keeper** | — | 🟡 doc sync (пише тільки docs/) | — |
| **qa-rejector** | ✅ exit gates, invariant check | — | — |
| **patch-master** | — | — | 🔴 завжди з GO |

### 11.3 Готові автономні сценарії

Юзер може запустити будь-який агент з одного з цих промптів. Агент працює автономно і повертає структурований звіт.

---

#### 🟢 PERIODIC: System Health Check (log-analyst)

**Коли**: раз на день, після deployment, або "чи все ок?"

```
Проведи повну перевірку здоров'я системи:
1. Перевір всі процеси (живі? memory? CPU?)
2. Redis: ping, keys count, memory
3. Порти 6379, 8089, 8000 — чи слухають
4. Логи: нові ERROR/WARNING за останню годину
5. data flow: OBS_60S, writer_drops
6. stderr файли: чисті чи є exceptions
Дай структурований звіт з ✅/⚠️/❌ по кожному пункту.
```

---

#### 🟢 PERIODIC: Trading Setup Scan (smc-trader)

**Коли**: перед сесією, або "що там по сетапах?"

```
Оціни поточний стан XAU/USD з точки зору трейдера:
1. Який bias на D1/H4? (структура, останній BOS/CHoCH)
2. Які зони актуальні? (POI grade A+ або A)
3. Чи є sweep liquidity?
4. Який сценарій: WAIT / PREPARE / READY?
5. Якщо є сетап — опиши: entry, SL, TP, R:R
Використай mcp_aione-trading_inspect_bars для реальних даних.
```

---

#### 🟢 PERIODIC: Visual UI Audit (chart-ux)

**Коли**: після UI змін, або "як виглядає чарт?"

```
Відкрий браузер (http://127.0.0.1:8000/) і зроби візуальний аудит:
1. Зроби скріншот чарту XAU/USD M15
2. Перевір N1–N12 (overlay не перекриває chart? contrast OK? тощо)
3. Перевір DPR rendering (чи не розмитий canvas?)
4. Перевір themes (dark/light перемикання)
5. Дай Screenshot Audit Table з findings
```

---

#### 🟢 PERIODIC: Code Quality Audit (bug-hunter)

**Коли**: раз на тиждень, або "перевір якість коду"

```
Проведи аудит якості коду:
1. Запусти exit gates (mcp_aione-trading_run_exit_gates)
2. Перевір I0–I6 інваріанти (dependency rule, UDS writes тощо)
3. Знайди silent except handlers (Z6 violations)
4. Знайди hardcoded values що мають бути в config.json
5. Перевір SSOT точки: чи немає drift?
Дай Defect Ledger з severity та evidence.
```

---

#### 🟢 PERIODIC: Data Integrity Check (bug-hunter)

**Коли**: раз на тиждень, "чи дані ок?"

```
Перевір цілісність даних:
1. mcp_aione-trading_data_files_audit — gaps, integrity
2. mcp_aione-trading_derive_chain_status — каскад M1→H4+D1
3. mcp_aione-trading_inspect_bars — геометрія часу (I2) для XAU/USD по кожному TF
4. Перевір dedup: чи немає дублікатів по open_ms
5. Перевір final>preview: чи немає змішування (I3)
Дай звіт по кожному TF з ✅/❌.
```

---

#### 🟡 ON-DEMAND: Architecture Review (r-architect)

**Коли**: "проаналізуй архітектуру X", "чи потрібен ADR для Y?"

```
Проведи архітектурний аналіз <тема>:
1. RECON: поточний стан, які файли задіяні, data lineage
2. Failure model: 3–5 сценаріїв поломки
3. SSOT check: де правда? скільки копій?
4. Alternatives: мінімум 2 варіанти з trade-offs
5. Рекомендація: PATCH достатньо чи потрібен ADR?
Дай структурований RECON report.
```

---

#### 🟡 ON-DEMAND: Documentation Sync (r-doc-keeper)

**Коли**: після серії змін, "синхронізуй доки"

```
Перевір синхронізацію документації:
1. docs/system_current_overview.md — чи відповідає поточному коду?
2. docs/adr/index.md — чи всі ADR в індексі?
3. AGENTS.md — чи відповідає реальності?
4. docs/contracts.md — чи контракти актуальні?
5. Знайди drift і виправ (тільки docs/ файли).
```

---

#### 🟡 ON-DEMAND: Compliance Scan (compliance)

**Коли**: перед push, після додавання залежності

```
Проведи compliance scan:
1. Перевір requirements.txt на CVE (використай Context7 для кожної залежності)
2. Перевір .env/.gitignore — чи секрети захищені
3. Перевір порти — чи не exposed publicly
4. Перевір OWASP top-10 для HTTP endpoints
5. Дай risk register з severity та remediation plan.
```

---

#### 🟢 PERIODIC: SMC Doctrine Audit (smc-chief)

**Коли**: після змін у SMC engine, "чи доктрина дотримана?"

```
Перевір дотримання Clean Chart Doctrine:
1. Display budget: скільки зон видно на M15? (має бути ≤ budget)
2. Zone lifecycle: чи expired/mitigated зони прибираються?
3. Grade system: чи A+/A/B/C відповідають confluence scoring?
4. Cross-TF projection: чи HTF зони правильно проектуються?
5. Дай Decision Record якщо знайдеш порушення доктрини.
```

---

#### 🟢 ON-DEMAND: Pre-Trade Mentor Check (smc-mentor)

**Коли**: "чи входити?", "pre-trade check", "перевір мій аналіз", "що думаєш?"

```
Проведи менторський pre-trade check (DarkTrader Protocol):
1. MACRO: чи є high-impact news? який день? скільки trades сьогодні?
2. HTF BIAS: D1 bias? H4 bias? Aligned чи conflict?
3. STRUCTURE: M15 — останній BOS/CHoCH? Premium/Discount?
4. ZONE: тип, confluence count (sweep, FVG, HTF, extremum, session, P/D, momentum)
5. IOFED: яка стадія? (якщо не ⑤ — WAIT)
6. RISK: SL/TP/R:R ≥ 2:1? Position ≤ 2%?
7. ⚠️ THIN ICE: де конкретно легко помилитись у цьому сценарії?
Дай Pre-Trade Checklist + Mentor Verdict (ENTRY/WAIT/NO TRADE) + coaching note.
```

---

#### 🟢 PERIODIC: Weekly Mentor Review (smc-mentor)

**Коли**: "weekly review", "розбери мій тиждень", кінець тижня

```
Проведи менторський тижневий огляд:
1. JOURNAL REVIEW: trades, W/L/BE, process scores, найкращий/найгірший trade
2. BIAS ACCURACY: скільки разів bias визначений правильно?
3. MISSED SETUPS: чи були A+ setups що учень пропустив? ЧОМУ?
4. PATTERN: яка помилка повторюється? (P1–P12 pitfalls)
5. NEXT WEEK PREP: key levels D1/H4, потенційні зони, high-impact news
6. MENTAL STATE: рівень стресу, drawdown status, ready to trade?
Дай структурований Weekly Mentor Report з action items.
```

---

### 11.4 Правила автономної роботи

1. **READ-ONLY агенти (🟢)** — працюють повністю автономно. Результат = звіт. Жодних правок.
2. **SEMI-AUTO агенти (🟡)** — працюють автономно до точки рішення. Результат = draft (ADR/docs/report). Юзер вирішує чи приймати.
3. **GATED агенти (🔴)** — кожен крок з підтвердженням. patch-master НІКОЛИ не працює автономно без GO.
4. **Будь-який автономний результат** з findings severity ≥ S1 → автоматично ескалюється до юзера.
5. **Агент зобов'язаний** використовувати MCP tools (не тільки grep/read) для живих даних.
6. **Автономний звіт = structured** (не вільний текст). Таблиці, чеклісти, severity, evidence.
7. **Якщо агент виявив щось критичне** (S0: data corruption, crash) — НЕГАЙНО повідомити юзера, не чекати кінця аудиту.
