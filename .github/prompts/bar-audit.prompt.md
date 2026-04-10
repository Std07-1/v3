---
mode: agent
description: "Аудит якості барів: gaps, monotonicity, completeness, геометрія часу"
tools:
  - run_in_terminal
  - read_file
  - mcp_aione-trading-platform_inspect_bars
  - mcp_aione-trading-platform_data_files_audit
  - mcp_aione-trading-platform_derive_chain_status
  - mcp_aione-trading-platform_redis_inspect
---

# Аудит якості барів

**Мова**: Українська.

## Протокол

Для символу {{symbol:XAU/USD}} та TF {{tf:M5}}:

### 1. Disk JSONL audit
Викликати `data_files_audit` — перевірити файли, bar counts, monotonicity.

### 2. API bars audit
Викликати `inspect_bars` з limit=5000 — перевірити:
- **Gaps**: пропущені бари (з врахуванням market calendar)
- **Monotonicity**: open_time_ms строго зростає
- **Complete flag**: всі бари complete=true (крім останнього)
- **Source consistency**: src="history" або "derived"

### 3. Геометрія часу (I2)
Для кожного бару перевірити інваріант:
```
close_time_ms == open_time_ms + tf_s * 1000
```

### 4. Derive consistency
Якщо tf > M1: перевірити що derive chain заповнений
(M1 bars покривають весь range HTF bars).

### 5. Redis vs Disk
Порівняти кількість барів у Redis tail vs disk tail.

## Формат відповіді

```
# Bar Quality Audit: {symbol} {tf}

## Summary: PASS / FAIL

## Checks
| Check | Status | Details |
|-------|--------|---------|
| Monotonicity | ✅/❌ | ... |
| No gaps | ✅/❌ | X gaps found |
| Completeness | ✅/❌ | ... |
| Time geometry (I2) | ✅/❌ | ... |
| Derive coverage | ✅/❌ | ... |
| Redis/Disk consistency | ✅/❌ | ... |

## Issues
- (деталі кожної проблеми)
```
