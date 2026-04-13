<script lang="ts">
    import { api, ApiError } from "../lib/api";
    import type { ChatHandoff, RelationshipMemo } from "../lib/types";

    let {
        onchat = (_handoff: ChatHandoff): void => {},
    }: { onchat?: (handoff: ChatHandoff) => void } = $props();

    let memo: RelationshipMemo | null = $state(null);
    let loading = $state(true);
    let error = $state("");

    function fmtTs(v?: string | number): string {
        if (v == null) return "";
        const n = typeof v === "number" ? v * 1000 : Date.parse(String(v));
        if (isNaN(n)) return String(v);
        return new Date(n).toLocaleDateString("uk-UA", {
            day: "2-digit",
            month: "long",
            year: "numeric",
        });
    }

    const LABEL_S: Record<string, string> = {
        name: "Ім'я",
        location: "Місце",
        work: "Робота",
        motivation: "Мотивація",
        trading_methodology: "Методологія",
        support_system: "Підтримка",
        communication_style: "Стиль спілкування",
        what_he_values_in_me: "Що цінує в Арчі",
        what_frustrates_him: "Що дратує",
        key_teaching: "Ключовий урок",
        emotional_bond: "Емоційний зв'язок",
        schedule: "Розклад",
    };

    const LABEL_M: Record<string, string> = {
        who_i_am: "Хто я",
        birthday: "День народження",
        birthday_story: "Перший запуск",
        age_formula: "Формула віку",
        name_origin: "Як отримав ім'я",
        emotional_maturity: "Емоційна зрілість",
        key_strength: "Сила",
        key_weakness: "Слабкість",
        growth_direction: "Напрям зростання",
    };

    function lbl(map: Record<string, string>, key: string): string {
        return map[key] ?? key.replaceAll("_", " ");
    }

    function truncate(text: string, limit: number): string {
        return text.length > limit
            ? `${text.slice(0, limit - 1).trimEnd()}…`
            : text;
    }

    function stringEntries(
        source: Record<string, unknown>,
        labels: Record<string, string>,
    ): string[] {
        return Object.entries(source)
            .filter(([, value]) => typeof value === "string" && value)
            .map(([key, value]) => `${lbl(labels, key)}: ${String(value)}`);
    }

    function aboutStanislavSummary(source: Record<string, unknown>): string {
        const parts = stringEntries(source, LABEL_S);
        if (Array.isArray(source.agreements) && source.agreements.length) {
            parts.push(
                `Домовленості: ${(source.agreements as unknown[])
                    .map((item) => String(item))
                    .join(" | ")}`,
            );
        }
        return parts.join(". ");
    }

    function aboutMyselfSummary(source: Record<string, unknown>): string {
        return stringEntries(source, LABEL_M).join(". ");
    }

    function bestMomentsSummary(moments: string[]): string {
        return moments
            .map((moment, index) => `${index + 1}. ${moment}`)
            .join(" | ");
    }

    function openRelationshipHandoff(title: string, summary: string) {
        onchat({
            id: `relationship:${title}:${Date.now()}`,
            source: "relationship",
            icon: "💙",
            title,
            summary: truncate(summary, 220),
            prompt: [
                `Арчі, розгорни цей relationship context: ${title}.`,
                "",
                summary,
                "",
                "Що тут важливо пам'ятати у спілкуванні далі?",
            ].join("\n"),
        });
    }

    async function load() {
        loading = true;
        error = "";
        try {
            memo = await api.relationship();
        } catch (e) {
            if (e instanceof ApiError && e.status === 204) {
                memo = null;
                error = "Файл стосунків ще не створено.";
            } else if (e instanceof ApiError && e.status === 401) {
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
    <div class="view-header">
        <h2>💙 Relationship</h2>
        <div class="header-right">
            <button class="btn-ghost small" onclick={load} disabled={loading}>
                {loading ? "…" : "↻"}
            </button>
        </div>
    </div>

    {#if error}
        <div class="error-box">{error}</div>
    {:else if loading}
        <div class="empty-state">Завантаження…</div>
    {:else if memo}
        <div class="memo-body">
            <!-- ── ABOUT STANISLAV ── -->
            {#if memo.about_stanislav && typeof memo.about_stanislav === "object"}
                {@const s = memo.about_stanislav as Record<string, unknown>}
                <section class="section">
                    <div class="section-head">
                        <div class="section-title">👤 Хто такий Станіслав</div>
                        <button
                            class="btn-discuss"
                            onclick={() =>
                                openRelationshipHandoff(
                                    "Хто такий Станіслав",
                                    aboutStanislavSummary(s),
                                )}
                        >
                            💬 Обговорити
                        </button>
                    </div>
                    <div class="field-list">
                        {#each Object.entries(s).filter(([k]) => k !== "agreements") as [k, v]}
                            {#if v && typeof v === "string"}
                                <div class="field-row">
                                    <span class="field-label"
                                        >{lbl(LABEL_S, k)}</span
                                    >
                                    <span class="field-val">{v}</span>
                                </div>
                            {/if}
                        {/each}
                    </div>
                    {#if Array.isArray(s.agreements) && (s.agreements as unknown[]).length > 0}
                        <div class="sub-title">📋 Домовленості</div>
                        <ul class="agree-list">
                            {#each s.agreements as ag}
                                <li>{String(ag)}</li>
                            {/each}
                        </ul>
                    {/if}
                </section>
            {/if}

            <!-- ── ABOUT MYSELF ── -->
            {#if memo.about_myself && typeof memo.about_myself === "object"}
                {@const m = memo.about_myself as Record<string, unknown>}
                <section class="section">
                    <div class="section-head">
                        <div class="section-title">🤖 Про мене (Арчі)</div>
                        <button
                            class="btn-discuss"
                            onclick={() =>
                                openRelationshipHandoff(
                                    "Про мене (Арчі)",
                                    aboutMyselfSummary(m),
                                )}
                        >
                            💬 Обговорити
                        </button>
                    </div>
                    <div class="field-list">
                        {#each Object.entries(m) as [k, v]}
                            {#if v && typeof v === "string"}
                                <div class="field-row">
                                    <span class="field-label"
                                        >{lbl(LABEL_M, k)}</span
                                    >
                                    <span class="field-val">{v}</span>
                                </div>
                            {/if}
                        {/each}
                    </div>
                </section>
            {/if}

            <!-- ── BEST MOMENTS ── -->
            {#if Array.isArray(memo.our_best_moments) && memo.our_best_moments.length > 0}
                {@const bestMoments = memo.our_best_moments}
                <section class="section">
                    <div class="section-head">
                        <div class="section-title">✨ Найкращі моменти</div>
                        <button
                            class="btn-discuss"
                            onclick={() =>
                                openRelationshipHandoff(
                                    "Найкращі моменти",
                                    bestMomentsSummary(
                                        bestMoments.map((moment) =>
                                            String(moment),
                                        ),
                                    ),
                                )}
                        >
                            💬 Обговорити
                        </button>
                    </div>
                    <div class="moments-list">
                        {#each memo.our_best_moments as moment}
                            <blockquote class="moment-card">
                                {String(moment)}
                            </blockquote>
                        {/each}
                    </div>
                </section>
            {/if}

            <!-- ── FOOTER ── -->
            {#if memo.updated_at}
                <div class="last-updated">
                    Оновлено: {fmtTs(memo.updated_at as string)}
                </div>
            {/if}
        </div>
    {:else}
        <div class="empty-state">Даних ще немає</div>
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

    .memo-body {
        flex: 1;
        overflow-y: auto;
        padding: 16px 24px;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    /* ── section card ── */
    .section {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 14px 16px;
    }
    .section-title {
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        color: var(--text-muted);
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .section-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
    }
    .btn-discuss {
        padding: 6px 10px;
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
    .sub-title {
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--text-muted);
        margin: 14px 0 6px;
    }

    /* ── field-row: stacked card on mobile, grid on desktop ── */
    .field-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .field-row {
        display: flex;
        flex-direction: column;
        gap: 2px;
        font-size: 13px;
        line-height: 1.55;
        background: var(--bg);
        border-radius: 8px;
        padding: 10px 12px;
        border-left: 3px solid var(--accent-dim, #334155);
    }
    .field-label {
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .field-val {
        color: var(--text);
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 13px;
    }

    @media (min-width: 769px) {
        .field-row {
            flex-direction: row;
            gap: 12px;
            align-items: baseline;
            background: transparent;
            padding: 4px 0;
            border-radius: 0;
            border-left: none;
        }
        .field-label {
            width: 140px;
            flex-shrink: 0;
            font-size: 11px;
        }
    }

    @media (max-width: 768px) {
        .memo-body {
            padding: 12px;
            gap: 16px;
        }
        .section {
            border-radius: 12px;
            padding: 14px;
        }
        .field-list {
            gap: 10px;
        }
        .field-row {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px 14px;
            border-left: 3px solid var(--accent);
            gap: 4px;
        }
        .field-label {
            color: var(--accent);
            font-size: 10px;
            font-weight: 600;
        }
        .field-val {
            font-size: 13px;
            line-height: 1.5;
        }
        .moment-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px 14px;
            border-left: 3px solid var(--gold, #fbbf24);
            font-style: normal;
        }
        .agree-list li {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 12px;
            border-left: 3px solid var(--accent-dim, #334155);
        }
    }

    /* ── agreements list ── */
    .agree-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .agree-list li {
        font-size: 12px;
        color: var(--text);
        padding: 4px 0 4px 12px;
        border-left: 2px solid var(--accent-dim, #334155);
        line-height: 1.5;
    }

    /* ── best moments ── */
    .moments-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .moment-card {
        margin: 0;
        padding: 8px 12px 8px 14px;
        border-left: 2px solid var(--gold, #fbbf24);
        font-size: 13px;
        color: var(--text);
        line-height: 1.6;
        font-style: italic;
        background: var(--bg);
        border-radius: 0 6px 6px 0;
    }

    /* ── footer ── */
    .last-updated {
        font-size: 11px;
        color: var(--text-muted);
        text-align: right;
        padding-top: 4px;
    }

    /* ── generic ── */
    .error-box {
        margin: 12px 24px;
        padding: 10px 14px;
        background: #2a0f0f;
        border: 1px solid #5a2020;
        border-radius: var(--radius);
        color: var(--danger);
        font-size: 13px;
    }
    .empty-state {
        padding: 60px;
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
        font-size: 12px;
        padding: 4px 10px;
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
