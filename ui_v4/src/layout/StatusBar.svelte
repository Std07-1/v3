<!-- src/layout/StatusBar.svelte -->
<!-- GO-5.2: Нижній статус-бар. Показує: статус, latency, cursor price, warnings count. -->
<!-- 1s tick для stale re-derive (diagSelectors потребує now). -->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import type { StatusInfo } from "../app/diagSelectors";
    import { resolveStatus } from "../app/diagSelectors";
    import { diagStore } from "../app/diagState";
    import type { DiagStateData } from "../app/diagState";
    import { metaStore } from "../stores/meta";
    import {
        uiWarnings as routerWarnings,
        serverWarnings,
    } from "../app/frameRouter";

    const {
        statusInfo,
        latencyMs,
        onDiagToggle,
    }: {
        statusInfo: StatusInfo;
        latencyMs: number | null;
        onDiagToggle?: () => void;
    } = $props();

    // 1s tick для stale detection
    let now = $state(Date.now());
    let tickInterval: ReturnType<typeof setInterval> | null = null;

    onMount(() => {
        tickInterval = setInterval(() => {
            now = Date.now();
        }, 1000);
    });
    onDestroy(() => {
        if (tickInterval) clearInterval(tickInterval);
    });

    // Store subscriptions → $state
    let diagSnap: DiagStateData | null = $state(null);
    let cursorPrice: number | null = $state(null);
    let uiWarnCount = $state(0);
    let serverWarnCount = $state(0);

    const unsubDiag = diagStore.subscribe((d) => {
        diagSnap = d;
    });
    const unsubMeta = metaStore.subscribe((s) => {
        cursorPrice = s.cursorPrice;
    });
    const unsubUi = routerWarnings.subscribe((arr) => {
        uiWarnCount = arr.length;
    });
    const unsubSrv = serverWarnings.subscribe((arr) => {
        serverWarnCount = arr.length;
    });
    onDestroy(() => {
        unsubDiag();
        unsubMeta();
        unsubUi();
        unsubSrv();
    });

    // Derived liveStatus
    let liveStatusInfo: StatusInfo = $derived(
        diagSnap ? resolveStatus(diagSnap, now) : statusInfo,
    );

    const STATUS_COLORS: Record<string, string> = {
        HEALTHY: "#26a69a",
        CONNECTING: "#f0b90b",
        STALLED: "#ef5350",
        WS_UNAVAILABLE: "#ef5350",
        EDGE_BLOCKED: "#ef5350",
        OFFLINE: "#ef5350",
        FRONTEND_ERROR: "#ef5350",
    };
</script>

<footer class="status-bar">
    <span
        class="status-dot"
        style:background={STATUS_COLORS[liveStatusInfo.status] ?? "#888"}
    ></span>
    <span class="status-label">{liveStatusInfo.status}</span>

    {#if latencyMs != null}
        <span class="status-item">⏱ {latencyMs}ms</span>
    {/if}

    {#if cursorPrice != null}
        <span class="status-item">{cursorPrice.toFixed(5)}</span>
    {/if}

    {#if uiWarnCount + serverWarnCount > 0}
        <span class="status-item warn-count"
            >⚠ {uiWarnCount + serverWarnCount}</span
        >
    {/if}

    <span class="status-detail">{liveStatusInfo.detail}</span>

    <!-- P3.14: Diag panel toggle button -->
    {#if onDiagToggle}
        <button
            class="diag-btn"
            onclick={onDiagToggle}
            title="Diagnostics (Ctrl+Shift+D)">🔧</button
        >
    {/if}
</footer>

<style>
    .status-bar {
        flex: 0 0 auto;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 3px 10px;
        background: #1e222d;
        border-top: 1px solid #2a2e39;
        font-size: 12px;
        color: #787b86;
        min-height: 24px;
    }

    .status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
    }

    .status-label {
        font-weight: 600;
        color: #d1d4dc;
    }

    .status-item {
        color: #b2b5be;
    }

    .warn-count {
        color: #f0b90b;
    }

    .status-detail {
        margin-left: auto;
        font-size: 11px;
        color: #5d606b;
        max-width: 300px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    /* P3.14: Diagnostics toggle button */
    .diag-btn {
        all: unset;
        cursor: pointer;
        font-size: 12px;
        padding: 0 4px;
        opacity: 0.5;
        transition: opacity 0.15s;
        flex-shrink: 0;
    }
    .diag-btn:hover {
        opacity: 1;
    }
</style>
