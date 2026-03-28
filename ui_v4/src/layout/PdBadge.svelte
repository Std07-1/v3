<!--
  ADR-0041 §5a (Variant H): P/D chip — inline thesis bar element.
  Uses derivePdBadge() for directional coloring (amber = conflict).
  Position: inline-flex in ChartHud thesis bar row.
-->
<script lang="ts">
    import type { PdBadgeResult } from "../stores/shellState";

    interface Props {
        badge: PdBadgeResult | null;
    }
    let { badge }: Props = $props();
</script>

{#if badge}
    <span class="pd-chip {badge.colorVariant}">
        <span class="pd-full">{badge.label}</span>
        <span class="pd-short">{badge.shortLabel}</span>
    </span>
{/if}

<style>
    .pd-chip {
        font-size: 9px;
        font-weight: 700;
        font-family: "SF Mono", "Cascadia Code", "Consolas", monospace;
        padding: 1px 5px;
        border-radius: 3px;
        border: 1px solid;
        letter-spacing: 0.5px;
        pointer-events: none;
        line-height: 1.3;
        white-space: nowrap;
    }

    /* Directional color variants (ADR-0041 §5a) */
    .pd-chip.aligned-green {
        color: #26a69a;
        border-color: rgba(38, 166, 154, 0.4);
        background: rgba(38, 166, 154, 0.1);
    }
    .pd-chip.aligned-red {
        color: #ef5350;
        border-color: rgba(239, 83, 80, 0.4);
        background: rgba(239, 83, 80, 0.1);
    }
    .pd-chip.amber {
        color: #ffa726;
        border-color: rgba(255, 167, 38, 0.45);
        background: rgba(255, 167, 38, 0.12);
    }
    .pd-chip.neutral {
        color: rgba(255, 255, 255, 0.5);
        border-color: rgba(255, 255, 255, 0.12);
        background: rgba(255, 255, 255, 0.04);
    }

    /* Mobile: show short label, hide full */
    .pd-short {
        display: none;
    }
    @media (max-width: 768px) {
        .pd-full {
            display: none;
        }
        .pd-short {
            display: inline;
        }
    }
</style>
