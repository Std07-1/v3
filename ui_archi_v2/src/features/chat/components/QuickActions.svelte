<!--
    QuickActions — рядок швидких дій (pills) над інпутом.

    Props:
      - actions: { icon: string; label: string; text: string }[]
      - compact: boolean (true = зменшений padding-top, коли під іншим контентом)

    Events:
      - onselect(text: string) — клік по pill-у
-->
<script lang="ts">
    export type QuickAction = {
        icon: string;
        label: string;
        text: string;
    };

    let {
        actions = [],
        compact = false,
        onselect = (_text: string): void => {},
    } = $props<{
        actions?: QuickAction[];
        compact?: boolean;
        onselect?: (text: string) => void;
    }>();
</script>

{#if actions.length > 0}
    <div class="quick-actions" class:compact>
        {#each actions as act}
            <button
                class="qa-btn"
                onclick={() => onselect(act.text)}
                title={act.text}
            >
                <span class="qa-icon">{act.icon}</span>
                <span class="qa-label">{act.label}</span>
            </button>
        {/each}
    </div>
{/if}

<style>
    .quick-actions {
        display: flex;
        gap: 6px;
        padding: 0 0 10px;
        margin: 0;
        background: transparent;
        flex-shrink: 0;
        overflow-x: auto;
        scrollbar-width: none;
    }
    .quick-actions.compact { padding-top: 2px; }
    .quick-actions::-webkit-scrollbar { display: none; }

    .qa-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 12px;
        border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
        border-radius: 20px;
        background: var(--surface2);
        color: var(--text-muted);
        cursor: pointer;
        font-size: 12px;
        white-space: nowrap;
        flex-shrink: 0;
        scroll-snap-align: start;
        transition: border-color 0.15s, color 0.15s, background 0.15s;
    }
    .qa-btn:hover {
        border-color: var(--accent);
        color: var(--text);
        background: var(--surface2);
    }
    .qa-icon { font-size: 13px; }
    .qa-label { font-weight: 500; }

    @media (max-width: 768px) {
        .quick-actions { padding-bottom: 8px; }
    }
</style>
