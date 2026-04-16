---
applyTo: "tests/**"
---

# tests/ — Testing conventions

**Runner**: `python -m pytest tests/ -v`.

## Структура тестів

- `tests/test_smc_*.py` — SMC engine (ADR-0024, S0-S6)
- `tests/test_uds_*.py` — UDS compliance
- `tests/test_s{1-6}_*.py` — SSOT invariants
- `tests/test_adr{NNNN}_*.py` — ADR-specific parity tests
- `tests/test_derive_*.py` — DeriveChain logic

## Правила

### Determinism
- Fixtures з fixed seeds (не використовувати `time.time()`)
- Не спиратися на dict ordering (використовувати sorted() в assertions)

### SMC tests specific
- S2 (determinism): same bars → same zones — **критичний інваріант**
- S3 (deterministic IDs): zone ID має бути `{kind}_{symbol}_{tf_s}_{anchor_ms}`
- Тест на `bar.low` vs `bar.l` trap — не пропустити AttributeError

### Coverage target
- Нова логіка в `core/smc/` без тесту = **не merge-ready**
- ADR-driven slice = 1 новий test у `tests/test_adr{NNNN}_*.py`

### Hardcode scan
- `test_hardcode_scan.py` ловить hardcoded thresholds
- Якщо твій commit додає config value → онови SSOT_KEYS у сканері якщо треба

## Pattern reminders

- Ніяких mocks на `core/` pure logic (воно детерміноване, тестуй напряму)
- Mock тільки I/O boundary (`UDS.get_window()`, `redis.publish()`)
- Assertions on zone grade → читай з config, не хардкодь "A+" (S5)
