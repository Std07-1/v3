# ADR-0068: Brand Surface & Info Hub

## Metadata

| Field          | Value                                                     |
| -------------- | --------------------------------------------------------- |
| ID             | ADR-0068                                                   |
| Status         | ACCEPTED                                                  |
| Date           | 2026-05-09                                                |
| Authors        | Станіслав                                                 |
| Supersedes     | —                                                         |
| Builds on      | ADR-0066 rev 5 (tokens, mark plate `#0D1117`, T1–T5 typography) — shipped |
| Coordinates with | ADR-0065 rev 2 (frees top-right rail of theme/style/diag); ADR-0069 (NarrativePanel slot 2) |
| Affects layers | `ui_v4/` chrome (`App.svelte`, new `BrandWatermark.svelte`, rename `AboutModal.svelte` → `InfoModal.svelte`); favicon/splash assets; Telegram bot avatar; marketing OG image |

---

## Quality Axes

- **Ambition target**: R3 — first formalization of the brand surface contract; chart real-estate reclaim
- **Maturity impact**: M3 → M4 — brand placement is no longer ad-hoc per surface; one named slot per surface, brand-invariant tokens

---

## Context

The current `App.svelte` chrome puts the wordmark `АІONE / SMC` in the
top-left chrome bar (`brand-slot` ~line 511). The v2 design mockup
(`tp3_identity_full.html` §Brand placement) reframes this:

1. **Top-left chrome is the wrong location for brand.** Top-left of a
   trading chart is high-cognition real-estate — symbol, TF, price,
   bias all compete there. A wordmark there fights chart info.
2. **Chart watermark (bottom-right corner) is the canonical brand
   surface for trading platforms** (TradingView, ThinkOrSwim, NinjaTrader
   precedent). Subtle, always-on, doesn't compete with data.
3. **AboutModal is currently a single-tab modal** (About text + Credits
   open-source attribution). Diagnostics live in a separate floating
   `DiagPanel` triggered by a chrome icon (which ADR-0065 rev 2 removes
   from the rail). This produces two near-modal surfaces with no shared
   chrome. Consolidating them into one `InfoModal` with tabs unifies the
   "what is this app / how is it doing" surface.
4. **Brand mark plate must stay invariant across themes.** ADR-0066 rev 5
   established the mark plate as `#0D1117` (dark) — for **all** themes
   including light. The mark itself is a stamped artifact, not a
   theme-reactive element. This needs to be explicit (not implicit
   convention) so future theme work doesn't leak the light token in.
5. **Six brand surfaces** exist across the product (favicon, watermark,
   splash, InfoModal header, Telegram avatar, Marketing OG). They share
   one mark; they need one source of truth for placement spec.

---

## Decision

Three coordinated changes:

### Part A · Wordmark relocation (top-left chrome → bottom-right watermark)

| Surface              | Before                                          | After                                                       |
| -------------------- | ----------------------------------------------- | ----------------------------------------------------------- |
| Top-left chrome      | `АІONE / SMC` wordmark + mark plate             | Removed. Slot is empty (or repurposed by other ADRs).        |
| Chart bottom-right   | (none)                                          | `BrandWatermark.svelte` — mark plate + wordmark, opacity 0.55 idle / 0.85 hover |

`BrandWatermark.svelte` (new, ~50 LOC):
- Position: `position: absolute; bottom: 12px; right: 76px;` (clears chart price axis ≥64px per ADR-0065 rev 2 width budget convention).
- Render: ADR-0066 mark plate (`#0D1117` background, mark glyph) + wordmark `АІONE / SMC` to the right. Same composition as current `brand-slot`, scaled down ~85%.
- z-index: above chart canvas, below modals/menus.
- Pointer behavior: entire zone (mark + wordmark) is one click target. Click → opens `InfoModal` (default tab: About).
- Hover: opacity 0.55 → 0.85 over 120ms; cursor `pointer`.
- ARIA: `role="button"`, `aria-label="Open info & credits"`, focusable, `Enter`/`Space` activate.

Mobile (`<640px`): watermark stays visible but shrinks to 75% scale and shifts to `bottom: 8px; right: 12px;` (no axis to clear when chart is full-width).

### Part B · AboutModal → InfoModal with tabs

| Tab            | Content                                                                                          | Trigger                                       |
| -------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------- |
| **About**      | Existing `AboutModal` body (product name, tagline, version, "Not financial advice" disclaimer).  | Watermark click; default open tab.             |
| **Credits**    | Existing Credits content (open-source attribution, license notes — already in `AboutModal`).      | Tab switch from About; deep-link via watermark long-press (deferred). |
| **Diagnostics** | Existing `DiagPanel` content — relocated, not rewritten. Renders inside the modal tab body.      | `Ctrl+Shift+D` shortcut; ADR-0065 rev 2 overflow → Diagnostics; tab switch from About/Credits. |

