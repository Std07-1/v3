<!--
    Chat.svelte — новий shell чату (ADR-0053 S1).

    Філософія (коротко):
      - Один фокус: розмова. Жодних Mode Hearth / Pulse Board / Pinned
        Thought на сторінці чату. Ці поверхні переїхали у Mind (S2).
      - Топбар 44 px: ім'я + focus pill (symbol · bias · session · live-dot).
      - Повідомлення групуються: сусідні від одного автора у 3-хв вікні =
        один bubble з одним timestamp знизу.
      - Inline action chips під archi-бабблом (`msg.chips[]`) — клік
        вставляє текст у InputBar для редагування. Slash-команди заборонені.
      - Hover-reactions (👍/📌/⭐) — localStorage; S4 додасть Redis XADD.
      - Thinking indicator = три pulsing dots + "Арчі думає"; над input,
        не у стрічці повідомлень.
      - Streaming reply (S3): коли `msg.streaming=true`, bubble рендериться
        з blinking caret в кінці тексту.

    Props (від App.svelte):
      - draft, handoff, ondraftchange, ondismisshandoff — без змін з ADR-0052.

    Зняте з попередньої версії (ADR-0053 S1):
      - ModeHearth, PulseBoard, PinnedThought, ChatHeader, HandoffStrip
        (HandoffStrip повертається як thin banner над input зверху — drop-in).
      - hearthHelpers builders (buildModeHearth, buildPulseCards, buildPulseFreshness).
      - railStore (thinking signal + contextVisible більше не потрібні — думки
        живуть у Thinking/Mind view).
