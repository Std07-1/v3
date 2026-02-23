<!-- src/layout/OhlcvTooltip.svelte -->
<!-- Entry 078: TV-style OHLCV legend below HUD.
     Line 1: O H L C (colored by direction).
     Line 2: V + Δ delta (absolute + percentage).
     Fixed position — no cursor following. -->
<script lang="ts">
    import type { CrosshairData } from "../chart/engine";

    const {
        data,
    }: {
        data: CrosshairData | null;
    } = $props();

    function fmt(value: number, digits = 5): string {
        if (value >= 100) digits = 2;
        else if (value >= 10) digits = 3;
        return value.toLocaleString("en-US", {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        });
    }

    function fmtVol(value: number): string {
        return value.toLocaleString("en-US", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        });
    }

    function fmtDelta(o: number, c: number): string {
        const diff = c - o;
        const pct = o !== 0 ? (diff / o) * 100 : 0;
        const sign = diff >= 0 ? "+" : "";
        return `${sign}${fmt(diff)} (${sign}${pct.toFixed(2)}%)`;
    }

    let visible = $derived(data?.inRange === true && data?.time != null);
    let barColor = $derived(
        data ? (data.c >= data.o ? "#26a69a" : "#ef5350") : "#787b86",
    );
    let deltaColor = $derived(
        data ? (data.c >= data.o ? "#26a69a" : "#ef5350") : "#787b86",
    );
</script>

{#if visible && data}
    <div class="tv-legend">
        <div class="leg-row">
            <span class="leg-lbl">O</span><span
                class="leg-val"
                style:color={barColor}>{fmt(data.o)}</span
            >
            <span class="leg-lbl">H</span><span
                class="leg-val"
                style:color={barColor}>{fmt(data.h)}</span
            >
            <span class="leg-lbl">L</span><span
                class="leg-val"
                style:color={barColor}>{fmt(data.l)}</span
            >
            <span class="leg-lbl">C</span><span
                class="leg-val"
                style:color={barColor}>{fmt(data.c)}</span
            >
        </div>
        <div class="leg-row leg-row-secondary">
            <span class="leg-lbl">V</span><span class="leg-secondary"
                >{fmtVol(data.v)}</span
            >
            <span class="leg-lbl">Δ</span><span
                class="leg-secondary"
                style:color={deltaColor}>{fmtDelta(data.o, data.c)}</span
            >
        </div>
    </div>
{/if}

<style>
    .tv-legend {
        position: absolute;
        top: 34px;
        left: 10px;
        z-index: 30;
        display: flex;
        flex-direction: column;
        gap: 1px;
        font-size: 12px;
        font-family: "Roboto Mono", monospace, sans-serif;
        pointer-events: none;
        white-space: nowrap;
    }
    .leg-row {
        display: flex;
        align-items: baseline;
        gap: 3px;
    }
    .leg-row-secondary {
        font-size: 11px;
        opacity: 0.8;
    }
    .leg-lbl {
        opacity: 0.45;
        font-size: 11px;
    }
    .leg-val {
        font-weight: 500;
        margin-right: 4px;
    }
    .leg-secondary {
        margin-right: 6px;
    }
</style>
