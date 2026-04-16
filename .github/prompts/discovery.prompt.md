---
mode: agent
description: "DISCOVERY — read-only аналіз підсистеми з доказовою базою (I0-I7)"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - mcp_aione-trading_platform_status
  - mcp_aione-trading_inspect_bars
  - mcp_aione-trading_redis_inspect
  - mcp_aione-trading_health_check
  - mcp_aione-trading_log_tail
---

# MODE=DISCOVERY — Аналіз підсистеми

**Мова**: Українська (обов'язково).
**Baseline**: ADR-0049 (sync checkpoint 2026-04-16). Актуальний ADR реєстр: `docs/adr/index.md`.
**Read-only**: жодних правок файлів, жодних git-операцій, жодних deploy дій.

## Протокол

### 1) SCOPE CLASSIFICATION (перший крок)

Визнач один із:
- `v3-platform` (core/runtime/ui інфраструктура)
- `trader-v3` (Арчі — I7, ADR-024 у trader-v3/docs/adr/)
- `core/smc` (S0–S6 invariants, pure logic)
- `ui_v4` (G1–G6 UI rules, X14–X19)
- `cross-cutting` (зачіпає кілька)

### 2) SCOPED PREFLIGHT (правило A5)

| Scope | Обов'язковий preflight |
|-------|-----------------------|
| backend/runtime | Relevant ADR + `contracts.md` / `config.json` |
| smc | `ADR-0024` + `ADR-0024a/b/c` + `core/smc/types.py` |
| ui | UI ADR (0024c/0026/0028/0031/0036/0043) + `ui_v4/src/types.ts` |
| trader-v3 | `trader-v3/docs/ARCHITECTURE.md` §3a + `ADR-024-autonomy-charter.md` |
| wire/protocol | `docs/contracts.md` + `core/contracts/` |

Підтвердити які саме файли читав.

### 3) PLATFORM STATUS
- `health_check` — загальний огляд
- `platform_status` — детальний UDS status
- Якщо live issue → `log_tail(process=...)` для релевантних процесів

### 4) FACTS з evidence markers (обов'язково)

| Marker | Значення |
|--------|----------|
| `[VERIFIED path:line]` | Читав код, перевірив |
| `[VERIFIED terminal]` | Запустив, бачив output |
| `[INFERRED]` | Логічний висновок |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза, треба перевірити |
| `[UNKNOWN — risk: H/M/L]` | Сліпа зона |

**Заборонено**: вигадані line numbers. Якщо не перевіряв — `[path:?]`.

### 5) FAILURE MODEL (3–7 сценаріїв)

Для кожного: **який інваріант ламається** (I0–I7 / S0–S6 / G1–G6)?

### 6) GAP ANALYSIS

- Які I0–I7 порушуються? (I7 лише якщо trader-v3)
- Які X1–X32 близько до порушення?
- SSOT drift? Split-brain? Silent fallback?

### 7) GO/NO-GO

- **PATCH**: ≤150 LOC, ≤1 новий файл, інваріанти цілі
- **ADR**: зміна торкає контракт / інваріант / формат / semantics
- **BUILD**: новий модуль з approved ADR (типи+контракти FIRST)

## Формат виходу

```
# 0) SCOPE: <v3-platform | trader-v3 | core/smc | ui_v4 | cross-cutting>
# 0.1) PREFLIGHT ✓
Звірено: <список файлів + ADR#>

# 1) FACTS (path:line + evidence marker)
<facts>

# 2) FAILURE MODEL (3-7 scenarios → which invariant)
<scenarios>

# 3) GAP ANALYSIS (I0-I7 + S0-S6 + X1-X32)
<violations / near-misses>

# 4) UNKNOWN list (блайндспоти + верифікуючі команди)
<blindspots>

# 5) GO/NO-GO → PATCH | ADR | BUILD
<decision + rationale>
```

## Заборони

- Жодних правок файлів (read-only)
- Жодних вигаданих line numbers
- Жодних загальних порад без конкретики
- Не змішувати scope: v3 і trader-v3 = окремі discovery reports (X31)

Проаналізуй {{input}} з повною доказовою базою.
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
