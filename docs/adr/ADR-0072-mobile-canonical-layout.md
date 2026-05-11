# ADR-0072: Mobile Canonical Layout — Portrait + Landscape Phone Reflow

## Metadata

| Field          | Value                                                                |
| -------------- | -------------------------------------------------------------------- |
| ID             | ADR-0072                                                              |
| Status         | ACCEPTED                                                              |
| Date           | 2026-05-11                                                            |
| Authors        | Станіслав                                                             |
| Supersedes     | —                                                                     |
| Builds on      | ADR-0065 rev 2 §Tier 3 (portrait CommandRail reflow); ADR-0068 §Mobile (BrandWatermark scale); ADR-0069 §Mobile (NP sheet); ADR-0070 (desktop TR corner — extends to mobile here) |
| Affects layers | `ui_v4/index.html` (viewport + theme-color + safe-area), `ui_v4/src/App.svelte` (top-right-bar landscape rules), `ui_v4/src/layout/ChartHud.svelte` (landscape narrative/stctx hide), `ui_v4/src/chart/engine.ts` (mobile price-scale `minimumWidth:44`) |

---

## Quality Axes

- **Ambition target**: R3 — locks empirically-tuned mobile geometry (☰ position, vertical row alignment, landscape hide-list) so future agents do not re-derive on every change cycle. Fills gaps NOT covered by ADR-0065/0068/0069 (landscape phone, cross-cutting alignment).
- **Maturity impact**: M3 → M4 — mobile surface is now invariant-protected with re-measurement instructions in code comments. No more visual drift between ChartHud and CommandRail rows.

---

## Context

After ADR-0070 locked the desktop TR corner, mobile remained partially
specified across three independent ADRs (0065/0068/0069). During the
2026-05-11 mobile tuning session, multiple problems surfaced that
spanned **multiple components and could not be fixed by amending any
one of those ADRs alone**:

1. **`☰` position drift** — ADR-0065 rev 2 §Tier 3 said "keep ☰ visible
   on `<640px`" but did not specify *where*. CSS shipped with `right:12px`
   which landed ☰ directly on top of the LWC right-price-scale labels
   ("4760.00" etc). Owner feedback: "☰ вилізло й сидить на ціновій шкалі".
2. **Backdrop "пятно"** — first fix added `rgba(13,17,23,0.85)` + border
   + `backdrop-filter: blur(8px)` to make ☰ readable above price labels.
   Owner feedback: "прибери нафіг те пятно. прозоре воно має бути".
3. **Empirical price-scale width** — ChartEngine sets `minimumWidth: 44`
   on mobile; theoretical calculation gave ~50-56px (44 + label content
   stretch). Live measurement on actual device showed price scale renders
   at **~40px exactly**, not the theoretical value. `minimumWidth` is a
   floor, not actual width. ☰ position must be set from empirical
   measurement, not calc().
4. **Landscape phone uncovered** — modern phones in landscape are
   720-932px wide, well above the `<640px` (App) and `<768px` (ChartHud)
   breakpoints. Result: in landscape the FULL desktop chrome shipped
   (NP pill "Арчі вимкнений", system-narrative chip "WAIT · APPROACH",
   `.shell-stctx` "HTF bearish — шукаємо структуру для входу", ▶ replay
   button). Owner: "звідси також потрібно прибрати... арчі наратив,
   реплей та опис для системного wait".
5. **Vertical row misalignment** — in landscape, ChartHud row 1 (XAU/USD
   M15 ... WAIT) sat at content-y=7px (`.hud-stack top:1` + `.hud
   padding-top:6`) while CommandRail status row (ATR · RV · UTC) sat at
   content-y=13px (`.top-right-bar top:8` + `padding-top:5`). 6px visible
   offset. Owner: "row 1 зміщені у перевернутому стані. можеш підрівняти?"
6. **Browser address bar visual eat** — Chrome address bar
   "aione-smc.com" is its own UI strip ~70px tall above viewport. ADR-0070
   shipped `theme-color: #0D1117` + `apple-mobile-web-app-capable` to
   blend it visually with our dark bg, but full hide requires PWA
   standalone (ADR-0071 PROPOSED, separate scope).

This ADR locks the resolved mobile contract so future agents do not
re-derive past mistakes.

---

## Decision

Mobile is split into **two breakpoint regimes**, each with its own locked
behavior. Both are orthogonal — a portrait phone hits regime A only, a
landscape phone hits regime B only, a tablet hits neither (desktop
chrome applies).

