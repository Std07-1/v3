<!--
    MessageList — scroll container + рендер списку бабблів + typing indicator.

    Props:
      - messages: ChatMessage[]       (з chatStore.messages, упорядковані ASC за ts_ms)
      - loading: boolean              (показати placeholder завантаження)
      - awaitingReply: boolean        (показати typing-dots)
      - innerThought: string | undefined  (для dedupe — якщо archi msg.text === innerThought, приховати)
      - ttsSupported: boolean

    Events:
      - onspeak(text: string)

    Exposed methods (через bind:this):
      - scrollToBottom()
      - isNearBottom(): boolean
      - isSelectingMessageText(): boolean
-->
<script lang="ts">
    import type { ChatMessage } from "../../../lib/types";
    import MessageBubble from "./MessageBubble.svelte";

    let {
        messages,
        loading = false,
        awaitingReply = false,
        innerThought = "",
        ttsSupported = false,
        onspeak = (_text: string): void => {},
    } = $props<{
        messages: ChatMessage[];
        loading?: boolean;
        awaitingReply?: boolean;
        innerThought?: string;
        ttsSupported?: boolean;
        onspeak?: (text: string) => void;
    }>();

    let containerEl: HTMLDivElement;

    // Dedup: приховуємо Archi-повідомлення, що дублює текст поточного inner_thought.
    const filteredMessages = $derived(
        messages.filter((m: ChatMessage) => {
            if (m.role === "archi" && innerThought && m.text === innerThought) {
                return false;
            }
            return true;
        }),
    );

    export function scrollToBottom(): void {
        if (containerEl) containerEl.scrollTop = containerEl.scrollHeight;
    }

    export function isNearBottom(): boolean {
        if (!containerEl) return true;
        const remaining =
            containerEl.scrollHeight -
            containerEl.scrollTop -
            containerEl.clientHeight;
        return remaining < 72;
    }

    export function isSelectingMessageText(): boolean {
        const selection = window.getSelection?.();
        if (!selection || selection.isCollapsed) return false;
        const anchor = selection.anchorNode;
        const focus = selection.focusNode;
        return !!(
            anchor &&
            focus &&
            containerEl?.contains(anchor) &&
            containerEl.contains(focus)
        );
    }
</script>

<div class="messages" bind:this={containerEl}>
    {#if loading}
        <div class="empty-state">
            <div class="empty-icon">💬</div>
            <p>Завантаження…</p>
        </div>
    {:else if filteredMessages.length === 0}
        <div class="empty-state">
            <div class="empty-icon">💬</div>
            <p>Напиши Арчі — він прочитає і відповість</p>
            {#if innerThought}
                <blockquote class="archi-thought">"{innerThought}"</blockquote>
            {/if}
        </div>
    {:else}
        <div class="messages-spacer"></div>
        {#each filteredMessages as msg, idx (msg.id)}
            {@const prevMsg = idx > 0 ? filteredMessages[idx - 1] : null}
            {@const sameAuthor = prevMsg?.role === msg.role}
            {@const timeDiff = prevMsg ? msg.ts_ms - prevMsg.ts_ms : Infinity}
            {@const grouped = sameAuthor && timeDiff < 120_000}
            <MessageBubble
                message={msg}
                {grouped}
                {ttsSupported}
                onspeak={(t) => onspeak(t)}
            />
        {/each}
        {#if awaitingReply}
            <div class="bubble-row archi">
                <div class="bubble typing">
                    <span class="typing-dot">●</span><span
                        class="typing-dot"
                        style="animation-delay:0.15s">●</span
                    ><span class="typing-dot" style="animation-delay:0.3s">●</span>
                </div>
            </div>
        {/if}
    {/if}
</div>

<style>
    .messages {
        flex: 1;
        overflow-y: auto;
        padding: 12px 16px 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-height: 0;
        overscroll-behavior-y: contain;
    }
    .messages-spacer { flex: 1; }

    .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        gap: 10px;
        color: var(--text-muted);
        text-align: center;
    }
    .empty-icon { font-size: 36px; }
    .empty-state p { font-size: 14px; margin: 0; }

    .archi-thought {
        max-width: 320px;
        font-style: italic;
        font-size: 13px;
        color: var(--text-muted);
        border-left: 2px solid var(--accent);
        padding-left: 10px;
        margin: 0;
    }

    /* Typing-indicator bubble (MessageBubble не рендериться, бо немає message) */
    .bubble-row {
        display: flex;
        max-width: 82%;
        align-self: flex-start;
    }
    .bubble.typing {
        padding: 8px 14px;
        border-radius: 18px;
        background: var(--surface);
        border-bottom-left-radius: 6px;
    }
    .typing-dot {
        display: inline-block;
        animation: tp 1.4s infinite ease-in-out;
        font-size: 14px;
        opacity: 0.6;
    }
    @keyframes tp {
        0%, 80%, 100% { opacity: 0.15; }
        40% { opacity: 1; }
    }

    @media (max-width: 768px) {
        .bubble-row { max-width: 88%; }
    }
</style>
