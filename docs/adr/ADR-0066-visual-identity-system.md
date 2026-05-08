# ADR-0066: Visual Identity System (AI·ONE · v3 · SMC)

## Metadata

| Field          | Value                                                    |
| -------------- | -------------------------------------------------------- |
| ID             | ADR-0066                                                 |
| Status         | PARTIALLY IMPLEMENTED (rev 4 — 2026-05-08; PATCH 02a/b/c shipped, 02d / 02e / 03 / 04 / 05 / 06a / 06b pending; rev 4 absorbs exploration-board GAP-1–6 + crosshair/focus/disabled gaps as scope extensions — no new slices) |
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

## Reality Audit (post-PATCH-02c, 2026-05-08)

This ADR was originally drafted by multiple agents without grounding in the
actual `ui_v4/` source. This section is the honest snapshot of where reality
stands vs the spec, recorded after PATCH 02a/b/c shipped. Future agents:
**read this before editing the ADR or starting any PATCH 03+ work.**

### What landed (DONE)

| Slice    | Commit    | Files                             | What it actually did                                                                                       |
| -------- | --------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| PATCH 02a | `c7f8428` | `tokens.css`, `main.ts`            | Created `ui_v4/src/styles/tokens.css` (palette + typography + spacing tokens). Imported once via `main.ts`. **Zero visual change** — no consumers yet. |
| PATCH 02b | `f0689b1` | `themes.ts`                        | Mirrored `:root` palette into `THEMES.dark` and rewired `applyThemeCssVars` to set `<html data-theme="...">`. Gold accent visible on toolbar in **dark theme via `applyThemeCssVars` only** (see Engine constructor trap below). |
| PATCH 02c | `03a201f` | `themes.ts`                        | Mirrored palette into `THEMES.black` (gold accent, harmonized grid/text) and `THEMES.light` (`--accent-soft` `#B8881A` for WCAG AA on white). Brand consistency across all 3 themes. |

### Files that still hold legacy color literals (NOT MIGRATED)

Grep evidence at audit time (search across `ui_v4/src/`):

| File                                | Line(s)            | Literal              | Will be addressed by                  |
| ----------------------------------- | ------------------ | -------------------- | ------------------------------------- |
| `chart/engine.ts`                   | 138-148            | `#d5d5d5`, `rgba(43,56,70,0.4)`, `rgba(213,213,213,0.35)` | **PATCH 02d** (Engine constructor — see trap below) |
| `chart/drawings/DrawingsRenderer.ts` | 771, 855          | `#3d9aff`            | PATCH 06b (color migration sweep)     |
| `layout/ChartHud.svelte`            | 21 (prop default)  | `#d1d4dc`            | PATCH 06b                             |
| `layout/ChartPane.svelte`           | 768, 781, 867, 908 | `#4a90d9`            | PATCH 06b                             |
| `layout/SymbolTfPicker.svelte`      | 79, 89, 93, 94     | `#d1d4dc`, `#4a90d9`, `#4a90d950` | PATCH 06b                  |
| `layout/StatusBar.svelte`           | 136                | `#d1d4dc`            | PATCH 06b                             |
| `layout/ReplayBar.svelte`           | 188                | `#d1d4dc`            | PATCH 06b                             |
| `layout/StatusOverlay.svelte`       | 279, 288           | `#4a90d9`, `#d1d4dc` | PATCH 06b                             |
| `layout/DiagPanel.svelte`           | 235                | `#d1d4dc`            | PATCH 06b                             |
| `index.html`                        | 24-25 (inline `<style>`) | `#131722`, `#d1d4dc` | PATCH 05 (touches `index.html` already) |

### Critical trap — Engine constructor bypass (S1, blocks dark-mode parity)

`chart/engine.ts:184` reads:

```ts
const savedTheme = loadTheme();
if (savedTheme !== 'dark') this.applyTheme(savedTheme);
```

Default dark users **never trigger `applyTheme`** → constructor's hardcoded
`textColor: '#d5d5d5'` and `'rgba(43, 56, 70, 0.4)'` grid (lines 138-148)
remain in effect. PATCH 02b changed `THEMES.dark.chart.textColor` to
`#9B9BB0` and grid to `'rgba(48, 54, 61, 0.6)'` — but those values **only
reach the chart if user toggled away from dark and back, or for non-default
users**. Toolbar gold accent works via `applyThemeCssVars` (separate path,
called unconditionally from `App.svelte`), which is why the 02b ship
appeared partially correct.

**Fix is mandatory before PATCH 06b**: PATCH 02d either (a) calls
`applyTheme(savedTheme)` unconditionally, or (b) replaces constructor
literals with `THEMES.dark.chart.*` values. Option (b) preferred — single
source of truth, no double-init flicker.

### Other spec drifts found vs reality

1. **Tier 6 slot 7** ("top-left replaces TV-attribution position"): LWC v5
   renders `attributionLogo` **bottom-right** of the chart pane, not
   top-left. Wordmark slot at chart top-left is still a valid placement
   choice, but it doesn't "replace" anything geometrically. Spec corrected
   below.
2. **`themes.ts` carries 6 fields not in the ADR**: `pdBadgeDiscountBg/Text`,
   `pdBadgePremiumBg/Text`, `pdBadgeEqBg/Text`, `pdEqLineColor`. These
   belong to ADR-0041 (P/D Badge + EQ line) and are explicitly
   **out of scope** for ADR-0066. They will be migrated to tokens only
   when ADR-0041 is amended; this ADR does not touch them.
3. **`tokens.css` self-comment is stale** (lines 84-95 of the file): says
   black/light overrides land in PATCH 02b. Actual decision (recorded
   here): black/light live in `themes.ts` palette mirror because LWC
   `applyOptions` cannot read CSS variables. `[data-theme="..."]`
   selectors in `tokens.css` are deferred until non-LWC components need
   them (i.e., PATCH 06 typography sweep, when chrome `.svelte` reads
   tokens). Comment will be updated as part of PATCH 06.
4. **PATCH 02 originally one-shot** in the spec; actually shipped as
   three sub-slices (02a / 02b / 02c) for K6 one-slice-one-verify
   discipline. Updated in the implementation table below.
