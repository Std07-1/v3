# ADR-0070: Новий кут (TR area) — Canonical Contract

## Metadata

| Field          | Value                                                                |
| -------------- | -------------------------------------------------------------------- |
| ID             | ADR-0070                                                              |
| Status         | ACCEPTED                                                              |
| Date           | 2026-05-09                                                            |
| Authors        | Станіслав                                                             |
| Supersedes     | —                                                                     |
| Builds on      | ADR-0065 rev 2 (CR-2.5 layout); ADR-0069 rev 2 (NP state machine); ADR-0049 (Архi presence + thesis); ADR-0066 rev 5 (tokens) |
| Locks          | The four user-facing surfaces in the top-right corner of the chart pane: CommandRail, NarrativePanel compact pill, NarrativePanel expanded body, and the boundary against системний наратив v3. |
| Affects layers | `ui_v4/src/lib/agentState.ts`, `ui_v4/src/layout/NarrativePanel.svelte`, `ui_v4/src/layout/CommandRail.svelte`, `ui_v4/src/App.svelte`, `runtime/ws/ws_server.py` (frame.atr field) |

---

## Quality Axes

- **Ambition target**: R3 — formal scope contract for Арчi-surface vs системний наратив; X28 enforcement codified at chrome boundary
- **Maturity impact**: M3 → M4 — TR corner is now invariant-protected; future agents have a lock-in spec, not interpretive freedom

---

## Context

The top-right corner of the chart pane (новий кут) hosts three surfaces
that interact: `CommandRail` (peripheral chrome — ATR, countdown, UTC),
the NarrativePanel **compact pill** (always-visible Арчi indicator),
and the NarrativePanel **expanded body** (Арчi detail card on click).

During the 2026-05-09 session, multiple session-spanning bugs and
violations surfaced when these surfaces were modified independently:

1. **CommandRail violated X28** — frontend recomputed ATR locally from raw
   candles, and invented an RV (relative volume) concept that has no backend
   equivalent. Domain values must come from backend SSOT.
2. **NP compact pill leaked системний наратив** — pulled `narrative.headline`
   directly + synthesized text from `narrative.scenarios[0]`, blurring the
   line between Арчi-surface and the v3 system narrative engine.
3. **NP expanded body duplicated системний наратив** — rendered
   `bias_summary`, `scenarios[]`, `warnings`, `fvg_context`, `next_area` on
   top of an Арчi area. Same content also lived in ChartHud `.narr-tooltip`.
4. **WakeEngine status was misread as bot liveness** — `archi_presence.status`
   reflects "wake conditions armed" (platform-side), NOT "bot process alive".
   Killed bot → status still ships "watching" → UI lied "Арчі спостерігає".
5. **Wording drift** — name spelled "Архі" inconsistently with project
   canonical "Арчі"; status mappings paraphrased to "чекає умов" instead of
   the literal "спить" matching backend semantics.
6. **Decorative icons misled trader** — `↻` next to TF label looked clickable
   (refresh-button affordance) but was read-only countdown decoration.
7. **Redundant chrome badges** — NP pill stamped `[STAY OUT]` / `[CLOSED]` /
   `[AWAITING]` over states whose presence was already shown by ChartHud's
   shell-stage pill.

This ADR locks down the resolved contract so future agents reading the
code don't re-derive past mistakes.

---

## Decision

The TR corner is split into exactly three surfaces with strict, locked,
non-overlapping contracts.

### Tier 1 · CommandRail — peripheral market context

**Slot composition (left → right)**: `[ATR(14) of current TF] · [TF label + countdown to bar close] · [UTC clock]`.

**Data sources (X28-strict)**:

| Slot       | Source                                                                                          | Formula in UI |
| ---------- | ----------------------------------------------------------------------------------------------- | ------------- |
| ATR        | `frame.atr` shipped by backend `ws_server._build_full_frame` + delta path. Source: `_smc_runner._engine.get_atr(symbol, tf_s)`. Same value as REST `/api/context.atr`. | none — formatter only (`fmtAtr` adaptive precision) |
| Countdown  | Wallclock + bucket math: `Math.floor((nowMs - anchorMs) / tfMs) * tfMs + anchorMs + tfMs - nowMs`. Anchor map mirrors `core/utils/buckets.bucket_start_ms` (D1 = 79200000, H4 = 82800000, others = 0). | display arithmetic only — NOT domain compute |
| UTC clock  | `clockNow` $state ticking every 1s in App.svelte                                                | format `HH:MM` |

