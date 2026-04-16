---
mode: agent
description: "Post-deploy watch: 48h observation window після deploy Арчі або platform"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - mcp_aione-trading_health_check
  - mcp_aione-trading_platform_status
  - mcp_aione-trading_log_tail
  - mcp_aione-trading_inspect_updates
---

# MODE=POST-DEPLOY-WATCH — Observability window

**Мова**: Українська.
**Тривалість вікна**: 48h (стандарт), 24h (мінор), 72h (major release).
**Правило**: deploy без post-deploy watch = **НЕ deploy**, а **blind flight**.

## Коли активувати

- Будь-який deploy на VPS (`aione-vps`)
- Будь-який deploy trader-v3 (Арчі)
- Будь-яка зміна у platform supervisor, broker_sidecar, SMC Engine, Wake Engine
- Будь-яке міграційне ADR (зміна schema / wire format / storage)

## Протокол

### Phase 1 — T+0 до T+1h (gold hour)

**Найвища ймовірність катастрофи. Watch ACTIVELY.**

#### Для trader-v3 (Арчі):
- [ ] `ssh aione-vps "sudo supervisorctl status smc_trader_v3"` — RUNNING?
- [ ] `ssh aione-vps "tail -200 /opt/smc-trader-v3/logs/supervisor.log"` — errors?
- [ ] Telegram: 1 test message → чи відповідає?
- [ ] API cost: `grep "cost" logs` → не спалахнув?
- [ ] Anthropic rate limits не hit?
- [ ] `*_conversation.json` розмір не stale з dev сесії (memory: conversation.json incident 2026-03-30)
- [ ] Wake subscription: чи отримує `v3_local:wake:*` events?

#### Для v3 platform:
- [ ] `health_check` — зелене
- [ ] `platform_status` — `prime_ready=true`, `boot_id` змінився (перезапуск)
- [ ] `inspect_updates` — cursor_seq monotonic, events приходять
- [ ] `log_tail(process="ws_server")` — no bursts of ERROR
- [ ] UI у браузері → WS reconnect без loop
- [ ] Redis size не росте аномально

#### Blockers → immediate rollback
- RSS пам'яті росте linearly >50MB/min
- Supervisor restarts >3 за 15 хв
- API cost >$0.5/хв (Арчі) або >$1/5хв (platform)
- UDS write failures
- Market data gaps там де market відкритий

### Phase 2 — T+1h до T+6h (stabilization)

- [ ] Метрики OBS_60S (v3): spam-loop patterns?
- [ ] Error rate stable (не росте)
- [ ] Memory plateau (не leak)
- [ ] SMC Engine compute_ms < budget (ADR-0024 S4)
- [ ] Wake Engine events delivered to external consumer (ADR-0049)
- [ ] Арчі response latency < 30s p95

### Phase 3 — T+6h до T+24h (slow burn)

Рідкісні check-ins (кожні 2-3 год):
- [ ] Disk usage (`data_v3/` growth rate)
- [ ] Redis TTL expiry — старі keys прибираються?
- [ ] Log rotation працює (logs не забили диск)
- [ ] Арчі budget counter працює (daily $ cap, I7)
- [ ] Арчі thesis TTL expiry — bias оновлюється?

### Phase 4 — T+24h до T+48h (long tail)

- [ ] Жодних cumulative drift патернів
- [ ] Trader-v3 conversation.json не зростає неконтрольовано
- [ ] Platform M1 backfill стабільний
- [ ] Daily digest / post-mortem якщо incidents були

## Rollback triggers

| Сигнал | Дія |
|--------|-----|
| Supervisor restart loop (>5 у 5 хв) | **Immediate rollback** |
| Data corruption detected | **Immediate rollback** + forensic |
| API cost explosion (>$10/h Арчі) | **Kill** Арчі, investigate |
| Platform split-brain (I1) | **Immediate rollback** + manual Redis clean |
| UI mass disconnect (>50% users) | **Immediate rollback** WS server |
| Арчі sending gibberish / wrong language | **Kill** Арчі + prompt audit |

## Звіт після вікна (T+48h)

```markdown
# Post-Deploy Report — {deploy_id}

## Deploy
- Timestamp: ...
- Scope: v3-platform | trader-v3 | both
- ADR: <ref>
- Changed files: <list>

## Observations
| Phase | Status | Anomalies |
|-------|--------|-----------|
| T+0..1h | ✅ / ⚠️ / ❌ | ... |
| T+1..6h | ... | ... |
| T+6..24h | ... | ... |
| T+24..48h | ... | ... |

## Metrics
- API cost (Арчі): $X ($Y expected)
- Error rate: X/h (Y baseline)
- Platform uptime: X% (target 99.9%)
- UDS integrity: ✅ / ❌

## Issues
- <if any, з severity + fix plan>

## Verdict
- ✅ STABILIZED (можна закривати window)
- ⚠️ WATCH EXTENDED (додати +24h)
- ❌ ROLLBACK (план rollback)
```

## Пам'ятка

- Арчі — це серйозна система. Ти **кодовий хірург**. Після операції пацієнт під наглядом.
- **НЕ** залишай Арчі unattended перші 48h.
- **НЕ** деплой в п'ятницю увечері (watch window падає на weekend).
- **ЗАВЖДИ** перевіряй `*_conversation.json` розмір перед deploy — не має бути dev session residue.

Запусти post-deploy watch для deploy: {{input}}
