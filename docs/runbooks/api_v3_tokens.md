# Runbook — API v3 Token Operations

> **ADR-0058** slice 058.4. CLI tooling для життєвого циклу публічних read-only API токенів.

## TL;DR

```bash
# Видати токен (90 днів) — full token друкується в stdout, capture його
python -m tools.api_v3.issue_token --consumer old_news_bot --scope read --ttl-days 90 > /tmp/new_token.txt 2> /dev/null
NEW_TOKEN=$(cat /tmp/new_token.txt)
echo $NEW_TOKEN  # tk_<64 hex>

# Список усіх активних токенів (показує тільки prefix)
python -m tools.api_v3.list_tokens

# JSONL формат (для jq/grep)
python -m tools.api_v3.list_tokens --json | jq -r '.consumer'

# Продовжити TTL (Option B з ADR §3.4 — тільки після out-of-band verify)
python -m tools.api_v3.extend_token --token $NEW_TOKEN --days 90

# Відкликати конкретний токен (instant)
python -m tools.api_v3.revoke_token --token $NEW_TOKEN

# Відкликати ВСІ токени для consumer (rotation cleanup)
python -m tools.api_v3.revoke_token --consumer old_news_bot
```

## Передумови

- Python venv: `/opt/smc-v3/.venv` на VPS (Python 3.11) або `.venv` локально (Python 3.14)
- Redis запущений (`127.0.0.1:6379 db=1` за замовчуванням)
- `PYTHONPATH=.` встановлений (запускати з `/opt/smc-v3` або repo root)
- Env vars override (опціонально): `API_V3_REDIS_HOST`, `API_V3_REDIS_PORT`, `API_V3_REDIS_DB`, `API_V3_REDIS_NS`, `API_V3_REDIS_TIMEOUT_S`

## Сценарії

### S1 — Видати токен новому consumer

```bash
cd /opt/smc-v3
PYTHONPATH=. .venv/bin/python -m tools.api_v3.issue_token \
    --consumer external_consumer \
    --scope read \
    --ttl-days 90
# stderr: OK consumer='external_consumer' scope='read' ttl_days=90 expires=2026-08-01T...Z
# stdout: tk_a3f4...
```

**Доставити токен** consumer'у out-of-band (Telegram message, encrypted email).
Токен **НЕ** відображається повторно — `list_tokens` показує тільки 11-символьний prefix.

### S2 — Rotation з grace period (recommended, F-S1-001)

ADR §3.4 — **7 днів grace** для покриття weekend deployments + reaction window.

1. **День 0**: видати новий токен
   ```bash
   .venv/bin/python -m tools.api_v3.issue_token --consumer external_consumer --ttl-days 90
   ```
2. **День 0**: повідомити consumer (вибраний канал): «Новий токен: `tk_NEW`. Старий `tk_OLD` працюватиме до `<old_expires + 7d>`.»
3. **День 0–1**: consumer оновлює свою конфігурацію
4. **День 0–7**: обидва токени активні. Verify через `list_tokens`:
   ```bash
   .venv/bin/python -m tools.api_v3.list_tokens
   # бачимо обидва запити для external_consumer
   ```
5. **День 7**: monitor `data_v3/_audit/api_v3_access.jsonl` — старий токен має бути silent (zero hits протягом 24h)
6. **День 7**: revoke старий токен:
   ```bash
   .venv/bin/python -m tools.api_v3.revoke_token --token tk_OLD
   ```
7. **День 8**: verify відсутність 401 у audit log за consumer

### S3 — Renewal (Option B, operational shortcut, F-S2-003)

**Попередження**: тільки якщо consumer identity verified out-of-band (Telegram message від owner).
Recommended path = S2 (rotation).

```bash
.venv/bin/python -m tools.api_v3.extend_token --token tk_X --days 90
# stdout: OK extended token=tk_X... consumer='external_consumer' new_expires=2026-08-01...Z
```

`extend_token` атомарно перезаписує і Redis TTL, і `expires` поле в JSON value (audit accuracy).

### S4 — Revoke стара compromised token

**Instant** (не кешується — наступний запит дає 401):

```bash
# Конкретний токен (із Telegram alert або security incident)
.venv/bin/python -m tools.api_v3.revoke_token --token tk_LEAKED

# Всі токени для compromised consumer
.venv/bin/python -m tools.api_v3.revoke_token --consumer compromised_bot
# stdout: OK revoked count=N prefixes=['tk_aaaa...', ...]
```

### S5 — Audit без рестарту сервісу

```bash
# Hot reload — list_tokens завжди читає Redis live
.venv/bin/python -m tools.api_v3.list_tokens

# JSONL для grep/jq
.venv/bin/python -m tools.api_v3.list_tokens --json | jq 'select(.ttl_s < 86400 * 7)'  # expires within 7d
```

## Exit codes

| Команда | 0 | 1 | 2 |
|---|---|---|---|
| `issue_token` | OK, токен видано | — | bad args (`--ttl-days` out of 1..365) |
| `list_tokens` | OK (навіть якщо порожньо) | — | — |
| `revoke_token --token` | OK, deleted | NOT_FOUND | malformed shape |
| `revoke_token --consumer` | OK, ≥1 deleted | NOT_FOUND (zero matches) | — |
| `extend_token` | OK, extended | NOT_FOUND or malformed-existing | bad shape / bad days |

## Безпекові інваріанти (X1, ADR §3.4, §4)

- **Full token у логах = заборонено**. Скрипти показують максимум 11 chars (`tk_` + 8 hex).
- **issue_token** друкує full token тільки в stdout (для capture). stderr = OK confirmation з consumer/scope/expires.
- **revoke_token --token** не друкує full token у success/error messages.
- **extend_token** перезаписує `expires` field у JSON value, щоб audit (`list_tokens`) показував поточний справжній expiry, не stale issued-at.

## Troubleshooting

### `redis.exceptions.ConnectionError`

- Перевірити `redis-cli -h $API_V3_REDIS_HOST -p $API_V3_REDIS_PORT -n $API_V3_REDIS_DB ping`
- На VPS: `systemctl status redis-server`

### `MaximumRetriesError` під час `revoke_token --consumer`

Якщо база має 100k+ ключів, `SCAN` ітерація може не поміститися в default timeout (1.5s). Збільшити:

```bash
API_V3_REDIS_TIMEOUT_S=10 .venv/bin/python -m tools.api_v3.revoke_token --consumer X
```

### Token виданий але `/api/v3/*` повертає 401

1. `list_tokens --json | grep <prefix>` — токен в Redis?
2. Перевірити namespace: env `API_V3_REDIS_NS` (CLI) має співпадати з env auth_validator (`/opt/smc-v3/runtime/api_v3/auth_validator.py` config)
3. Перевірити db number: `API_V3_REDIS_DB`
4. `redis-cli -n 1 ttl v3_local:tokens:tk_<full>` — чи не expired?

## Cross-references

- ADR-0058 §3.4 — Token lifecycle (rotation runbook)
- ADR-0058 §3.5 — Audit JSONL (`data_v3/_audit/api_v3_access.jsonl`)
- ADR-0058 §4 — Security model (X1 secrets handling)
- `runtime/api_v3/auth_validator.py` — token consumer (slice 058.1)
- `runtime/api_v3/token_store.py` — SSOT для token format + Redis key shape
