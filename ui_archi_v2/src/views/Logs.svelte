<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import { api, ApiError } from "../lib/api";
    import type { ChatHandoff, LogLine } from "../lib/types";

    let {
        onchat = (_handoff: ChatHandoff): void => {},
    }: { onchat?: (handoff: ChatHandoff) => void } = $props();

    let lines = $state<LogLine[]>([]);
    let loading = $state(true);
    let source = $state("");
    let filter = $state<"all" | "error" | "warn">("all");
    let copied = $state<number | null>(null);
    let autoRefresh = $state(true);
    let refreshId: ReturnType<typeof setInterval>;
    let loadError = $state("");

    async function fetchLogs() {
        try {
            loadError = "";
            const res = await api.logs(150, filter);
            // Reverse: newest first
            lines = (res.lines ?? []).slice().reverse();
            source = res.source ?? "";
        } catch (e) {
            lines = [];
            source = "";
            if (e instanceof ApiError && e.status === 401) {
                loadError = "Невірний токен.";
            } else {
                loadError = "Не вдалося завантажити логи.";
            }
        } finally {
            loading = false;
        }
    }

    function copyLine(idx: number) {
        const text = lines[idx]?.text;
        if (!text) return;
        navigator.clipboard.writeText(text).then(() => {
            copied = idx;
            setTimeout(() => {
                copied = null;
            }, 1200);
        });
    }

    function copyAll() {
        const text = lines.map((l) => l.text).join("\n");
        navigator.clipboard.writeText(text).then(() => {
            copied = -1;
            setTimeout(() => {
                copied = null;
            }, 1200);
        });
    }

    function setFilter(f: "all" | "error" | "warn") {
        filter = f;
        loading = true;
        fetchLogs();
    }

    function openLogsHandoff() {
        if (loadError || loading || lines.length === 0) return;
        const preview = lines
            .slice(0, 5)
            .map((line) => `[${line.level}] ${line.text}`)
            .join("\n");

        onchat({
            id: `logs:${filter}:${Date.now()}`,
            source: "logs",
            icon: "📋",
            title: `Logs · ${filter}`,
            summary: preview.slice(0, 220),
            prompt: [
                `Арчі, поясни цей лог-контекст (${filter}).`,
                "",
                preview,
                "",
                "Що тут важливе, чи це шум, і чи потрібна дія?",
            ].join("\n"),
        });
    }

    onMount(() => {
        fetchLogs();
        refreshId = setInterval(() => {
            if (autoRefresh) fetchLogs();
        }, 5_000);
    });

    onDestroy(() => clearInterval(refreshId));
</script>

