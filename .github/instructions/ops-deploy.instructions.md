---
applyTo: "app/**,tools/**,config.json"
---

# ops/deploy — Supervisor, Tools, Config

**SSOT**: `.github/copilot-instructions.md` §D1 (config), §K (exit gates).

## app/main.py (Supervisor)

- Windows trampoline: Python 3.14 venv launcher creates trampoline processes → `_kill_tree()` (ADR-0016 App. C)
- `logs/supervisor_{mode}.pid` blocks duplicate instances
- S2 restart backoff: exponential with max cap
- `--stdio pipe` для interactive, `--stdio files` для prod

## config.json — SSOT policy

- Секрети НЕ тут (вони у `.env`)
- TF allowlist, Redis config, broker_python path, SMC params — тут
- K5 gate: `enabled: true` для feature flag **тільки** якщо ADR статус = Accepted/Implemented/Active
- Нова секція → додай приклад у `docs/config_reference.md`

## tools/ — isolated utilities

- **Ніколи** не використовуються у prod hot-path
- Можуть імпортувати `core/` і `runtime/`
- `runtime/` не імпортує `tools/` (I0)
- Нова tool → додай у `tools/__init__.py` description + README

## Exit Gates (K1)

- Всі gates живуть у `tools/exit_gates/gates/`
- Manifest: `tools/exit_gates/manifest.json`
- Запуск: `python -m tools.run_exit_gates --manifest ...`
- Нова architectural rule → новий AST gate

## Deploy patterns

### v3 platform (VPS)
- `ssh aione-vps` → `/opt/smc-v3/`
- Supervisor processes — через systemd або `supervisorctl`
- Nginx reverse proxy → port 8000 ws_server
- **CRITICAL**: після `scp dist/*` → `sudo chmod 755` (Windows perms trap)

### Logs
- `logs/` локально, `/var/log/smc-v3/` на VPS
- Rotation через logrotate (VPS) або manual (local)

## Pattern reminders

- `--mode all` = full supervisor
- Окремі режими: `m1_poller`, `broker_sidecar`, `m1_ingestion_worker`, `tick_preview`, `binance_ingest_worker`, `ws_server`
- TUI монітор: `python -m aione_top` (окремо)
- Health check: `curl http://127.0.0.1:8000/api/status`
