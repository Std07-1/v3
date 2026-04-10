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
