# AGENTS.md — Trading Platform v3 (FXCM Connector)

> **Системний довідник** — структура, команди, конфігурація, схеми.
> Правила, інваріанти, заборони → `.github/copilot-instructions.md`.
> Командна взаємодія агентів → `CLAUDE.md`.
> **Last Updated**: 2026-03-22

---

## 1. Project Overview

**Trading Platform v3** — це торгова платформа "дані → аналітика/SMC → UI → торгова взаємодія" з жорсткими інваріантами та **UnifiedDataStore (UDS)** як єдиним write-center.

### 1.1 Архітектура A → C → B

| Шар | Що | Де |
|---|---|---|
| **A** Broker + ingest | FXCM History + tick stream → 5 writer-процесів | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI (http) | read-only HTTP polling renderer, same-origin, порт 8089 | `ui_chart_v3/` *(private module)* |
| **B** UI (ws) | read-only WS real-time renderer, same-origin, порт 8000 | `ui_v4/` + `runtime/ws/ws_server.py` |
| **TUI** | aione-top: інтерактивний TUI-монітор процесів/pipeline | `aione_top/` |

---

## 1.1 Актуальний статус (2026-03-13)

- Потік B (мульти-символьна активація) — **відкладено** через integrity derived TF (див. ADR-0025)
- Всі результати audit/rebuild зафіксовані в [docs/adr/0025-potik-b-data-quality-summary.md](docs/adr/0025-potik-b-data-quality-summary.md)
- Потік B закритий, фокус на XAU/USD та SMC engine (Потік C)
- SMC Engine **Implemented** (ADR-0024): E1+S4+E2+N1/N2/N3+D1-D3+ADR-0024a, comprehensive pytest coverage
- Elimination Engine + Confluence Scoring (ADR-0028, ADR-0029): display budget, 8-factor grade
- TF Sovereignty + Bias Banner (ADR-0030-alt, ADR-0031): cross-TF projection, multi-TF bias display
- Sessions & Killzones **Implemented** (ADR-0035): Asia/London/NY session H/L levels, killzone context, F9 sweep confluence, narrative session integration, 40 tests
- Client-Side Replay (ADR-0027): TradingView-style replay з data_v3/
- Для майбутньої активації інших символів потрібен окремий audit/fix integrity derived TF

### 1.2 Технологічний стек

**Backend (Python, dual-venv — ADR-0016):**

- Python ≥3.11 (main venv `.venv/` — platform, UDS, derive, SMC, UI; current workspace migration documented on Python 3.14.2)
- Python 3.7 (broker venv `.venv37/` — forexconnect SDK only)
- Redis 5.0.1 (pub/sub, snapshots, updates bus, broker IPC)
- numpy ≥1.26, pandas ≥2.1
- forexconnect 1.6.43 (FXCM SDK, `.venv37/` only)
- aiohttp ≥3.9 (для UI HTTP API)

**Windows supervisor rail (ADR-0016 Appendix C):**

- Python 3.14 venv launcher creates trampoline processes on Windows
- `app/main.py` uses `_kill_tree()` (`taskkill /T`) instead of single-PID terminate
- `logs/supervisor.pid` blocks duplicate supervisor instances

**Frontend UI v4:**

- Svelte 5 (runes mode) + Vite 6 + TypeScript 5.7
- lightweight-charts 5.0.0 (LWC)
- WebSocket transport

**Інфраструктура:**

- Redis (db=1, namespace `v3_local`)
- JSONL файли як SSOT (`data_v3/{symbol}/tf_{tf_s}/`)

### 1.3 Ролі агента (Role Routing)

Source of truth: .github/copilot-instructions.md
Mirror mode: index-only
Do not redefine triggers or precedence here.

AGENTS.md §1.3 тримає лише компактний індекс ролей для discovery/navigation.
Повні routing rules, trigger vocabulary і precedence живуть у `.github/copilot-instructions.md` → `РІВЕНЬ 0 — РОЛІ АГЕНТА`.
CI gate звіряє ID→spec mapping і не дозволяє drift.

