<!-- src/layout/DiagPanel.svelte -->
<!-- P3.14: Diagnostic/developer panel — toggle-able overlay showing DiagState.
     Клавіша: Ctrl+Shift+D або кнопка 🔧 у StatusBar.
     Без бізнес-логіки — pure read-only view of diagState + frameRouter. -->
<script lang="ts">
    import { onDestroy } from "svelte";
    import { diagStore } from "../app/diagState";
    import type { DiagStateData } from "../app/diagState";
    import {
        uiWarnings as routerUiWarnings,
        serverWarnings,
    } from "../app/frameRouter";
    import type { UiWarning } from "../types";

    const { visible }: { visible: boolean } = $props();

    let snap: DiagStateData | null = $state(null);
    let uiWarns: UiWarning[] = $state([]);
    let srvWarns: string[] = $state([]);
    let now = $state(Date.now());

    const unsubs = [
        diagStore.subscribe((d) => {
            snap = d;
        }),
        routerUiWarnings.subscribe((w) => {
            uiWarns = w;
        }),
        serverWarnings.subscribe((w) => {
            srvWarns = w;
        }),
    ];
    const tick = setInterval(() => {
        now = Date.now();
    }, 1000);
    onDestroy(() => {
        unsubs.forEach((u) => u());
        clearInterval(tick);
    });

    function age(ms: number | null): string {
        if (ms == null) return "—";
        const s = Math.round((now - ms) / 1000);
        return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
    }

    function ts(ms: number | null): string {
        if (ms == null) return "—";
        return new Date(ms).toISOString().slice(11, 23);
    }
</script>

