<!-- src/layout/OhlcvTooltip.svelte -->
<!-- P3.6: Cursor-following OHLCV tooltip (V3 parity: chart_adapter_lite.js:430-446).
     Follows crosshair cursor with 12px offset, flips near edges. -->
<script lang="ts">
    import type { CrosshairData } from "../chart/engine";

    const {
        data,
        containerWidth = 0,
        containerHeight = 0,
    }: {
        data: CrosshairData | null;
        containerWidth?: number;
        containerHeight?: number;
    } = $props();

    function fmt(value: number, digits = 5): string {
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

    function fmtTime(time: unknown): string {
        if (typeof time === "string") return time; // D1
        if (typeof time === "number" && time > 0) {
            const d = new Date(time * 1000);
            const yy = d.getUTCFullYear();
            const mo = String(d.getUTCMonth() + 1).padStart(2, "0");
            const dd = String(d.getUTCDate()).padStart(2, "0");
            const hh = String(d.getUTCHours()).padStart(2, "0");
            const mi = String(d.getUTCMinutes()).padStart(2, "0");
            return `${yy}-${mo}-${dd} ${hh}:${mi} UTC`;
        }
        return "";
    }

    // Derived display
    let visible = $derived(data?.inRange === true && data?.time != null);
    let delta = $derived(data ? data.c - data.o : 0);
    let deltaColor = $derived(delta >= 0 ? "#26a69a" : "#ef5350");

    // P3.6: Cursor-following position (V3: chart_adapter_lite.js:430-438)
    const TOOLTIP_MARGIN = 12;
    const TOOLTIP_W = 220; // approximate width
    const TOOLTIP_H = 70; // approximate height

    let posStyle = $derived(
        (() => {
            if (!data || !visible) return "top:8px;left:8px";
            const cx = data.x;
            const cy = data.y;
            // Default: place right-below cursor
            let left = cx + TOOLTIP_MARGIN;
            let top = cy + TOOLTIP_MARGIN;
            // Flip horizontal if too close to right edge
            if (containerWidth > 0 && left + TOOLTIP_W > containerWidth - 10) {
                left = cx - TOOLTIP_W - TOOLTIP_MARGIN;
            }
            // Flip vertical if too close to bottom
            if (containerHeight > 0 && top + TOOLTIP_H > containerHeight - 10) {
                top = cy - TOOLTIP_H - TOOLTIP_MARGIN;
            }
            // Floor bounds
            if (left < 4) left = 4;
            if (top < 4) top = 4;
            return `top:${top}px;left:${left}px`;
        })(),
    );
</script>

{#if visible && data}
    <div class="ohlcv-tooltip" style={posStyle}>
        <span class="tooltip-time">{fmtTime(data.time)}</span>
        <span class="tooltip-row">
            <span class="label">O</span><span class="val">{fmt(data.o)}</span>
            <span class="label">H</span><span class="val">{fmt(data.h)}</span>
            <span class="label">L</span><span class="val">{fmt(data.l)}</span>
            <span class="label">C</span><span class="val">{fmt(data.c)}</span>
        </span>
        <span class="tooltip-row">
            <span class="label" style:color={deltaColor}>Δ</span>
            <span class="val" style:color={deltaColor}>{fmt(delta)}</span>
            <span class="label">V</span><span class="val">{fmtVol(data.v)}</span
            >
        </span>
    </div>
{/if}

<style>
    .ohlcv-tooltip {
        position: absolute;
        z-index: 30;
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 4px 8px;
        background: rgba(30, 34, 45, 0.85);
        border-radius: 4px;
        font-size: 12px;
        font-family: "Roboto Mono", monospace, sans-serif;
        color: #d1d4dc;
        pointer-events: none;
        white-space: nowrap;
    }
    .tooltip-time {
        color: #787b86;
        font-size: 11px;
    }
    .tooltip-row {
        display: flex;
        gap: 4px;
        align-items: baseline;
    }
    .label {
        color: #787b86;
        font-size: 11px;
        min-width: 12px;
    }
    .val {
        color: #d1d4dc;
    }
</style>
