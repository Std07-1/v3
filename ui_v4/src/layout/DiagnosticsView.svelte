<!--
  src/layout/DiagnosticsView.svelte — ADR-0068 Slice 2

  Pure read-only view of diagState + frameRouter warnings.
  Extracted from DiagPanel.svelte body so the same content can live both:
    - in the floating DiagPanel (legacy 🔧 button + Ctrl+Shift+D toggle)
    - inside InfoModal[Diagnostics] tab (ADR-0068 forward-only redirect)

  No fixed positioning, no visibility prop — caller controls layout.
-->

<script lang="ts">
    import { onDestroy } from "svelte";
    import { diagStore } from "../app/diagState";
    import type { DiagStateData } from "../app/diagState";
    import {
        uiWarnings as routerUiWarnings,
        serverWarnings,
    } from "../app/frameRouter";
    import type { UiWarning } from "../types";

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

{#if snap}
    <div class="diag-view" role="region" aria-label="Diagnostics">
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
{:else}
    <div class="diag-empty">No diagnostic snapshot yet.</div>
{/if}

<style>
    .diag-view {
        font-size: var(--t3-size);
        color: var(--text-2);
        font-family: var(--font-sans);
    }
    .diag-empty {
        color: var(--text-3);
        font-style: italic;
        font-size: var(--t3-size);
        padding: 8px 0;
    }
    .diag-section {
        margin-bottom: 12px;
    }
    .diag-section h4 {
        margin: 0 0 4px;
        font-size: var(--t3-size);
        color: var(--text-3);
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
        border-bottom: 1px solid var(--border-mute);
    }
    .diag-table td:first-child {
        color: var(--text-3);
        font-weight: 600;
        width: 100px;
        white-space: nowrap;
    }
    .diag-table td:last-child {
        color: var(--text-2);
        word-break: break-all;
        font-family: var(--font-mono);
        font-size: var(--t4-size);
    }
    .ok {
        color: var(--bull) !important;
    }
    .err {
        color: var(--bear) !important;
    }
    .diag-warns {
        max-height: 160px;
        overflow-y: auto;
        font-family: var(--font-mono);
        font-size: var(--t4-size);
    }
    .warn-line {
        padding: 1px 0;
        border-bottom: 1px solid var(--border-mute);
    }
    .warn-line.srv {
        color: var(--warn);
    }
    .warn-line.ui {
        color: var(--text-2);
    }
</style>
