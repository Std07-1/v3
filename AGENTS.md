# AGENTS.md — Trading Platform v3

> **Системний довідник** — структура, команди, конфігурація, схеми.
> Правила, інваріанти, заборони → `.github/copilot-instructions.md`.
> **Last Updated**: 2026-04-16

---

## 1. Project Overview

**Trading Platform v3** — це торгова платформа "дані → аналітика/SMC → UI → торгова взаємодія" з жорсткими інваріантами та **UnifiedDataStore (UDS)** як єдиним write-center.

### 1.1 Архітектура A → C → B

| Шар | Що | Де |
|---|---|---|
| **A** Broker + ingest | FXCM/Binance History + tick stream → writer-процеси | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI (ws) | read-only WS real-time renderer, same-origin, порт 8000 | `ui_v4/` + `runtime/ws/ws_server.py` |
| **TUI** | aione-top: інтерактивний TUI-монітор процесів/pipeline | `aione_top/` |

---

## 1.1 Актуальний статус (2026-04-16)

- Потік B (мульти-символьна активація) — **відкладено** через integrity derived TF (див. ADR-0025)
- Всі результати audit/rebuild зафіксовані в [docs/adr/0025-potik-b-data-quality-summary.md](docs/adr/0025-potik-b-data-quality-summary.md)
- Потік B закритий, фокус на XAU/USD та SMC engine (Потік C)
- SMC Engine **Implemented** (ADR-0024): E1+S4+E2+N1/N2/N3+D1-D3+ADR-0024a, comprehensive pytest coverage
- Elimination Engine + Confluence Scoring (ADR-0028, ADR-0029): display budget, 8-factor grade
- TF Sovereignty + Bias Banner (ADR-0030-alt, ADR-0031): cross-TF projection, multi-TF bias display
- Sessions & Killzones **Implemented** (ADR-0035): Asia/London/NY session H/L levels, killzone context, F9 sweep confluence, narrative session integration, 40 tests
- Structure Detection V2 **Implemented** (ADR-0047): BOS/CHoCH canonical ICT, confirmation_bars, FVG display cap
- Delta Frame State Sync **Implemented** (ADR-0042): zone_grades, pd_state, bias_map thick delta
- UI v4 Canvas Safe Zones **Implemented** (ADR-0043): CANVAS_SAFE_TOP_Y, pd_state null-clear, boot_id reset
- Client-Side Replay (ADR-0027): TradingView-style replay з data_v3/
- Binance Second Broker **Implemented** (ADR-0037): BTCUSDT/ETHUSDT 24/7 ingest
- Platform Wake Engine (ADR-0049): wake conditions + external consumer IPC — **Accepted**
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
- `logs/supervisor_{mode}.pid` blocks duplicate supervisor instances

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
| `R_REJECTOR` | **QA Rejector & Final Gate** | `.github/role_spec_rejector_v1.md` |
| `R_ELEVATOR` | **Ambition Auditor** | `.github/role_spec_elevator_v1.md` |

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
│       ├── structure.py   # detect_structure() — BOS/CHoCH V2 (ADR-0047)
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
│   │   ├── broker/binance/ # Binance Futures provider (24/7, ADR-0037)
│   │   ├── binance_ingest_worker.py # Binance M1 ingest + backward crawl (ADR-0037/0038)
│   │   ├── broker_sidecar.py  # FXCM M1 fetcher + tick relay V2 via Redis IPC (ADR-0016, .venv37/)
│   │   ├── m1_ingestion_worker.py # Platform-side M1 ingestion with BrokerRedisProxy (ADR-0016, .venv/)
│   │   ├── polling/       # m1_poller (legacy single-process mode)
│   │   ├── tick_agg.py    # TickAggregator (preview-plane)
│   │   ├── tick_common.py # спільні утиліти для tick pipeline
│   │   ├── tick_preview_worker.py # tick→preview
│   │   ├── tick_publisher_fxcm.py # [DEPRECATED] FXCM tick — тепер інтегровано в broker_sidecar
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
│   ├── relay/             # Signal relay (ADR-0039)
│   │   └── signal_relay.py # Signal event relay
│   └── obs_60s.py         # 60s observability
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
│   ├── exit_gates/        # 29 AST gates (dependency, contract, geometry, dual_python)
│   ├── rebuild_from_m1.py # Canonical rebuild all derived TFs
│   ├── repair/            # htf_rebuild, htf_tail_sync, repair_m1_gaps
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
│   ├── role_spec_*.md     # 11 role specs (patch_master, bug_hunter, smc_chief, doc_keeper, trader, chart_ux, architect, compliance, signal_architect, mentor, rejector)
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

### 2.1 trader-v3/ — AI Trading Agent "Арчі" (окрема підсистема)

