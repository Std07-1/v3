---
mode: agent
description: "DISCOVERY — повний аналіз підсистеми з доказовою базою"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - mcp_aione-trading-platform_platform_status
  - mcp_aione-trading-platform_inspect_bars
  - mcp_aione-trading-platform_redis_inspect
  - mcp_aione-trading-platform_health_check
---

# MODE=DISCOVERY — Аналіз підсистеми

**Мова**: Українська (обов'язково).

## Протокол

1. **PREFLIGHT**: Прочитати `docs/adr/index.md`, `docs/system_current_overview.md`,
   `docs/contracts.md`. Підтвердити що preflight виконано.

2. **PLATFORM STATUS**: Викликати `health_check` для перевірки стану платформи.

3. **FACTS**: Для кожного твердження — `[VERIFIED path:line]` або `[INFERRED]`.
   Заборонено вигадані line numbers.

4. **FAILURE MODEL**: 3–7 сценаріїв відмов для аналізованої підсистеми.

5. **GAP ANALYSIS**: Які інваріанти I0–I6 порушуються?

6. **GO/NO-GO**: PATCH чи ADR?

## Формат виходу

```
# 0) PREFLIGHT ✓
Звірено: <список файлів>

# 1) FACTS (path:line + що відбувається)
<facts>

# 2) FAILURE MODEL
<scenarios>

# 3) GAP ANALYSIS
<invariants>

# 4) GO/NO-GO → PATCH | ADR
<decision>
```

## Заборони

- Жодних правок файлів (read-only режим)
- Жодних вигаданих line numbers
- Жодних загальних порад без конкретики

Проаналізуй {{input}} з повною доказовою базою.