**Forbidden**:

- Any frontend computation of ATR / Wilder TR / SMA / EMA / RV / any
  domain indicator from raw `candles[]`. X28 violation. If a new indicator
  is needed, backend ships it as a frame field; this ADR will be amended.
- RV (relative volume) cell — explicitly removed from CommandRail. Has no
  backend equivalent. Future re-introduction requires backend slice + ADR.
- Decorative icons that look interactive (`↻` removed). If a future cell
  needs an icon, it MUST be either truly clickable OR semantically
  obvious as decorative (e.g., `⏱` for timer).

**TF prop convention**: CommandRail receives `currentTf: string` as
**label form** (`"M15"`, `"H1"`, …), NOT seconds string. Conversion to
seconds happens via `_LABEL_TO_S` inverse map inside CommandRail. App.svelte
passes `hudTf` directly. This was a latent bug (parseInt("M15") = NaN
→ countdown silently always null) — locked here as canonical.

### Tier 2 · NarrativePanel compact pill — Арчi voice (one line)

**Position**: leftmost item in `.top-right-bar` flex row, before CommandRail.

**Text source priority (compactPillText in `lib/agentState.ts`)** — strict
priority chain, no synthesis from системний наратив:

1. **Bot alive AND has fresh thesis** (`archi_thesis` exists AND
   `archi_thesis.freshness !== 'stale'`): pill text = `archi_thesis.thesis`
   verbatim. Truncated by CSS ellipsis if exceeds `max-width: 220px`.
2. **Bot offline** (no `archi_thesis` OR `freshness === 'stale'`): pill
   text = `"Арчі вимкнений · {silence_h}h"` (silence appended only when
   `presence.silence_h > 0`).
3. **Bot alive, no thesis yet** (cold start, brand-new symbol): pill text =
   `"Арчі {status_uk} · {focus}"` using the WakeEngine status mapping
   (see §Wording).
4. **Nothing Арчi-related shipped at all**: pill text = `""` (caller
   may collapse the pill entirely; behavior left to NP component).

**Forbidden in pill text**:

- `narrative.headline` — that's системний наратив scope.
- `narrative.scenarios[]` — fields like `entry_desc`, `trigger_desc`,
  `target_desc`, `invalidation`. All системний наратив.
- `narrative.bias_summary`, `narrative.warnings`, `narrative.fvg_context`,
  `narrative.next_area` — same reason.
- "Action-first" synthesis from scenarios (e.g., `"▲ ОЧІКУЄ {entry_desc}"`).
  Removed in this revision. Was an X28-spirit violation (frontend stitching
  domain content) plus a scope violation (системний наратив content in
  Арчi-surface).

**Forbidden badges in pill**:

- `[STAY OUT]`, `[CLOSED]`, `[AWAITING]` — duplicate of ChartHud
  shell-stage pill that already shows them. `badgeLabel()` returns `""`
  for these three states; the pill template hides the badge span when
  label is empty.
- `↑trend` / `↓trend` `phase-badge` — bias pills in the HUD sub-row
  (left side) already carry HTF direction. Removed.

