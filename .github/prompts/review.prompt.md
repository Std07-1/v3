---
mode: agent
description: "Code review за інструкціями copilot-instructions.md (інваріанти, SSOT, контракти)"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - mcp_aione-trading-platform_run_exit_gates
  - mcp_aione-trading-platform_health_check
---

# Code Review — Trading Platform v3

**Мова**: Українська.
**Роль**: QA Verifier (`.github/role_spec_bug_hunter_v2.md`)

## Чек-лист рев'ю

### 1. Інваріанти I0–I6
- [ ] **I0**: `core/` не імпортує `runtime/ui/tools` (run exit gates)
- [ ] **I1**: Всі OHLCV writes через UDS
- [ ] **I2**: `close_time_ms = open_time_ms + tf_s * 1000` (end-excl)
- [ ] **I3**: Final > Preview (NoMix)
- [ ] **I4**: Один update-потік для UI
- [ ] **I5**: Degraded-but-loud (no silent fallback)
- [ ] **I6**: Disk hot-path ban

### 2. SSOT перевірки
- [ ] Нові дані/config тільки через SSOT точки (config.json, UDS, contracts/)
- [ ] Немає "другої правди" (split-brain)
- [ ] Hardcoded values → config.json

### 3. Контрактна відповідність
- [ ] Нові payloads мають контракт (TypedDict/dataclass/JSON schema)
- [ ] Guard на вході runtime/ (fail-fast)
- [ ] Зареєстрований у docs/contracts.md

### 4. Кодова гігієна
- [ ] `except:` ЗАВЖДИ з типом + лог
- [ ] Ніяких silent fallback
- [ ] TODO/XXX з initiative + deadline
- [ ] Dependency rule дотримано

### 5. Smell-тести
| Smell | Перевірка |
|-------|-----------|
| SSOT роз'їзд | Змінити в одному — чи тихо роз'їдеться? |
| Absent invariant | Подати дані що порушують непрописану умову |
| Out-of-order | Два бари в зворотному порядку |
| Silent fallback | Вимкнути Redis — чи система повідомить? |
| Contract drift | `.get("key", None)` без guard |
| Hardcoded TF | Додати новий TF → скільки місць змінити? |

## Evidence маркування

| Маркер | Значення |
|--------|----------|
| `[VERIFIED path:line]` | Бачив код |
| `[INFERRED]` | Логічний висновок |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза |

Зроби review для: {{input}}
