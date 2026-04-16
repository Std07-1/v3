---
mode: agent
description: "Діагностика: симптом → root cause (v3 platform або trader-v3)"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
  - mcp_aione-trading_health_check
  - mcp_aione-trading_platform_status
  - mcp_aione-trading_inspect_bars
  - mcp_aione-trading_inspect_updates
  - mcp_aione-trading_redis_inspect
  - mcp_aione-trading_log_tail
  - mcp_aione-trading_derive_chain_status
  - mcp_aione-trading_ws_server_check
  - mcp_aione-trading_run_exit_gates
---

# Діагностика проблеми

**Мова**: Українська.
**Роль**: SRE + R_BUG_HUNTER.
**Baseline**: ADR-0049 (sync 2026-04-16).

## Протокол діагностики

### Крок 1: Scope + збір контексту (паралельно)

1. **Scope**: v3 platform process або `trader-v3/` (Арчі bot на VPS)?
2. **Platform health**:
   - `health_check` — загальний стан
   - `platform_status` — детальний UDS status
   - `log_tail` — останні логи (шукати ERROR, WARN, DEGRADED)

### Крок 2: Звуження (залежно від симптому)

**"Немає даних / пусті бари":**
- `derive_chain_status` — чи є дані по TF chain
- `redis_inspect(pattern="v3_local:ohlcv:snap:*")` — Redis snap
- Disk: `data_v3/{symbol}/tf_{tf_s}/` — JSONL файли

**"Старі дані / не оновлюється":**
- `inspect_updates` — чи приходять events (cursor_seq, boot_id)
- `inspect_bars` — вік останнього бару
- `log_tail(process="m1_ingestion_worker")` — M1 ingestion
- `log_tail(process="broker_sidecar")` — FXCM M1 + tick relay V2

**"Неправильні ціни / gaps":**
- `inspect_bars(limit=1000)` — gap analysis
- Market calendar: чи попадає на closed interval?
- Redis vs Disk порівняння для того ж ключа

**"UI не показує / WS не працює":**
- `ws_server_check` — WS alive?
- `log_tail(process="ws_server")` — WS логи
- `inspect_bars` через HTTP — чи є дані в API
- Browser console → WS reconnect errors?

**"SMC overlay пустий":**
- **X13 trap**: чи не `bar.l` замість `bar.low`?
- `log_tail(process="ws_server")` → шукати SmcRunner errors
- Config: `config.json:smc.enabled=true`?
- Warmup bars enough?

**"Wake Engine не видає events":**
- `redis_inspect(pattern="v3_local:wake:*")` — Wake events в Redis bus
- Config: `config.json:wake_engine.enabled=true`?
- `log_tail(process="ws_server")` → WakeEngine init logs

**"Арчі (trader-v3) не реагує":** (VPS)
- `ssh aione-vps "sudo supervisorctl status smc_trader_v3"`
- `ssh aione-vps "tail -100 /opt/smc-trader-v3/logs/supervisor.log"`
- Telegram connectivity, Anthropic API rate limits
- Wake subscription: чи підписаний на v3 Wake events?

### Крок 3: Root Cause

- Один інваріант (I0–I7 / S0–S6 / G1–G6) = одна причина
- Evidence: `[VERIFIED path:line]` обов'язково
- Repro steps: ≤6 команд

### Крок 4: Severity + Рекомендація

| Sev | Критерій |
|-----|----------|
| **S0** | Data corruption / crash / split-brain write |
| **S1** | Wrong data, silent degradation, missing alerts |
| **S2** | Operational inefficiency, misleading logs |
| **S3** | Cosmetic, doc drift |

- **PATCH** → конкретний план (≤150 LOC)
- **ADR** → зміна зачіпає інваріант/контракт
- **Rollback** → якщо recent deploy

## Формат відповіді

```
# Діагностика: <симптом>

## Scope: v3-platform | trader-v3 | cross-cutting
## Severity: S0 / S1 / S2 / S3

## Root Cause
[VERIFIED path:line] — <що саме>

## Evidence
1. <факт з proof marker>
2. <факт з proof marker>

## Failure chain
<як симптом → root cause>

## Repro Steps
1. ...

## Fix
- **Type**: PATCH | ADR | Rollback
- **Plan**: <конкретні кроки>
- **Rollback if fix fails**: <команди>
```

Діагностуй: {{input}}
