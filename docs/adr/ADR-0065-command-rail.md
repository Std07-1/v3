# ADR-0065: Command Rail (CR-2.5) — Top-Right Status & Control Strip

## Metadata

| Field          | Value                                                    |
| -------------- | -------------------------------------------------------- |
| ID             | ADR-0065                                                 |
| Status         | PROPOSED                                                 |
| Date           | 2026-05-08                                               |
| Authors        | Станіслав                                                |
| Supersedes     | —                                                        |
| Depends on     | ADR-0066 PATCH 02 (gold accent token, soft) — gold/warn must exist before CR-2.5 references them |
| Soft-blocks    | —                                                        |
| Affects layers | `ui_v4/` chrome (`App.svelte` top-right-bar, new `CommandRail.svelte`) |

---

## Quality Axes

- **Ambition target**: R3 (formalize ad-hoc strip into a single declarative rail with slot contract)
- **Maturity impact**: M3 → M4 (introduce typed slot model + render-order SSOT for chrome status surfaces)

---

## Context

The top-right strip in `ui_v4` is the platform's **status & control rail** —
the place where the trader glances to confirm the system is healthy and
the chart is reading reality correctly. Currently
([`App.svelte:528-597`](../../ui_v4/src/App.svelte#L528-L597)) it is an
ad-hoc inline `<div class="top-right-bar">` containing six chrome
elements added incrementally over Entry 077/078:

```
[◐ theme] [▮ style] | [● health dot] [☀ brightness] | [🔧 diag] [HH:MM UTC]
```

Problems:

1. **No slot contract** — order of elements, separators, and grouping
   are baked into JSX. Adding/reordering anything = JSX diff per element.
2. **No semantic categories** — health dot (system state), brightness
   (theme control), diag toggle (developer), and clock (live data) all
   live in one undifferentiated row. Trader cannot scan "system state at
   a glance" without parsing icon vocabulary.
3. **No real-time market context** — the rail does NOT surface the data
   that actually answers "is the chart reading reality?":
   - **ATR** for the active TF (volatility scale)
   - **RV** (relative volume of current bar vs N-bar SMA)
   - **cd** (countdown to next bar close)
   - **SMC F** (Feature gate — is SMC overlay computing or stalled?)
4. **Inline styles + `font-size` literals** — every element has its own
   inline style block, conflicting with the typographic scale that
   ADR-0066 PATCH 06 will enforce.
5. **No mobile contract** — on `<480px` the entire bar is `display:none`
   and the clock is reparented to `ChartHud`. There is no formal mobile
   render path; the desktop strip is simply hidden.

CR-2.5 is the spec name carried over from earlier design sessions
("Command Rail iteration 2.5" — final iteration before formalization).

---

## Decision

Adopt a single declarative **Command Rail** component
(`ui_v4/src/layout/CommandRail.svelte`) with a typed slot contract,
three semantic groups, and explicit render order. The rail consumes
tokens from `tokens.css` (introduced by ADR-0066 PATCH 02) — no inline
colors, no inline `font-size`. Mobile path = subset of the same slots,
not a separate component.

### Tier 1 · Semantic groups

The rail is divided into **three groups** read left-to-right:

| Group | Purpose                          | Slots                                        |
| ----- | -------------------------------- | -------------------------------------------- |
| **G1 · Market context**  | Real-time data scan          | `ATR` · `RV` · `cd` · `SMC F` badge          |
| **G2 · System state**    | Is the platform healthy?     | health dot · WS latency tag (optional)       |
| **G3 · Controls**        | User-driven chrome           | theme · candle style · brightness · diag · clock |

Groups separated by a thin vertical `--rule` (1px, `--text-3` at 18% alpha).
Groups are **always rendered in this order**. No slot may move between
groups without an ADR amendment.

### Tier 2 · Slot contract

Each slot is a typed Svelte snippet with a fixed signature:

```ts
type RailSlot = {
  id: string;                    // stable id, e.g. 'atr', 'rv', 'cd', 'smc-f', 'health', 'theme', 'clock'
  group: 'G1' | 'G2' | 'G3';
  width: 'auto' | number;        // px hint; 'auto' = content-driven
  visibility: 'always' | 'desktop-only' | 'mobile-only' | 'when-active';
  render: Snippet;               // Svelte 5 snippet
};
```

Rail renders `slots.filter(visibilityMatches).sort(byGroupThenIndex)`.
Order within a group is the order of declaration in `RAIL_SLOTS` array
(SSOT — see Tier 5).

### Tier 3 · G1 slots — Market context (NEW)

These are the four data slots that answer "is the chart reading reality?"
at a single glance. All four consume data already on `diagStore` (no
new wire payload required).

| Slot      | Source                                  | Format                                | Color rule                                     |
| --------- | --------------------------------------- | ------------------------------------- | ---------------------------------------------- |
| `atr`     | `diagStore.smc.atr_pips` (active TF)    | `ATR 23` (T4 pill, JBMono 10px caps)  | `--text-2` always                              |
| `rv`      | `diagStore.smc.rv_ratio` (current bar)  | `RV 1.4×` (T4 pill)                   | `--bull` if ≥1.5, `--text-2` if 0.7–1.5, `--warn` if <0.7 |
| `cd`      | computed: `(bar_close_ms - now) / 1000` | `cd 47s` / `cd 12m` / `cd 2h` (T4)    | `--text-3` always; **no animation**            |
| `smc-f`   | `diagStore.smc.feature_state`           | `SMC F` (T4 pill, framed)             | `--accent` border if active, `--bear` if stalled, hidden if disabled |

`cd` updates at the same cadence as the existing clock interval
(1 Hz). No new timer.

If `diagStore.smc` is absent (SMC overlay disabled) → entire G1 group
collapses (returns no DOM). Group separator before G2 also hidden.

### Tier 4 · G2 slots — System state

| Slot      | Renders                                                   | Replaces                                |
| --------- | --------------------------------------------------------- | --------------------------------------- |
| `health`  | 8px dot, color from `STATUS_COLORS[statusInfo.status]`    | current `.tr-dot`                       |
| `ws-lag`  | `WS 42ms` (T5, only when lag > 200ms; else hidden)        | NEW (degraded-but-loud — I5)            |

`ws-lag` slot is `visibility: 'when-active'` — invisible during normal
operation, becomes visible (with `--warn` color at >200ms, `--bear` at
>1000ms) to satisfy I5 "no silent degradation".

### Tier 5 · G3 slots — Controls

| Slot         | Renders                                  | Behavior change vs current     |
| ------------ | ---------------------------------------- | ------------------------------ |
| `theme`      | ◐ icon button + dropdown                 | unchanged                      |
| `candle`     | ▮ icon button + dropdown with swatches   | unchanged                      |
| `brightness` | ☀/◐/●/◌/◯ glyph + wheel handler          | unchanged                      |
| `diag`       | 🔧 icon button                            | unchanged                      |
| `clock`      | `HH:MM UTC` (T3, JBMono 11px)            | now last in rail               |

Visual: clock moves to the **far right** (terminal position). This is
the "anchor of time" — cf. trading platforms convention. Currently
clock is rendered first after the diag button; CR-2.5 puts it last.

### Tier 6 · SSOT slot registry

Slot order, grouping, and visibility live in a single array
`ui_v4/src/layout/CommandRailSlots.ts`:

```ts
export const RAIL_SLOTS: RailSlot[] = [
  // G1 · Market context
  { id: 'atr', group: 'G1', visibility: 'always', ... },
  { id: 'rv', group: 'G1', visibility: 'always', ... },
  { id: 'cd', group: 'G1', visibility: 'always', ... },
  { id: 'smc-f', group: 'G1', visibility: 'when-active', ... },
  // G2 · System state
  { id: 'health', group: 'G2', visibility: 'always', ... },
  { id: 'ws-lag', group: 'G2', visibility: 'when-active', ... },
  // G3 · Controls
  { id: 'theme', group: 'G3', visibility: 'desktop-only', ... },
  { id: 'candle', group: 'G3', visibility: 'desktop-only', ... },
  { id: 'brightness', group: 'G3', visibility: 'desktop-only', ... },
  { id: 'diag', group: 'G3', visibility: 'always', ... },
  { id: 'clock', group: 'G3', visibility: 'always', ... },
];
```

Adding a new chrome status element = one entry in this array. No
JSX edit in `CommandRail.svelte` or `App.svelte` required for new slots
once their snippet is defined.

### Tier 7 · Typography & spacing — strict tokens

CR-2.5 is the **first surface fully on the typographic scale system**
defined in ADR-0066. It does not declare any inline `font-size`,
`font-weight`, or `font-family`. All declarations resolve to T1–T5
classes from ADR-0066 PATCH 06.

| Element              | Class    | Tier (ADR-0066) |
| -------------------- | -------- | --------------- |
| ATR / RV / cd values | `.t-num-sec` | T3            |
| SMC F badge          | `.t-pill`    | T4            |
| Health dot           | (none — pure shape) | —      |
| WS lag               | `.t-tert`    | T5            |
| Clock                | `.t-num-sec` | T3            |
| Icon buttons         | `.t-pill`    | T4 (icon glyph) |

Spacing (matches ADR-0066 spec):
- Slot height: `22px`
- Pill padding-x: `6px`
- Group gap (within G): `8px`
- Inter-group gap (G1↔G2↔G3): `12px` (separator spans 4px on each side)
- Icon hit area: `14×14`

### Tier 8 · Mobile path

On `<480px` (existing breakpoint), `RAIL_SLOTS.filter(s => s.visibility !== 'desktop-only')`
renders. That = G1 (atr/rv/cd/smc-f) + G2 (health/ws-lag) + clock + diag.
Theme/candle/brightness collapse — they live in a separate mobile
overflow menu (out of scope for CR-2.5; tracked under future
"Mobile chrome consolidation" ADR).

This replaces the current full-hide of `.top-right-bar` on mobile.
Trader on mobile keeps the market-context scan and system state.

### Tier 9 · Accessibility

- Each pill has `aria-label` (e.g., `aria-label="ATR 23 pips, M15 timeframe"`).
- Color-coded states (RV `--bull`/`--warn`, ws-lag `--warn`/`--bear`) carry
  a redundant glyph or text suffix (e.g., `RV 0.4× ↓` for low).
- Icon buttons have `title` and `aria-label`.
- Tab-order: G3 controls only (G1, G2 are read-only data — `tabindex="-1"`).

---

## Alternatives considered

1. **Keep inline strip, just add ATR/RV/cd/smc-f inline**
   - Cheaper (≤30 LOC), no new component.
   - Rejected: perpetuates the no-contract problem; next addition is
     another inline diff. Doesn't solve M3→M4 ambition.
2. **Split into two rails (top-right + below banner)**
   - Cleaner separation of "system" vs "market".
   - Rejected: adds a second chrome row, eats vertical canvas, breaks
     "single glance" property. Trader's eye should land on one zone.
3. **Embed market-context (G1) inside `BiasBanner` instead**
   - BiasBanner is multi-TF directional bias, not real-time scan;
     conflating ATR/cd into it dilutes its purpose.
   - Rejected: keep each surface single-purpose.
4. **Use a third-party rail/toolbar component**
   - `lucide-svelte` icons are already incoming per ADR-0066, but a
     full toolbar lib (e.g., `@melt-ui`) would add ~30KB.
   - Rejected: 11 slots on one row do not justify a dependency.

---

## Implementation plan

Single PATCH 07 (~180 LOC across 3 files) — runs **after** ADR-0066
PATCH 02 (tokens.css). PATCH 07 is its own slice, not split because
the slot contract + component + integration must land atomically (a
half-migrated rail is worse than the current strip).

| Step | File                                          | LOC  | What                                                       |
| ---- | --------------------------------------------- | ---- | ---------------------------------------------------------- |
| 1    | `ui_v4/src/layout/CommandRailSlots.ts` (new)  | ~50  | `RailSlot` type + `RAIL_SLOTS` registry                    |
| 2    | `ui_v4/src/layout/CommandRail.svelte` (new)   | ~110 | Component: groups, separators, slot dispatcher, snippets   |
| 3    | `ui_v4/src/App.svelte` (modify)               | ~20  | Replace `.top-right-bar` block with `<CommandRail />`. Keep dropdowns/state local in App, pass via props/snippets to slots that need them (theme picker, candle picker, diag toggle). |

**Removed**: `.top-right-bar` styles in `App.svelte` (lines ~646–800).
Mobile-hide rule moves into `CommandRail.svelte` via slot visibility.

**Verify** (mini-medium):

1. Visual: open `localhost:5173` → top-right rail shows three groups
   with thin separators. Order matches Tier 1 table.
2. Resize to 360px width → G3 collapses to (diag + clock); G1 + G2
   remain visible.
3. Disable SMC overlay (toggle in DiagPanel) → G1 collapses entirely;
   G1↔G2 separator hidden.
4. Inject high WS latency (mock `diagStore.ws_lag_ms = 850`) → `ws-lag`
   slot appears with `--warn` color.
5. `npm run build` succeeds with no new diagnostics.
6. Tab through chrome → focus order: theme → candle → brightness →
   diag (G1, G2 skipped per Tier 9).

**Rollback**: revert PATCH 07 commit. Component files (`CommandRail.svelte`,
`CommandRailSlots.ts`) deleted. `App.svelte` restored to current strip.
No data migration. No coordinated rollback with PATCH 02 — tokens stay,
they're harmless without consumer.

---

## Consequences

### Visible changes

1. **New data on rail** — ATR, RV, countdown, SMC F badge appear
   top-right. Trader gains "is reality being read?" scan at a glance.
2. **Group separators** — thin vertical rules visually segment the rail
   into three semantic zones (was: one undifferentiated row).
3. **Clock moves to far right** — terminal position; matches platform
   convention.
4. **Mobile rail** — instead of full hide, a subset (G1 + G2 + clock +
   diag) renders. Trader on mobile keeps situational awareness.
5. **WS lag pill** appears only when degraded — silent normal operation,
   loud degradation (I5 satisfied).

### Invariants touched

- **I4 (one update-potok)** — preserved: G1 reads from existing
  `diagStore`, no new endpoint.
- **I5 (degraded-but-loud)** — strengthened: WS lag, RV anomaly, SMC F
  stall now have visible signals where previously they were hidden in
  DiagPanel only.
- **G1 (UI = read-only renderer)** — preserved: rail renders values
  directly from `diagStore`, no derivation/classification of backend
  truth (X28 respected). Coloring of RV is a directional render rule
  defined in this ADR (acceptable per X28 carve-out).

### Not touched

- Chart canvas, OverlayRenderer, candle/volume series — untouched.
- WS protocol — no new fields. ATR/RV/SMC F all already on
  `diagStore.smc.*`. cd computed client-side from `bar_close_ms`.
- Backend, UDS, broker layer — untouched.
- DiagPanel — unchanged; remains the verbose detail surface. CR-2.5 is
  the **glance** surface; DiagPanel is the **inspect** surface.

### Future work

- **Per-slot user customization** (hide/show, reorder via drag) — out of
  scope. Rail is fixed by `RAIL_SLOTS` array. If demand emerges, store
  user preference in `localStorage` and filter `RAIL_SLOTS` at render.
- **Mobile chrome consolidation** — separate ADR. CR-2.5 only ensures
  mobile has the critical subset; full mobile redesign is out of scope.
- **Slot animation policy** — CR-2.5 explicitly forbids animation
  (per "no fluff, premium restraint" — ADR-0036). If animation is
  needed (e.g., `cd` last-5s flash), separate ADR with motion budget.

---

## References

- ADR-0066 (Visual Identity System) — token SSOT, typographic scale
- ADR-0036 (Premium Trader-First Shell) — premium restraint, no fluff
- ADR-0028-v2 (Elimination Engine) — display budget philosophy
- `ui_v4/src/App.svelte:528-800` — current ad-hoc strip
- `ui_v4/src/layout/ChartHud.svelte:326` — current mobile clock reparent
