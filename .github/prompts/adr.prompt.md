---
mode: agent
description: "ADR — Architecture Decision Record для нетривіальних змін (v3 або trader-v3)"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - create_file
  - mcp_aione-trading_platform_config
  - mcp_aione-trading_health_check
---

# MODE=ADR — Architecture Decision Record

**Мова**: Українська.
**Baseline**: v3 next ADR = **0050** (last: 0049 Wake Engine). Для trader-v3 next = **040** (last: 039 Platform Wake Integration).
**X31 boundary**: v3 ADR → `docs/adr/`. Арчі ADR → `trader-v3/docs/adr/`. **Ніколи не змішувати**.

## Коли ADR обов'язковий

- Новий модуль/підсистема (queue, scheduler, store, протокол)
- Зміна SSOT config або формату контракту
- Зміна інваріантів **I0–I7** (I7 — для trader-v3)
- Нетривіальне рішення що визначає траєкторію системи
- Зміна семантики часу/серій/previous_close/stitching/zone-lifecycle
- Нова integration (broker, exchange, external service)
- Breaking change у wire format / UI types

## Протокол

### 1. Scope & Preflight

Визнач repo:
- `v3` → preflight: `docs/adr/index.md` (next 0050), `docs/system_current_overview.md`, `docs/contracts.md`
- `trader-v3` → preflight: `trader-v3/docs/adr/index.md` (next 040), `trader-v3/docs/ARCHITECTURE.md`, `trader-v3/docs/adr/ADR-024-autonomy-charter.md` (I7 guardrail)

### 2. Дослідження

- FACTS (`[VERIFIED path:line]`) про поточну поведінку
- `platform_config` — SSOT конфіг
- Alternatives мінімум 2 (з плюсами/мінусами)
- Blast radius: які файли/контракти/тести зачіпаються
- Compatibility: breaking change? migration path?

### 3. Створення ADR

**v3**: `docs/adr/NNNN-<коротка-назва>.md` (NNNN = 4-digit, next 0050)
**trader-v3**: `trader-v3/docs/adr/ADR-NNN-<коротка-назва>.md` (NNN = 3-digit, next 040)

```markdown
# ADR-NNNN: <Назва>

- **Статус**: Proposed | Accepted | Implemented | Deprecated
- **Дата**: YYYY-MM-DD
- **Автор**: <хто ініціював>
- **Initiative**: <initiative_id>
- **Supersedes**: <NNNN якщо заміщує> (optional)

## Контекст і проблема

<Що зламано / чого бракує. Посилання на код path:line та попередні ADR.>

## Розглянуті варіанти

1. **<Варіант A>** — <плюси/мінуси>
2. **<Варіант B>** — <плюси/мінуси>
3. **<Варіант C>** (якщо доречний)

## Рішення

<Що вибрали і чому. Як вписується в A→C→B / інваріанти I0–I7 / SSOT / Dependency Rule.>

## Наслідки

- Файли що змінюються
- Контракти що оновлюються (`docs/contracts.md`)
- Нові гейти / тести
- Вплив на SLO / performance
- Breaking change для UI / зовнішніх клієнтів?
- **X31/X32** compliance (якщо cross-repo)

## Rollback

<Конкретні кроки відкату. Що саме git-revert / data-migrate / config-set.>

## Open questions

<Що лишилось невизначеним — як будемо це вирішувати?>
```

### 4. Оновити індекс

- v3: `docs/adr/index.md` (додати рядок у реєстр)
- trader-v3: `trader-v3/docs/adr/index.md`
- Якщо supersedes — старий ADR → Deprecated, додати cross-ref

### 5. Governance gates

- **K5**: якщо ADR вводить config flag, у `config.json` = `enabled: false` поки status=Proposed
- **K6**: ADR-driven slice ≤3 файли + окремий verify
- **B3**: оновити ADR index

### 6. Cross-repo check (X31)

Якщо ADR — Арчі-specific (trader-v3) але має platform-side consumer → потрібно ДВА ADR:
- Приклад: ADR-0049 (v3, WakeEngine external consumer IPC) + ADR-039 (trader-v3, platform integration)
- Чітко розділити perspectives

Створи ADR для: {{input}}
