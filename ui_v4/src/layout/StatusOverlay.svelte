<!-- src/layout/StatusOverlay.svelte -->
<!-- GO-5.3: Overlay для критичних станів (OFFLINE, EDGE_BLOCKED, WS_UNAVAILABLE, STALLED, FRONTEND_ERROR). -->
<!-- Показує: статус, деталі, кнопки Retry/Reload, collapsible діагностика. -->
<script lang="ts">
    import { onDestroy } from "svelte";
    import type { StatusInfo } from "../app/diagSelectors";
    import { diagStore } from "../app/diagState";
    import type { DiagStateData } from "../app/diagState";

    const {
        statusInfo,
        onReconnect,
        onReload,
        wsUrl = "",
    }: {
        statusInfo: StatusInfo;
        onReconnect: () => void;
        onReload: () => void;
        wsUrl?: string;
    } = $props();

    let showDetails = $state(false);

    // Діагностичний snapshot (оновлюється при зміні)
    let diagSnap: DiagStateData | undefined = $state(undefined);
    const unsub = diagStore.subscribe((d) => {
        diagSnap = d;
    });
    onDestroy(() => {
        unsub();
        clearTimeout(showTimer);
        clearTimeout(hideTimer);
    });

    // --- Hysteresis overlay visibility ---
    // Show: critical стан тримається ≥ SHOW_DELAY_MS → stableVisible = true
    // Hide: non-critical тримається ≥ HIDE_DELAY_MS → stableVisible = false
    const SHOW_DELAY_MS = 1500;
    const HIDE_DELAY_MS = 2000;
    let stableVisible = $state(false);
    let showTimer: ReturnType<typeof setTimeout> | undefined;
    let hideTimer: ReturnType<typeof setTimeout> | undefined;

    $effect(() => {
        const want = statusInfo.critical;
        if (want) {
            // Critical → cancel pending hide, start show timer
            clearTimeout(hideTimer);
            hideTimer = undefined;
            if (!stableVisible && showTimer == null) {
                showTimer = setTimeout(() => {
                    showTimer = undefined;
                    stableVisible = true;
                }, SHOW_DELAY_MS);
            }
        } else {
            // Non-critical → cancel pending show, start hide timer
            clearTimeout(showTimer);
            showTimer = undefined;
            if (stableVisible && hideTimer == null) {
                hideTimer = setTimeout(() => {
                    hideTimer = undefined;
                    stableVisible = false;
                }, HIDE_DELAY_MS);
            }
        }
    });

    // Тексти для кожного статусу
    const STATUS_MESSAGES: Record<string, { title: string; desc: string }> = {
        OFFLINE: {
            title: "Мережа недоступна",
            desc: "Перевірте інтернет-з'єднання.",
        },
        EDGE_BLOCKED: {
            title: "Доступ заблоковано",
            desc: "CDN або firewall блокує підключення.",
        },
        WS_UNAVAILABLE: {
            title: "WS сервіс недоступний",
            desc: "WebSocket endpoint не відповідає. Автоматичне перепідключення...",
        },
        STALLED: {
            title: "Немає даних",
            desc: "WS підключено, але фрейми не надходять.",
        },
        FRONTEND_ERROR: {
            title: "Помилка UI",
            desc: "Виникла непередбачена помилка.",
        },
    };

    // "Last known" critical msg — зберігає вміст при переході в non-critical
    let lastMsg: { title: string; desc: string } = $state(
        STATUS_MESSAGES["WS_UNAVAILABLE"],
    );
    $effect(() => {
        const m = STATUS_MESSAGES[statusInfo.status];
        if (m) lastMsg = m;
    });

    let visible = $derived(stableVisible);
</script>

