<!-- src/layout/NarrativePanel.svelte — ADR-0033: Context Flow Narrative Panel -->
<script lang="ts">
    import type { NarrativeBlock } from "../types";

    const { narrative }: { narrative: NarrativeBlock | null } = $props();

    let expanded = $state(false);
    let autoTimer: ReturnType<typeof setTimeout> | null = null;

    // SC-6: auto-collapse 10s after expand
    function toggle() {
        expanded = !expanded;
        if (autoTimer) clearTimeout(autoTimer);
        if (expanded) {
            autoTimer = setTimeout(() => {
                expanded = false;
            }, 10000);
        }
    }

    // Trigger emoji + color class
    const triggerClass = $derived(
        narrative?.scenarios?.[0]?.trigger === "ready"
            ? "trigger-ready"
            : narrative?.scenarios?.[0]?.trigger === "triggered"
              ? "trigger-triggered"
              : narrative?.scenarios?.[0]?.trigger === "in_zone"
                ? "trigger-inzone"
                : "trigger-approaching",
    );
</script>

{#if narrative}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div class="narrative-panel" onclick={(e) => e.stopPropagation()}>
        <!-- 1-line headline bar (always visible) -->
        <button
            class="headline-bar"
            class:trade={narrative.mode === "trade"}
            onclick={toggle}
        >
            <span class="headline-text">{narrative.headline}</span>
            {#if narrative.market_phase !== "ranging"}
                <span class="phase-badge"
                    >{narrative.market_phase === "trending_up"
                        ? "↑trend"
                        : "↓trend"}</span
                >
            {/if}
            <span class="expand-arrow">{expanded ? "▾" : "▸"}</span>
        </button>

        {#if expanded}
            <div class="narrative-body">
                <!-- Bias summary -->
                <div class="row bias-row">{narrative.bias_summary}</div>

                <!-- Scenarios -->
                {#each narrative.scenarios as sc, i}
                    <div class="scenario" class:alt={i > 0}>
                        <div class="sc-header">
                            <span
                                class="sc-dir"
                                class:long={sc.direction === "long"}
                                class:short={sc.direction === "short"}
                            >
                                {sc.direction === "long" ? "▲" : "▼"}
                                {sc.entry_desc}
                            </span>
                        </div>
                        <div class="sc-trigger {triggerClass}">
                            {sc.trigger_desc}
                        </div>
                        {#if sc.target_desc}
                            <div class="sc-target">→ {sc.target_desc}</div>
                        {/if}
                        <div class="sc-invalidation">✕ {sc.invalidation}</div>
                    </div>
                {/each}

                {#if narrative.scenarios.length === 0}
                    <div class="row dim">
                        {narrative.next_area || "Awaiting setup..."}
                    </div>
                {/if}

                <!-- FVG context -->
                {#if narrative.fvg_context}
                    <div class="row fvg-ctx">{narrative.fvg_context}</div>
                {/if}

                <!-- Warnings -->
                {#if narrative.warnings.length > 0}
                    <div class="row warnings">
                        ⚠ {narrative.warnings.join(", ")}
                    </div>
                {/if}
            </div>
        {/if}
    </div>
{/if}

<style>
    .narrative-panel {
        position: absolute;
        top: 36px;
        left: 240px;
        z-index: 34;
        max-width: 420px;
        min-width: 200px;
        pointer-events: auto;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .headline-bar {
        display: flex;
        align-items: center;
        gap: 6px;
        width: 100%;
        padding: 4px 10px;
        border: 1px solid rgba(120, 123, 134, 0.2);
        border-radius: 6px;
        background: rgba(19, 23, 34, 0.85);
        backdrop-filter: blur(6px);
        color: #c0c4cc;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.15s ease;
        text-align: left;
        line-height: 1.4;
    }
    .headline-bar:hover {
        border-color: rgba(74, 144, 217, 0.4);
        background: rgba(25, 30, 44, 0.92);
    }
    .headline-bar.trade {
        border-color: rgba(74, 144, 217, 0.35);
    }
    .headline-text {
        flex: 1;
    }
    .phase-badge {
        font-size: 9px;
        padding: 1px 5px;
        border-radius: 3px;
        background: rgba(120, 123, 134, 0.15);
        color: #8b8f9a;
    }
    .expand-arrow {
        font-size: 10px;
        color: #5d6068;
    }

    .narrative-body {
        margin-top: 2px;
        padding: 6px 10px;
        border: 1px solid rgba(120, 123, 134, 0.15);
        border-radius: 6px;
        background: rgba(19, 23, 34, 0.9);
        backdrop-filter: blur(6px);
        font-size: 10px;
        color: #a0a4b0;
        display: flex;
        flex-direction: column;
        gap: 4px;
        animation: fadeIn 0.12s ease-out;
    }
    @keyframes fadeIn {
        from {
            opacity: 0;
            transform: translateY(4px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    .row {
        line-height: 1.45;
    }
    .bias-row {
        color: #8b8f9a;
        font-style: italic;
    }
    .dim {
        color: #5d6068;
    }

    .scenario {
        padding: 3px 0;
        border-top: 1px solid rgba(120, 123, 134, 0.1);
    }
    .scenario.alt {
        opacity: 0.7;
    }
    .sc-header {
        font-weight: 600;
    }
    .sc-dir.long {
        color: #26a69a;
    }
    .sc-dir.short {
        color: #ef5350;
    }
    .sc-trigger {
        font-size: 9px;
        margin-top: 2px;
        padding: 2px 6px;
        border-radius: 3px;
        background: rgba(120, 123, 134, 0.08);
    }
    .trigger-ready {
        color: #26a69a;
        background: rgba(38, 166, 154, 0.1);
    }
    .trigger-triggered {
        color: #ffa726;
        background: rgba(255, 167, 38, 0.1);
    }
    .trigger-inzone {
        color: #42a5f5;
        background: rgba(66, 165, 245, 0.1);
    }
    .trigger-approaching {
        color: #8b8f9a;
    }
    .sc-target {
        color: #4a90d9;
        font-size: 9px;
        margin-top: 1px;
    }
    .sc-invalidation {
        color: #ef5350;
        font-size: 9px;
        margin-top: 1px;
        opacity: 0.7;
    }
    .fvg-ctx {
        color: #2ecc71;
        font-size: 9px;
    }
    .warnings {
        color: #ff9800;
        font-size: 9px;
    }
</style>
