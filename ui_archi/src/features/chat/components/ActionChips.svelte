<!--
    ActionChips — inline chips під archi-баблом (ADR-0053 S1).

    Замінює slash-команди: Арчі сам пропонує 1–4 продовження через
    `msg.chips[]`. Клік → primeDraft(chip) у parent → текст улітає в input
    готовий для редагування.

    Props:
      - chips: string[]            — short actionable labels (≤28 chars кожен)
      - onchipclick(text: string)  — клік по chip
-->
<script lang="ts">
    let {
        chips = [],
        onchipclick = (_text: string): void => {},
    } = $props<{
        chips?: string[];
        onchipclick?: (text: string) => void;
    }>();
</script>

{#if chips.length > 0}
    <div class="chip-row" role="group" aria-label="Пропозиції Арчі">
        {#each chips as chip, i (i)}
            <button
                type="button"
                class="chip"
                onclick={() => onchipclick(chip)}
                title="Вставити у поле вводу"
            >{chip}</button>
        {/each}
    </div>
{/if}

<style>
    .chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 4px 0 2px;
        max-width: 82%;
        align-self: flex-start;
    }
    .chip {
        padding: 5px 12px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border));
        background: color-mix(in srgb, var(--accent) 8%, var(--surface));
        color: var(--text);
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        transition: background 0.15s, border-color 0.15s, transform 0.1s;
        white-space: nowrap;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .chip:hover {
        background: color-mix(in srgb, var(--accent) 18%, var(--surface));
        border-color: var(--accent);
    }
    .chip:active { transform: scale(0.97); }

    @media (max-width: 768px) {
        .chip-row { max-width: 88%; }
        .chip { font-size: 12.5px; padding: 6px 13px; }
    }
</style>