{#if visible}
    <div class="overlay" role="alert">
        <div class="overlay-card">
            <h2 class="overlay-title">{lastMsg.title}</h2>
            <p class="overlay-desc">{lastMsg.desc}</p>
            <p class="overlay-detail-short">{statusInfo.detail}</p>

            <div class="overlay-actions">
                <button class="btn btn-primary" onclick={onReconnect}
                    >Перепідключити</button
                >
                <button class="btn btn-secondary" onclick={onReload}
                    >Перезавантажити</button
                >
            </div>

            <!-- Collapsible діагностика -->
            <button
                class="toggle-details"
                onclick={() => (showDetails = !showDetails)}
            >
                {showDetails ? "▲ Сховати деталі" : "▼ Показати деталі"}
            </button>

            {#if showDetails && diagSnap}
                <div class="details-panel">
                    <table class="details-table">
                        <tbody>
                            <tr
                                ><td>ws_state</td><td>{diagSnap.ws_state}</td
                                ></tr
                            >
                            <tr
                                ><td>ws_reconnect</td><td
                                    >{diagSnap.ws_reconnect_attempt}</td
                                ></tr
                            >
                            {#if diagSnap.ws_last_close}
                                <tr
                                    ><td>close_code</td><td
                                        >{diagSnap.ws_last_close.code}</td
                                    ></tr
                                >
                                <tr
                                    ><td>close_reason</td><td
                                        >{diagSnap.ws_last_close.reason ||
                                            "—"}</td
                                    ></tr
                                >
                                <tr
                                    ><td>wasClean</td><td
                                        >{diagSnap.ws_last_close.wasClean}</td
                                    ></tr
                                >
                            {/if}
                            <tr
                                ><td>last_seq</td><td
                                    >{diagSnap.last_frame_seq ?? "—"}</td
                                ></tr
                            >
                            <tr>
                                <td>frame_age</td>
                                <td>
                                    {#if diagSnap.last_frame_received_ms}
                                        {Math.round(
                                            (Date.now() -
                                                diagSnap.last_frame_received_ms) /
                                                1000,
                                        )}s
                                    {:else}
                                        —
                                    {/if}
                                </td>
                            </tr>
                            <tr
                                ><td>net_offline</td><td
                                    >{diagSnap.net_offline}</td
                                ></tr
                            >
                            {#if diagSnap.edge_probe}
                                <tr
                                    ><td>edge_probe.ok</td><td
                                        >{diagSnap.edge_probe.ok}</td
                                    ></tr
                                >
                                <tr
                                    ><td>edge_probe.status</td><td
                                        >{diagSnap.edge_probe.status ?? "—"}</td
                                    ></tr
                                >
                            {/if}
                            {#if diagSnap.fe_last_error}
                                <tr
                                    ><td>fe_error</td><td
                                        >{diagSnap.fe_last_error.message}</td
                                    ></tr
                                >
                            {/if}
                            {#if diagSnap.ws_last_error}
                                <tr
                                    ><td>ws_error</td><td
                                        >{diagSnap.ws_last_error}</td
                                    ></tr
                                >
                            {/if}
                            {#if wsUrl}
                                <tr><td>ws_url</td><td>{wsUrl}</td></tr>
                            {/if}
                        </tbody>
                    </table>
                </div>
            {/if}
        </div>
    </div>
{/if}

<style>
    .overlay {
        position: fixed;
        inset: 0;
        z-index: 1000;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0, 0, 0, 0.65);
        backdrop-filter: blur(4px);
    }

    .overlay-card {
        background: #1e222d;
        border: 1px solid #363a45;
        border-radius: 12px;
        padding: 32px;
        max-width: 440px;
        text-align: center;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }

    .overlay-title {
        font-size: 20px;
        font-weight: 700;
        color: #ef5350;
        margin: 0 0 8px;
    }

    .overlay-desc {
        color: #b2b5be;
        margin: 0 0 4px;
        font-size: 14px;
    }

    .overlay-detail-short {
        color: #5d606b;
        font-size: 12px;
        margin: 0 0 20px;
    }

    .overlay-actions {
        display: flex;
        gap: 12px;
        justify-content: center;
        margin-bottom: 16px;
    }

    .btn {
        padding: 8px 20px;
        border: none;
        border-radius: 6px;
        font-size: 14px;
        cursor: pointer;
        font-weight: 600;
    }

    .btn-primary {
        background: #4a90d9;
        color: #fff;
    }
    .btn-primary:hover {
        background: #5da1ea;
    }

    .btn-secondary {
        background: #2a2e39;
        color: #d1d4dc;
        border: 1px solid #363a45;
    }
    .btn-secondary:hover {
        background: #363a45;
    }

    .toggle-details {
        background: none;
        border: none;
        color: #5d606b;
        font-size: 12px;
        cursor: pointer;
        padding: 4px;
    }
    .toggle-details:hover {
        color: #b2b5be;
    }

    .details-panel {
        margin-top: 12px;
        text-align: left;
    }

    .details-table {
        width: 100%;
        font-size: 11px;
        border-collapse: collapse;
    }

    .details-table td {
        padding: 2px 6px;
        border-bottom: 1px solid #2a2e39;
    }

    .details-table td:first-child {
        color: #787b86;
        font-weight: 600;
        width: 120px;
    }

    .details-table td:last-child {
        color: #b2b5be;
        word-break: break-all;
    }
</style>
