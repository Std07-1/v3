---
mode: agent
description: "Аудит якості барів: gaps, monotonicity, I2 geometry, derive consistency"
tools:
  - run_in_terminal
  - read_file
  - mcp_aione-trading_inspect_bars
  - mcp_aione-trading_derive_chain_status
  - mcp_aione-trading_redis_inspect
---

# Аудит якості барів

**Мова**: Українська.
**Baseline**: I2 dual-convention geometry (CandleBar end-excl, Redis end-incl).

## Протокол

Для символу {{symbol:XAU/USD}} та TF {{tf:M5}}:

### 1. API bars audit (`inspect_bars`)
Запросити limit=5000, перевірити:
- **Gaps**: пропущені бари (з урахуванням market calendar breaks; свят календар НЕ знає — див. §5)
- **Monotonicity**: `open_time_ms` строго зростає
- **Complete flag**: всі complete=true крім останнього (якщо live)
- **Source consistency**: src ∈ FINAL_SOURCES (`core/model/bars.py`)

### 2. Геометрія часу (I2)
Для кожного бару:
```
close_time_ms == open_time_ms + tf_s * 1000  # end-exclusive (CandleBar)
```
Redis check (якщо торкаємось ключів):
```
close_ms == open_ms + tf_s * 1000 - 1        # end-inclusive (Redis only)
```

### 3. Derive consistency
Якщо tf > M1:
- `derive_chain_status` → перевірити каскад M1→M3→M5→...→H4, M1→D1
- M1 coverage має покривати весь range HTF bars
- Anchors H4/D1 (ADR-0095): сітка одна, сезонна. D1 відкривається о 17:00 America/New_York
  (21:00 UTC влітку, 22:00 UTC взимку), H4 — відкриття дня + k·4 год (обрубок доби переходу
  DST на 1 або 3 год легальний). Правило — на символ: `config.json` → `htf_anchor.rule_by_calendar_group`
  (`ny_close_us_dst` для FXCM, `utc_midnight` для Binance). Другого «легального» якоря (alt) немає:
  H4 22:00 влітку — дефект. Не хардкодити — межу бакета рахує `core.session_anchor.htf_bucket_start_ms`.
  Сітку перевіряти `python -m tools.symbol_health_check --symbol <S>`: H4/D1 — `geometry.off_season_grid`
  (RED, семпли `open → expected_open`), M1..H1 — `geometry.align_bad`. Для H4/D1 `align_bad` завжди 0,
  тож «вирівняні» з нього не випливає.

### 4. Redis vs Disk
- Порівняти кількість барів Redis tail vs disk JSONL (`data_v3/{symbol}/tf_{tf_s}/`)
- Розбіжність → split-brain indicator (ADR-0014)

### 5. Market calendar
Календар (`runtime/ingest/market_calendar.py`) моделює ЛИШЕ weekend-вікно і денні
перерви. **Свят і скорочених сесій він НЕ знає**: у `MarketCalendar` немає поля для
винятків, `is_trading_minute()` не бере дату.
- Gap у weekend або в щоденну перерву = expected (календар це підтверджує).
- Gap у святковий день календар назве «торговим». **НЕ списуй його в expected** —
  звітуй як знахідку з датою і назвою свята. Саме так Memorial Day, Juneteenth,
  July 4 і Labor Day 2026 тихо коштували D1-свічки на всіх символах.
- Відсутній D1-бар за торговий день — ЗАВЖДИ знахідка (дефект D-06,
  `docs/adr/0023-d1-live-derive-from-m1.md:497-508`): свічка зникає без алерту, а SMC
  бере «попередній день» позиційно (`core/smc/key_levels.py`) — PDH/PDL стають чужими.
- Правильний предикат «бакет очікується» — у `python -m tools.symbol_health_check`
  (D1-вимір виправлено комітом `3e423ca`). Власний підрахунок «за календарем» цього
  не замінює.

### 6. Preview vs Final (I3 NoMix)
- Для того ж (symbol, tf, open_ms) не має бути двох записів з різним source
- Final завжди перемагає preview

## Формат відповіді

```
# Bar Quality Audit: {symbol} {tf}

## Summary: PASS / FAIL

## Checks
| Check | Status | Details |
|-------|--------|---------|
| Monotonicity | ✅/❌ | ... |
| Gaps (non-calendar) | ✅/❌ | X expected, Y unexpected |
| Completeness | ✅/❌ | ... |
| Time geometry (I2) | ✅/❌ | CandleBar end-excl / Redis end-incl |
| Derive coverage | ✅/❌ | ... |
| Redis/Disk consistency (I1) | ✅/❌ | Δ=X bars |
| NoMix (I3) | ✅/❌ | ... |
| Source in FINAL_SOURCES | ✅/❌ | ... |

## Issues
- (деталі кожної проблеми, з path:line якщо relevant)

## Recommendations
- PATCH | ADR | Rollback | None
```
