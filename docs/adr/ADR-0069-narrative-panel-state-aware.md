# ADR-0069: NarrativePanel — State-Aware Modes

## Metadata

| Field          | Value                                                     |
| -------------- | --------------------------------------------------------- |
| ID             | ADR-0069                                                   |
| Status         | ACCEPTED — rev 2 (2026-05-09): §A and §B both RESOLVED. See §Open for resolution paths. **SUPERSEDED IN PART by ADR-0070** (2026-05-09): scope of NP narrowed to PURE Арчi-surface — `narrative.bias_summary`, `scenarios[]`, `warnings`, `fvg_context`, `next_area`, `headline` are NO LONGER rendered by NP per ADR-0070 §Tier 2 + §Tier 3. State machine (compact / banner / expanded) + override logic from this ADR remain authoritative. Read ADR-0070 BEFORE editing NP. |
| Date           | 2026-05-09                                                |
| Authors        | Станіслав                                                 |
| Supersedes     | —                                                         |
| Builds on      | ADR-0066 rev 5 (tokens, T1–T5 typography); ADR-0033 (narrative engine — backend signal source) |
| Coordinates with | ADR-0065 rev 2 (CR-2.5 and NarrativePanel share the same `.top-right-bar` row — NP as leftmost inline-pill); ADR-0068 (chrome real-estate freed) |
| Affects layers | `ui_v4/` chrome (`App.svelte`, existing `NarrativePanel.svelte`); contract field `frame.smc.narrative.agent_state` (or equivalent — see Open §A) |

---

## Quality Axes

- **Ambition target**: R3 — first explicit state→mode mapping for the agent narrative surface; premium real-estate rule formalized
- **Maturity impact**: M3 → M4 — narrative chrome is no longer single-mode-fits-all; size adapts to actionability tier

---

## Context

The current `NarrativePanel.svelte` is a single-mode surface — it
renders the agent narrative at one fixed visual weight regardless of
what the agent is actually doing. This produces two failure modes:

1. **Idle states waste vertical real-estate** — when market is closed
   or agent is in `awaiting_setup`, the panel still occupies its full
   expanded footprint, pushing chart down and competing with CR-2.5.
2. **Time-critical states under-signal** — when agent transitions to
   `prepare` / `ready` / `triggered`, the panel renders identically to
   idle. The trader has no peripheral cue that something changed.

The v2 mockup (`tp3_identity_full.html` §NarrativePanel 3-state
library) reframes this as a **3-mode state machine** driven by the
agent's current state. Premium real-estate rule:

> Vertical real-estate above the chart is the most expensive surface
> in the product. Spend it only when the agent has something time-
> critical to say. Otherwise, collapse.

NarrativePanel lives **inline inside `.top-right-bar`** as the leftmost
item — same horizontal row as CR-2.5 (ATR · RV · cd · UTC · ▶ · ☰).
This was intentionally moved from the "slot 2 under CR-2.5" spec in the
original draft to avoid layout complexity. The 3-mode state machine still
applies: Compact pill stays inline; Banner and Expanded drop as
`position: absolute` below the bar without shifting the row height.

This ADR also formalizes the **agent_state contract** — the backend
must surface a discrete agent state field; if it doesn't yet, that's a
named Open Decision (§A) and a separate slice.

---

## Decision

NarrativePanel becomes a **3-mode** component with state-driven
mode selection and bounded user override.

### Mode 1 · Compact (~28px)

Single line. Agent state badge + one-sentence summary.

| Render                          | Example                                                                |
| ------------------------------- | ---------------------------------------------------------------------- |
| `[state-badge] summary text`    | `[ AWAITING ] No setup. Watching M15 OB at 4520 for reaction.`         |
| Height                          | 28px                                                                    |
| Background                      | `--bg-1` (matches chrome rail)                                          |
| Border                          | bottom 1px `--text-3` at 8% alpha                                       |
| Typography                      | T4 (mono) for state badge, T3 (sans) for summary                        |
| Truncation                      | summary truncates with ellipsis if width exceeded                       |
| Click target                    | entire row → expand to mode 3                                           |