File operation: rename `ui_v4/src/layout/AboutModal.svelte` →
`ui_v4/src/layout/InfoModal.svelte`. Add `<Tabs>` wrapper (Svelte
component or inline `<button role="tab">` group). Mount existing
DiagPanel content under Diagnostics tab.

**No** Settings tab. Theme / candle style / brightness live in
ADR-0065 rev 2 overflow menu. Settings would duplicate that surface and
violate the "one place per concern" maturity rule.

### Part C · Brand placement contract (6 surfaces)

| # | Surface              | Asset / spec                                                              | Mark plate background       |
| - | -------------------- | ------------------------------------------------------------------------- | --------------------------- |
| 1 | Favicon              | 32×32 + 16×16 ICO, mark glyph only (no wordmark)                          | `#0D1117` invariant         |
| 2 | Watermark★ (canonical) | `BrandWatermark.svelte`, mark plate + wordmark                          | `#0D1117` invariant         |
| 3 | Splash / loading state | Centered mark plate + wordmark, full-bleed dark background               | `#0D1117` invariant         |
| 4 | InfoModal header     | Mark plate + wordmark inline at top of modal, all 3 tabs                  | `#0D1117` invariant         |
| 5 | Telegram bot avatar  | 512×512 PNG, mark glyph only on `#0D1117` plate                           | `#0D1117` invariant         |
| 6 | Marketing OG image   | 1200×630 PNG, mark + wordmark + tagline, dark layout                      | `#0D1117` invariant         |

**Brand-invariant rule (constitutional for visual identity)**: the mark
plate `#0D1117` background and the mark glyph color do **not** react to
theme, season, A/B test, or feature flag. This is a stamped brand
artifact. ADR amendment required to change.

