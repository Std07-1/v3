---
mode: agent
description: "ADR — Architecture Decision Record для нетривіальних змін"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - create_file
  - mcp_aione-trading-platform_platform_config
  - mcp_aione-trading-platform_health_check
---

# MODE=ADR — Architecture Decision Record

**Мова**: Українська.

## Коли ADR обов'язковий

- Новий модуль/підсистема
- Зміна SSOT config або формату контракту
- Зміна інваріантів I0–I6
- Нетривіальне рішення що визначає траєкторію системи
- Зміна семантики часу/серій/stitching

## Протокол

### 1. Preflight
Прочитай `docs/adr/index.md` — визнач наступний номер ADR.

### 2. Дослідження
- Зібрати FACTS (path:line) про поточну поведінку
- `platform_config` — перевірити SSOT конфіг
- Визначити alternatives (мінімум 2)

### 3. Створення ADR

Створити файл `docs/adr/NNNN-<коротка-назва>.md`:

```markdown
# ADR-NNNN: <Назва>
- **Статус**: Proposed
- **Дата**: YYYY-MM-DD
- **Автор**: <хто ініціював>
- **Initiative**: <initiative_id>

## Контекст і проблема
<Що зламано / чого бракує. Посилання на код path:line та попередні ADR.>

## Розглянуті варіанти
1. <Варіант A> — <плюси/мінуси>
2. <Варіант B> — <плюси/мінуси>

## Рішення
<Що вибрали і чому. Як вписується в A→C→B, інваріанти, SSOT.>

## Наслідки
- Що змінюється (файли, контракти, тести)
- Які гейти потрібно додати/адаптувати
- Вплив на продуктивність/SLO

## Rollback
<Конкретні кроки відкату.>
```

### 4. Оновити індекс
Додати запис у `docs/adr/index.md`.

Створи ADR для: {{input}}
