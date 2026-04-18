<script lang="ts">
    import { api, ApiError } from "../lib/api";
    import { getDirectives, refreshDirectives } from "../lib/state.svelte";
    import type {
        ChatHandoff,
        Directives,
        OwnerNote,
        ImprovementProposal,
    } from "../lib/types";

    let {
        onchat = (_handoff: ChatHandoff): void => {},
    }: { onchat?: (handoff: ChatHandoff) => void } = $props();

    let data: Directives | null = $state(null);
    let loading = $state(true);
    let error = $state("");

    // ── Owner note state ──
    let ownerNote = $state<OwnerNote | null>(null);
    let noteText = $state("");
    let noteStatus = $state("");
    let noteSaving = $state(false);
    let noteSaved = $state(false);
    let noteEditing = $state(false);

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

    function formatValue(value: unknown): string {
        if (value == null || value === "") return "";
        if (Array.isArray(value)) return value.map(formatValue).join(", ");
        if (typeof value === "object") {
            return Object.entries(value as Record<string, unknown>)
                .map(([key, item]) => `${key}: ${formatValue(item)}`)
                .filter(Boolean)
                .join("; ");
        }
        return String(value);
    }

    function summarizeScenario(scenario: unknown): string {
        if (typeof scenario === "string") return scenario;
        if (!scenario || typeof scenario !== "object")
            return "сценарій не задано";
        return Object.entries(scenario as Record<string, unknown>)
            .map(([key, value]) => `${key}: ${formatValue(value)}`)
            .filter((line) => !line.endsWith(": "))
            .join(". ");
    }

    function handoffPrompt(
        title: string,
        summary: string,
        context?: string,
    ): string {
        const lines = [
            `Арчі, розбери цей контекст із Mind: ${title}.`,
            "",
            summary,
            context ? `Контекст: ${context}` : "",
            "",
            "Що тут головне і що з цього потрібно врахувати далі?",
        ].filter(Boolean);
        return lines.join("\n");
    }

    function openMindHandoff(title: string, summary: string, context = "") {
        onchat({
            id: `mind:${title}:${Date.now()}`,
            source: "mind",
            icon: "🧩",
            title,
            summary: truncate(summary, 220),
            prompt: handoffPrompt(title, summary, context),
        });
    }

    function currentMindSummary(): string {
        const mood = str("mood") || "невідомий настрій";
        const thought = str("inner_thought") || "inner thought відсутня";
        const scenario = (data as any)?.active_scenario;
        const scenarioText = summarizeScenario(scenario);
        return `Настрій: ${mood}. Внутрішня думка: ${thought}. Активний сценарій: ${scenarioText}.`;
    }

    async function load() {
        loading = true;
        error = "";
        try {
            await refreshDirectives(false); // brief=0 → full state
            data = getDirectives();
            // Load owner note in parallel
            api.ownerNote()
                .then((n) => {
                    ownerNote = n;
                    if (!noteText && n.text) noteText = n.text;
                    if (!noteStatus && n.status) noteStatus = n.status;
                })
                .catch(() => {});
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

    async function saveNote() {
        if (noteSaving) return;
        noteSaving = true;
        try {
            await api.saveOwnerNote({ text: noteText, status: noteStatus });
            noteSaved = true;
            setTimeout(() => {
                noteSaved = false;
            }, 1500);
        } catch {
            /* quiet */
        } finally {
            noteSaving = false;
        }
    }

    // ── ADR-028 P3 J5: Proposal review ──
    let proposalReviewing = $state<string | null>(null); // proposal id being processed
    let proposalError = $state("");

    async function handleProposalReview(id: string, approved: boolean) {
        if (proposalReviewing) return;
        proposalReviewing = id;
        proposalError = "";
        try {
            await api.reviewProposal(id, approved);
            await load(); // refresh directives to reflect new status
        } catch (e) {
            proposalError = approved
                ? "Помилка підтвердження"
                : "Помилка відхилення";
            setTimeout(() => {
                proposalError = "";
            }, 3000);
        } finally {
            proposalReviewing = null;
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

<div class="view">
    <!-- ── Header ── -->
    <div class="view-header">
        <h2>🧩 Mind</h2>
        <div class="header-right">
            {#if data}
                <span class="ts-muted">{str("mood") || "—"}</span>
            {/if}
            <button
                class="btn-ghost small"
                onclick={load}
                disabled={loading}
            >
                {loading ? "…" : "↻ Refresh"}
            </button>
        </div>
    </div>

    <!-- ── Error ── -->
    {#if error}
        <div class="error-box">{error}</div>
    {/if}

    {#if loading && !data}
        <div class="empty-state">Завантаження стану…</div>
    {:else if data}
        <div class="mind-body">
            <!-- ── Hero: mood + inner thought ── -->
            <section class="mind-section hero-section">
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
                    <button
                        class="btn-discuss"
                        onclick={() =>
                            openMindHandoff(
                                "Поточний стан Арчі",
                                currentMindSummary(),
                            )}
                    >
                        💬 Обговорити в Chat
                    </button>
                </div>
                {#if str("inner_thought")}
                    <blockquote class="inner-thought">
                        "{str("inner_thought")}"
                    </blockquote>
                {/if}
            </section>

            <!-- ── Owner Note for Archi ── -->
            <section class="mind-section owner-note-section">
                <h3 class="section-title">
                    <span class="sec-icon">📝</span>
                    Нотатка для Арчі
                </h3>
                {#if !noteEditing}
                    <!-- Card display mode -->
                    <div
                        class="note-card"
                        role="button"
                        tabindex="0"
                        onclick={() => {
                            noteEditing = true;
                        }}
                        onkeydown={(e) => {
                            if (e.key === "Enter") noteEditing = true;
                        }}
                    >
                        {#if noteStatus}
                            <div class="nc-status">{noteStatus}</div>
                        {/if}
                        {#if noteText}
                            <div class="nc-text">{noteText}</div>
                        {:else}
                            <div class="nc-placeholder">
                                Натисни щоб додати нотатку…
                            </div>
                        {/if}
                        {#if ownerNote?.updated_at}
                            <div class="nc-footer">
                                {fmtAgo(Number(ownerNote.updated_at))}
                            </div>
                        {/if}
                    </div>
                {:else}
                    <!-- Edit mode -->
                    <div class="note-edit">
                        <input
                            class="note-status-input"
                            type="text"
                            bind:value={noteStatus}
                            placeholder="Статус (працюю / відпочиваю / аналізую)"
                            maxlength="100"
                        />
                        <textarea
                            class="note-textarea"
                            bind:value={noteText}
                            placeholder="Думки, контекст для Арчі…"
                            rows={3}
                            maxlength="500"
                        ></textarea>
                        <div class="note-actions">
                            <span class="note-counter">{noteText.length}/500</span>
                            <button
                                class="btn-note-cancel"
                                onclick={() => {
                                    noteEditing = false;
                                }}>Скасувати</button
                            >
                            <button
                                class="btn-note-save"
                                onclick={() => {
                                    saveNote();
                                    noteEditing = false;
                                }}
                                disabled={noteSaving}
                            >
                                {noteSaving ? "⟳" : "✓ Зберегти"}
                            </button>
                        </div>
                    </div>
                {/if}
                {#if noteSaved}
                    <div class="note-toast">✓ Збережено</div>
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
                    <div class="cards-list">
                        {#each arr("watch_levels") as lvl}
                            <div class="mind-card">
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
                                    <div class="ts-muted">{fmtAgo(lvl.created_at)}</div>
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
                    <div class="section-head">
                        <h3 class="section-title">
                            <span class="sec-icon">🎯</span>
                            Активний сценарій
                        </h3>
                        <button
                            class="btn-discuss"
                            onclick={() =>
                                openMindHandoff(
                                    "Active Scenario",
                                    summarizeScenario(sc),
                                    str("inner_thought"),
                                )}
                        >
                            💬 Обговорити
                        </button>
                    </div>
                    <div class="mind-card">
                        {#if typeof sc === "object"}
                            {#each Object.entries(sc) as [k, v]}
                                {#if v != null && v !== ""}
                                    <div class="kv-row">
                                        <span class="kv-key">{k}</span>
                                        <span class="kv-val">{v}</span>
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
                    <div class="note-list">
                        {#each arr("scratchpad") as note, i}
                            <div class="note-item">
                                <span class="note-idx">{i + 1}</span>
                                <span class="note-text"
                                    >{typeof note === "string"
                                        ? note
                                        : JSON.stringify(note)}</span
                                >
                            </div>
                        {/each}
                    </div>
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
                                        <span class="entry-ts"
                                            >{fmtTime(thought.ts)}</span
                                        >
                                        {#if thought.mood}
                                            <span class="badge mood-badge"
                                                >{MOOD_ICON[thought.mood] ?? ""}
                                                {thought.mood}</span
                                            >
                                        {/if}
                                        <span class="ts-muted ml-auto"
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
                                        <span class="entry-ts"
                                            >{fmtTime(finding.ts)}</span
                                        >
                                        <span class="ts-muted ml-auto"
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
                    <div class="mind-card">
                        {#each Object.entries(obj("market_mental_model")) as [k, v]}
                            {#if v != null && v !== "" && v !== "none"}
                                <div class="kv-row">
                                    <span class="kv-key"
                                        >{k.replaceAll("_", " ")}</span
                                    >
                                    <span class="kv-val"
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
                    <div class="mind-card">
                        {#each Object.entries(obj("self_model")) as [k, v]}
                            {#if v != null && v !== ""}
                                <div class="kv-row">
                                    <span class="kv-key"
                                        >{k.replaceAll("_", " ")}</span
                                    >
                                    <span class="kv-val">
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
                    <div class="mind-card">
                        {#each Object.entries(obj("metacognition")) as [k, v]}
                            {#if v != null && v !== ""}
                                <div class="kv-row">
                                    <span class="kv-key"
                                        >{k.replaceAll("_", " ")}</span
                                    >
                                    <span class="kv-val">
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
                    <div class="cards-list">
                        {#each arr("wake_at") as timer}
                            <div class="mind-card">
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
                <section class="mind-section">
                    <details>
                        <summary class="section-title clickable">
                            <span class="sec-icon">📋</span>
                            Правила
                            <span class="counter"
                                >{arr("operational_rules").length}</span
                            >
                        </summary>
                        <div class="note-list compact">
                            {#each arr("operational_rules") as rule, i}
                                <div class="note-item">
                                    <span class="note-idx">{i + 1}</span>
                                    <span class="note-text"
                                        >{typeof rule === "string"
                                            ? rule
                                            : JSON.stringify(rule)}</span
                                    >
                                </div>
                            {/each}
                        </div>
                    </details>
                </section>
            {/if}

            <!-- ── ADR-028 P3 J5: Improvement Proposals ── -->
            {#if arr("improvement_proposals").filter((p: ImprovementProposal) => p.status === "pending").length}
                <section class="mind-section">
                    <div class="section-title">
                        <span class="sec-icon">💡</span>
                        Пропозиції Арчі
                        <span class="badge-pending">
                            {arr("improvement_proposals").filter(
                                (p: ImprovementProposal) => p.status === "pending",
                            ).length}
                        </span>
                    </div>
                    {#if proposalError}
                        <div class="proposal-error">{proposalError}</div>
                    {/if}
                    {#each arr("improvement_proposals").filter((p: ImprovementProposal) => p.status === "pending") as proposal (proposal.id)}
                        <div class="proposal-card">
                            <div class="proposal-header">
                                <span class="badge proposal-type"
                                    >{proposal.type}</span
                                >
                                <span class="ts-muted ml-auto"
                                    >{fmtAgo(proposal.ts)}</span
                                >
                            </div>
                            <div class="proposal-rule">
                                {proposal.proposed_rule}
                            </div>
                            {#if proposal.evidence}
                                <div class="proposal-detail">
                                    📊 {proposal.evidence}
                                </div>
                            {/if}
                            {#if proposal.reasoning}
                                <div class="proposal-detail">
                                    💭 {proposal.reasoning}
                                </div>
                            {/if}
                            {#if proposal.alternatives_considered?.length}
                                <div class="proposal-detail">
                                    Альтернативи: {proposal.alternatives_considered.join(
                                        " / ",
                                    )}
                                </div>
                            {/if}
                            <div class="proposal-actions">
                                <button
                                    class="btn-approve"
                                    onclick={() =>
                                        handleProposalReview(proposal.id, true)}
                                    disabled={proposalReviewing === proposal.id}
                                >
                                    {proposalReviewing === proposal.id
                                        ? "…"
                                        : "✓ Прийняти"}
                                </button>
                                <button
                                    class="btn-reject"
                                    onclick={() =>
                                        handleProposalReview(proposal.id, false)}
                                    disabled={proposalReviewing === proposal.id}
                                >
                                    {proposalReviewing === proposal.id
                                        ? "…"
                                        : "✗ Відхилити"}
                                </button>
                            </div>
                        </div>
                    {/each}
                </section>
            {/if}
        </div>
    {/if}
</div>

<style>
    /* ── View container (matches Feed/Thinking) ── */
    .view {
        display: flex;
        flex-direction: column;
        height: 100%;
    }

    /* ── Header (matches Feed/Thinking exactly) ── */
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

    /* ── Error box (matches Feed/Thinking) ── */
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

    /* ── Empty state (matches Feed/Thinking) ── */
    .empty-state {
        padding: 48px;
        text-align: center;
        color: var(--text-muted);
        font-size: 14px;
    }

    /* ── Scrollable body (card-style padding like Thinking) ── */
    .mind-body {
        flex: 1;
        overflow-y: auto;
        padding: 16px 24px;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    /* ── Sections ── */
    .mind-section {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    /* ── Hero section ── */
    .hero-section {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 16px;
    }
    .hero-row {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
    }
    .mood-icon {
        font-size: 24px;
    }
    .mood-text {
        font-size: 16px;
        font-weight: 600;
        color: var(--text);
        text-transform: capitalize;
    }
    .inner-thought {
        margin: 10px 0 0;
        padding: 10px 14px;
        border-left: 3px solid var(--accent-dim);
        background: var(--surface2);
        border-radius: 0 8px 8px 0;
        color: var(--text-muted);
        font-style: italic;
        font-size: 13px;
        line-height: 1.5;
    }

    /* ── Section title (consistent across Mind) ── */
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
    .section-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
    }
    .section-title.clickable {
        cursor: pointer;
        user-select: none;
    }
    .sec-icon {
        font-size: 15px;
    }
    .counter {
        font-size: 11px;
        font-weight: 500;
        padding: 2px 7px;
        border-radius: 4px;
        background: var(--surface2);
        color: var(--text-muted);
        border: 1px solid var(--border);
    }

    /* ── Badges (matches Feed/Thinking exactly) ── */
    .badge {
        font-size: 11px;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 500;
    }
    .badge.strategy {
        background: var(--accent-dim);
        color: #c0b8ff;
    }
    .badge.budget {
        background: rgba(251, 191, 36, 0.18);
        color: #fbbf24;
    }
    .badge.priority {
        background: rgba(248, 113, 113, 0.15);
        color: var(--danger);
        font-size: 10px;
        padding: 1px 5px;
    }
    .badge.mood-badge {
        background: var(--surface2);
        color: var(--text-muted);
        font-size: 10px;
        padding: 1px 6px;
    }
    .badge.proposal-type {
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        background: rgba(245, 158, 11, 0.14);
        color: #f59e0b;
    }

    /* ── Discuss buttons (matches Feed pill pattern) ── */
    .btn-discuss {
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, var(--surface2));
        color: var(--text);
        cursor: pointer;
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        transition:
            border-color 0.15s,
            background 0.15s,
            color 0.15s;
    }
    .btn-discuss:hover {
        border-color: color-mix(in srgb, var(--accent) 44%, transparent);
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
    }

    /* ── ts-muted + ml-auto (matches Feed/Thinking) ── */
    .ts-muted {
        font-size: 12px;
        color: var(--text-muted);
    }
    .ml-auto {
        margin-left: auto;
    }

    /* ── btn-ghost (matches Feed/Thinking exactly) ── */
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

    /* ── Cards (consistent card pattern) ── */
    .cards-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .mind-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 14px;
    }

    /* ── Watch level cards ── */
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
        font-size: 14px;
        font-weight: 600;
        color: var(--text);
    }
    .watch-alert {
        font-size: 12px;
        color: var(--text-muted);
        margin-top: 4px;
        line-height: 1.4;
    }

    /* ── KV rows (scenario, mental model, self model, metacognition) ── */
    .kv-row {
        display: flex;
        gap: 10px;
        padding: 5px 0;
        border-bottom: 1px solid var(--border);
        font-size: 12px;
    }
    .kv-row:last-child {
        border-bottom: none;
    }
    .kv-key {
        flex-shrink: 0;
        min-width: 120px;
        color: var(--text-muted);
        text-transform: capitalize;
    }
    .kv-val {
        color: var(--text);
        word-break: break-word;
        line-height: 1.4;
    }
    .scenario-text {
        font-size: 13px;
        color: var(--text);
    }

    /* ── Timer cards ── */
    .timer-label {
        font-size: 13px;
        font-weight: 500;
        color: var(--text);
    }
    .timer-time {
        font-family: var(--font-mono);
        font-size: 12px;
        color: var(--accent);
        margin-top: 2px;
    }
    .timer-prompt {
        font-size: 11px;
        color: var(--text-muted);
        margin-top: 2px;
    }

    /* ── Note list (scratchpad, rules) ── */
    .note-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .note-list.compact {
        gap: 2px;
        margin-top: 8px;
    }
    .note-item {
        display: flex;
        gap: 8px;
        font-size: 13px;
        padding: 8px 12px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
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

    /* ── Timeline (thoughts, findings — matches Thinking entry-card style) ── */
    .timeline {
        display: flex;
        flex-direction: column;
    }
    .timeline-item {
        padding: 12px 14px;
        border-bottom: 1px solid var(--border);
        border-left: 3px solid var(--accent-dim);
    }
    .timeline-item:last-child {
        border-bottom: none;
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
    .entry-ts {
        font-size: 12px;
        font-weight: 500;
        color: var(--text);
        font-family: var(--font-mono);
    }
    .timeline-text {
        font-size: 13px;
        color: var(--text);
        line-height: 1.5;
    }

    /* ── Owner Note ── */
    .owner-note-section {
        position: relative;
    }
    .note-card {
        cursor: pointer;
        border-radius: 8px;
        padding: 10px 12px;
        background: var(--surface);
        border: 1px solid var(--border);
        transition:
            border-color 0.2s,
            background 0.2s;
    }
    .note-card:hover {
        border-color: var(--accent);
    }
    .nc-status {
        font-size: 12px;
        font-weight: 600;
        color: var(--accent);
        margin-bottom: 4px;
        text-transform: capitalize;
    }
    .nc-text {
        font-size: 13px;
        line-height: 1.5;
        color: var(--text);
        white-space: pre-wrap;
    }
    .nc-placeholder {
        font-size: 13px;
        color: var(--text-muted);
        font-style: italic;
    }
    .nc-footer {
        font-size: 10px;
        color: var(--text-muted);
        margin-top: 6px;
        text-align: right;
    }
    /* Edit mode */
    .note-edit {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .note-status-input {
        padding: 8px 12px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text);
        font-size: 13px;
        outline: none;
    }
    .note-status-input:focus {
        border-color: var(--accent);
    }
    .note-textarea {
        padding: 10px 12px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text);
        font-size: 13px;
        resize: vertical;
        min-height: 60px;
        outline: none;
        font-family: inherit;
        line-height: 1.5;
    }
    .note-textarea:focus {
        border-color: var(--accent);
    }
    .note-actions {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .note-counter {
        font-size: 11px;
        color: var(--text-muted);
        margin-right: auto;
    }
    .btn-note-cancel {
        padding: 6px 12px;
        background: none;
        color: var(--text-muted);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        font-size: 12px;
        cursor: pointer;
        transition: color 0.15s;
    }
    .btn-note-cancel:hover {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .btn-note-save {
        padding: 6px 14px;
        background: var(--accent);
        color: white;
        border: none;
        border-radius: var(--radius);
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: filter 0.15s;
    }
    .btn-note-save:hover {
        filter: brightness(1.1);
    }
    .btn-note-save:disabled {
        opacity: 0.5;
        cursor: default;
    }
    .note-toast {
        position: absolute;
        top: 0;
        right: 0;
        font-size: 11px;
        font-weight: 600;
        color: #34d399;
        animation: toast-fade 1.5s ease-out forwards;
    }
    @keyframes toast-fade {
        0% {
            opacity: 1;
            transform: translateY(0);
        }
        70% {
            opacity: 1;
        }
        100% {
            opacity: 0;
            transform: translateY(-6px);
        }
    }

    /* ── Improvement Proposals ── */
    .badge-pending {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: #f59e0b;
        color: #1a1a1a;
        border-radius: 10px;
        padding: 1px 7px;
        font-size: 11px;
        font-weight: 700;
        margin-left: 4px;
    }
    .proposal-card {
        background: var(--surface);
        border: 1px solid rgba(245, 158, 11, 0.25);
        border-radius: 8px;
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .proposal-header {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .proposal-rule {
        font-size: 14px;
        font-weight: 600;
        color: var(--text);
        line-height: 1.4;
    }
    .proposal-detail {
        font-size: 12px;
        color: var(--text-muted);
        line-height: 1.4;
    }
    .proposal-actions {
        display: flex;
        gap: 8px;
        margin-top: 4px;
    }
    .btn-approve {
        flex: 1;
        padding: 7px 14px;
        background: #16a34a;
        color: #fff;
        border: none;
        border-radius: var(--radius);
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: filter 0.15s;
    }
    .btn-approve:hover:not(:disabled) {
        filter: brightness(1.15);
    }
    .btn-approve:disabled {
        opacity: 0.5;
        cursor: default;
    }
    .btn-reject {
        flex: 1;
        padding: 7px 14px;
        background: var(--surface);
        color: var(--danger);
        border: 1px solid var(--danger);
        border-radius: var(--radius);
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition:
            filter 0.15s,
            background 0.15s,
            color 0.15s;
    }
    .btn-reject:hover:not(:disabled) {
        background: var(--danger);
        color: #fff;
    }
    .btn-reject:disabled {
        opacity: 0.5;
        cursor: default;
    }
    .proposal-error {
        font-size: 12px;
        color: var(--danger);
        padding: 4px 0;
    }

    /* ── Details/collapsible ── */
    details summary {
        list-style: none;
    }
    details summary::-webkit-details-marker {
        display: none;
    }

    /* ── Responsive ── */
    @media (max-width: 768px) {
        .mind-body {
            padding: 12px 16px;
        }
        .kv-key {
            min-width: 90px;
        }
    }
</style>