### Mode 2 · Banner (~36px)

Single line, accented background, attention-drawing.

| Render                                                                      | Example                                                       |
| --------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `[state-badge] summary text  ·  [ → expand ]`                               | `[ WATCHING ] M15 OB at 4520 — price approaching, 8 pips above` |
| Height                                                                      | 36px                                                           |
| Background                                                                  | tinted (see Open §B) — currently UNRESOLVED                    |
| Border                                                                      | bottom 1px `--accent-soft`                                     |
| Typography                                                                  | T4 (mono) state badge, T2 (sans) summary, slightly heavier weight |
| Click target                                                                | entire row → expand to mode 3                                   |

### Mode 3 · Expanded (~180px)

Full narrative panel. State badge + summary + structured body
(scenario, levels, conditions, rationale).

| Region                          | Content                                                              |
| ------------------------------- | -------------------------------------------------------------------- |
| Header                          | state badge + summary + collapse `→` button                          |
| Scenario block                  | one-line scenario name + bias arrow                                   |
| Watch levels                    | 2–4 numeric levels with labels (entry, invalidation, target if known) |
| Conditions                      | bullet list of trigger conditions ("close M15 below X", "sweep of Y") |
| Rationale (collapsible)         | 2–3 sentence reasoning, default expanded in mode 3                    |
| Height                          | 180px (clamped); content scrolls if overflows                         |

The expanded body is **the existing NarrativePanel content** — this
ADR doesn't redesign that surface, only formalizes when it's shown
fully vs collapsed.

### State → Mode mapping

Driven by `frame.smc.narrative.agent_state` (or equivalent, see Open §A).

| Agent state         | Default mode | Rationale                                                                            |
| ------------------- | ------------ | ------------------------------------------------------------------------------------ |
| `market_closed`     | Compact      | Nothing to act on; brand+status only                                                  |
| `awaiting_setup`    | Compact      | No actionable level yet; idle watching                                                |
| `stay_out`          | Compact      | Explicit "do nothing" verdict — minimal footprint                                     |
| `watching`          | Banner       | Level identified but conditions not met; needs peripheral attention                    |
| `bias_confirmed`    | Banner       | Direction set but no entry trigger yet; needs awareness                                |
| `prepare`           | Expanded     | Setup forming; trader needs to read full conditions to be ready                        |
| `ready`             | Expanded     | Trigger imminent; full context required for execution decision                          |
| `triggered`         | Expanded     | Active position context; SL/TP/management state shown                                   |

If `agent_state` is unknown / null / not yet provided by backend → mode
defaults to **Compact** with `agent_state` rendered as `[ — ]` and an
`I5 degraded-but-loud` log entry on first occurrence per session.

### User override (sticky per session)

User can force a mode via the panel itself:
- Click compact/banner → expands to mode 3.
- Click `→ collapse` in mode 3 header → collapses to mode 1 (compact).

Override scope: **session** (`sessionStorage`), keyed by
`(symbol, tf)`. New tab / new symbol / new TF / hard reload resets.

Override interaction with state changes — see Open §B.

### Layout & positioning

- Slot: **inline-pill inside `.top-right-bar`**, leftmost item before
  ATR · RV · cd · UTC · ▶ · ☰. NOT a separate band under the rail.
- Compact mode: pill stays inline, `~28px` height, row height unchanged.
- Banner / Expanded: drop as `position: absolute` below the bar.
  `top-right-bar` keeps fixed height — no layout shift to chart.
- z-index: above chart, below modals/menus.
- Transition: mode change uses 160ms height + opacity ease.

### Mobile (`<640px`)

