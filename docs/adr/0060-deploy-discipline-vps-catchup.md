# ADR-0060: Deploy Discipline + VPS Catch-Up Plan

- **Status**: Proposed (2026-05-04)
- **Date**: 2026-05-04
- **Author**: Стас + Copilot (R_ARCHITECT)
- **Initiative**: `deploy_discipline_v1`
- **Related ADRs**: ADR-0058 (Public Read-Only API), ADR-0059 (Analysis API — Deferred), ADR-0052 (Chat Modularization), trader-v3 ADR-052 (Архі startup chain refactor), trader-v3 ADR-056 (snapshot evidence)
- **Supersedes**: nothing

---

## Quality Axes

- **Ambition target**: R3 — встановлює відсутню governance ось (deploy ritual + monitoring), яка стримуватиме всі майбутні drift'и; не одноразовий патч, а systemic capability
- **Maturity impact**: M3 → M4 — додає observability rail (deploy gap detector + worker liveness check) + automated backup gate; усуває "tribal knowledge deploys"

---

## 1. Context

### 1.1 Тригер

4 травня 2026 під час валідації cowork experiment виявлено **systemic deploy collapse у двох системах одночасно**:

| Система | VPS HEAD | Local HEAD | Реальний gap | Worker state |
|---|---|---|---|---|
| Platform `/opt/smc-v3/` | `a522f51` (≈Apr) | `c52924c` | 30+ commits + **uncommitted local edits** на `config.json` і `core/smc/engine.py` | All RUNNING (21d uptime) |
| Архі `/opt/smc-trader-v3/` | Apr 13 file mtimes (NOT a git repo) | post-`cc31931` + ADR-052 (14 commits) | ADR-052/053/054/055/056 + ~6 missing modules (`agent_call.py`, `checks/` subpackage, 4 нових state файлів) | **STOPPED Apr 29 19:11** після Anthropic 403 |

### 1.2 Чому це сталося — root causes (не симптоми)

| # | Root cause | Evidence |
|---|---|---|
| RC1 | **Немає deploy ритуалу як exit-criterion** | `D9` checklist існує в `CLAUDE.md`, але не enforced в patch cycle. Кожен PATCH `verify` — локально, deploy — manual ad-hoc |
| RC2 | **Немає worker liveness alerting** | Архі STOPPED 5 днів — ніхто не помітив. `aione_top` TUI працює, але не push-alert |
| RC3 | **Live edits на проді** | `M config.json M core/smc/engine.py` на VPS working tree — robust → conflict готує `git pull` failure |
| RC4 | **Нема staging environment** | Кожен deploy = surgery без репетиції |
| RC5 | **Cowork experiment з'їв focus** | 2-тижневий sprint на нову поверхню замість підтримки існуючої |
| RC6 | **Архі deploy через SCP без git** | `/opt/smc-trader-v3/` not git repo. Стан непровіряємий через `git status`. Виявляється тільки через `find -newer` + LOC diff |
| RC7 | **Backup asymmetry** | Архі має `/opt/backups/trader-v3-*.tar.gz` 9-day retention. Платформа — **0 automated backups** |

### 1.3 Архі Apr 29 stop — root cause

З логів `/opt/smc-trader-v3/logs/supervisor.log` за 19:04:02 UTC:

```
httpx INFO HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 403 Forbidden"
v3bot.agent ERROR Agent proactive call failed: <Cloudflare challenge HTML>
anthropic.PermissionDeniedError: <Cloudflare 'Just a moment...' challenge>
v3bot.monitor INFO COST_TRACK: +$0.020 = $0.020 today (budget $6.00)
[19:11:29] aiogram.dispatcher WARNING Received SIGTERM signal
v3bot.main INFO Bot stopped
```

**Висновок**: 403 від Cloudflare WAF на Anthropic API → owner clean SIGTERM через 7 хв. **Не bug в коді Архі**. Можливі причини: VPS IP rate-limited/flagged, API key issue, anti-bot challenge. Owner-decision: довгострокова hibernation (немає інвесторського інтересу) — підтверджено вербально.

**Implication для catch-up**: перед будь-яким Архі рестартом — `curl` test на Anthropic API з VPS, перевірити чи 403 ще actual.

### 1.4 Чому НЕ робимо mass deploy зараз

1. **K6 violation**: full bot tarball deploy = >>3 файлів без verify gate per slice
2. **Uncommitted платформи** — `git pull` втратить production-tested edits на `config.json`+`engine.py`
3. **Архі 5 днів простою** — runtime state migration risk: `v3_agent_directives.json` schema може не пасувати ADR-056 (preadr056 backup існує — це хвіст підготовки до deploy який обірвався)
4. **Anthropic 403 unverified** — рестарт без перевірки = повторне 403 → новий cycle stop
5. **F9 craftsmanship**: hack/workaround "просто розгорнемо все одразу і подивимось" = регрес M3→нижче

