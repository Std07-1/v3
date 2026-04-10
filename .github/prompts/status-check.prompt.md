---
mode: agent
description: "Перевірка стану платформи, runtime health, bars, gaps"
tools:
  - run_in_terminal
  - mcp_aione-trading-platform_health_check
  - mcp_aione-trading-platform_platform_status
  - mcp_aione-trading-platform_inspect_bars
  - mcp_aione-trading-platform_inspect_updates
  - mcp_aione-trading-platform_derive_chain_status
  - mcp_aione-trading-platform_redis_inspect
  - mcp_aione-trading-platform_ws_server_check
  - mcp_aione-trading-platform_log_tail
---

# Перевірка стану платформи

**Мова**: Українська.

## Що перевірити

1. **Health check** — викликати `health_check` для загального огляду
2. **Platform status** — детальний статус через `platform_status`
3. **Derive chain** — стан каскаду для {{symbol:XAU/USD}} через `derive_chain_status`
4. **Bars quality** — останні бари по ключових TF через `inspect_bars`:
   - M1 (60) — base TF, latest bars
   - M5 (300) — main trading TF
   - H4 (14400) — HTF structure
   - D1 (86400) — daily candles
5. **Live updates** — перевірити `inspect_updates` для aktyvних TF
6. **Redis** — стан Redis ключів через `redis_inspect`
7. **WS server** — перевірити `ws_server_check`

## Формат відповіді

```
# Platform Health Report

## Summary: 🟢/🟡/🔴

## Components
- HTTP API: OK/FAIL
- WS Server: OK/FAIL
- Redis: OK/FAIL (X keys)
- Data: OK/FAIL

## Bar Quality
- M1: X bars, age Xm, gaps: X
- M5: ...
- H4: ...
- D1: ...

## Issues Found
- (список проблем або "немає")

## Recommendations
- (якщо є)
```
