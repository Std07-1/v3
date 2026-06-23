# Trading Platform v3 (FXCM Connector + UDS + UI)

[![CI](https://github.com/Std07-1/v3/actions/workflows/ci.yml/badge.svg)](https://github.com/Std07-1/v3/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

![AI·ONE v3 — live XAU/USD M15 chart with SMC markup (FVG, BOS, CHoCH, EQH) and Archi's autonomous market thesis](docs/assets/hero.png)

**Real-time Smart Money Concepts (SMC) analytics for gold, indices and crypto** — a
broker-grade data pipeline (FXCM / Binance) feeding a live WebSocket chart, wired over
Redis to **Archi**, an autonomous Claude trading agent that reasons, remembers, and
decides for itself.

**▶ Live: [aione-smc.com](https://aione-smc.com/)**

### Why it isn't just another trading bot

- **Autonomy-first AI agent** — Archi sets its own wake conditions, runs a 7-layer
  memory, writes its own market thesis, and is *never silently overridden*. Code
  advises; the agent decides (constitutional invariant **I7**).
- **Hard data invariants** — a single `UnifiedDataStore` write-center, `Final > Preview`,
  degraded-but-loud (no silent fallbacks). Every non-trivial decision is captured in
  **47+ ADRs**.
- **$0 analytics** — SMC structure (BOS / CHoCH), order blocks, FVG, liquidity,
  premium/discount and confluence scoring are computed **in-process** — no paid
  signal feeds.
- **Built like production** — dual-venv broker isolation, exit-gates, security scan,
  green CI, and a real FXCM real-time stream. Maturity is tracked honestly (**M3 → M7**),
  not faked.

> **⚠️ Not financial advice.** Analytical / research tool only — SMC labels are technical
> markers, not signals. Trading carries substantial risk of loss. Full terms:
> **[DISCLAIMER.md](DISCLAIMER.md)** · [LICENSE](LICENSE).

---

> **English** · [Українська](README.uk.md)

A "data → analytics/SMC → UI → trading interaction" platform built on hard invariants,
with the **UnifiedDataStore (UDS)** as the single write-center.

## Meet Archi — the autonomous agent

Archi (Арчі) is not a strategy script. It's a single-personality AI agent built on the
Claude API that watches the market, forms its own thesis, and decides when — and whether —
to act. It lives in its own repository
([Std07-1/smc-trader-v3](https://github.com/Std07-1/smc-trader-v3)) and talks to this
platform over Redis, HTTP and WebSocket. The platform is the **body** (eyes, price,
structure, chart); Archi is the **mind**.

What sets it apart from a trading bot:

- **It decides; code only advises (invariant I7).** No hidden cooldown, forced model
  downgrade, or suppressed message may override Archi. If the system needs to constrain
  it, it must *say so, out loud, in Archi's context* — silent control is a constitutional
  violation. The only hard stops are the kill switch, a daily budget cap, and an
  anti-hallucination guard.
- **It schedules its own attention.** Instead of a fixed timer, Archi tells the platform
  *"wake me when price crosses 4199, when London opens, or after 4h of silence"*
  (wake conditions, ADR-034). The platform's WakeEngine checks those every 2 s for $0 —
  Archi only spends a model call when something it cares about actually happens.
- **It remembers.** Seven memory systems — conversation, agent journal, knowledge base,
  learning journal, per-symbol profile, forecasts, and live directives — plus an extended
  "thinking archive." An overnight curator (ADR-050) consolidates each day's experience
  into lessons, the way a disciplined trader reviews their own trades.
- **It runs on a budget.** Three model tiers (Haiku / Sonnet / Opus), prompt caching with
  identity as a stable prefix (ADR-061), and self-chosen cognitive depth keep it at a few
  dollars a *month*, not a few dollars a *call*.

Archi's live reasoning, straight off the chart above:

> *D1/H4 bearish cascade intact (BOS BEAR 4267.95). H1 CHoCH BULL @ 4112.67 = corrective
> phase. Iran peace deal confirmed = geo-premium exit = SHORT catalyst. Price closed 4210.
> Waiting for Monday's London killzone.* — watching · 8 conditions armed.

Neither side hands trading decisions to the other: the platform never fabricates signals,
and Archi is never silently steered.

## Architecture: A → C → B

| Layer | What | Where |
|---|---|---|
| **A** Broker + ingest | FXCM/Binance history + tick stream → writer processes | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI (ws) | read-only WS real-time renderer, same-origin, port 8000 | `ui_v4/` + `runtime/ws/ws_server.py` |
| **TUI** | aione-top: interactive TUI monitor for processes/pipeline | `aione_top/` |

## Core principles

- **SSOT**: one UDS, one `config.json`, one TF allowlist.
- **NoMix / Final > Preview**: `complete=true` always wins; two different final sources for one key are forbidden.
- **Degraded-but-loud**: no silent fallbacks — only `warnings[]` / `meta.degraded[]`.
- **Disk hot-path ban**: disk only for bootstrap/scrollback/recovery; interactive = RAM/Redis. Scrollback: max_steps=6, cooldown 0.5s.
- **Time geometry (dual convention)**: CandleBar/SSOT/API = end-exclusive (`close_time_ms = open + tf_s*1000`); Redis ALL = end-inclusive (`close_ms = open + tf_s*1000 - 1`). Conversion happens only at the Redis write boundary.

## Quickstart

### 1. Installation

```powershell
# Main venv (Python >=3.11) — platform, UDS, derive, SMC, UI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Broker venv (Python 3.7) — forexconnect SDK (.venv37/)
C:\Python37\python.exe -m venv .venv37
.venv37\Scripts\pip install -r requirements-broker.txt

# UI v4 frontend (Svelte 5 + Vite)
cd ui_v4
npm install
npm run build
cd ..

# Secrets
cp .env.example .env   # edit FXCM_USER / FXCM_PASS / FXCM_URL
```

### 2. Run

The platform is 6 independent processes. Each starts separately — so you can restart the
UI (ws_server) in 3 seconds without stopping the data pipeline.

| Process | `--mode` | What it does |
|--------|----------|-------------|
| M1 poller | `m1_poller` | broker_sidecar + m1_ingestion + derive cascade |
| Broker sidecar | `broker_sidecar` | FXCM M1 fetch + tick relay V2 (.venv37/) |
| Tick preview | `tick_preview` | ticks → preview bars |
| Binance ingest | `binance_ingest_worker` | BTCUSDT/ETHUSDT M1 + backfill |
| Binance ticks | `binance_tick_publisher` | Binance live ticks → Redis |
| WS server | `ws_server` | UI backend, port 8000 |

```bash
# Each in its own terminal:
python -m app.main --mode <mode> --stdio pipe

# Or all together (legacy, NOT recommended for dev):
python -m app.main --mode all --stdio pipe
```

Full cheat-sheet with every command (local + VPS): **[docs/runbooks/commands.md](docs/runbooks/commands.md)**

> **Dual-venv (ADR-0016)**: the supervisor automatically uses `.venv37/` for broker_sidecar
> (M1 fetch + tick relay V2) and `.venv/` for everything else.
> `tick_publisher_fxcm` is permanently stopped (FXCM dual-session conflict).
> Per-mode PID locks: `logs/supervisor_{mode}.pid` — allow parallel startup.

## Quality gates

```bash
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

If gates FAIL → a formal **NO-GO** until the next PATCH.

## Automation baseline

- GitHub Actions: Python smoke tests + UI v4 typecheck/build on every push and pull request
- Dependabot: weekly dependency updates for `pip` and `npm`
- Goal: close a baseline enforcement loop for governance, SSOT smoke, and frontend compile health

## Documentation (SSOT)

Full documentation: **[docs/index.md](docs/index.md)** — single entry point.

| Document | Description |
|---|---|
| [docs/index.md](docs/index.md) | Navigation across all documentation |
| [docs/system_current_overview.md](docs/system_current_overview.md) | Architecture, processes, diagrams, invariants |
| [docs/contracts.md](docs/contracts.md) | Contract registry (bar_v1, window_v1, updates_v1, tick_v1) |
| [docs/ui_api.md](docs/ui_api.md) | HTTP API reference (endpoints, guards, TTL) |
| [docs/config_reference.md](docs/config_reference.md) | config.json field reference |
| [docs/runbooks/production.md](docs/runbooks/production.md) | Production runbook (startup, incidents, recovery) |
| [docs/adr/index.md](docs/adr/index.md) | Registry of all ADRs (canonical archive) |
| [docs/adr/0001-unified-data-store.md](docs/adr/0001-unified-data-store.md) | ADR: UDS as the single waist |
| [docs/adr/0002-derive-chain-from-m1.md](docs/adr/0002-derive-chain-from-m1.md) | ADR: DeriveChain M1→M3→M5→H4 (Phase 0 complete) |
| [docs/adr/0003-cold-start-hardening.md](docs/adr/0003-cold-start-hardening.md) | ADR: Cold start hardening (S1 ✅, S2 ✅, S3-S4 pending) |

```text
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: INGEST (simple, focused, single-responsibility)   │
│                                                             │
│  ┌──────────────┐    ┌──────────────────┐                   │
│  │ m1_poller    │    │ binance_ingest   │                   │
│  │ M1 from FXCM │    │ M1 from Binance  │                   │
│  └──────┬───────┘    └──────┬───────────┘                   │
│         │ commit M1         │ commit M1                     │
│         ▼                   ▼                               │
│  ┌──────────────────────────────────┐                       │
│  │            UDS (SSOT)            │                       │
│  └──────────────┬───────────────────┘                       │
│                 │ updates bus (Redis pub/sub)               │
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
│  │    M1 → M3 (3×M1)                               │        │
│  │    M1 → M5 (5×M1)                               │        │
│  │      M5 → M15 (3×M5)                            │        │
│  │        M15 → M30 (2×M15)                        │        │
│  │          M30 → H1 (2×M30)                       │        │
│  │            H1 → H4 (4×H1, calendar+TV anchor)   │        │
│  │    M1 → D1 (1440×M1, anchor 22:00 UTC)          │        │
│  │                                                 │        │
│  │  4 symbols (XAU/USD, XAG/USD, BTCUSDT, ETHUSDT) │        │
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
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3: UI v4 (WS real-time, read-only, ZERO domain logic) │
│  WS full/delta/scrollback → UDS via ws_server.py             │
│  Svelte 5 + LWC 5 + TypeScript, ~40 files ~10000 LOC          │
│  Port 8000, same-origin, config-gated                        │
│  Chart parity DONE, audit T1-T10 COMPLETE                    │
└──────────────────────────────────────────────────────────────┘
```

## Project history

In active development since **14 October 2024** — it began as the AiOne-t crypto
screener, evolved through SMC analytics engines (AiOne_t v2/v3, smc_v1) and FXCM
connectors, and became this real-time platform and the Archi agent (Archi's first
boot: 30 March 2026).

## Contact

Channel (live SMC analysis from Archi): [@smc_v3](https://t.me/smc_v3) · GitHub: [@Std07-1](https://github.com/Std07-1)

## License

See [LICENSE](LICENSE) (MIT).

### Third-party dependencies

A list of third-party dependencies and their licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

**FXCM ForexConnect SDK**: this repository does NOT contain or distribute the ForexConnect
SDK. To use the SDK, each user must accept the FXCM EULA themselves and hold an active FXCM
account. Details: [docs/compliance/fxcm-sdk-license-review.md](docs/compliance/fxcm-sdk-license-review.md)
