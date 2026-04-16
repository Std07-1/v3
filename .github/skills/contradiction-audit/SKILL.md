# Contradiction Audit Skill

**Призначення**: знайти внутрішні протиріччя у власній відповіді / patch / ADR ПЕРЕД здачею.
**Коли викликати**: будь-який агент перед "done"; R_REJECTOR використовує як обов'язковий gate.

## Протокол CA1-CA10

### CA1 — Самозаперечення
Чи відповідь містить два твердження які не можуть бути одночасно істинними?

Приклад violation:
> "Patch є мінімальним (15 LOC)" + "Додав 3 нових файли"

### CA2 — Invariant violation
Чи patch порушує один з I0-I7?

| I | Перевірка |
|---|-----------|
| I0 | Чи `core/` тепер імпортує `runtime/` / `ui*/` / `tools/`? |
| I1 | Чи писали в Redis OHLCV поза UDS? |
| I2 | Чи переплутані end-incl / end-excl? |
| I3 | Чи preview може перебити final? |
| I4 | Чи додано паралельний update path? |
| I5 | Чи є silent except / fallback? |
| I6 | Чи зупинились коли треба? |
| I7 | (trader-v3) Чи додано hidden hard block для Арчі? |

### CA3 — Stop-list violation (X1-X32)
Особливо часті:
- X9: `except:` без log
- X13: `bar.l` замість `bar.low`
- X28: UI re-derives backend SSOT
- X29-X30: trader-v3 hidden constraint

### CA4 — Evidence quality
Чи є fabricated `[VERIFIED path:line]` без реального `read_file`?
Якщо line number — здогадка, треба `[path:?]` або `[INFERRED]`.

### CA5 — Scope creep
Чи patch торкає більше ніж заявлено у RECON? Якщо так — це 2 patches, розщепити.

### CA6 — Adjacent contract (K4)
Якщо змінювали:
- Collection shape (list→deque, set→frozenset)
- `Optional[T]` додано/прибрано
- Config dataclass field
- Protocol/TypedDict field
- Dict key type

→ Чи перевірено всі залежні type annotations?

### CA7 — Diagnostics zero (K3)
Чи є lint/type errors у touched files? Якщо так — це не done.

### CA8 — ADR Status (K5)
Чи `enabled: true` для feature з ADR статусом ≠ Accepted/Implemented/Active?

### CA9 — Cross-repo (X31)
Якщо це trader-v3 task — чи є зміни у v3 platform файлах?
Якщо v3 task — чи є зміни у trader-v3?

### CA10 — Documentation drift
S0/S1 changes — чи оновлено: changelog.jsonl + docs/adr/index.md (якщо новий ADR) + AGENTS.md (якщо архітектурне)?

## Output format

```markdown
## Contradiction Audit
- CA1: ✅ no self-contradictions
- CA2: ✅ I0-I7 clean | ❌ I3 violation: <details>
- CA3: ⚠️ X9 risk: line N has bare except — review
- ...
- CA10: ✅ changelog updated, ADR index synced

VERDICT: PASS / FAIL
BLOCKERS: <list if FAIL>
```

## Правило використання

- **R_REJECTOR**: обов'язково CA1-CA10 перед verdict
- **R_PATCH_MASTER**: CA2, CA3, CA5, CA6, CA7 у Self-Audit gate
- **R_ARCHITECT**: CA1, CA8, CA9 перед commit ADR
- **R_CHART_UX**: CA1-CA10 повний (UI має жорсткі rules)
