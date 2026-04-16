---
mode: agent
description: "Code review — I0-I7, S0-S6, G1-G6, X1-X32 + R_REJECTOR final gate"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - get_errors
  - mcp_aione-trading_run_exit_gates
  - mcp_aione-trading_health_check
---

# Code Review — Trading Platform v3

**Мова**: Українська.
**Baseline**: ADR-0049 (2026-04-16).
**Роль**: R_BUG_HUNTER (`.github/role_spec_bug_hunter_v2.md`) з фінальним gate через R_REJECTOR.

## Чек-лист рев'ю

### 1. Платформенні інваріанти I0–I7
- [ ] **I0**: `core/` не імпортує `runtime/ui/tools` (exit gates: dependency rule)
- [ ] **I1**: Всі OHLCV writes через UDS (UI = read-only; жодного direct Redis write)
- [ ] **I2**: `close_time_ms = open_time_ms + tf_s*1000` (CandleBar end-excl, Redis end-incl — конвертація лише на межі Redis)
- [ ] **I3**: Final > Preview (NoMix) — один key = один final source
- [ ] **I4**: Один update-потік для UI (events через /api/updates)
- [ ] **I5**: Degraded-but-loud (no bare `except:`, no silent fallback, degraded[] явний)
- [ ] **I6**: Stop-rule — зміна не ламає інші інваріанти (інакше → ADR)
- [ ] **I7**: Autonomy-first — **тільки `trader-v3/`**: жодних прихованих hard-блоків для Арчі (ADR-024 у trader-v3)

### 2. SMC-специфічно (`core/smc/**`, `runtime/smc/**`)
- [ ] **S0**: pure logic, NO I/O в `core/smc/`
- [ ] **S1**: SMC не пише в UDS/SSOT JSONL (read-only overlay)
- [ ] **S2**: determinism — same bars → same zones
- [ ] **S3**: deterministic zone IDs (`{kind}_{symbol}_{tf_s}_{anchor_ms}`)
- [ ] **S4**: `on_bar()` < `smc.max_compute_ms` (runtime rail)
- [ ] **S5**: config SSOT — всі параметри з `config.json:smc`, no hardcoded thresholds
- [ ] **S6**: wire format = `core/smc/types.py` → `ui_v4/src/lib/types.ts`

### 3. UI-специфічно (`ui_v4/**`)
- [ ] **G1**: UI = read-only renderer (thin controller)
- [ ] **G2**: Canvas/DPR коректно (retina-sharp, subpixel aligned)
- [ ] **G3**: Screenshot Audit Table є (X14 — без цього "done" ЗАБОРОНЕНО)
- [ ] **G4**: Negative Checklist N1–N12 пройдений
- [ ] **G5**: Contradiction Audit CA1–CA10 пройдений
- [ ] **G6**: UI phases не перескакуються

### 4. SSOT перевірки
- [ ] Нові дані/config → тільки через SSOT (config.json, UDS, contracts/)
- [ ] Немає split-brain (другого джерела правди)
- [ ] Hardcoded values → `config.json`
- [ ] **X28**: UI не re-derives backend SSOT (label / grade / bias / phase / scenario)

### 5. Контрактна відповідність
- [ ] Нові payloads мають контракт (TypedDict/dataclass/JSON schema)
- [ ] Guard на вході `runtime/` (fail-fast)
- [ ] Зареєстровано у `docs/contracts.md`
- [ ] **K4 Adjacent**: collection shape / Optional / Protocol → сусідні annotations оновлено

### 6. Кодова гігієна
- [ ] `except:` завжди з типом + лог (I5)
- [ ] Ніяких silent fallback
- [ ] TODO/XXX → initiative + deadline + degraded-but-loud
- [ ] Dependency rule (I0)
- [ ] **X13 trap**: `bar.low` (не `.l`) для CandleBar; wire dict `"l"` ≠ dataclass `.low`

### 7. Stop-list X1–X32 (ключові smell-тести)
| Smell | Перевірка |
|-------|-----------|
| SSOT роз'їзд | Змінити в одному місці — чи тихо роз'їдеться? |
| Absent invariant | Подати дані що порушують непрописану умову |
| Out-of-order | Два бари в зворотному порядку → що станеться? |
| Silent fallback (X9) | Вимкнути Redis — чи система повідомить? |
| Contract drift | `.get("key", None)` без guard |
| Hardcoded TF (X5) | Додати новий TF → скільки місць треба змінити? |
| **X13** | `bar.l` замість `bar.low` |
| **X24** | diagnostics errors у touched files перед changelog |
| **X28** | UI перемальовує backend grade/label/phase |
| **X29–X31** | trader-v3 hard-блок без safety justification / cross-repo contamination |
| **X32** | data/ dumping у v3 root |

### 8. Evidence маркування

| Маркер | Значення |
|--------|----------|
| `[VERIFIED path:line]` | Бачив код |
| `[INFERRED]` | Логічний висновок |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза |

### 9. Final gate (обов'язковий перед "review passed")
Викликати R_REJECTOR: `.github/role_spec_rejector_v1.md` — contradiction-seeking audit, evidence quality, completeness.

Зроби review для: {{input}}