{#if visible && snap}
    <div
        class="diag-panel"
        role="region"
        aria-label="Diagnostics"
        onclick={(e) => e.stopPropagation()}
        onkeydown={() => {}}
    >
        <h3 class="diag-title">🔧 Diagnostics</h3>

        <!-- WS Section -->
        <section class="diag-section">
            <h4>WebSocket</h4>
            <table class="diag-table">
                <tbody>
                    <tr
                        ><td>state</td><td
                            class:ok={snap.ws_state === "open"}
                            class:err={snap.ws_state === "closed"}
                            >{snap.ws_state}</td
                        ></tr
                    >
                    <tr
                        ><td>last_open</td><td>{ts(snap.ws_last_open_ms)}</td
                        ></tr
                    >
                    <tr
                        ><td>last_msg</td><td
                            >{ts(snap.ws_last_msg_ms)} ({age(
                                snap.ws_last_msg_ms,
                            )} ago)</td
                        ></tr
                    >
                    <tr
                        ><td>reconnects</td><td>{snap.ws_reconnect_attempt}</td
                        ></tr
                    >
                    {#if snap.ws_last_error}
                        <tr
                            ><td>error</td><td class="err"
                                >{snap.ws_last_error}</td
                            ></tr
                        >
                    {/if}
                    {#if snap.ws_last_close}
                        <tr
                            ><td>close_code</td><td
                                >{snap.ws_last_close.code}</td
                            ></tr
                        >
                        <tr
                            ><td>close_reason</td><td
                                >{snap.ws_last_close.reason || "—"}</td
                            ></tr
                        >
                        <tr
                            ><td>wasClean</td><td
                                >{snap.ws_last_close.wasClean}</td
                            ></tr
                        >
                    {/if}
                </tbody>
            </table>
        </section>

        <!-- Frames Section -->
        <section class="diag-section">
            <h4>Frames</h4>
            <table class="diag-table">
                <tbody>
                    <tr
                        ><td>last_seq</td><td>{snap.last_frame_seq ?? "—"}</td
                        ></tr
                    >
                    <tr
                        ><td>server_ts</td><td
                            >{ts(snap.last_frame_server_ts_ms)}</td
                        ></tr
                    >
                    <tr
                        ><td>received</td><td
                            >{ts(snap.last_frame_received_ms)} ({age(
                                snap.last_frame_received_ms,
                            )} ago)</td
                        ></tr
                    >
                    {#if snap.last_frame_server_ts_ms && snap.last_frame_received_ms}
                        <tr
                            ><td>latency</td><td
                                >{snap.last_frame_received_ms -
                                    snap.last_frame_server_ts_ms}ms</td
                            ></tr
                        >
                    {/if}
                </tbody>
            </table>
        </section>

        <!-- Network Section -->
        <section class="diag-section">
            <h4>Network</h4>
            <table class="diag-table">
                <tbody>
                    <tr
                        ><td>net_offline</td><td class:err={snap.net_offline}
                            >{snap.net_offline}</td
                        ></tr
                    >
                    <tr
                        ><td>fe_ok</td><td
                            class:ok={snap.fe_ok}
                            class:err={!snap.fe_ok}>{snap.fe_ok}</td
                        ></tr
                    >
                    {#if snap.fe_last_error}
                        <tr
                            ><td>fe_error</td><td class="err"
                                >{snap.fe_last_error.message}</td
                            ></tr
                        >
                    {/if}
                    {#if snap.edge_probe}
                        <tr
                            ><td>edge_ok</td><td
                                class:ok={snap.edge_probe.ok}
                                class:err={!snap.edge_probe.ok}
                                >{snap.edge_probe.ok}</td
                            ></tr
                        >
                        <tr
                            ><td>edge_status</td><td
                                >{snap.edge_probe.status ?? "—"}</td
                            ></tr
                        >
                        <tr
                            ><td>edge_ts</td><td>{ts(snap.edge_probe.ts_ms)}</td
                            ></tr
                        >
                    {/if}
                </tbody>
            </table>
        </section>

        <!-- Warnings Section -->
        {#if uiWarns.length + srvWarns.length > 0}
            <section class="diag-section">
                <h4>Warnings ({uiWarns.length + srvWarns.length})</h4>
                <div class="diag-warns">
                    {#each srvWarns as w}
                        <div class="warn-line srv">SRV: {w}</div>
                    {/each}
                    {#each uiWarns.slice(0, 10) as w}
                        <div class="warn-line ui">[{w.code}] {w.details}</div>
                    {/each}
                </div>
            </section>
        {/if}
    </div>
{/if}

<style>
    .diag-panel {
        position: fixed;
        top: 40px;
        right: 8px;
        width: 340px;
        max-height: calc(100vh - 80px);
        overflow-y: auto;
        z-index: 200;
        background: rgba(19, 23, 34, 0.95);
        border: 1px solid rgba(74, 144, 217, 0.25);
        border-radius: 10px;
        padding: 12px 14px;
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
        font-size: 11px;
        color: #b2b5be;
        pointer-events: auto;
        backdrop-filter: blur(12px);
    }
    .diag-title {
        margin: 0 0 10px;
        font-size: 14px;
        color: #d1d4dc;
        font-weight: 700;
    }
    .diag-section {
        margin-bottom: 10px;
    }
    .diag-section h4 {
        margin: 0 0 4px;
        font-size: 11px;
        color: #787b86;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        font-weight: 600;
    }
    .diag-table {
        width: 100%;
        border-collapse: collapse;
    }
    .diag-table td {
        padding: 1px 4px;
        border-bottom: 1px solid rgba(42, 46, 57, 0.5);
    }
    .diag-table td:first-child {
        color: #5d606b;
        font-weight: 600;
        width: 100px;
        white-space: nowrap;
    }
    .diag-table td:last-child {
        color: #b2b5be;
        word-break: break-all;
    }
    .ok {
        color: #26a69a !important;
    }
    .err {
        color: #ef5350 !important;
    }
    .diag-warns {
        max-height: 120px;
        overflow-y: auto;
        font-family: "Roboto Mono", monospace;
        font-size: 10px;
    }
    .warn-line {
        padding: 1px 0;
        border-bottom: 1px solid rgba(42, 46, 57, 0.3);
    }
    .warn-line.srv {
        color: #f0b90b;
    }
    .warn-line.ui {
        color: #b2b5be;
    }

    /* Scrollbar */
    .diag-panel::-webkit-scrollbar {
        width: 4px;
    }
    .diag-panel::-webkit-scrollbar-track {
        background: transparent;
    }
    .diag-panel::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.15);
        border-radius: 2px;
    }
</style>
