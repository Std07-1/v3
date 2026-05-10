---
name: TF Label Map SSOT Violation
description: TF label mapping (60→M1, 86400→D1) is defined in 6+ independent sites across Python and TypeScript. Recurring C1 defect.
type: project
---

TF label maps are duplicated in these sites (as of 2026-03-24 audit):

1. `core/smc/narrative.py:44` — _TF_LABELS (8 entries, int→str)
2. `core/smc/shell_composer.py:52` — _TF_LABELS (4 entries only: D1,H4,H1,M15)
3. `runtime/ws/ws_server.py:135` — _TF_CANONICAL_LABELS (8 entries, str→int)
4. `tools/mcp/platform_server.py:41` — TF_LABELS (8 entries, int→str)
5. `ui_v4/src/chart/engine.ts:65` — TF_TO_S (8 entries)
6. `ui_v4/src/chart/overlay/OverlayRenderer.ts:113` — _TF_NAMES (8 entries)
7. `ui_v4/src/layout/BiasBanner.svelte:8` — TF_LABELS (4 entries, bias subset)
8. `ui_v4/src/layout/ChartHud.svelte:47` — BIAS_TF_LABELS (4 entries, identical to BiasBanner)

**Why:** No canonical single source for TF label mapping exists in `core/`.

**How to apply:** When reviewing changes that add/modify TF support, check ALL 8 sites. Propose RFC to centralize into core/config_loader.py or core/buckets.py.