> **46 .py files, ~18,000 LOC** (April 2026, post hibernation + monitor v2). Deploy = SCP to VPS.

```
trader-v3/                          # .gitignore'd — proprietary
├── bot/                            # Bot application code (42 .py, ~17,800 LOC)
│   ├── main.py                    # Entry point, Telegram polling (366)
│   ├── config.py                  # Config dataclasses (533)
│   ├── agent/                     # AI agent core
│   │   ├── core.py               # AgentCore: Anthropic calls, tool use (1210)
│   │   ├── prompts.py            # System prompts, personality DNA extraction (752)
│   │   ├── discipline.py         # Pre/post trade discipline checks (480)
│   │   ├── observation_router.py # TSM/observation routing (ADR-033) (299)
│   │   ├── scanner.py            # Market scanning logic (180)
│   │   └── structured_output.py  # Structured output parsing (180)
│   ├── enrichment/                # External data enrichment
│   │   ├── market_data.py        # Yahoo/ForexFactory market data (416)
│   │   └── news_feed.py          # News feed integration (150)
│   ├── scheduling/                # Proactive scheduling
│   │   ├── monitor.py            # Proactive monitor loop v3 + TSM (2064)
│   │   ├── mechanical.py         # Mechanical operations (timers, budget reset) (174)
│   │   ├── cost.py               # API cost tracking (72)
│   │   └── scheduler.py          # Cron-like scheduler (45)
│   ├── state/                     # Agent state management (largest package)
│   │   ├── directives.py         # AgentDirectives: bias, levels, scenarios, wake (3586)
│   │   ├── manager.py            # StateManager: load/save/merge state (1049)
│   │   ├── curator.py            # Knowledge curation + learning journal (476)
│   │   ├── thesis.py             # ThesisStateMachine (TSM, ADR-033) (395)
│   │   ├── self_eval.py          # Self-evaluation + accuracy tracking (291)
│   │   ├── thinking_archive.py   # Consciousness archive (ADR-018) (263)
│   │   ├── event_journal.py      # Structured event journaling (236)
│   │   ├── hibernation.py        # API limit hibernation mode (189)
│   │   ├── predictions.py        # Prediction tracking (163)
│   │   ├── conv_memory.py        # Conversation memory management (143)
│   │   ├── digest.py             # Daily digest generation (130)
│   │   └── forecasts.py          # Forecast tracking (115)
│   ├── tools/                     # Agent tool use
│   │   └── executor.py           # Tool executor (ADR-026) (263)
│   └── transport/                 # I/O layer
│       ├── handlers.py           # Telegram message handlers (2169)
│       ├── web_inbox.py          # Archi Console web chat (305)
│       ├── platform.py           # Platform WS/HTTP client (214)
│       ├── voice.py              # TTS/STT voice pipeline (187)
│       ├── events.py             # Event dispatching (185)
│       ├── wake_sync.py          # Wake conditions → Redis sync (164)
│       ├── telegram.py           # Telegram transport utilities (113)
│       └── wake_reader.py        # WakeEngine events reader (99)
├── docs/
│   ├── ARCHITECTURE.md           # Bot architecture + I7 governance (§3a)
│   ├── CURRENT_STATE.md          # Current snapshot (updated regularly)
│   ├── CODEMAP.md                # Code map (needs refresh)
│   └── adr/                      # Bot ADRs (001–034)
│       └── ADR-024-autonomy-charter.md  # I7 SSOT
├── smc_trader_prompt_v3.md       # Арчі's personality prompt (~1,500 lines, 95KB)
├── ui_archi/                     # Archi Console (Svelte web UI)
└── tests/                        # 153+ tests
```

**Інваріант I7 — Autonomy-First (ADR-024)**:

Арчі — автономний AI-агент з власною особистістю і правом прийняття рішень.
Код trader-v3/ = **advisory + explain**, рішення приймає Арчі.

| Що | Правило |
|---|---|
| Cooldowns (VP, channel) | Warning → agent decides. НЕ hard block |
| Economy mode (budget) | Advisory log → agent picks model. НЕ force-downgrade |
| System timers | Agent can delete any timer. НЕ re-injection |
| Suppress/notify | Default = deliver. Agent opt-out. НЕ suppress-by-default |
| Prompt language | Recommendations, not commands. "З досвіду" not "НІКОЛИ" |
| Hard blocks allowed | ТІЛЬКИ: kill switch, daily $ hard cap, owner-only, anti-hallucination |

**Для всіх агентів (R_PATCH_MASTER, R_BUG_HUNTER, etc.)** при роботі з `trader-v3/`:
1. Перед додаванням будь-якого обмеження → перевірити I7
2. `if blocked:` або `return` без пояснення Арчі = I7 violation
3. Перечитати `trader-v3/docs/adr/ADR-024-autonomy-charter.md` перед PATCH

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

