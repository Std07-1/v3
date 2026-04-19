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

    function pick(e: string): void {
        oninsert(e);
        open = false;
    }
</script>

<div class="emoji-anchor">
    <button
        class="ia-btn"
        onclick={() => {
            open = !open;
        }}
        {disabled}
        title="Емодзі"
        aria-label="Відкрити панель емодзі"
    >😊</button>
    {#if open}
        <div class="emoji-panel">
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
        width: 38px;
        height: 38px;
        border-radius: 50%;
        border: none;
        background: none;
        cursor: pointer;
        font-size: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--text-muted);
        transition: background 0.15s, color 0.15s;
    }
    .ia-btn:hover {
        background: var(--surface2);
        color: var(--text);
    }

    .emoji-anchor { position: relative; }
    .emoji-panel {
        position: absolute;
        bottom: 48px;
        right: 0;
        width: 280px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 14px;
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
        z-index: 60;
        overflow: hidden;
        animation: emojiIn 0.15s ease-out;
    }
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

    @media (max-width: 768px) {
        .ia-btn { width: 42px; height: 42px; }
        .emoji-panel { width: 260px; right: -12px; }
    }
</style>