| ID | Роль | Файл |
|---|---|---|
| `R_PATCH_MASTER` | **Patch Master** | `.github/role_spec_patch_master_v1.md` |
| `R_BUG_HUNTER` | **Bug Hunter** | `.github/role_spec_bug_hunter_v2.md` |
| `R_SMC_CHIEF` | **SMC Chief Strategist** | `.github/role_spec_smc_chief_strategist_v1.md` |
| `R_DOC_KEEPER` | **Doc Keeper** | `.github/role_spec_doc_keeper_v1.md` |
| `R_TRADER` | **SMC Trader** | `.github/role_spec_trader_v1.md` |
| `R_CHART_UX` | **Chart Experience Product Designer** | `.github/role_spec_chart_ux_v1.md` |
| `R_ARCHITECT` | **Systems Architect** | `.github/role_spec_architect_v1.md` |
| `R_COMPLIANCE` | **Compliance & Safety Officer** | `.github/role_spec_compliance_v1.md` |
| `R_SIGNAL_ARCHITECT` | **Signal Architect** | `.github/role_spec_signal_architect_v1.md` |
| `R_MENTOR` | **Personal SMC Mentor (DarkTrader)** | `.github/role_spec_mentor_v1.md` |

---

## 2. Project Structure

```
v3/
├── app/                    # Supervisor та lifecycle
│   └── main.py            # Головний supervisor (S2 restart backoff, --mode all/replay/…)
│
├── core/                   # Pure logic (NO I/O)
│   ├── model/bars.py      # CandleBar dataclass, FINAL_SOURCES
│   ├── derive.py          # DERIVE_CHAIN, GenericBuffer, aggregate_bars
│   ├── buckets.py         # Time bucket math
│   ├── config_loader.py   # Config parsing helpers
│   ├── contracts/         # JSON Schema (bar_v1, tick_v1, updates_v1, window_v1)
│   └── smc/               # SMC Engine — pure logic, NO I/O (ADR-0024)
│       ├── types.py       # SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta, NarrativeBlock, ActiveScenario
│       ├── config.py      # SmcConfig + nested configs
│       ├── swings.py      # detect_swings() — rolling window period
│       ├── structure.py   # detect_structure() — BOS/CHoCH
│       ├── order_blocks.py # detect_order_blocks()
│       ├── fvg.py         # detect_fvg() — bull/bear + height guard
│       ├── liquidity.py   # detect_liquidity_levels()
│       ├── premium_discount.py # detect_premium_discount()
│       ├── inducement.py  # detect_inducements()
│       ├── key_levels.py  # detect_key_levels() — PDH/PDL/DH/DL (ADR-0024b)
│       ├── confluence.py  # confluence scoring — 8-factor grade A+/A/B/C (ADR-0029)
│       ├── momentum.py    # displacement detection — body/ATR ratio
│       ├── sessions.py    # session H/L, killzones, classify (ADR-0035)
│       ├── context_stack.py # ContextStack — cross-TF zone aggregation
│       ├── narrative.py   # synthesize_narrative() — Context Flow (ADR-0033)
│       ├── engine.py      # SmcEngine orchestrator + zone lifecycle
│       └── tda/           # TDA Cascade — daily signal engine (ADR-0040)
│           ├── types.py       # TdaCascadeConfig, TdaSignal, FvgEntry, TradeState
│           ├── stage1_macro.py    # D1 macro direction (3-bar pivot + slope)
│           ├── stage2_h4_confirm.py # H4 confirmation (midpoint + trending)
│           ├── stage3_session.py   # Session narrative (Asia/London sweep)
│           ├── stage4_fvg_entry.py # M15 FVG entry (touch + close outside)
│           ├── stage5_trade_mgmt.py # Config F trade management
│           └── orchestrator.py    # 4-stage cascade orchestrator
│
├── runtime/               # I/O та процеси
│   ├── ingest/            # Data ingestion
│   │   ├── broker/fxcm/   # FXCM provider (.venv37/ only)
│   │   ├── broker_sidecar.py  # Stateless FXCM M1 fetcher via Redis IPC (ADR-0016, .venv37/)
│   │   ├── m1_ingestion_worker.py # Platform-side M1 ingestion with BrokerRedisProxy (ADR-0016, .venv/)
│   │   ├── polling/       # m1_poller (legacy single-process mode)
│   │   ├── tick_agg.py    # TickAggregator (preview-plane)
│   │   ├── tick_common.py # спільні утиліти для tick pipeline
│   │   ├── tick_preview_worker.py # tick→preview
│   │   ├── tick_publisher_fxcm.py # FXCM tick → Redis PubSub (.venv37/)
│   │   ├── derive_engine.py  # Derive cascade (M1→H4+D1, ADR-0023)
│   │   ├── market_calendar.py # Calendar breaks (UTC)
│   │   └── replay.py      # ReplayFeeder — offline replay (ADR-0017/0027)
│   ├── store/             # UDS та storage layers
│   │   ├── uds.py         # UnifiedDataStore (SSOT)
│   │   ├── layers/        # disk_layer, ram_layer, redis_layer
│   │   ├── redis_snapshot.py, redis_keys.py, redis_spec.py
│   │   └── ssot_jsonl.py  # JSONL append-only writer
│   ├── ws/                # WebSocket server (ui_v4)
│   │   ├── ws_server.py   # Port 8000 (SmcRunner integration)
│   │   └── candle_map.py  # bar→Candle mapping
│   ├── smc/               # SMC runtime wiring (ADR-0024)
│   │   ├── smc_runner.py  # SmcRunner: warmup + on_bar callback
│   │   └── tda_live.py    # TdaLiveRunner: TDA cascade I/O wrapper (ADR-0040)
│   └── obs_60s.py         # 60s observability
│
├── ui_chart_v3/           # HTTP UI (port 8089, polling)
│   └── server.py          # HTTP API + static server
│
├── ui_v4/                 # WebSocket UI (port 8000, Svelte 5)
│   ├── src/
│   │   ├── types.ts       # SSOT types: RenderFrame, Candle, SmcData, Drawing
│   │   ├── App.svelte     # root wiring
│   │   ├── app/           # diagState, frameRouter, edgeProbe
│   │   ├── ws/            # WSConnection, WsAction creators
│   │   ├── stores/        # smcStore, replayStore, favorites, meta, viewCache
│   │   ├── layout/        # ChartPane, ChartHud, StatusBar, DrawingToolbar, ReplayBar, BiasBanner, ...
│   │   └── chart/         # engine.ts, lwc.ts, themes.ts, interaction.ts, OverlayRenderer.ts, DrawingsRenderer.ts, overlay/DisplayBudget.ts
│   ├── package.json       # npm deps
│   └── README_DEV.md      # UI v4 dev guide
│
├── aione_top/             # TUI монітор процесів
│   └── app.py             # `python -m aione_top`
│
├── tools/                 # Утиліти та діагностика (isolated)
│   ├── run_exit_gates.py  # Quality gates runner
│   ├── exit_gates/        # 26 AST gates (dependency, contract, geometry, dual_python)
│   ├── rebuild_from_m1.py # Canonical rebuild all derived TFs
│   ├── repair/            # htf_rebuild, htf_tail_sync
│   └── diag/              # classify gaps, clear redis, disk_max_open_ms
│
├── tests/                 # pytest suite: SMC, UDS, WS, ingest, supervisor rails
│   ├── test_smc_*.py      # SMC tests (e1, e2, runner, key_levels, n1_lifecycle, d1_display, confluence)
│   ├── test_derive_*.py   # Derivation tests
│   ├── test_uds_*.py      # UDS tests (split-brain, partial penalty)
│   ├── test_s*_*.py       # SSOT compliance tests (s1–s6)
│   ├── test_ws_server.py  # WS tests
│   └── test_candle_map.py # Candle mapping tests
│
├── .github/               # AI agent governance
│   ├── copilot-instructions.md  # SSOT інструкція для AI-агентів
│   ├── role_spec_*.md     # 8 role specs (patch_master, bug_hunter, smc_chief, doc_keeper, trader, chart_ux, architect, compliance)
│   └── prompts/           # 8 prompt files (adr, discovery, patch, review, ...)
│
├── docs/                  # Повна документація
│   ├── index.md           # Точка входу
│   ├── system_current_overview.md  # Архітектура
│   ├── adr/               # ADR archive (canonical architecture decisions index)
│   ├── contracts.md       # JSON Schema contracts
│   ├── ui_api.md          # HTTP API reference
│   └── runbooks/          # Production runbooks
│
├── config.json            # SSOT конфігурація
├── pyproject.toml         # Python package metadata (>=3.11)
├── requirements.txt       # Runtime deps (main venv, >=3.11)
├── requirements-broker.txt # Broker deps (.venv37/, forexconnect)
└── data_v3/               # SSOT JSONL data storage
```

