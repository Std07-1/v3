<script lang="ts">
    import { api, ApiError } from "../lib/api";
    import type { ChatHandoff, WorkspaceItem, Directives } from "../lib/types";
    import { getDirectives, refreshDirectives } from "../lib/state.svelte";
    import { marked } from "marked";
    import { sanitizeHtml } from "../lib/sanitize";

    let {
        onchat = (_handoff: ChatHandoff): void => {},
    }: { onchat?: (handoff: ChatHandoff) => void } = $props();

    let directives = $derived(getDirectives());
    let loading = $state(true);
    let error = $state("");
    let showArchived = $state(false);

    // Filter + search
    let filterKind = $state("");
    let searchText = $state("");

    const KIND_FILTERS: { key: string; label: string; icon: string }[] = [
        { key: "", label: "Всі", icon: "" },
        { key: "__task__", label: "Tasks", icon: "▶" },
        { key: "pin", label: "Pins", icon: "📌" },
        { key: "note", label: "Notes", icon: "📝" },
        { key: "briefing", label: "Briefings", icon: "☀️" },
        { key: "scenario_map", label: "Scenarios", icon: "🗺️" },
        { key: "alert", label: "Alerts", icon: "🚨" },
    ];

    marked.setOptions({ breaks: true, gfm: true });

    function parseMarkdown(raw: string): string {
        if (!raw) return "";
        return sanitizeHtml(marked.parse(raw) as string);
    }

    // ── Extract workspace_items from directives ──
    const allItems = $derived<WorkspaceItem[]>(() => {
        const d = directives as Record<string, unknown> | null;
        if (!d) return [];
        const raw = d.workspace_items;
        if (!Array.isArray(raw)) return [];
        return raw as WorkspaceItem[];
    });

    function isTask(i: WorkspaceItem): boolean {
        return typeof i.next_step === "string" && i.next_step.length > 0;
    }

    const activeItems = $derived<WorkspaceItem[]>(
        allItems()
            .filter((i: WorkspaceItem) => i.status === "active")
            .filter((i: WorkspaceItem) => {
                if (filterKind === "__task__") return isTask(i);
                if (filterKind && i.kind !== filterKind) return false;
                if (searchText) {
                    const q = searchText.toLowerCase();
                    const text = [i.title, i.content, ...i.tags].join(" ").toLowerCase();
                    if (!text.includes(q)) return false;
                }
                return true;
            })
            .sort((a: WorkspaceItem, b: WorkspaceItem) => {
                // ADR-045: Active tasks (with next_step) bubble to top
                const aTask = isTask(a);
                const bTask = isTask(b);
                if (aTask !== bTask) return aTask ? -1 : 1;
                // Pinned next
                if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
                // Then by priority ascending
                if (a.priority !== b.priority) return a.priority - b.priority;
                // Then by created_at descending (newest first)
                return b.created_at - a.created_at;
            }),
    );

    const taskCount = $derived<number>(
        allItems().filter((i: WorkspaceItem) => i.status === "active" && isTask(i)).length,
    );

    const archivedItems = $derived<WorkspaceItem[]>(
        allItems()
            .filter((i: WorkspaceItem) => i.status !== "active")
            .sort((a: WorkspaceItem, b: WorkspaceItem) => b.created_at - a.created_at),
    );

    const ICONS: Record<string, string> = {
        pin: "📌",
        note: "📝",
        briefing: "☀️",
        scenario_map: "🗺️",
        alert: "🚨",
    };

    function kindIcon(kind: string): string {
        return ICONS[kind] ?? "•";
    }

    function fmtAgo(epochSec: number): string {
        const diff = (Date.now() / 1000 - epochSec);
        if (diff < 60) return "щойно";
        if (diff < 3600) return `${Math.floor(diff / 60)}хв тому`;
        if (diff < 86400) return `${(diff / 3600).toFixed(1)}год тому`;
        return `${Math.floor(diff / 86400)}д тому`;
    }

    function fmtExpiry(expiresAt: number | null | undefined): string {
        if (!expiresAt) return "";
        const ttl = expiresAt - Date.now() / 1000;
        if (ttl <= 0) return "expired";
        if (ttl < 3600) return `${Math.floor(ttl / 60)}хв`;
        return `${(ttl / 3600).toFixed(1)}год`;
    }

    function buildHandoff(item: WorkspaceItem): void {
        const preview = item.content.slice(0, 120).replace(/\n/g, " ");
        onchat({
            id: `ws-${item.id}`,
            source: "mind",
            icon: kindIcon(item.kind),
            title: item.title,
            summary: preview,
            prompt: `Арчі, ти поклав на стіл "${item.title}": "${preview}..." — розкажи детальніше`,
        });
    }

    // Expanded state per item
    let expanded = $state<Record<string, boolean>>({});
    function toggle(id: string) {
        expanded[id] = !expanded[id];
    }

    // Copy
    let copied = $state<Record<string, boolean>>({});
    async function copyItem(item: WorkspaceItem) {
        await navigator.clipboard.writeText(`${item.title}\n\n${item.content}`);
        copied[item.id] = true;
        setTimeout(() => (copied[item.id] = false), 1500);
    }

    async function load() {
        loading = true;
        error = "";
        try {
            await refreshDirectives();
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
        load();
    });
