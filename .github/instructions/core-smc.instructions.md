---
applyTo: "core/smc/**"
---

# core/smc/ — SMC Engine Pure Logic (ADR-0024)

**SSOT**: ADR-0024 + `.github/copilot-instructions.md` §C5/C6 (S0-S6).

## Найжорсткіші правила цього підшару

### S0 — Pure logic, NO I/O
- Жодного Redis, HTTP, subprocess, file write
- SMC deltas повертаються як об'єкти — publish НЕ тут (це робить `runtime/smc/smc_runner.py`)

### S1 — Read-only для UDS
- `core/smc/` **НЕ пише** в UDS. Ні `uds.commit()`, ні `uds.write_*()`. Ніколи.
- Zones/swings/levels — ephemeral overlay state, не SSOT OHLCV data

### S2 — Determinism (hard)
- Same bars → same zones. Тест `test_smc_determinism` має зелене світло
- Заборонено: `datetime.now()`, `random.*` без seed, set iteration order assumptions
- Якщо треба "поточний час" → передавай `now_ms: int` параметром

### S3 — Deterministic IDs
- Zone ID формат: `{kind}_{symbol}_{tf_s}_{anchor_ms}` — НЕ hash, НЕ UUID
- Приклад: `"ob_XAU_USD_900_1729875600000"` для OB на M15

### S4 — Performance budget
- `on_bar()` < `smc.max_compute_ms` (default 50ms)
- Якщо бачиш O(n²) у hot path — STOP, переосмислюй перед commit

### S5 — Config SSOT
- **Жоден** threshold не у коді. `ATR_MULT`, `SWING_PERIOD`, `FVG_MIN_HEIGHT` — з `config.json:smc`
- Хардкод числа типу `2.0 * atr` = SSOT violation

### S6 — Wire format contract
- Типи у `core/smc/types.py` = contract з `ui_v4/src/lib/types.ts`
- Зміна field name у Python → **обов'язково** оновлювати TS types (ADR Check + K4 adjacent contract)

## Типові пастки

- **X13** (bar.low): в SMC-коді ОСОБЛИВО легко написати `bar.l` у 3-й годині ночі → AttributeError → silent empty overlay → користувач не бачить зон. ЗАВЖДИ `bar.low`.
- **Swing order**: swing list має бути time-sorted. Не sort'уй по price.
- **Zone mitigation**: мітигація по **closing body**, не по тіні (ADR-0024c Z-правила).
- **FVG overlap**: перевіряй `<=` vs `<` на краях — одним пікселем відрізняється "touch" від "mitigation".
- **Impulse bars**: OB mitigation skip'ає impulse bars (ADR-0042 ob_impulse_grace).

## Checklist перед commit у `core/smc/`

1. [ ] Жодного імпорту з `runtime/` / `ui*/` / `tools/`
2. [ ] Жодного I/O call
3. [ ] Всі числові thresholds — з config, не хардкод
4. [ ] Всі поля CandleBar = `.low` (не `.l`)
5. [ ] Zone ID детермінований
6. [ ] Є тест у `tests/test_smc_*.py` який покриває зміну
7. [ ] `ui_v4/src/lib/types.ts` оновлено якщо змінювались wire types
