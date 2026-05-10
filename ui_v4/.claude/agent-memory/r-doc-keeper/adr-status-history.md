---
name: adr-status-history
description: ADR status changes tracked across sync sessions
type: project
---

## ADR Status History

| ADR | Status at 2026-03-22 sync | Status at 2026-03-24 sync | Evidence |
|-----|--------------------------|--------------------------|----------|
| 0041 | Accepted (P1–P9 implemented) in index | Implemented (P1–P9) | changelog 20260322-003/005, git 5867912 |
| 0042 | Implemented | Implemented | changelog (no change) |
| 0043 | — (not in index) | Implemented | changelog 20260324-001 |
| 0044 | — (not in index, file existed) | Proposed | file 0044-htf-live-preview.md — patch plan only |

## Notes
- ADR-0041 body file still said "P1-P4 implemented, P5-P9 next" as of 2026-03-24 — corrected to "Implemented (P1-P9)".
- ADR-0044 is a Patch Plan document (Variant A), not yet implemented. Status = Proposed.
- "Наступний номер" in adr/index.md: 0044 → updated to 0045 after adding ADR-0044 entry.
