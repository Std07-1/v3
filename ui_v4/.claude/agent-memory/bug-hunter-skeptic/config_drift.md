---
name: Config Key Drift — phantom keys
description: Config keys read by code via cfg.get() but absent from config.json. Creates operator confusion and undocumented defaults.
type: project
---

Known phantom config keys (as of 2026-03-24 audit):

1. `day_anchor_offset_s_alt2` — read in uds.py:2376, ssot_jsonl.py, fxcm/provider.py, 4 repair tools. NOT in config.json.
2. `max_display_fractals` — read in config.py:214, used in engine.py:1135. NOT in config.json:smc.display.
3. `updates.retain` — read in uds.py:2288-2292. No "updates" section in config.json.

Also: `gate_preview_not_on_disk.py:8` redefines DEFAULT_PREVIEW_TF_ALLOWLIST = {60, 180} instead of importing from core.config_loader where it is {60, 180, 300, 900, 1800, 3600, 14400}. This is a broken guard.

**Why:** No automated coverage test exists for config keys.

**How to apply:** When reviewing config-related changes, verify every cfg.get("X") has a corresponding key in config.json. Propose exit gate for config key coverage.
