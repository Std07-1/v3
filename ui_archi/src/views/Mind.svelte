<script lang="ts">
    import { api, ApiError } from "../lib/api";
    import type { Directives } from "../lib/types";

    let data: Directives | null = $state(null);
    let loading = $state(true);
    let error = $state("");

    function fmtTime(ts: number): string {
        if (!ts) return "?";
        // ts can be epoch seconds (>1e12 → ms already, otherwise seconds)
        const ms = ts > 1e12 ? ts : ts * 1000;
        const d = new Date(ms);
        return d.toLocaleString("uk-UA", {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function fmtAgo(ts: number): string {
        if (!ts) return "";
        const ms = ts > 1e12 ? ts : ts * 1000;
        const diff = Date.now() - ms;
        if (diff < 0) return "в майбутньому";
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return "щойно";
        if (mins < 60) return `${mins} хв тому`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs} год тому`;
        const days = Math.floor(hrs / 24);
        return `${days} д тому`;
    }

    function truncate(s: string, n: number): string {
        return s.length > n ? s.slice(0, n) + "…" : s;
    }

    async function load() {
        loading = true;
        error = "";
        try {
            data = await api.directives(false); // brief=0 → full state
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) {
                error = "Не авторизовано";
            } else {
                error = "Не вдалось завантажити";
            }
        } finally {
            loading = false;
        }
    }

    $effect(() => {
        load();
        const id = setInterval(load, 30_000);
        return () => clearInterval(id);
    });

    // Extract typed data from the flexible Directives interface
    function arr(key: string): any[] {
        if (!data) return [];
        const v = (data as any)[key];
        return Array.isArray(v) ? v : [];
    }
    function obj(key: string): Record<string, any> {
        if (!data) return {};
        const v = (data as any)[key];
        return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    }
    function str(key: string): string {
        if (!data) return "";
        const v = (data as any)[key];
        return typeof v === "string" ? v : "";
    }

    const MOOD_ICON: Record<string, string> = {
        calm: "😌",
        focused: "🎯",
        energetic: "⚡",
        cautious: "🛡️",
        confident: "💪",
        analytical: "🔬",
        excited: "🔥",
        frustrated: "😤",
        reflective: "🪞",
    };
</script>

{#if loading && !data}
    <div class="loading">Завантаження стану…</div>
{:else if error}
    <div class="error-msg">{error}</div>
{:else if data}
    <div class="mind-container">
        <!-- ── Header: mood + inner thought ── -->
        <section class="mind-section hero">
            <div class="hero-row">
                <span class="mood-icon">{MOOD_ICON[str("mood")] ?? "🧠"}</span>
                <span class="mood-text">{str("mood") || "—"}</span>
                {#if (data as any).budget_strategy}
                    <span class="badge strategy"
                        >{(data as any).budget_strategy}</span
                    >
                {/if}
                {#if (data as any).estimated_cost_usd_today != null}
                    <span class="badge budget">
                        ${Number(
                            (data as any).estimated_cost_usd_today,
                        ).toFixed(2)}
                        / ${Number((data as any).budget_limit ?? 6).toFixed(0)}
                    </span>
                {/if}
            </div>
            {#if str("inner_thought")}
                <blockquote class="inner-thought">
                    "{str("inner_thought")}"
                </blockquote>
            {/if}
        </section>

        <!-- ── Watch Levels ── -->
        {#if arr("watch_levels").length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">👁</span>
                    Watch Levels
                    <span class="counter">{arr("watch_levels").length}</span>
                </h3>
                <div class="cards">
                    {#each arr("watch_levels") as lvl}
                        <div class="card watch-card">
                            <div class="watch-header">
                                <span
                                    class="watch-dir"
                                    class:above={lvl.direction === "above"}
                                    class:below={lvl.direction === "below"}
                                >
                                    {lvl.direction === "above" ? "▲" : "▼"}
                                </span>
                                <span class="watch-price">{lvl.price}</span>
                                {#if lvl.priority}
                                    <span class="badge priority"
                                        >P{lvl.priority}</span
                                    >
                                {/if}
                            </div>
                            {#if lvl.alert_text}
                                <div class="watch-alert">{lvl.alert_text}</div>
                            {/if}
                            {#if lvl.created_at}
                                <div class="meta">{fmtAgo(lvl.created_at)}</div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Active Scenario ── -->
        {#if (data as any).active_scenario}
            {@const sc = (data as any).active_scenario}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">🎯</span>
                    Активний сценарій
                </h3>
                <div class="card scenario-card">
                    {#if typeof sc === "object"}
                        {#each Object.entries(sc) as [k, v]}
                            {#if v != null && v !== ""}
                                <div class="scenario-row">
                                    <span class="scenario-key">{k}</span>
                                    <span class="scenario-val">{v}</span>
                                </div>
                            {/if}
                        {/each}
                    {:else}
                        <div class="scenario-text">{sc}</div>
                    {/if}
                </div>
            </section>
        {/if}

        <!-- ── Scratchpad (Notes) ── -->
        {#if arr("scratchpad").length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">📝</span>
                    Нотатки
                    <span class="counter">{arr("scratchpad").length}</span>
                </h3>
                <ul class="note-list">
                    {#each arr("scratchpad") as note, i}
                        <li class="note-item">
                            <span class="note-idx">{i + 1}</span>
                            <span class="note-text"
                                >{typeof note === "string"
                                    ? note
                                    : JSON.stringify(note)}</span
                            >
                        </li>
                    {/each}
                </ul>
            </section>
        {/if}

        <!-- ── Thought History ── -->
        {#if arr("thought_history").length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">🔄</span>
                    Думки
                    <span class="counter">{arr("thought_history").length}</span>
                </h3>
                <div class="timeline">
                    {#each [...arr("thought_history")].reverse() as thought}
                        <div class="timeline-item">
                            {#if typeof thought === "object" && thought.ts}
                                <div class="timeline-meta">
                                    <span class="timeline-time"
                                        >{fmtTime(thought.ts)}</span
                                    >
                                    {#if thought.mood}
                                        <span class="badge mood-badge"
                                            >{MOOD_ICON[thought.mood] ?? ""}
                                            {thought.mood}</span
                                        >
                                    {/if}
                                    <span class="timeline-ago"
                                        >{fmtAgo(thought.ts)}</span
                                    >
                                </div>
                                <div class="timeline-text">
                                    {thought.text ?? JSON.stringify(thought)}
                                </div>
                            {:else}
                                <div class="timeline-text">
                                    {typeof thought === "string"
                                        ? thought
                                        : JSON.stringify(thought)}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Internal Findings ── -->
        {#if arr("internal_findings").length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">🔍</span>
                    Знахідки
                    <span class="counter"
                        >{arr("internal_findings").length}</span
                    >
                </h3>
                <div class="timeline">
                    {#each [...arr("internal_findings")].reverse() as finding}
                        <div class="timeline-item finding">
                            {#if typeof finding === "object" && finding.ts}
                                <div class="timeline-meta">
                                    <span class="timeline-time"
                                        >{fmtTime(finding.ts)}</span
                                    >
                                    <span class="timeline-ago"
                                        >{fmtAgo(finding.ts)}</span
                                    >
                                </div>
                                <div class="timeline-text">
                                    {finding.text ?? JSON.stringify(finding)}
                                </div>
                            {:else}
                                <div class="timeline-text">
                                    {typeof finding === "string"
                                        ? finding
                                        : JSON.stringify(finding)}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Market Mental Model ── -->
        {#if Object.keys(obj("market_mental_model")).length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">🗺️</span>
                    Ментальна модель ринку
                </h3>
                <div class="card">
                    {#each Object.entries(obj("market_mental_model")) as [k, v]}
                        {#if v != null && v !== "" && v !== "none"}
                            <div class="model-row">
                                <span class="model-key"
                                    >{k.replaceAll("_", " ")}</span
                                >
                                <span class="model-val"
                                    >{typeof v === "object"
                                        ? JSON.stringify(v)
                                        : v}</span
                                >
                            </div>
                        {/if}
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Self Model ── -->
        {#if Object.keys(obj("self_model")).length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">🪞</span>
                    Самооцінка
                </h3>
                <div class="card">
                    {#each Object.entries(obj("self_model")) as [k, v]}
                        {#if v != null && v !== ""}
                            <div class="model-row">
                                <span class="model-key"
                                    >{k.replaceAll("_", " ")}</span
                                >
                                <span class="model-val">
                                    {#if Array.isArray(v)}
                                        {v.join(", ")}
                                    {:else if typeof v === "object"}
                                        {JSON.stringify(v)}
                                    {:else}
                                        {v}
                                    {/if}
                                </span>
                            </div>
                        {/if}
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Metacognition ── -->
        {#if Object.keys(obj("metacognition")).length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">📊</span>
                    Метакогніція
                </h3>
                <div class="card">
                    {#each Object.entries(obj("metacognition")) as [k, v]}
                        {#if v != null && v !== ""}
                            <div class="model-row">
                                <span class="model-key"
                                    >{k.replaceAll("_", " ")}</span
                                >
                                <span class="model-val">
                                    {#if Array.isArray(v)}
                                        {v.join(", ")}
                                    {:else if typeof v === "object"}
                                        {JSON.stringify(v)}
                                    {:else}
                                        {v}
                                    {/if}
                                </span>
                            </div>
                        {/if}
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Wake Timers ── -->
        {#if arr("wake_at").length}
            <section class="mind-section">
                <h3 class="section-title">
                    <span class="sec-icon">⏰</span>
                    Таймери
                    <span class="counter">{arr("wake_at").length}</span>
                </h3>
                <div class="cards">
                    {#each arr("wake_at") as timer}
                        <div class="card timer-card">
                            {#if typeof timer === "object"}
                                {#if timer.label}
                                    <div class="timer-label">{timer.label}</div>
                                {/if}
                                {#if timer.at_utc}
                                    <div class="timer-time">{timer.at_utc}</div>
                                {/if}
                                {#if timer.prompt}
                                    <div class="timer-prompt">
                                        {timer.prompt}
                                    </div>
                                {/if}
                            {:else}
                                <div class="timer-label">{timer}</div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </section>
        {/if}

        <!-- ── Operational Rules ── -->
        {#if arr("operational_rules").length}
            <section class="mind-section collapsible">
                <details>
                    <summary class="section-title clickable">
                        <span class="sec-icon">📋</span>
                        Правила
                        <span class="counter"
                            >{arr("operational_rules").length}</span
                        >
                    </summary>
                    <ul class="note-list compact">
                        {#each arr("operational_rules") as rule, i}
                            <li class="note-item">
                                <span class="note-idx">{i + 1}</span>
                                <span class="note-text"
                                    >{typeof rule === "string"
                                        ? rule
                                        : JSON.stringify(rule)}</span
                                >
                            </li>
                        {/each}
                    </ul>
                </details>
            </section>
        {/if}

        <div class="refresh-row">
            <button class="btn-ghost small" onclick={load} disabled={loading}>
                {loading ? "⟳" : "↻"} Оновити
            </button>
        </div>
    </div>
{/if}

<style>
    .mind-container {
        padding: 24px;
        max-width: 800px;
        margin: 0 auto;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    .loading,
    .error-msg {
        padding: 60px;
        text-align: center;
        color: var(--text-muted);
    }
    .error-msg {
        color: var(--danger);
    }

    /* ── Sections ── */
    .mind-section {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .mind-section.hero {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px;
    }
    .hero-row {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
    }
    .mood-icon {
        font-size: 28px;
    }
    .mood-text {
        font-size: 18px;
        font-weight: 600;
        color: var(--text);
        text-transform: capitalize;
    }
    .inner-thought {
        margin: 8px 0 0;
        padding: 10px 14px;
        border-left: 3px solid var(--accent-dim);
        background: var(--surface2);
        border-radius: 0 8px 8px 0;
        color: var(--text-muted);
        font-style: italic;
        font-size: 13px;
        line-height: 1.5;
    }

    /* ── Section Title ── */
    .section-title {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .section-title.clickable {
        cursor: pointer;
        user-select: none;
    }
    .sec-icon {
        font-size: 16px;
    }
    .counter {
        font-size: 10px;
        font-weight: 500;
        padding: 1px 6px;
        border-radius: 8px;
        background: var(--surface2);
        color: var(--text-muted);
        border: 1px solid var(--border);
    }

    /* ── Badges ── */
    .badge {
        font-size: 11px;
        font-weight: 500;
        padding: 2px 8px;
        border-radius: 6px;
        border: 1px solid var(--border);
    }
    .badge.strategy {
        background: var(--accent-dim);
        color: var(--accent);
        border-color: transparent;
    }
    .badge.budget {
        background: rgba(251, 191, 36, 0.1);
        color: var(--warning);
        border-color: rgba(251, 191, 36, 0.25);
    }
    .badge.priority {
        background: rgba(239, 68, 68, 0.12);
        color: var(--danger);
        border-color: rgba(239, 68, 68, 0.25);
        font-size: 10px;
    }
    .badge.mood-badge {
        background: var(--surface2);
        color: var(--text-muted);
        font-size: 10px;
        padding: 1px 6px;
    }

    /* ── Cards ── */
    .cards {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 14px;
    }

    /* Watch card */
    .watch-header {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .watch-dir {
        font-weight: 700;
        font-size: 14px;
    }
    .watch-dir.above {
        color: var(--positive);
    }
    .watch-dir.below {
        color: var(--danger);
    }
    .watch-price {
        font-family: var(--font-mono);
        font-size: 15px;
        font-weight: 600;
        color: var(--text);
    }
    .watch-alert {
        font-size: 12px;
        color: var(--text-muted);
        margin-top: 4px;
        line-height: 1.4;
    }
    .meta {
        font-size: 10px;
        color: var(--text-muted);
        margin-top: 4px;
    }

    /* Scenario card */
    .scenario-row {
        display: flex;
        gap: 8px;
        font-size: 12px;
        padding: 3px 0;
        border-bottom: 1px solid var(--border);
    }
    .scenario-row:last-child {
        border-bottom: none;
    }
    .scenario-key {
        flex-shrink: 0;
        color: var(--text-muted);
        min-width: 100px;
    }
    .scenario-val {
        color: var(--text);
        word-break: break-word;
    }
    .scenario-text {
        font-size: 13px;
        color: var(--text);
    }

    /* Timer card */
    .timer-card {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .timer-label {
        font-size: 13px;
        font-weight: 500;
        color: var(--text);
    }
    .timer-time {
        font-family: var(--font-mono);
        font-size: 12px;
        color: var(--accent);
    }
    .timer-prompt {
        font-size: 11px;
        color: var(--text-muted);
    }

    /* ── Notes ── */
    .note-list {
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .note-list.compact {
        gap: 2px;
    }
    .note-item {
        display: flex;
        gap: 8px;
        font-size: 13px;
        padding: 6px 10px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 6px;
    }
    .note-idx {
        flex-shrink: 0;
        color: var(--text-muted);
        font-size: 11px;
        font-family: var(--font-mono);
        min-width: 16px;
    }
    .note-text {
        color: var(--text);
        line-height: 1.4;
    }

    /* ── Timeline ── */
    .timeline {
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .timeline-item {
        padding: 10px 12px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        border-left: 3px solid var(--accent-dim);
    }
    .timeline-item.finding {
        border-left-color: var(--gold);
    }
    .timeline-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
    }
    .timeline-time {
        font-family: var(--font-mono);
        font-size: 11px;
        color: var(--accent);
    }
    .timeline-ago {
        font-size: 10px;
        color: var(--text-muted);
        margin-left: auto;
    }
    .timeline-text {
        font-size: 13px;
        color: var(--text);
        line-height: 1.45;
    }

    /* ── Model rows ── */
    .model-row {
        display: flex;
        gap: 10px;
        padding: 5px 0;
        border-bottom: 1px solid var(--border);
        font-size: 12px;
    }
    .model-row:last-child {
        border-bottom: none;
    }
    .model-key {
        flex-shrink: 0;
        min-width: 130px;
        color: var(--text-muted);
        text-transform: capitalize;
    }
    .model-val {
        color: var(--text);
        word-break: break-word;
        line-height: 1.4;
    }

    /* ── Refresh ── */
    .refresh-row {
        display: flex;
        justify-content: center;
        padding: 8px;
    }
    .btn-ghost {
        background: none;
        border: 1px solid var(--border);
        border-radius: var(--radius);
        color: var(--text-muted);
        cursor: pointer;
        padding: 6px 12px;
    }
    .btn-ghost:hover {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .btn-ghost.small {
        font-size: 12px;
        padding: 4px 10px;
    }

    details summary {
        list-style: none;
    }
    details summary::-webkit-details-marker {
        display: none;
    }
    details[open] .note-list {
        margin-top: 8px;
    }

    @media (max-width: 768px) {
        .mind-container {
            padding: 16px 12px;
        }
        .model-key {
            min-width: 90px;
        }
    }
</style>