<div class="logs-view">
    <div class="view-header">
        <h2>📋 Logs</h2>
        <div class="header-right">
            <span class="meta-count">{lines.length}</span>
            <button
                class="action-btn discuss-btn"
                onclick={openLogsHandoff}
                title="Обговорити логи в Chat"
                disabled={!!loadError || loading || lines.length === 0}
            >
                💬
            </button>
            <button class="action-btn" onclick={copyAll} title="Copy all">
                {copied === -1 ? "✓" : "📋"}
            </button>
            <label class="auto-toggle">
                <input type="checkbox" bind:checked={autoRefresh} />
                <span class="toggle-track"
                    ><span class="toggle-thumb"></span></span
                >
            </label>
        </div>
    </div>

    <div class="filter-bar">
        <div class="filter-pills">
            <button
                class="filter-pill"
                class:active={filter === "all"}
                onclick={() => setFilter("all")}>All</button
            >
            <button
                class="filter-pill warn"
                class:active={filter === "warn"}
                onclick={() => setFilter("warn")}>⚠️ Warn</button
            >
            <button
                class="filter-pill error"
                class:active={filter === "error"}
                onclick={() => setFilter("error")}>❌ Error</button
            >
        </div>
    </div>

    <div class="logs-body">
        {#if loading && lines.length === 0}
            <div class="empty-state">Завантаження…</div>
        {:else if loadError}
            <div class="empty-state error-state">{loadError}</div>
        {:else if lines.length === 0}
            <div class="empty-state">Порожньо</div>
        {:else}
            {#each lines as line, i (i)}
                <div
                    class="log-line"
                    class:is-error={line.level === "ERROR"}
                    class:is-warn={line.level === "WARN"}
                >
                    <span
                        class="line-level"
                        class:err={line.level === "ERROR"}
                        class:wrn={line.level === "WARN"}>{line.level}</span
                    >
                    <span class="line-text">{line.text}</span>
                    <button
                        class="copy-btn"
                        onclick={() => copyLine(i)}
                        title="Copy"
                    >
                        {copied === i ? "✓" : "📋"}
                    </button>
                </div>
            {/each}
        {/if}
    </div>
</div>

<style>
    .logs-view {
        display: flex;
        flex-direction: column;
        height: 100%;
        overflow: hidden;
    }
    .view-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 20px 24px 12px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
    }
    .view-header h2 {
        font-size: 16px;
        font-weight: 600;
    }
    .header-right {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .filter-bar {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 24px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
    }
    .filter-pills {
        display: flex;
        gap: 4px;
    }
    .filter-pill {
        display: flex;
        align-items: center;
        gap: 3px;
        padding: 4px 10px;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: none;
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        white-space: nowrap;
        transition:
            border-color 0.15s,
            color 0.15s,
            background 0.15s;
    }
    .filter-pill:hover {
        border-color: var(--accent);
        color: var(--text);
    }
    .filter-pill.active {
        background: var(--accent-dim);
        border-color: var(--accent);
        color: var(--text);
    }
    .filter-pill.warn.active {
        background: rgba(180, 83, 9, 0.12);
        border-color: #b45309;
        color: #fbbf24;
    }
    .filter-pill.error.active {
        background: rgba(220, 38, 38, 0.12);
        border-color: #dc2626;
        color: #f87171;
    }

    .meta-count {
        font-size: 11px;
        font-weight: 600;
        color: var(--text-muted);
        font-variant-numeric: tabular-nums;
    }
    .action-btn {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 14px;
        width: 28px;
        height: 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 6px;
        transition: background 0.1s;
    }
    .action-btn:hover {
        background: var(--surface);
    }

    /* Toggle switch */
    .auto-toggle {
        cursor: pointer;
        display: flex;
        align-items: center;
    }
    .auto-toggle input {
        display: none;
    }
    .toggle-track {
        display: block;
        width: 32px;
        height: 18px;
        background: var(--surface2);
        border-radius: 9px;
        position: relative;
        transition: background 0.2s;
    }
    .auto-toggle input:checked + .toggle-track {
        background: var(--accent);
    }
    .toggle-thumb {
        position: absolute;
        top: 2px;
        left: 2px;
        width: 14px;
        height: 14px;
        background: #fff;
        border-radius: 50%;
        transition: transform 0.2s;
    }
    .auto-toggle input:checked + .toggle-track .toggle-thumb {
        transform: translateX(14px);
    }

    .logs-body {
        flex: 1;
        overflow-y: auto;
        padding: 4px 8px;
        font-family: var(--font-mono, monospace);
        font-size: 11.5px;
        line-height: 1.55;
    }
    .log-line {
        display: flex;
        align-items: flex-start;
        gap: 6px;
        padding: 4px 8px;
        border-radius: 4px;
        transition: background 0.1s;
    }
    .log-line:hover {
        background: var(--surface);
    }
    .log-line.is-error {
        background: rgba(239, 68, 68, 0.06);
    }
    .log-line.is-warn {
        background: rgba(245, 158, 11, 0.04);
    }
    .line-level {
        flex-shrink: 0;
        width: 40px;
        font-size: 10px;
        font-weight: 700;
        color: var(--text-muted);
        padding-top: 1px;
    }
    .line-level.err {
        color: #f87171;
    }
    .line-level.wrn {
        color: #fbbf24;
    }
    .line-text {
        flex: 1;
        word-break: break-all;
        color: var(--text);
    }
    .copy-btn {
        flex-shrink: 0;
        width: 24px;
        height: 24px;
        border: none;
        background: none;
        cursor: pointer;
        font-size: 12px;
        border-radius: 4px;
        opacity: 0;
        transition: opacity 0.15s;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .log-line:hover .copy-btn {
        opacity: 1;
    }
    @media (max-width: 768px) {
        .copy-btn {
            opacity: 0.5;
        }
    }
    .empty-state {
        text-align: center;
        padding: 40px;
        color: var(--text-muted);
    }
</style>