### Regime A — Portrait phone (`@media (max-width: 640px)` for App.svelte; `@media (max-width: 768px)` for ChartHud)

**Why two breakpoints**: ADR-0065 rev 2 chose `640px` for App.svelte
chrome; ADR-0068/0069/ChartHud chose `768px` for HUD elements. The 128px
gap (640..768) is a tablet-portrait band where chart shows desktop chrome
but ChartHud already mobile-tunes. Documented as historical inheritance,
not re-litigated here.

#### Hidden in portrait

| Selector | File | Reason |
| -------- | ---- | ------ |
| `.tr-status-row` | App.svelte | ATR/RV/M15-cd/UTC peripheral row — too noisy on narrow screen |
| `.tr-sep-status` | App.svelte | dependent separator |
| `.tr-replay-btn` + `.tr-replay-badge` | App.svelte | replay UX is desktop-first MVP |
| `.narrative-wrap` (NP pill) | App.svelte | Архі pill deferred to ADR-0073 mobile NarrativeSheet |
| `.hud-narrative` | ChartHud | inline "WAIT · APPROACH..." — too dense for thumb-zone |
| `.shell-stctx` | ChartHud | "HTF bearish — шукаємо структуру..." stage_context text |

#### Visible in portrait

| Selector | Position | Notes |
| -------- | -------- | ----- |
| `.hud-stack` (ChartHud) | `left:2px top:0` | row 1 (symbol/price/dot/state/bias-mini) + row 2 (bias pills) |
| `.shell-stage` (label only — `.shell-stctx` hidden) | inline | stage label like CONFIRM/PREPARE remains |
| `.tr-overflow-wrap` containing `☰` | top-right via `.top-right-bar` | only chrome trigger |
| `.brand-watermark` | `bottom:30px left:6px` per ADR-0068 §Mobile | scale 75% |
| `.hud-clock` (UTC) | `position:fixed bottom:4px right:4px` | fallback for hidden CommandRail |

#### `☰` geometry (portrait — also applies as default for landscape regime B unless overridden)

```
[ chart canvas .................. ☰ ][ price scale ]
                                  ↑ │  ↑ "4712.00" labels
                                  │ 4px gap (visual breathing)
                                  44px from viewport right edge

Owner-tuned 2026-05-11:
  right: calc(44px + var(--safe-right, 0px))   /* matches engine.ts isMobile minimumWidth:44 */
  top:   calc(2px  + var(--safe-top,  0px))    /* aligns with ChartHud row 1 visual baseline */
  z-index: 40
  padding: 0 (on .top-right-bar wrapper)
```

#### `☰` button visual contract (CRITICAL — anti-"пятно")

```css
.tr-overflow-btn (mobile) {
  /* Hit-area 44×44 (Apple HIG) preserved via asymmetric padding;
     glyph anchored to top of hit-box to land on ChartHud row 1 line. */
  min-width: 44px;
  min-height: 44px;
  padding: 2px 12px 22px 12px;   /* top:2 small | bottom:22 large invisible extension */
  font-size: var(--t1-size, 18px);
  line-height: 1;
  display: inline-flex;
  align-items: flex-start;
  justify-content: center;

  /* PURE TRANSPARENT — no backdrop, no border, no blur. */
  background: transparent;
  border: 0;
  border-radius: 0;

  /* Glyph legibility above price labels via text-shadow only.
     This is NOT a backdrop — it is a 1-pixel drop shadow on the glyph itself. */
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}
.tr-overflow-btn:active {
  /* Tap feedback ONLY when pressed; idle state is invisible chrome. */
  background: rgba(255, 255, 255, 0.08);
  border-radius: 6px;
}
```

**Forbidden** (anti-drift, owner-confirmed):

- `background: rgba(13,17,23,0.85)` or any solid/semi-solid plate at idle.
- `border: 1px solid ...` at idle.
- `backdrop-filter: blur(...)` at idle.
- `border-radius: 8px` at idle (rounded plate visual).
- These were tried and rolled back as "пятно". Tap-state feedback
  (`:active`) is allowed and intentional.

### Regime B — Landscape phone (`@media (orientation: landscape) and (max-height: 500px)`)

**Why this query, not `(max-width: ...)`**: modern phones in landscape are
**720-932px wide** (iPhone 14 Pro Max landscape = 932×430). All wider than
the 640/768 portrait breakpoints. But all phones in landscape are
**under 500px tall** (typical 360-430px); no tablet is under 500px tall
(iPad Mini landscape = 744px, iPad Pro = 1024px). Catching by
`max-height: 500px` + `orientation: landscape` precisely targets
phone-landscape and excludes everything else.

