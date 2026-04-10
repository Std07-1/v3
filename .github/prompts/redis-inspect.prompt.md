---
mode: agent
description: "Інспекція Redis стану та live даних платформи"
tools:
  - mcp_aione-trading-platform_redis_inspect
  - mcp_aione-trading-platform_platform_status
  - mcp_aione-trading-platform_inspect_bars
  - mcp_aione-trading-platform_inspect_updates
---

# Redis & Live Data інспекція

**Мова**: Українська.

## Протокол

### 1. Redis overview
```
redis_inspect(command="dbsize")
redis_inspect(command="info")
```

### 2. OHLCV snapshots
```
redis_inspect(pattern="v3_local:ohlcv:snap:*")
```

### 3. Preview state
```
redis_inspect(pattern="v3_local:preview:*")
```

### 4. Updates bus
```
redis_inspect(pattern="v3_local:updates:*")
```

### 5. Порівняння з API
Для символу {{symbol:XAU/USD}}:
- `inspect_bars` з M5 → порівняй кількість з Redis snap
- `inspect_updates` → перевір cursor_seq та boot_id

### 6. Аналіз TTL
Перевірити TTL ключових Redis ключів — чи відповідають config.json.

## Що шукаємо

- **Stale keys**: ключі з TTL=0 або дуже старі
- **Missing keys**: TF є в allowlist але немає snap
- **Inconsistency**: Redis count ≠ API count
- **NoMix violation**: preview і final в одному ключі
