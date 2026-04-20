<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import {
        getDirectives,
        getAgentState,
        getLastDirectivesSyncMs,
        getLastAgentStateSyncMs,
        getDirectivesError,
        getAgentStateError,
    } from "../lib/state.svelte";
    import type { ChatHandoff } from "../lib/types";
    import { chatStore } from "../features/chat/stores/chatStore.svelte";
    import { ttsStore } from "../features/chat/stores/ttsStore.svelte";
    import { railStore } from "../features/chat/stores/railStore.svelte";
    import MessageList from "../features/chat/components/MessageList.svelte";
    import InputBar from "../features/chat/components/InputBar.svelte";
    import ChatHeader from "../features/chat/components/ChatHeader.svelte";
    import HandoffStrip from "../features/chat/components/HandoffStrip.svelte";
    import ModeHearth from "../features/chat/components/ModeHearth.svelte";
    import PulseBoard from "../features/chat/components/PulseBoard.svelte";
    import PinnedThought from "../features/chat/components/PinnedThought.svelte";
    import type { QuickAction } from "../features/chat/components/QuickActions.svelte";
    import {
        buildModeHearth,
        buildPulseCards,
        buildPulseFreshness,
        formatTs,
        getBias,
    } from "../features/chat/lib/hearthHelpers";

    // ── props: prefill from Thinking quick reply ──
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

    // ── state ──
    // messages/sending/loading/error/awaitingReply живуть у chatStore (ADR-0052 S2).
    let messages = $derived(chatStore.messages);
    let sending = $derived(chatStore.sending);
    let loading = $derived(chatStore.loading);
    let error = $derived(chatStore.error);
    let awaitingReply = $derived(chatStore.awaitingReply);
    let inputText = $state("");

    // Market pulse (shared state — single polling source in state.svelte.ts)
    let directives = $derived(getDirectives());
    let agentState = $derived(getAgentState());
    let lastDirectivesSyncMs = $derived(getLastDirectivesSyncMs());
    let lastAgentStateSyncMs = $derived(getLastAgentStateSyncMs());
    let directivesSyncError = $derived(getDirectivesError());
    let agentStateSyncError = $derived(getAgentStateError());

    // Rail state (thinking signal + pinned-thought visibility — ADR-0052 S5).
    let latestThinking = $derived(railStore.latestThinking);
    let lastThinkingSyncMs = $derived(railStore.lastThinkingSyncMs);
    let thinkingSyncError = $derived(railStore.thinkingSyncError);
    let contextVisible = $derived(railStore.contextVisible);

    // Input concerns (composer height, focus, voice, emoji, quick-action hint) живуть
    // у InputBar (ADR-0052 S4). Тут лише ref для setDraft/primeDraft delegation.
    let inputBarRef = $state<{
        primeDraft: (text: string) => void;
        setDraft: (text: string, hint: string) => void;
        focus: () => void;
        applyHeight: () => void;
        resetHeight: () => void;
    } | null>(null);

    // ── TTS (ADR-0052 S4): state + speak/toggle у ttsStore. ──
    // Auto-TTS watcher на оновлення messages нижче (через ttsStore.maybeAutoSpeak).

    // ── inner_thought filter (dedupe) — для auto-TTS; MessageList робить свою
    //    ідентичну dedup, але тут потрібен масив для ttsStore.maybeAutoSpeak. ──
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

    // ── auto-TTS: ttsStore сам перевіряє auto + lastSpokenId ──
    $effect(() => {
        ttsStore.maybeAutoSpeak(filteredMessages);
    });

    // ── Smart Context Actions — adapt to Archi's current state ──
    const contextActions = $derived.by<QuickAction[]>(() => {
        const d = directives;
        const acts: QuickAction[] = [];

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
    let messageListRef = $state<{
        scrollToBottom: () => void;
        isNearBottom: () => boolean;
        isSelectingMessageText: () => boolean;
    } | null>(null);
    // Composer height / textarea DOM — повністю у InputBar (ADR-0052 S4).
    // Parent-level primeDraft/setDraft делегують у inputBarRef.
    function primeDraft(text: string): void {
        inputBarRef?.primeDraft(text);
    }

    function restoreHandoffDraft(context: ChatHandoff): void {
        inputBarRef?.setDraft(
            context.prompt,
            `Чернетку відновлено з ${handoffSourceLabel(context.source)}.`,
        );
    }

    // ── (ADR-0052 S5) All rail derivation moved to hearthHelpers.ts;
    //    thinking signal + context timer → railStore.

    // Scroll helpers делегують у MessageList (який володіє scroll container-ом).
    function scrollToBottom() {
        messageListRef?.scrollToBottom();
    }
    function isNearBottom(): boolean {
        return messageListRef?.isNearBottom() ?? true;
    }
    function isSelectingMessageText(): boolean {
        return messageListRef?.isSelectingMessageText() ?? false;
    }

    function maybeScrollToBottom(force = false) {
        if (force || (isNearBottom() && !isSelectingMessageText())) {
            scrollToBottom();
        }
    }

    // ── send message (store handles optimistic add + rollback + fast poll) ──
    async function sendMessage() {
        const text = inputText.trim();
        if (!text || sending) return;
        inputText = "";
        ondraftchange("");
        inputBarRef?.resetHeight();

        await tick();
        scrollToBottom();

        const result = await chatStore.send(text);
        if (!result.ok) {
            // I7 degraded-but-loud: restore draft у інпут, UI показує error banner з store.
            inputText = result.text;
            ondraftchange(result.text);
        }
        await tick();
        maybeScrollToBottom(true);
    }

    // Voice + Ctrl/Cmd+Enter keyboard тепер у InputBar (ADR-0052 S4).

    // ── polling for new messages — лоіка повністю у chatStore. Thinking signal +
    //    context timer — railStore. Тут лишається тільки лінкування lifecycle. ──

    // Scroll-to-bottom коли store додає нові повідомлення (не під час user-selection).
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

    // Після первинного завантаження — позначити останнє archi-повідомлення
    // як "вже промовлене", щоб auto-TTS не читав історію при відкритті вкладки.
    let _ttsInitialized = false;
    $effect(() => {
        if (_ttsInitialized || chatStore.loading) return;
        const lastArchi = [...chatStore.messages]
            .reverse()
            .find((m) => m.role === "archi");
        if (lastArchi) ttsStore.seed(lastArchi.id);
        _ttsInitialized = true;
    });

    // ── lifecycle ──
    onMount(() => {
        // chatStore.init() = загрузка history + старт normal polling (8s) + fast-poll готовий.
        // railStore.init()  = initial shell snapshot + thinking signal + 30s poll + context timer.
        void chatStore.init();
        void railStore.init();
    });

    onDestroy(() => {
        chatStore.shutdown();
        railStore.shutdown();
    });

    // draft prop (від App.svelte) → inputText. Height synchronization тепер у InputBar.
    $effect(() => {
        if (draft === inputText) return;
        inputText = draft;
    });

    const bias = $derived(getBias(directives));
    const activeHandoff = $derived(handoff);
    // ── (ADR-0052 S6) Mobile-only collapse toggle for the context rail. ──
    let railCollapsed = $state(false);
    const handoffDraftActive = $derived.by(() => {
        const current = activeHandoff;
        if (!current) return false;
        return inputText.trim() === current.prompt.trim();
    });
    const modeHearth = $derived(
        buildModeHearth({
            directives,
            agentState,
            latestThinking,
            lastThinkingSyncMs,
            directivesSyncError,
            agentStateSyncError,
            thinkingSyncError,
            loading,
        }),
    );
    const pulseFreshness = $derived(
        buildPulseFreshness(
            lastDirectivesSyncMs,
            lastAgentStateSyncMs,
            directivesSyncError,
            agentStateSyncError,
        ),
    );
    const pulseCards = $derived(buildPulseCards(directives, agentState));

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
</script>

<!-- ── Chat Header (ADR-0052 S5) ── -->
<ChatHeader
    mood={typeof directives?.mood === "string" ? directives.mood : ""}
    focusSymbol={typeof directives?.focus_symbol === "string"
        ? directives.focus_symbol
        : ""}
    biasLabel={bias.label}
    biasColor={bias.color}
    ttsSupported={ttsStore.supported}
    ttsAuto={ttsStore.auto}
    ontoggletts={() => ttsStore.toggleAuto()}
/>

<!-- ── Context Rail (ADR-0052 S5/S6): scrollable on mobile + collapse toggle ── -->
<button
    class="rail-toggle"
    type="button"
    aria-expanded={!railCollapsed}
    aria-controls="chat-context-rail"
    onclick={() => (railCollapsed = !railCollapsed)}
>
    {railCollapsed ? "▼ Контекст" : "▲ Сховати контекст"}
</button>
<div id="chat-context-rail" class="chat-context-rail" class:collapsed={railCollapsed}>
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

    {#if modeHearth}
        <ModeHearth hearth={modeHearth} onaction={primeDraft} />
    {/if}

    {#if pulseCards.length > 0}
        <PulseBoard
            cards={pulseCards}
            freshness={pulseFreshness}
            oncardaction={primeDraft}
        />
    {/if}

    {#if directives?.inner_thought}
        <PinnedThought
            text={directives.inner_thought}
            faded={!contextVisible}
        />
    {/if}
</div><!-- /chat-context-rail -->

<!-- ── Messages Area (ADR-0052 S3) ── -->
<MessageList
    bind:this={messageListRef}
    {messages}
    {loading}
    {awaitingReply}
    innerThought={directives?.inner_thought ?? ""}
    ttsSupported={ttsStore.supported}
    onspeak={(text) => ttsStore.speak(text)}
/>

<!-- ── Input Bar (ADR-0052 S4) ── -->
<InputBar
    bind:this={inputBarRef}
    bind:value={inputText}
    {sending}
    {error}
    {contextActions}
    onsend={sendMessage}
    oninputchange={(t) => ondraftchange(t)}
/>

<style>
    /* ═══ Chat Shell (ADR-0052 S5) ═══
     *
     * Header, handoff strip, mode hearth, pulse board, pinned thought — всі мають
     * власні `<style>` у своїх компонентах. Messages/bubbles → MessageList (S3).
     * Input bar/voice/emoji → InputBar (S4). Тут лишається ТІЛЬКИ .chat-context-rail
     * scroll wrapper + його mobile max-height clamp.
     */

    /* ── Handoff strip ── */
    /* ── Context Rail wrapper (scroll + mobile clamp) ── */
    .chat-context-rail {
        flex-shrink: 1;
        overflow-y: auto;
        overflow-x: hidden;
        overscroll-behavior-y: contain;
        transition: max-height 0.24s ease;
    }

    /* Collapse toggle — mobile only (display: none on desktop). */
    .rail-toggle {
        display: none;
    }

    @media (max-width: 768px) {
        .rail-toggle {
            display: block;
            align-self: stretch;
            padding: 6px 12px;
            border: none;
            border-bottom: 1px solid var(--border);
            background: color-mix(in srgb, var(--surface) 94%, var(--bg));
            color: var(--text-muted);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-align: left;
            cursor: pointer;
            flex-shrink: 0;
        }
        .rail-toggle:hover { color: var(--text); }
        .chat-context-rail {
            max-height: 40vh;
            /* Fade hint at bottom when scrollable */
            mask-image: linear-gradient(
                to bottom,
                black calc(100% - 16px),
                transparent 100%
            );
            -webkit-mask-image: linear-gradient(
                to bottom,
                black calc(100% - 16px),
                transparent 100%
            );
        }
        .chat-context-rail.collapsed {
            max-height: 0;
            border-bottom: none;
        }
    }
</style>
