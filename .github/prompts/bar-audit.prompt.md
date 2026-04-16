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
- **Gaps**: пропущені бари (з урахуванням market calendar breaks)
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
- Anchors: H4 = 82800s (23:00 UTC), D1 = 79200s (22:00 UTC) — ADR-0023

### 4. Redis vs Disk
- Порівняти кількість барів Redis tail vs disk JSONL (`data_v3/{symbol}/tf_{tf_s}/`)
- Розбіжність → split-brain indicator (ADR-0014)

### 5. Market calendar
Gaps на weekend / holiday / session break = **expected**. Перевірити через market calendar замість "all gaps = bad".

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
