<!--
  CommandRail.svelte — Variant B (client-side compute, frontend-only)
  Implements partial scope of ADR-0065 CR-2.5: peripheral trader context
  surfaced inline in top-right-bar. Backend payload not required.

  Slots (left → right):
    [ATR(14) of current TF]  [RV(20) of current bar]  [↻ countdown to bar close]

  Data source: frame.candles[] (ui_v4/src/types.ts:Candle, fields t_ms,o,h,l,c,v).
  All compute is $derived; no I/O, no store, no side effects.

  Invariants:
    - If candles undefined / <2 → render dashes (degraded-but-loud, not silent zero).
    - ATR uses Wilder TR over last 14 *closed* bars (skip last = preview/forming).
    - RV  uses last *closed* bar volume / SMA(volume, 20) of bars before it.
    - Countdown uses bar.t_ms + tfMs - nowMs (open-excl, end-excl per I2).
    - tfS=0 or invalid → dashes.

  ADR refs:
    - ADR-0065 Command Rail (PROPOSED) — partial implementation, status remains PROPOSED
      until full slot contract + theme integration shipped.
    - ADR-0066 Tier 4 (gold accent), Tier 5 (typography tokens) — consumed via vars only.

  Out of scope (Variant B explicit):
    - SMC F (feature gate indicator) — needs backend signal
    - Mobile reflow — desktop-first MVP
    - Slot contract / declarative rail order — defer to ADR-0065 rev 2
    - Replacement of theme/style/diag pickers — those stay as user-controls
-->
<script lang="ts">
  import type { Candle } from "../types";

  type Props = {
    candles: Candle[] | undefined;
    currentTf: string;        // TF in seconds, string ("900", "3600"...)
    nowMs: number;            // live clock from parent $state
  };

  const { candles, currentTf, nowMs }: Props = $props();

  // ─── TF parsing ────────────────────────────────────────────────────────
  const tfS = $derived.by(() => {
    const n = Number.parseInt(currentTf, 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  });
  const tfMs = $derived(tfS * 1000);
  const tfLabel = $derived.by(() => {
    switch (tfS) {
      case 60: return "M1";
      case 180: return "M3";
      case 300: return "M5";
      case 900: return "M15";
      case 1800: return "M30";
      case 3600: return "H1";
      case 14400: return "H4";
      case 86400: return "D1";
      default: return tfS ? `${tfS}s` : "—";
    }
  });

  // ─── ATR(14) on closed bars ────────────────────────────────────────────
  // Wilder True Range = max(h-l, |h - prev_c|, |l - prev_c|)
  // Use simple SMA of TR over last 14 closed bars (Wilder-equivalent for stationary
  // window; acceptable for peripheral context, not for signal generation).
  const ATR_PERIOD = 14;
  const atr = $derived.by(() => {
    if (!candles || candles.length < ATR_PERIOD + 2) return null;
    // last bar = forming (preview); skip it
    const closed = candles.slice(0, -1);
    if (closed.length < ATR_PERIOD + 1) return null;
    const window = closed.slice(-(ATR_PERIOD + 1));
    let sum = 0;
    for (let i = 1; i < window.length; i++) {
      const cur = window[i];
      const prev = window[i - 1];
      const tr = Math.max(
        cur.h - cur.l,
        Math.abs(cur.h - prev.c),
        Math.abs(cur.l - prev.c),
      );
      sum += tr;
    }
    return sum / ATR_PERIOD;
  });

  // ─── RV(20) — current closed bar volume / SMA(v, 20) of prior closed bars ──
  const RV_PERIOD = 20;
  const rv = $derived.by(() => {
    if (!candles || candles.length < RV_PERIOD + 2) return null;
    const closed = candles.slice(0, -1);
    if (closed.length < RV_PERIOD + 1) return null;
    const lastClosed = closed[closed.length - 1];
    const lastV = lastClosed.v;
    if (lastV == null || lastV <= 0) return null;
    const priorWindow = closed.slice(-(RV_PERIOD + 1), -1);
    let sum = 0;
    let n = 0;
    for (const b of priorWindow) {
      if (b.v != null && b.v > 0) {
        sum += b.v;
        n++;
      }
    }
    if (n < RV_PERIOD / 2) return null; // insufficient sample
    const sma = sum / n;
    if (sma <= 0) return null;
    return lastV / sma;
  });

  // ─── Countdown to current bar close ────────────────────────────────────
  const countdownMs = $derived.by(() => {
    if (!candles || candles.length === 0 || tfMs === 0) return null;
    const last = candles[candles.length - 1];
    const closeMs = last.t_ms + tfMs;
    const remain = closeMs - nowMs;
    if (remain < 0 || remain > tfMs) return null; // stale or invalid
    return remain;
  });
  const countdownStr = $derived.by(() => {
    if (countdownMs == null) return "—:—";
    const totalSec = Math.floor(countdownMs / 1000);
    const mm = Math.floor(totalSec / 60);
    const ss = totalSec % 60;
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  });

  // ─── Formatters ────────────────────────────────────────────────────────
  function fmtAtr(v: number | null): string {
    if (v == null) return "—";
    // Adaptive precision: large prices (XAU ~4500) → 1 decimal,
    // small (BTC ratio etc) → 2 decimals.
    return v >= 10 ? v.toFixed(1) : v.toFixed(2);
  }
  function fmtRv(v: number | null): string {
    if (v == null) return "—";
    return v.toFixed(2) + "×";
  }

  // ─── RV emphasis class ─────────────────────────────────────────────────
  // Visual signal: RV > 1.5× = elevated activity (gold), RV < 0.5× = quiet (dim)
  const rvClass = $derived.by(() => {
    if (rv == null) return "";
    if (rv >= 1.5) return "rv-hot";
    if (rv <= 0.5) return "rv-cool";
    return "";
  });
</script>

<div class="cmd-rail" role="status" aria-label="Market context: ATR, relative volume, bar countdown">
  <span class="cell" title="ATR(14) on {tfLabel} closed bars — average true range">
    <span class="lbl">ATR</span>
    <span class="val">{fmtAtr(atr)}</span>
  </span>

  <span class="sep">·</span>

  <span class="cell {rvClass}" title="Relative volume — current bar volume vs 20-bar SMA">
    <span class="lbl">RV</span>
    <span class="val">{fmtRv(rv)}</span>
  </span>

  <span class="sep">·</span>

  <span class="cell" title="Countdown to {tfLabel} bar close">
    <span class="lbl">↻ {tfLabel}</span>
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

  /* RV emphasis: high activity → brand gold, low → dim */
  .cell.rv-hot .val {
    color: var(--accent, #d4a017);
  }
  .cell.rv-cool .val {
    color: var(--text-2, #9b9bb0);
    opacity: 0.7;
  }

  /* Mobile collapse: hide the rail on narrow viewports — the existing
     top-right-bar already collapses pickers on <480px (App.svelte:874).
     Trader peripheral context is desktop-first. */
  @media (max-width: 600px) {
    .cmd-rail {
      display: none;
    }
  }
</style>
