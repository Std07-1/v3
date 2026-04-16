---
mode: agent
description: "Інспекція Redis стану та live даних платформи (v3_local namespace)"
tools:
  - mcp_aione-trading_redis_inspect
  - mcp_aione-trading_platform_status
  - mcp_aione-trading_inspect_bars
  - mcp_aione-trading_inspect_updates
---

# Redis & Live Data інспекція

**Мова**: Українська.
**Namespace**: `v3_local` (db=1, ADR-0014).

## Протокол

### 1. Redis overview
```
redis_inspect(command="dbsize")
redis_inspect(command="info")
```

### 2. Ключові namespaces (SSOT — `runtime/store/redis_keys.py`)

#### OHLCV snapshots (I1 — UDS only writes)
```
redis_inspect(pattern="v3_local:ohlcv:snap:*")
```

#### Preview plane (tick_preview_worker, I3 NoMix)
```
redis_inspect(pattern="v3_local:preview:curr:*")
redis_inspect(pattern="v3_local:preview:tail:*")
```

#### Updates bus (I4 — UI single source)
```
redis_inspect(pattern="v3_local:updates:*")
```

#### Wake Engine (ADR-0049)
```
redis_inspect(pattern="v3_local:wake:*")
```

#### Broker IPC (broker_sidecar → m1_ingestion_worker, ADR-0016)
```
redis_inspect(pattern="v3_local:broker:*")
redis_inspect(pattern="v3_local:tick:*")
```

### 3. Порівняння з API

Для символу {{symbol:XAU/USD}}:
- `inspect_bars` для M5 → порівняй count з Redis snap
- `inspect_updates` → cursor_seq monotonicity + boot_id

### 4. TTL аналіз

Redis TTL має відповідати `config.json`:
- M1 tail: 10080 барів (~7 днів)
- Preview TTL: з SSOT config

Ключі з TTL=0 / близько до expiry → degraded indicator.

### 5. Contamination checks

- **NoMix (I3)**: чи не мішається preview і final в одному ключі?
- **Split-brain (I1, ADR-0014)**: чи Redis count = Disk count?
- **Orphaned keys**: старі ключі для символів не в allowlist?

## Що шукаємо

- **Stale keys**: TTL=0 або дуже старі
- **Missing keys**: TF в allowlist але немає snap
- **Inconsistency**: Redis count ≠ API count
- **NoMix violation**: preview і final в одному ключі
- **Wake Engine silent**: `v3_local:wake:*` порожній при активних умовах

## Формат відповіді

```
# Redis State — {YYYY-MM-DD HH:MM UTC}

## Summary: 🟢 / 🟡 / 🔴
- Total keys: X
- DB size: X MB

## Per namespace
- ohlcv:snap: X keys
- preview: X keys
- updates: X keys
- wake: X keys (ADR-0049)
- broker: X keys

## Issues
- (stale / missing / inconsistent keys)

## Recommendations
```
