# ADR-0069: NarrativePanel — State-Aware Modes

## Metadata

| Field          | Value                                                     |
| -------------- | --------------------------------------------------------- |
| ID             | ADR-0069                                                   |
| Status         | ACCEPTED (with 2 named Open Decisions, see §Open)         |
| Date           | 2026-05-09                                                |
| Authors        | Станіслав                                                 |
| Supersedes     | —                                                         |
| Builds on      | ADR-0066 rev 5 (tokens, T1–T5 typography); ADR-0033 (narrative engine — backend signal source) |
| Coordinates with | ADR-0065 rev 2 (CR-2.5 sits in slot 1; NarrativePanel slot 2 immediately below); ADR-0068 (chrome real-estate freed) |
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

ADR-0065 rev 2 places NarrativePanel in slot 2 (under CR-2.5). Without
this state-aware contract, slot 2 either always-takes-180px (wastes
real-estate in idle) or always-takes-28px (loses signal in critical).
The 3-mode contract resolves the trade-off.

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

- Slot: directly under CR-2.5 in the `top-right-bar` column. ADR-0065
  rev 2 reserves vertical band immediately under the rail.
- Width: matches CR-2.5 width (≤300px desktop, ≤120px is too narrow
  for narrative — on mobile the panel goes full-width below chrome,
  see Mobile below).
- z-index: above chart, below modals/menus.
- Transition: mode change uses 160ms height + opacity ease. No layout
  shift to chart (chart container has fixed height; panel grows into
  reserved band, doesn't push chart).

### Mobile (`<640px`)

| Mode     | Mobile behavior                                                  |
| -------- | ---------------------------------------------------------------- |
| Compact  | Full-width strip under CR-2.5 mobile rail                         |
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

### §A · `agent_state` backend signal contract

**Question**: which backend field surfaces the discrete agent state?

Options:
1. New `frame.smc.narrative.agent_state: AgentState` (string enum from the 8-value set).
2. Derive from existing fields (e.g., `frame.smc.narrative.scenario` + `frame.smc.signals[]` — frontend infers state).
3. New `/api/status` field surfaced separately from frame.

**Recommendation**: Option 1, keeps panel rendering pure-from-frame and explicit-state. ADR-0033 narrative engine likely already has internal state — surface it. Resolve before Slice 1 ships, otherwise Slice 1 ships with null-fallback (always compact, `[ — ]` badge) and tracks `[degraded-pending-signal]`.

### §B · Banner color rule + override-vs-state-transition stickiness

Two coupled questions deferred per design conversation:

**B.1 Banner background tint**: should banner always use one tint
(e.g., warning-soft) regardless of which Banner-state triggered it
(`watching` vs `bias_confirmed`), or vary per state?

- Option A: Always warning-tint (`--warn` at low alpha) — banner ≡ "needs attention", source of attention is in the text not the color.
- Option B: Vary — `watching` uses neutral tint, `bias_confirmed` uses bias direction tint (`--bull` / `--bear` low alpha).

**B.2 Override-vs-state-transition stickiness**: when user collapses
banner/expanded to compact via override, then agent state transitions
to a more critical state (e.g., `watching` → `ready`), should the
override be respected (stay compact) or reset (auto-expand)?

- Option A: Override always wins within session — trader chose compact, system respects until session ends or user clicks expand again.
- Option B: Override resets on escalation (Banner → Expanded transition), preserves on de-escalation. Critical states force re-attention.
- Option C: Tiered — override respected for ≤1 escalation step, reset after that.

**Resolution path**: both B.1 and B.2 require live trader feedback to
decide. Slice 2 must not ship without resolution. Recommended interim
approach if forced to ship Slice 2 before resolution:
- B.1 → Option A (always warning-tint) as least-surprising default.
- B.2 → Option B (escalation resets override) as safest default — favors signal preservation over stickiness; user can re-collapse if annoyed.

Both interim defaults are easily reversible without contract change
(localized to render logic).

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