-->
<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import {
        getDirectives,
        getAgentState,
        getLastDirectivesSyncMs,
        getLastAgentStateSyncMs,
    } from "../lib/state.svelte";
    import type { ChatHandoff, ChatMessage } from "../lib/types";
    import { chatStore } from "../features/chat/stores/chatStore.svelte";
    import { ttsStore } from "../features/chat/stores/ttsStore.svelte";
    import ChatTopbar from "../features/chat/components/ChatTopbar.svelte";
    import MessageBubble from "../features/chat/components/MessageBubble.svelte";
    import ActionChips from "../features/chat/components/ActionChips.svelte";
    import ReactionBar from "../features/chat/components/ReactionBar.svelte";
    import HandoffStrip from "../features/chat/components/HandoffStrip.svelte";
    import InputBar from "../features/chat/components/InputBar.svelte";
    import { getBias, formatTs } from "../features/chat/lib/hearthHelpers";

    // ── props ──
    let {
        draft = "",
        handoff = null,
        ondraftchange = (_text: string): void => {},
        ondismisshandoff = (_handoffId: string): void => {},
    } = $props<{
        draft?: string;
        handoff?: ChatHandoff | null;
        ondraftchange?: (text: string) => void;
        ondismisshandoff?: (handoffId: string) => void;
    }>();

    // ── state from chatStore ──
    let messages = $derived(chatStore.messages);
    let sending = $derived(chatStore.sending);
    let loading = $derived(chatStore.loading);
    let error = $derived(chatStore.error);
    let awaitingReply = $derived(chatStore.awaitingReply);
    let inputText = $state("");

    // ── Shell snapshot (only for topbar pill + live-dot) ──
    let directives = $derived(getDirectives());
    let agentState = $derived(getAgentState());
    let lastDirSync = $derived(getLastDirectivesSyncMs());
    let lastStateSync = $derived(getLastAgentStateSyncMs());

    const bias = $derived(getBias(directives));
    const session = $derived(
        typeof agentState?.market_session === "string"
            ? agentState.market_session
            : typeof directives?.last_market_status === "string"
              ? directives.last_market_status
              : "",
    );
    const focusSymbol = $derived(
        typeof directives?.focus_symbol === "string"
            ? directives.focus_symbol
            : "",
    );
    // live-dot: зелений коли будь-який з двох каналів синкався за 30 s.
    let nowMs = $state(Date.now());
    const FRESHNESS_WINDOW = 30_000;
    const liveFresh = $derived(
        (lastDirSync > 0 && nowMs - lastDirSync < FRESHNESS_WINDOW) ||
            (lastStateSync > 0 && nowMs - lastStateSync < FRESHNESS_WINDOW),
    );

    // ── InputBar ref (для primeDraft / setDraft делегації) ──
    let inputBarRef = $state<{
        primeDraft: (text: string) => void;
        setDraft: (text: string, hint: string) => void;
        focus: () => void;
        applyHeight: () => void;
        resetHeight: () => void;
    } | null>(null);

    // Filter для auto-TTS: не озвучуємо дублікат inner_thought.
    const ttsFeed = $derived(
        messages.filter(
            (m: ChatMessage) =>
                !(
                    m.role === "archi" &&
                    typeof directives?.inner_thought === "string" &&
                    directives.inner_thought &&
                    m.text === directives.inner_thought
                ),
        ),
    );
    $effect(() => {
        ttsStore.maybeAutoSpeak(ttsFeed);
    });

    // ── Scroll container ──
    let scrollEl: HTMLDivElement | null = null;

    function isNearBottom(): boolean {
        if (!scrollEl) return true;
        const remaining =
            scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
        return remaining < 72;
    }
    function scrollToBottom(): void {
        if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
    }
    function isSelectingMessageText(): boolean {
        const sel = window.getSelection?.();
        if (!sel || sel.isCollapsed) return false;
        const a = sel.anchorNode;
        const f = sel.focusNode;
        return !!(a && f && scrollEl?.contains(a) && scrollEl.contains(f));
    }
    function maybeScrollToBottom(force = false): void {
        if (force || (isNearBottom() && !isSelectingMessageText())) {
            scrollToBottom();
        }
    }

    // ── Grouping: same author + 3-хв вікно = grouped=true (no top margin, timestamp hidden) ──
    const GROUP_WINDOW_MS = 180_000;
    type DisplayMsg = ChatMessage & {
        grouped: boolean;
        lastInGroup: boolean;
    };
    const displayMessages = $derived.by<DisplayMsg[]>(() => {
        // Хайдим archi-bubbles, що дублюють inner_thought (узгоджено з попередньою
        // поведінкою MessageList — думки живуть у Thinking view, а не у чаті).
        const innerThought =
            typeof directives?.inner_thought === "string"
                ? directives.inner_thought
                : "";
        const filtered = messages.filter(
            (m: ChatMessage) =>
                !(
                    m.role === "archi" &&
                    innerThought &&
                    m.text === innerThought
                ),
        );
        return filtered.map((msg, idx, arr) => {
            const prev = idx > 0 ? arr[idx - 1] : null;
            const next = idx < arr.length - 1 ? arr[idx + 1] : null;
            const sameAuthorPrev = prev?.role === msg.role;
            const dtPrev = prev ? msg.ts_ms - prev.ts_ms : Infinity;
            const grouped = sameAuthorPrev && dtPrev < GROUP_WINDOW_MS;
            const sameAuthorNext = next?.role === msg.role;
            const dtNext = next ? next.ts_ms - msg.ts_ms : Infinity;
            const lastInGroup = !(sameAuthorNext && dtNext < GROUP_WINDOW_MS);
            return { ...msg, grouped, lastInGroup };
        });
    });

    // Streaming bubble в потоці → не показуємо "Арчі думає" дублем.
    const hasStreamingBubble = $derived(
        displayMessages.some((m) => m.streaming === true),
    );

    // ── send ──
    async function sendMessage(): Promise<void> {
        const text = inputText.trim();
        if (!text || sending) return;
        inputText = "";
        ondraftchange("");
        inputBarRef?.resetHeight();

        await tick();
        scrollToBottom();

        const result = await chatStore.send(text);
        if (!result.ok) {
            inputText = result.text;
            ondraftchange(result.text);
        }
        await tick();
        maybeScrollToBottom(true);
    }

    function primeDraft(text: string): void {
        inputBarRef?.primeDraft(text);
    }

    function restoreHandoffDraft(context: ChatHandoff): void {
        inputBarRef?.setDraft(
            context.prompt,
            `Чернетку відновлено з ${handoffSourceLabel(context.source)}.`,
        );
    }

    function handoffSourceLabel(source: ChatHandoff["source"]): string {
        switch (source) {
            case "feed":
                return "Feed context";
            case "thinking":
                return "Thinking context";
            case "relationship":
                return "Relationship context";
            case "mind":
                return "Mind context";
            case "logs":
                return "Logs context";
            default:
                return "Context";
        }
    }

    const activeHandoff = $derived(handoff);
    const handoffDraftActive = $derived.by(() => {
        const current = activeHandoff;
        if (!current) return false;
        return inputText.trim() === current.prompt.trim();
    });

    // ── auto-scroll effects ──
    // Initial jump: історія з Redis приходить ДО того як scrollEl ініціалізовано.
    // Перший рендер = scrollTop=0, isNearBottom() буде false → без цього effect
    // ми застрягаємо на перших повідомленнях. Фіксуємо однократно на момент
    // переходу loading:true→false.
    let _initialScrollDone = false;
    $effect(() => {
        if (_initialScrollDone) return;
        if (chatStore.loading) return;
        if (chatStore.messages.length === 0) return;
        _initialScrollDone = true;
        void tick().then(() => {
            // instant (без smooth) — менше мерехтіння при відкритті вкладки
            if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
        });
    });

    let _lastVersion = 0;
    $effect(() => {
        const v = chatStore.messagesVersion;
        if (v === _lastVersion) return;
        const shouldStick = chatStore.awaitingReply || isNearBottom();
        _lastVersion = v;
        void tick().then(() => {
            if (!isSelectingMessageText() && shouldStick) scrollToBottom();
        });
    });

    // Seed TTS (щоб історія не озвучувалась при mount).
    let _ttsInitialized = false;
    $effect(() => {
        if (_ttsInitialized || chatStore.loading) return;
        const lastArchi = [...chatStore.messages]
            .reverse()
            .find((m) => m.role === "archi");
        if (lastArchi) ttsStore.seed(lastArchi.id);
        _ttsInitialized = true;
    });

    // Draft prop sync — тільки ЗОВНІШНІ зміни draft пушимо в inputText.
    // Якщо тракати inputText теж — race: user types "x" → inputText="x",
    // draft ще "" (callback async) → effect бачить mismatch → wipe inputText.
    // Тому зберігаємо останній побачений draft у plain let (не state),
    // і effect тригериться виключно коли draft реально змінився ззовні.
    let _lastDraft = "";
    $effect(() => {
        if (draft === _lastDraft) return;
        _lastDraft = draft;
        inputText = draft;
    });

    // ── lifecycle ──
    let tickHandle: ReturnType<typeof setInterval> | null = null;
    onMount(() => {
        void chatStore.init();
        // Oновлення nowMs кожну секунду — для liveFresh індикатора.
        tickHandle = setInterval(() => {
            nowMs = Date.now();
        }, 1000);
    });
    onDestroy(() => {
        chatStore.shutdown();
        if (tickHandle) clearInterval(tickHandle);
    });
