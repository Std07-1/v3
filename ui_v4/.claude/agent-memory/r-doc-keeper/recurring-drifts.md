---
name: recurring-drifts
description: Patterns that drift repeatedly across sync cycles — check these first in every audit
type: feedback
---

## High-Recurrence Drift Patterns

### 1. docs/index.md ADR count + missing entries
**Pattern**: After every batch of ADR additions, `docs/index.md` §1 lags behind.
**Why**: ADR author updates `adr/index.md` but forgets `docs/index.md` §1 navigation table.
**Check**: `grep "35 ADR\|43 ADR\|44 ADR" docs/index.md` + compare ADR list with `adr/index.md`.
**How to apply**: Always sync `docs/index.md` §1 ADR table with `docs/adr/index.md` content.

### 2. ADR link naming in docs/index.md (ADR- prefix vs numeric)
**Pattern**: Links in docs/index.md use `ADR-NNNN-name.md` but real files are `NNNN-name.md`.
**Why**: Copy-paste from ADR body headers vs actual filenames.
**Check**: Glob for `docs/adr/ADR-*.md` — should return empty (confirmed 2026-03-24).
**How to apply**: Always use lowercase numeric prefix: `adr/0028-v2-elimination-engine.md` not `adr/ADR-0028-v2-elimination-engine.md`.

### 3. Test count in system_current_overview.md and AGENTS.md §1.1
**Pattern**: Test count (778+, 798+) becomes stale after each pytest test addition.
**Why**: Numeric counts require manual update; no automation.
**Check**: `pytest --collect-only -q 2>&1 | tail -3` — get current count.
**How to apply**: Update both SMC Overlay SSOT plane box AND `tests/` tree entry.

### 4. ADR-0041 body status
**Pattern**: ADR body file says "P1–P4 implemented, P5–P9 next" while implementation is complete.
**Why**: Patch-master updates code but forgets to update ADR body status line.
**How to apply**: Check ADR body `Статус:` line against changelog for completion evidence.

### 5. tools/repair/ new files missing from tree docs
**Pattern**: New repair utilities appear in `tools/repair/` but are not added to annotated trees.
**Why**: Tools are added quickly without doc updates.
**Check**: `Glob tools/repair/*.py` → compare with AGENTS.md and system_current_overview.md.

### 6. New broker integrations missing from tree
**Pattern**: New broker (Binance, ADR-0037) added to `runtime/ingest/broker/` but not in annotated trees.
**Why**: New broker is a significant architectural change but tree update is easy to miss.

### 7. Dead link: ui_v4_integration.md
**Pattern**: Several docs/index.md entries referenced `ui_v4_integration.md` which never existed.
**Resolution (2026-03-24)**: Replaced with `../ui_v4/README_DEV.md` which exists.
**How to apply**: If `ui_v4_integration.md` appears anywhere, replace with `../ui_v4/README_DEV.md`.

### 8. contracts.md delta frame format lags ADR updates
**Pattern**: SmcDeltaWire section in contracts.md doesn't reflect ADR-0042 "thick delta" additions.
**Why**: Wire format changes are code-side; contracts.md needs manual sync.
**Check**: Compare contracts.md SmcDeltaWire with ADR-0042 §3 Alternative A description.
