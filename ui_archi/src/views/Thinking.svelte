<script lang="ts">
    import { api, ApiError } from "../lib/api";
    import type { ThinkingEntry } from "../lib/types";
    import { marked } from "marked";

    let {
        onchat = (_text: string): void => {},
    }: { onchat?: (text: string) => void } = $props();

    let entries: ThinkingEntry[] = $state([]);
    let total = $state(0);
    let offset = $state(0);
    const LIMIT = 30;
    let loading = $state(true);
    let error = $state("");
    let expanded = $state<Record<number, boolean>>({});

    // в”Ђв”Ђ T1.3 filter + search в”Ђв”Ђ
    let filterType = $state("");
    let searchText = $state("");

    const CALL_TYPES = ["reactive", "proactive", "tda", "manual"];

    const filteredEntries = $derived(
        entries.filter((e) => {
            if (filterType && e.call_type !== filterType) return false;
            if (searchText) {
                const q = searchText.toLowerCase();
                const text = [
                    e.output_snippet ?? "",
                    e.thinking ?? "",
                    e.trigger ?? "",
                    e.model ?? "",
                ]
                    .join(" ")
                    .toLowerCase();
                if (!text.includes(q)) return false;
            }
            return true;
        }),
    );

    // в”Ђв”Ђ T3 model colors в”Ђв”Ђ
    function modelColor(model?: string): string {
        if (!model) return "model-default";
        const m = model.toLowerCase();
        if (m.includes("opus")) return "model-opus";
        if (m.includes("sonnet")) return "model-sonnet";
        if (m.includes("haiku")) return "model-haiku";
        return "model-default";
    }

    // в”Ђв”Ђ T3 copy button в”Ђв”Ђ
    let copied = $state<Record<number, boolean>>({});
    async function copyEntry(i: number, entry: ThinkingEntry) {
        const text = [
            entry.output_snippet ?? "",
            entry.thinking ? `\n\n[Thinking]\n${entry.thinking}` : "",
        ].join("");
        await navigator.clipboard.writeText(text);
        copied[i] = true;
        setTimeout(() => (copied[i] = false), 1500);
    }

    marked.setOptions({ breaks: true, gfm: true });

    function toggle(i: number) {
        expanded[i] = !expanded[i];
    }

    function fmtTs(epochSeconds: number): string {
        const d = new Date(epochSeconds * 1000);
        return d.toLocaleString("uk-UA", {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function fmtChars(n?: number): string {
        if (!n) return "";
        if (n >= 1000) return `${(n / 1000).toFixed(1)}k sym`;
        return `${n} sym`;
    }

    function parseThinking(raw?: string): string {
        if (!raw) return "";
        return marked.parse(raw) as string;
    }

    function parseSnippet(raw?: string): string {
        if (!raw) return "";
        return marked.parse(raw) as string;
    }

    async function load(newOffset = 0) {
        loading = true;
        error = "";
        try {
            const res = await api.thinking(LIMIT, newOffset);
            entries = res.entries ?? [];
            total = res.total ?? 0;
            offset = newOffset;
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) {
                error = "Невірний токен.";
            } else {
                error = e instanceof Error ? e.message : "Помилка запиту";
            }
        } finally {
            loading = false;
        }
    }

    $effect(() => {
        load(0);
    });

    const pages = $derived(Math.ceil(total / LIMIT));
    const currentPage = $derived(Math.floor(offset / LIMIT));
</script>

<div class="view">
    <!-- в”Ђв”Ђ Header в”Ђв”Ђ -->
    <div class="view-header">
        <h2>🧠 Thinking Archive</h2>
        <div class="header-right">
            <span class="ts-muted">{total} записів</span>
            <button
                class="btn-ghost small"
                onclick={() => load(offset)}
                disabled={loading}
            >
                {loading ? "…" : "↻"}
            </button>
        </div>
    </div>

    <!-- в”Ђв”Ђ Filter bar в”Ђв”Ђ -->
    <div class="filter-bar">
        <div class="type-pills">
            <button
                class="pill"
                class:active={filterType === ""}
                onclick={() => (filterType = "")}>все</button
            >
            {#each CALL_TYPES as ct}
                <button
                    class="pill"
                    class:active={filterType === ct}
                    onclick={() => (filterType = filterType === ct ? "" : ct)}
                >
                    {ct}
                </button>
            {/each}
        </div>
        <input
            class="search-input"
            type="search"
            placeholder="Пошук…"
            bind:value={searchText}
        />
        {#if filterType || searchText}
            <span class="filter-count ts-muted">
                {filteredEntries.length} / {entries.length}
            </span>
        {/if}
    </div>

    {#if error}
        <div class="error-box">{error}</div>
    {/if}

    <!-- в”Ђв”Ђ Entries в”Ђв”Ђ -->
    <div class="entries-list">
        {#if loading && entries.length === 0}
            <div class="empty-state">Завантаження…</div>
        {:else if filteredEntries.length === 0}
            <div class="empty-state">
                {entries.length === 0
                    ? "Думок не знайдено"
                    : "Нічого не відповідає фільтру"}
            </div>
        {:else}
            {#each filteredEntries as entry, i}
                <div class="entry-card">
                    <div
                        class="entry-header"
                        onclick={() => toggle(i)}
                        role="button"
                        tabindex="0"
                        onkeydown={(e) => e.key === "Enter" && toggle(i)}
                    >
                        <div class="entry-meta">
                            <span class="entry-ts">{fmtTs(entry.ts)}</span>
                            {#if entry.call_type}
                                <span class="badge accent"
                                    >{entry.call_type}</span
                                >
                            {/if}
                            {#if entry.model}
                                <span class="badge {modelColor(entry.model)}"
                                    >{entry.model}</span
                                >
                            {/if}
                            {#if entry.chars}
                                <span class="ts-muted"
                                    >{fmtChars(entry.chars)}</span
                                >
                            {/if}
                            {#if entry.trigger}
                                <span class="ts-muted">← {entry.trigger}</span>
                            {/if}
                        </div>
                        <div class="entry-actions">
                            <button
                                class="btn-copy"
                                onclick={(e) => {
                                    e.stopPropagation();
                                    copyEntry(i, entry);
                                }}
                                title="Копіювати"
                            >
                                {copied[i] ? "✓" : "⎘"}
                            </button>
                            <button
                                class="btn-discuss"
                                onclick={(e) => {
                                    e.stopPropagation();
                                    const preview = (entry.output_snippet ?? "")
                                        .slice(0, 80)
                                        .replace(/\n/g, " ");
                                    const ts = fmtTs(entry.ts);
                                    onchat(
                                        `Арчі, ти писав ${ts}: "${preview}..."`,
                                    );
                                }}
                                title="Обговорити в Chat"
                            >
                                💬
                            </button>
                            <span class="expand-icon"
                                >{expanded[i] ? "▲" : "▾"}</span
                            >
                        </div>
                    </div>

                    <!-- Output snippet always visible -->
                    {#if entry.output_snippet}
                        <div class="output-snippet prose">
                            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
                            {@html parseSnippet(entry.output_snippet)}
                        </div>
                    {/if}

                    <!-- Thinking block вЂ” collapsible -->
                    {#if expanded[i] && entry.thinking}
                        <div class="thinking-block">
                            <div class="thinking-label">💭 inner thinking</div>
                            <div class="thinking-text prose">
                                <!-- eslint-disable-next-line svelte/no-at-html-tags -->
                                {@html parseThinking(entry.thinking)}
                            </div>
                        </div>
                    {/if}
                </div>
            {/each}
        {/if}
    </div>

    <!-- в”Ђв”Ђ Pagination в”Ђв”Ђ -->
    {#if pages > 1}
        <div class="pagination">
            <button
                class="btn-ghost small"
                onclick={() => load(offset - LIMIT)}
                disabled={offset === 0 || loading}
            >
                ← Новіші
            </button>
            <span class="ts-muted">стор. {currentPage + 1} / {pages}</span>
            <button
                class="btn-ghost small"
                onclick={() => load(offset + LIMIT)}
                disabled={offset + LIMIT >= total || loading}
            >
                Старіші →
            </button>
        </div>
    {/if}
</div>

<style>
    .view {
        display: flex;
        flex-direction: column;
        height: 100%;
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

    /* в”Ђв”Ђ Filter bar в”Ђв”Ђ */
    .filter-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 24px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        flex-wrap: wrap;
    }
    .type-pills {
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
    }
    .pill {
        font-size: 11px;
        padding: 3px 10px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: none;
        color: var(--text-muted);
        cursor: pointer;
        transition: all 0.1s;
        white-space: nowrap;
    }
    .pill:hover {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .pill.active {
        background: var(--accent-dim);
        color: var(--accent);
        border-color: transparent;
    }
    .search-input {
        flex: 1;
        min-width: 120px;
        max-width: 240px;
        padding: 4px 10px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        color: var(--text);
        font-size: 12px;
        outline: none;
    }
    .search-input:focus {
        border-color: var(--accent);
    }
    .filter-count {
        font-size: 11px;
    }

    .error-box {
        margin: 12px 24px;
        padding: 10px 14px;
        background: #2a0f0f;
        border: 1px solid #5a2020;
        border-radius: var(--radius);
        color: var(--danger);
        font-size: 13px;
        flex-shrink: 0;
    }

    .entries-list {
        flex: 1;
        overflow-y: auto;
    }

    .entry-card {
        border-bottom: 1px solid var(--border);
    }

    .entry-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 24px;
        cursor: pointer;
        transition: background 0.1s;
    }
    .entry-header:hover {
        background: var(--surface);
    }

    .entry-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .entry-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 0;
    }
    .btn-copy {
        background: none;
        border: 1px solid var(--border);
        border-radius: 4px;
        color: var(--text-muted);
        cursor: pointer;
        font-size: 13px;
        padding: 2px 6px;
        line-height: 1;
        transition: all 0.1s;
    }
    .btn-copy:hover {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .btn-discuss {
        width: 26px;
        height: 26px;
        border-radius: 50%;
        border: 1px solid var(--border);
        background: none;
        cursor: pointer;
        font-size: 13px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s;
        flex-shrink: 0;
    }
    .btn-discuss:hover {
        background: rgba(124, 111, 255, 0.12);
        border-color: var(--accent, #7c6fff);
    }
    .entry-ts {
        font-size: 12px;
        font-weight: 500;
        color: var(--text);
        font-family: var(--font-mono);
    }
    .expand-icon {
        font-size: 10px;
        color: var(--text-muted);
        flex-shrink: 0;
    }

    .badge {
        font-size: 11px;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 500;
    }
    .badge.accent {
        background: var(--accent-dim);
        color: #c0b8ff;
    }
    /* model colors */
    .badge.model-opus {
        background: rgba(251, 191, 36, 0.18);
        color: #fbbf24;
    }
    .badge.model-sonnet {
        background: rgba(96, 165, 250, 0.18);
        color: #60a5fa;
    }
    .badge.model-haiku {
        background: rgba(148, 163, 184, 0.15);
        color: #94a3b8;
    }
    .badge.model-default {
        background: var(--surface2);
        color: var(--text-muted);
    }

    .ts-muted {
        font-size: 12px;
        color: var(--text-muted);
    }

    .output-snippet {
        padding: 0 24px 10px;
        font-size: 13px;
        color: var(--text);
        line-height: 1.55;
    }

    .thinking-block {
        margin: 0 24px 12px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        overflow: hidden;
    }
    .thinking-label {
        font-size: 11px;
        color: var(--text-muted);
        padding: 6px 12px;
        border-bottom: 1px solid var(--border);
        font-weight: 500;
    }
    .thinking-text {
        padding: 12px;
        font-size: 13px;
        color: var(--text-muted);
        max-height: 400px;
        overflow-y: auto;
    }

    .pagination {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 12px 24px;
        border-top: 1px solid var(--border);
        flex-shrink: 0;
    }

    .empty-state {
        padding: 48px;
        text-align: center;
        color: var(--text-muted);
        font-size: 14px;
    }

    .btn-ghost {
        background: none;
        border: 1px solid var(--border);
        border-radius: var(--radius);
        color: var(--text-muted);
        cursor: pointer;
        font-size: 13px;
        padding: 6px 12px;
    }
    .btn-ghost:hover:not(:disabled) {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .btn-ghost:disabled {
        opacity: 0.4;
        cursor: default;
    }
    .btn-ghost.small {
        font-size: 12px;
        padding: 4px 10px;
    }
</style>