---

## 2. Alternatives розглянуті

### A. Path A — Mass tarball deploy (відкинуто)

`tar`-im все, replace, restart, спостерігати 60-120s.

- **Плюс**: швидко (~30 хв)
- **Мінус**: irreversible якщо runtime data зламається; не вирішує RC1-RC7; повторюватиметься через 2-3 місяці

### B. Path B — Incremental staged deploy (частково ОК)

ADR-by-ADR deploy з verify per stage.

- **Плюс**: low risk per slice
- **Мінус**: повільно; не вирішує systemic RC1/RC2 (governance). Якщо тільки виконати — через 3 місяці новий gap

### C. Path C — Stop / design / staged execute (ОБРАНО)

Спочатку governance hardening (this ADR), потім staged execute з gates, потім operational rails (monitoring + backups + ritual).

- **Плюс**: Адресує root causes, не симптоми. Ladder M3→M4
- **Мінус**: ще ~1 день перед першим deploy

### D. Path D — Deprecate Архі повністю

Приняти що Архі не буде resumed, видалити dir, lock VPS.

- Відкинуто owner'ом (Архі = primary AI investment, hibernation ≠ death)

---

## 3. Decision

**Implement Path C** через 4 фази, кожна з R_REJECTOR gate, з паузою для owner review між фазами.

### 3.1 Фаза 1 — Foundation (governance + safety nets) — **до будь-якого deploy**

P-slice 1.1: **Backup rail для платформи** (P0)

- Створити `/opt/backups/platform/` на VPS, cron daily 03:30 UTC
- Backup target: `/opt/smc-v3/` (без `node_modules`, `dist/assets`, `__pycache__`, `data_v3/`)
- Retention: 9 днів (паритет з Архі)
- Verify: один restore test з `--dry-run` через `tar -tzf`

P-slice 1.2: **Worker liveness alerting** (P0)

- Cron-based heartbeat: every 15 min, перевіряє `supervisorctl status` для кожного критичного сервісу
- Якщо STOPPED або FATAL >30 хв → Telegram message owner'у через `tg_guard` (вже RUNNING)
- Файл: `tools/ops/heartbeat.sh` + crontab entry
- **Implication**: Архі stop ніколи більше не залишиться непоміченим 5 днів

P-slice 1.3: **VPS local-edit rescue** (P0)

- `ssh aione-vps 'cd /opt/smc-v3 && git diff config.json core/smc/engine.py'` → save до `reports/vps_local_edits_2026_05_04.diff`
- Аналіз diff: чи це bug-fixes що треба cherry-pick в local, чи stale debug
- Decision: cherry-pick → commit на local → pull working tree clean
- Тільки після цього working tree готовий до `git pull`

P-slice 1.4: **Deploy ritual SOP** (P1)

- Створити `docs/runbooks/deploy_platform.md` + `docs/runbooks/deploy_archi.md`
- Кожен SOP: pre-flight checklist (backup, working tree clean, tests pass), step-by-step, post-deploy observation D9.1, rollback steps
- Додати поле `deploy_status: "deployed_<date>" | "pending_deploy" | "deferred"` у YAML фронтматер кожного нового ADR з кодовими змінами
- Update `K6` правило: коли ADR має код — **deploy = exit-criterion**, не "патч завершено"

P-slice 1.5: **Drift detector exit-gate** (P2)

- `tools/exit_gates/gates/gate_deploy_drift.py` — щотижня (cron Sun 04:00 UTC) рапортує:
  - Local commits not on VPS (count + list)
  - VPS uncommitted edits (file list)
  - Архі file mtime vs latest local commit touching that file
  - Workers DOWN >24h
- Result → `reports/deploy_drift_weekly.md` + Telegram summary

**Phase 1 exit gate**: 5 P-slices applied, backups verified, heartbeat alerting actually fires test-message, VPS working tree clean. Owner approves → Phase 2.

### 3.2 Фаза 2 — Platform catch-up (ADR-058 + 059 + interim drift)

P-slice 2.1: Inventory всіх commits `a522f51..c52924c` per ADR. Класифікувати: must-deploy / safe-to-skip / needs-rework.

P-slice 2.2: ADR-058 (auth + token store + audit JSONL + nginx) deploy. Окремий verify: token issuance flow + nginx reverse proxy + smoke test з валідним і invalid token.

P-slice 2.3: ADR-059 endpoints — **тільки якщо** owner вирішив re-open cowork. Інакше — endpoints залишаються в коді але kill switch ON.

P-slice 2.4: Решта commits (chat modularization, F9 docs, X33 stop-rule) — staged.

**Phase 2 exit gate**: VPS HEAD = local HEAD, all workers RUNNING, 24h observation window, smoke matrix pass.

