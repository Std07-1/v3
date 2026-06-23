# Contributing to Trading Platform v3

Thanks for your interest. This is an opinionated, invariant-driven platform — small,
sharp contributions that respect the architecture are very welcome; large unscoped
rewrites are not.

> **Status**: actively developed, AI-native. Much of the code is written by AI agents
> under strict governance (see [`.github/`](.github/)). The same rules apply to every
> contributor, human or agent.

## Read first

Before changing anything, read — in order:

1. [`.github/copilot-instructions.md`](.github/copilot-instructions.md) — the SSOT for all
   rules: invariants `I0–I7`, severities `S0–S6`, the ADR workflow, evidence markers.
2. [`AGENTS.md`](AGENTS.md) — project structure, build/run, dual-venv, test inventory.
3. [`docs/adr/index.md`](docs/adr/index.md) — every non-trivial decision already made.
   Don't re-invent what's already decided; supersede with a new ADR instead.

## The non-negotiable invariants

A change that breaks any of these will be rejected:

- **I1 — UDS is the single write-center.** All OHLCV writes/reads go through
  `runtime/store/uds.py`. No parallel writers, no direct Redis writes from the UI.
- **I3 — Final > Preview.** `complete=true` always wins; one key has exactly one final source.
- **I7 — Autonomy-first (Archi).** Code advises; the agent decides. No silent cooldown,
  forced downgrade, or suppressed message.
- **Degraded-but-loud.** No silent fallbacks — surface `warnings[]` / `meta.degraded[]`.
  `except: pass` is forbidden.
- **Frontend is a dumb renderer.** No domain recompute (grade, bias, phase) in the UI.

## Workflow: RECON → DESIGN → CUT

1. **RECON** — root cause with evidence markers (`[VERIFIED path:line]`,
   `[ASSUMED — verify: …]`). Never invent line numbers.
2. **DESIGN** — one fix point, ≥2 alternatives, invariant check, blast radius. A
   non-trivial change needs an **ADR** first (`docs/adr/NNNN-*.md`).
3. **CUT** — min-diff (≤150 LOC, ≤1 file per patch; split larger into slices), ≥1 test,
   ≥1 safety rail, and a `changelog.jsonl` entry for any `S0`/`S1`.

## Before you open a PR

```bash
# Tests for the area you touched
python -m pytest tests/ -q

# Quality gates — FAIL = NO-GO
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json

# Frontend (if you touched ui_v4/)
cd ui_v4 && npm run typecheck && npm run build
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs Python smoke tests,
governance gates, a security baseline (bandit + pip-audit), and the UI typecheck/build on
every push and PR. Keep it green.

## Commit & PR style

- Conventional, scoped, intent-first:
  `fix(smc): zone mitigation skips impulse bars (ADR-0029 §4.3)` — not `fix bug`.
- One PR = one goal. Describe the invariant you protected and the evidence you gathered.
- Be honest in claims — `[ASSUMED]` beats a fake `path:42`.

## Reporting issues

- **Bugs / features** — open a GitHub issue with repro steps and evidence.
- **Security** — do **not** open a public issue; follow [`SECURITY.md`](SECURITY.md).
- All interaction is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).

## A note on the FXCM SDK

This repo does **not** contain the ForexConnect SDK. To run the broker path you must
accept the FXCM EULA yourself and hold an active FXCM account. See
[`docs/compliance/fxcm-sdk-license-review.md`](docs/compliance/fxcm-sdk-license-review.md).
