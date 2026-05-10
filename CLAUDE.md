# CLAUDE.md — Trading Platform v3

Entry point for AI assistants (Claude Code, etc.) working on this repository. Gives you the bare minimum to orient yourself, then points at the SSOT docs for everything else.

> **Conflict rule:** if anything here contradicts `.github/copilot-instructions.md`, the `.github` file wins. This file is a front door, not a rulebook.

---

## What this repo is

**Trading Platform v3** — a production-grade three-layer platform for Smart Money Concepts (SMC) market analysis on FX and crypto:

- **Layer A — Broker / Ingest:** FXCM ForexConnect + Binance Futures M1 ingestion (no HTF contracts from brokers).
- **Layer C — Core Store:** `UnifiedDataStore` (UDS) is the single source of truth for OHLCV data across disk (JSONL), Redis, and in-process RAM (ADR-0001, invariant I1).
- **Layer B — UI / SMC Overlay:** WebSocket-driven Svelte 5 + lightweight-charts UI rendering deterministic SMC zones, order blocks, FVGs, liquidity, and multi-timeframe narrative (ADR-0024).

The platform is **isolated from `smc-trader-v3`** (the Archi agent repo): integration is one-way, over HTTP / WS / Redis only.

## Read first (in order)

1. **`.github/copilot-instructions.md`** — SSOT for AI agent rules: roles, invariants I0–I7, principles F1–F9, stop-rules X1–X39, role-routing, ADR registry, enforcement.
2. **`AGENTS.md`** — condensed mirror of the above (33 KB), faster to skim for first orientation.
3. **`docs/system_current_overview.md`** — architecture + A→C→B dataflow diagrams.
4. **`docs/adr/index.md`** — registry of 54+ ADRs (decisions you must not silently revisit).
5. **`docs/contracts.md`** — wire-format SSOT: `bar_v1`, `updates_v1`, `window_v1`, `tick_v1`.
6. **`config.json`** — runtime policy SSOT: symbols, TF allowlist, calendars, Redis spec, SMC params, broker Python paths.
7. **`.env.example`** — required env vars. Copy to `.env`. **Never commit `.env`.**

## Tech stack

| Layer | Tech |
|---|---|
| Main Python venv (`.venv/`) | Python ≥3.11; numpy ≥1.26, pandas ≥2.1, aiohttp ≥3.9, FastAPI ≥0.110 (ADR-0058 sidecar), `python-binance` ≥1.0.36 (ADR-0037), redis 5 |
| Broker venv (`.venv37/`) | Python 3.7 — **only** for `forexconnect 1.6.43` (ADR-0016 dual-venv rail) |
| UI (`ui_v4/`) | Svelte 5 (runes), Vite 6, TypeScript 6, lightweight-charts 5.1 (LWC) |
| Infra | Redis db=1 ns `v3_local` (pub/sub, snapshots, IPC); JSONL SSOT in `data_v3/{symbol}/tf_{tf_s}/`; supervisor via Python multiprocessing |
| Calendars | FX 24/5 UTC, US CFD 22–23, EU CFD 21–07, HK CFD 01–15, Crypto 24/7 |

## Directory layout

| Top-level | Purpose |
|---|---|
| `app/` | Process supervisor + lifecycle (`app/main.py` — 10 modes, PID locking, restart backoff) |
| `core/` | **Pure logic, no I/O** (invariant I0). `derive.py`, `model/bars.py`, `contracts/`, `buckets.py` |
| `core/smc/` | Pure SMC engine — swings, OB, FVG, confluence, sessions, narrative, TDA cascade (37 .py files; ADR-0024) |
| `runtime/` | **I/O layer**: ingest, UDS, WS server, SMC runner, observability (~55 .py files) |
| `ui_v4/` | WebSocket UI frontend (Svelte 5 + LWC) |
| `aione_top/` | TUI process monitor (`python -m aione_top`) |
| `tools/` | Isolated utilities, exit gates, repair, diag (**not** on hot path) |
| `tests/` | Pytest suite (~70 files) |
| `docs/` | SSOT docs: ARCHITECTURE, contracts, ADRs (54+), system overview |
| `cowork/` | Experimental SMC analysis API + event watcher (not core platform) |
| `.github/` | Copilot instructions, role specs, domain instructions, workflows |