---

## 3. Build and Run Commands

### 3.1 Initial Setup (Dual-venv, ADR-0016)

```bash
# 1. Main venv (Python >=3.11) — platform, UDS, derive, SMC, UI
#    Current documented migration incident: Python 3.14.2, see ADR-0016 Appendix C
python -m venv .venv
.venv/Scripts/activate    # Windows
pip install -r requirements.txt

# 2. Broker venv (Python 3.7) — forexconnect SDK only
#    Потребує окрему інсталяцію Python 3.7
C:\Python37\python.exe -m venv .venv37
.venv37/Scripts/pip install -r requirements-broker.txt

# 3. Secrets (.env — лише FXCM credentials)
cp .env.example .env  # відредагуй FXCM_USER / FXCM_PASS / FXCM_URL

# 4. UI v4 build (опціонально, для WebSocket UI)
cd ui_v4
npm install
npm run build
cd ..
```

### 3.2 Run Commands

```bash
# Запуск усіх процесів (supervisor mode)
python -m app.main --mode all --stdio pipe

# Windows note: supervisor kills worker process trees and keeps logs/supervisor.pid
# because Python 3.14 venv launcher may create a trampoline process before real worker.

# Окремі режими
python -m app.main --mode connector          # D1 fetcher (disabled, ADR-0023)
python -m app.main --mode m1_poller          # Legacy single-process M1 (Python 3.7)
python -m app.main --mode broker_sidecar     # FXCM fetcher sidecar (.venv37/, ADR-0016)
python -m app.main --mode m1_ingestion_worker # Platform M1 ingestion (.venv/, ADR-0016)
python -m app.main --mode tick_publisher     # Tick stream (.venv37/)
python -m app.main --mode tick_preview       # Preview worker
python -m app.main --mode ui                 # HTTP UI (port 8089)
python -m app.main --mode ws_server          # WS UI (port 8000)

# TUI монітор (окремо)
python -m aione_top
```

