# Evidence Quality Check Skill

**Призначення**: перевірити що твердження у відповіді мають реальні докази, не вигадані.
**Коли викликати**: при будь-якому RECON / DISCOVERY / review / ADR.

## Канонічні маркери (SSOT)

| Маркер | Значення | Приклад |
|--------|----------|---------|
| `[VERIFIED path:line]` | Бачив код, перевірив | `[VERIFIED core/smc/engine.py:42]` |
| `[VERIFIED terminal]` | Запустив, побачив output | `[VERIFIED terminal: pytest passed]` |
| `[INFERRED]` | Логічний висновок з даних | `[INFERRED from naming pattern]` |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза, треба перевірити | `[ASSUMED — verify: grep "FINAL_SOURCES"]` |
| `[UNKNOWN — risk: H/M/L]` | Сліпа зона | `[UNKNOWN — risk: M (legacy code path)]` |
| `[path:?]` | Шлях відомий, line — НЕ перевірений | `[runtime/store/uds.py:?]` |

## Forbidden anti-patterns

### A1 — Fabricated line numbers
❌ `core/smc/engine.py:42` коли ти не читав файл
✅ `core/smc/engine.py:?` або `[INFERRED]`

### A2 — "Should be" disguised as fact
❌ "Function X handles error correctly"
✅ "Function X **appears to handle** error correctly [INFERRED — not VERIFIED with terminal run]"

### A3 — Phantom imports
❌ "Module Y imports Z" коли ти не grep'ав
✅ "Module Y likely imports Z [ASSUMED — verify: grep 'import Z' Y.py]"

### A4 — Hand-wave conclusions
❌ "Tests cover this case"
✅ "Tests cover this case [VERIFIED tests/test_X.py:N test_case_Y]"

### A5 — Stale memory
❌ Цитуєш файл по пам'яті як "I remember it says..."
✅ `read_file` зараз → `[VERIFIED path:line]`

## Audit checklist

### Перед здачею тверджень:
1. [ ] Кожне твердження про код має маркер
2. [ ] `[VERIFIED path:line]` — line був реально прочитаний цим запитом? (не з summary, не з пам'яті)
3. [ ] `[INFERRED]` — ясно що це не factual, а логічний висновок
4. [ ] `[UNKNOWN]` чесно прописані з risk level
5. [ ] Жодного fabricated line number

### Червоні прапори у власній відповіді:
- "Це працює" без `[VERIFIED terminal]`
- "У файлі X є функція Y" без `[VERIFIED X:line]`
- "Я перевірив" без showing how
- "Я впевнений" — це слово майже завжди = no evidence

## Output format

При повідомленні висновків після RECON:

```markdown
## FACTS
- [VERIFIED core/smc/engine.py:128] SmcEngine.on_bar() returns None on stale data
- [VERIFIED terminal: pytest tests/test_smc_e1.py] all 12 tests pass
- [INFERRED] config drift у smc.max_compute_ms (default 50ms у code, 30ms у docs)
- [ASSUMED — verify: grep "DERIVE_CHAIN"] DERIVE_CHAIN визначений тільки у core/derive.py

## UNKNOWN
- [UNKNOWN — risk: M] чи D1 anchor offset актуальний для weekend gap (не тестовано)

## EVIDENCE QUALITY: HIGH (3 VERIFIED, 1 INFERRED, 0 fabricated)
```

## Правило використання

- **Будь-який RECON / DISCOVERY** = обов'язкове маркування
- **R_REJECTOR**: окремо аудитує evidence quality (CA4)
- **R_PATCH_MASTER**: проводить evidence-check у RECON gate
- **R_BUG_HUNTER**: ніяких висновків без VERIFIED маркерів
