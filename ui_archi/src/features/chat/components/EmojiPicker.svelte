<!--
    EmojiPicker — Telegram-style categorized emoji picker.

    Encapsulates:
      - trigger button (😊)
      - anchored panel з табами і grid-ом
      - внутрішній state (open, активна категорія)
      - 4 категорії emoji (smiles / gestures / hearts-fire / markets)

    Props:
      - disabled: boolean (блокує toggle)

    Events:
      - oninsert(emoji: string) — клік по emoji cell (panel сам закривається)
-->
<script lang="ts">
    import Icon from "../../../lib/Icon.svelte";

    let {
        disabled = false,
        oninsert = (_emoji: string): void => {},
    } = $props<{
        disabled?: boolean;
        oninsert?: (emoji: string) => void;
    }>();

    const EMOJI_CATS: { icon: string; emojis: string[] }[] = [
        {
            icon: "😀",
            emojis: ["😊","😂","🤣","😎","🤔","😏","🙄","😤","😴","🥳","🫡","😈"],
        },
        {
            icon: "👋",
            emojis: ["👍","👎","👏","🤝","💪","✋","🤞","👀","🙏","🫶","✌️","🤙"],
        },
        {
            icon: "❤️",
            emojis: ["❤️","🔥","⚡","💎","🎯","⭐","💰","🏆","🚀","💡","🔮","🎰"],
        },
        {
            icon: "📈",
            emojis: ["📈","📉","💹","🟢","🔴","⚠️","🏦","🪙","📊","💵","🐂","🐻"],
        },
    ];

    let open = $state(false);
    let cat = $state(0);
    let anchorEl: HTMLElement;
    let alignLeft = $state(true); // панель відкривається у бік де є місце

    function toggle(): void {
        if (!open) {
            const r = anchorEl?.getBoundingClientRect();
            // кнопка в лівій половині → панель ліворуч-якорена (відкрити праворуч)
            alignLeft = r ? r.left < window.innerWidth / 2 : true;
        }
        open = !open;
    }

    function pick(e: string): void {
        oninsert(e);
        open = false;
    }

    // Outside-dismiss: тап поза якорем/панеллю або Esc → закрити.
    $effect(() => {
        if (!open) return;
        const onDown = (e: Event) => {
            if (anchorEl && !anchorEl.contains(e.target as Node)) open = false;
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") open = false;
        };
        // setTimeout — щоб не зловити сам клік відкриття
        const id = setTimeout(() => {
            document.addEventListener("pointerdown", onDown);
            document.addEventListener("keydown", onKey);
        }, 0);
        return () => {
            clearTimeout(id);
            document.removeEventListener("pointerdown", onDown);
            document.removeEventListener("keydown", onKey);
        };
    });
</script>

<div class="emoji-anchor" bind:this={anchorEl}>
    <button
        class="ia-btn"
        onclick={toggle}
        {disabled}
        title="Емодзі"
        aria-label="Відкрити панель емодзі"
    ><Icon name="smile" size={18} /></button>
    {#if open}
        <div
            class="emoji-panel"
            class:align-left={alignLeft}
            class:align-right={!alignLeft}
        >
            <div class="emoji-tabs">
                {#each EMOJI_CATS as c, ci}
                    <button
                        class="emoji-tab"
                        class:active={cat === ci}
                        onclick={() => {
                            cat = ci;
                        }}
                        title="Категорія"
                        aria-label="Категорія емодзі {ci + 1}"
                    >{c.icon}</button>
                {/each}
            </div>
            <div class="emoji-grid">
                {#each EMOJI_CATS[cat].emojis as em}
                    <button
                        class="emoji-cell"
                        onclick={() => pick(em)}
                        aria-label="Вставити {em}"
                    >{em}</button>
                {/each}
            </div>
        </div>
    {/if}
</div>

<style>
    .ia-btn {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: none;
        background: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--text-muted);
        transition: background 0.15s, color 0.15s;
    }
    .ia-btn:hover {
        background: color-mix(in srgb, var(--accent) 12%, var(--surface2));
        color: var(--text);
    }
    @media (max-width: 768px) {
        .ia-btn { width: 36px; height: 36px; }
    }

    .emoji-anchor { position: relative; }
    .emoji-panel {
        position: absolute;
        bottom: calc(100% + 8px);
        width: min(280px, calc(100vw - 24px));
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 14px;
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.45);
        z-index: 200;
        overflow: hidden;
        animation: emojiIn 0.15s ease-out;
    }
    .emoji-panel.align-left { left: 0; }
    .emoji-panel.align-right { right: 0; }
    @keyframes emojiIn {
        from { opacity: 0; transform: translateY(8px) scale(0.95); }
        to { opacity: 1; transform: none; }
    }
    .emoji-tabs {
        display: flex;
        border-bottom: 1px solid var(--border);
    }
    .emoji-tab {
        flex: 1;
        padding: 8px 0;
        font-size: 16px;
        background: none;
        border: none;
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition: border-color 0.15s;
    }
    .emoji-tab.active { border-bottom-color: var(--accent); }
    .emoji-tab:hover { background: var(--surface2); }

    .emoji-grid {
        display: grid;
        grid-template-columns: repeat(6, 1fr);
        gap: 2px;
        padding: 8px;
    }
    .emoji-cell {
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 22px;
        border: none;
        background: none;
        border-radius: 8px;
        cursor: pointer;
        transition: background 0.1s, transform 0.1s;
    }
    .emoji-cell:hover {
        background: var(--surface2);
        transform: scale(1.15);
    }
</style>
