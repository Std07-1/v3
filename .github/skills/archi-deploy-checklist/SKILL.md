# Архі Deploy Checklist Skill

**Призначення**: безпечний deploy Арчі (trader-v3) на VPS з 48h post-deploy watch.
**Коли викликати**: будь-який trader-v3 deploy; mentor для post-deploy моніторингу.

## Pre-deploy gate (обов'язково)

### Code quality
- [ ] `cd trader-v3&& pytest` — всі тести зелені
- [ ] `git status` clean у `trader-v3/`
- [ ] Немає TODO у hot path без issue ref
- [ ] CHANGELOG / ADR оновлено для S0/S1 змін

### I7 Autonomy check (CRITICAL)
- [ ] Жоден `if blocked: return` без safety justification
- [ ] Жоден hard cooldown / force-downgrade / suppress-by-default
- [ ] Прийняті 4 hard blocks: kill switch, daily $ cap, owner-only, anti-hallucination
- [ ] Мова промптів = recommendations ("З досвіду..."), не commands ("НІКОЛИ...")
- [ ] Кожне нове обмеження — visible to Арчі через логи / промпт / контекст

### Data hygiene (X32)
- [ ] `data/*.json` НЕ у git status (deploy не повинен їх перезаписати)
- [ ] `*_conversation.json` локально НЕ містить dev session residue (memory: incident 2026-03-30)
  - Якщо містить → reset до `{"messages":[]}` ПЕРЕД deploy
- [ ] `setup_v3.sh` skip'ає existing data files

### Cross-repo (X31)
- [ ] Жодних змін у v3 platform файлах у цьому commit/branch
- [ ] Якщо потрібен v3 platform feature — окремий v3 ADR створено перед

### Prompt sanity
- [ ] `smc_trader_prompt_v3.md` — поточна версія
- [ ] PLATFORM DATA = АБСОЛЮТНА ІСТИНА rule присутній (anti-hallucination)
- [ ] Owner-only Telegram user_id check присутній
- [ ] Daily $ cap configured

## Deploy execution

```bash
# 1. Verify VPS state
ssh aione-vps "sudo supervisorctl status smc_trader_v3"
# Expected: RUNNING (uptime > 0)

# 2. Pre-deploy snapshot
ssh aione-vps "cp /opt/smc-trader-v3/data/*.json /tmp/archi-backup-$(date +%Y%m%d)/"

# 3. Copy code (NOT data/)
cp trader-v3/smc_trader_v3.py trader-v3/smc_trader_prompt_v3.md  /opt/smc-trader-v3/
# Or use deploy.sh which respects .gitignore

# 4. Restart
ssh aione-vps "sudo supervisorctl restart smc_trader_v3"

# 5. Immediate sanity
ssh aione-vps "tail -50 /opt/smc-trader-v3/logs/supervisor.log"
# Expect: clean startup, no Python tracebacks
```

## Post-deploy watch (T+0 → T+48h)

### Phase 1 — Gold hour (T+0 → T+1h)

**Active monitoring required.**

| Check | Method | Pass |
|-------|--------|------|
| Supervisor RUNNING | `supervisorctl status smc_trader_v3` | uptime > 5min |
| No tracebacks | `tail -200 logs/supervisor.log \| grep -i "error\|trace"` | empty |
| Telegram alive | Send 1 test message | response within 30s |
| API cost reasonable | `grep "cost" logs \| tail -20` | <$0.10/min |
| Wake subscription | `redis-cli subscribe v3_local:wake:*` | events приходять (if active wake conditions) |
| `*_conversation.json` size | `ls -la /opt/smc-trader-v3/data/` | NOT growing exponentially |

**Rollback triggers** (immediate revert):
- Supervisor restart loop (>3 у 5 хв)
- API cost >$1/хв
- Telegram silent >5 min after test
- Gibberish output / wrong language

### Phase 2 — Stabilization (T+1h → T+6h)

Periodic check кожні 30 хв:
- Memory stable (`ps -o rss -p $(pgrep -f smc_trader_v3)`)
- Error rate stable (not growing)
- Thesis updates working (Арчі прокидається на wake events)
- Daily budget counter accurate

### Phase 3 — Slow burn (T+6h → T+24h)

Check every 2-3 hours:
- Disk usage (`du -sh /opt/smc-trader-v3/data/`)
- Conversation memory не explode'нула
- Daily digest формується (якщо запланований)
- Supervisor uptime continuous (no restarts)

### Phase 4 — Long tail (T+24h → T+48h)

Check кожні 6h:
- Cumulative drift patterns
- Owner satisfaction (subjective — чи Арчі поводиться як очікувано?)
- Cost projection: чи будем у місячному бюджеті?

## Post-deploy report (T+48h)

```markdown
# Архі Deploy Report — <date>

## Deployed
- Files: <list>
- Commit: <SHA>
- ADR: trader-v3/docs/adr/ADR-NNN

## Phase results
| Phase | Status | Notes |
|-------|--------|-------|
| T+0..1h | ✅ / ⚠️ / ❌ | ... |
| T+1..6h | ... | ... |
| T+6..24h | ... | ... |
| T+24..48h | ... | ... |

## Metrics
- API cost (48h): $X (target <$Y)
- Telegram messages: X (Y from owner, Z proactive)
- Wake events processed: X
- Restarts: 0 / N
- Memory peak: X MB
- Disk growth: X MB

## Anomalies & resolutions
- <list, if any>

## Verdict
✅ STABILIZED / ⚠️ WATCH +24h / ❌ ROLLBACK
```

## Заборони specific to Арчі deploy

- ❌ Deploy в п'ятницю після 18:00 (watch падає на weekend)
- ❌ Deploy без post-deploy watch плану
- ❌ Copy `data/*.json` під час deploy
- ❌ Ігнорувати "minor" anomalies в gold hour
- ❌ Закривати watch window раніше T+48h без явного OK

## Правило використання

- **R_PATCH_MASTER**: при кожному trader-v3 deploy
- **Owner directly**: для manual deployments
- **R_REJECTOR**: gates Арчі deploys — без post-deploy watch плану NO-GO
