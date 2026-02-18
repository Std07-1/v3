# Trading Platform v3 (FXCM Connector + UDS + UI)

Торгова платформа "дані → аналітика/SMC → UI → торгова взаємодія" з жорсткими інваріантами та **UnifiedDataStore (UDS)** як єдиним write-center.

## Канон A → C → B

| Шар | Що | Де |
|---|---|---|
| **A** Broker + ingest | FXCM History + tick stream → 5 writer-процесів | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI | read-only HTTP renderer, same-origin | `ui_chart_v3/` |

## Ключові принципи

- **SSOT**: один UDS, один `config.json`, один TF allowlist.
- **NoMix / Final > Preview**: `complete=true` завжди перемагає; два різні final source для одного ключа заборонені.
- **Degraded-but-loud**: жодних silent fallback — лише `warnings[]` / `meta.degraded[]`.
- **Disk hot-path ban**: disk лише для bootstrap/scrollback/recovery; interactive = RAM/Redis.
- **Часова геометрія**: `close_time_ms = open_time_ms + tf_s * 1000` (end-excl), UTC epoch ms int.

## Quickstart

```bash
# 1. Python 3.7 venv + залежності
pip install -r requirements.txt

# 2. Секрети (.env — тільки FXCM креденшіали)
cp .env.example .env  # або створіть вручну

# 3. Запуск усіх 5 процесів
python -m app.main --mode all --stdio pipe

# 4. Перевірка
curl http://127.0.0.1:8089/api/status
# Або відкрийте http://127.0.0.1:8089/ у браузері
```

## Quality Gates

```bash
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

Якщо gates FAIL → формальний **NO-GO** до наступних PATCH.

## Документація (SSOT)

Повна документація: **[docs/index.md](docs/index.md)** — єдина точка входу.

| Документ | Опис |
|---|---|
| [docs/index.md](docs/index.md) | Навігація по всій документації |
| [docs/system_current_overview.md](docs/system_current_overview.md) | Архітектура, процеси, схеми, інваріанти |
| [docs/contracts.md](docs/contracts.md) | Реєстр контрактів (bar_v1, window_v1, updates_v1, tick_v1) |
| [docs/ui_api.md](docs/ui_api.md) | HTTP API reference (endpoints, guards, TTL) |
| [docs/config_reference.md](docs/config_reference.md) | Довідник полів config.json |
| [docs/runbooks/production.md](docs/runbooks/production.md) | Production runbook (запуск, інциденти, recovery) |
| [docs/audit/progress.md](docs/audit/progress.md) | Аудит прогресу P0-P6 з evidence |
| [docs/ADR-0001 UnifiedDataStore.md](docs/ADR-0001%20UnifiedDataStore.md) | ADR: UDS як єдина талія |

## Ліцензія

Див. [LICENSE_v1](LICENSE_v1).
