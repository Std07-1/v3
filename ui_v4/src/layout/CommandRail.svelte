<!--
  CommandRail.svelte — peripheral trader context inline in top-right-bar.
  Slots (left → right):
    [ATR(14) of current TF]    ← backend SSOT, NO frontend recompute (X28)
    [↻ countdown to bar close] ← display arithmetic (wallclock + tf bucket)

  ADR refs:
    - ADR-0065 rev 2 (CR-2.5 final layout)
    - ADR-0066 Tier 4 (gold accent), Tier 5 (typography tokens)
    - X28 invariant: frontend MUST NOT re-derive backend SSOT.
      ATR previously computed locally from candles — REMOVED. Now reads
      `frame.atr` shipped by ws_server (engine.get_atr, same as REST
      /api/context.atr).

  REMOVED (X28 violation):
    - ATR compute from candles[] (was: SMA of TR over last 14)
    - RV (relative volume) cell entirely — no backend equivalent yet,
      separate ADR + backend slice needed before re-introducing.

  Countdown is intentionally retained in UI: it's wallclock subtraction
  (next_bar_close_ms - now_ms), not domain compute. Anchor map mirrors
  backend bucket_start convention (D1=22:00 UTC, H4=23:00 UTC, others=0).

  Out of scope:
    - SMC F badge — needs backend feature gate signal
    - Mobile reflow — desktop-first MVP (hidden via @media <600px)
-->
<script lang="ts">
  type Props = {
    /** ATR(14) for current symbol+tf, sourced from backend (frame.atr).
     *  null when no frame yet OR frame missing the field (legacy server). */
    atr: number | null;
    /** TF label ("M15", "H1", ...) — converted to seconds via inverse map. */
    currentTf: string;
    /** Live wallclock from parent $state (ticks every 1s). */
    nowMs: number;
  };

  const { atr, currentTf, nowMs }: Props = $props();

  // ─── TF label → seconds (inverse of App.svelte _S_TO_LABEL) ────────────
  const _LABEL_TO_S: Record<string, number> = {
    M1: 60,
    M3: 180,
    M5: 300,
    M15: 900,
    M30: 1800,
    H1: 3600,
    H4: 14400,
    D1: 86400,
  };
  const tfS = $derived(_LABEL_TO_S[currentTf] ?? 0);
  const tfMs = $derived(tfS * 1000);
  const tfLabel = $derived(currentTf || "—");

  // ─── Bucket anchor per TF (mirrors backend resolve_anchor_offset_ms) ───
  // D1 anchors at 22:00 UTC (FXCM session boundary), H4 at 23:00 UTC,
  // others at epoch midnight (0). Matches core/utils/buckets.bucket_start_ms.
  const _ANCHOR_MS_BY_TFS: Record<number, number> = {
    14400: 82800000, // H4 — 23:00 UTC = 82800s
    86400: 79200000, // D1 — 22:00 UTC = 79200s
  };

  // ─── Countdown (wallclock + bucket math) ───────────────────────────────
  // Display arithmetic per X28: not domain computation, just `next_close - now`.
  const countdownMs = $derived.by(() => {
    if (tfMs === 0) return null;
    const anchorMs = _ANCHOR_MS_BY_TFS[tfS] ?? 0;
    const bucketOpen =
      Math.floor((nowMs - anchorMs) / tfMs) * tfMs + anchorMs;
    const closeMs = bucketOpen + tfMs;
    const remain = closeMs - nowMs;
    // Bounded: 0..tfMs always (wallclock anchor guarantees this); guard anyway.
    return remain >= 0 && remain <= tfMs ? remain : null;
  });
  const countdownStr = $derived.by(() => {
    if (countdownMs == null) return "—:—";
    const totalSec = Math.floor(countdownMs / 1000);
    const mm = Math.floor(totalSec / 60);
    const ss = totalSec % 60;
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  });

  // ─── ATR formatter ─────────────────────────────────────────────────────
  // Backend returns 1.0 as fallback when no bars (engine.get_atr); we cannot
  // distinguish from real ATR=1.0, so we display as-is. Adaptive precision:
  // large prices (XAU ~4500 → ATR ~80) → 1 decimal; small ratios → 2 dec.
  function fmtAtr(v: number | null): string {
    if (v == null) return "—";
    return v >= 10 ? v.toFixed(1) : v.toFixed(2);
  }
</script>

<div
  class="cmd-rail"
  role="status"
  aria-label="Market context: ATR, bar close countdown"
>
  <span class="cell" title="ATR(14) on {tfLabel} — backend value (engine.get_atr)">
    <span class="lbl">ATR</span>
    <span class="val">{fmtAtr(atr)}</span>
  </span>

  <span class="sep">·</span>

  <span class="cell" title="Countdown to {tfLabel} bar close (wallclock + tf bucket)">
    <!-- §6 removal: ↻ icon dropped (it implied refresh-button affordance
         when in fact the countdown is read-only; M15 label suffices). -->
    <span class="lbl">{tfLabel}</span>
    <span class="val mono">{countdownStr}</span>
  </span>
</div>

<style>
  /* CR-2.5: cells render inline within parent .top-right-bar flex.
     No own border/padding — visual separation provided by parent .tr-sep. */
  .cmd-rail {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono, ui-monospace, "SF Mono", Consolas, monospace);
    font-size: var(--t3a-size, 12px);
    color: var(--text-2, #9b9bb0);
    line-height: 1;
    user-select: none;
  }
  .cell {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
  }
  .lbl {
    opacity: 0.6;
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .val {
    color: var(--text-1, #e6edf3);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .val.mono {
    font-feature-settings: "tnum";
  }
  .sep {
    opacity: 0.3;
    font-weight: 400;
  }

  /* Mobile collapse: hide rail on narrow viewports. Trader peripheral
     context is desktop-first; mobile keeps just the ☰ overflow trigger. */
  @media (max-width: 600px) {
    .cmd-rail {
      display: none;
    }
  }
</style>