5. **Asset folder** (`ui_v4/public/brand/`) does not exist yet —
   `ui_v4/public/` contains only `robots.txt`. No favicon, no PWA
   manifest. Untouched until PATCH 03.
6. **No `Brand.svelte`, no `AboutModal.svelte`, no `oss-notices.ts`** in
   `ui_v4/src/`. All PATCH 03/04 deliverables remain to be built.
7. **`CANDLE_STYLES` ignores theme** (audit 2026-05-08, post-rev-2). The
   table is `Record<CandleStyleName, CandleStyleDef>` — single palette
   per style, applied identically on dark / black / light. Cross-product
   (3 themes × 5 styles = 15 cells) has multiple visibility failures:
   `stealth` is invisible on dark/black (dark grays on dark bg),
   `white` up-bodies are invisible on light (`#e2e5e9` on `#FFFFFF`),
   `gray` down-borders read at ~3.5:1 contrast on `black` (WCAG AA fails
   for 4.5:1 UI element threshold), `hollow` up-bodies are transparent
   (works on dark/black showing bg, marginal on light). This was missed
   during ADR-0007 / Entry 078 because only one dark theme existed at
   that time. Addressed by **PATCH 02e** (new Tier 8 below) — explicit
   amendment to ADR-0007 scope handled in this ADR because it's the
   theme-system that surfaced the failure.
8. **Crosshair literal in engine constructor not in original PATCH 02d scope**
   (audit 2026-05-08, post-rev-3). `engine.ts:84-94` hardcodes
   `vertLine.color: 'rgba(213,213,213,0.35)'` and same for `horzLine`.
   On `light` theme this renders near-white-on-white — crosshair becomes
   invisible. Originally rev 2 listed this in the same `engine.ts:138-148`
   bundle but only called out `textColor` and `gridLines`. Rev 4 explicitly
   extends PATCH 02d scope to include `crosshair.vertLine.color` and
   `crosshair.horzLine.color` reads from `THEMES[name].chart.crosshair.*`
   (themes table must carry these fields per theme; light gets a darker
   variant such as `rgba(60,60,80,0.35)` or `var(--text-3)` alpha-applied).
9. **Pill `rgba(212,160,23,0.16)` ignores light background** (audit 2026-05-08,
   exploration board). Pill bg opacity tuned for dark/black; on `light`
   the gold-on-white pill (ASIA / PREMIUM / TF active / state) renders at
   ~1.4:1 vs `#FFFFFF` — user cannot see active state. Same applies to
   `#FAFBFC` off-white. Resolution: opacity table per theme (see Tier 4
   amendment block below) — dark/black keep `0.16`, light bumps to `0.22`
   with text shifted to `--accent-deep` (`#8E6A12`). Closed in PATCH 06b.
10. **Light bg pure `#FFFFFF` is hostile to hollow/white candles and to
    rounded corners** (exploration board, 2026-05-08). Pure white gives
    harsh glow on `border-radius` and zero contrast for the few candle
    cells that already sit at the bright end of the spectrum. Decision:
    light bg shifts from `#FFFFFF` to **off-white `#FAFBFC`** (GitHub-style),
    keeps WCAG ratios on `--accent-soft` (`#B8881A` on `#FAFBFC` ≈ 5.0:1,
    still passes AA). Closed in PATCH 06b along with token table refresh
    (`--bg` light row and `[data-theme="light"]` selector in `tokens.css`).

### Pre-rev-4 exploration board observations now part of spec

A standalone HTML exploration board (rendered 3 themes × brand elements
side-by-side, 15-cell candle matrix, pill/state previews per theme) was
produced 2026-05-08 between rev 3 and rev 4 to ground the amendment in
actual visual evidence. Findings translated into:

- Drifts #8–#10 above (crosshair, pill opacity, off-white bg)
- Tier 4 amendment block (pill opacity per theme + active indicator + focus + disabled)
- NEW Tier 9 (Brand Element Theme Adaptation — mark plate, Brand.svelte tokens, splash 3-theme audit)
- Verify-section extensions for PATCH 02d / 02e / 03 / 04 / 06b

Board is not committed (one-shot artifact); the decisions it produced
live only in this ADR. Future agents starting work on PATCH 03/04/06b
**read the rev 4 sections, not the board**.

### Where the engine + index.html stand on PATCH 05 prerequisites

- `engine.ts` does **not** yet pass `attributionLogo: false` — TV "T" logo
  still renders bottom-right.
- `index.html:13` title is still `AiOne Trading — UI v4`. No favicon link.
  No dynamic `$effect` in `App.svelte` updating the tab title from
  `currentPair`.

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

Existing colors lived in three places before this ADR:
[`themes.ts`](../../ui_v4/src/chart/themes.ts) (chart-internal LWC options),
[`engine.ts:138-148`](../../ui_v4/src/chart/engine.ts#L138-L148) (constructor
hardcoded fallbacks), and inline `<style>` blocks across `.svelte` components.
The migration mapping (verified against grep at PATCH 02c ship):

