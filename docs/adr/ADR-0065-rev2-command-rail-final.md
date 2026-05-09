# ADR-0065 rev 2: Command Rail (CR-2.5) — Final Layout Contract

## Metadata

| Field          | Value                                                    |
| -------------- | -------------------------------------------------------- |
| ID             | ADR-0065 rev 2                                           |
| Status         | ACCEPTED                                                 |
| Date           | 2026-05-09                                               |
| Authors        | Станіслав                                                |
| Supersedes     | ADR-0065 rev 1 (PROPOSED) — slot taxonomy obsolete        |
| Builds on      | ADR-0065 rev 1 MVP code (`CommandRail.svelte` ~225 LOC, commit `e6b8497`) — keeps ATR/RV/cd compute logic, replaces presentation contract |
| Depends on     | ADR-0066 rev 5 (tokens, T1–T5 typography) — shipped       |
| Coordinates with | ADR-0068 (Brand Surface) — frees top-right of theme/style/diag icons; ADR-0069 (NarrativePanel state-aware) — CR-2.5 sits in slot 1, NarrativePanel slot 2 |
| Affects layers | `ui_v4/` chrome (`App.svelte`, `CommandRail.svelte`, new `CommandRailOverflow.svelte`); contract field `frame.flags.smc_focus` (or equivalent — see Open §A) |

---

## Quality Axes

- **Ambition target**: R3 — operator surface formalization with explicit slot+overflow contract; mobile reflow first-class
- **Maturity impact**: M3 → M4 — CR-2.5 becomes the canonical chrome rail; chrome icon budget is no longer ad-hoc

---

## Context

ADR-0065 rev 1 (PROPOSED, 2026-05-08) defined a 3-group rail (G1 Market context · G2 System state · G3 Controls) with 11 slots inline. The Variant B MVP shipped in commit `e6b8497` implemented G1 only (`ATR · RV · M15-cd`) inline in the existing `.top-right-bar`, deferring everything else to "rev 2".

In parallel, the visual identity work (ADR-0066 rev 5) plus the v2 design mockup (`tp3_identity_full.html`) reframed what the rail should be:

1. **Inline icon budget is the wrong axis to grow on.** Rev 1 wanted 11 visible icons. Real chrome scan time degrades past ~6 visible elements. The mockup audit reduced visible to 5 (3 status texts + 2 buttons + SMC F badge), with everything else collapsed into an overflow `☰` menu.
2. **Theme / candle style / brightness / diag are intra-session controls**, not glance-state. They change rarely (theme: 0–1× per session; candle style: 0–1× per session; brightness: 0–2×; diag: developer-only). Promoting them to inline slots is a category error. They belong in an overflow.
3. **Brand wordmark moves out** of the top-left chrome (per ADR-0068) into a bottom-right chart watermark. This frees top-left for trading info and confirms top-right rail as the **single chrome operator surface**.
4. **Mobile reflow** (`<640px`) needs a real spec, not `display:none` on the whole rail. Status text row hides; buttons + SMC F stay.
5. **Width target** ≈ 290px (from mockup) vs current MVP ≈ 470px — leaves chart real-estate for NarrativePanel banner mode (ADR-0069) directly under the rail without horizontal collision.

Rev 1's group taxonomy (G1/G2/G3) was a useful design lens but is not what shipped and is not what the v2 mockup specifies. Rev 2 replaces the slot taxonomy with a flat **inline + overflow** contract.

---

## Decision

CR-2.5 final layout has **two visibility tiers**:

### Tier 1 · Inline (always visible on desktop; subset on mobile)

Read left-to-right inside `top-right-bar`, separated by `tr-sep` (1px,
`--text-3` at 8% alpha) groups:

```
[ ATR · RV · M15-cd · UTC ]  |  [ ▶ replay ]  [ ☰ overflow ]  [ SMC F ]
       status row                action buttons          state badge
```