### 3.3 Health Checks

```bash
# HTTP API status
curl http://127.0.0.1:8089/api/status

# UI v3 (HTTP polling)
open http://127.0.0.1:8089/

# UI v4 (WebSocket real-time) — requires ws_server.enabled=true
open http://127.0.0.1:8000/
```

---

## 4. Testing Strategy

### 4.1 Quality Gates (MUST PASS)

```bash
# Головні ворота якості — FAIL = NO-GO
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

### 4.2 Test Suite

```bash
# Run all tests
python -m pytest tests/ -v

# Специфічні категорії
python -m pytest tests/test_derive_*.py -v    # Derivation logic
python -m pytest tests/test_uds_*.py -v       # UDS compliance
python -m pytest tests/test_s*_*.py -v        # SSOT invariants
```

### 4.3 Key Test Files

| Тест | Що перевіряє |
|---|---|
| `test_derive_calendar_pause_partial.py` | Каскадна деривація з calendar pause |
| `test_uds_commit_split_brain.py` | Split-brain resilience |
| `test_uds_partial_penalty.py` | Partial bar penalty logic |
| `test_s1_preview_ttl_ssot.py` | Preview TTL compliance (ADR-0001) |
| `test_s3_final_sources_ssot.py` | FINAL_SOURCES integrity |
| `test_ws_server.py` | WebSocket server functionality |
| `test_smc_e1.py` | SMC E1: swings, structure, OB, FVG, engine |
| `test_smc_runner.py` | SMC Runner: warmup, on_bar, delta, performance |
| `test_smc_key_levels.py` | SMC key levels: PDH/PDL/DH/DL |
| `test_smc_n1_lifecycle.py` | SMC N1: zone lifecycle (merge/evict/decay) |
| `test_smc_confluence.py` | SMC confluence scoring: 8 factors, grade (ADR-0029) |
| `test_smc_sessions.py` | SMC sessions: session H/L, killzones, F9 sweep (ADR-0035) |
| `test_d1_derive.py` | D1 derive from M1 (ADR-0023) |
| `test_candle_map.py` | bar→Candle mapping (R2 closure) |
| `test_tda_stage1.py` | TDA Stage 1: D1 macro direction (ADR-0040) |
| `test_tda_stage2.py` | TDA Stage 2: H4 confirmation |
| `test_tda_stage3.py` | TDA Stage 3: session narrative (sweep) |
| `test_tda_stage4.py` | TDA Stage 4: M15 FVG entry |
| `test_tda_stage5.py` | TDA Stage 5: Config F trade management |
| `test_tda_orchestrator.py` | TDA cascade orchestrator (4-stage flow) |
| `test_tda_live.py` | TDA runtime wiring + config SSOT |
| `test_pd_state.py` | PdState wire format + PremiumDiscountConfig compat (ADR-0041, 21 тест) |

---

## 5. Code Conventions

### 5.1 Naming Conventions

- **Python**: `snake_case` для функцій/змінних, `PascalCase` для класів
- **Time fields**: `open_time_ms`, `close_time_ms` (epoch milliseconds)
- **Redis keys**: `{namespace}:ohlcv:snap:{sym}:{tf_s}`, `{NS}:preview:curr:{sym}:{tf_s}`
- **Config keys**: `snake_case` в JSON
- **CandleBar fields** (CRITICAL): `.o`, `.h`, `.low`, `.c`, `.v` — НЕ `.l`! Wire/dict формат використовує `l`, але dataclass поле = `low`. Завжди звіряти з `core/model/bars.py:CandleBar`

### 5.2 Time Geometry (Dual Convention) — CRITICAL

| Шар | Поле | Семантика | Формула |
|---|---|---|---|
| CandleBar / SSOT JSONL / HTTP API | `close_time_ms` | **end-excl** | `open_time_ms + tf_s * 1000` |
| Redis (ohlcv / preview:curr / preview:tail) | `close_ms` | **end-incl** | `open_ms + tf_s * 1000 - 1` |

- Конвертація end-excl → end-incl відбувається **тільки** на межі Redis write
- При читанні з Redis, UDS перераховує `close_ms = open_ms + tf_s*1000`

### 5.3 Logging Format

```python
# Structured logs з префіксами
logging.info("EVENT_NAME key1=%s key2=%d", value1, value2)
logging.warning("DEGRADED_REASON symbol=%s tf=%s", sym, tf)
```

Префікси:

- `SUPERVISOR_*` — supervisor events
- `PRIME_READY_*` — cold start readiness
- `UDS_*` — UDS operations
- `DERIVE_*` — derive engine

---

## 6. Configuration

### 6.1 Key Config Files

| Файл | Призначення |
|---|---|
| `config.json` | SSOT policy (symbols, TFs, calendar, Redis, broker_python) |
| `.env` | Secrets only (FXCM credentials) |
| `env_profile.py` | Env loading logic |
| `requirements-broker.txt` | Broker venv deps (.venv37/) |

### 6.2 Critical Config Sections

```json
{
  "symbols": ["XAU/USD", "XAG/USD", "BTCUSDT", "ETHUSDT"],  // 4 активних символи
  "tf_allowlist_s": [60, 180, 300, 900, 1800, 3600, 14400, 86400],
  "redis": { "enabled": true, "host": "127.0.0.1", "port": 6379, "db": 1 },
  "ws_server": { "enabled": true, "host": "127.0.0.1", "port": 8000 },
  "broker_python": ".venv37/Scripts/python.exe",  // ADR-0016: шлях до Python 3.7
  "bootstrap": { "prime_ready_timeout_s": 120, ... }
}
```

### 6.3 Environment Variables

```bash
# FXCM credentials (required)
FXCM_USER=your_user
FXCM_PASS=your_pass
FXCM_URL=https://www.fxcorporate.com/Hosts.jsp

