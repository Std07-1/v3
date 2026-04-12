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

    // Input focus state (hides quick actions on mobile keyboard)
    let inputFocused = $state(false);

    // ── Context bar auto-hide ──
    let contextVisible = $state(true);
    let contextTimer: ReturnType<typeof setTimeout> | null = null;
    function resetContextTimer() {
        contextVisible = true;
        if (contextTimer) clearTimeout(contextTimer);
        contextTimer = setTimeout(() => {
            contextVisible = false;
        }, 5000);
    }

    // ── TTS (P2 — session-only, no scare on reload) ──
    let ttsAuto = $state(false);
    const ttsSupported =
        typeof window !== "undefined" && "speechSynthesis" in window;
    let lastSpokenId = "";

    function speak(text: string) {
        if (!ttsSupported) return;
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "uk-UA";
        u.rate = 1.05;
        speechSynthesis.speak(u);
    }

    function toggleAutoTTS() {
        ttsAuto = !ttsAuto;
        if (!ttsAuto) speechSynthesis.cancel();
    }

    // ── Emoji (P6) — Categorized Telegram-style ──
    let showEmoji = $state(false);
    const EMOJI_CATS: { icon: string; emojis: string[] }[] = [
        {
            icon: "😀",
            emojis: [
                "😊",
                "😂",
                "🤣",
                "😎",
                "🤔",
                "😏",
                "🙄",
                "😤",
                "😴",
                "🥳",
                "🫡",
                "😈",
            ],
        },
        {
            icon: "👋",
            emojis: [
                "👍",
                "👎",
                "👏",
                "🤝",
                "💪",
                "✋",
                "🤞",
                "👀",
                "🙏",
                "🫶",
                "✌️",
                "🤙",
            ],
        },
        {
            icon: "❤️",
            emojis: [
                "❤️",
                "🔥",
                "⚡",
                "💎",
                "🎯",
                "⭐",
                "💰",
                "🏆",
                "🚀",
                "💡",
                "🔮",
                "🎰",
            ],
        },
        {
            icon: "📈",
            emojis: [
                "📈",
                "📉",
                "💹",
                "🟢",
                "🔴",
                "⚠️",
                "🏦",
                "🪙",
                "📊",
                "💵",
                "🐂",
                "🐻",
            ],
        },
    ];
    let emojiCat = $state(0);

    function insertEmoji(e: string) {
        inputText += e;
        showEmoji = false;
        textareaEl?.focus();
    }

    // ── inner_thought filter (dedupe) ──
    const filteredMessages = $derived(
        messages.filter((m) => {
            if (
                m.role === "archi" &&
                directives?.inner_thought &&
                m.text === directives.inner_thought
            )
                return false;
            return true;
        }),
    );

    // ── auto-TTS on new archi message ──
    $effect(() => {
        if (!ttsAuto || filteredMessages.length === 0) return;
        const last = filteredMessages[filteredMessages.length - 1];
        if (last.role === "archi" && last.id !== lastSpokenId) {
            lastSpokenId = last.id;
            speak(last.text);
        }
    });

    // ── Smart Context Actions — adapt to Archi's current state ──
    const contextActions = $derived.by(() => {
        const d = directives;
        const acts: { label: string; text: string; icon: string }[] = [];

        const hasScenario = !!d?.active_scenario;
        const vp = (d as any)?.virtual_position;
        const hasOpenVP = vp && typeof vp === "object" && vp.status === "open";
        const isClosed = (d as any)?.last_market_status === "closed";
        const wl = d?.watch_levels;
        const hasLevels = Array.isArray(wl) && wl.length > 0;

        if (isClosed) {
            acts.push({
                icon: "📊",
                label: "Підсумки",
                text: "Підведи підсумки торгового дня — що вийшло, що ні?",
            });
            acts.push({
                icon: "🪞",
                label: "Self-review",
                text: "Зроби self-review: що було добре, де помилки, що покращити?",
            });
            acts.push({
                icon: "📋",
                label: "План",
                text: "Який план на наступну торгову сесію?",
            });
        } else {
            if (hasScenario) {
                acts.push({
                    icon: "🎯",
                    label: "Перевір тезис",
                    text: "Перевір валідність поточного сценарію — чи тримається тезис?",
                });
            } else {
                acts.push({
                    icon: "🎯",
                    label: "Сценарій",
                    text: "Побудуй торговий сценарій на поточну сесію",
                });
            }
            if (hasOpenVP) {
                acts.push({
                    icon: "💼",
                    label: "VP статус",
                    text: "Як моя віртуальна позиція? Тримаємо чи закриваємо?",
                });
            }
            acts.push({
                icon: "🧠",
                label: "Аналіз",
                text: "Проаналізуй поточний ринок та оновити bias",
            });
            if (!hasLevels) {
                acts.push({
                    icon: "👁",
                    label: "Рівні",
                    text: "Визнач ключові рівні та зони для спостереження",
                });
            }
        }
        acts.push({
            icon: "💭",
            label: "Думки?",
            text: "Що зараз на думці? Поділись",
        });
        return acts.slice(0, 5);
    });

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
            // Prevent auto-TTS of existing messages on load
            const _lastArchi = [...messages]
                .reverse()
                .find((m) => m.role === "archi");
            if (_lastArchi) lastSpokenId = _lastArchi.id;
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

    // ── send message (async: POST saves + bot replies via Redis) ──
    let awaitingReply = $state(false);
    let lastSentId = $state("");

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
        messages = [...messages, tmpMsg];
        await tick();
        scrollToBottom();

        try {
            const res = await api.chatSend(text);
            // Replace tmp user msg with real (server-assigned) one
            const realUser = res.message;
            messages = messages.map((m) => (m.id === tmpMsg.id ? realUser : m));
            // Bot will reply asynchronously — start fast polling
            lastSentId = realUser.id;
            awaitingReply = true;
            startFastPoll();
        } catch {
            error = "Повідомлення не відправлено. Спробуй ще раз.";
            messages = messages.filter((m) => m.id !== tmpMsg.id);
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

    // ── polling for new messages ──
    let pollId: ReturnType<typeof setInterval>;
    let fastPollId: ReturnType<typeof setInterval> | null = null;
    const NORMAL_POLL_MS = 8_000;
    const FAST_POLL_MS = 2_000;
    const FAST_POLL_MAX_MS = 90_000; // stop fast polling after 90s

    async function pollMessages() {
        try {
            const result = await api.chatHistory(80);
            const newMsgs = result.messages ?? [];
            // Check if we got a reply to our last sent message
            if (awaitingReply && lastSentId) {
                const hasReply = newMsgs.some(
                    (m) =>
                        m.role === "archi" &&
                        m.ts_ms >
                            (messages.find((x) => x.id === lastSentId)?.ts_ms ??
                                0),
                );
                if (hasReply) {
                    awaitingReply = false;
                    lastSentId = "";
                    stopFastPoll();
                }
            }
            if (
                newMsgs.length !== messages.length ||
                (newMsgs.length > 0 &&
                    newMsgs[newMsgs.length - 1]?.id !==
                        messages[messages.length - 1]?.id)
            ) {
                messages = newMsgs;
                await tick();
                scrollToBottom();
            }
        } catch {
            /* silent */
        }
    }

    function startFastPoll() {
        stopFastPoll();
        fastPollId = setInterval(pollMessages, FAST_POLL_MS);
        // Safety: stop fast poll after timeout
        setTimeout(() => {
            if (awaitingReply) {
                awaitingReply = false;
                stopFastPoll();
            }
        }, FAST_POLL_MAX_MS);
    }

    function stopFastPoll() {
        if (fastPollId) {
            clearInterval(fastPollId);
            fastPollId = null;
        }
    }

    // ── lifecycle ──
    onMount(() => {
        loadData();
        initVoice();
        pollId = setInterval(pollMessages, NORMAL_POLL_MS);
        resetContextTimer();
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
        stopFastPoll();
        if (recognition && listening) recognition.stop();
    });

    // prefill reactive: when prop changes, update input
    $effect(() => {
        if (prefill) inputText = prefill;
    });

    const bias = $derived(getBias(directives));
    const levels = $derived(getWatchLevels(directives));
</script>

<!-- ── Chat Header ── -->
<div class="chat-header">
    <div class="ch-left">
        {#if directives?.mood}
            <span class="ch-mood-orb" data-mood={directives.mood}></span>
        {/if}
        <h2 class="ch-title">Чат</h2>
        {#if directives?.focus_symbol}
            <span class="ch-symbol">{directives.focus_symbol}</span>
        {/if}
        {#if bias.label}
            <span class="ch-bias {bias.color}">{bias.label}</span>
        {/if}
    </div>
    <div class="ch-right">
        {#if ttsSupported}
            <button
                class="tts-pill"
                class:active={ttsAuto}
                onclick={toggleAutoTTS}
                title={ttsAuto
                    ? "Вимкнути авто-озвучення"
                    : "Увімкнути авто-озвучення"}
            >
                {ttsAuto ? "🔊 Авто" : "🔇"}
            </button>
        {/if}
    </div>
</div>

{#if directives?.inner_thought}
    <div class="pinned-thought" class:faded={!contextVisible}>
        <span class="pt-icon">💭</span>
        <span class="pt-text">{directives.inner_thought}</span>
    </div>
{/if}

<!-- ── Messages Area ── -->
<div class="messages" bind:this={messagesEl}>
    {#if loading}
        <div class="empty-state">
            <div class="empty-icon">💬</div>
            <p>Завантаження…</p>
        </div>
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
        <div class="messages-spacer"></div>
        {#each filteredMessages as msg, idx (msg.id)}
            {@const prevMsg = idx > 0 ? filteredMessages[idx - 1] : null}
            {@const sameAuthor = prevMsg?.role === msg.role}
            {@const timeDiff = prevMsg ? msg.ts_ms - prevMsg.ts_ms : Infinity}
            {@const grouped = sameAuthor && timeDiff < 120_000}
            <div
                class="bubble-row"
                class:user={msg.role === "user"}
                class:archi={msg.role === "archi"}
                class:grouped
            >
                <div class="bubble">
                    <div class="bubble-text">{msg.text}</div>
                    <div class="bubble-meta">
                        <span class="bubble-ts">{formatTs(msg.ts_ms)}</span>
                        {#if (msg as any).source === "telegram"}
                            <span class="src-tg">TG</span>
                        {/if}
                        {#if msg.role === "archi" && ttsSupported}
                            <button
                                class="btn-tts"
                                onclick={() => speak(msg.text)}
                                title="Озвучити">🔊</button
                            >
                        {/if}
                    </div>
                </div>
            </div>
        {/each}
        {#if awaitingReply}
            <div class="bubble-row archi">
                <div class="bubble">
                    <span class="typing-dot">●</span><span
                        class="typing-dot"
                        style="animation-delay:0.15s">●</span
                    ><span class="typing-dot" style="animation-delay:0.3s"
                        >●</span
                    >
                </div>
            </div>
        {/if}
    {/if}
</div>

<!-- ── Context Actions (state-aware, auto-send) ── -->
{#if !inputFocused && contextActions.length > 0}
    <div class="quick-actions">
        {#each contextActions as act}
            <button
                class="qa-btn"
                onclick={() => {
                    inputText = act.text;
                    tick().then(() => sendMessage());
                }}
                title={act.text}
            >
                <span class="qa-icon">{act.icon}</span>
                <span class="qa-label">{act.label}</span>
            </button>
        {/each}
    </div>
{/if}

<!-- ── Input Bar ── -->
<div class="input-bar">
    {#if error}<div class="input-error">{error}</div>{/if}
    <div class="input-row">
        <textarea
            class="chat-input"
            bind:this={textareaEl}
            bind:value={inputText}
            oninput={autoGrow}
            onkeydown={handleKeydown}
            onfocus={() => {
                inputFocused = true;
            }}
            onblur={() => {
                setTimeout(() => {
                    inputFocused = false;
                }, 150);
            }}
            placeholder="Повідомлення…"
            rows={1}
            disabled={sending}
        ></textarea>

        <div class="input-actions">
            <!-- Voice -->
            {#if voiceSupported}
                <button
                    class="ia-btn"
                    class:recording={listening}
                    onclick={toggleVoice}
                    title={listening ? "Зупинити" : "Голос"}
                >
                    {listening ? "🔴" : "🎤"}
                </button>
            {/if}

            <!-- Emoji -->
            <div class="emoji-anchor">
                <button
                    class="ia-btn"
                    onclick={() => {
                        showEmoji = !showEmoji;
                    }}
                    title="Емодзі">😊</button
                >
                {#if showEmoji}
                    <div class="emoji-panel">
                        <div class="emoji-tabs">
                            {#each EMOJI_CATS as cat, ci}
                                <button
                                    class="emoji-tab"
                                    class:active={emojiCat === ci}
                                    onclick={() => {
                                        emojiCat = ci;
                                    }}>{cat.icon}</button
                                >
                            {/each}
                        </div>
                        <div class="emoji-grid">
                            {#each EMOJI_CATS[emojiCat].emojis as em}
                                <button
                                    class="emoji-cell"
                                    onclick={() => insertEmoji(em)}>{em}</button
                                >
                            {/each}
                        </div>
                    </div>
                {/if}
            </div>

            <!-- Send -->
            <button
                class="btn-send"
                onclick={sendMessage}
                disabled={sending || !inputText.trim()}
            >
                {sending ? "⏳" : "➤"}
            </button>
        </div>
    </div>
    {#if voiceError}<div class="voice-error">{voiceError}</div>{/if}
</div>

<style>
    /* ═══ Chat Premium Layout ═══ */

    /* ── Chat Header ── */
    .chat-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 16px 12px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        background: var(--surface);
    }
    .ch-left {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
    }
    .ch-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--text);
        margin: 0;
    }
    .ch-mood-orb {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--accent);
        animation: ch-pulse 2.5s ease-in-out infinite;
        flex-shrink: 0;
    }
    .ch-mood-orb[data-mood="calm"] {
        background: #60a5fa;
        animation-duration: 3s;
    }
    .ch-mood-orb[data-mood="focused"] {
        background: #34d399;
    }
    .ch-mood-orb[data-mood="alert"] {
        background: #fbbf24;
        animation-duration: 1.5s;
    }
    .ch-mood-orb[data-mood="stressed"] {
        background: #f87171;
        animation-duration: 1s;
    }
    .ch-mood-orb[data-mood="cautious"] {
        background: #fb923c;
    }
    .ch-mood-orb[data-mood="excited"] {
        background: #c084fc;
        animation-duration: 1.2s;
    }
    @keyframes ch-pulse {
        0%,
        100% {
            opacity: 1;
            transform: scale(1);
        }
        50% {
            opacity: 0.5;
            transform: scale(0.7);
        }
    }
    .ch-symbol {
        font-size: 11px;
        font-weight: 700;
        color: var(--text-muted);
        letter-spacing: 0.04em;
        padding: 2px 7px;
        background: var(--surface2);
        border-radius: 6px;
    }
    .ch-bias {
        font-size: 10px;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 10px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    .ch-bias.bull {
        background: rgba(40, 200, 100, 0.12);
        color: #28c864;
    }
    .ch-bias.bear {
        background: rgba(220, 60, 60, 0.12);
        color: #e05555;
    }
    .ch-right {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 0;
    }

    /* ── TTS Pill (prominent, easy to find) ── */
    .tts-pill {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 12px;
        border-radius: 16px;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text-muted);
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s ease;
        white-space: nowrap;
    }
    .tts-pill:hover {
        border-color: var(--accent);
        color: var(--text);
    }
    .tts-pill.active {
        background: var(--accent);
        color: #fff;
        border-color: var(--accent);
        box-shadow: 0 0 12px rgba(124, 111, 255, 0.3);
    }

    /* ── Pinned Thought ── */
    .pinned-thought {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 8px 16px;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        transition:
            opacity 0.6s ease,
            max-height 0.6s ease;
        max-height: 60px;
        overflow: hidden;
    }
    .pinned-thought.faded {
        opacity: 0.3;
        max-height: 24px;
    }
    .pt-icon {
        font-size: 14px;
        flex-shrink: 0;
        line-height: 1.5;
    }
    .pt-text {
        font-size: 12px;
        color: var(--text-muted);
        font-style: italic;
        line-height: 1.5;
        overflow: hidden;
        text-overflow: ellipsis;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
    }

    /* ── Messages area ── */
    .messages {
        flex: 1;
        overflow-y: auto;
        padding: 12px 16px 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
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
        gap: 10px;
        color: var(--text-muted);
        text-align: center;
    }
    .empty-icon {
        font-size: 36px;
    }
    .empty-state p {
        font-size: 14px;
        margin: 0;
    }
    .archi-thought {
        max-width: 320px;
        font-style: italic;
        font-size: 13px;
        color: var(--text-muted);
        border-left: 2px solid var(--accent);
        padding-left: 10px;
        margin: 0;
    }

    /* ── Bubble rows ── */
    .bubble-row {
        display: flex;
        max-width: 82%;
        position: relative;
    }
    .bubble-row.user {
        align-self: flex-end;
    }
    .bubble-row.archi {
        align-self: flex-start;
    }
    .bubble-row.grouped {
        margin-top: -2px;
    }

    .bubble {
        padding: 8px 14px;
        border-radius: 18px;
        max-width: 100%;
        word-break: break-word;
        position: relative;
    }
    /* User bubbles */
    .bubble-row.user .bubble {
        background: color-mix(in srgb, var(--accent) 22%, var(--surface));
        color: var(--text);
        border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
        border-bottom-right-radius: 6px;
    }
    .bubble-row.user.grouped .bubble {
        border-top-right-radius: 6px;
    }

    /* Archi bubbles */
    .bubble-row.archi .bubble {
        background: var(--surface);
        color: var(--text);
        border-bottom-left-radius: 6px;
    }
    .bubble-row.archi.grouped .bubble {
        border-top-left-radius: 6px;
    }

    .bubble-text {
        font-size: 14.5px;
        line-height: 1.5;
        white-space: pre-wrap;
    }
    .bubble-meta {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 5px;
        margin-top: 2px;
    }
    .bubble-ts {
        font-size: 10px;
        opacity: 0.45;
    }
    .bubble-row.user .bubble-ts {
        opacity: 0.45;
    }
    .src-tg {
        font-size: 8px;
        font-weight: 700;
        padding: 1px 4px;
        border-radius: 3px;
        background: rgba(0, 136, 204, 0.15);
        color: #0088cc;
    }

    /* TTS */
    .btn-tts {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 11px;
        padding: 0;
        opacity: 0;
        transition: opacity 0.15s;
    }
    .bubble:hover .btn-tts {
        opacity: 0.5;
    }
    .btn-tts:hover {
        opacity: 1 !important;
    }

    /* Typing indicator */
    .typing-dot {
        display: inline-block;
        animation: tp 1.4s infinite ease-in-out;
        font-size: 14px;
        opacity: 0.6;
    }
    @keyframes tp {
        0%,
        80%,
        100% {
            opacity: 0.15;
        }
        40% {
            opacity: 1;
        }
    }

    /* ── Quick actions ── */
    .quick-actions {
        display: flex;
        gap: 6px;
        padding: 6px 16px;
        background: var(--bg);
        flex-shrink: 0;
        overflow-x: auto;
        scrollbar-width: none;
    }
    .quick-actions::-webkit-scrollbar {
        display: none;
    }
    .qa-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 12px;
        border: 1px solid var(--border);
        border-radius: 20px;
        background: var(--surface);
        color: var(--text-muted);
        cursor: pointer;
        font-size: 12px;
        white-space: nowrap;
        flex-shrink: 0;
        transition:
            border-color 0.15s,
            color 0.15s,
            background 0.15s;
    }
    .qa-btn:hover {
        border-color: var(--accent);
        color: var(--text);
        background: var(--surface2);
    }
    .qa-icon {
        font-size: 13px;
    }
    .qa-label {
        font-weight: 500;
    }

    /* ── Input bar ── */
    .input-bar {
        padding: 8px 12px 12px;
        background: var(--surface);
        border-top: 1px solid var(--border);
        flex-shrink: 0;
    }
    .input-error {
        font-size: 12px;
        color: #e05555;
        margin-bottom: 4px;
        padding: 0 4px;
    }
    .voice-error {
        font-size: 11px;
        color: #e05555;
        margin-top: 4px;
        padding: 0 4px;
    }
    .input-row {
        display: flex;
        align-items: flex-end;
        gap: 8px;
    }

    .chat-input {
        flex: 1;
        resize: none;
        border: 1px solid var(--border);
        border-radius: 22px;
        background: var(--bg);
        color: var(--text);
        font-family: inherit;
        font-size: 15px;
        line-height: 1.45;
        padding: 10px 16px;
        outline: none;
        transition: border-color 0.2s;
        min-height: 44px;
        max-height: 140px;
        overflow-y: auto;
    }
    .chat-input:focus {
        border-color: var(--accent);
    }
    .chat-input:disabled {
        opacity: 0.6;
    }
    .chat-input::placeholder {
        color: var(--text-muted);
    }

    .input-actions {
        display: flex;
        align-items: flex-end;
        gap: 4px;
        flex-shrink: 0;
    }
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
        transition:
            background 0.15s,
            color 0.15s;
    }
    .ia-btn:hover {
        background: var(--surface2);
        color: var(--text);
    }
    .ia-btn.tts-on {
        color: var(--accent);
    }
    .ia-btn.recording {
        color: #e05555;
        animation: rec-pulse 1s ease infinite;
    }
    @keyframes rec-pulse {
        0%,
        100% {
            box-shadow: 0 0 0 0 rgba(224, 85, 85, 0.35);
        }
        50% {
            box-shadow: 0 0 0 6px rgba(224, 85, 85, 0);
        }
    }

    .btn-send {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        border: none;
        background: var(--accent);
        color: #fff;
        cursor: pointer;
        font-size: 17px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition:
            opacity 0.15s,
            transform 0.1s;
    }
    .btn-send:disabled {
        opacity: 0.35;
        cursor: not-allowed;
    }
    .btn-send:not(:disabled):hover {
        opacity: 0.85;
        transform: scale(1.06);
    }

    /* ── Emoji panel (Telegram-style) ── */
    .emoji-anchor {
        position: relative;
    }
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
        from {
            opacity: 0;
            transform: translateY(8px) scale(0.95);
        }
        to {
            opacity: 1;
            transform: none;
        }
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
    .emoji-tab.active {
        border-bottom-color: var(--accent);
    }
    .emoji-tab:hover {
        background: var(--surface2);
    }
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
        transition:
            background 0.1s,
            transform 0.1s;
    }
    .emoji-cell:hover {
        background: var(--surface2);
        transform: scale(1.15);
    }

    @media (max-width: 768px) {
        .bubble-row {
            max-width: 88%;
        }
        .emoji-panel {
            width: 260px;
            right: -12px;
        }
        .pinned-thought {
            display: none;
        }

        /* ── Mobile input: textarea grows big, actions wrap below ── */
        .input-bar {
            padding: 8px 10px env(safe-area-inset-bottom) 10px;
        }
        .input-row {
            flex-wrap: wrap;
            gap: 6px;
        }
        .chat-input {
            flex-basis: 100%;  /* full width, wraps alone on first line */
            max-height: 200px;
            min-height: 46px;
            font-size: 16px; /* prevents iOS zoom on focus */
            border-radius: 14px;
            padding: 12px 14px;
        }
        .input-actions {
            width: 100%;
            justify-content: flex-end;
            gap: 8px;
        }
        .btn-send {
            width: 44px;
            height: 44px;
        }
        .ia-btn {
            width: 42px;
            height: 42px;
        }
    }
</style>
