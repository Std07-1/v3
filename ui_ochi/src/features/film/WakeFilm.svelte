<!--
    WakeFilm — кіноплівка пробуджень: хронологічний потік WakeCard (newest-first).

    Драйвиться filmStore (init 30 → poll верхівки 20s → «Показати ще» тягне старіші
    через before_ts). Degraded-but-loud: store.error видимий рядком; порожньо →
    чесний empty-state. X28: жодного домену — лише композиція карток.
-->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import { filmStore } from "../../stores/filmStore.svelte";
    import WakeCard from "./WakeCard.svelte";

    let { nowMs }: { nowMs: number } = $props();

    // Межа епох: картки з дзеркалом (ADR-097) vs старіші без нього. Замість
    // «недоступне» на кожній старій картці — ОДИН роздільник на межі (i5 чесно,
    // без 200 повторів шуму). Якщо дзеркал ще нема взагалі — один рядок зверху.
    let anyTrace = $derived(filmStore.cards.some((c) => c.trace));

    function isEpochBoundary(i: number): boolean {
        if (i === 0) return false;
        const prev = filmStore.cards[i - 1];
        const cur = filmStore.cards[i];
        return Boolean(prev.trace) && !cur.trace;
    }

    onMount(() => {
        void filmStore.init();
    });
    onDestroy(() => {
        filmStore.shutdown();
    });
</script>

<section class="film">
    <div class="film-head">
        <h2 class="title">Плівка пробуджень</h2>
        {#if !filmStore.loading}
            <span class="count">{filmStore.cards.length}</span>
        {/if}
    </div>

    {#if filmStore.error}
        <div class="film-error">⚠ Не вдалося завантажити плівку: {filmStore.error}</div>
    {/if}

    {#if filmStore.loading}
        <div class="film-empty">Завантаження…</div>
    {:else if filmStore.cards.length === 0}
        <div class="film-empty">Пробуджень поки немає.</div>
    {:else}
        {#if !anyTrace}
            <div class="epoch-note">
                👁 Дзеркала пробуджень почали записуватись (ADR-097) — зʼявляться
                на нових картках із наступного пробудження.
            </div>
        {/if}
        <div class="cards">
            {#each filmStore.cards as card, i (card.wake_id || card.ts)}
                {#if isEpochBoundary(i)}
                    <div class="epoch-divider">
                        — старіші пробудження: до запису дзеркал (ADR-097) —
                    </div>
                {/if}
                <WakeCard {card} {nowMs} />
            {/each}
        </div>

        {#if filmStore.hasMore}
            <button
                class="more"
                onclick={() => filmStore.loadMore()}
                disabled={filmStore.loadingMore}
            >
                {filmStore.loadingMore ? "Завантаження…" : "Показати ще"}
            </button>
        {:else}
            <div class="film-end">— початок плівки —</div>
        {/if}
    {/if}
</section>

<style>
    .film {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }
    .film-head {
        display: flex;
        align-items: baseline;
        gap: 10px;
        padding: 0 4px;
    }
    .title {
        font-size: 14px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: var(--text);
    }
    .count {
        font-family: var(--font-mono);
        font-size: 12px;
        color: var(--text-muted);
    }
    .film-error {
        font-size: 12.5px;
        color: var(--danger);
        font-family: var(--font-mono);
        padding: 8px 12px;
        border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--border));
        border-radius: 8px;
    }
    .film-empty,
    .film-end {
        text-align: center;
        font-size: 12.5px;
        color: var(--text-muted);
        padding: 18px;
        letter-spacing: 0.04em;
    }
    .epoch-note {
        font-size: 12px;
        color: var(--text-muted);
        padding: 8px 12px;
        border: 1px dashed var(--border);
        border-radius: 8px;
        line-height: 1.5;
    }
    .epoch-divider {
        text-align: center;
        font-size: 11px;
        color: var(--text-muted);
        letter-spacing: 0.06em;
        padding: 4px 0 0;
        opacity: 0.8;
    }
    .cards {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }
    .more {
        margin: 4px auto 0;
        padding: 10px 24px;
        border: 1px solid var(--card-border);
        border-radius: 999px;
        background: var(--surface);
        color: var(--text);
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        transition: border-color 0.2s, background 0.2s, transform 0.12s;
    }
    .more:hover:not(:disabled) {
        border-color: var(--accent);
        background: var(--surface2);
    }
    .more:active:not(:disabled) {
        transform: translateY(1px);
    }
    .more:disabled {
        opacity: 0.55;
        cursor: default;
    }
</style>
