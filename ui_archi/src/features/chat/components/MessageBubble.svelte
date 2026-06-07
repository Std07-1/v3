<!--
    MessageBubble — один баббл чату (user / archi). Design: Б v2 (presence через mood-фон).

    Props:
      - message: ChatMessage
      - grouped: boolean (примикає до попереднього того ж автора)
      - ttsSupported: boolean (кнопка "озвучити" для archi)

    Events:
      - onspeak(text: string)

    Design (ADR — chat depth Б v2):
      - Архі: настрій живе на ФОНІ баблів (mood-tint, тече з --accent) — без orb-аватара.
      - Ти: солідний сірий, тихий — без accent.
      - Цифри/рівні: mono + тонкий тон (без кольорового бокса).
      - Борделес: розділення фоном+тінню. Солідні поверхні (текст не на чорному — анти-halation).

    Security (T1 XSS):
      - user: escapeHtml + \n→<br />
      - archi: marked.parse → sanitizeHtml (allowlist)
      - highlightNumbers застосовується ПІСЛЯ sanitize, lookahead не чіпає вміст тегів.
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

    /** Підсвічує ціни/рівні/час (mono-tint). Lookahead `(?![^<]*>)` пропускає
        вміст тегів — не ламає атрибути/розмітку у вже-санітизованому HTML. */
    function highlightNumbers(html: string): string {
        return html.replace(
            /(\d{1,2}:\d{2}|\d{4}(?:\.\d+)?(?:-\d{4})?)(?![^<]*>)/g,
            '<span class="num">$1</span>',
        );
    }

    function renderMessageHtml(msg: ChatMessage): string {
        if (msg.role === "user") {
            const safe = escapeHtml(msg.text).replace(/\n/g, "<br />");
            return highlightNumbers(safe);
        }
        const rendered = marked.parse(msg.text) as string;
        return highlightNumbers(sanitizeHtml(rendered));
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
        max-width: 80%;
        position: relative;
    }
    .bubble-row.user { align-self: flex-end; }
    .bubble-row.archi { align-self: flex-start; }
    .bubble-row.grouped { margin-top: -4px; }

    .bubble {
        padding: 9px 14px;
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

    /* ── Ти: солідний сірий, тихий (без accent) ── */
    .bubble-row.user .bubble {
        background: var(--surface2);
        color: var(--text);
        border-bottom-right-radius: 6px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    }
    .bubble-row.user.grouped .bubble { border-top-right-radius: 6px; }

    /* ── Архі: настрій на ФОНІ баблів (тече з --accent). Солідна поверхня —
       текст лежить на surface, не на чорному (анти-halation). ── */
    .bubble-row.archi .bubble {
        background: linear-gradient(135deg,
            color-mix(in srgb, var(--accent) 15%, var(--surface)) 0%,
            color-mix(in srgb, var(--accent) 6%, var(--surface)) 100%);
        color: var(--text);
        border-bottom-left-radius: 6px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    }
    .bubble-row.archi.grouped .bubble { border-top-left-radius: 6px; }

    .bubble-text {
        font-size: 14.5px;
        line-height: 1.55;
        white-space: pre-wrap;
    }
    .bubble-text :global(p) { margin: 0 0 0.55em; }
    .bubble-text :global(p:last-child) { margin-bottom: 0; }
    .bubble-text :global(strong) { font-weight: 650; }
    .bubble-text :global(blockquote) {
        margin: 0.45em 0;
        padding-left: 10px;
        border-left: 2px solid color-mix(in srgb, var(--accent) 35%, transparent);
        color: var(--text-muted);
    }
    .bubble-text :global(code) {
        font-family: var(--font-mono);
        font-size: 0.88em;
        background: color-mix(in srgb, var(--surface2) 70%, transparent);
        padding: 1px 4px;
        border-radius: 4px;
    }
    /* Цифри/рівні — спокійні: mono + тонкий тон, БЕЗ кольорового бокса */
    .bubble-text :global(.num) {
        font-family: var(--font-mono);
        font-size: 0.9em;
        color: color-mix(in srgb, var(--accent) 32%, var(--text));
    }

    .bubble-meta {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 5px;
        margin-top: 2px;
    }
    .bubble-ts { font-size: 10px; opacity: 0.4; }

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