| Mode     | Mobile behavior                                                  |
| -------- | ---------------------------------------------------------------- |
| Compact  | Inline pill inside mobile chrome row (leftmost)                   |
| Banner   | Full-width strip; tap-to-expand                                   |
| Expanded | Full-width sheet, dismiss via swipe-down or `→` collapse button   |

State→mode mapping unchanged on mobile.

### Accessibility

- State badge: semantic color + text label (color is not the only signal).
- Mode change announces via `aria-live="polite"` region: "Narrative expanded" / "Narrative collapsed to banner".
- Banner mode: `role="alert"` on first paint per state transition (announces once, then quiets).
- Expanded mode: `role="region"`, `aria-label="Agent narrative — <agent_state>"`.
- Keyboard: `Tab` to focus panel, `Enter` to expand, `Esc` to collapse from expanded.

---

## Alternatives considered

1. **Single fixed mode (current)**
   - Zero migration cost.
   - Rejected: produces both failure modes (idle waste + critical
     under-signal). Doesn't reach M4 maturity for narrative surface.

2. **Two modes only (collapsed + expanded), no banner**
   - Simpler state machine; banner overlap with compact is a fair concern.
   - Rejected: loses the peripheral-attention signal for `watching` /
     `bias_confirmed`. Trader needs a "something is happening but not
     critical yet" mode that a binary collapsed/expanded can't express.

3. **Always-banner mode (drop compact and expanded)**
   - Consistent visual weight; one footprint to design around.
   - Rejected: idle states (closed market, no setup) get false-positive
     attention treatment, and time-critical states can't surface enough
     content to be useful. Worst of both ends.

4. **Modal popup for `prepare`/`ready`/`triggered` instead of inline expand**
   - Maximum signal for critical; can't be missed.
   - Rejected: modal interrupts whatever the trader is doing on the
     chart. Inline expand respects the chart context. Modal would be
     the right call for `triggered` only if trader misses 3+ banners
     — not in scope for this ADR (could be a future P-slice with explicit
     escalation contract).

5. **Auto-expand on every state transition, no override stickiness**
   - Simpler; always-current.
   - Rejected: hostile to traders who actively collapse to compact for
     focus. Stickiness within session respects user agency.

---

## Implementation plan

3 P-slices, each ≤ ~140 LOC, K6 verify gate per slice.

### Slice 1 · State→mode mapping + Compact / Expanded modes (no Banner yet)

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/NarrativePanel.svelte` | ~120 | Add mode state (`'compact' \| 'banner' \| 'expanded'`). Read `frame.smc.narrative.agent_state` (with null-fallback to `compact`). Implement Compact (28px) and Expanded (180px) renders. State→mode mapping per table (Banner states route to Expanded for now). User override via click expand / `→` collapse. sessionStorage persistence keyed by `(symbol, tf)`. ARIA + keyboard. Existing expanded body unchanged. |
| `ui_v4/src/App.svelte` | ~10 | Reserve slot under CR-2.5; pass `frame` prop. |

Verify: load chart → compact mode if `agent_state ∈ {market_closed, awaiting_setup, stay_out}`, else expanded. Click compact → expands. Click `→` collapse in expanded → compacts. Reload page → state persists in session. New TF → state resets. `npm run build` clean. `get_errors()` zero on touched files.

### Slice 2 · Banner mode

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/NarrativePanel.svelte` | ~80 | Add Banner render (36px). Wire `watching` / `bias_confirmed` agent states to Banner default. Tinted background per Open §B resolution (or fallback to `--accent-soft` at low alpha if §B unresolved at slice ship). `aria-live` announce on state→mode change. Override interaction per Open §B resolution. |

Verify: simulate `watching` state → Banner mode at 36px. Click → expands to mode 3. Force agent state cycle (compact → banner → expanded → compact) via dev tool → smooth 160ms transitions. `npm run build` clean.