**Kept badges** (states that add information chrome doesn't otherwise show):
`WATCHING`, `BIAS`, `PREPARE`, `READY`, `TRIGGERED`. Tier-tinted via
`.state-{name}` classes (Tier 4 amendment colors).

### Tier 3 · NarrativePanel expanded body — Арчi detail card

**Render scope (PURE Арчi)**: ONLY two sections, in this order:

1. `archi_thesis` block — when `narrative.archi_thesis` is present:
   - Header: `🧠 Арчі` + `conviction-{level}` chip + `freshness-{level}` chip
   - Thesis text
   - `🎯 {key_level}` + `✕ {invalidation}` if key_level exists
2. `archi_presence` row — when `narrative.archi_presence` is present:
   - Status dot `presence-{status}` + status word
   - Silence hours hint when `silence_h > 0`
   - "X conditions" hint when `conditions > 0`

**Forbidden in expanded body**:

- `<div class="row bias-row">{narrative.bias_summary}</div>` — removed.
- `{#each narrative.scenarios as sc} ... {/each}` block (sc-header,
  sc-trigger, sc-target, sc-invalidation) — removed.
- `{#if narrative.scenarios.length === 0}` fallback row — removed.
- `{#if narrative.fvg_context}` `<div class="row fvg-ctx">` — removed.
- `{#if narrative.warnings.length > 0}` `<div class="row warnings">` — removed.
- The dead `getTriggerClass()` helper — removed.

`.bias-row` / `.scenario` / `.fvg-ctx` / `.warnings` CSS rules left as
dead style (no selector hits) — harmless. May be cleaned in a future
сlean-up patch.

### Tier 4 · Click-outside-to-collapse

When NP `mode === 'expanded'`, a document-level `click` listener (added
on next event loop tick to avoid catching the click that opened it)
collapses NP back to compact when the click target is NOT inside
`.narrative-panel`.

**Inside-NP clicks** are blocked by the existing
`onclick={(e) => e.stopPropagation()}` on the root `<div class="narrative-panel">` — they never reach the document listener.

This means: clicking the chart canvas (or HUD, or anywhere outside NP)
collapses the expanded body. The compact pill remains visible.

The override + sessionStorage logic from ADR-0069 §B.2 is preserved:
collapse via outside-click sets `override = 'compact'` + persists to
`sessionStorage` keyed by `(symbol, tf)`. Tier escalation
(`watching → ready`) still resets the override per ADR-0069 §B.2.

### Liveness detection — canonical rule

`archi_presence.status` is a **WakeEngine state machine output** (per
`runtime/smc/wake_engine.py:319-320`):

```python
self._presence[symbol] = PresenceStatus(
    status="watching" if all_conditions else "sleeping",
    ...
)
```

It says "are wake conditions armed". It does **NOT** say "is the Арчi
bot process alive". A killed bot → backend still ships
`status="watching"` → UI must NOT interpret this as "bot is online".

**The canonical liveness signal is `archi_thesis`**:

- `archi_thesis` exists AND `archi_thesis.freshness !== 'stale'` → bot is
  alive (writing to Redis recently per `narrative_enricher.py refresh_thesis_sync`)
- `archi_thesis` missing OR `freshness === 'stale'` → bot is silent / killed

Used in `compactPillText` priority chain (rule 1 vs 2 above). This rule
is the SOURCE OF TRUTH for "is Арчi alive" anywhere in the UI.

### Wording — name + status mapping

**Canonical name spelling**: `Арчі` (one and only). Per
`trader-v3/smc_trader_prompt_v3.md` + existing `NarrativePanel.svelte:230`
`<span class="archi-label">Арчі</span>`. The spelling `Архі` is **wrong**
in any agent-naming context (it is the prefix of Ukrainian "архітектура",
unrelated).

**WakeEngine status → Ukrainian map** (`_ARCHI_STATUS_UK`) — literal
translations matching backend semantics, no paraphrasing:

| Backend status | Ukrainian | Backend semantic |
| -------------- | --------- | ---------------- |
| `sleeping`     | `спить`   | wake conditions not all met |
| `watching`     | `спостерігає` | wake conditions all met, armed for trigger |
| `analyzing`    | `аналізує` | bot actively running Sonnet call |
| `alert`        | `сигнал`  | urgent / triggered signal state |
| `active`       | `активний` | legacy PresenceStatus value |

**Forbidden paraphrases** (these were tried and rolled back):

- `sleeping → "чекає умов"` — adds spin not in source. WakeEngine literally
  named the state `sleeping`; UI translates literally.
- `watching → "стежить за умовами"` — same reason; "спостерігає" is
  user-recognizable Ukrainian for watching.

**Offline copy**: `"Арчі вимкнений · {silence_h.toFixed(1)}h"` (silence
suffix omitted when `silence_h === 0`). NOT "Арчі офлайн", NOT "Арчі
заснув", NOT euphemism — bot is killed/disconnected, copy says so.

### Boundary with системний наратив v3

The системний наратив v3 (`narrative.bias_summary`, `narrative.scenarios[]`,
`narrative.warnings`, `narrative.fvg_context`, `narrative.next_area`,
`narrative.headline`) is generated by `core/smc/narrative.py` and is
authoritative for "what is the SMC engine's read of the market right now".

**Where it MAY render**:

- `ChartHud.svelte` `.hud-narrative` inline pill + `.narr-tooltip` hover
  card (lines 341-422) — currently renders system narrative on hover.
  This surface is NOT in scope of this ADR. If it should also be
  cleaned/moved, separate slice.
- Future on-chart overlays / dedicated panels — out of scope here.

**Where it MUST NOT render**:

- `NarrativePanel` — neither in compact pill text nor in expanded body.
  This ADR locks that boundary.
- `CommandRail` — peripheral chrome, not a narrative surface.

**Backend untouched**: `core/smc/narrative.py` keeps shipping all
system-narrative fields. Removing them from NP is purely a UI scope
decision; backend semantics unchanged.

---

## Consequences

### Visible delta (post-deployment)

For Sunday-closed XAU/USD, M15 (typical observation case during this session):

- **CommandRail**: shows `ATR 4.55 · M15 03:42 · 21:06 UTC` (or whatever
  current values) — RV cell gone, ↻ icon gone.
- **NP compact pill**: shows `Арчі спить · 0.5h` (when bot offline) OR
  Арчi's actual thesis text (when bot wrote one and it's fresh).
  Does NOT show `narrative.headline` ever.