| Slot       | Type            | Source                                                       | Format                   | Color rule                                                                                |
| ---------- | --------------- | ------------------------------------------------------------ | ------------------------ | ----------------------------------------------------------------------------------------- |
| `atr`      | status text     | `frame.candles` (Wilder TR over 14 closed bars)              | `ATR 4.82` (mono)        | `--text-2` always                                                                          |
| `rv`       | status text     | `frame.candles` (last v / mean prior 20 v)                   | `RV 1.23×`               | `--accent` (gold) if ≥1.5×, `--text-2` if 0.5–1.5×, `--text-3` dim if ≤0.5×                |
| `cd`       | status text     | `(last.t_ms + tfMs - now)` clamped                           | `M15 02:17`              | `--text-2`; `null` → `M15 ——:——` (I5 degraded-but-loud)                                  |
| `utc`      | status text     | `Date.now()` formatted HH:MM                                 | `06:48 UTC`              | `--text-2`                                                                                 |
| `replay`   | icon button     | wires to existing `ReplayBar` open action (does NOT duplicate state) | `▶` glyph (12px) | `--text-3` idle, `--accent` hover, `--accent` solid when replay-mode active                |
| `overflow` | icon button     | toggles dropdown menu (Tier 2)                               | `☰` glyph (12px)         | `--text-3` idle, `--accent` hover; pressed-state when menu open                            |
| `smc-f`    | clickable badge | preserves **existing SMC Focus toggle behavior** (no semantic change) | `SMC F` framed pill | unchanged — exact current colors and click handler. Rev 2 only restricts where it lives. |

Order is fixed. Adding/removing slots = ADR amendment.

### Tier 2 · Overflow menu (`☰`)

A dropdown menu (NOT a sliding sheet) anchored under the `☰` button.
Same-side anchor (right edge aligns with `☰` right edge), opens
downward, max-width ≈ 220px, max-height per viewport with internal
scroll if needed.

| Item              | Renders                                                       | Behavior preservation                                                          |
| ----------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Theme picker      | `Theme ▸` submenu (Dark / Black / Light) with current radio   | Same setter as current `◐` button. Dropdown moves out of inline.               |
| Candle style picker | `Style ▸` submenu (Classic / Gray / Stealth / White / Hollow) | Same setter as current `▮` button. Dropdown moves out of inline.               |
| Brightness        | `Brightness ●●●○○` glyph row, **scroll wheel anywhere over the row adjusts** | **Preserves current scroll-on-icon UX exactly** (scroll-handler relocated). Click = no-op. |
| Diagnostics       | `Diagnostics` text item, click toggles existing DiagPanel     | Same as current `🔧` button. Inline icon removed.                              |

Menu close triggers (all preserved from standard menu UX):
- click outside menu
- ESC key
- click on a non-submenu item (after action runs)
- focus blur outside menu

Keyboard nav: `↑`/`↓` move highlight, `→` opens submenu, `←`/`Esc` close,
`Enter` activates. Standard ARIA `role="menu"` + `aria-haspopup`.

### Tier 3 · Mobile reflow (`<640px`)

| Slot     | Mobile visibility            |
| -------- | ---------------------------- |
| atr/rv/cd/utc | hidden (status text row collapses) |
| replay   | **hidden** (replay is a desktop-only research workflow; not actionable on phone-class screens) |
| overflow | visible                      |
| smc-f    | visible                      |
| separator (status ↔ buttons) | hidden (collapsed) |

Result: `[ ☰ SMC F ]` on mobile. Width ≈ 80px. The `☰` menu still
contains theme/style/brightness/diag — touch-friendly. Replay remains
fully accessible on desktop and via direct URL on mobile if needed for
triage; the chrome button is just hidden.

The existing breakpoint (`<480px`) is widened to `<640px` for status row
+ replay hide. Below 480px no further change.

### Tier 4 · SMC F backend gate

The `SMC F` badge is **clickable** (toggle SMC Focus mode) — current
behavior preserved verbatim. This rev 2 does **not** redefine the click
handler.

What rev 2 does add: an explicit **degraded-but-loud signal** when SMC
compute is stalled. Backend SHOULD expose one of:

- (preferred) `frame.flags.smc_focus` — boolean current focus state
- (additional) `frame.flags.smc_compute_state` — `"ok" | "stalled" | "disabled"`

If backend signal is absent, frontend falls back to the existing local
toggle state and the badge renders without compute-state border. Adding
the signal is a separate slice (see Open §A) and does not block CR-2.5
shipping.

### Tier 5 · Width budget

| Constraint                              | Target |
| --------------------------------------- | ------ |
| Inline rail width (desktop, status row + buttons) | ≤ 300px |
| Inline rail width (mobile, buttons only)          | ≤ 120px |
| Overflow menu width                               | ≤ 240px |
| Overflow menu max-height                          | min(360px, 60vh) |
| Right-edge offset from chart price axis           | ≥ 64px (preserves Phase 3 fix from MVP) |