# Redis override (optional)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

---

## 7. Security Considerations

### 7.1 Secrets Management

- **Ніколи** не коміть `.env` — він у `.gitignore`
- Використовуй `.env.example` як шаблон
- FXCM credentials завантажуються через `env_profile.load_env_secrets()`

### 7.2 Data Protection

- `data_v3/` — локальне сховище JSONL (чутливі дані)
- `logs/` — логи процесів (можуть містити sensitive data)
- Redis db=1 — не production shared instance

### 7.3 Network Security

- UI HTTP: `127.0.0.1:8089` (localhost only)
- UI WS: `127.0.0.1:8000` (localhost only)
- FXCM: HTTPS з автентифікацією
- Redis: localhost only (за замовчуванням)

---

## 8. Troubleshooting

### 8.1 Common Issues

| Проблема | Діагностика | Рішення |
|---|---|---|
| `prime_pending` | `curl /api/status` | Чекати cold start завершення |
| `prime_broken` | Логи `connector`/`m1_poller` | Перевірити FXCM credentials |
| `redis_spec_mismatch` | UDS warnings | Перевірити Redis TTL конфігурацію |
| `insufficient_warmup` | UI показує порожній графік | Зачекати bootstrap або перезапустити |

### 8.2 Recovery Commands

```bash
# Перезбірка derived барів з M1
python -m tools.rebuild_from_m1 --symbol XAU/USD --tf 300

# Deduplication M1/M3
python -m tools.dedup_rebuild_m1m3 --symbol XAU/USD

# Purge broken bars
python -m tools.purge_broken_bars --symbol XAU/USD --tf 14400
```