#### Hidden in landscape (additional to anything Regime A would hide if it fired — but it does NOT in landscape)

| Selector | File | Reason |
| -------- | ---- | ------ |
| `.narrative-wrap` (NP pill) | App.svelte | mirrors portrait — Архі pill not on mobile |
| `.tr-replay-btn` + `.tr-replay-badge` | App.svelte | mirrors portrait — replay desktop-first |
| `.hud-narrative` | ChartHud | mirrors portrait — inline narrative chip |
| `.shell-stctx` | ChartHud | mirrors portrait — stage_context text |

#### Kept visible in landscape (DIFFERENT from portrait — horizontal real estate available)

| Selector | Why |
| -------- | --- |
| `.tr-status-row` (ATR · RV · M15-cd · UTC) | landscape phone gives ~720+ horizontal pixels; peripheral context fits cleanly. Portrait hides it because narrow screen makes it cramped. |
| `.tr-overflow-wrap` (☰) | always — only chrome trigger |

#### Vertical alignment (Regime B specific)

Landscape phone has the FULL .top-right-bar visible (not just ☰).
ChartHud row 1 and CommandRail status row MUST align on the same y line.

```
ChartHud row 1 baseline:    .hud-stack top:1 + .hud padding-top:6 = y=7
CommandRail row baseline:   .top-right-bar top:8 + padding-top:5  = y=13   ← DEFAULT, MISALIGNED
                            .top-right-bar top:1 + padding-top:6  = y=7    ← LOCKED in landscape
```

Landscape override (locked):

```css
@media (orientation: landscape) and (max-height: 500px) {
  .top-right-bar {
    top: calc(1px + var(--safe-top, 0px));
    padding: 6px 12px;   /* mirrors ChartHud .hud padding for matched baseline */
  }
}
```

### Empirical measurements (DO NOT trust theory)

| Quantity | Theoretical (calc) | Empirical (measured 2026-05-11) | Use |
| -------- | ------------------ | -------------------------------- | --- |
| Mobile right-price-scale width | 44-56px (engine.ts minimumWidth:44 + label stretch like "4724.00") | **~40px exactly** | Set `☰ right:44px` (40 + 4 gap) |
| ChartHud row 1 content y (mobile) | top:1 + padding:6 = 7 | matches ✓ | reference baseline |
| ChartHud row 2 (bias pills) y | row 1 + gap:4 + row1.height ≈ 27px | matches ✓ | — |
| Browser address bar height (Chrome Android) | varies | ~70px (above viewport, not in our box model) | `theme-color` blends; PWA standalone (ADR-0071) hides |

**Re-measurement protocol** when device or engine.ts changes:

1. Take live screenshot on target device.
2. Count pixels from the right viewport edge to where price labels start.
3. Set `.top-right-bar { right: that + 4 }` for `☰` placement.
4. If price labels visible position changes mid-session (LWC re-layout),
   the SAME pixel count is correct because LWC honors `minimumWidth` as
   a floor — content-driven width tends to land slightly above floor on
   narrow displays.

### Address bar visual contract (cross-ref ADR-0070 + ADR-0071)

Mobile browser chrome (Chrome Android, Safari iOS) shows an address bar
above our viewport. ADR-0070 shipped meta tags that **blend** it with our
dark bg:

| Meta tag | Effect |
| -------- | ------ |
| `theme-color: #0D1117` | Chrome address bar paints in our dark bg color (no longer white/grey) |
| `viewport-fit: cover` | Content extends under iPhone notch / home indicator |
| `apple-mobile-web-app-capable: yes` | iOS "Add to Home Screen" → standalone (no Safari chrome) |
| `mobile-web-app-capable: yes` | Android legacy WebView equivalent |

**Full hide of address bar** is OUT OF SCOPE here — requires PWA install
(manifest + SW). See ADR-0071 PROPOSED. Until that ships, address bar
is visually merged with our dark bg but physically still occupies its
~70px strip.

---

## Implementation reference (commits)

This ADR locks state shipped in 5 sequential commits during 2026-05-11
mobile tuning session:

| Commit    | Title                                                                          | Scope |
| --------- | ------------------------------------------------------------------------------ | ----- |
| `42da74c` | feat(mobile): chrome-bar blend + transparent price scale + ☰ on mobile         | Initial: theme-color, viewport-fit, ☰ unhidden, price-scale border off |
| `8e7d177` | fix(mobile): ☰ overlapping price scale — bump right offset 12→56px             | First position iteration (overshoot) |
| `2f39b76` | fix(mobile): ☰ overflow click-outside-to-close via document listener           | $effect doc-listener for click-outside on mobile |
| `899ba24` | fix(mobile): ☰ anchored to true viewport top-right + readable backdrop          | Backdrop "пятно" added (later rolled back) |
| `b0c2bfa` | fix(mobile): ☰ flush against price scale, transparent, row-1 aligned (final)   | Final ☰ geometry: right:44, top:2, transparent, asymmetric padding |
| `(this commit)` | feat(mobile): landscape phone reflow + row-1 vertical alignment + this ADR | Regime B + alignment fix + documentation lock |

---

## Visible delta (what trader sees)

### Portrait phone (any width <640px)

```
┌──────────────────────────────────────┐
│ [browser address bar — blended dark] │ ← theme-color #0D1117
├──────────────────────────────────────┤
│ XAU/USD M15 4661.89 ● WAIT      ☰   │ ← row 1 + ☰ flush right (44px from edge)
│ LDN KZ DISCOUNT 0% D1▼ H4▼ H1▼ M15▼  │ ← row 2 (bias pills)
│                                      │
│         [chart canvas]               │ ← maximum vertical area
│                                      │
│                                      │
│  AI·ONE                       09:38  │ ← BrandWatermark + UTC clock
└──────────────────────────────────────┘
```

NOT visible: NP pill, replay ▶, system-narrative chip, stage_context,
ATR/RV/cd/UTC peripheral row.

### Landscape phone (any width, height <500px)

```
┌────────────────────────────────────────────────────────────────────┐
│ XAU/USD M15 4661.89 ● WAIT      ATR 7.69 · RV 1.06x · UTC 09:48 ☰ │ ← row 1 ALIGNED + status row visible
│ LDN KZ DISCOUNT 0% · D1▼ H4▼ H1▼ M15▼                              │ ← row 2 (bias)
│                                                                    │
│           [chart canvas — wide horizontal]                         │
│                                                                    │
│  AI·ONE                                                            │
└────────────────────────────────────────────────────────────────────┘
```

NOT visible: NP pill, replay ▶, system-narrative chip, stage_context.
VISIBLE (vs portrait): ATR/RV/M15-cd/UTC status row.
ALIGNED: row 1 of ChartHud and CommandRail status sit on same y-baseline.

### Test cases (manual smoke after any future change in mobile chrome)

1. **Portrait phone, market open** → ☰ at top-right corner with 4px gap
   from price-scale labels; tap ☰ → menu opens; tap chart → menu closes
   (document click-outside listener).