### Slice 3 · Mobile reflow + escalation polish

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/NarrativePanel.svelte` | ~50 | `@media (max-width: 640px)` rules: full-width strips for compact/banner, full-width sheet for expanded with swipe-down dismiss. Touch hit area ≥ 44×44 for collapse button. |

Verify: resize devtools to 600px → panel full-width under chrome rail. Trigger expanded mode → sheet covers chart; dismiss via swipe-down works. State→mode mapping unchanged. `npm run build` clean.

---

## Consequences

**Positive**:

- Vertical real-estate spent in proportion to actionability tier.
- Peripheral attention signal (Banner) for watching/bias_confirmed states.
- User agency preserved (override sticky per session).
- Inline expansion (not modal) respects chart context.
- Mobile path explicit, not afterthought.
- F9 craftsmanship: mode contract is declarative, no special-case branches per agent state inside renderer.

**Negative / accepted costs**:

- Layout transitions on every state change — 160ms ease is fast enough not to feel laggy, but visible. Mitigation: chart container reserves the band so no chart layout shift; only panel grows.
- User override scope is per-session per-(symbol,tf), not global. Trade: respects per-context preferences but doesn't remember across reloads. Acceptable; can extend to localStorage later if requested.
- Agent state set is closed (8 values) — adding a new state requires ADR amendment (mapping + default mode + transition behavior). This is intentional; loose state vocabulary breaks the contract.

**Risks / monitoring**:

- Backend `agent_state` field absence → all panels render Compact with `[ — ]` badge. I5 degraded-but-loud once-per-session log. Monitor: if log fires in production for >0% of sessions, escalate to backend coordination (Open §A).
- Banner color regression: if Open §B chooses warning-tint always and a future agent state needs different semantics (e.g., `triggered_won` vs `triggered_lost`), the always-warning rule breaks. Mitigation: §B resolution must consider future state additions.
- Override stickiness conflict: user collapses banner to focus on chart, then state transitions to `ready` — should override reset? See Open §B.

---

## Open decisions

### §A · `agent_state` backend signal contract — RESOLVED rev 2 (2026-05-09)

**Question**: which backend field surfaces the discrete agent state?

Options considered:

1. New `frame.smc.narrative.agent_state: AgentState` (string enum from
   the 8-value set).
2. Derive from existing fields (e.g., `frame.smc.narrative.scenario` +
   `frame.smc.signals[]` — frontend infers state).
3. New `/api/status` field surfaced separately from frame.

**Resolution: Hybrid (Option 2 → Option 1 path)** — neither original
option alone, chosen 2026-05-09 after backend audit revealed all required
state is already on the wire.

Backend audit confirms all 8 ADR-0069 agent states are derivable from
existing frame fields without backend amendment. Source refs:

- [`core/smc/narrative.py:803 narrative_to_wire`](../../core/smc/narrative.py#L803)
- [`ui_v4/src/types.ts:130 NarrativeBlock`](../../ui_v4/src/types.ts#L130)
- [`ui_v4/src/types.ts:151 ShellStage`](../../ui_v4/src/types.ts#L151)

Derivation table:

| Output `agent_state` | Derivation rule (frontend, SSOT)                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------ |
| `market_closed`      | `narrative.sub_mode === 'market_closed'`                                                                 |
| `triggered`          | `shell.stage === 'triggered'`                                                                           |
| `ready`              | `shell.stage === 'ready'`                                                                               |
| `prepare`            | `shell.stage === 'prepare'`                                                                             |
| `stay_out`           | `shell.stage === 'stayout'`                                                                             |
| `bias_confirmed`     | `narrative.mode === 'wait'` AND `narrative.bias_summary` non-empty AND `scenarios[0]?.trigger === 'approaching'` |
| `watching`           | `narrative.mode === 'wait'` AND `scenarios.length > 0` AND no `bias_summary`                            |
| `awaiting_setup`     | `narrative.mode === 'wait'` AND `scenarios.length === 0` (default fallback when above unmatched)        |

Implementation contract: a pure helper `deriveAgentState(narrative, shell): AgentState` lives in `ui_v4/src/lib/agentState.ts` (or co-located with NarrativePanel). Slice 1 reads from this helper. The 8-value union `AgentState` type lives in `ui_v4/src/types.ts`.

**Forward path to Option 1**: backend MAY later add explicit
`frame.smc.narrative.agent_state: AgentState` field as **additive,
non-breaking** opt-in. NarrativePanel reads explicit when present, falls
back to `deriveAgentState(...)` otherwise. Single switch point preserves
the contract. Backend coordination is optional, not blocking.

**Why not Option 1 alone**: would have shipped Slice 1 with null-fallback (always compact `[ — ]`) for 1-2 weeks until backend amendment lands. Hybrid avoids that regression while keeping the door open for explicit signal later.

**Why not Option 2 alone**: drift risk — derivation logic could diverge from backend intent over time. Hybrid preserves the explicit-signal escape hatch.

**Trade-off accepted**: derivation rules are MVP — corner cases (`bias_confirmed` vs `watching` boundary, transient `prepare` flicker) may need refinement after live observation. ADR-0033 may amend wire to add `agent_state` if frontend derivation proves brittle.

### §B · Banner color rule + override-vs-state-transition stickiness — RESOLVED rev 2 (2026-05-09)

Both questions resolved with the interim defaults from §B's recommendation block, accepted 2026-05-09 without need for live trader feedback round (defaults are reversible if feedback later surfaces friction).

**B.1 Banner background tint — RESOLVED: Option A (always warning-tint)**

Banner uses `color-mix(in srgb, var(--warn) 14%, transparent)` background and `var(--warn)` left-border accent (1.5px) regardless of which Banner-state triggered it (`watching` vs `bias_confirmed`).

Rationale: banner ≡ "peripheral attention required" — color reserved for **urgency channel**, not state-type channel. State type lives in the pill text. Cleaner mental model + avoids color noise as more banner-routed states are added.

**B.2 Override stickiness — RESOLVED: Option B (escalation resets override)**

When user collapses banner/expanded to compact via override (sessionStorage keyed by `(symbol, tf)`):

- **Escalation** (Banner-state → Expanded-state, e.g., `watching` → `ready` or `triggered`): override **resets**, panel re-expands automatically. User attention forced for critical states.
- **De-escalation** (Expanded-state → Banner-state, e.g., `triggered` → `watching` after position close): override **preserved**, panel stays compact.
- **Lateral** (within same tier, e.g., `awaiting_setup` → `stay_out`): override preserved.

Rationale: trader workflow safety > UX stickiness. Missing a `ready`
signal because of an earlier user-collapse is worse than a brief
auto-expand surprise. Trader can re-collapse manually if annoyed. Both
interim defaults are localized to render logic — easily reversible
without contract change.

Implementation: NarrativePanel keeps a `lastSeenTier: 'compact' | 'banner' | 'expanded' | null` in sessionStorage alongside the override. On state change, compute new tier; if `tier > lastSeenTier`, clear override + update `lastSeenTier`. Otherwise preserve override.

---

## Rollback

Per slice (each is one commit):

- Slice 3 revert → mobile loses dedicated reflow; falls back to desktop renders cropped. Compact + banner fit, expanded overflows. Acceptable temporary state.
- Slice 2 revert → Banner-state agent states route to Expanded (slice 1 fallback). User loses Banner peripheral signal but doesn't lose information; everything escalates one tier.
- Slice 1 revert → restores current single-mode NarrativePanel. No data migration. Backend `agent_state` field (if landed for §A) stays harmless with no consumer.

If the entire 3-mode contract proves wrong post-ship, the panel can
be feature-flagged behind a runtime check while leaving the original
single-mode renderer in place via a forked component path. Avoid
reverting the backend `agent_state` field — useful for other consumers
(metrics, alerting, ADR-0033 internal observability) regardless of
the panel UI.
