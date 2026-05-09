<!-- src/layout/NarrativePanel.svelte вЂ” ADR-0033: Context Flow Narrative Panel -->
<script lang="ts">
    import type { NarrativeBlock } from "../types";
    import { untrack } from "svelte";

    interface Props {
        narrative: NarrativeBlock | null;
        // ADR-0065 Phase 1: inline=true → compact pill inside .top-right-bar flex.
        // Expanded body drops as position:absolute below the bar.
        // inline=false (default) → legacy floating panel position:absolute top-right.
        inline?: boolean;
    }

    const { narrative, inline = false }: Props = $props();

    // When inline, default collapsed (compact pill). When floating, default expanded.
    // untrack() reads the prop's initial value without subscribing — correct Svelte 5
    // pattern when prop is static after mount (silences state_referenced_locally).
    let expanded = $state(untrack(() => !inline));
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

    // Trigger color class вЂ” per-scenario (P4 fix: was global from [0])
    function getTriggerClass(trigger: string): string {
        if (trigger === "ready") return "trigger-ready";
        if (trigger === "triggered") return "trigger-triggered";
        if (trigger === "in_zone") return "trigger-inzone";
        return "trigger-approaching";
    }
</script>

{#if narrative}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
        class="narrative-panel"
        class:inline
        onclick={(e) => e.stopPropagation()}
    >
        <!-- Compact pill (always visible) -->
        <button
            class="headline-bar"
            class:trade={narrative.mode === "trade"}
            class:counter={narrative.sub_mode === "counter"}
            onclick={toggle}
        >
            <span class="headline-text">{narrative.headline}</span>
            {#if narrative.market_phase === "trending_up" || narrative.market_phase === "trending_down"}
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
                        <div class="sc-trigger {getTriggerClass(sc.trigger)}">
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

                <!-- ADR-0049: Archi Thesis Layer -->
                {#if narrative.archi_thesis}
                    <div class="archi-section">
                        <div class="archi-header">
                            <span class="archi-icon">🧠</span>
                            <span class="archi-label">Арчі</span>
                            <span class="conviction conviction-{narrative.archi_thesis.conviction}">
                                {narrative.archi_thesis.conviction}
                            </span>
                            <span class="freshness freshness-{narrative.archi_thesis.freshness}">
                                {narrative.archi_thesis.freshness}
                            </span>
                        </div>
                        <div class="archi-thesis">{narrative.archi_thesis.thesis}</div>
                        {#if narrative.archi_thesis.key_level}
                            <div class="archi-detail">
                                🎯 {narrative.archi_thesis.key_level}
                                {#if narrative.archi_thesis.invalidation}
                                    <span class="archi-inv">✕ {narrative.archi_thesis.invalidation}</span>
                                {/if}
                            </div>
                        {/if}
                    </div>
                {/if}

                <!-- ADR-0049: Archi Presence -->
                {#if narrative.archi_presence}
                    <div class="presence-row">
                        <span class="presence-dot presence-{narrative.archi_presence.status}"></span>
                        <span class="presence-text">
                            {narrative.archi_presence.status}
                            {#if narrative.archi_presence.silence_h > 0}
                                · {narrative.archi_presence.silence_h}h ago
                            {/if}
                        </span>
                        {#if narrative.archi_presence.conditions > 0}
                            <span class="presence-conditions">
                                {narrative.archi_presence.conditions} conditions
                            </span>
                        {/if}
                    </div>
                {/if}
            </div>
        {/if}
    </div>
{/if}

<style>
    /* ── Floating mode (default, legacy): position:absolute top-right of chart ── */
    .narrative-panel {
        position: absolute;
        top: 50px;
        right: 12px;
        z-index: 34;
        max-width: 380px;
        min-width: 240px;
        pointer-events: auto;
        font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
    }
    @media (max-width: 768px) {
        .narrative-panel {
            top: 44px;
            right: 4px;
            left: 4px;
            max-width: none;
            min-width: 0;
        }
    }

    /* ── Inline mode (ADR-0065 Phase 1): sits as flex item in .top-right-bar ── */
    .narrative-panel.inline {
        position: relative;   /* override absolute — flex item in parent row */
        top: auto;
        right: auto;
        z-index: auto;        /* stacking managed by parent .top-right-bar z:35 */
        max-width: none;
        min-width: 0;
    }
    /* When inline + expanded: body drops BELOW the bar row absolutely.
       Parent .top-right-bar is position:fixed so this positions within it. */
    .narrative-panel.inline .narrative-body {
        position: absolute;
        top: calc(100% + 6px);
        left: 0;
        z-index: 200;
        min-width: 280px;
        max-width: 400px;
        margin-top: 0;
    }
    /* Inline headline-bar: compact pill, no full-width stretch */
    .narrative-panel.inline .headline-bar {
        width: auto;
        white-space: nowrap;
        padding: 2px 8px;
        max-width: 220px;
    }
    .narrative-panel.inline .headline-text {
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 160px;
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
        font-size: var(--t3-size);
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
    .headline-bar.counter {
        border-color: rgba(255, 167, 38, 0.45);
    }
    .headline-text {
        flex: 1;
    }
    .phase-badge {
        font-size: var(--t6-size);
        padding: 1px 5px;
        border-radius: 3px;
        background: rgba(120, 123, 134, 0.15);
        color: #8b8f9a;
    }
    .expand-arrow {
        font-size: var(--t4-size);
        color: #5d6068;
    }

    .narrative-body {
        margin-top: 2px;
        padding: 6px 10px;
        border: 1px solid rgba(120, 123, 134, 0.15);
        border-radius: 6px;
        background: rgba(19, 23, 34, 0.9);
        backdrop-filter: blur(6px);
        font-size: var(--t4-size);
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
        font-size: var(--t6-size);
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
        color: var(--accent);
        font-size: var(--t6-size);
        margin-top: 1px;
    }
    .sc-invalidation {
        color: #ef5350;
        font-size: var(--t6-size);
        margin-top: 1px;
        opacity: 0.7;
    }
    .fvg-ctx {
        color: #2ecc71;
        font-size: var(--t6-size);
    }
    .warnings {
        color: #ff9800;
        font-size: var(--t6-size);
    }

    /* ADR-0049: Archi Thesis */
    .archi-section {
        margin-top: 4px;
        padding: 4px 6px;
        border-top: 1px solid rgba(124, 77, 255, 0.2);
        border-radius: 4px;
        background: rgba(124, 77, 255, 0.05);
    }
    .archi-header {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: var(--t6-size);
    }
    .archi-icon { font-size: var(--t4-size); }
    .archi-label {
        font-weight: 700;
        color: #b388ff;
        font-size: var(--t6-size);
    }
    .conviction {
        font-size: var(--t7-size);
        padding: 1px 4px;
        border-radius: 3px;
    }
    .conviction-high { color: #26a69a; background: rgba(38, 166, 154, 0.15); }
    .conviction-medium { color: #ffa726; background: rgba(255, 167, 38, 0.12); }
    .conviction-low { color: #8b8f9a; background: rgba(120, 123, 134, 0.12); }
    .freshness {
        font-size: var(--t8-size);
        padding: 1px 3px;
        border-radius: 2px;
        margin-left: auto;
    }
    .freshness-fresh { color: #26a69a; }
    .freshness-aging { color: #ffa726; }
    .freshness-stale { color: #ef5350; }
    .archi-thesis {
        font-size: var(--t4-size);
        color: #d0d4e0;
        margin-top: 3px;
        line-height: 1.4;
    }
    .archi-detail {
        font-size: var(--t6-size);
        color: var(--accent);
        margin-top: 2px;
    }
    .archi-inv {
        color: #ef5350;
        margin-left: 8px;
    }

    /* ADR-0049: Presence */
    .presence-row {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-top: 4px;
        padding-top: 3px;
        border-top: 1px solid rgba(120, 123, 134, 0.08);
        font-size: var(--t7-size);
        color: #5d6068;
    }
    .presence-dot {
        width: 5px;
        height: 5px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .presence-sleeping { background: #5d6068; }
    .presence-watching { background: #ffa726; }
    .presence-analyzing { background: #42a5f5; animation: pulse 1.5s infinite; }
    .presence-active { background: #26a69a; animation: pulse 1s infinite; }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
    }
    .presence-text { flex: 1; }
    .presence-conditions {
        color: #8b8f9a;
        font-size: var(--t8-size);
    }
</style>
