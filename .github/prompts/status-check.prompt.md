---
mode: agent
description: "Перевірка стану платформи — health, UDS, SMC, Wake Engine, bars, updates"
tools:
  - run_in_terminal
  - mcp_aione-trading_health_check
  - mcp_aione-trading_platform_status
  - mcp_aione-trading_inspect_bars
  - mcp_aione-trading_inspect_updates
  - mcp_aione-trading_derive_chain_status
  - mcp_aione-trading_redis_inspect
  - mcp_aione-trading_ws_server_check
  - mcp_aione-trading_log_tail
---

# Перевірка стану платформи

**Мова**: Українська.
**Baseline**: ADR-0049 (Wake Engine External Consumer IPC, 2026-04-16).

## Що перевірити

### Core health
1. **Health check** — `health_check` загальний огляд
2. **Platform status** — `platform_status`: boot_id, prime_ready, Redis state, uptime, disk policy, bar counts per TF
3. **WS server** — `ws_server_check`: alive, connected clients, конфіг

### Data pipeline
4. **Derive chain** — `derive_chain_status` для {{symbol:XAU/USD}} (M1→M3→M5→M15→M30→H1→H4, M1→D1)
5. **Bars quality** — `inspect_bars` для ключових TF:
   - M1 (60) — base TF
   - M5 (300) — main trading TF
   - M15 (900) — entry TF
   - H4 (14400) — HTF structure
   - D1 (86400) — daily context
6. **Live updates** — `inspect_updates` — cursor_seq, boot_id, event delays

### Infrastructure
7. **Redis** — `redis_inspect(command="dbsize")`, перевірити namespaces:
   - `v3_local:ohlcv:snap:*` — OHLCV snapshots
   - `v3_local:preview:*` — preview plane
   - `v3_local:updates:*` — updates bus (I4)
   - `v3_local:wake:*` — Wake Engine bus (ADR-0049)
   - `v3_local:smc:*` — SMC state (if any)

### Subsystems (per config.json)
8. **SMC Engine** (ADR-0024): `log_tail(process="ws_server", grep="SMC")` — warmup/delta/compute_ms
9. **Wake Engine** (ADR-0049): `redis_inspect(pattern="v3_local:wake:*")` — events, external consumer health
10. **Signal Engine** (ADR-0039): `log_tail(process="ws_server", grep="signal")`

### Broker health
11. **FXCM**: `log_tail(process="broker_sidecar")` — M1 fetch + tick relay V2
12. **Binance** (ADR-0037): `log_tail(process="binance_ingest_worker")` + `log_tail(process="binance_tick_publisher")`

## Формат відповіді

```
# Platform Health Report — {YYYY-MM-DD HH:MM UTC}

## Overall: 🟢 / 🟡 / 🔴

## Components
- HTTP API: OK / FAIL (<error>)
- WS Server: OK / FAIL (X clients connected)
- Redis: OK / FAIL (X keys, Y MB)
- UDS: OK / FAIL (boot_id=..., prime_ready=true)
- SMC Engine: OK / FAIL / DISABLED
- Wake Engine: OK / FAIL / DISABLED (X events in last 5m)
- FXCM Ingest: OK / FAIL
- Binance Ingest: OK / FAIL

## Bar Quality (per TF)
| TF | Bars | Age | Gaps (non-cal) | Source |
|----|------|-----|----------------|--------|
| M1 | X | Xm | 0 | history |
| M5 | X | Xm | 0 | derived |
| H4 | X | Xh | 0 | derived |
| D1 | X | Xh | 0 | derived |

## Live Updates
- cursor_seq: X (monotonic ✓)
- boot_id: <uuid>
- Last event age: Xs

## Issues Found
- (список з severity або "немає")

## Recommendations
- (якщо є, з action: PATCH | ADR | Rollback)
```