- **NP expanded body** (on pill click): shows ONLY Арчi thesis section
  (when present) and presence row. `H4↓ (D1 n/a)`, `2087 buy FVG (C/0)`,
  `⚠ zone_too_far` and similar system narrative — gone.
- **Click on chart / HUD**: collapses expanded NP back to compact pill.

### Backend dependency

`runtime/ws/ws_server.py` ships `frame.atr` from `_smc_runner._engine.get_atr(symbol, tf_s)`.
Same value as REST `/api/context.atr`. **Backend restart required** to
activate this field after ws_server.py changes; UI has graceful
`atr={frame?.atr ?? null}` fallback to `"—"` if field is absent.

### Test cases (manual smoke after any future change in TR corner)

1. **Sunday market, bot killed** → CommandRail shows ATR + countdown
   (countdown ticks even on closed market via wallclock); NP pill shows
   `Арчі вимкнений · {silence}h`; click pill → expanded body shows only
   presence row (no thesis).
2. **Active market, bot alive + writing thesis** → CommandRail same; NP
   pill shows `archi_thesis.thesis` truncated; click pill → expanded
   body shows thesis section + presence row.
3. **Active market, bot alive but no thesis yet** → NP pill shows
   `Арчі спостерігає · {focus}` or similar status; expanded body shows
   only presence row.
4. **TF switch M15 → H1** → CommandRail TF label updates; countdown resets
   to new bucket; NP override persists per `(symbol, tf)` via
   sessionStorage; ATR refreshes from new frame.atr.
5. **Click on chart while NP expanded** → NP collapses to compact;
   compact pill remains visible.

### NOT touched in this ADR

- `core/smc/narrative.py` — keeps emitting all fields as before.
- `ChartHud.svelte` `.hud-narrative` + `.narr-tooltip` — system narrative
  still renders there. If trader feedback later wants it removed/moved,
  separate slice.
- `runtime/smc/wake_engine.py` — `status="watching"|"sleeping"` semantics
  remain. The platform's view of "wake conditions armed" is correctly
  expressed; UI just stops MISREADING it as bot liveness.
- `runtime/smc/narrative_enricher.py` — thesis read from Redis, freshness
  computation per existing logic.