If inline width exceeds 300px under any locale/font, the rail is
broken — cut a status item to overflow, do not wrap.

### Tier 6 · Typography & tokens — strict

Rail uses ADR-0066 typography classes only. No inline `font-size`,
`font-weight`, `font-family`, or hex colors. Tokens used:

- `--text-1/2/3` (state text foregrounds)
- `--accent`, `--accent-soft` (gold accents, hover)
- `--bear`, `--warn` (compute-stalled / disabled state)
- `--font-mono` (status row)

Per-theme overrides resolved by ADR-0066 token system; CR-2.5 ships
zero theme conditionals.

### Tier 7 · Accessibility

- Status texts: `aria-label` with full unit (`"ATR 4.82 pips, M15"`).
- Icon buttons: `aria-label` (`"Open replay"`, `"More options"`),
  `title` for hover tooltip, visible focus ring.
- SMC F badge: `aria-pressed` reflects toggle state, `aria-label`
  (`"SMC Focus mode, currently on"`).
- Tab-order: `replay` → `overflow` → `smc-f`. Status texts skip tab
  (`tabindex="-1"`).
- Overflow menu: `role="menu"`, items `role="menuitem"` /
  `role="menuitemradio"` for theme/style.

---

## Alternatives considered

1. **Keep all icons inline (rev 1 G3 group)**
   - Faster to ship (only need to add ATR/RV/cd which MVP already did).
   - Rejected: violates the audit finding that >6 inline icons degrade
     scan time. v2 mockup explicitly removed them. Doesn't reach M4.

2. **Sliding sheet (right-edge panel) instead of dropdown menu**
   - More room for future settings; touch-friendly out of the box.
   - Rejected: 4 items + 1 brightness widget fit comfortably in a
     220px menu; sheet is over-engineering for current item count.
     Reconsider if overflow grows past 8 items.

3. **Move status text (ATR/RV/cd/UTC) into a separate row under the rail**
   - Frees inline width; cleaner per-row purpose.
   - Rejected: adds vertical chrome that competes with NarrativePanel
     banner mode for the same band of pixels (ADR-0069). One row better.

4. **Keep theme/candle inline, only collapse diag/brightness**
   - Lighter surgery (keeps muscle memory).
   - Rejected: half-measure. Either inline icons are operator-state
     scan items (ADR-0065 rev 2 semantics) or they are not. Mixed model
     is the failure mode rev 1 originally identified.

---

## Implementation plan

3 P-slices, each ≤ ~150 LOC, each with own verify gate (per K6 One
Slice = One Verify Gate).

### Slice 1 · Strip inline theme/style/diag from `App.svelte` top-right-bar

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/App.svelte` | ~30 | Remove inline theme `◐` button + dropdown wiring, candle style `▮` + dropdown, brightness icon (handler stays — moves to overflow in slice 2), diag `🔧` button. Keep state in App, expose imperative open/close handlers for slice 2 to consume. |

Verify: `npm run build` clean. Local preview shows `[ ATR · RV · cd · UTC | ☰? ▶? SMC F ]` — `☰` and `▶` not yet wired (placeholders or hidden). Theme/style/diag inaccessible (intentional gap until slice 2).

### Slice 2 · Add `CommandRailOverflow.svelte` + `▶ replay` button + `☰` overflow

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/CommandRailOverflow.svelte` (new) | ~110 | Dropdown menu. Items: Theme submenu, Style submenu, Brightness scroll-row, Diagnostics. Receives setters as props from `App.svelte`. ARIA, keyboard nav, click-outside, ESC. |
| `ui_v4/src/App.svelte` | ~40 | Mount `<CommandRailOverflow>` after `☰`. Wire `▶` button → existing `ReplayBar` open action (find existing handler, call it; do not duplicate replay state). Pass theme/style/brightness/diag setters as props. Anchor positioning logic. |

Verify: open `localhost:5173`. Click `☰` → menu opens with 4 items. Submenu hover for Theme/Style. Scroll over Brightness row → brightness changes (matches current icon scroll behavior). Click Diagnostics → DiagPanel toggles. ESC closes menu. Click `▶` → ReplayBar opens (or replay panel surfaces). `npm run build` clean. Tab order verified.

