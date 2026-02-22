# Trading Platform v3 (FXCM Connector + UDS + UI)

Торгова платформа "дані → аналітика/SMC → UI → торгова взаємодія" з жорсткими інваріантами та **UnifiedDataStore (UDS)** як єдиним write-center.

## Канон A → C → B

| Шар | Що | Де |
|---|---|---|
| **A** Broker + ingest | FXCM History + tick stream → 5 writer-процесів | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI (http) | read-only HTTP polling renderer, same-origin, порт 8089 | `ui_chart_v3/` |
| **B** UI (ws) | read-only WS real-time renderer, same-origin, порт 8000 | `ui_v4/` + `runtime/ws/ws_server.py` |

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

# 3. Запуск усіх процесів (включаючи ws_server якщо ws_server.enabled=true)
python -m app.main --mode all --stdio pipe

# 4. Перевірка (HTTP polling UI)
curl http://127.0.0.1:8089/api/status
# Або відкрийте http://127.0.0.1:8089/ у браузері

# 5. ui_v4 (WS real-time UI, порт 8000)
# Відкрийте http://127.0.0.1:8000/ у браузері (потребує npm run build у ui_v4/)
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
| [docs/guide_candle_acquisition.md](docs/guide_candle_acquisition.md) | Повний посібник: отримання свічок (FXCM API, календар, watermark, recover) |
| [docs/contracts.md](docs/contracts.md) | Реєстр контрактів (bar_v1, window_v1, updates_v1, tick_v1) |
| [docs/ui_api.md](docs/ui_api.md) | HTTP API reference (endpoints, guards, TTL) |
| [docs/config_reference.md](docs/config_reference.md) | Довідник полів config.json |
| [docs/runbooks/production.md](docs/runbooks/production.md) | Production runbook (запуск, інциденти, recovery) |
| [docs/audit/progress.md](docs/audit/progress.md) | Аудит прогресу P0-P6 з evidence |
| [docs/ADR-0001 UnifiedDataStore.md](docs/ADR-0001%20UnifiedDataStore.md) | ADR: UDS як єдина талія |
| [docs/ADR-0002-derive-chain-from-m1.md](docs/ADR-0002-derive-chain-from-m1.md) | ADR: DeriveChain M1→M3→M5→H4 (Phase 0 завершено) |
| [docs/ADR-0003 Cold Start Hardening.md](docs/ADR-0003%20Cold%20Start%20Hardening.md) | ADR: Cold start hardening (S1 error isolation ✅, S2 supervisor restart ✅, S3-S4 pending) |
| [docs/system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md](docs/system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md) | UI v4 audit: T1-T10 ALL COMPLETE, chart parity DONE |

┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: POLLING (simple, focused, single-responsibility)  │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐                       │
│  │ m1_poller    │    │ d1_fetcher   │                       │
│  │ M1 від FXCM  │    │ D1 від FXCM  │                       │
│  │ ~400 LOC     │    │ ~200 LOC     │                       │
│  └──────┬───────┘    └──────┬───────┘                       │
│         │ commit M1         │ commit D1                     │
│         ▼                   ▼                               │
│  ┌──────────────────────────────────┐                       │
│  │            UDS (SSOT)            │                       │
│  └──────────────┬───────────────────┘                       │
│                 │ updates bus (Redis pub/sub)                │
└─────────────────┼───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2: DERIVE ENGINE (cascade, async, priority)          │
│                                                             │
│  ┌─────────────────────────────────────────────────┐        │
│  │          core/derive.py (PURE, no I/O)          │        │
│  │  GenericBuffer(tf_s)  +  aggregate_bars()       │        │
│  │  DERIVE_CHAIN = declarative cascade rules       │        │
│  └─────────────────────────────────────────────────┘        │
│                                                             │
│  ┌─────────────────────────────────────────────────┐        │
│  │    runtime/ingest/derive_engine.py (I/O layer)  │        │
│  │                                                 │        │
│  │  On new M1 bar →  cascade per symbol:           │        │
│  │    M1 → M3 (3×M1)                              │        │
│  │    M1 → M5 (5×M1)                              │        │
│  │      M5 → M15 (3×M5)                           │        │
│  │        M15 → M30 (2×M15)                       │        │
│  │          M30 → H1 (2×M30)                      │        │
│  │            H1 → H4 (4×H1, calendar+TV anchor)  │        │
│  │                                                 │        │
│  │  ThreadPool: 13 symbols паралельно              │        │
│  │  Priority: watched symbol → front of queue      │        │
│  │  Buffers: GenericBuffer per (symbol, tf_s)      │        │
│  └─────────────────────────────────────────────────┘        │
│         │                                                   │
│         ▼ commit derived bars                               │
│  ┌──────────────────────────────────┐                       │
│  │            UDS (SSOT)            │                       │
│  └──────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3a: UI v3 (HTTP polling, read-only, ZERO domain logic)  │
│  /api/bars → UDS.read_window() (ALL TFs, including H4!)        │
│  /api/updates → UDS updates bus                               │
│  H4 кеш/derive OUT OF UI completely                            │
│  Порт 8089, same-origin, vanilla JS polling                   │
├─────────────────────────────────────────────────────────────┤
│  LAYER 3b: UI v4 (WS real-time, read-only, ZERO domain logic)  │
│  WS full/delta/scrollback → UDS via ws_server.py               │
│  Svelte 5 + LWC 5 + TypeScript, 25 файлів ~4045 LOC          │
│  Порт 8000, same-origin, config-gated                        │
│  Chart parity DONE, audit T1-T10 COMPLETE                     │
└─────────────────────────────────────────────────────────────┘

## Ліцензія

Див. [LICENSE_v1](LICENSE_v1).