### 8.3 Log Locations

```bash
# Supervisor mode (stdio=pipe) — логи в консолі
# Supervisor mode (stdio=files) — логи в:
ls logs/*.log

# Окремі процеси (якщо запущені вручну):
tail -f logs/connector.out.log
tail -f logs/m1_poller.out.log
```

---

## 9. Quick Reference

| Команда | Призначення |
|---|---|
| `python -m app.main --mode all --stdio pipe` | Запуск всіх процесів |
| `python -m tools.run_exit_gates` | Quality gates |
| `python -m pytest tests/ -v` | Run tests |
| `python -m aione_top` | TUI монітор |
| `curl http://127.0.0.1:8089/api/status` | Health check |

### Key Files to Know

| Файл | Чому важливий |
|---|---|
| `runtime/store/uds.py` | UDS — єдине місце запису |
| `core/derive.py` | DERIVE_CHAIN — логіка агрегації TF |
| `core/model/bars.py` | CandleBar, FINAL_SOURCES — модель даних |
| `config.json` | SSOT конфігурація |
| `docs/index.md` | Навігація по документації |

### Resources

- **Документація**: `docs/index.md`
- **ADR Index**: `docs/adr/index.md`
- **Config Reference**: `docs/config_reference.md`
- **Production Runbook**: `docs/runbooks/production.md`
- **Changelog**: `CHANGELOG.md` / `changelog.jsonl`
- **Правила, інваріанти, заборони**: `.github/copilot-instructions.md`
- **Командна взаємодія агентів**: `CLAUDE.md`