## Setup & run

```bash
# Main venv (Python ≥3.11)
python -m venv .venv
. .venv/bin/activate           # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Broker venv (Python 3.7 — forexconnect only; ADR-0016)
python3.7 -m venv .venv37
.venv37/bin/pip install -r requirements-broker.txt

# Secrets
cp .env.example .env           # fill FXCM_*, BINANCE_*, TELEGRAM_*, ANTHROPIC_API_KEY, etc.

# UI build (optional, for WS UI)
( cd ui_v4 && npm install && npm run build )
```

### Supervisor modes (`python -m app.main --mode <name>`)

| Mode | Criticality | What it does |
|---|---|---|
| `all` | — | All processes in one supervisor (development) |
| `m1_poller` | critical | FXCM M1 polling driver |
| `broker_sidecar` | critical | FXCM ForexConnect fetch + tick relay v2 (runs in `.venv37/`) |
| `m1_ingestion_worker` | critical | Platform M1 ingestion (runs in `.venv/`) |
| `binance_ingest_worker` | critical | Binance Futures M1 ingestion (ADR-0037) |
| `binance_tick_publisher` | non-critical | Binance tick publisher |
| `tick_preview` | non-critical | Preview bars on tick stream |
| `tick_publisher` | non-critical | FXCM tick relay |
| `ws_server` | essential | UI backend on port 8000 |
| `replay` | critical | M1 replay → DeriveEngine → UDS → UI (ADR-0017) |

```bash
python -m app.main --mode all          # one-shot dev startup
python -m aione_top                    # TUI process monitor
curl http://127.0.0.1:8000/api/status  # health
```

## Test & verify

```bash
python -m pytest tests/ -v                                          # full suite (~70 files)
python -m pytest tests/test_smc_*.py -v                             # SMC core
python -m pytest tests/test_derive_*.py -v                          # derive cascade
python -m pytest tests/test_uds_*.py -v                             # UDS / SSOT
python -m pytest tests/test_s*_*.py -v                              # invariants S1–S6
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json   # AST quality gates

# UI
( cd ui_v4 && npm run typecheck && npm run build && npm run test )
```

No top-level `ruff`/`black`/`mypy` configs: lint is enforced by the exit-gate AST checks under `tools/exit_gates/`.

## Conventions (compact)

**Principles F1–F9** (full text in `.github/copilot-instructions.md`):
F1 SSOT • F2 Final > Preview • F3 One update-stream • F4 Degraded-but-loud • F5 Dependency rule (`core/` → `runtime/` → `ui/`) • F6 Patch-cycle (one invariant per patch) • F7 ADR-driven • F8 UDS = narrow waist • F9 Craftsmanship-first.

**Hard invariants I0–I7:**
- **I0** `core/` imports nothing from `runtime/`, `ui/`, `tools/`.
- **I1** All OHLCV reads/writes go through UDS; `/api/updates` = Redis bus only.
- **I2** **Time geometry (DUAL CONVENTION) — easiest bug to introduce:**
  - CandleBar / SSOT JSONL / HTTP API: `close_time_ms = open + tf_s * 1000` → **end-exclusive**
  - Redis keys: `close_ms = open + tf_s * 1000 - 1` → **end-inclusive**
  - Conversion happens **only** at the Redis write boundary.
- **I3** `complete=true` always wins (NoMix).
- **I4** UI updates only via `/api/updates`.
- **I5** No silent fallback; degradation = `warnings[]` + log.
- **I6** Disk hot-path ban (bootstrap/scrollback only; `max_steps=6`, cooldown 0.5s).
- **I7** Autonomy-First (Archi-specific; cross-repo).