</script>

<ChatTopbar
    {focusSymbol}
    biasLabel={bias.label}
    biasColor={bias.color as "bull" | "bear" | ""}
    {session}
    {liveFresh}
    ttsSupported={ttsStore.supported}
    ttsAuto={ttsStore.auto}
    ontoggletts={() => ttsStore.toggleAuto()}
/>

{#if activeHandoff}
    <HandoffStrip
        handoff={activeHandoff}
        draftActive={handoffDraftActive}
        sourceLabel={handoffSourceLabel(activeHandoff.source)}
        formattedTs={activeHandoff.ts_ms ? formatTs(activeHandoff.ts_ms) : ""}
        onrestore={() => restoreHandoffDraft(activeHandoff)}
        ondismiss={() => ondismisshandoff(activeHandoff.id)}
    />
{/if}

<div class="scroll" bind:this={scrollEl}>
    {#if loading}
        <div class="empty">
            <div class="empty-icon">💬</div>
            <p>Завантаження…</p>
        </div>
    {:else if displayMessages.length === 0}
        <div class="empty">
            <div class="empty-icon">💬</div>
            <p>Напиши Арчі — він прочитає і відповість</p>
        </div>
    {:else}
        <div class="spacer"></div>
        {#each displayMessages as msg (msg.id)}
            <div
                class="msg-block"
                class:grouped={msg.grouped}
                class:streaming={msg.streaming === true}
            >
                <MessageBubble
                    message={msg}
                    grouped={msg.grouped}
                    ttsSupported={ttsStore.supported}
                    onspeak={(t) => ttsStore.speak(t)}
                />
                {#if msg.role === "archi" && msg.chips && msg.chips.length > 0}
                    <ActionChips
                        chips={msg.chips}
                        onchipclick={primeDraft}
                    />
                {/if}
                {#if msg.lastInGroup}
                    <ReactionBar
                        msgId={msg.id}
                        side={msg.role === "user" ? "right" : "left"}
                    />
                {/if}
            </div>
        {/each}
        {#if awaitingReply && !hasStreamingBubble}
            <div class="thinking" role="status" aria-live="polite">
                <span class="td"></span>
                <span class="td"></span>
                <span class="td"></span>
                <span class="thinking-label">Арчі думає</span>
            </div>
        {/if}
    {/if}
</div>

<InputBar
    bind:this={inputBarRef}
    bind:value={inputText}
    {sending}
    {error}
    onsend={sendMessage}
    oninputchange={(t) => ondraftchange(t)}
/>

<style>
    /* ═══ Chat shell (ADR-0053 S1) ═══
     * Structure: Topbar (sticky) → [HandoffStrip] → .scroll (flex:1) → InputBar.
     * Parent App.svelte layout already провайдить flex-direction: column.
     */

    .scroll {
        flex: 1;
        overflow-y: auto;
        padding: 12px 16px 10px;
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-height: 0;
        overscroll-behavior-y: contain;
    }
    .spacer { flex: 1; }

    .msg-block {
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .msg-block:not(.grouped) {
        margin-top: 8px;
    }
    .msg-block:first-of-type {
        margin-top: 0;
    }

    /* Streaming bubble (ADR-0053 S3): blinking cursor на bubble-text.
     * MessageBubble не знає про streaming, тож цілимо через global descendant. */
    :global(.msg-block.streaming .bubble::after) {
        content: "▍";
        display: inline-block;
        margin-left: 2px;
        color: var(--accent);
        animation: streamBlink 1s steps(2) infinite;
        vertical-align: baseline;
        font-weight: 500;
    }
    @keyframes streamBlink {
        0%, 49% { opacity: 1; }
        50%, 100% { opacity: 0; }
    }

    .empty {
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
    .empty p { font-size: 14px; margin: 0; }

    /* Thinking indicator (pulsing dots + label) */
    .thinking {
        display: flex;
        align-items: center;
        gap: 6px;
        align-self: flex-start;
        padding: 6px 12px;
        border-radius: 14px;
        background: var(--surface);
        color: var(--text-muted);
        font-size: 12px;
        margin-top: 4px;
    }
    .td {
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: currentColor;
        opacity: 0.35;
        animation: td-pulse 1.2s ease-in-out infinite;
    }
    .td:nth-child(2) { animation-delay: 0.15s; }
    .td:nth-child(3) { animation-delay: 0.3s; }
    @keyframes td-pulse {
        0%, 100% { opacity: 0.2; transform: scale(0.8); }
        50% { opacity: 1; transform: scale(1.15); }
    }
    .thinking-label { margin-left: 2px; }

    @media (max-width: 768px) {
        .scroll { padding: 10px 12px 8px; }
    }
</style>
