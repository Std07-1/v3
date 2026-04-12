<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import { api } from "../lib/api";
    import type { ChatMessage, Directives, AgentState } from "../lib/types";

    // ── props: prefill from Thinking quick reply ──
    let { prefill = "" }: { prefill?: string } = $props();

    // ── state ──
    let messages = $state<ChatMessage[]>([]);
    let inputText = $state(prefill);
    let sending = $state(false);
    let loading = $state(true);
    let error = $state("");

    // Market pulse
    let directives = $state<Directives | null>(null);
    let agentState = $state<AgentState | null>(null);

    // Voice
    let listening = $state(false);
    let voiceSupported = $state(false);
    let voiceError = $state("");
    let recognition: any = null;

    // Emoji
    let showEmoji = $state(false);
    const EMOJI_SETS: { label: string; emojis: string[] }[] = [
        { label: "Часті", emojis: ["👍", "🔥", "💰", "📊", "🎯", "⚡", "✅", "❌", "🤔", "💪", "🚀", "⏰"] },
        { label: "Ринок", emojis: ["📈", "📉", "🐂", "🐻", "💎", "🏦", "⚠️", "🛡️", "🔑", "🧲", "🪙", "💸"] },
        { label: "Настрій", emojis: ["😌", "😤", "🤯", "😎", "🫡", "🙏", "👀", "💀", "🧠", "❤️", "😂", "🫠"] },
    ];
    function insertEmoji(e: string) {
        inputText += e;
        showEmoji = false;
        textareaEl?.focus();
    }

    // Quick actions
    const QUICK_ACTIONS = [
        { label: "/mind", text: "Покажи свій стан", icon: "🧩" },
        { label: "/bias", text: "Який твій поточний bias?", icon: "🧭" },
        { label: "/levels", text: "Які рівні спостерігаєш?", icon: "👁" },
        { label: "/plan", text: "Який план на сьогодні?", icon: "📋" },
        { label: "/review", text: "Зроби self-review", icon: "🪞" },
    ];

    // Scroll + textarea refs
    let messagesEl: HTMLDivElement;
    let textareaEl: HTMLTextAreaElement;

    // Auto-grow textarea
    function autoGrow() {
        if (!textareaEl) return;
        textareaEl.style.height = "auto";
        textareaEl.style.height = Math.min(textareaEl.scrollHeight, 140) + "px";
    }

    // ── bias helpers ──
    function getBias(dir: Directives | null): { label: string; color: string } {
        const bm = dir?.bias_map as Record<string, unknown> | null;
        if (!bm) return { label: "", color: "" };
        // look at H4 or D1 first
        const priority = ["H4", "D1", "H1"];
        for (const tf of priority) {
            const val = bm[tf];
            if (typeof val === "string") {
                const lower = val.toLowerCase();
                if (lower.includes("bull") || lower.includes("long"))
                    return { label: `${tf}: bull`, color: "bull" };
                if (lower.includes("bear") || lower.includes("short"))
                    return { label: `${tf}: bear`, color: "bear" };
            }
        }
        return { label: "", color: "" };
    }

    function getWatchLevels(dir: Directives | null): {
        up: number | null;
        down: number | null;
    } {
        const wl = dir?.watch_levels;
        if (!Array.isArray(wl)) return { up: null, down: null };
        let up: number | null = null;
        let down: number | null = null;
        for (const lvl of wl as Array<{ direction?: string; price?: number }>) {
            if (lvl.direction === "above" && lvl.price) up = lvl.price;
            if (lvl.direction === "below" && lvl.price) down = lvl.price;
        }
        return { up, down };
    }

    function formatTs(ts_ms: number): string {
        const d = new Date(ts_ms);
        const h = d.getHours().toString().padStart(2, "0");
        const m = d.getMinutes().toString().padStart(2, "0");
        return `${h}:${m}`;
    }

    // ── data fetch ──
    async function loadData() {
        try {
            [directives, messages] = await Promise.all([
                api.directives(false).catch(() => null),
                api
                    .chatHistory(80)
                    .then((r) => r.messages ?? [])
                    .catch(() => []),
            ]);
            api.agentState()
                .catch(() => null)
                .then((s) => {
                    if (s) agentState = s;
                });
        } catch {
            // graceful
        } finally {
            loading = false;
            await tick();
            scrollToBottom();
        }
    }

    function scrollToBottom() {
        if (messagesEl) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    }

    // ── send message ──
    async function sendMessage() {
        const text = inputText.trim();
        if (!text || sending) return;
        inputText = "";
        if (textareaEl) textareaEl.style.height = "auto";
        sending = true;
        error = "";

        // Optimistic local add for user message
        const tmpMsg: ChatMessage = {
            id: `u_tmp_${Date.now()}`,
            role: "user",
            text,
            ts_ms: Date.now(),
        };
        // Optimistic placeholder for Archi's reply
        const tmpArchi: ChatMessage = {
            id: `a_tmp_${Date.now()}`,
            role: "archi",
            text: "…",
            ts_ms: Date.now() + 1,
        };
        messages = [...messages, tmpMsg, tmpArchi];
        await tick();
        scrollToBottom();

        try {
            const res = await api.chatSend(text);
            // Replace tmp user msg + tmp archi with real versions
            const realUser = res.message;
            const realArchi = res.archi_response;
            messages = messages
                .map((m) => (m.id === tmpMsg.id ? realUser : m))
                .map((m) => {
                    if (m.id === tmpArchi.id) {
                        return (
                            realArchi ?? {
                                ...tmpArchi,
                                text: "Помилка відповіді",
                            }
                        );
                    }
                    return m;
                });
        } catch {
            error = "Повідомлення не відправлено. Спробуй ще раз.";
            messages = messages.filter(
                (m) => m.id !== tmpMsg.id && m.id !== tmpArchi.id,
            );
        } finally {
            sending = false;
            await tick();
            scrollToBottom();
        }
    }

    function handleKeydown(e: KeyboardEvent) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
        // close emoji on Escape
        if (e.key === "Escape" && showEmoji) {
            showEmoji = false;
        }
    }

    // ── voice input ──
    function initVoice() {
        const SR =
            (window as any).SpeechRecognition ||
            (window as any).webkitSpeechRecognition;
        if (!SR) {
            voiceSupported = false;
            return;
        }
        voiceSupported = true;
        recognition = new SR();
        recognition.lang = "uk-UA";
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onresult = (e: any) => {
            const transcript = e.results[0]?.[0]?.transcript ?? "";
            if (transcript) inputText = (inputText + " " + transcript).trim();
        };
        recognition.onerror = (e: any) => {
            voiceError =
                e.error === "not-allowed" ? "Мікрофон заблоковано" : e.error;
            listening = false;
        };
        recognition.onend = () => {
            listening = false;
        };
    }

    function toggleVoice() {
        if (!voiceSupported || !recognition) return;
        if (listening) {
            recognition.stop();
            listening = false;
        } else {
            voiceError = "";
            recognition.start();
            listening = true;
        }
    }

    // ── polling for new messages (every 10s) ──
    let pollId: ReturnType<typeof setInterval>;

    async function pollMessages() {
        if (sending) return; // don't overwrite optimistic messages while waiting
        try {
            const result = await api.chatHistory(80);
            if (result.messages.length !== messages.length) {
                messages = result.messages;
                await tick();
                scrollToBottom();
            }
        } catch {
            /* silent */
        }
    }

    // ── lifecycle ──
    onMount(() => {
        loadData();
        initVoice();
        pollId = setInterval(pollMessages, 10_000);
        // also refresh agent state
        setInterval(() => {
            api.agentState()
                .catch(() => null)
                .then((s) => {
                    if (s) agentState = s;
                });
        }, 30_000);
    });

    onDestroy(() => {
        clearInterval(pollId);
        if (recognition && listening) recognition.stop();
    });

    // prefill reactive: when prop changes, update input
    $effect(() => {
        if (prefill) inputText = prefill;
    });

    const bias = $derived(getBias(directives));
    const levels = $derived(getWatchLevels(directives));