**SMC invariants S0–S6** (ADR-0024): pure logic / read-only overlay / deterministic / deterministic zone IDs (`{kind}_{symbol}_{tf_s}_{anchor_ms}`) / perf budget `on_bar() < max_compute_ms` / config SSOT / wire format `core/smc/types.py` ↔ `ui_v4/src/types.ts`.

**Patch modes:** `DISCOVERY` (default when uncertain — facts + failure model + gaps) → `PATCH` (≤150 LOC, 1 file, ADR reference) → `ADR` (new doc in `docs/adr/`) → `BUILD` (new subsystem: types → logic → tests → integration → UI wiring).

**Stop-rules to remember (full X1–X39 in copilot-instructions.md):**
- **X13** Never use `bar.l` — wire-dict key `l` ≠ dataclass field `.low`.
- **X28** Frontend never re-derives backend SSOT (P/D split-brain precedent).
- **X33** No silent file truncation — files >1500 LOC require AST + `wc -l` verification (ADR-0016 App. C).
- **X35** Code duplicated in ≥3 places = SSOT violation; extract to shared helper.
- **X39** No maturity regression — current rung is M3, north star is M7.

## Critical files (do not break)

| File | Why it's load-bearing |
|---|---|
| `core/model/bars.py` | `CandleBar` SSOT; `.low/.high` vs wire-dict `l/h` is X13 |
| `core/derive.py` | `DERIVE_CHAIN` M1→M3→M5→M15→M30→H1→H4 + D1 (ADR-0002, ADR-0023) |
| `core/buckets.py` | `resolve_anchor_offset_ms()` — centralised anchor math (X35) |
| `runtime/store/uds.py` | UDS narrow waist (I1); Redis conversion boundary (I2) lives here |
| `core/smc/engine.py` | `SmcEngine.on_bar()` orchestrator, zone lifecycle |
| `core/smc/types.py` ↔ `ui_v4/src/types.ts` | S6 wire-format contract — change both or neither |
| `runtime/smc/smc_runner.py` | Warmup + on_bar wiring inside ws_server process |
| `ui_v4/src/chart/OverlayRenderer.ts` | Rendering rules Z1–Z10, L1–L6 |
| `config.json` | Policy SSOT — every limit and threshold (D1, K5) |
| `.env` | Secrets — **gitignored, never commit** |

## Recent direction

Headline categories from ADRs (see `docs/adr/index.md` for the registry):

- **ADR-0058** Public read-only API (FastAPI sidecar for external consumers).
- **ADR-0049** Wake Engine IPC (trader-v3 ↔ platform event bus).
- **ADR-0040** TDA 4-stage daily signal cascade (D1 macro → H4 confirm → session narrative → M15 FVG entry).
- **ADR-0037** Binance Futures live ingestion (24/7 crypto).
- **ADR-0024** SMC Engine architecture + P-slices.
- **ADR-0016** Dual-venv broker rail (Python 3.7 forexconnect + Python 3.11 platform).
- Ongoing maturity push M3 → M7 (see `docs/SYSTEM_MATURITY_LADDER.md`).

## What an AI assistant must not do

- Modify `core/` to import from `runtime/`, `ui_v4/`, or `tools/` (I0).
- Write OHLCV anywhere but through UDS (I1, F8).
- Change `core/smc/types.py` without mirroring `ui_v4/src/types.ts` (S6).
- Add `bar.l` / `bar.h` / `bar.o` / `bar.c` access (X13).
- Silently truncate files >1500 LOC (X33).
- Lower maturity below M3 (X39).
- Commit `.env`, contents of `data_v3/`, or any FXCM/Binance credentials.
- Patch invariants, contracts, or protocol without an ADR (F7).
