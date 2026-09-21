<!--
  CommandRail.svelte — peripheral trader context inline in top-right-bar.
  Slots (left → right):
    [ATR(14) of current TF]    ← backend SSOT (frame.atr) + display % normalize
    [RV(20) of current TF]     ← backend SSOT (frame.rv), 1.0 = neutral
    [↻ countdown to bar close] ← display arithmetic (wallclock + tf bucket)

  ADR refs:
    - ADR-0065 rev 2 (CR-2.5 final layout)
    - ADR-0066 Tier 4 (gold accent), Tier 5 (typography tokens)
    - ADR-0070 amendment (RV restored, ATR dual-format value · percent)
    - X28 invariant: frontend MUST NOT re-derive backend SSOT.
      ATR + RV come from `frame.atr` / `frame.rv` (engine.get_atr /
      engine.get_rv). The "%" form is display normalization
      (atr / lastPrice * 100), NOT a re-derivation of the domain value.

  Countdown is intentionally retained in UI: it's wallclock subtraction
  (next_bar_close_ms - now_ms), not domain compute. Сітку H4/D1 таймер бере
  з даних бекенда: відкриття останньої свічки цього TF mod tf (сезонний якір
  живе лише на бекенді); інші TF вирівняні до епохи (якір 0).

  Out of scope:
    - SMC F badge — needs backend feature gate signal
    - Mobile reflow — desktop-first MVP (hidden via @media <600px)
-->
<script lang="ts">
  import { hintsOn } from "../stores/uiHints";

  type Props = {
    /** ATR(14) for current symbol+tf, sourced from backend (frame.atr).
     *  null when no frame yet OR frame missing the field (legacy server). */
    atr: number | null;
    /** RV(20) — backend SSOT (frame.rv). 1.0 = neutral / no signal.
     *  null when no frame yet OR field missing (legacy server). */
    rv: number | null;
    /** Last price (frame's last candle close) — used ONLY to normalize ATR
     *  into a % display alongside the absolute value. Display arithmetic per
     *  X28; domain ATR still comes from backend. */
    lastPrice: number | null;
    /** TF label ("M15", "H1", ...) — converted to seconds via inverse map. */
    currentTf: string;
    /** Остання свічка кадру (її TF і час відкриття) — джерело сітки H4/D1. */
    lastCandle: { tf: string; t_ms: number } | null;
    /** Live wallclock from parent $state (ticks every 1s). */
    nowMs: number;
  };

  const { atr, rv, lastPrice, currentTf, lastCandle, nowMs }: Props = $props();

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

  // ─── Bucket anchor ────────────────────────────────────────────────────
  // H4/D1 мають сезонний якір (17:00 NY — літо/зима), SSOT на бекенді; UI не дублює
  // таблицю якорів (раніше тут були зашиті зимові 23:00/22:00 → таймер цілив у чужу
  // межу). Якір = відкриття останньої свічки саме цього TF mod tf. Коротші TF — 0.
  const _ANCHORED_MIN_TF_S = 14400;
  const anchorMs = $derived.by((): number | null => {
    if (tfS < _ANCHORED_MIN_TF_S) return 0;
    if (!lastCandle || lastCandle.tf !== currentTf) return null;
    return ((lastCandle.t_ms % tfMs) + tfMs) % tfMs;
  });

  // ─── Countdown (wallclock + bucket math) ───────────────────────────────
  // Display arithmetic per X28: not domain computation, just `next_close - now`.
  const countdownMs = $derived.by(() => {
    if (tfMs === 0 || anchorMs == null) return null;
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
  // ATR as % of lastPrice — display normalization, NOT re-derivation.
  // Hidden when lastPrice missing or ATR is the 1.0 fallback (would mislead).
  const atrPctStr = $derived.by(() => {
    if (atr == null || lastPrice == null || lastPrice <= 0) return "";
    if (atr === 1.0) return ""; // backend fallback sentinel — skip % to avoid noise
    const pct = (atr / lastPrice) * 100;
    return `${pct.toFixed(2)}%`;
  });

  // ─── RV formatter ──────────────────────────────────────────────────────
  // Backend returns 1.0 as fallback (no data, null/zero last-bar volume,
  // insufficient samples). 1.0 = neutral / no signal per RV convention.
  // Display as multiplier with 'x' suffix: "1.42x", "0.87x".
  function fmtRv(v: number | null): string {
    if (v == null) return "—";
    return `${v.toFixed(2)}x`;
  }
</script>

<div
  class="cmd-rail"
  role="status"
  aria-label="Market context: ATR, relative volume, bar close countdown"
>
  <span class="cell" title={$hintsOn ? `ATR(14) on ${tfLabel} — backend value (engine.get_atr)` : undefined}>
    <span class="lbl">ATR</span>
    <span class="val">{fmtAtr(atr)}</span>
    {#if atrPctStr}
      <span class="sub">· {atrPctStr}</span>
    {/if}
  </span>

  <span class="sep">·</span>

  <span class="cell" title={$hintsOn ? `RV(20) on ${tfLabel} — backend value (engine.get_rv); 1.0 = neutral` : undefined}>
    <span class="lbl">RV</span>
    <span class="val">{fmtRv(rv)}</span>
  </span>

  <span class="sep">·</span>

  <span class="cell" title={$hintsOn ? `Countdown to ${tfLabel} bar close (wallclock + tf bucket)` : undefined}>
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
  .sub {
    opacity: 0.55;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
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