The wordmark text color stays per ADR-0066 (`--accent` gold for "АІONE",
`--text-1` for "/ SMC"). These read against the `#0D1117` plate
identically across themes (because the plate doesn't change).

### Part D · `Ctrl+Shift+D` redirect

Currently `Ctrl+Shift+D` (if bound) toggles the floating `DiagPanel`.

After this ADR: `Ctrl+Shift+D` opens `InfoModal` directly on
**Diagnostics** tab. The floating `DiagPanel` is removed (its content
lives only inside the modal). One-way redirect — the old shortcut
still works, just lands in the new home.

---

## Alternatives considered

1. **Keep wordmark in top-left chrome, only add subtle watermark too**
   - Less surgery; no risk of "where did the brand go" confusion.
   - Rejected: doubles the brand footprint without doubling the value.
     Top-left chrome stays as data real-estate-eater. Defeats the
     reclaim that motivated the change.

2. **Watermark in bottom-LEFT instead of bottom-right**
   - Doesn't compete with price axis; matches some platform precedents.
   - Rejected: bottom-right is the dominant precedent (TradingView et al.)
     and matches the natural F-shaped scan pattern's terminal point.
     Mockup specified bottom-right.

3. **Keep AboutModal and DiagPanel as separate surfaces**
   - Lighter migration; preserves muscle memory for `Ctrl+Shift+D` users.
   - Rejected: produces two near-modal surfaces with overlapping chrome
     concerns. Maturity regression — every new "info-ish" thing would
     need to pick a home.

4. **Make watermark theme-reactive (light plate in light theme)**
   - Brand fits visually in every theme.
   - Rejected: brand mark is a stamped artifact, not theme content.
     Reactivity here breaks brand consistency across surfaces (favicon
     can't react to user theme; OG image is pre-rendered). One rule,
     six surfaces.

5. **Add Settings tab to InfoModal**
   - Single home for all "config-ish" things.
   - Rejected: ADR-0065 rev 2 overflow menu is the home for theme/style/
     brightness. Two homes = SSOT violation in progress.

---

## Implementation plan

3 P-slices, each ≤ ~120 LOC, K6 verify gate per slice.

### Slice 1 · Rename AboutModal → InfoModal + add tab structure (no new content)

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/AboutModal.svelte` → `InfoModal.svelte` | rename | Svelte uses filename for component name; rename + update imports. |
| `ui_v4/src/layout/InfoModal.svelte` | ~50 | Wrap existing body in `<Tabs>` structure with About (default) + Credits tabs. Credits content already exists in current AboutModal — split into `<TabPanel>`. ARIA `role="tablist"`, `role="tab"`, `aria-selected`, keyboard nav (`←`/`→`/`Home`/`End`). |
| `ui_v4/src/App.svelte` | ~5 | Update import + component reference. |

Verify: open modal via existing trigger → renders with About tab active. Click Credits tab → switches. Keyboard nav works. `npm run build` clean. `get_errors()` zero on touched files.

### Slice 2 · Relocate DiagPanel content into Diagnostics tab + `Ctrl+Shift+D` redirect

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/InfoModal.svelte` | ~30 | Add Diagnostics `<TabPanel>`. Import DiagPanel content (or extract its body into a child component `DiagnosticsView.svelte` and mount in both old and new locations transitionally — see rollback). |
| `ui_v4/src/layout/DiagnosticsView.svelte` (new, optional) | ~80 | Extracted body of current DiagPanel. If extraction is non-trivial, slice 2 splits. |
| `ui_v4/src/App.svelte` | ~15 | `Ctrl+Shift+D` keybinding → opens `InfoModal` with `defaultTab="diagnostics"`. Remove floating `DiagPanel` mount. |

Verify: `Ctrl+Shift+D` → modal opens on Diagnostics tab with all current diag info present (parity test). Click Diagnostics tab from About → switches without remount loss. ADR-0065 rev 2 overflow → Diagnostics → modal opens on same tab. Floating DiagPanel no longer visible. `npm run build` clean.

### Slice 3 · BrandWatermark.svelte + remove top-left wordmark

| File | LOC est. | What |
|---|---|---|
| `ui_v4/src/layout/BrandWatermark.svelte` (new) | ~60 | Mark plate + wordmark, positioned bottom-right (desktop) / bottom-right shrunk (mobile). Click → opens InfoModal[About]. Hover opacity transition. ARIA button role. |
| `ui_v4/src/App.svelte` | ~30 | Remove top-left `brand-slot` (wordmark + mark plate at line ~511). Mount `<BrandWatermark>` over chart container. Wire click to open InfoModal. |

Verify: chart loads → watermark visible bottom-right at opacity 0.55. Hover → 0.85 + cursor pointer. Click → InfoModal opens on About. Tab to focus → focus ring visible, Enter activates. Resize <640px → watermark scales 75%, shifts. Top-left chrome shows no wordmark. `npm run build` clean. Visual parity check across all 3 themes (Dark/Black/Light) — mark plate stays `#0D1117` in light theme (constitutional check).

---

## Consequences

**Positive**:

- Top-left chrome reclaimed for trading data (symbol/TF/price/bias).
- One named brand surface per concern (6 surfaces, one rule).
- Mark plate brand-invariant rule documented constitutionally — no future drift.
- Single `InfoModal` for product info + diagnostics — fewer near-modal surfaces.
- DiagPanel relocation is content-move, not rewrite — low risk.
- `Ctrl+Shift+D` muscle memory preserved (forward-only redirect).
- F9 craftsmanship: brand placement contract is explicit, all surfaces enumerated.

**Negative / accepted costs**:

- Visual change: users notice top-left wordmark gone. Mitigation: watermark is highly visible, click-to-info is discoverable.
- Light theme will have a `#0D1117` mark plate that doesn't match the surrounding light surface. **Intentional** (brand-invariant rule). Mockup endorsed this trade — brand stamp wins over local theme harmony.
- One extra click depth for Diagnostics (`Ctrl+Shift+D` → modal vs floating panel). Acceptable; diag is developer/triage-only, not in normal hot path.

**Risks / monitoring**:

- BrandWatermark click-target overlap with chart drawing tools or HUD elements at bottom-right. Mitigation: z-index below modals, above canvas; right offset ≥64px (clears price axis); regression test: drag from inside watermark zone in chart should not start chart tool gesture (pointer event isolation).
- Marketing OG image (#6) and Telegram avatar (#5) require asset regeneration with the new mark. Out of scope for this ADR's code slices — list in CHANGELOG as separate asset task.

---

## Open decisions

None. All 4 questions raised during design were resolved in the
Decision section:

- Mark plate brand-invariant in LIGHT theme → **yes** (Part C, Part D rule).
- Settings tab in InfoModal → **no** (theme/style/brightness live in ADR-0065 rev 2 overflow).
- `Ctrl+Shift+D` behavior → **forward-only redirect** to InfoModal[Diagnostics].
- Entire watermark zone clickable → **yes** (Part A pointer behavior).

---

## Rollback

Per slice (each is one commit):

- Slice 3 revert → BrandWatermark removed; top-left wordmark restored. Modal still works (opens via existing trigger if any, or becomes inaccessible until Slice 1/2 also reverted — acceptable temporary state).
- Slice 2 revert → Diagnostics tab content removed; `Ctrl+Shift+D` reverts to opening floating DiagPanel (re-mount). InfoModal stays with About + Credits.
- Slice 1 revert → InfoModal renamed back to AboutModal; tabs removed; single-body modal restored. No data migration.

If a regression in BrandWatermark click handling or z-index emerges
after slice 3 ships, the watermark can be feature-flagged behind a
runtime check while leaving the top-left chrome empty (light revert
path), or fully reverted to restore wordmark.

Asset rollback (#5 Telegram avatar, #6 OG image) is independent — no
code coupling.