</script>

<!-- ── Market Pulse Header ── -->
<header class="pulse-bar">
    <div class="pulse-symbol">
        {directives?.focus_symbol ?? "XAU/USD"}
    </div>

    {#if bias.label}
        <span class="pulse-chip {bias.color}">{bias.label}</span>
    {/if}

    {#if levels.up !== null || levels.down !== null}
        <span class="pulse-levels">
            {#if levels.up !== null}<span class="lvl up"
                    >▲ {levels.up.toFixed(0)}</span
                >{/if}
            {#if levels.down !== null}<span class="lvl dn"
                    >▼ {levels.down.toFixed(0)}</span
                >{/if}
        </span>
    {/if}

    {#if agentState?.market_session}
        <span class="pulse-chip session">{agentState.market_session}</span>
    {:else if directives?.mode}
        <span class="pulse-chip mode">{directives.mode}</span>
    {/if}

    {#if agentState?.model_current}
        <span class="pulse-model"
            >{agentState.model_current
                .replace("claude-", "")
                .replace("-latest", "")}</span
        >
    {:else if directives}
        <span class="pulse-model">archi</span>
    {/if}
</header>

<!-- ── Messages Area ── -->
<div class="messages" bind:this={messagesEl}>
    {#if loading}
        <div class="empty-state">Завантаження...</div>
    {:else if messages.length === 0}
        <div class="empty-state">
            <div class="empty-icon">💬</div>
            <p>Напиши Арчі — він прочитає і відповість</p>
            {#if directives?.inner_thought}
                <blockquote class="archi-thought">
                    "{directives.inner_thought}"
                </blockquote>
            {/if}
        </div>
    {:else}
        <!-- Spacer pushes messages to bottom when few -->
        <div class="messages-spacer"></div>
        {#each messages as msg (msg.id)}
            <div
                class="bubble-row"
                class:user={msg.role === "user"}
                class:archi={msg.role === "archi"}
            >
                {#if msg.role === "archi"}
                    <div class="avatar">⬡</div>
                {/if}
                <div class="bubble">
                    {#if msg.role === "archi" && msg.text === "…"}
                        <div class="bubble-text">
                            <span class="typing-dot">●</span><span
                                class="typing-dot"
                                style="animation-delay:0.2s">●</span
                            ><span
                                class="typing-dot"
                                style="animation-delay:0.4s">●</span
                            >
                        </div>
                    {:else}
                        <div class="bubble-text">{msg.text}</div>
                    {/if}
                    <div class="bubble-ts">{formatTs(msg.ts_ms)}</div>
                </div>
            </div>
        {/each}
    {/if}
</div>

<!-- ── Quick Actions ── -->
<div class="quick-actions">
    {#each QUICK_ACTIONS as act}
        <button
            class="qa-btn"
            onclick={() => { inputText = act.text; textareaEl?.focus(); }}
            title={act.text}
        >
            <span class="qa-icon">{act.icon}</span>
            <span class="qa-label">{act.label}</span>
        </button>
    {/each}
</div>

<!-- ── Input Bar ── -->
<div class="input-bar">
    {#if error}
        <div class="input-error">{error}</div>
    {/if}

    <!-- Emoji picker -->
    {#if showEmoji}
        <div class="emoji-picker">
            {#each EMOJI_SETS as group}
                <div class="emoji-group">
                    <div class="emoji-group-label">{group.label}</div>
                    <div class="emoji-grid">
                        {#each group.emojis as em}
                            <button class="emoji-btn" onclick={() => insertEmoji(em)}>{em}</button>
                        {/each}
                    </div>
                </div>
            {/each}
        </div>
    {/if}

    <div class="input-row">
        <!-- Emoji toggle -->
        <button
            class="btn-icon"
            class:active={showEmoji}
            onclick={() => { showEmoji = !showEmoji; }}
            title="Емодзі"
            aria-label="Емодзі"
        >
            😊
        </button>

        <!-- Voice button -->
        <button
            class="btn-icon"
            class:active={listening}
            class:recording={listening}
            onclick={toggleVoice}
            title={voiceSupported
                ? listening
                    ? "Зупинити"
                    : "Голосовий ввід (uk-UA)"
                : "Голос не підтримується"}
            disabled={!voiceSupported}
            aria-label="Голосовий ввід"
        >
            {listening ? "🔴" : "🎤"}
        </button>

        <textarea
            class="chat-input"
            bind:this={textareaEl}
            bind:value={inputText}
            oninput={autoGrow}
            onkeydown={handleKeydown}
            onfocus={() => { showEmoji = false; }}
            placeholder="Напиши Арчі..."
            rows={1}
            disabled={sending}
        ></textarea>

        <button
            class="btn-send"
            onclick={sendMessage}
            disabled={sending || !inputText.trim()}
            aria-label="Відправити"
        >
            {sending ? "⏳" : "➤"}
        </button>
    </div>
    {#if voiceError}
        <div class="voice-error">{voiceError}</div>
    {/if}
</div>

<style>
    :host,
    :global(.content) {
        display: flex;
        flex-direction: column;
        height: 100%;
        /* dvh: accounts for virtual keyboard on mobile */
        height: 100dvh;
        max-height: 100dvh;
        overflow: hidden;
    }

    /* ── Pulse bar ── */
    .pulse-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 16px;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        flex-wrap: wrap;
    }
    .pulse-symbol {
        font-weight: 700;
        font-size: 13px;
        color: var(--text);
        letter-spacing: 0.04em;
    }
    .pulse-chip {
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 20px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .pulse-chip.bull {
        background: rgba(40, 200, 100, 0.15);
        color: #28c864;
        border: 1px solid rgba(40, 200, 100, 0.3);
    }
    .pulse-chip.bear {
        background: rgba(220, 60, 60, 0.15);
        color: #e05555;
        border: 1px solid rgba(220, 60, 60, 0.3);
    }
    .pulse-chip.session {
        background: rgba(120, 120, 255, 0.15);
        color: #8888ff;
        border: 1px solid rgba(120, 120, 255, 0.3);
    }
    .pulse-chip.mode {
        background: var(--surface-alt, rgba(255, 255, 255, 0.06));
        color: var(--text-muted);
        border: 1px solid var(--border);
    }
    .pulse-levels {
        display: flex;
        gap: 6px;
        font-size: 11px;
        font-variant-numeric: tabular-nums;
    }
    .lvl {
        font-weight: 600;
    }
    .lvl.up {
        color: #28c864;
    }
    .lvl.dn {
        color: #e05555;
    }
    .pulse-model {
        margin-left: auto;
        font-size: 11px;
        color: var(--text-muted);
        font-family: monospace;
    }

    /* ── Messages area ── */
    .messages {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-height: 0;
    }
    .messages-spacer {
        flex: 1;
    }

    .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        gap: 12px;
        color: var(--text-muted);
        text-align: center;
    }
    .empty-icon {
        font-size: 40px;
    }
    .empty-state p {
        font-size: 14px;
    }
    .archi-thought {
        max-width: 360px;
        font-style: italic;
        font-size: 13px;
        color: var(--text-muted);
        border-left: 2px solid var(--accent, #7c6fff);
        padding-left: 10px;
        margin: 0;
    }

    /* ── Bubble rows ── */
    .bubble-row {
        display: flex;
        align-items: flex-end;
        gap: 8px;
        max-width: 80%;
    }
    .bubble-row.user {
        align-self: flex-end;
        flex-direction: row-reverse;
    }
    .bubble-row.archi {
        align-self: flex-start;
    }

    .avatar {
        width: 28px;
        height: 28px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        flex-shrink: 0;
        color: var(--accent, #7c6fff);
    }

    .bubble {
        padding: 10px 14px;
        border-radius: 16px;
        max-width: 100%;
        word-break: break-word;
    }
    .bubble-row.user .bubble {
        background: var(--accent, #7c6fff);
        color: #fff;
        border-bottom-right-radius: 4px;
    }
    .bubble-row.archi .bubble {
        background: var(--surface);
        border: 1px solid var(--border);
        color: var(--text);
        border-bottom-left-radius: 4px;
    }
    .bubble-text {
        font-size: 14px;
        line-height: 1.5;
        white-space: pre-wrap;
    }
    /* Typing indicator for pending archi response */
    .bubble-row.archi .bubble .bubble-text:only-child:empty::after,
    .bubble-row.archi .bubble-text:is([data-typing]) {
        content: "";
    }
    .typing-dot {
        display: inline-block;
        animation: typing-blink 1.2s ease infinite;
        opacity: 0.6;
    }
    @keyframes typing-blink {
        0%,
        80%,
        100% {
            opacity: 0.2;
        }
        40% {
            opacity: 1;
        }
    }
    .bubble-ts {
        font-size: 10px;
        margin-top: 4px;
        opacity: 0.55;
        text-align: right;
    }

    /* ── Input bar ── */
    .input-bar {
        padding: 8px 16px 12px;
        border-top: 1px solid var(--border);
        background: var(--surface);
        flex-shrink: 0;
        position: relative;
    }
    .input-error {
        font-size: 12px;
        color: #e05555;
        margin-bottom: 6px;
    }
    .voice-error {
        font-size: 11px;
        color: #e05555;
        margin-top: 4px;
    }
    .input-row {
        display: flex;
        align-items: flex-end;
        gap: 6px;
    }

    /* ── Quick actions ── */
    .quick-actions {
        display: flex;
        gap: 6px;
        padding: 6px 16px;
        background: var(--bg);
        border-top: 1px solid var(--border);
        flex-shrink: 0;
        overflow-x: auto;
        scrollbar-width: none;
    }
    .quick-actions::-webkit-scrollbar { display: none; }
    .qa-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: var(--surface);
        color: var(--text-muted);
        cursor: pointer;
        font-size: 12px;
        white-space: nowrap;
        flex-shrink: 0;
        transition: border-color 0.2s, color 0.2s;
    }
    .qa-btn:hover {
        border-color: var(--accent, #7c6fff);
        color: var(--text);
    }
    .qa-icon { font-size: 13px; }
    .qa-label { font-weight: 500; }

    /* ── Emoji picker ── */
    .emoji-picker {
        position: absolute;
        bottom: 100%;
        left: 8px;
        right: 8px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        box-shadow: 0 -4px 20px rgba(0,0,0,0.4);
        z-index: 50;
        max-height: 220px;
        overflow-y: auto;
    }
    .emoji-group-label {
        font-size: 10px;
        font-weight: 600;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .emoji-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(36px, 1fr));
        gap: 2px;
    }
    .emoji-btn {
        width: 36px;
        height: 36px;
        border: none;
        background: none;
        cursor: pointer;
        font-size: 20px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s;
    }
    .emoji-btn:hover {
        background: var(--surface2, rgba(255,255,255,0.06));
    }

    /* ── Shared icon buttons (emoji + voice) ── */
    .btn-icon {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        border: 1px solid var(--border);
        background: var(--bg);
        cursor: pointer;
        font-size: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition: background 0.2s, border-color 0.2s;
    }
    .btn-icon:disabled {
        opacity: 0.4;
        cursor: not-allowed;
    }
    .btn-icon.active {
        border-color: var(--accent, #7c6fff);
        background: rgba(124, 111, 255, 0.1);
    }
    .btn-icon.recording {
        border-color: #e05555;
        background: rgba(224, 85, 85, 0.1);
        animation: pulse-ring 1s ease infinite;
    }
    @keyframes pulse-ring {
        0%,
        100% {
            box-shadow: 0 0 0 0 rgba(224, 85, 85, 0.4);
        }
        50% {
            box-shadow: 0 0 0 6px rgba(224, 85, 85, 0);
        }
    }

    .chat-input {
        flex: 1;
        resize: none;
        border: 1px solid var(--border);
        border-radius: 20px;
        background: var(--bg);
        color: var(--text);
        font-family: inherit;
        font-size: 15px;
        line-height: 1.5;
        padding: 10px 16px;
        outline: none;
        transition: border-color 0.2s;
        min-height: 44px;
        max-height: 140px;
        overflow-y: auto;
    }
    .chat-input:focus {
        border-color: var(--accent, #7c6fff);
    }
    .chat-input:disabled {
        opacity: 0.6;
    }
    .chat-input::placeholder {
        color: var(--text-muted);
    }

    .btn-send {
        width: 44px;
        height: 44px;
        border-radius: 50%;
        border: none;
        background: var(--accent, #7c6fff);
        color: #fff;
        cursor: pointer;
        font-size: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition:
            opacity 0.2s,
            transform 0.1s;
    }
    .btn-send:disabled {
        opacity: 0.4;
        cursor: not-allowed;
    }
    .btn-send:not(:disabled):hover {
        opacity: 0.85;
        transform: scale(1.05);
    }
</style>
