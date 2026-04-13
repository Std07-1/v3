<script lang="ts">
    import { api, ApiError, getToken } from "../lib/api";
    import { getDirectives, refreshDirectives } from "../lib/state.svelte";
    import type { ChatHandoff, FeedEvent, Directives } from "../lib/types";

    let {
        onchat = (_handoff: ChatHandoff): void => {},
    }: { onchat?: (handoff: ChatHandoff) => void } = $props();

    let events: FeedEvent[] = $state([]);
    let loading = $state(true);
    let error = $state("");
    let lastRefresh = $state("");
    let sseConnected = $state(false);
    let directives = $derived(getDirectives());

    // IDs of events that just arrived via SSE (for fade-in animation)
    let newEventIds = $state<Set<string | number>>(new Set());

    // ── Filters + Search ──
    let activeFilter = $state("all");
    let searchQuery = $state("");

    const TYPE_FILTERS: { key: string; label: string; icon: string }[] = [
        { key: "all", label: "Всі", icon: "" },
        { key: "analysis", label: "Аналіз", icon: "🧠" },
        { key: "signal", label: "Сигнал", icon: "🎯" },
        { key: "alert", label: "Alert", icon: "⚠️" },
        { key: "trade", label: "Trade", icon: "💰" },
        { key: "system", label: "Система", icon: "⚙️" },
        { key: "heartbeat", label: "HB", icon: "💓" },
    ];

    const filteredEvents = $derived.by(() => {
        let result = events;
        if (activeFilter !== "all") {
            result = result.filter((ev) =>
                (ev.type ?? "").toLowerCase().includes(activeFilter),
            );
        }
        if (searchQuery.trim()) {
            const q = searchQuery.trim().toLowerCase();
            result = result.filter(
                (ev) =>
                    (ev.body ?? "").toLowerCase().includes(q) ||
                    (ev.type ?? "").toLowerCase().includes(q) ||
                    (ev.symbol ?? "").toLowerCase().includes(q),
            );
        }
        return result;
    });

    const TYPE_ICONS: Record<string, string> = {
        analysis: "🧠",
        signal: "🎯",
        trade: "💰",
        market: "📊",
        system: "⚙️",
        error: "❌",
        heartbeat: "💓",
        directive: "📋",
        alert: "⚠️",
    };

    const IMP_COLOR: Record<number, string> = {
        1: "#4b4b5a",
        2: "#4b4b5a",
        3: "#6b6b80",
        4: "#fbbf24",
        5: "#f87171",
    };

    function typeIcon(t: string): string {
        for (const [key, icon] of Object.entries(TYPE_ICONS)) {
            if (t.includes(key)) return icon;
        }
        return "•";
    }

    function impColor(imp: number): string {
        return IMP_COLOR[Math.min(5, Math.max(1, imp))] ?? "#6b6b80";
    }

    // ── Card style discrimination ──
    type CardStyle = "compact" | "standard" | "prominent" | "critical";
    function cardStyle(ev: FeedEvent): CardStyle {
        const t = (ev.type ?? "").toLowerCase();
        const body = (ev.body ?? (ev as any).message ?? "").toLowerCase();
        // Classify by body text if type is generic "analysis"
        if (body.includes("[heartbeat]") || t.includes("heartbeat"))
            return "compact";
        if (
            body.includes("signal") ||
            body.includes("entry") ||
            t.includes("signal")
        )
            return "prominent";
        if (
            body.includes("alert") ||
            body.includes("[alert]") ||
            t.includes("alert") ||
            t.includes("error")
        )
            return "critical";
        if (t.includes("heartbeat")) return "compact";
        // Fallback by importance
        if ((ev.importance ?? 1) >= 5) return "critical";
        if ((ev.importance ?? 1) >= 4) return "prominent";
        // Compact for low-importance analysis
        if ((ev.importance ?? 1) <= 1 && t.includes("analysis"))
            return "compact";
        return "standard";
    }

    function fmtTs(ms: number): string {
        const d = new Date(ms);
        return d.toLocaleTimeString("uk-UA", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    }

    function fmtCost(c?: number): string {
        if (c == null) return "";
        return c < 0.001 ? `<$0.001` : `$${c.toFixed(3)}`;
    }

    function truncate(text: string, limit: number): string {
        if (text.length <= limit) return text;
        return `${text.slice(0, limit - 1).trimEnd()}…`;
    }

    function buildFeedPrompt(
        title: string,
        body: string,
        meta: string[],
    ): string {
        const lines = [
            "Арчі, розгорни цей контекст із Feed і скажи, що тут головне зараз.",
            "",
            `Подія: ${title}`,
            ...meta,
            body ? `Контекст: ${body}` : "",
            "",
            "Що це означає, який ризик/можливість і чи потрібна дія?",
        ].filter(Boolean);
        return lines.join("\n");
    }

    function buildEventHandoff(ev: FeedEvent): ChatHandoff {
        const title = ev.symbol
            ? `${ev.symbol} · ${ev.type ?? "Подія"}`
            : (ev.type ?? "Подія");
        const body = truncate(
            ev.body?.trim() || "Подія без текстового опису.",
            240,
        );
        const meta: string[] = [];
        if (ev.symbol) meta.push(`Символ: ${ev.symbol}`);
        if (ev.ts_ms) meta.push(`Час: ${fmtTs(ev.ts_ms)}`);

        return {
            id: `feed:${ev.id ?? ev.ts_ms}:${Date.now()}`,
            source: "feed",
            icon: typeIcon(ev.type ?? ""),
            title,
            summary: body,
            prompt: buildFeedPrompt(title, body, meta),
            ts_ms: ev.ts_ms,
            symbol: ev.symbol,
        };
    }

    function buildScenarioHandoff(scenarioText: string): ChatHandoff {
        const title = directives?.focus_symbol
            ? `${directives.focus_symbol} · Active Scenario`
            : "Active Scenario";
        const body = truncate(scenarioText.trim(), 240);
        const meta: string[] = [];
        if (directives?.focus_symbol) {
            meta.push(`Символ: ${directives.focus_symbol}`);
        }

        return {
            id: `scenario:${Date.now()}`,
            source: "feed",
            icon: "🎯",
            title,
            summary: body,
            prompt: buildFeedPrompt(title, body, meta),
            symbol: directives?.focus_symbol,
        };
    }

    async function refresh() {
        loading = true;
        error = "";
        try {
            const [feedRes] = await Promise.all([
                api.feed(100),
                refreshDirectives(false),
            ]);
            events = feedRes.events ?? [];
            lastRefresh = new Date().toLocaleTimeString("uk-UA");
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) {
                error = "Невірний токен. Вийди і введи правильний.";
            } else {
                error = e instanceof Error ? e.message : "Помилка запиту";
            }
        } finally {
            loading = false;
        }
    }

    // в”Ђв”Ђ SSE real-time stream в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    function connectSSE(): () => void {
        const token = getToken();
        const url = `/api/archi/stream?token=${encodeURIComponent(token)}`;
        const es = new EventSource(url);

        es.onopen = () => {
            sseConnected = true;
        };
        es.onerror = () => {
            sseConnected = false;
        };

        es.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                if (msg.type === "feed" && msg.data) {
                    const ev: FeedEvent = msg.data;
                    const key = ev.id ?? ev.ts_ms;
                    // Prepend only if not already in list
                    const exists = events.some(
                        (e) => (e.id ?? e.ts_ms) === key,
                    );
                    if (!exists) {
                        events = [ev, ...events];
                        // Mark for fade-in, clear after animation
                        newEventIds = new Set([...newEventIds, key]);
                        setTimeout(() => {
                            newEventIds = new Set(
                                [...newEventIds].filter((k) => k !== key),
                            );
                        }, 1200);
                    }
                }
            } catch {
                // ignore parse errors
            }
        };

        return () => es.close();
    }

    $effect(() => {
        refresh();
        const stopSSE = connectSSE();
        // Fallback 60s poll (SSE covers real-time, poll catches missed events)
        const id = setInterval(refresh, 60_000);
        return () => {
            clearInterval(id);
            stopSSE();
        };
    });