</script>

<div class="view">
    <!-- ── Header ── -->
    <div class="view-header">
        <h2>🏠 Workspace</h2>
        <div class="header-right">
            <span class="ts-muted">
                {#if taskCount > 0}<span class="task-count-badge">{taskCount} ▶</span> {/if}{activeItems.length} active{#if archivedItems.length > 0}, {archivedItems.length} archived{/if}
            </span>
            <button
                class="btn-ghost small"
                onclick={() => load()}
                disabled={loading}
            >
                {loading ? "…" : "↻"}
            </button>
        </div>
    </div>

    <!-- ── Filter bar ── -->
    <div class="filter-bar">
        <div class="type-pills">
            {#each KIND_FILTERS as f}
                <button
                    class="pill"
                    class:active={filterKind === f.key}
                    onclick={() => (filterKind = filterKind === f.key ? "" : f.key)}
                >
                    {#if f.icon}<span class="pill-icon">{f.icon}</span>{/if}
                    {f.label}
                </button>
            {/each}
        </div>
        <input
            class="search-input"
            type="search"
            placeholder="Пошук…"
            bind:value={searchText}
        />
        {#if filterKind || searchText}
            <span class="filter-count ts-muted">
                {activeItems.length} / {allItems().filter((i: WorkspaceItem) => i.status === "active").length}
            </span>
        {/if}
    </div>

    {#if error}
        <div class="error-box">{error}</div>
    {/if}

    <!-- ── Workspace Items ── -->
    <div class="items-list">
        {#if loading && allItems().length === 0}
            <div class="empty-state">Завантаження…</div>
        {:else if activeItems.length === 0 && archivedItems.length === 0}
            <div class="empty-state">
                <div class="empty-icon">🏠</div>
                <div class="empty-title">Арчі ще нічого не поклав на стіл</div>
                <div class="empty-desc">
                    Він додасть сюди важливі рівні, briefings і сценарії
                    коли буде що показати.
                </div>
            </div>
        {:else if activeItems.length === 0}
            <div class="empty-state">
                {#if filterKind || searchText}
                    Нічого не відповідає фільтру
                {:else}
                    Немає активних items
                {/if}
            </div>
        {:else}
            {#each activeItems as item (item.id)}
                {@const task = isTask(item)}
                {@const hasProgress = Array.isArray(item.progress_log) && item.progress_log.length > 0}
                {@const isExpanded = expanded[item.id] ?? (task || hasProgress || item.content.length <= 300)}
                <div class="ws-card" class:pinned={item.pinned} class:alert={item.kind === "alert"} class:task={task}>
                    <div
                        class="ws-card-header"
                        onclick={() => toggle(item.id)}
                        role="button"
                        tabindex="0"
                        onkeydown={(e) => e.key === "Enter" && toggle(item.id)}
                    >
                        <div class="ws-meta">
                            <span class="ws-icon">{kindIcon(item.kind)}</span>
                            <span class="ws-kind badge">{item.kind}</span>
                            <span class="ws-title">{item.title}</span>
                            {#if task}
                                <span class="badge task-badge" title="Active task з next_step">▶ TASK</span>
                            {/if}
                            {#if item.pinned}
                                <span class="badge pin-badge">pinned</span>
                            {/if}
                        </div>
                        <div class="ws-actions">
                            <span class="ws-ago ts-muted">{fmtAgo(item.created_at)}</span>
                            {#if item.expires_at}
                                {@const exp = fmtExpiry(item.expires_at)}
                                <span class="badge" class:expired={exp === "expired"} class:expiry={exp !== "expired"}>
                                    {exp === "expired" ? "⏰ expired" : `⏳ ${exp}`}
                                </span>
                            {/if}
                            <button
                                class="btn-copy"
                                onclick={(e) => { e.stopPropagation(); copyItem(item); }}
                                title="Копіювати"
                            >
                                {copied[item.id] ? "✓" : "⎘"}
                            </button>
                            <button
                                class="btn-discuss"
                                onclick={(e) => { e.stopPropagation(); buildHandoff(item); }}
                                title="Обговорити"
                            >
                                💬
                            </button>
                            <span class="expand-icon">{isExpanded ? "▲" : "▾"}</span>
                        </div>
                    </div>

                    {#if isExpanded}
                        <div class="ws-content prose">
                            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
                            {@html parseMarkdown(item.content)}
                        </div>
                    {/if}

                    {#if isExpanded && (task || hasProgress)}
                        <div class="task-panel">
                            {#if task}
                                <div class="task-next">
                                    <span class="task-next-label">→ Next</span>
                                    <span class="task-next-text">{item.next_step}</span>
                                </div>
                            {/if}
                            {#if item.wake_condition_id}
                                <div class="task-wake">
                                    <span class="wake-chip">⚡ wake: {item.wake_condition_id}</span>
                                </div>
                            {/if}
                            {#if hasProgress}
                                <div class="task-progress">
                                    <div class="task-progress-label">
                                        {task ? "Progress" : "✓ Last steps"}
                                    </div>
                                    <ul class="task-progress-list">
                                        {#each item.progress_log ?? [] as step}
                                            <li>{step}</li>
                                        {/each}
                                    </ul>
                                </div>
                            {/if}
                        </div>
                    {/if}

                    {#if item.tags.length > 0}
                        <div class="ws-tags">
                            {#each item.tags as tag}
                                <span class="tag-pill">{tag}</span>
                            {/each}
                            {#if item.linked_scenario_id}
                                <span class="tag-pill linked">→ {item.linked_scenario_id}</span>
                            {/if}
                        </div>
                    {:else if item.linked_scenario_id}
                        <div class="ws-tags">
                            <span class="tag-pill linked">→ {item.linked_scenario_id}</span>
                        </div>
                    {/if}
                </div>
            {/each}
        {/if}

        <!-- ── Archived ── -->
        {#if archivedItems.length > 0}
            <div class="archived-section">
                <button
                    class="archived-toggle"
                    onclick={() => (showArchived = !showArchived)}
                >
                    <span class="expand-icon">{showArchived ? "▲" : "▸"}</span>
                    <span class="ts-muted">{archivedItems.length} archived items</span>
                </button>
                {#if showArchived}
                    {#each archivedItems as item (item.id)}
                        <div class="ws-card archived">
                            <div class="ws-card-header">
                                <div class="ws-meta">
                                    <span class="ws-icon">{kindIcon(item.kind)}</span>
                                    <span class="ws-kind badge">{item.kind}</span>
                                    <span class="ws-title">{item.title}</span>
                                    {#if item.status === "superseded" && item.superseded_by}
                                        <span class="badge superseded">→ {item.superseded_by}</span>
                                    {/if}
                                </div>
                                <span class="ws-ago ts-muted">{fmtAgo(item.created_at)}</span>
                            </div>
                        </div>
                    {/each}
                {/if}
            </div>
        {/if}
    </div>
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

    /* ── Filter bar ── */
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
    .pill-icon {
        margin-right: 2px;
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

    .items-list {
        flex: 1;
        overflow-y: auto;
        padding: 0 0 16px;
    }

    /* ── Empty state ── */
    .empty-state {
        padding: 48px 24px;
        text-align: center;
        color: var(--text-muted);
        font-size: 14px;
    }
    .empty-icon {
        font-size: 40px;
        margin-bottom: 12px;
        opacity: 0.4;
    }
    .empty-title {
        font-size: 15px;
        font-weight: 500;
        color: var(--text);
        margin-bottom: 8px;
    }
    .empty-desc {
        font-size: 13px;
        max-width: 320px;
        margin: 0 auto;
        line-height: 1.5;
    }

    /* ── Card ── */
    .ws-card {
        border-bottom: 1px solid var(--border);
        transition: background 0.1s;
    }
    .ws-card.pinned {
        border-left: 3px solid var(--accent);
    }
    .ws-card.alert {
        border-left: 3px solid #ef4444;
    }
    .ws-card.task {
        border-left: 3px solid #10b981;
        background: linear-gradient(90deg, rgba(16, 185, 129, 0.04) 0%, transparent 60%);
    }
    .ws-card.task.pinned {
        border-left-color: #10b981;
    }
    .ws-card.archived {
        opacity: 0.5;
    }

    .ws-card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 24px;
        cursor: pointer;
        transition: background 0.1s;
        gap: 8px;
    }
    .ws-card-header:hover {
        background: var(--surface);
    }

    .ws-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        min-width: 0;
    }
    .ws-icon {
        font-size: 14px;
        flex-shrink: 0;
    }
    .ws-title {
        font-size: 13px;
        font-weight: 500;
        color: var(--text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 360px;
    }

    .ws-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 0;
    }
    .ws-ago {
        font-size: 11px;
        font-family: var(--font-mono);
        white-space: nowrap;
    }

    /* ── Badges ── */
    .badge {
        font-size: 11px;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 500;
        white-space: nowrap;
    }
    .ws-kind {
        background: var(--surface2);
        color: var(--text-muted);
    }
    .pin-badge {
        background: var(--accent-dim);
        color: var(--accent);
    }
    .task-badge {
        background: rgba(16, 185, 129, 0.18);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.35);
        letter-spacing: 0.04em;
        font-weight: 600;
    }
    .task-count-badge {
        display: inline-block;
        padding: 1px 7px;
        margin-right: 4px;
        border-radius: 9px;
        background: rgba(16, 185, 129, 0.18);
        color: #10b981;
        font-weight: 600;
        font-size: 11px;
    }
    .expiry {
        background: rgba(251, 191, 36, 0.15);
        color: #fbbf24;
    }
    .expired {
        background: rgba(239, 68, 68, 0.15);
        color: #ef4444;
    }
    .superseded {
        background: var(--surface2);
        color: var(--text-muted);
        font-style: italic;
    }

    /* ── Content ── */
    .ws-content {
        padding: 0 24px 12px;
        font-size: 13px;
        color: var(--text);
        line-height: 1.6;
    }

    /* ── Task panel (ADR-045) ── */
    .task-panel {
        margin: 0 24px 12px;
        padding: 10px 14px;
        border-left: 2px solid rgba(16, 185, 129, 0.55);
        background: rgba(16, 185, 129, 0.05);
        border-radius: 0 4px 4px 0;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .task-next {
        display: flex;
        gap: 10px;
        align-items: flex-start;
        font-size: 13px;
    }
    .task-next-label {
        color: #10b981;
        font-weight: 600;
        flex-shrink: 0;
        font-family: var(--font-mono);
        font-size: 12px;
        padding-top: 1px;
    }
    .task-next-text {
        color: var(--text);
        line-height: 1.5;
        flex: 1;
        word-wrap: break-word;
        overflow-wrap: anywhere;
    }
    .task-wake {
        font-size: 11px;
    }
    .wake-chip {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        background: rgba(251, 191, 36, 0.12);
        color: #fbbf24;
        border: 1px solid rgba(251, 191, 36, 0.28);
        font-family: var(--font-mono);
    }
    .task-progress-label {
        font-size: 11px;
        color: var(--text-muted);
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 3px;
    }
    .task-progress-list {
        list-style: none;
        margin: 0;
        padding: 0;
        font-size: 12px;
        color: var(--text-muted);
    }
    .task-progress-list li {
        padding: 2px 0 2px 14px;
        line-height: 1.45;
        position: relative;
    }
    .task-progress-list li::before {
        content: "·";
        position: absolute;
        left: 4px;
        color: #10b981;
        font-weight: 700;
    }
    .task-progress-list li:last-child {
        color: var(--text);
    }

    /* ── Tags ── */
    .ws-tags {
        display: flex;
        gap: 4px;
        padding: 0 24px 10px;
        flex-wrap: wrap;
    }
    .tag-pill {
        font-size: 10px;
        padding: 2px 8px;
        border-radius: 10px;
        background: var(--surface2);
        color: var(--text-muted);
        border: 1px solid var(--border);
    }
    .tag-pill.linked {
        background: var(--accent-dim);
        color: var(--accent);
        border-color: transparent;
    }

    /* ── Buttons ── */
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
        border-color: var(--accent);
    }
    .expand-icon {
        font-size: 10px;
        color: var(--text-muted);
        flex-shrink: 0;
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

    /* ── Archived section ── */
    .archived-section {
        border-top: 1px solid var(--border);
        margin-top: 8px;
    }
    .archived-toggle {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        padding: 10px 24px;
        background: none;
        border: none;
        cursor: pointer;
        text-align: left;
    }
    .archived-toggle:hover {
        background: var(--surface);
    }

    .ts-muted {
        font-size: 12px;
        color: var(--text-muted);
    }
</style>
