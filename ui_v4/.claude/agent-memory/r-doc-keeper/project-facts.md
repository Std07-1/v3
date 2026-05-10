---
name: project-facts
description: Verified canonical values for Trading Platform v3 — ports, test counts, ADR ranges, key file locations
type: reference
---

## Verified Facts (as of 2026-03-24)

### Test Count
- Python pytest: **798 tests** (verified: pytest --collect-only → 798 tests collected)
- Vitest (ui_v4): **28 tests** (verified: changelog 20260324-001)
- Previous sync (2026-03-22): 778 python tests

### ADR Registry
- Total ADR files in docs/adr/: 44 (0001–0044, plus variant files like 0024a/b/c, 0013b)
- Next ADR number: **0045**
- ADR-0044 status: Proposed (Patch Plan only, not yet implemented)
- ADR-0043 status: Implemented (2026-03-24)
- ADR-0042 status: Implemented (2026-03-22)
- ADR-0041 status: Implemented (P1–P9, changelog 20260322-003/005)

### Ports
- HTTP UI (ui_chart_v3): **8089** (localhost only)
- WS UI (ui_v4): **8000** (localhost only)
- Redis: **6379**, db=1

### Key Config Values
- tf_allowlist_s: [60, 180, 300, 900, 1800, 3600, 14400, 86400] (8 items)
- D1 anchor: 79200s (22:00 UTC)
- H4 anchor: 82800s

### ADR Naming Convention (CRITICAL)
- Canonical file names: lowercase numeric prefix, e.g. `0028-v2-elimination-engine.md`
- docs/index.md had dead links with `ADR-` prefix (ADR-0028-v2...) — corrected 2026-03-24
- Always use lowercase `NN-name.md` format in links, not `ADR-NN-name.md`

### Key New Files (added since last sync, 2026-03-22→2026-03-24)
- `tools/repair/repair_m1_gaps.py` — M1 gap repair utility
- `runtime/ingest/broker/binance/provider.py` — Binance Futures provider (ADR-0037)
- `runtime/ingest/binance_ingest_worker.py` — Binance M1 ingest + backward crawl
- `docs/adr/0044-htf-live-preview.md` — HTF Live Preview patch plan (Proposed)
- `.claude/agents/smc-mentor-darktrader.md` — new agent file

### Broker Architecture
- FXCM: `.venv37/` (Python 3.7), `broker/fxcm/provider.py`
- Binance Futures: main venv, `broker/binance/provider.py` (ADR-0037, 24/7, anchor=0)
- Both brokers: M1 → DeriveEngine cascade → UDS