# Windows note: supervisor kills worker process trees and keeps logs/supervisor_{mode}.pid
# because Python 3.14 venv launcher may create a trampoline process before real worker.

# Окремі режими
python -m app.main --mode m1_poller          # Legacy single-process M1 (Python 3.7)
python -m app.main --mode broker_sidecar     # FXCM M1 fetch + tick relay V2 (.venv37/, ADR-0016)
python -m app.main --mode m1_ingestion_worker # Platform M1 ingestion (.venv/, ADR-0016)
python -m app.main --mode tick_publisher     # [DEPRECATED] use broker_sidecar — VPS smc-ticks permanently stopped
python -m app.main --mode tick_preview       # Preview worker
python -m app.main --mode binance_ingest_worker  # Binance M1 ingest (ADR-0037/0038)
python -m app.main --mode binance_tick_publisher # Binance tick stream (ADR-0037)
python -m app.main --mode ws_server          # WS UI (port 8000)

# TUI монітор (окремо)
python -m aione_top
```

### 3.3 Health Checks

```bash
# API status
curl http://127.0.0.1:8000/api/status

# UI v4 (WebSocket real-time)
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
| `test_ob_impulse_grace.py` | OB impulse grace: zone mitigation skips impulse bars (6 тестів) |
| `test_adr0042_config_ssot.py` | ADR-0042 config SSOT compliance |
| `test_adr0042_delta_parity.py` | ADR-0042 delta frame parity |
| `test_app_main_supervisor.py` | Supervisor lifecycle, restart policy |
| `test_cascade_no_dupes.py` | Derive cascade dedup |
| `test_hardcode_scan.py` | Hardcoded values scan |
| `test_htf_rebuild.py` | HTF rebuild correctness |
| `test_htf_running_accumulator.py` | HTF running accumulator |
| `test_htf_tail_sync.py` | HTF tail synchronization |
| `test_m1_flat_bar_policy.py` | M1 flat bar filtering policy |
| `test_m1_ingestion_ipc.py` | M1 ingestion IPC (broker sidecar) |
| `test_market_calendar.py` | Market calendar breaks/sessions |
| `test_near_dedup_d1.py` | D1 near-dedup logic |
| `test_overdue_cascade.py` | Overdue cascade detection |
| `test_qa_001.py` | QA regression #001 |
| `test_qa_002.py` | QA regression #002 |
| `test_qa_003.py` | QA regression #003 |
| `test_qa_003_exhaustive.py` | QA regression #003 exhaustive |
| `test_s2_tick_drops_loud.py` | Tick drops degraded-but-loud (I5) |
| `test_s6_tf_allowlist_ssot.py` | TF allowlist SSOT compliance |
| `test_shell_composer.py` | Shell composer logic |
| `test_signal_journal_recovery.py` | Signal journal recovery |
| `test_smc_d1_display_filter.py` | SMC D1 display filter |
| `test_smc_e2_liquidity.py` | SMC E2: liquidity detection |
| `test_smc_e2_pd_inducement.py` | SMC E2: P/D + inducement |
| `test_smc_narrative.py` | SMC narrative (Context Flow, ADR-0033) |
| `test_smc_signals.py` | SMC signal engine (ADR-0039) |
| `test_smc_tda_p0_p1.py` | TDA P0+P1 integration |
| `test_tick_agg.py` | Tick aggregator logic |
| `test_tick_preview_calendar.py` | Tick preview + calendar gate |
| `test_structure_v2.py` | Structure V2: BOS/CHoCH canonical (ADR-0047) |

> **54 test files total** on disk. Table above lists key tests; run `pytest tests/ -v` for full coverage.

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

- UI WS: `127.0.0.1:8000` (localhost only)
- FXCM: HTTPS з автентифікацією
- Redis: localhost only (за замовчуванням)

---

## 8. Troubleshooting

### 8.1 Common Issues

| Проблема | Діагностика | Рішення |
|---|---|---|
| `prime_pending` | `curl /api/status` | Чекати cold start завершення |
| `prime_broken` | Логи `m1_poller` / `binance_ingest_worker` | Перевірити FXCM credentials / Binance API |
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
tail -f logs/m1_poller.out.log
tail -f logs/ws_server.out.log
```

---

## 9. Quick Reference

| Команда | Призначення |
|---|---|
| `python -m app.main --mode all --stdio pipe` | Запуск всіх процесів |
| `python -m tools.run_exit_gates` | Quality gates |
| `python -m pytest tests/ -v` | Run tests |
| `python -m aione_top` | TUI монітор |
| `curl http://127.0.0.1:8000/api/status` | Health check |

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
- **Правила, інваріанти, заборони**: `.github/copilot-instructions.md`