### 3.3 Фаза 3 — Архі catch-up (ADR-052 + 053 + 054 + 055 + 056)

**Передумова**: owner GO на resume Архі (зараз hibernation).

P-slice 3.1: **Anthropic API health check** з VPS — `curl` test, перевірити що 403 не повторюється. Якщо повторюється — окремий incident slice.

P-slice 3.2: **Pre-deploy snapshot** — створити snapshot `/opt/smc-trader-v3/` ПЕРЕД deploy (поза daily backup): `tar czf /opt/backups/trader-v3-prerestart-$(date +%s).tar.gz /opt/smc-trader-v3/`.

P-slice 3.3: **Schema migration audit** — порівняти `v3_agent_directives.json` schema з очікуваним після ADR-056. Якщо break → migration script.

P-slice 3.4: Deploy bot/ через rsync (НЕ tar replace — щоб preserve runtime data в `data/`):
```bash
rsync -av --delete --exclude='data/' --exclude='logs/' --exclude='*.bak*' \
  trader-v3/bot/ aione-vps:/opt/smc-trader-v3/bot/
```

P-slice 3.5: AST validate всіх .py на VPS після rsync, перш ніж start.

P-slice 3.6: Start + 120s observation window per D9.1.

P-slice 3.7: Smoke test через Telegram (`/state`, `/status`).

**Phase 3 exit gate**: Архі RUNNING 24h без crash, owner reactions через TG OK.

### 3.4 Фаза 4 — Operational hardening (закріпити gains)

P-slice 4.1: **Архі → git repo** — ініціалізувати git у `/opt/smc-trader-v3/` (separate repo, в .gitignore parent), щоб майбутній drift був visible.

P-slice 4.2: **Staging environment design** (окремий ADR? або section тут) — docker-compose локально що відтворює supervisor + redis + nginx + ws_server + Архі.

P-slice 4.3: Update `AGENTS.md` §1.1 з actual deploy state. Update `CLAUDE.md` D9 checklist if drift.

P-slice 4.4: Post-mortem doc `docs/audit/deploy_collapse_2026_05.md` — публічна версія для майбутніх агентів.

---

## 4. Consequences

### 4.1 Позитивні

- Deploy gap >7 днів стає detectable за <1 тиждень (drift gate)
- Worker stop стає detectable за <30 хв (heartbeat)
- Платформа отримує backup parity з Архі
- Archi рестарт перестає бути ad-hoc — є SOP
- F9 invariant маніфестується в operational layer (не тільки в коді)
- Cowork re-open (якщо колись) має чисту surface для testing

### 4.2 Негативні / risks

- **Час**: Phase 1 ~1-2 days, Phase 2-3 ~2-4 hours кожен, Phase 4 — 1 day
- **Складність cron entries** — операційний overhead на VPS
- **Telegram alert fatigue ризик** — heartbeat треба calibrate (не "every check", а "first DOWN >30min")
- Можливо знадобиться окремий ADR-0061 для staging environment design

### 4.3 Що НЕ робимо в цьому ADR

- Cowork v3 redesign — Deferred per ADR-0059
- Multi-symbol re-activation — окремий ADR-0054 plan
- Анти-bot mitigation для Anthropic 403 — окремий incident slice якщо повториться

---

## 5. Rollback

- Phase 1 P-slices independent → rollback per slice (видалити cron, restore checklist)
- Phase 2/3 rollback via backups (Phase 1.1 + Архі daily backup)
- Якщо drift detector spamить → disable cron, recalibrate

---

## 6. Verify

- Phase 1: `tools/ops/heartbeat.sh` test message в Telegram; backup `tar -tzf` listing OK; rescued diff committed
- Phase 2: VPS `git status` clean + HEAD == local HEAD; smoke matrix pass
- Phase 3: `supervisorctl status smc_trader_v3` = RUNNING 24h; TG smoke OK
- Phase 4: git log в `/opt/smc-trader-v3/` показує ініціальний commit; staging spinup test pass

---

## 7. Open questions (для owner)

1. **Cowork re-open** — чи деплоїти ADR-059 endpoints у Phase 2 (kill switch ON), чи тримати в коді dormant?
2. **Архі resume timeline** — Phase 3 виконуємо коли? "Через тиждень", "коли інвестори", "ніколи"? Це впливає на priority Phase 1 P-slices
3. **Staging budget** — окремий VPS ($5-10/міс) vs docker-compose локально (free, але Windows + Linux nginx differences)?
4. **Anthropic 403 mitigation** — змінювати IP (новий VPS) чи residential proxy чи приймати ризик?

---

## Quality Axes (recap)

- Ambition: R3 (systemic governance, not patch)
- Maturity: M3 → M4 (observability rail + backup parity + deploy SOP)