| Token             | Pre-ADR sources (verified)                                                                          | Spec (this ADR)         | Status post-02c                              |
| ----------------- | --------------------------------------------------------------------------------------------------- | ----------------------- | -------------------------------------------- |
| `--bg`            | `#131722` (themes.ts dark old, `index.html:24` inline)                                              | `#0D1117`               | tokens ✅ · themes.ts dark ✅ · index.html ❌ |
| `--elev`          | (not defined)                                                                                       | `#161B22`               | tokens ✅ · themes.ts dark ✅                 |
| `--card`          | `rgba(30, 34, 45, 0.92)` ad-hoc menus                                                               | `#1C2128`               | tokens ✅ · themes.ts dark ✅                 |
| `--border`        | `rgba(255,255,255,0.08)` and `rgba(43,56,70,0.4)` (engine.ts:144) mixed                              | `#30363D`               | tokens ✅ · themes.ts dark ✅ · engine.ts ❌ |
| `--bull`          | `#26a69a` (themes.ts CANDLE_STYLES.classic, kept)                                                    | `#22CC8F`               | tokens ✅ · candle styles **NOT migrated** (intentional — different ADR scope) |
| `--bear`          | `#ef5350` (themes.ts CANDLE_STYLES.classic, kept)                                                    | `#ED4554`               | tokens ✅ · candle styles **NOT migrated** (intentional) |
| `--accent`        | `#4a90d9` (ChartHud, ChartPane, SymbolTfPicker, StatusOverlay), `#3d9aff` (DrawingsRenderer, DrawingToolbar default) | `#D4A017` (gold)        | tokens ✅ · themes.ts toolbarActiveColor ✅ · `.svelte` literals ❌ |
| `--accent-soft`   | (not defined)                                                                                       | `#B8881A`               | tokens ✅ · themes.ts light ✅                |
| `--text-1`        | `#d5d5d5` (engine.ts:140), `#d1d4dc` (themes.ts old + ChartHud/StatusBar/ReplayBar/DiagPanel/SymbolTfPicker/StatusOverlay/index.html) | `#E6EDF3`               | tokens ✅ · themes.ts dark ✅ · engine.ts ❌ · 7 `.svelte` files ❌ |
| `--text-2`        | `#8b8f9a` ad-hoc                                                                                    | `#9B9BB0`               | tokens ✅ · themes.ts dark chart.textColor ✅ |
| `--text-3`        | `#5d6068` ad-hoc                                                                                    | `#6B6B80`               | tokens ✅                                     |
| `--text-4`        | `#45455A` (not in use)                                                                              | `#45455A`               | tokens ✅                                     |
| `--info`          | (overlapped with accent blue)                                                                       | `#5487FF`               | tokens ✅                                     |
| `--warn`          | `#ff9800` (tact-session ad-hoc)                                                                     | `#FFB347`               | tokens ✅                                     |

Legend: ✅ = migrated and live, ❌ = literal still in code, addressed by
PATCH 02d (engine.ts) or PATCH 06b (`.svelte` sweep) or PATCH 05 (index.html).

**Out of scope for this ADR** (do not migrate without separate amendment):

- `themes.ts` `pdBadge*` and `pdEqLineColor` fields (6 entries) — owned by
  ADR-0041 (P/D Badge + EQ line).
- `themes.ts` `CANDLE_STYLES` **bull/bear semantic shift** (e.g.,
  `#26a69a → #22CC8F`) — owned by ADR-0007 / Entry 078; would change
  candle body colors and break trader muscle memory across symbols.
  Keep separate.
- **EXCEPTION (PATCH 02e, Tier 8 below)**: theme-aware adaptation of
  existing `CANDLE_STYLES` IS in scope here, because theme×style
  visibility failures (stealth on dark, white on light, gray on black)
  are a direct consequence of this ADR introducing 3 themes against a
  table that assumed 1. Adapting palettes to render correctly per
  theme is **not** a semantic shift — bull stays "green-ish", bear
  stays "red-ish"; only luminance is rebalanced per background.
  Trader muscle memory is preserved.

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

#### Tier 4 amendment (rev 4) — Per-theme opacity, active indicator, focus, disabled

The pill table above gives single opacity values tuned for dark. On light
(and slightly on black) those values either disappear or over-shout. The
following per-theme overrides land via `[data-theme="..."]` selectors in
`tokens.css` and via the candle/volume resolver from Tier 8 (volume alpha
shares the same per-theme curve).

