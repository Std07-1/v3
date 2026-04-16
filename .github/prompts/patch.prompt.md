---
mode: agent
description: "PATCH — мінімальний хірургічний фікс з R_REJECTOR gate (I0-I7, K3-K6)"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - replace_string_in_file
  - create_file
  - get_errors
  - mcp_aione-trading_health_check
  - mcp_aione-trading_run_exit_gates
---

# MODE=PATCH — Хірургічний фікс

**Мова**: Українська.
**Baseline**: ADR-0049 (Wake Engine External Consumer IPC) — sync checkpoint 2026-04-16.
**SSOT rules**: `.github/copilot-instructions.md` + `AGENTS.md`.

## Попередні умови

- DISCOVERY вже виконано (facts + gap + go/no-go = PATCH)
- ADR існує або не потрібен (зміна не торкається інваріантів I0–I7 / контракту / формату)
- **Scope classified**: v3 platform АБО `trader-v3/` (Арчі — I7, X31, X32)

## Протокол PATCH

### GATE 1 → DESIGN
- [ ] Root cause з evidence `[VERIFIED path:line]`
- [ ] Proof pack з repro steps (≤6 команд)
- [ ] Failure model ≥3 сценаріїв (і який invariant кожен ламає)
- [ ] Scope: `v3-platform` | `trader-v3` | `ui_v4` | `core/smc` | `cross-cutting`

### GATE 2 → CUT
- [ ] Fix point визначений (одна зміна, max ефект)
- [ ] SSOT routing перевірений (правило 3-х місць → централізуй)
- [ ] I0–I7 поштучно ✓ (I7 лише якщо trader-v3/)
- [ ] Alternatives ≥2 (чому не обрав)
- [ ] X1–X32 перевірено (жодна заборона не зачеплена)
- [ ] Adjacent contract (K4): collection shape / Optional / Protocol → чи сусідні annotations оновлено?

### GATE 3 → SELF-AUDIT (10/10 перед verify)
- [ ] root cause confirmed
- [ ] I0 (dependency rule: core/ ← нічого з runtime/ui/tools)
- [ ] I1 (UDS вузька талія — всі OHLCV writes через UDS)
- [ ] I2 (геометрія: CandleBar end-excl, Redis end-incl — конвертація лише на межі Redis)
- [ ] I3 (final > preview, NoMix)
- [ ] I4 (один update-потік для UI)
- [ ] I5 (degraded-but-loud — no bare `except:`, no silent fallback)
- [ ] I6 (stop-rule — якщо ламає I0-I5,I7 → PATCH зупинити, писати ADR)
- [ ] I7 (autonomy-first — **лише trader-v3/**; жодних прихованих hard-блоків для Арчі)
- [ ] SSOT не порушено (жодного split-brain)
- [ ] Mutation sites audit (grep усі сайти поля/типу)
- [ ] Blast radius задокументовано
- [ ] Rollback steps явні
- [ ] X13 trap: `bar.low` (не `.l`) для CandleBar; wire dict `"l"` ≠ dataclass `.low`

### GATE 4 → R_REJECTOR (**mandatory перед "done"**)
**НЕ повідомляти замовнику "готово" без R_REJECTOR gate**.
Виклик: `.github/role_spec_rejector_v1.md` — 10 contradiction-seeking питань,
evidence quality audit, hidden-assumption hunt, completeness check.

## Бюджет

- ≤150 LOC (інакше split у P-slices ≤3 файли кожен, K6)
- ≤1 новий файл без ADR
- ≥1 runtime rail (якщо зачіпає I0–I5)
- ≥1 test (positive + edge)
- **Zero diagnostics (K3)**: `get_errors()` = 0 для всіх touched files перед changelog

## POST

- Запустити exit gates: `run_exit_gates`
- Запустити pytest для зачеплених тестів
- S0/S1 → changelog.jsonl entry обов'язковий (J1), з `adr_ref`, `rollback_steps`
- S2/S3 → git commit достатньо
- К5 (ADR status gate): якщо торкнувся feature flag у config.json — перевір що ADR = Accepted/Implemented/Active/Done

## Cross-repo boundary (X31, X32)

Якщо PATCH торкає `trader-v3/`:
- ADR Арчі живе **тільки** у `trader-v3/docs/adr/`
- **Жодних** змін у `v3/docs/adr/`, `core/`, `runtime/`, `ui_v4/`, `config.json`
- Runtime data Арчі (`*_directives.json`, `*_conversation.json`, `*_journal.json`) — **тільки** у `trader-v3/data/` або на VPS (X32)
- Перед додаванням будь-якого обмеження до Арчі → прочитати `trader-v3/docs/adr/ADR-024-autonomy-charter.md`. `if blocked:` без пояснення Арчі = I7 violation.

Виконай патч для: {{input}}
---
mode: agent
description: "PATCH — мінімальний хірургічний фікс з verify"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - replace_string_in_file
  - create_file
  - mcp_aione-trading-platform_health_check
  - mcp_aione-trading-platform_run_exit_gates
---

# MODE=PATCH — Хірургічний фікс

**Мова**: Українська.

## Попередні умови

- DISCOVERY вже виконано (facts + gap + go/no-go = PATCH)
- ADR існує або не потрібен (зміна не торкається інваріантів)

## Протокол PATCH

### GATE 1 → DESIGN
- [ ] Root cause з evidence (path:line)
- [ ] Proof pack з repro steps
- [ ] Failure model ≥3 сценаріїв

### GATE 2 → CUT
- [ ] Fix point визначений (одна зміна, max ефект)
- [ ] SSOT routing перевірений
- [ ] I0–I6 поштучно ✓
- [ ] Alternatives ≥2

### GATE 3 → DONE (self-check 10/10)
- [ ] root cause
- [ ] I0 (dependency rule)
- [ ] I1 (UDS вузька талія)
- [ ] I2 (геометрія часу)
- [ ] I3 (final > preview)
- [ ] I5 (degraded-but-loud)
- [ ] SSOT не порушено
- [ ] mutation sites audit
- [ ] blast radius
- [ ] rollback steps

## Бюджет
- ≤150 LOC
- ≤1 новий файл
- ≥1 runtime rail
- ≥1 test (positive + edge)

## POST
- Запустити exit gates: `run_exit_gates`
- Записати в changelog.jsonl

Виконай патч для: {{input}}