### Slice 3 · Mobile reflow `<640px`

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/App.svelte` (or `CommandRail.svelte` if status row gets extracted there) | ~25 | `@media (max-width: 640px)` rule: hide status text row + status↔buttons separator + `▶` replay button. `☰` and `SMC F` remain. |

Verify: resize devtools to 600px → status row + replay hide, `[ ☰ SMC F ]` visible. Resize to 320px → still visible. Open `☰` on mobile → menu opens, items still actionable (touch hit area ≥ 32×32). `npm run build` clean.

**Each slice ends with**: `get_errors()` zero diagnostics on touched files (K3); changelog entry with `adr_ref: "ADR-0065 rev 2 slice N"`.

---

## Consequences

**Positive**:

- Single operator surface in chrome top-right; no competing icon trays.
- Width ≈ 290px frees space for NarrativePanel banner mode (ADR-0069) without horizontal collision.
- Scan time improves: 5 inline elements vs 11 in rev 1, 7 in current MVP+ pre-relocation.
- Theme/style/brightness/diag still 1 click away (☰ → item or → submenu → option) — slight increase in click distance for low-frequency actions, acceptable trade.
- Mobile path is a real spec, not a hide-everything fallback.
- SMC F backend gate path defined (Open §A) — degraded-but-loud preserved (I5).
- F9 craftsmanship: overflow contract is declarative, no inline icon proliferation.

**Negative / accepted costs**:

- Brightness scroll UX moves from "anywhere on inline icon" to "anywhere on overflow row". Muscle memory loss for users who scroll on the icon. Mitigation: keep scroll-on-row behavior identical (no click required to focus); document in CHANGELOG.
- `▶ replay` button visible 100% of the time even when replay is irrelevant (e.g., live trading). Trade-off: discoverability vs noise. v2 mockup chose discoverability; revisit if traders complain.
- SMC F badge stays clickable — keeps a stateful element in chrome rail. This is intentional (frequent toggle), but it deviates from the otherwise pure "scan + control" model.

**Risks / monitoring**:

- Width budget overrun on locales with longer mono font glyphs (none expected for ASCII numerics). Monitor: if inline width > 300px under any condition, cut to overflow.
- Overflow menu growth past 8 items would justify Alternative 2 (sliding sheet). Add no items without checking item count.
- Backend SMC F signal not landing → fallback to local toggle is silent in the sense that compute-stalled state cannot be distinguished from "user hasn't clicked yet". Acceptable for now (Open §A), but tracks I5 as `[degraded-pending-signal]`.

---

## Open decisions (deferred — must resolve before slice 2 ships)

### §A · SMC F backend signal contract

**Question**: what backend field surfaces (a) current Focus state and (b) compute-stalled state?

Options:
1. New `frame.flags.smc_focus: boolean` + `frame.flags.smc_compute_state: "ok" | "stalled" | "disabled"`.
2. Reuse existing `meta.degraded[]` for stalled detection; keep focus state purely client-side.
3. Add to `/api/status` endpoint instead of frame.

**Recommendation**: Option 1, keeps rail rendering pure-from-frame (no extra subscription needed). Resolve before slice 2 ships, otherwise badge ships with local-only state and `[degraded-pending-signal]` marker logged.

### §B · `▶ replay` exact wiring

**Question**: which existing handler does `▶` invoke?

To verify in slice 2: `grep` for `ReplayBar.svelte` toggle / open handler. Pre-condition: handler exists and is exposed (or trivially exposable) to `App.svelte`. If neither — slice 2 splits into 2a (button + open contract) + 2b (overflow menu).

---

## Rollback

Revert per slice (each slice is one commit):

- Slice 3 revert → mobile loses status row hide, but rail still functions.
- Slice 2 revert → overflow + replay button removed; `App.svelte` retains the gap left by slice 1 (theme/style/diag still inaccessible). Slice 1 must be reverted alongside.
- Slice 1 revert → restores rev 1 MVP state (commit `e6b8497`). No data migration. No backend coordination required.

If the SMC F backend signal lands separately and rev 2 needs to be fully unwound, the signal can stay (harmless without consumer); only frontend reverts.

---

## Amendment: UX Refinements (2026-05-09)

> **Status**: IMPLEMENTED — built, verified (build `index-CAp6u945.js`).
> These decisions were locked during the session that completed slices 1+2.
> They are **invariants** — must NOT be reverted without a new ADR amendment.

### A1 · Menu stays open after item picks (non-close items)

During slice 2 implementation, the original Tier 2 spec said "click on a non-submenu item (after action runs)" closes the menu. This was revised:

| Control | Close on pick? | Rationale |
|---|---|---|
| Theme picker (Dark / Black / Light) | ❌ submenu closes only (`openSubmenu = null`), main menu stays | User needs to compare themes without re-opening menu |
| Style picker (Classic / Gray / Stealth / …) | ❌ same pattern | Same rationale |
| DisplayMode toggle (F / R) | ❌ `onClose()` NOT called | User needs to compare Focus/Research result live while menu is open |
| SMC toggle | ✅ `onClose()` IS called | SMC panel and overflow must never co-exist (see A3) |
| Diagnostics | ✅ `onClose()` IS called | Diag panel is a large surface; menu over it = noise |

**Invariant**: Theme / Style / DisplayMode picks MUST NOT call `onClose()`. Only SMC and Diagnostics toggles close the menu.

### A2 · Brightness ◀/▶ step buttons

The Tier 2 Brightness row spec (scroll wheel only) was extended with explicit step buttons:

```html
<button class="bri-step" onclick={(e) => { e.stopPropagation(); onBrightnessStep?.(-1); }}>◀</button>
<span class="brightness-leds" ...>  <!-- 5 LEDs showing current level -->
<button class="bri-step" onclick={(e) => { e.stopPropagation(); onBrightnessStep?.(1); }}>▶</button>
```

- Step size: `0.08` (range `[0.8, 1.2]` = 5 equal steps)
- Prop: `onBrightnessStep?: (direction: number) => void` added to `CommandRailOverflow` props
- `handleBrightnessStep(direction: number)` in `App.svelte`:
  ```ts
  brightness = Math.max(0.8, Math.min(1.2, +(brightness + direction * 0.08).toFixed(2)));
  saveBrightness(brightness);
  ```
- Scroll wheel (`onBrightnessWheel`) is **preserved** — both input methods coexist
- Cursor on brightness row changed to `default` (was `ns-resize` — removed, confusing on non-scroll input)

**Invariant**: Both `onBrightnessStep` (click) and `onBrightnessWheel` (scroll) MUST coexist. Neither replaces the other.

### A3 · Mutual exclusion: overflow ↔ SMC panel

Two overlapping surfaces (overflow dropdown at z=100, SMC panel at z=36) must never be open simultaneously:

**Rule 1 — Opening overflow closes SMC:**
```ts
function toggleOverflow(e: MouseEvent) {
    e.stopPropagation();
    overflowOpen = !overflowOpen;
    if (overflowOpen) smcPanelOpen = false;  // mutual exclusion
}
```

**Rule 2 — SMC toggle in overflow closes overflow:**
`CommandRailOverflow` SMC toggle item calls `onClose()` (see A1 table above).

**Invariant**: At any given moment, `overflowOpen && smcPanelOpen` MUST be `false`. Any future code path that opens one surface must close the other.

### A4 · Chart click closes overflow

The `.chart-wrapper` element has `onclick={closeOverflow}`, ensuring any click on the chart area (including candle interaction, drawing, etc.) dismisses the overflow dropdown:

```svelte
<div class="chart-wrapper" onclick={closeOverflow}>
```

`ChartPane.svelte` does NOT `stopPropagation()` on its container click, so clicks bubble up to this handler.

**Invariant**: Chart area interaction MUST close the overflow. Do not add `stopPropagation()` to `.chart-wrapper` children unless explicitly justified (and even then, add explicit `closeOverflow()` call before propagation stops).

### A5 · Updated "Tier 2 menu close triggers" (replaces original list)

The original list in this ADR ("click outside menu, ESC key, click on a non-submenu item, focus blur outside menu") is superseded by this amendment. Correct list:

```
Close overflow:
  - click outside menu (chart area, anywhere outside dropdown bounds)
  - ESC key
  - SMC toggle inside menu (→ mutual exclusion)
  - Diagnostics toggle inside menu
  - Opening SMC panel directly (☰ still visible, but overflow is closed)

DOES NOT close overflow:
  - Theme pick (Dark / Black / Light)
  - Style pick (any style)
  - DisplayMode toggle (F / R)
  - Brightness ◀ / ▶ step buttons
  - Brightness scroll wheel
  - Submenu open/close (opening a submenu does not close the parent menu)
```