2. **Portrait phone, scroll/pan chart** → ☰ stays in same physical
   position (it's `position: fixed` on viewport).
3. **Rotate portrait → landscape** → status row (ATR·RV·UTC) appears in
   row 1 alongside XAU/USD; NP pill stays hidden; system-narrative chip
   stays hidden; row 1 of both sides on same y.
4. **Rotate landscape → portrait** → status row hides; layout collapses
   to portrait; ☰ stays at right:44px.
5. **iOS notch device (iPhone 14+)** → ☰ shifts down by safe-area-inset-top
   so it does not overlap the dynamic island; right offset includes
   safe-area-inset-right where applicable.
6. **Tablet portrait (>640px)** → desktop layout applies; this ADR's
   media queries do NOT fire (correct: tablets handle full chrome).
7. **Tablet landscape** → desktop layout applies; `(max-height: 500px)`
   filter excludes tablets (iPad Mini landscape = 744px tall).

---

## Forbidden patterns (anti-drift checklist)

A future change suggesting any of these → STOP, read this section,
discuss before coding:

| Suggestion | Why forbidden |
| ---------- | ------------- |
| "Add `background` / `border` / `backdrop-filter` to `.tr-overflow-btn` at idle" | Owner-rejected as "пятно". Tap-state `:active` is the only allowed visual feedback. |
| "Set `☰ right` to a calc() based on engine.ts `minimumWidth`" | Empirical, not theoretical. Live measurement gives ~40px price-scale width on this device, NOT 44+. Use the measured number. |
| "Show NP pill on landscape mobile because there's room" | Архі-surface mobile contract is ADR-0073 (NarrativeSheet bottom-peek), not the desktop NP pill. Mobile NP path is an explicit different component. |
| "Show system narrative on mobile because it adds context" | ADR-0070 §Boundary + this ADR. Mobile is too dense for system narrative chips. If a mobile narrative surface is needed, ADR-0073 covers it. |
| "Use `(max-width: ...)` to detect landscape phone" | Modern phones in landscape exceed 640/768/900px wide. Use `(orientation: landscape) and (max-height: 500px)` — owner-validated geometry from this ADR. |
| "Bump `.top-right-bar top` to 8px in landscape because it looks 'too high'" | The 1px aligns with ChartHud baseline (calculated in §"Vertical alignment"). 8px misaligns by 6px. |
| "Hide CommandRail status row in landscape too (mirror portrait)" | Owner-rejected — landscape gives horizontal real estate, peripheral context fits. Portrait hide is because narrow vertical screen needs every horizontal pixel for chart. |
| "Use `vh` units instead of `--app-vh` CSS var" | TG WebView and some mobile browsers do not support `dvh`/`vh` correctly during keyboard or address-bar dynamics. `viewport.ts` `--app-vh` from `visualViewport.height` is the canonical mobile vh. |

---

## Rollback

This ADR is reversible per-section:

1. **Remove landscape regime B**: revert App.svelte and ChartHud.svelte
   `@media (orientation: landscape) and (max-height: 500px)` blocks.
   Landscape returns to showing full desktop chrome (NP pill, narrative
   chip, stage_context, replay button), and CommandRail row is misaligned
   from ChartHud row 1 by 6px again. ADR-0065 rev 2 / 0068 / 0069
   portrait behavior unaffected.
2. **Revert ☰ geometry to "пятно" experiment**: re-add `background:
   rgba(13,17,23,0.85)` + `border` + `backdrop-filter` to
   `.tr-overflow-btn`. Owner has confirmed rejection — only do this if a
   different problem demands it AND owner re-approves.
3. **Restore default `.top-right-bar` position**: remove `top:1px,
   padding:6px 12px` override in landscape block. CommandRail row drifts
   ~6px below ChartHud row 1.
4. **Full mobile rollback**: revert all 5 implementation commits + this
   ADR. Mobile returns to pre-2026-05-11 state (☰ hidden on portrait,
   browser bar grey, NP pill always visible in landscape, etc.).

No backend or contract changes anywhere — all CSS / index.html. No
data migration. No service-worker complexity (PWA is separate
ADR-0071).

---

## Notes

### Why this ADR vs amending ADR-0065/0068/0069/0070

Each of those addresses ONE component's mobile behavior:
- ADR-0065 rev 2 § Tier 3 = CommandRail portrait reflow
- ADR-0068 § Mobile = BrandWatermark scaling
- ADR-0069 § Mobile = NP sheet behavior
- ADR-0070 = desktop TR corner (no mobile)

The 2026-05-11 issues are **cross-cutting**: ☰ geometry + ChartHud
hides + CommandRail vertical alignment + landscape regime + empirical
measurements. None fits into one existing ADR without distorting that
ADR's scope. Mobile-canonical needs its own document. Same precedent
as ADR-0070 created for desktop TR corner instead of amending five
others.

### Why a separate ADR for landscape regime

Landscape phone is a distinct viewport regime, not "wider portrait":
- Hides DIFFERENT subset (NP/replay/narrative/stctx mirror portrait,
  but status row stays visible — opposite of portrait)
- Requires VERTICAL alignment fix (portrait does not — there's no
  CommandRail row to align with in portrait)
- Detection mechanism is `orientation` + `max-height`, not `max-width`
  (orthogonal to portrait detection)

Trying to express this with portrait media queries forces ugly compound
selectors. Two clean orthogonal blocks read better.

### Forward-ref to ADR-0073 (mobile Архі-surface)

Mobile NP scope was deferred from ADR-0070 to a future ADR-0073 which
will introduce a NarrativeSheet bottom-peek component. That ADR will
need to dodge:
- `.brand-watermark` at `bottom:30px left:6px` (ADR-0068 locked)
- `.hud-clock` at `bottom:4px right:4px` (this ADR)
- `☰` at top-right (this ADR)

Mobile NarrativeSheet placement strawman: bottom-peek slide-up sheet
anchored at `bottom: 60px` (clears watermark + clock) with swipe-down
dismiss. Detail not specified here — ADR-0073 will own it.

### Why `text-shadow` on `☰` glyph instead of contrast color swap

The glyph color (`var(--text-3, #8b8f9a)`) reads cleanly against dark
chart bg AND against grey-on-dark price labels. Adding a 1px drop
shadow `rgba(0,0,0,0.6)` gives just enough edge separation when the
glyph happens to sit right on top of a price label that day. Switching
glyph color per layer would require runtime computation or per-theme
override — overkill for a 1px shadow that costs nothing.