**Pill background opacity per theme** (closes drift #9, GAP-2):

| Pill semantic              | dark `#0D1117` | black `#000000` | light `#FAFBFC`                       |
| -------------------------- | -------------- | --------------- | ------------------------------------- |
| Session active             | `0.16`         | `0.20`          | `0.22` bg + text → `--accent-deep`    |
| Premium                    | `0.12`         | `0.16`          | `0.18` bg + text → `--accent-deep`    |
| Discount                   | `0.12`         | `0.16`          | `0.18` bg + text → `--bull-deep`      |
| State · WAIT/STAY OUT      | `0.16`         | `0.18`          | `0.20` bg + text → `--warn-deep`      |
| State · READY/TRIGGERED    | `0.16`         | `0.20`          | `0.22` bg + text → `--bull-deep`      |
| State · WARNING            | `0.16`         | `0.18`          | `0.20` bg + text → `--bear-deep`      |
| TF active pill             | `0.16`         | `0.20`          | `0.22` bg + text → `--accent-deep`    |
| Feature gate (SMC F)       | `--card` + 40% border | `--card` + 40% border | `--card` + 50% border         |

**Why semi-solid + darker text on light** (GAP-2 decision rationale): pill
shape parity is preserved across themes (user sees "the same pill, only
darker"), which beats reverse-text (gold-on-white) when 3–4 pills sit
in a row (TF strip) — reverse-text becomes visually noisy at row scale.

**Deep accent tokens added (`tokens.css`)**:

| Token            | Value      | Purpose                                                  |
| ---------------- | ---------- | -------------------------------------------------------- |
| `--accent-deep`  | `#8E6A12`  | Pill text on light theme (gold family, AA on `#FAFBFC`)  |
| `--bull-deep`    | `#178A5B`  | Discount/READY pill text on light                         |
| `--bear-deep`    | `#B53641`  | WARNING pill text on light                                |
| `--warn-deep`    | `#A8732A`  | WAIT/STAY OUT pill text on light                          |

Deep tokens are light-only by default; on dark/black the existing
`--accent` / `--bull` / `--bear` / `--warn` continue to be used for pill
text (sufficient contrast against semi-transparent gold/green/red bg).

**Active indicator pattern** (closes GAP-6):

| Theme   | Pattern                                                                         |
| ------- | ------------------------------------------------------------------------------- |
| dark    | 1.5px gold underbar (`--accent`), 6px inset from icon edges                     |
| black   | 1.5px gold underbar (`--accent`), 6px inset — same as dark                       |
| light   | 1.5px deep-gold underbar (`--accent-deep`), 6px inset — contrast against `#FAFBFC` |

Underbar is the single canonical active indicator across all themes
(toolbar buttons, theme picker, candle-style picker, TF active tab in
header). No filled-bg active state — underbar reads as "this is
selected" without competing with content.

**Focus ring per theme** (closes GAP-8, keyboard a11y):

| Theme   | Focus ring                                                                     |
| ------- | ------------------------------------------------------------------------------ |
| dark    | `0 0 0 2px var(--accent)` (gold on near-black, ~6.8:1)                         |
| black   | `0 0 0 2px var(--accent)` (gold on pure-black, ~7.4:1)                         |
| light   | `0 0 0 2px var(--accent-deep)` (deep-gold on `#FAFBFC`, ~5.0:1)                |

Focus ring uses `outline-offset: 1px` so it sits clear of pill borders
without overlapping adjacent elements. Applied via `:focus-visible`
(not `:focus`) so mouse clicks don't trigger ring.

**Disabled state opacity per theme** (closes GAP-9):

| Theme   | Disabled opacity | Notes                                                  |
| ------- | ---------------- | ------------------------------------------------------ |
| dark    | `0.40`           | Current behavior — unchanged                            |
| black   | `0.45`           | Slight bump — pure-black makes 0.40 read as too dim    |
| light   | `0.55`           | Higher — 0.40 on `#FAFBFC` washes out, 0.55 reads disabled-but-present |

**Crosshair color per theme** (closes drift #8 / GAP-7, scope of PATCH 02d):

| Theme   | `crosshair.vertLine.color` and `horzLine.color`            |
| ------- | ---------------------------------------------------------- |
| dark    | `rgba(213,213,213,0.35)` — unchanged                       |
| black   | `rgba(213,213,213,0.40)` — slight bump on pure black       |
| light   | `rgba(60,60,80,0.40)` — dark-on-light for visibility        |

These fields are added to `THEMES[name].chart.crosshair.*` in `themes.ts`
and consumed by PATCH 02d (engine constructor parity slice).

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
| 7  | Chart chrome — top-left       | wordmark `AI · ONE` — clickable to open About. **Note**: TV `attributionLogo` renders bottom-right in LWC v5 (not top-left); wordmark placement here is independent. | wordmark    |
| 8  | Chart chrome — top-right      | CR-2.5 Status row (icons + ATR/RV/cd/clock + SMC F badge); no brand       | none           |
| 9  | Telegram bot signature        | `— Archi · AI · ONE v3` (appended to every outbound message)              | compact text   |
| 10 | Telegram bot avatar           | mark M-v3, 256×256 PNG, gradient                                          | mark only      |
| 11 | Marketing / docs / README     | full lockup or wordmark only depending on context                         | full lockup or wordmark |

### Tier 8 · Theme × Candle Style Adaptation (PATCH 02e)

**Problem.** The current `CANDLE_STYLES` table in
[`themes.ts`](../../ui_v4/src/chart/themes.ts) is
`Record<CandleStyleName, CandleStyleDef>` — one palette per style,
applied to all themes. With 3 themes (`dark` `#0D1117`, `black`
`#000000`, `light` `#FFFFFF`) × 5 styles
(`classic` / `gray` / `stealth` / `white` / `hollow`) = 15 cells, the
following combinations break visibility or contrast:

| Style ↓ / Theme → | dark `#0D1117` | black `#000000` | light `#FFFFFF` |
| ----------------- | -------------- | --------------- | --------------- |
| `classic`         | ✅              | ✅               | ✅               |
| `gray`            | ⚠️ down border weak | ❌ down border `#5f6368` ≈ 3.5:1 (WCAG AA fail for UI) | ✅               |
| `stealth`         | ❌ dark grays on dark bg, candles invisible | ❌ candles fully invisible | ✅               |
| `white`           | ✅              | ✅               | ❌ up `#e2e5e9` on `#FFFFFF` ≈ 1.06:1, bodies invisible |
| `hollow`          | ✅              | ✅               | ⚠️ up border `#26a69a` on white reads ~2.4:1, marginal |

**Decision.** Restructure `CANDLE_STYLES` to a 2D resolver indexed by
`(styleName, themeName)`, with a default fallback per style. Keep
public API of `applyCandleStyle(name)` unchanged; resolution against
current theme happens inside `themes.ts`. `applyTheme(name)` MUST
re-apply the current candle style after switching theme so candle
palette refreshes.

Proposed shape (illustrative, final form decided in PATCH 02e):

```ts
type CandleStyleByTheme = {
  default: CandleStyleDef;                          // fallback per style
  dark?:  Partial<CandleStyleDef>;                  // overrides per theme
  black?: Partial<CandleStyleDef>;
  light?: Partial<CandleStyleDef>;
};
export const CANDLE_STYLES: Record<CandleStyleName, CandleStyleByTheme> = {
  classic: { default: { upColor: '#26a69a', downColor: '#ef5350', /* … */ } },
  // styles below override per theme where the default fails:
  gray:    { default: { /* current gray */ }, black: { borderDownColor: '#9aa0a6' } },
  stealth: { default: { upColor: '#3a3f44', downColor: '#1f2327', /* … */ },
             dark:    { upColor: '#7c8189', downColor: '#5d6268', /* … */ },
             black:   { upColor: '#7c8189', downColor: '#5d6268', /* … */ } },
  white:   { default: { upColor: '#e2e5e9', downColor: '#2f3338', /* … */ },
             light:   { upColor: '#cfd2d6', downColor: '#2f3338', /* … */ } },
  hollow:  { default: { /* current hollow */ },
             light:   { borderUpColor: '#1a8580', wickUpColor: '#1a8580',
                        borderDownColor: '#c7423f', wickDownColor: '#c7423f' } },
};

export function resolveCandleStyle(
  style: CandleStyleName, theme: ThemeName,
): CandleStyleDef {
  const entry = CANDLE_STYLES[style];
  return { ...entry.default, ...(entry[theme] ?? {}) };
}
```

**Engine wiring** (changes in `engine.ts` PATCH 02e):

1. `applyCandleStyle(name)` reads `resolveCandleStyle(name, this.currentTheme)`
   instead of `CANDLE_STYLES[name]` directly.
2. `applyTheme(name)` calls `this.applyCandleStyle(this.currentCandleStyle)`
   at the end — so theme switch refreshes candle palette using the new
   theme's overrides. (Volume colors auto-refresh via existing
   `_refreshVolumeColors()`.)
3. Constructor init: PATCH 02d already standardizes constructor to read
   `THEMES.dark.chart.*`; same slice extends to read
   `resolveCandleStyle(savedStyle, savedTheme)` instead of literal
   `CANDLE_STYLES.classic`.

**Per-cell luminance contract** (acceptance threshold, WCAG-informed):

- Candle body or border vs background: **≥ 3.0:1** for graphical objects
  (WCAG 1.4.11). Each adapted cell verified with contrast checker.
- Up-color vs down-color must remain perceptually distinct
  (≥ 25 ΔE) — preserves bull/bear at-a-glance discrimination.
- Bull stays in green family (hue 140–180°), bear in red family
  (hue 0–20° or 340–360°). No semantic crossing.

**Why not the 3 alternatives:**

- **Option A — Drop incompatible** (filter picker per theme): would
  surface `(2 themes × 1 style) + (1 theme × 1 style) = 3 missing
  cells` to user, looks like a bug rather than a feature.
- **Option B — Auto-swap** (force `classic` if incompatible): magical,
  user loses their style choice on theme switch. Toast disclaimer
  doesn't fix the loss of intent.
- **Option D — Edit literals only** (raise stealth grays globally):
  fixes stealth on dark by breaking it on light. Whack-a-mole; no
  invariant restored.

Option C (theme-aware variants) is the only one that **structurally**
makes the table sound: every (theme, style) cell is now a first-class
coordinate, with explicit overrides where the default fails. Adding a
4th theme later means filling N more `?:` overrides — no surface
breakage, no picker filtering, no auto-swap surprise.

**Why this does not break ADR-0007 / Entry 078:** the public API
(`applyCandleStyle(name)`, persisted user choice in localStorage,
style-name semantics) is preserved. The change is internal to
`themes.ts`. Trader-visible behavior: when toggling theme, candles
rebalance luminance to stay readable on the new background — which
is the trader-correct behavior anyway (the failure mode it replaces
is "candles disappear when I switch to dark").

**Volume alpha shares the same resolver** (closes GAP-5, rev 4): the
`engine.ts` `_volUpColor` / `_volDownColor` setters are extended to read
`_withAlpha(resolveCandleStyle(name, theme).upColor, VOLUME_ALPHA_BY_THEME[theme])`
 where `VOLUME_ALPHA_BY_THEME = { dark: 0.32, black: 0.36, light: 0.42 }`.
Light theme needs higher alpha so volume bars don't ghost out against
off-white. Single resolver, no duplication — ~3 LOC extension to PATCH 02e
rather than a new slice.

### Tier 9 · Brand Element Theme Adaptation (rev 4)

Dark/black/light themes affect not only chart pane but every brand-bearing
surface: wordmark, mark, lockup, splash. Rev 4 codifies the per-theme
behavior so PATCH 03/04 land coherently across themes rather than
shipping as "dark works, light is sticker-ish".

#### 9.1 Wordmark color per theme (GAP-3, Brand.svelte token enforcement)

| Element     | dark `#0D1117`        | black `#000000`        | light `#FAFBFC`             |
| ----------- | --------------------- | ---------------------- | --------------------------- |
| `AI`        | `var(--text-1)`       | `var(--text-1)`        | `var(--text-1)` (dark text) |
| `ONE`       | `var(--text-2)` 75%   | `var(--text-2)` 75%    | `var(--text-2)` 75%         |
| dot `·`     | `var(--accent)`       | `var(--accent)`        | `var(--accent-deep)`        |
| tagline     | `var(--text-3)`       | `var(--text-3)`        | `var(--text-3)`             |
| `v3` label  | `var(--text-3)`       | `var(--text-3)`        | `var(--text-3)`             |

**Brand.svelte token enforcement (GAP-3 closure)**: `ui_v4/src/layout/Brand.svelte`
MUST consume tokens via `var(--*)` only — zero hex literals. PATCH 03 verify
includes `grep -E '#[0-9a-fA-F]{3,8}' ui_v4/src/layout/Brand.svelte` returns
zero hits. Same rule for `lockup-full.svg` if rendered as inline SVG with
`currentColor` (sets fill via parent `color: var(--text-1)`).

#### 9.2 Mark plate strategy (GAP-1, Option B — adaptive)

Three options were considered (board section 01):

- **A. Static dark plate everywhere** — mark always sits on `#0D1117`
  rounded plate. On light theme this looks like a sticker glued onto
  white — breaks the visual integration.
- **B. Adaptive plate** — chosen. On dark/black: no plate (transparent
  bg, mark sits directly on theme bg). On light: subtle plate using
  `--surface-1` + 1px `--border-mute` border, `border-radius: 10px`
  matching mark spec. Plate provides necessary visual anchor on light
  without sticker effect.
- **C. No plate ever** — mark loses readability on light at small sizes
  (32px and below) because gradient `#D4A017 → #22CC8F` lacks contrast
  against `#FAFBFC` at thin strokes.

**Plate spec per theme** (Brand.svelte renders this):

| Theme   | Plate background          | Plate border                    | Border-radius |
| ------- | ------------------------- | ------------------------------- | ------------- |
| dark    | none (transparent)        | none                            | n/a           |
| black   | none (transparent)        | none                            | n/a           |
| light   | `var(--surface-1)`        | `1px solid var(--border-mute)`  | 10px          |

Where `--surface-1` and `--border-mute` are NEW tokens added in PATCH 06b
(or moved earlier into PATCH 02d if Brand.svelte ships before 06b):

| Token            | dark      | black     | light       | Purpose                              |
| ---------------- | --------- | --------- | ----------- | ------------------------------------ |
| `--surface-1`    | `#161B22` | `#0A0A0A` | `#FFFFFF`   | Subtle elevated surface (plate, card)|
| `--border-mute`  | `#21262D` | `#1A1A1A` | `#E1E4E8`   | Soft border (plate edge, dividers)   |

Mark gradient itself (`#D4A017 → #22CC8F` per Tier 3) is unchanged across
themes — the gradient is the brand signature; only the plate underneath
adapts.

#### 9.3 Splash overlay readability per theme (GAP-4)

Splash overlay (`StatusOverlay.svelte` extended in PATCH 04) renders the
full lockup over a dimmed chart bg during WS warming. Per theme:

| Theme   | Backdrop                            | Lockup contrast                                   |
| ------- | ----------------------------------- | ------------------------------------------------- |
| dark    | `rgba(13,17,23,0.85)` (var of bg)   | wordmark `--text-1` reads at >12:1 — strong       |
| black   | `rgba(0,0,0,0.88)`                  | wordmark `--text-1` reads at >18:1 — strongest    |
| light   | `rgba(250,251,252,0.92)`            | wordmark `--text-1` (dark) reads at >10:1 — strong |

Warming progress bar uses `--accent` on dark/black, `--accent-deep` on
light (same pattern as pill text and active indicator). Tagline always
at `--text-3` opacity — stays subordinate to wordmark across all themes.

PATCH 04 verify includes screenshot diff of splash on all 3 themes (one
failure cell = slice not done).

#### 9.4 Tier 9 promotion criteria

If the brand surface grows beyond what Tier 9 covers (e.g., per-symbol
mark variants, seasonal/event branding, animated splash variants per
theme, AR/embed contexts), promote to standalone **ADR-0067 — Brand
Adaptation System**. Until then, Tier 9 is the SSOT. Triggers for
promotion: (a) >5 themes, (b) seasonal mark variants needed, (c) per-symbol
mark/wordmark deviations, (d) embed-target adaptation (e.g., Telegram
WebApp dark/light forced).

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

| #  | PATCH     | Scope                                                                                                              | LOC est. | Files | Verify | Status |
| -- | --------- | ------------------------------------------------------------------------------------------------------------------ | -------- | ----- | ------ | ------ |
| —  | ADR-0066  | This document                                                                                                      | doc      | adr/  | review | rev2 (this audit) |
| 1  | PATCH 02a | Create `tokens.css` SSOT (palette + typography + spacing). Import once via `main.ts`. Zero consumers, zero visual delta. | ~95   | 2     | small  | ✅ shipped `c7f8428` |
| 2  | PATCH 02b | Mirror tokens into `THEMES.dark` (`themes.ts`) + rewire `applyThemeCssVars` to set `<html data-theme>`. Toolbar accent → gold via CSS-var path. | ~30   | 1     | small  | ✅ shipped `f0689b1` |
| 3  | PATCH 02c | Mirror tokens into `THEMES.black` and `THEMES.light` (`--accent-soft` for WCAG AA on white). Brand consistency across 3 themes. | ~35   | 1     | small  | ✅ shipped `03a201f` |
| 4  | **PATCH 02d** | **Engine constructor parity fix** (rev 4 expanded scope): replace `engine.ts:138-148` hardcoded `#d5d5d5` / `'rgba(43,56,70,0.4)'` AND `engine.ts:84-94` `crosshair.vertLine.color` / `horzLine.color` `'rgba(213,213,213,0.35)'` literals with `THEMES.dark.chart.*` references; add `crosshair.vertLine.color` + `horzLine.color` fields to `THEMES[dark|black|light].chart.crosshair` per Tier 4 amendment table. Removes split-brain between constructor defaults and `THEMES.dark` AND closes drift #8 (crosshair invisible on light). **Mandatory before PATCH 06b**. | ~25 | 2 | small | ⏳ pending |
| 4b | **PATCH 02e** | **Theme × candle adaptation matrix** (Tier 8) + **per-theme volume alpha** (GAP-5): restructure `CANDLE_STYLES` in `themes.ts` to `Record<CandleStyleName, CandleStyleByTheme>` with `resolveCandleStyle(style, theme)` resolver; rewire `engine.ts` `applyCandleStyle()` to use resolver and `applyTheme()` to re-apply candle style on theme switch. Adds per-theme overrides for `stealth`/`white`/`gray`/`hollow` so all 15 cells pass ≥3.0:1 contrast. Volume alpha extended to `VOLUME_ALPHA_BY_THEME` (dark 0.32, black 0.36, light 0.42) sharing the same resolver — no duplicate function. **Depends on PATCH 02d** (shared constructor refactor). | ~95 | 2 | medium | ⏳ pending |
| 5  | PATCH 03  | Asset deployment (mark + wordmark SVG/PNG + favicon + manifest), `Brand.svelte` component. Creates `ui_v4/public/brand/`. | ~140  | 6–8   | medium | ⏳ pending |
| 6  | PATCH 04  | `AboutModal.svelte` + Credits tab (`oss-notices.ts`) + wordmark click wiring + `StatusOverlay` splash extension.   | ~150  | 4–5   | medium | ⏳ pending |
| 7  | PATCH 05  | `attributionLogo: false` in `engine.ts` chart layout opts + dynamic tab title `$effect` in `App.svelte` + favicon link + bg/color tokens in `index.html` inline `<style>`. | ~30   | 3     | small  | ⏳ pending |
| 8  | **PATCH 06a** | Typography enforcement in chrome `.svelte` (apply T1–T5 via `var(--t*-size)` / `var(--t*-weight)` / `var(--font-sans|mono)` across `ChartHud`, `StatusBar`, `DrawingToolbar`, `ChartPane`, `SymbolTfPicker`, `ReplayBar`, `DiagPanel`, `StatusOverlay`). | ~150  | 3–8   | large  | ⏳ pending |
| 9  | **PATCH 06b** | Color literal sweep in `.svelte` and `DrawingsRenderer.ts`: replace remaining `#4a90d9` / `#3d9aff` / `#d1d4dc` with `var(--accent)` / `var(--text-1)` (and add `[data-theme="black"]` / `[data-theme="light"]` selector overrides in `tokens.css` for surfaces that need theme-specific tints). Closes the migration table. | ~120 | 8–10  | large  | ⏳ pending |

ADR-0065 (Command Rail CR-2.5) is parallel and was soft-blocked on the
gold token (`--accent`) existing — that landed at PATCH 02b (`f0689b1`),
so ADR-0065 PATCH 07 is unblocked.

**Slice ordering rationale**: PATCH 02d MUST land before PATCH 06b. If
.svelte components migrate to `var(--text-1)` while `engine.ts`
constructor still emits `#d5d5d5` to LWC for default-dark users, the
chart's price-axis text would read `#d5d5d5` while every other chrome
element reads `#E6EDF3` — visible drift on the same surface.

---

## Verification

### Per-PATCH verify

**PATCH 02a (tokens scaffold) — ✅ verified at ship:**

1. `ui_v4/src/styles/tokens.css` exists with `:root` block per Tier 4–5.
2. `main.ts` imports `./styles/tokens.css` (single line at top).
3. `npx vite build` succeeds; bundle size delta minimal.
4. Visual: zero change vs pre-02a baseline (no consumers yet).

**PATCH 02b (themes.ts dark mirror) — ✅ verified at ship:**

1. `THEMES.dark` palette matches tokens (textColor `#9B9BB0`, grid `rgba(48,54,61,0.6)`, toolbarActiveColor `#D4A017`).
2. `applyThemeCssVars` sets `<html data-theme="dark">` before CSS-var setters.
3. Toolbar gold accent visible after page reload (CSS-var path active).
4. Chart palette unchanged for default-dark users — **trap discovered, scheduled as PATCH 02d**.

**PATCH 02c (themes.ts black + light mirror) — ✅ verified at ship:**

1. `THEMES.black` and `THEMES.light` palettes mirror tokens; gold on all 3 themes (`--accent` on dark/black, `--accent-soft` on light).
2. WCAG AA contrast verified: `#B8881A` on `#FFFFFF` text passes (light); `#D4A017` on `#0D1117` and `#000000` passes (dark/black).
3. Switch theme via picker; toolbar accent + status bar tint shift correctly.

**PATCH 02d (Engine constructor parity) — ⏳ pending verify (rev 4 expanded):**

1. `engine.ts:138-148` constructor no longer carries `#d5d5d5` / `'rgba(43,56,70,0.4)'` literals.
2. `engine.ts:84-94` constructor no longer carries `'rgba(213,213,213,0.35)'` for crosshair vertLine/horzLine; reads from `THEMES.dark.chart.crosshair.*`.
3. `THEMES[dark|black|light].chart.crosshair.{vertLine,horzLine}.color` fields exist per Tier 4 amendment table.
4. Default-dark user reload: chart price-axis text renders at `#9B9BB0`, grid at `rgba(48,54,61,0.6)` (DevTools computed style on canvas inspector).
5. Switching theme to black/light/back-to-dark produces the same chart palette as initial load (no double-init flicker).
6. **Crosshair visibility on light theme**: hover chart on `light` — crosshair lines visible (dark-on-off-white per Tier 4 amendment), not invisible white-on-white.
7. `npx tsc --noEmit` clean.

**PATCH 02e (Theme × candle adaptation) — ⏳ pending verify:**

1. `CANDLE_STYLES` shape changed to `Record<CandleStyleName, CandleStyleByTheme>` with `default` + optional per-theme overrides; `resolveCandleStyle(style, theme)` exported and unit-callable.
2. `engine.ts` `applyCandleStyle(name)` reads `resolveCandleStyle(name, this.currentTheme)`; `applyTheme(name)` invokes `this.applyCandleStyle(this.currentCandleStyle)` at end.
3. **15-cell visual matrix audit** (3 themes × 5 styles, screenshots in PR):
   - `stealth × dark`: candle bodies clearly visible against `#0D1117`; up/down distinguishable.
   - `stealth × black`: same vs `#000000`.
   - `white × light`: up bodies (`#cfd2d6`) clearly visible against `#FFFFFF`.
   - `gray × black`: down border ≥3.0:1 against `#000000`.
   - `hollow × light`: up borders (`#1a8580`) ≥3.0:1 against `#FFFFFF`.
   - All other 10 cells: no regression vs PATCH 02c baseline.
4. Theme switch (e.g., dark → light) with `stealth` selected: candles immediately re-render with light-theme palette, no manual style re-pick required.
5. Volume bars per theme switch: `_refreshVolumeColors()` produces alpha-derived volume colors matching the theme-resolved candle AND the per-theme `VOLUME_ALPHA_BY_THEME` value (dark 0.32, black 0.36, light 0.42); shared resolver, no duplicate function (GAP-5).
6. localStorage round-trip: persisted style+theme on reload renders identically to in-session switch.
7. `npx tsc --noEmit` clean; no `any` introduced in resolver.

**PATCH 03 (assets) — ⏳ pending verify (rev 4 expanded):**

1. Browser tab shows new favicon (16×16 mark visible at favorites bar).
2. PWA install (Add to Home Screen) shows mark, not default — when manifest is added.
3. `Brand.svelte` renders wordmark at three sizes (16/32/canonical) without layout shift.
4. SVG assets pass aXe accessibility check (alt text present where applicable).
5. **Brand.svelte token enforcement (GAP-3)**: `grep -E '#[0-9a-fA-F]{3,8}' ui_v4/src/layout/Brand.svelte` returns zero hits. All colors via `var(--*)`.
6. **Mark plate per theme (GAP-1, Tier 9.2)**: render Brand.svelte mark variant on all 3 themes — dark/black show no plate (transparent), light shows `--surface-1` plate with `--border-mute` border at 10px radius. No "sticker" appearance on light.
7. Wordmark color shifts per theme per Tier 9.1 table (dot is `--accent` on dark/black, `--accent-deep` on light).

**PATCH 04 (AboutModal + Splash) — ⏳ pending verify (rev 4 expanded):**

1. Wordmark click in HUD top-left opens AboutModal with focus trap.
2. AboutModal shows new header (lockup + version line + build-date).
3. Credits tab lists all OSS deps with NOTICE text reproduced verbatim.
4. Splash overlay renders during WS warming with Brand lockup above warming progress; disappears on `status === 'live'`.
5. Esc and backdrop-click close AboutModal.
6. **Splash 3-theme audit (GAP-4, Tier 9.3)**: trigger splash on dark / black / light themes — lockup contrast ≥5:1 against backdrop on all three; warming progress bar uses `--accent` on dark/black, `--accent-deep` on light; tagline at `--text-3` opacity, subordinate to wordmark on all three.
7. AboutModal renders correctly on all 3 themes (no theme-specific layout breakage).

**PATCH 05 (attribution + tab title + index.html cleanup) — ⏳ pending:**

1. TradingView "T" logo no longer rendered bottom-right of chart pane.
2. Browser tab title format matches `AI · ONE v3 — {SYMBOL} {TF}` for all symbol/TF combinations; updates on switch.
3. Favicon link in `index.html` resolves to `/brand/favicon.ico`.
4. `index.html` inline `<style>` no longer hardcodes `#131722` / `#d1d4dc` — reads tokens via `var(--bg)` / `var(--text-1)` (loaded via `main.ts` before HTML paints? — if FOUC, accept and document, or inline a minimal token stub in `<head>`).

**PATCH 06a (typography enforcement) — ⏳ pending:**

1. All chrome text elements reference T1–T5 token classes (no inline `font-size` / `font-weight` literals).
2. Visual diff: chrome looks coherent, no orphan typography.
3. Zoom test (browser zoom 75%, 100%, 125%, 150%) — chrome scales gracefully.
4. `npx tsc --noEmit` clean.

**PATCH 06b (color literal sweep) — ⏳ pending verify (rev 4 expanded):**

1. `grep -E '#4a90d9|#3d9aff|#d1d4dc' ui_v4/src/` returns zero hits.
2. Theme-switch test: hover a drawing on each theme; selection accent matches theme's `toolbarActiveColor` (gold/gold-soft), not stale `#3d9aff`.
3. `[data-theme="black"]` / `[data-theme="light"]` selectors in `tokens.css` override surfaces that need theme-specific tints.
4. `tokens.css` self-comment updated to reflect the actual decision (black/light layered via `[data-theme]` here for non-LWC; LWC continues to read `themes.ts` mirror).
5. **Pill opacity per theme (Tier 4 amendment, GAP-2)**: Inspect ASIA / PREMIUM / TF active / state pills on all 3 themes — background opacity matches Tier 4 amendment table; light theme pill text reads `--accent-deep` / `--bull-deep` / `--bear-deep` / `--warn-deep`, contrast ≥4.5:1 vs pill bg.
6. **Active indicator pattern (GAP-6)**: 1.5px underbar on dark/black is `--accent`; on light is `--accent-deep`. No filled-bg active state.
7. **Focus ring per theme (GAP-8)**: tab through toolbar with keyboard — focus ring visible on all 3 themes (`--accent` 2px on dark/black, `--accent-deep` 2px on light, `outline-offset: 1px`). Triggered only by `:focus-visible`, not mouse click.
8. **Disabled state opacity (GAP-9)**: disabled toolbar buttons render at 0.40 on dark, 0.45 on black, 0.55 on light — reads as disabled-but-present on all three.
9. **Light bg shift to off-white (drift #10)**: `--bg` light token = `#FAFBFC` (not `#FFFFFF`); `index.html` inline `<style>` for light theme matches; rounded corners no longer give harsh glow.
10. **Deep accent tokens land**: `--accent-deep`, `--bull-deep`, `--bear-deep`, `--warn-deep`, `--surface-1`, `--border-mute` exist in `tokens.css` per Tier 4 amendment + Tier 9.2 tables.

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
shipped PATCHes in reverse order. No data migration is involved (this
is purely visual + asset + component additions).

| PATCH      | Status     | Rollback method                                                                           |
| ---------- | ---------- | ----------------------------------------------------------------------------------------- |
| PATCH 02a  | ✅ shipped | `git revert c7f8428` — removes `tokens.css` + `main.ts` import. Zero visible delta.       |
| PATCH 02b  | ✅ shipped | `git revert f0689b1` — `themes.ts` dark + `applyThemeCssVars` revert.                     |
| PATCH 02c  | ✅ shipped | `git revert 03a201f` — `themes.ts` black + light revert.                                  |
| PATCH 02d  | ⏳ pending | `git revert {commit}` — restore `engine.ts` constructor literals.                         |
| PATCH 02e  | ⏳ pending | `git revert {commit}` — restore flat `CANDLE_STYLES` shape and engine direct-lookup. Style choice persisted in localStorage stays; resolver vanishes; visibility regressions return on broken cells. Safe — public API preserved across revert. |
| PATCH 03   | ⏳ pending | `git revert {commit}` — remove asset files; `Brand.svelte` revert                         |
| PATCH 04   | ⏳ pending | `git revert {commit}` — remove `AboutModal.svelte`, `oss-notices.ts`, restore StatusOverlay |
| PATCH 05   | ⏳ pending | `git revert {commit}` — `attributionLogo: false` reverts; restore tab-title; remove favicon link; restore `index.html` literals |
| PATCH 06a  | ⏳ pending | `git revert {commit}` — restores pre-T1–T5 inline styles                                  |
| PATCH 06b  | ⏳ pending | `git revert {commit}` — restores pre-sweep color literals + removes `[data-theme]` overrides |

Full ADR rollback (all shipped PATCHes): single
`git revert --no-commit c7f8428 f0689b1 03a201f` then commit.
Estimated rollback time: 5 minutes.

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
  *(Update 2026-05-08: light theme already exists per PATCH 02c; this
  bullet is stale and slated for removal in next ADR cleanup pass.)*
- **Candle style system promotion.** PATCH 02e adapts the existing 5
  styles to 3 themes via per-theme overrides inside `themes.ts`. If the
  catalog grows beyond ~8 styles or ~5 themes, the resolver becomes a
  larger surface deserving its own ADR. Triggers: (a) a third group of
  users (e.g., colorblind preset) demands its own style family;
  (b) candle palette starts encoding signal semantics (e.g., grade-tinted
  candles per ADR-0029); (c) introduction of per-symbol candle defaults
  (XAU vs BTC distinct palettes). Until any of those, Tier 8 in this ADR
  is sufficient SSOT.
- **Brand adaptation system promotion.** Tier 9 (rev 4) covers wordmark
  + mark plate + splash adaptation across 3 themes. If brand surface
  grows beyond this (per-symbol mark variants, seasonal/event branding,
  animated splash per theme, embed-target adaptation), promote to
  standalone **ADR-0067 — Brand Adaptation System**. Triggers per Tier
  9.4. Until then, Tier 9 in this ADR is sufficient SSOT.
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
