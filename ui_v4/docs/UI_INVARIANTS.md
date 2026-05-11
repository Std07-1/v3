# UI Invariants — Locked Patterns

> **Purpose**: Single index of UI patterns that must NOT be modified without
> owner approval. Each entry points to source-of-truth file with full
> rationale + alternatives + lock criteria.
>
> **Audience**: future maintainers, refactorers, AI code agents.
>
> **Update protocol**: коли додаєш новий locked pattern → entry тут + LOCKED
> marker у самому файлі. Коли deprecate-yєш → mark як DEPRECATED date+reason,
> не видаляй entry щоб git blame не загубив context.

---

## I-UI-1: Long-press crosshair lock (mobile chart UX)

**File**: [`src/chart/longPressLock.ts`](../src/chart/longPressLock.ts)
**Wired in**: [`src/layout/ChartPane.svelte`](../src/layout/ChartPane.svelte) onMount/onDestroy
**Status**: LOCKED ✓ (owner-confirmed working 2026-05-11, Approach C)

**What**: Mobile-only — 300ms hold на чарт freeze-ить його (autoScale OFF,
LWC pan blocked у capture phase) → drag = pure crosshair drag через manual
`chart.setCrosshairPosition()`. TradingView-mobile / Binance app pattern.

**Critical invariants**:

- Capture-phase touchmove interception (NOT applyOptions toggling — LWC v5.1.0
  не реактивує handleScroll runtime для vertical pan, доведено емпірично)
- `setCrosshairPosition` на КОЖЕН touchmove (NOT subscribeCrosshairMove —
  буде throttled через LWC render queue)
- `priceScale.autoScale: false` під час lock (без цього chart стрибає
  на нових ticks)

**Why locked**: 3 ітерації знадобилось знайти робочий патерн (v1 vertTouchDrag
only, v2 +pressedMouseMove, v3 capture-phase). Заміна на applyOptions toggle
поверне баг "vertical chart moves with finger".

---

## I-UI-2: Outside-dismiss central pattern

**File**: [`src/lib/actions/dismissOnOutside.ts`](../src/lib/actions/dismissOnOutside.ts)
**Consumers** (станом на 2026-05-11):

- [`App.svelte`](../src/App.svelte) — ☰ overflow menu
- [`ChartPane.svelte`](../src/layout/ChartPane.svelte) — SMC layer panel
- [`ChartHud.svelte`](../src/layout/ChartHud.svelte) — symbol/TF dropdowns + micro-card
- [`NarrativePanel.svelte`](../src/layout/NarrativePanel.svelte) — expanded narrative

**Status**: LOCKED ✓ (owner-confirmed working 2026-05-11)

**What**: Svelte 5 action `use:dismissOnOutside={{enabled, onDismiss, ignoreSelector?}}`
що слухає `click + touchend + keydown Escape` на document і закриває
прив'язаний panel коли event поза node.

**Critical invariants**:

- **touchend listener ОБОВ'ЯЗКОВИЙ** — LWC `preventDefault()` на канвасі
  блокує синтез click event для chart taps. Без touchend dropdowns не
  закриваються від tap'у на чарт.
- `setTimeout(0)` attach guard — інакше opening click одразу закриває panel
- Однаковий API для всіх panels — ніяких ad-hoc handlers

**Why locked**: до 2026-05-11 було 4 фрагментованих реалізації
(svelte:window onclick / chart-wrapper onclick / document.addEventListener /
panel onclick + ontouchend) — drift, mobile-buggy. Один централізований
паттерн = consistent behavior + easy to extend.

---

## I-UI-3: BrandWatermark — V3 mark only

**File**: [`src/layout/BrandWatermark.svelte`](../src/layout/BrandWatermark.svelte)
**Status**: LOCKED ✓ (owner-direction 2026-05-11)

**What**: Bottom-left chart watermark показує **V3 mark only** (NOT wordmark
"AI · ONE", NOT lockup). `<Brand variant="mark" size={20} />`.

**Critical invariants**:

- Position: `bottom: 36px, left: 12px` desktop / `bottom: 30px, left: 6px`
  mobile (acima time axis labels)
- Variant: `mark` (not wordmark/lockup) — brand-internal scope, mark-only
  виглядає як generic chart attribution (discreet)
- z-index: 36 (above HUD 35, below dropdowns 100, below modals 200)

**Why locked**: brand "AI · ONE" — internal scope (owner + sponsors), не для
public mass exposure. Wordmark/lockup на чарті = реклама. Mark = neutral
attribution badge.

---

## I-UI-4: Agent pill text — neutral English, no "Арчі" mention

**File**: [`src/lib/agentState.ts`](../src/lib/agentState.ts) `compactPillText()`
**Used by**: [`NarrativePanel.svelte`](../src/layout/NarrativePanel.svelte) compact pill
**Status**: LOCKED ✓ (owner-direction 2026-05-11)

**What**: NarrativePanel compact pill показує single-word neutral English
status: `Sleeping / Watching / Analyzing / Alert / Off / Idle`. Bot's actual
thesis text passes through verbatim коли свіжий (це brand-neutral strategic
narrative, не self-introduction).

**Critical invariants**:

- НІКОЛИ не додавати "Арчі" / "Archi" / "Архи" у будь-який return string
- НІКОЛИ не експонувати internal bot details (model name, Claude, OpenAI)
- Single-word states (NOT "Bot is sleeping", NOT "Архі spить")

**Why locked**: brand "Арчі" — brand-internal scope (owner + sponsors).
Public users (free + paying tiers) бачать neutral status що виглядає як
data feed/analytics indicator, не AI-bot personality. Це comm strategy.

---

## Empirically tuned values (not locked, but documented)

Це значення які знайдено iter-tuning-ом і документовані inline для майбутніх re-tunes. Не "locked" як invariant-и вище, але якщо хочеш міняти — спочатку прочитай context.

### `.top-right-bar right:` (☰ position vs LWC price scale)

| Контекст | Value | Gap to price scale |
|---|---|---|
| Desktop / landscape phone | `67px` | ~13px |
| Mobile portrait (<640px) | `56px` | ~16px |

**File**: [`src/App.svelte`](../src/App.svelte) `.top-right-bar` CSS
**Memory**: [repo_top_right_bar_position_tuning.md](/memories/repo/repo_top_right_bar_position_tuning.md) (procedure для re-tune)

Iteration history (2026-05-11, 4 iters): 64→70→67 desktop / 44→50→54→56 mobile.
Inline comments у App.svelte містять той самий журнал.

---

## How to add a new locked pattern

1. Додай LOCKED marker блок у source file (header comment, ~10-15 lines)
2. Додай entry сюди (`UI_INVARIANTS.md`) з посиланням на файл + статус
3. Додай memory entry якщо це pattern з complex history
   (e.g. `repo_long_press_crosshair_lock.md`, `repo_ui_outside_dismiss_pattern.md`)
4. Reference у commit message: `feat(ui): lock-in crosshair pattern (I-UI-1)`