- `core/smc/swings.compute_atr` — backend ATR formula (simplified TR,
  not strict Wilder per line 109 comment) is the SSOT. If a stricter
  formula is needed, fix backend; do NOT shadow with frontend recompute.

---

## Implementation reference

Shipped in 9 commits during 2026-05-09 session (chronological order):

| Commit    | Title                                                                          | What it did |
| --------- | ------------------------------------------------------------------------------ | ----------- |
| `491ab73` | fix(np): remove redundant AWAITING badge for awaiting_setup state              | First badgeLabel cleanup — `'AWAITING' → ''` |
| `6e8e8a4` | PATCH-09: X28-fix CommandRail — backend ATR via wire; RV removed; countdown wallclock | Tier 1 contract enforcement |
| `4183b97` | fix(np): remove STAY OUT + CLOSED badges (ChartHud shell-stage already shows them) | Tier 2 badge cleanup completion |
| `9bda295` | PATCH-10: новий кут cleanup — action-first compact pill + drop ↑trend + drop ↻ | Tier 1 + Tier 2 polish (later partly superseded by 039ba8b) |
| `7132565` | fix(np): Архі → Арчі (canonical agent name spelling)                           | Wording fix |
| `3e7d24e` | fix(np): real Архi-alive detection via thesis freshness, not WakeEngine status | Liveness rule codified |
| `307995c` | fix(np): sleeping → спить (revert overcorrect)                                 | Wording rollback to literal |
| `039ba8b` | fix(np): NarrativePanel = pure Арчi-surface (strip system narrative content)   | Tier 2 + Tier 3 scope lock |
| `a9b66cc` | feat(np): click-outside collapses expanded body (chart click hides NP)         | Tier 4 UX |

---

## Rollback

This ADR is reversible per-commit via `git revert` of the 9 commits in
reverse order. No data migration. No backend contract change beyond
`frame.atr` (rolling that back removes the field; UI falls back to "—").

If the entire новий кут redesign needs to be undone, revert all 9 commits
+ this ADR file. CommandRail returns to local-compute X28 violation,
NP returns to mixed surface — both with full audit trail of why this
was rejected.

---

## Notes

### Why a separate ADR vs amending ADR-0069?

ADR-0069 governs the NP **state machine** (compact / banner / expanded
modes, override persistence, escalation reset). This ADR governs the
**scope, data sources, and wording** of the entire новий кут — broader
than NP alone. It also touches CommandRail and asserts the X28 boundary
for chrome data sources. Different concern, different lifecycle.

ADR-0069 rev 2 §A and §B are still authoritative for:

- agent_state derivation table
- Banner tint rule (B.1 Option A)
- Override stickiness (B.2 Option B escalation reset)

ADR-0070 adds on top: scope (Арчi-only), wording, click-outside, X28-fix.

### Known untouched debt

- `.bias-row` / `.scenario` / `.fvg-ctx` / `.warnings` CSS rules in
  `NarrativePanel.svelte` are dead style (no selectors hit) — leftover
  from pre-039ba8b body. Cleaning is mechanical, do whenever convenient.
- `getTriggerClass()` already removed in 039ba8b. No leftovers.
- ChartHud `.hud-narrative` system-narrative tooltip — out of scope here;
  if trader UX feedback wants it gone too, separate slice.

### Future agents reading this

If a future change suggests:

- "Let's compute ATR / RV / any indicator in CommandRail from candles" → STOP. Read §Tier 1 forbidden list. Backend ADR + slice required first.
- "Let's show {bias_summary | scenarios | warnings | next_area} in NP" → STOP. Read §Tier 3 forbidden list. NP is Арчi-only.
- "Let's translate `sleeping` as something more elegant than 'спить'" → STOP. Read §Wording. Literal mapping is canonical.
- "Let's spell the agent's name as 'Архі'" → STOP. It is `Арчі`. Always.
- "Let's use `archi_presence.status === 'watching'` to detect bot alive" → STOP. Read §Liveness detection. Use `archi_thesis.freshness`.

The contract is locked. Modifications require ADR amendment, not silent edits.