</script>

<div class="view">
    <!-- в”Ђв”Ђ Header в”Ђв”Ђ -->
    <div class="view-header">
        <h2>⚡ Activity Feed</h2>
        <div class="header-right">
            {#if sseConnected}
                <span class="sse-dot" title="SSE live">◉</span>
            {/if}
            {#if lastRefresh}<span class="ts-muted">оновлено {lastRefresh}</span
                >{/if}
            <button
                class="btn-ghost small"
                onclick={refresh}
                disabled={loading}
            >
                {loading ? "…" : "↻ Refresh"}
            </button>
        </div>
    </div>

    <!-- в”Ђв”Ђ Error в”Ђв”Ђ -->
    {#if error}
        <div class="error-box">{error}</div>
    {/if}
    <!-- Scenario sticky card -->
    {#if directives?.active_scenario}
        <div class="scenario-card">
            <span class="scenario-icon">🎯</span>
            <div class="scenario-body">
                <div class="scenario-label">ACTIVE SCENARIO</div>
                <div class="scenario-text">{directives.active_scenario}</div>
            </div>
            <button
                class="scenario-discuss"
                onclick={() =>
                    onchat(
                        buildScenarioHandoff(directives?.active_scenario ?? ""),
                    )}
            >
                💬 Обговорити
            </button>
        </div>
    {/if}
    <!-- ── Filter Bar ── -->
    <div class="filter-bar">
        <div class="filter-pills">
            {#each TYPE_FILTERS as f}
                <button
                    class="filter-pill"
                    class:active={activeFilter === f.key}
                    onclick={() => (activeFilter = f.key)}
                >
                    {#if f.icon}<span class="fp-icon">{f.icon}</span>{/if}
                    {f.label}
                </button>
            {/each}
        </div>
        <div class="search-box">
            <input
                type="text"
                placeholder="Пошук…"
                bind:value={searchQuery}
                class="search-input"
            />
            {#if searchQuery}
                <button class="search-clear" onclick={() => (searchQuery = "")}>
                    ✕
                </button>
            {/if}
        </div>
    </div>
    <!-- ── Events ── -->
    <div class="events-list">
        {#if loading && events.length === 0}
            <div class="empty-state">Завантаження…</div>
        {:else if filteredEvents.length === 0}
            <div class="empty-state">
                {events.length === 0 ? "Подій немає" : "Нічого не знайдено"}
            </div>
        {:else}
            {#each filteredEvents as ev (ev.id ?? ev.ts_ms)}
                {@const key = ev.id ?? ev.ts_ms}
                {@const style = cardStyle(ev)}
                <div
                    class="event-card"
                    class:new-event={newEventIds.has(key)}
                    class:card-compact={style === "compact"}
                    class:card-prominent={style === "prominent"}
                    class:card-critical={style === "critical"}
                >
                    <div class="event-left">
                        <div
                            class="imp-dot"
                            style="background: {impColor(ev.importance ?? 1)}"
                        ></div>
                    </div>
                    <div class="event-body">
                        <div class="event-meta">
                            <span class="event-type"
                                >{typeIcon(ev.type ?? "")}
                                {ev.type ?? "?"}</span
                            >
                            {#if ev.symbol}<span class="badge gold small"
                                    >{ev.symbol}</span
                                >{/if}
                            {#if ev.model}<span class="ts-muted"
                                    >{ev.model}</span
                                >{/if}
                            {#if ev.cost_usd}<span class="ts-muted"
                                    >{fmtCost(ev.cost_usd)}</span
                                >{/if}
                            <span class="ts-muted ml-auto"
                                >{fmtTs(ev.ts_ms ?? 0)}</span
                            >
                        </div>
                        {#if ev.body}
                            <div class="event-text">{ev.body}</div>
                        {/if}
                        <div class="event-footer">
                            <button
                                class="event-discuss"
                                onclick={() => onchat(buildEventHandoff(ev))}
                            >
                                💬 Обговорити в Chat
                            </button>
                        </div>
                    </div>
                </div>
            {/each}
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

    .sse-dot {
        font-size: 10px;
        color: #4ade80;
        animation: pulse 2s infinite;
    }
    @keyframes pulse {
        0%,
        100% {
            opacity: 1;
        }
        50% {
            opacity: 0.4;
        }
    }

    .badge {
        font-size: 11px;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 500;
        letter-spacing: 0.02em;
    }
    .badge.gold {
        background: #2a200a;
        color: var(--gold);
    }
    .badge.small {
        font-size: 10px;
        padding: 1px 5px;
    }

    .ts-muted {
        font-size: 12px;
        color: var(--text-muted);
    }
    .ml-auto {
        margin-left: auto;
    }

    .error-box {
        margin: 12px 24px;
        padding: 10px 14px;
        background: #2a0f0f;
        border: 1px solid #5a2020;
        border-radius: var(--radius);
        color: var(--danger);
        font-size: 13px;
    }

    .events-list {
        flex: 1;
        overflow-y: auto;
        padding: 8px 0;
    }

    /* ── Scenario sticky card ── */
    .scenario-card {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 20px;
        background: rgba(251, 191, 36, 0.08);
        border-bottom: 2px solid rgba(251, 191, 36, 0.3);
        flex-shrink: 0;
    }
    .scenario-icon {
        font-size: 18px;
    }
    .scenario-body {
        flex: 1;
        min-width: 0;
    }
    .scenario-label {
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 0.1em;
        color: #fbbf24;
        text-transform: uppercase;
        margin-bottom: 2px;
    }
    .scenario-text {
        font-size: 12px;
        color: var(--text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .scenario-discuss {
        flex-shrink: 0;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid rgba(251, 191, 36, 0.28);
        background: rgba(251, 191, 36, 0.08);
        color: #fbbf24;
        cursor: pointer;
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        transition:
            border-color 0.15s,
            background 0.15s,
            color 0.15s;
    }
    .scenario-discuss:hover {
        border-color: rgba(251, 191, 36, 0.44);
        background: rgba(251, 191, 36, 0.14);
        color: #fcd34d;
    }

    /* ── Card type variants ── */
    .card-compact {
        padding: 5px 24px;
        opacity: 0.65;
    }
    .card-compact .event-type {
        font-size: 11px;
        color: var(--text-muted);
    }
    .card-compact:hover {
        opacity: 1;
    }
    .card-prominent {
        border-left: 3px solid #fbbf24;
        background: rgba(251, 191, 36, 0.04);
    }
    .card-prominent .event-text {
        color: var(--text);
    }
    .card-critical {
        border-left: 3px solid #f87171;
        background: rgba(248, 113, 113, 0.06);
    }
    .card-critical .event-text {
        color: var(--text);
    }
    .card-critical .event-type {
        color: #f87171;
        font-weight: 600;
    }

    /* ── Event card base ── */
    .event-card {
        display: flex;
        gap: 12px;
        padding: 10px 24px;
        border-bottom: 1px solid var(--border);
        transition: background 0.1s;
    }
    .event-card:hover {
        background: var(--surface);
    }
    .event-card.new-event {
        animation: fadeInNew 0.6s ease-out;
    }
    @keyframes fadeInNew {
        from {
            background: rgba(124, 58, 237, 0.15);
        }
        to {
            background: transparent;
        }
    }

    .event-left {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding-top: 5px;
    }
    .imp-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
    }

    .event-body {
        flex: 1;
        min-width: 0;
    }

    .event-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 3px;
    }
    .event-type {
        font-size: 12px;
        font-weight: 500;
        color: var(--text);
    }

    .event-text {
        font-size: 13px;
        color: var(--text-muted);
        white-space: pre-wrap;
        word-break: break-word;
        line-height: 1.5;
    }
    .event-footer {
        margin-top: 8px;
        display: flex;
        justify-content: flex-start;
    }
    .event-discuss {
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--border) 90%, transparent);
        background: var(--surface2);
        color: var(--text-muted);
        cursor: pointer;
        font-size: 11px;
        font-weight: 600;
        transition:
            border-color 0.15s,
            color 0.15s,
            background 0.15s;
    }
    .event-discuss:hover {
        border-color: color-mix(in srgb, var(--accent) 35%, transparent);
        color: var(--text);
        background: color-mix(in srgb, var(--accent) 12%, var(--surface2));
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
        padding: 6px 12px;
        font-size: 13px;
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

    @media (max-width: 768px) {
        .scenario-card {
            align-items: flex-start;
            flex-wrap: wrap;
        }
        .scenario-discuss {
            margin-left: 28px;
        }
    }

    /* ── Filter Bar ── */
    .filter-bar {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 24px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        overflow: hidden;
    }
    .filter-pills {
        display: flex;
        gap: 4px;
        overflow-x: auto;
        scrollbar-width: none;
        flex-shrink: 0;
    }
    .filter-pills::-webkit-scrollbar {
        display: none;
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
        flex-shrink: 0;
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
    .fp-icon {
        font-size: 12px;
    }
    .search-box {
        position: relative;
        flex: 1;
        min-width: 100px;
        max-width: 220px;
    }
    .search-input {
        width: 100%;
        padding: 5px 28px 5px 10px;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: var(--bg);
        color: var(--text);
        font-size: 12px;
        outline: none;
        transition: border-color 0.15s;
    }
    .search-input:focus {
        border-color: var(--accent);
    }
    .search-input::placeholder {
        color: var(--text-muted);
    }
    .search-clear {
        position: absolute;
        right: 6px;
        top: 50%;
        transform: translateY(-50%);
        background: none;
        border: none;
        color: var(--text-muted);
        cursor: pointer;
        font-size: 12px;
        padding: 2px;
        line-height: 1;
    }
    .search-clear:hover {
        color: var(--text);
    }
</style>
