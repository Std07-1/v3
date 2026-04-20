<!--
    PulseBoard — dumb Pulse Rail (Living Platform).

    Показує 4 можливі картки (observation / change / decision / care) плюс freshness
    індикатор на правій стороні хедера.

    Props:
      - cards: PulseCard[]            — обчислено у Chat.svelte через buildPulseCards()
      - freshness: PulseFreshness     — обчислено через buildPulseFreshness()
      - oncardaction?: (prompt) => void — клік по "У чернетку" → primeDraft
-->
<script lang="ts">
    import type {
        PulseCard,
        PulseFreshness,
    } from "../lib/hearthHelpers";

    let {
        cards,
        freshness,
        oncardaction = (_prompt: string): void => {},
    } = $props<{
        cards: PulseCard[];
        freshness: PulseFreshness;
        oncardaction?: (prompt: string) => void;
    }>();
</script>

<div class="pulse-board">
    <div class="pulse-board-head">
        <div>
            <div class="pulse-board-kicker">Living Platform</div>
            <div class="pulse-board-title">Pulse Rail</div>
        </div>
        <div class="pulse-board-summary">
            <div class="pulse-board-copy">
                Живий контур shell: observation beat, остання зміна, decision
                trail і care loop без виходу з conversation stage.
            </div>
            <div class="pulse-board-status" data-tone={freshness.tone}>
                {freshness.label}
            </div>
        </div>
    </div>

    {#if freshness.detail}
        <div class="pulse-board-warning">{freshness.detail}</div>
    {/if}

    <div class="pulse-cards">
        {#each cards as card (card.id)}
            <article class={`pulse-card ${card.tone}`}>
                <div class="pulse-card-top">
                    <span class="pulse-card-kicker">{card.kicker}</span>
                    {#if card.meta}
                        <span class="pulse-card-meta">{card.meta}</span>
                    {/if}
                </div>

                <div class="pulse-card-title">{card.title}</div>
                <div class="pulse-card-detail">{card.detail}</div>

                <button
                    class="pulse-card-btn"
                    onclick={() => oncardaction(card.prompt)}
                >
                    У чернетку
                </button>
            </article>
        {/each}
    </div>
</div>

<style>
    .pulse-board {
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 12px 16px 14px;
        border-bottom: 1px solid var(--border);
        background: radial-gradient(
                circle at top right,
                rgba(255, 180, 90, 0.11),
                transparent 36%
            ),
            radial-gradient(
                circle at left center,
                rgba(92, 205, 180, 0.09),
                transparent 34%
            ),
            color-mix(in srgb, var(--surface) 95%, var(--bg));
        flex-shrink: 0;
    }
    .pulse-board-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
    }
    .pulse-board-kicker {
        font-size: 10px;
        color: #f6a84a;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .pulse-board-title {
        font-size: 14px;
        font-weight: 600;
        color: var(--text);
    }
    .pulse-board-copy {
        max-width: 420px;
        font-size: 12px;
        line-height: 1.45;
        color: var(--text-muted);
    }
    .pulse-board-summary {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 6px;
    }
    .pulse-board-status {
        align-self: flex-end;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
        background: color-mix(in srgb, var(--surface2) 88%, var(--bg));
        color: var(--text-muted);
    }
    .pulse-board-status[data-tone="live"] {
        border-color: rgba(92, 205, 180, 0.28);
        color: #53caae;
    }
    .pulse-board-status[data-tone="pending"] {
        border-color: rgba(255, 180, 90, 0.28);
        color: #f6a84a;
    }
    .pulse-board-status[data-tone="stale"] {
        border-color: rgba(239, 95, 71, 0.3);
        color: #ef5f47;
    }
    .pulse-board-warning {
        padding: 9px 11px;
        border-radius: 12px;
        border: 1px solid rgba(239, 95, 71, 0.18);
        background: rgba(239, 95, 71, 0.08);
        color: var(--text);
        font-size: 11px;
        line-height: 1.45;
    }
    .pulse-cards {
        display: grid;
        gap: 10px;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    }
    .pulse-card {
        display: flex;
        flex-direction: column;
        gap: 8px;
        min-height: 146px;
        padding: 12px;
        border-radius: 16px;
        border: 1px solid color-mix(in srgb, var(--border) 86%, transparent);
        background: linear-gradient(
                180deg,
                rgba(255, 255, 255, 0.03),
                rgba(255, 255, 255, 0)
            ),
            color-mix(in srgb, var(--surface2) 88%, var(--bg));
        box-shadow: 0 12px 24px rgba(6, 10, 18, 0.08);
    }
    .pulse-card.live { border-color: rgba(92, 205, 180, 0.26); }
    .pulse-card.change { border-color: rgba(255, 180, 90, 0.28); }
    .pulse-card.ledger { border-color: rgba(120, 164, 255, 0.28); }
    .pulse-card.care { border-color: rgba(255, 123, 158, 0.28); }
    .pulse-card.risk { border-color: rgba(239, 95, 71, 0.32); }
    .pulse-card-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
    }
    .pulse-card-kicker {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-muted);
    }
    .pulse-card-meta {
        font-size: 10px;
        color: var(--text-muted);
        text-align: right;
    }
    .pulse-card-title {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        line-height: 1.4;
        display: -webkit-box;
        line-clamp: 3;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .pulse-card-detail {
        flex: 1;
        font-size: 12px;
        color: var(--text-muted);
        line-height: 1.55;
        display: -webkit-box;
        line-clamp: 4;
        -webkit-line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .pulse-card-btn {
        align-self: flex-start;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, var(--surface2));
        color: var(--text);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition:
            border-color 0.15s,
            background 0.15s,
            transform 0.15s;
    }
    .pulse-card-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 44%, transparent);
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
        transform: translateY(-1px);
    }

    @media (max-width: 768px) {
        .pulse-board-head { flex-direction: column; }
        .pulse-board-summary { align-items: flex-start; }
        .pulse-board-status { align-self: flex-start; }
        .pulse-cards {
            grid-template-columns: none;
            grid-auto-flow: column;
            grid-auto-columns: minmax(228px, 78vw);
            overflow-x: auto;
            padding-bottom: 2px;
            scroll-snap-type: x proximity;
        }
        .pulse-card { scroll-snap-align: start; }
    }
</style>
