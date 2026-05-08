# ADR-0066: Visual Identity System (AI·ONE · v3 · SMC)

## Metadata

| Field          | Value                                                    |
| -------------- | -------------------------------------------------------- |
| ID             | ADR-0066                                                 |
| Status         | PROPOSED                                                 |
| Date           | 2026-05-08                                               |
| Authors        | Станіслав                                                |
| Supersedes     | —                                                        |
| Depends on     | —                                                        |
| Soft-blocks    | ADR-0065 (Command Rail) — runs in parallel               |
| Affects layers | `ui_v4/` chrome, splash overlay, About modal, assets     |

---

## Quality Axes

- **Ambition target**: R3 (formal identity system with enforcement scale)
- **Maturity impact**: M3 → M4 (introduce SSOT for design tokens and chrome typography)

---

## Context

UI v4 (deployed at `aione-smc.com`, served by aiohttp from
`ui_v4/dist/`) currently inherits visual artifacts from its dependencies
and from incremental development:

1. **TradingView attribution logo** is rendered in the chart pane via
   `lightweight-charts` v5 default `layout.attributionLogo: true`
   (Apache 2.0 + NOTICE inheritance,
   [`engine.ts:178-209`](../../ui_v4/src/chart/engine.ts#L178-L209)).
2. **No own brand mark** — browser tab shows ad-hoc text
   `AiOne Trading — UI v4`
   ([`index.html:13`](../../ui_v4/index.html#L13)) with inconsistent
   capitalization. No favicon configured (`ui_v4/public/` contains only
   `robots.txt`).
3. **Inconsistent typographic scale** across chrome elements — `ChartHud`
   left side (symbol/pills/TF strip) and `App` top-right toolbar (theme
   pickers/clock/diag) were built independently and don't share a formal
   scale system. Inline `font-size` declarations are scattered across
   `.svelte` files. The chrome "feels" assembled from multiple
   developers' work, not designed as one surface.
4. **Ad-hoc accent color** — `#4a90d9` blue
   ([`ChartHud.svelte:754, 833`](../../ui_v4/src/layout/ChartHud.svelte#L754))
   competes with developer-tooling palette and carries no thematic
   meaning for the primary trading symbol (XAU/USD).
5. **No formal hierarchy** between brand, product, and methodology —
   these three identity layers are either absent or conflated.
6. **No design tokens SSOT** — palette is split between
   [`themes.ts`](../../ui_v4/src/chart/themes.ts) (chart-internal LWC
   options) and inline CSS in components. There is no shared CSS
   custom-property layer.

Apache 2.0 license for TradingView lightweight-charts permits removal
of `attributionLogo` if NOTICE compliance is achieved through
alternative means (an in-app Credits surface).

The platform's positioning as a proprietary SMC analytics tool with an
autonomous AI agent (Archi) requires a unified visual identity that:

- Differentiates from generic dev-tool aesthetics
- Carries XAU thematic resonance (gold accent)
- Establishes formal hierarchy between brand / product / methodology / agent
- Imposes a single typographic scale across all chrome surfaces
- Establishes a CSS-token SSOT to migrate inline styles toward

---

## Decision

Adopt a four-tier identity hierarchy with formal lockup, geometric mark,
fixed palette, and a typographic scale system enforced across all chrome.
Tokens live in a new `ui_v4/src/styles/tokens.css` (CSS custom
properties) consumed by both `themes.ts` and `.svelte` components.
Splash, About, and Credits surfaces are delivered as **in-app components**
(no router added — ui_v4 is single-page Vite + Svelte 5).

### Tier 1 · Identity hierarchy

| Tier | Element              | Visible form                                        | Lives in                                                  |
| ---- | -------------------- | --------------------------------------------------- | --------------------------------------------------------- |
| 1    | **Brand**            | `AI · ONE`                                          | wordmark — splash, About, marketing, README               |
| 2    | **Product label**    | `v3`                                                | tab title, lockup, Credits, Telegram signature            |
| 3    | **Methodology**      | `Smart Money Concepts · agent-led trading platform` | tagline under wordmark in splash/About/marketing          |
| 4    | **Agent name**       | `Archi`                                             | Telegram bot signature, future Agent Panel header         |

Lockup composition (used in Splash overlay, AboutModal header, marketing):

```text
AI · ONE          ← Inter 800, 32px, contrast rhythm
            v3    ← JBMono 500, 13px, sub-baseline aligned, color text-3
Smart Money Concepts · agent-led trading platform
                  ← JBMono 400, 11px, color text-3
```

Wordmark-only (used in marketing assets, README headers):

```text
AI · ONE
```

### Tier 2 · Wordmark spec (W-A · Classic Graphite)

| Property     | Value                                                       |
| ------------ | ----------------------------------------------------------- |
| Font         | Inter 800 (variable Inter, weight 800)                      |
| Size · canon | 32px                                                        |
| Tracking     | −1.5%                                                       |
| `AI` color   | `--text-1` (`#E6EDF3`)                                      |
| `ONE` color  | `--text-2` (`#9B9BB0`) at 75% opacity (sub-emphasis)        |
| Dot `·`      | gold (`--accent`, `#D4A017`), 7px separator on each side    |
| Baseline     | shared with `AI` and `ONE` (no offset)                      |
| Min size     | 16px (below this, use mark only)                            |

Constraints:

- Never replace `·` with hyphen, period, or any other separator — the
  gold dot is load-bearing for brand recognition.
- Never set `ONE` to same opacity as `AI` — contrast rhythm is the
  wordmark's signature.
- Never use uppercase `AI·ONE` without spaces — that breaks the
  breathing rhythm.

### Tier 3 · Mark spec (M-v3 · v2 refined)

Geometric `V3` with V as the dominant element and 3 as supplementary.
The V's geometry encodes the trend signal (V-bottom reversal); no
additional trend-line is drawn.

| Property                  | Value                                                                      |
| ------------------------- | -------------------------------------------------------------------------- |
| Composition               | V (large, dominant) + 3 (smaller, lower-right of V)                        |
| Treatment @ ≥64px         | outlined (double parallel stroke 1.4px, hollow letter)                     |
| Treatment @ 32px          | solid stroke 4px (outlined effect not legible at this size)                |
| Treatment @ 16px          | solid stroke 6px, 3 simplified to single curve (favicon-grade)             |
| Gradient @ ≥64px          | `#D4A017` (bottom-left) → `#22CC8F` (top-right)                            |
| Gradient @ <64px          | solid `#D4A017` only                                                       |
| Background plate          | `--bg` (`#0D1117`), border-radius 10px (8px @ 16px size)                   |
| Swing-low marker          | small circle, `--bull` (`#22CC8F`), at V vertex; 64+/256 only              |
| Required asset sizes      | 16, 32, 64, 256, 512                                                       |
| Required formats          | SVG (master), PNG (16/32/64/256/512), ICO (16+32 multi-page favicon)       |

V geometry coordinates (64×64 viewBox, master):

- V outer left arm: `(9, 14) → (32, 51)`
- V inner left arm: `(15, 14) → (32, 42)`
- V outer right arm: `(32, 51) → (55, 14)`
- V inner right arm: `(32, 42) → (49, 14)`
- Top connectors: `(9, 14)–(15, 14)` and `(49, 14)–(55, 14)`

3 geometry coordinates (64×64 viewBox, master):

- Top arc: `M 39 30 Q 49 30 49 35 Q 49 39 44 39`
- Bottom arc: `M 44 39 Q 49 39 49 43 Q 49 48 39 48`

### Tier 4 · Palette spec (P-A2 · Hybrid) and tokens SSOT

Palette is delivered as CSS custom properties in a new file
`ui_v4/src/styles/tokens.css` imported once from `App.svelte` (or
`main.ts`). Both `chart/themes.ts` and `.svelte` component styles
consume `var(--token-name)`. Inline color literals in `.svelte` files
are migrated incrementally per PATCH 06.

Existing colors mostly live in
[`themes.ts`](../../ui_v4/src/chart/themes.ts) and inline `style:` props
across components. The migration mapping:

| Token             | Old (current ui_v4)             | New (this ADR)       | Migration |
| ----------------- | ------------------------------- | -------------------- | --------- |
| `--bg`            | `#131722` (themes.ts dark)      | `#0D1117`            | CHANGE    |
| `--elev`          | (not defined)                   | `#161B22`            | NEW       |
| `--card`          | `rgba(30, 34, 45, 0.92)` ad-hoc | `#1C2128`            | NEW formal |
| `--border`        | `rgba(255,255,255,0.08)` ad-hoc | `#30363D`            | NEW formal |
| `--bull`          | `#26a69a` (themes.ts default)   | `#22CC8F`            | CHANGE (slight) |
| `--bear`          | `#ef5350` (themes.ts default)   | `#ED4554`            | CHANGE (slight) |
| `--accent`        | `#4a90d9` (ChartHud)            | `#D4A017` (gold)     | CHANGE    |
| `--accent-soft`   | (not defined)                   | `#B8881A`            | NEW       |
| `--text-1`        | `#d1d4dc` (themes.ts)           | `#E6EDF3`            | CHANGE    |
| `--text-2`        | `#8b8f9a` ad-hoc                | `#9B9BB0`            | CHANGE (slight) |
| `--text-3`        | `#5d6068` ad-hoc                | `#6B6B80`            | CHANGE    |
| `--text-4`        | `#45455A` (not in use)          | `#45455A`            | NEW       |
| `--info`          | (overlapped with accent blue)   | `#5487FF`            | NEW separated |
| `--warn`          | `#ff9800` (tact-session ad-hoc) | `#FFB347`            | NEW       |

Rationale: bull/bear shifts are minor (within 3% perceptual delta) — a
trader's muscle memory survives. Accent shift `blue → gold` is the
primary semantic change: gold encodes XAU thematic resonance,
differentiates from dev-tool palettes, and reads as "premium" in
institutional contexts.

`--info` is split out at `#5487FF` for non-trading info elements (dialog
accents, links) where blue is correct UI semantics — separated from the
brand accent.

### Tier 5 · Chrome typographic scale system

This is the single source of truth for every text element in chart
chrome. Every label, number, pill, and badge MUST reference one of these
tiers via tokens or utility classes. New elements added later MUST
extend this scale, not introduce ad-hoc sizes.

| Tier | Use                                                | Font                | Size  | Weight | Color           | Examples                                      |
| ---- | -------------------------------------------------- | ------------------- | ----- | ------ | --------------- | --------------------------------------------- |
| T1   | Primary numbers                                    | JetBrains Mono      | 13px  | 600    | `--text-1`      | price (`4722.58`)                             |
| T2   | Primary labels                                     | Inter               | 13px  | 600    | `--text-1`      | symbol (`XAU/USD`), state words (`WAIT`)      |
| T3   | Secondary numbers                                  | JetBrains Mono      | 11px  | 500    | `--text-2`      | price change (`−6.48 ▼`), clock (`06:48 UTC`) |
| T4   | Pills (state, session, badges)                     | JetBrains Mono      | 10px  | 600    | varies (see below) | `ASIA`, `PREMIUM 91%`, `M15`, `SMC F`         |
| T5   | Tertiary numbers and labels                        | JetBrains Mono      | 10px  | 400    | `--text-3`      | TF strip (`D1↓`), ATR/RV values, bias arrows  |

Spacing system:

| Element            | Spec                                                |
| ------------------ | --------------------------------------------------- |
| Pill height        | 18px                                                |
| Pill padding       | `1px 7px` horizontal, vertical-centered             |
| Pill border-radius | 4px                                                 |
| Icon size          | 14×14 (rendered in 26×26 click target)              |
| Row height         | 22px (single-line chrome row)                       |
| Inter-row gap      | 6px                                                 |
| Inline gap         | 8px between unrelated items, 4px within group       |
| Group separator    | 1px × 14px vertical line, color `--border`          |
| Active indicator   | 1.5px gold underbar, 6px inset from icon edges      |

Pill color rules:

| Pill semantic                     | Background                            | Text color             |
| --------------------------------- | ------------------------------------- | ---------------------- |
| Session active (ASIA, LONDON, NY) | `rgba(212, 160, 23, 0.16)`            | `--accent` (`#D4A017`) |
| Premium                           | `rgba(212, 160, 23, 0.12)`            | `--accent`             |
| Discount                          | `rgba(34, 204, 143, 0.12)`            | `--bull`               |
| State · WAIT/STAY OUT             | `rgba(255, 179, 71, 0.16)`            | `--warn` (`#FFB347`)   |
| State · READY/TRIGGERED           | `rgba(34, 204, 143, 0.16)`            | `--bull`               |
| State · WARNING                   | `rgba(237, 69, 84, 0.16)`             | `--bear`               |
| TF active pill (M15, H1, etc)     | `rgba(212, 160, 23, 0.16)`            | `--accent`             |
| Feature gate (SMC F)              | `--card` bg, `--accent` border at 40% | `--accent`             |

This pill table replaces the current ad-hoc orange (`#ff9800` in
`tact-session`, blue `#4a90d9` in active TF, mixed) — it's the single
visible color shift per token migration.

### Tier 6 · Brand placement matrix

The 11 deployment slots and what each renders:

| #  | Slot                          | Renders                                                                   | Form           |
| -- | ----------------------------- | ------------------------------------------------------------------------- | -------------- |
| 1  | Browser tab title             | `AI · ONE v3 — {SYMBOL} {TF}` (e.g., `AI · ONE v3 — XAUUSD H1`)           | text label     |
| 2  | Favicon (.ico, 16+32)         | mark M-v3, solid gold                                                     | mark only      |
| 3  | PWA app icon (256, 512)       | mark M-v3, gradient gold→bull, full outlined                              | mark only      |
| 4  | Splash overlay (StatusOverlay) | full lockup (wordmark + v3 + tagline) above warming progress             | full lockup    |
| 5  | About modal header            | wordmark + v3 + commit-hash + build-date                                  | full lockup    |
| 6  | About modal · Credits tab     | wordmark + v3 + tagline + OSS NOTICE list (TradingView, etc.)             | full lockup    |
| 7  | Chart chrome — top-left       | wordmark `AI · ONE` (replaces TV-attribution position) — clickable to open About | wordmark    |
| 8  | Chart chrome — top-right      | CR-2.5 Status row (icons + ATR/RV/cd/clock + SMC F badge); no brand       | none           |
| 9  | Telegram bot signature        | `— Archi · AI · ONE v3` (appended to every outbound message)              | compact text   |
| 10 | Telegram bot avatar           | mark M-v3, 256×256 PNG, gradient                                          | mark only      |
| 11 | Marketing / docs / README     | full lockup or wordmark only depending on context                         | full lockup or wordmark |

### Tier 7 · TradingView attribution compliance

Apache 2.0 license + NOTICE for `lightweight-charts` permits removal of
the rendered attribution logo if NOTICE is satisfied through alternative
means.

This ADR closes the obligation via:

1. New Credits tab inside the AboutModal, accessible from the wordmark
   click (top-left of chart chrome) → About → Credits tab.
2. Credits tab lists all OSS dependencies with their NOTICE/license text
   and links to source repos.
3. LWC config sets `attributionLogo: false` in
   `ui_v4/src/chart/engine.ts` constructor opts (`layout` block).

The Credits tab MUST list at minimum:

- `lightweight-charts` (TradingView, Apache 2.0) — full NOTICE text
  reproduced
- `svelte`, `vite`, `lucide-svelte` (when added per ADR-0065), and other
  production runtime deps
- A footer link to the project's own license and contact

---

## Consequences

### Visible changes

1. **Browser tab title** changes from `AiOne Trading — UI v4` to
   `AI · ONE v3 — XAUUSD H1` (or active symbol/tf).
2. **All accent-blue chrome elements** (current `#4a90d9` family in
   active TF pill, narrative trade-mode color, dropdown active-state)
   shift to gold (`#D4A017` family).
3. **Background** deepens slightly (`#131722 → #0D1117`) — perceptible
   only side-by-side; not disruptive in normal use.
4. **TradingView "T" logo** disappears from chart pane.
5. **Splash overlay** appears during initial WS warming, replacing the
   current bare `StatusOverlay` text — new lockup with wordmark + v3 +
   tagline + warming progress.
6. **Wordmark** appears in chart top-left (currently empty); clickable
   to open AboutModal.
7. **About modal** (new): wordmark + version line + build-date +
   Credits tab.
8. **Favicon and PWA icon** changed from default to M-v3 mark.
9. **Telegram bot avatar** updated to M-v3 mark.

### NOT touched

- Chart layer rendering pipeline (UDS, candle/volume series, SMC overlay
  logic)
- Thesis Bar structure and copy
- TF strip directional arrows logic
- Pills logic (only color tokens shift)
- Replay button position and behavior
- Symbol selector logic
- Any `core/` or `runtime/` code — this ADR touches only `ui_v4/` and assets
- Archi (`smc_trader_v3`) operation — agent does not depend on UI tokens

### Asset deliverables

New files to create under `ui_v4/public/brand/`:

```text
ui_v4/public/brand/
├── wordmark.svg                       (W-A canonical, 32px height baseline)
├── lockup-full.svg                    (W-A + v3 label + tagline)
├── mark-v3.svg                        (master, 64×64 viewBox)
├── mark-v3-16.png                     (16×16, solid gold, favicon)
├── mark-v3-32.png                     (32×32, solid gold)
├── mark-v3-64.png                     (64×64, outlined gradient)
├── mark-v3-256.png                    (256×256, outlined gradient, PWA)
├── mark-v3-512.png                    (512×512, outlined gradient, PWA)
├── favicon.ico                        (multi-page: 16+32)
└── splash-bg.svg                      (optional decorative bg for splash)
```

Existing files modified:

- `ui_v4/src/styles/tokens.css` (NEW) — CSS custom properties SSOT
  (palette + typography + spacing) per Tier 4–5
- `ui_v4/src/main.ts` — import `./styles/tokens.css` at top
- `ui_v4/src/chart/themes.ts` — refactor color literals to read from
  `getComputedStyle(document.documentElement).getPropertyValue('--*')`
  with fallbacks
- `ui_v4/src/chart/engine.ts` — add `attributionLogo: false` to chart
  layout opts (constructor block ~line 178-209)
- `ui_v4/index.html` — minimal title `AI · ONE v3` + favicon link
  (dynamic symbol/tf appended via App.svelte `$effect`)
- `ui_v4/src/App.svelte` — wire wordmark in HUD top-left, AboutModal
  trigger, dynamic tab title `$effect`, import tokens.css

New components:

- `ui_v4/src/layout/Brand.svelte` (NEW) — wordmark + lockup variants,
  reusable
- `ui_v4/src/layout/AboutModal.svelte` (NEW) — modal with header
  (lockup) and tabs (About / Credits)
- `ui_v4/src/data/oss-notices.ts` (NEW) — NOTICE text data for Credits
  tab

Splash extension:

- `ui_v4/src/layout/StatusOverlay.svelte` (MODIFY) — when
  `status === 'warming'` or `'connecting'`, render Brand lockup above
  the warming progress bar; otherwise current behavior preserved.

PWA manifest (optional, per PATCH 03):

- `ui_v4/public/manifest.webmanifest` (NEW) — references mark-v3-256
  and mark-v3-512
- `ui_v4/index.html` — `<link rel="manifest" href="/manifest.webmanifest">`

---

## Implementation order

This ADR is the gate for PATCH 02 → PATCH 03 → PATCH 04 → PATCH 06.
Each PATCH respects the project's patching process: ≤150 LOC per patch,
≤1 file primary scope where possible, full changelog entry, exit gates
run after.

| # | PATCH    | Scope                                                                          | LOC est. | Files         | Verify |
| - | -------- | ------------------------------------------------------------------------------ | -------- | ------------- | ------ |
| — | ADR-0066 | This document                                                                  | doc      | adr/          | review |
| 1 | PATCH 02 | `tokens.css` creation + `themes.ts` consumes tokens + import wired in `main.ts` | ~80   | 3             | medium |
| 2 | PATCH 03 | Asset deployment (mark + wordmark SVG/PNG + favicon + manifest), `Brand.svelte` component | ~140 | 6–8         | medium |
| 3 | PATCH 04 | `AboutModal.svelte` + Credits tab (oss-notices.ts) + wordmark click wiring + StatusOverlay splash extension | ~150 | 4–5 | medium |
| 4 | PATCH 05 | `attributionLogo: false` + dynamic tab title + favicon link in index.html      | ~30      | 2             | small  |
| 5 | PATCH 06 | Chrome scale system enforcement (typography audit and refactor — apply T1–T5 across all chrome via tokens) | ~150 | 3–5 | large |

ADR-0065 (Command Rail CR-2.5) is parallel and depends on PATCH 02
(gold token must exist before CR-2.5 references it).

---

## Verification

### Per-PATCH verify

**PATCH 02 (tokens):**

1. Visual diff `localhost:8000` before/after — pills shift orange/blue →
   gold; bg slightly deeper.
2. No broken token references — grep `var(--accent)` across `ui_v4/src/`
   resolves; computed styles in DevTools show resolved values.
3. Bull/bear candles render identically (themes.ts color resolution
   path verified).
4. Rollback check: revert tokens.css + themes.ts changes, app reverts
   cleanly.

**PATCH 03 (assets):**

1. Browser tab shows new favicon (16×16 mark visible at favorites bar).
2. PWA install (Add to Home Screen) shows mark, not default — when
   manifest is added.
3. `Brand.svelte` renders wordmark at three sizes (16/32/canonical)
   without layout shift.
4. SVG assets pass aXe accessibility check (alt text present where
   applicable).

**PATCH 04 (AboutModal + Splash):**

1. Wordmark click in HUD top-left opens AboutModal with focus trap.
2. AboutModal shows new header (lockup + version line + build-date).
3. Credits tab lists all OSS deps with NOTICE text reproduced verbatim.
4. Splash overlay renders during WS warming with Brand lockup above
   warming progress; disappears on `status === 'live'`.
5. Esc and backdrop-click close AboutModal.

**PATCH 05 (attribution + tab title):**

1. TradingView "T" logo no longer rendered in chart pane.
2. Browser tab title format matches `AI · ONE v3 — {SYMBOL} {TF}` for
   all symbol/TF combinations; updates on switch.
3. Favicon link in index.html resolves to `/brand/favicon.ico`.

**PATCH 06 (typography enforcement):**

1. All chrome text elements reference T1–T5 token classes (no inline
   `font-size`/`font-weight`).
2. Visual diff: chrome looks coherent, no orphan typography.
3. Zoom test (browser zoom 75%, 100%, 125%, 150%) — chrome scales
   gracefully.
4. Markdown lint and stylelint clean on touched files.

### Exit gates

After each PATCH:

```sh
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

Five known-FAIL gates (`preview_not_on_disk`,
`preview_plane/api_splitbrain`,
`ui_live_candle_plane/overlay_anchor_sentinel`,
`htf_available/allowlist_htf`, `unexpected_gap_budget`) are pre-existing
and do not block this ADR — verify status quo (they remain FAIL but no
new FAILs introduced).

### Cross-cutting checks

- No `core/` or `runtime/` files touched (this ADR is UI-only).
- No new contract changes — `core/contracts/public/marketdata_v1/` untouched.
- No UDS API changes — bar reads/writes unchanged.
- `changelog.jsonl` entries committed for every PATCH.
- `CHANGELOG.md` synchronized.

---

## Rollback

Rollback is per-PATCH. The ADR itself is reversible by reverting the
five PATCHes in reverse order (PATCH 06 → 05 → 04 → 03 → 02). No data
migration is involved (this is purely visual + asset + component additions).

| PATCH    | Rollback method                                                                           |
| -------- | ----------------------------------------------------------------------------------------- |
| PATCH 02 | `git revert {commit}` — `tokens.css` + `themes.ts` + `main.ts` revert                     |
| PATCH 03 | `git revert {commit}` — remove asset files; `Brand.svelte` revert                         |
| PATCH 04 | `git revert {commit}` — remove `AboutModal.svelte`, `oss-notices.ts`, restore StatusOverlay |
| PATCH 05 | `git revert {commit}` — `attributionLogo: false` reverts; restore tab-title; remove favicon |
| PATCH 06 | `git revert {commit}` — restores pre-T1-T5 inline styles                                  |

Full ADR rollback (all five PATCHes): single
`git revert --no-commit {commit-list}` then commit. Estimated rollback
time: 5 minutes.

---

## Notes

### Future work (not in this ADR)

- **ICT methodology variant.** If the platform later supports ICT (Inner
  Circle Trader) methodology, the tagline shifts to
  `Inner Circle Trader · agent-led trading platform`. The brand
  (AI·ONE) and product (v3) stay; only Tier 3 changes. This is a
  config-level change, not a re-brand.
- **Color theme system.** P-A3 Deep Premium variant could be exposed as
  a user-toggle in Settings ("Deep mode") — a separate ADR.
- **Mark animation.** A subtle motion variant of M-v3 (V drawing in,
  then 3 fade-in) for splash screen — animation ADR if pursued.
- **Light-mode theme.** Currently only dark theme exists. Light theme
  would require a separate token set; defer until/unless requested.
- **Standalone /credits page.** If SEO or external linking demand
  arises, the Credits tab content can be promoted to a static
  `ui_v4/public/credits.html` served by aiohttp at `/credits`. Until
  then, the modal tab is sufficient for NOTICE compliance.
- **Router introduction.** ADR-0066 deliberately stays single-page. If a
  future ADR introduces SvelteKit or a hash router, the
  Splash/About/Credits surfaces can be migrated to routes — the
  components themselves remain reusable.

### Why this is reversible-friendly

The five PATCHes are independently revertible because:

1. Token migration (PATCH 02) does not modify any data — only color
   references; themes.ts retains literal fallbacks for safety.
2. Assets (PATCH 03) are additive — old assets remain in git history,
   no existing assets removed.
3. AboutModal + Splash (PATCH 04) is additive component code; old
   StatusOverlay path is preserved when status is not warming.
4. Attribution + tab title (PATCH 05) is a single config flag and a tab
   title formatter; no entanglement.
5. Typography enforcement (PATCH 06) is the most invasive but applies
   only to existing chrome elements; rolling back gives back inline
   styles.

If at any point the brand decision changes, no contract was broken, no
data migrated, no agent state lost.

### Why this does not affect Archi

This ADR touches only `ui_v4/` (UI layer B) and assets. The agent
(`smc_trader_v3`) does not depend on UI tokens, chrome typography, or
branding. Archi continues operating against the same `core/` contracts,
same `/api/` endpoints, same UDS. Telegram signature update
(`— Archi · AI · ONE v3`) is a config-level change in
`adr002_directives.py` formatter (or equivalent), ≤5 LOC, can be batched
with PATCH 03 or done independently.
