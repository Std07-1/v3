<!--
    MessageBubble — один баббл чату (user / archi).

    Props:
      - message: ChatMessage
      - grouped: boolean (true = без відступу зверху, примикає до попереднього того ж автора)
      - ttsSupported: boolean (показувати кнопку "озвучити" для archi бабблів)

    Events:
      - onspeak(text: string) — клік на кнопці TTS

    Security (T1 XSS):
      - user bubbles: escapeHtml + \n → <br />
      - archi bubbles: marked.parse → sanitizeHtml (src/lib/sanitize.ts, allowlist tags)
-->
<script lang="ts">
    import { marked } from "marked";
    import type { ChatMessage } from "../../../lib/types";
    import { sanitizeHtml } from "../../../lib/sanitize";

    let {
        message,
        grouped = false,
        ttsSupported = false,
        onspeak = (_text: string): void => {},
    } = $props<{
        message: ChatMessage;
        grouped?: boolean;
        ttsSupported?: boolean;
        onspeak?: (text: string) => void;
    }>();

    function escapeHtml(text: string): string {
        return text.replace(/[&<>"']/g, (char) => {
            switch (char) {
                case "&": return "&amp;";
                case "<": return "&lt;";
                case ">": return "&gt;";
                case '"': return "&quot;";
                case "'": return "&#39;";
                default: return char;
            }
        });
    }

    function renderMessageHtml(msg: ChatMessage): string {
        if (msg.role === "user") {
            return escapeHtml(msg.text).replace(/\n/g, "<br />");
        }
        const rendered = marked.parse(msg.text) as string;
        return sanitizeHtml(rendered);
    }

    function formatTs(ts_ms: number): string {
        const d = new Date(ts_ms);
        const h = d.getHours().toString().padStart(2, "0");
        const m = d.getMinutes().toString().padStart(2, "0");
        return `${h}:${m}`;
    }

    let bubbleHtml = $derived(renderMessageHtml(message));
    let timeLabel = $derived(formatTs(message.ts_ms));
    let isTelegramSource = $derived((message as any).source === "telegram");
</script>

<div
    class="bubble-row"
    class:user={message.role === "user"}
    class:archi={message.role === "archi"}
    class:grouped
>
    <div class="bubble">
        <div class="bubble-text prose">{@html bubbleHtml}</div>
        <div class="bubble-meta">
            <span class="bubble-ts">{timeLabel}</span>
            {#if isTelegramSource}
                <span class="src-tg">TG</span>
            {/if}
            {#if message.role === "archi" && ttsSupported}
                <button
                    class="btn-tts"
                    onclick={() => onspeak(message.text)}
                    title="Озвучити"
                    aria-label="Озвучити повідомлення"
                >🔊</button>
            {/if}
        </div>
    </div>
</div>

<style>
    .bubble-row {
        display: flex;
        max-width: 82%;
        position: relative;
    }
    .bubble-row.user { align-self: flex-end; }
    .bubble-row.archi { align-self: flex-start; }
    .bubble-row.grouped { margin-top: -2px; }

    .bubble {
        padding: 8px 14px;
        border-radius: 18px;
        max-width: 100%;
        word-break: break-word;
        position: relative;
    }
    .bubble,
    .bubble-text {
        user-select: text;
        -webkit-user-select: text;
        -webkit-touch-callout: default;
    }
    .bubble-text :global(*) {
        user-select: text;
        -webkit-user-select: text;
        -webkit-touch-callout: default;
    }
    .bubble-row.user .bubble {
        background: color-mix(in srgb, var(--accent) 22%, var(--surface));
        color: var(--text);
        border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
        border-bottom-right-radius: 6px;
    }
    .bubble-row.user.grouped .bubble { border-top-right-radius: 6px; }
    .bubble-row.archi .bubble {
        background: var(--surface);
        color: var(--text);
        border-bottom-left-radius: 6px;
    }
    .bubble-row.archi.grouped .bubble { border-top-left-radius: 6px; }

    .bubble-text {
        font-size: 14.5px;
        line-height: 1.5;
        white-space: pre-wrap;
    }
    .bubble-text :global(p) { margin: 0 0 0.55em; }
    .bubble-text :global(p:last-child) { margin-bottom: 0; }
    .bubble-text :global(strong) { font-weight: 650; }
    .bubble-text :global(blockquote) {
        margin: 0.45em 0;
        padding-left: 10px;
        border-left: 2px solid color-mix(in srgb, var(--accent) 45%, transparent);
        color: var(--text-muted);
    }
    .bubble-text :global(code) {
        font-family: var(--font-mono);
        font-size: 0.88em;
        background: var(--surface2);
        padding: 1px 4px;
        border-radius: 4px;
    }
    .bubble-row.user .bubble-text :global(code) {
        background: color-mix(in srgb, var(--accent) 18%, var(--surface2));
    }

    .bubble-meta {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 5px;
        margin-top: 2px;
    }
    .bubble-ts { font-size: 10px; opacity: 0.45; }
    .bubble-row.user .bubble-ts { opacity: 0.45; }

    .src-tg {
        font-size: 8px;
        font-weight: 700;
        padding: 1px 4px;
        border-radius: 3px;
        background: rgba(0, 136, 204, 0.15);
        color: #0088cc;
    }

    .btn-tts {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 11px;
        padding: 0;
        opacity: 0;
        transition: opacity 0.15s;
    }
    .bubble:hover .btn-tts { opacity: 0.5; }
    .btn-tts:hover { opacity: 1 !important; }

    @media (max-width: 768px) {
        .bubble-row { max-width: 88%; }
    }
</style>
