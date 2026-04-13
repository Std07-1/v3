<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import { marked } from "marked";
    import { api } from "../lib/api";
    import type {
        AgentState,
        ChatHandoff,
        ChatMessage,
        DecisionEntry,
        Directives,
        MarketMentalModel,
        MetaCognition,
        TrackedPromise,
    } from "../lib/types";

    type PulseCardTone = "live" | "change" | "ledger" | "care" | "risk";
    type PulseCard = {
        id: string;
        kicker: string;
        title: string;
        detail: string;
        meta?: string;
        tone: PulseCardTone;
        prompt: string;
    };

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
    let messages = $state<ChatMessage[]>([]);
    let inputText = $state("");
    let sending = $state(false);
    let loading = $state(true);
    let error = $state("");

    // Market pulse
    let directives = $state<Directives | null>(null);
    let agentState = $state<AgentState | null>(null);
    let lastDirectivesSyncMs = $state(0);
    let lastAgentStateSyncMs = $state(0);
    let directivesSyncError = $state("");
    let agentStateSyncError = $state("");

    // Voice
    let listening = $state(false);
    let voiceSupported = $state(false);
    let voiceError = $state("");
    let recognition: any = null;

    // Input focus state
    let inputFocused = $state(false);
    const DESKTOP_INPUT_MAX = 140;
    const MOBILE_INPUT_MIN = 52;
    const MOBILE_INPUT_MAX = 196;
    let quickActionHint = $state("");
    let quickActionHintTimer: ReturnType<typeof setTimeout> | null = null;

    marked.setOptions({ breaks: true, gfm: true });

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
        ondraftchange(inputText);
        showEmoji = false;
        tick().then(() => applyComposerHeight());
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

    function clamp(value: number, min: number, max: number): number {
        return Math.min(max, Math.max(min, value));
    }

    function isMobileViewport(): boolean {
        return (
            typeof window !== "undefined" &&
            window.matchMedia("(max-width: 768px)").matches
        );
    }

    function getComposerMaxHeight(): number {
        const vh = window.visualViewport?.height ?? window.innerHeight;
        return Math.round(Math.min(vh * 0.28, MOBILE_INPUT_MAX));
    }

    function applyComposerHeight() {
        if (!textareaEl) return;

        const mobile = isMobileViewport();
        const min = mobile ? MOBILE_INPUT_MIN : 44;
        const max = mobile ? getComposerMaxHeight() : DESKTOP_INPUT_MAX;

        textareaEl.style.height = "auto";
        const natural = textareaEl.scrollHeight;
        const target = clamp(natural, min, max);

        textareaEl.style.height = `${target}px`;
        textareaEl.style.overflowY = natural > target ? "auto" : "hidden";
    }

    // Auto-grow textarea
    function autoGrow() {
        applyComposerHeight();
    }

    function setQuickActionHint(text: string) {
        quickActionHint = text;
        if (quickActionHintTimer) clearTimeout(quickActionHintTimer);
        quickActionHintTimer = setTimeout(() => {
            quickActionHint = "";
            quickActionHintTimer = null;
        }, 2600);
    }

    function setDraft(text: string, hint: string) {
        inputText = text;
        ondraftchange(text);
        setQuickActionHint(hint);

        tick().then(() => {
            applyComposerHeight();
            textareaEl?.focus();
            const caret = inputText.length;
            textareaEl?.setSelectionRange?.(caret, caret);
        });
    }

    function primeDraft(text: string) {
        const existingDraft = inputText.trim();
        const nextText = existingDraft ? `${inputText}\n\n${text}` : text;
        setDraft(
            nextText,
            existingDraft
                ? "Команду додано в чернетку. Відправка лишається ручною."
                : "Чернетка вставлена. Перевір і відправ вручну.",
        );
    }

    function restoreHandoffDraft(context: ChatHandoff) {
        setDraft(
            context.prompt,
            `Чернетку відновлено з ${handoffSourceLabel(context.source)}.`,
        );
    }

    function escapeHtml(text: string): string {
        return text.replace(/[&<>"']/g, (char) => {
            switch (char) {
                case "&":
                    return "&amp;";
                case "<":
                    return "&lt;";
                case ">":
                    return "&gt;";
                case '"':
                    return "&quot;";
                case "'":
                    return "&#39;";
                default:
                    return char;
            }
        });
    }

    function renderMessageHtml(msg: ChatMessage): string {
        if (msg.role === "user") {
            return escapeHtml(msg.text).replace(/\n/g, "<br />");
        }

        const rendered = marked.parse(msg.text) as string;
        return sanitizeRenderedHtml(rendered);
    }

    function sanitizeRenderedHtml(html: string): string {
        if (typeof document === "undefined") return html;

        const allowedTags = new Set([
            "A",
            "BLOCKQUOTE",
            "BR",
            "CODE",
            "EM",
            "LI",
            "OL",
            "P",
            "PRE",
            "STRONG",
            "UL",
        ]);
        const template = document.createElement("template");
        template.innerHTML = html;

        const elements = Array.from(template.content.querySelectorAll("*"));
        for (const element of elements) {
            if (!allowedTags.has(element.tagName)) {
                element.replaceWith(
                    document.createTextNode(element.textContent ?? ""),
                );
                continue;
            }

            for (const attr of Array.from(element.attributes)) {
                const name = attr.name.toLowerCase();
                if (element.tagName === "A" && name === "href") {
                    const value = attr.value.trim();
                    if (!/^(https?:|mailto:)/i.test(value)) {
                        element.removeAttribute(attr.name);
                    }
                    continue;
                }
                if (
                    element.tagName === "A" &&
                    (name === "target" || name === "rel")
                ) {
                    continue;
                }
                element.removeAttribute(attr.name);
            }

            if (element.tagName === "A" && element.hasAttribute("href")) {
                element.setAttribute("target", "_blank");
                element.setAttribute("rel", "noopener noreferrer");
            }
        }

        return template.innerHTML;
    }

    function toNumber(value: unknown): number | null {
        if (typeof value === "number" && Number.isFinite(value)) return value;
        if (typeof value === "string" && value.trim()) {
            const parsed = Number(value);
            if (Number.isFinite(parsed)) return parsed;
        }
        return null;
    }

    function truncateText(text: string, max = 120): string {
        const compact = text.trim().replace(/\s+/g, " ");
        if (!compact) return "";
        if (compact.length <= max) return compact;
        return `${compact.slice(0, max - 1).trimEnd()}…`;
    }

    function relativeMinutes(minutes: number): string {
        if (minutes <= 0) return "щойно";
        if (minutes === 1) return "1 хв тому";
        return `${minutes} хв тому`;
    }

    function countdownLabel(targetMs: number): string {
        const diffMinutes = Math.round((targetMs - Date.now()) / 60000);
        if (diffMinutes <= 0) return "wake вже настав";
        if (diffMinutes === 1) return "wake через 1 хв";
        return `wake через ${diffMinutes} хв`;
    }

    function getMarketMentalModel(
        dir: Directives | null,
    ): MarketMentalModel | null {
        const model = dir?.market_mental_model;
        return model && typeof model === "object" && !Array.isArray(model)
            ? (model as MarketMentalModel)
            : null;
    }

    function getMetacognition(dir: Directives | null): MetaCognition | null {
        const meta = dir?.metacognition;
        return meta && typeof meta === "object" && !Array.isArray(meta)
            ? (meta as MetaCognition)
            : null;
    }

    function getDecisionEntries(dir: Directives | null): DecisionEntry[] {
        return Array.isArray(dir?.decision_log)
            ? (dir.decision_log as DecisionEntry[])
            : [];
    }

    function getActivePromises(dir: Directives | null): TrackedPromise[] {
        const promises = getMetacognition(dir)?.tracked_promises;
        if (!Array.isArray(promises)) return [];
        return promises.filter(
            (entry) => entry?.status === "active" && !!entry.text,
        );
    }

    function decisionCategoryLabel(category: string | undefined): string {
        switch (category) {
            case "risk":
                return "Ризик";
            case "exit":
                return "Вихід";
            case "rule":
                return "Правило";
            case "skip":
                return "Пропуск";
            case "scenario":
            default:
                return "Рішення";
        }
    }

    function userSignalCopy(signal: string): { title: string; detail: string } {
        switch (signal) {
            case "active_focus":
                return {
                    title: "Фокус і прямота",
                    detail: "Archi читає контакт як концентрований: потрібні короткі ясні відповіді без шуму.",
                };
            case "absence":
                return {
                    title: "Повернення після паузи",
                    detail: "Контур очікує м'якого re-entry: що змінилось, що важливо і що не потребує паніки.",
                };
            case "reflective":
                return {
                    title: "Рефлексивний режим",
                    detail: "Важливо не лише сказати що робити, а й чому це зараз має сенс.",
                };
            case "post_loss":
                return {
                    title: "Після втрати",
                    detail: "Пріоритет зміщується з дії на стабілізацію, ясність і контроль ризику.",
                };
            case "pre_session":
                return {
                    title: "Передсесійна підготовка",
                    detail: "Контур чекає чіткий brief: рівні, умова активації, ризик і wake plan.",
                };
            default:
                return {
                    title: "Контакт стабільний",
                    detail: "Спеціальний user-signal не зафіксований; Archi тримає звичайний тон супроводу.",
                };
        }
    }

    function recordPulseSync(
        nextDirectives: Directives | null,
        nextAgentState: AgentState | null,
        phase: "initial" | "refresh",
    ) {
        const now = Date.now();

        if (nextDirectives) {
            directives = nextDirectives;
            lastDirectivesSyncMs = now;
            directivesSyncError = "";
        } else {
            directivesSyncError =
                phase === "initial"
                    ? "Directives snapshot не завантажився. Pulse Rail частково сліпий."
                    : "Directives snapshot не оновився. Cards на основі directives можуть бути застарілими.";
        }

        if (nextAgentState) {
            agentState = nextAgentState;
            lastAgentStateSyncMs = now;
            agentStateSyncError = "";
        } else {
            agentStateSyncError =
                phase === "initial"
                    ? "Agent state snapshot не завантажився. Pulse Rail частково сліпий."
                    : "Agent state snapshot не оновився. Freshness / wake info може бути застарілим.";
        }
    }

    // ── bias helpers ──
    function getBias(dir: Directives | null): { label: string; color: string } {
        const bm = dir?.bias_map as Record<string, unknown> | null;
        if (!bm) return { label: "", color: "" };

        const priority = ["H4", "D1", "H1"];
        for (const tf of priority) {
            const val = bm[tf];
            if (typeof val === "string") {
                const lower = val.toLowerCase();
                if (lower.includes("bull") || lower.includes("long")) {
                    return { label: `${tf}: bull`, color: "bull" };
                }
                if (lower.includes("bear") || lower.includes("short")) {
                    return { label: `${tf}: bear`, color: "bear" };
                }
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
            const [nextDirectives, nextMessages, nextAgentState] =
                await Promise.all([
                    api.directives(false).catch(() => null),
                    api
                        .chatHistory(80)
                        .then((r) => r.messages ?? [])
                        .catch(() => []),
                    api.agentState().catch(() => null),
                ]);
            messages = nextMessages;
            recordPulseSync(nextDirectives, nextAgentState, "initial");
            // Prevent auto-TTS of existing messages on load
            const _lastArchi = [...messages]
                .reverse()
                .find((m) => m.role === "archi");
            if (_lastArchi) lastSpokenId = _lastArchi.id;
        } catch {
            directivesSyncError =
                "Directives snapshot не завантажився. Початковий shell sync не вдався.";
            agentStateSyncError =
                "Agent state snapshot не завантажився. Початковий shell sync не вдався.";
            // graceful
        } finally {
            loading = false;
            await tick();
            scrollToBottom();
        }
    }

    async function refreshPulseData() {
        try {
            const [nextDirectives, nextAgentState] = await Promise.all([
                api.directives(false).catch(() => null),
                api.agentState().catch(() => null),
            ]);
            recordPulseSync(nextDirectives, nextAgentState, "refresh");
        } catch {
            directivesSyncError =
                "Directives snapshot не оновився. Показано останній успішний directives state.";
            agentStateSyncError =
                "Agent state snapshot не оновився. Показано останній успішний agent state.";
        }
    }

    function scrollToBottom() {
        if (messagesEl) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    }

    function isNearBottom(): boolean {
        if (!messagesEl) return true;
        const remaining =
            messagesEl.scrollHeight -
            messagesEl.scrollTop -
            messagesEl.clientHeight;
        return remaining < 72;
    }

    function isSelectingMessageText(): boolean {
        const selection = window.getSelection?.();
        if (!selection || selection.isCollapsed) return false;
        const anchor = selection.anchorNode;
        const focus = selection.focusNode;
        return !!(
            anchor &&
            focus &&
            messagesEl?.contains(anchor) &&
            messagesEl.contains(focus)
        );
    }

    function maybeScrollToBottom(force = false) {
        if (force || (isNearBottom() && !isSelectingMessageText())) {
            scrollToBottom();
        }
    }

    // ── send message (async: POST saves + bot replies via Redis) ──
    let awaitingReply = $state(false);
    let lastSentId = $state("");

    async function sendMessage() {
        const text = inputText.trim();
        if (!text || sending) return;
        quickActionHint = "";
        if (quickActionHintTimer) {
            clearTimeout(quickActionHintTimer);
            quickActionHintTimer = null;
        }
        inputText = "";
        ondraftchange("");
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
            inputText = text;
            ondraftchange(text);
            messages = messages.filter((m) => m.id !== tmpMsg.id);
        } finally {
            sending = false;
            await tick();
            maybeScrollToBottom(true);
        }
    }

    function handleKeydown(e: KeyboardEvent) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
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
            if (transcript) {
                inputText = (inputText + " " + transcript).trim();
                ondraftchange(inputText);
                tick().then(() => applyComposerHeight());
            }
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
    let agentStatePollId: ReturnType<typeof setInterval> | null = null;
    const NORMAL_POLL_MS = 8_000;
    const FAST_POLL_MS = 2_000;
    const FAST_POLL_MAX_MS = 90_000; // stop fast polling after 90s

    async function pollMessages() {
        try {
            const shouldStickToBottom = awaitingReply || isNearBottom();
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
                if (!isSelectingMessageText() && shouldStickToBottom) {
                    scrollToBottom();
                }
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
        tick().then(() => applyComposerHeight());
        agentStatePollId = setInterval(refreshPulseData, 30_000);

        const syncComposer = () =>
            requestAnimationFrame(() => applyComposerHeight());
        window.visualViewport?.addEventListener("resize", syncComposer);
        window.addEventListener("orientationchange", syncComposer);

        return () => {
            window.visualViewport?.removeEventListener("resize", syncComposer);
            window.removeEventListener("orientationchange", syncComposer);
        };
    });

    onDestroy(() => {
        clearInterval(pollId);
        if (agentStatePollId) clearInterval(agentStatePollId);
        stopFastPoll();
        if (recognition && listening) recognition.stop();
        if (quickActionHintTimer) clearTimeout(quickActionHintTimer);
    });

    $effect(() => {
        if (draft === inputText) return;
        inputText = draft;
        tick().then(() => applyComposerHeight());
    });

    const bias = $derived(getBias(directives));
    const levels = $derived(getWatchLevels(directives));
    const activeHandoff = $derived(handoff);
    const handoffDraftActive = $derived.by(() => {
        const current = activeHandoff;
        if (!current) return false;
        return inputText.trim() === current.prompt.trim();
    });
    const pulseFreshness = $derived.by(() => {
        const freshest = [lastDirectivesSyncMs, lastAgentStateSyncMs].filter(
            (value) => value > 0,
        );
        const syncedMinutes = freshest.length
            ? Math.max(
                  0,
                  Math.round((Date.now() - Math.min(...freshest)) / 60000),
              )
            : null;
        const syncErrors = [directivesSyncError, agentStateSyncError].filter(
            Boolean,
        );

        if (syncErrors.length > 0) {
            return {
                label:
                    syncedMinutes !== null
                        ? `stale · sync ${relativeMinutes(syncedMinutes)}`
                        : "stale",
                tone: "stale",
                detail: syncErrors.join(" · "),
            };
        }

        if (syncedMinutes === null) {
            return {
                label: "sync pending",
                tone: "pending",
                detail: "",
            };
        }

        return {
            label:
                syncedMinutes === 0
                    ? "sync щойно"
                    : `sync ${relativeMinutes(syncedMinutes)}`,
            tone: "live",
            detail: "",
        };
    });
    const pulseCards = $derived.by(() => {
        const cards: PulseCard[] = [];
        const mentalModel = getMarketMentalModel(directives);
        const decisions = getDecisionEntries(directives);
        const activePromises = getActivePromises(directives);
        const userSignal =
            typeof directives?.user_signal === "string"
                ? directives.user_signal
                : "";
        const signalCopy = userSignalCopy(userSignal);

        const observationEnabled = directives?.observation_enabled !== false;
        const observationInterval = Math.max(
            5,
            toNumber(directives?.observation_interval_minutes) ?? 25,
        );
        const lastObservationTs = toNumber(directives?.last_observation_ts);
        const lastObservationMinutes =
            lastObservationTs !== null
                ? Math.max(
                      0,
                      Math.round((Date.now() / 1000 - lastObservationTs) / 60),
                  )
                : null;
        const nextWakeMs = toNumber(agentState?.next_wake_ms);
        const nextWakeReason =
            typeof agentState?.next_wake_reason === "string" &&
            agentState.next_wake_reason.trim()
                ? agentState.next_wake_reason
                : typeof directives?.next_check_reason === "string"
                  ? directives.next_check_reason
                  : "";
        const observationOverdue =
            observationEnabled &&
            lastObservationMinutes !== null &&
            lastObservationMinutes > observationInterval + 5;
        const observationTitle = !observationEnabled
            ? "Observation beat вимкнений"
            : lastObservationMinutes === null
              ? "Observation beat ще не зафіксований"
              : observationOverdue
                ? `Observation прострочений на ${lastObservationMinutes - observationInterval} хв`
                : `Останній beat ${relativeMinutes(lastObservationMinutes)}`;
        cards.push({
            id: "pulse-observation",
            kicker: "Pulse Rail",
            title: observationTitle,
            detail: truncateText(
                mentalModel?.what_watching ||
                    nextWakeReason ||
                    (typeof directives?.inner_thought === "string"
                        ? directives.inner_thought
                        : "") ||
                    "У shell ще нема короткого пояснення, що Арчі реально тримає у фокусі прямо зараз.",
            ),
            meta: nextWakeMs
                ? countdownLabel(nextWakeMs)
                : `cadence ${observationInterval} хв`,
            tone: observationOverdue ? "risk" : "live",
            prompt: observationEnabled
                ? "Поясни свій поточний pulse: коли ти востаннє перевіряв ринок, що зараз тримаєш у фокусі і коли повернешся до наступного observation beat?"
                : "Поясни, чому observation beat зараз вимкнений, який це створює ризик і що має повернути тебе назад у регулярний ритм спостереження.",
        });

        const changeTitle = truncateText(
            mentalModel?.what_changed || mentalModel?.current_narrative || "",
            92,
        );
        if (changeTitle) {
            const updatedAt = toNumber(mentalModel?.updated_at);
            const updatedMinutes =
                updatedAt !== null
                    ? Math.max(
                          0,
                          Math.round((Date.now() / 1000 - updatedAt) / 60),
                      )
                    : null;
            cards.push({
                id: "pulse-change",
                kicker: "Що змінилось",
                title: changeTitle,
                detail:
                    truncateText(
                        mentalModel?.what_watching ||
                            mentalModel?.structure_reasoning ||
                            mentalModel?.macro_reasoning ||
                            "",
                    ) ||
                    "Archi зафіксував зміну, але ще не розгорнув її в окреме пояснення.",
                meta:
                    updatedMinutes !== null
                        ? `оновлено ${relativeMinutes(updatedMinutes)}`
                        : undefined,
                tone: "change",
                prompt: "Розгорни, що саме змінилось у твоїй картині ринку, що це означає для поточного сценарію і що саме тепер потребує уваги.",
            });
        }

        const lastDecision = decisions[decisions.length - 1];
        if (lastDecision?.decision) {
            const createdAt = toNumber(lastDecision.created_at);
            const decisionMinutes =
                createdAt !== null
                    ? Math.max(
                          0,
                          Math.round((Date.now() / 1000 - createdAt) / 60),
                      )
                    : null;
            const alternativesText =
                Array.isArray(lastDecision.alternatives) &&
                lastDecision.alternatives.length > 0
                    ? `Альтернативи: ${lastDecision.alternatives.join(" · ")}`
                    : "";
            cards.push({
                id: "pulse-decision",
                kicker: decisionCategoryLabel(lastDecision.category),
                title: truncateText(lastDecision.decision, 92),
                detail: truncateText(
                    lastDecision.reasoning ||
                        alternativesText ||
                        "Decision trail існує, але reasoning ще не розгорнуте.",
                ),
                meta:
                    decisionMinutes !== null
                        ? `записано ${relativeMinutes(decisionMinutes)}`
                        : undefined,
                tone: "ledger",
                prompt: `Розбери останнє рішення: ${lastDecision.decision}\n\nПоясни reasoning, альтернативи і що може змусити тебе переглянути цей вибір.`,
            });
        }

        if (activePromises.length > 0 || userSignal) {
            const firstPromise = activePromises[0]?.text ?? "";
            const careMetaParts: string[] = [];
            if (userSignal) careMetaParts.push(signalCopy.title);
            if (typeof directives?.mood === "string" && directives.mood) {
                careMetaParts.push(`mood: ${directives.mood}`);
            }
            cards.push({
                id: "pulse-care",
                kicker: "Care Loop",
                title:
                    activePromises.length > 0
                        ? `${activePromises.length} активні обіцянки`
                        : signalCopy.title,
                detail:
                    truncateText(
                        activePromises.length > 0
                            ? `${firstPromise}${userSignal ? ` · Сигнал контакту: ${signalCopy.detail}` : ""}`
                            : signalCopy.detail,
                    ) ||
                    "Care loop активний, але shell ще не має короткого пояснення людського контуру.",
                meta: careMetaParts.join(" · ") || undefined,
                tone: "care",
                prompt:
                    activePromises.length > 0
                        ? "Пройдися по активних обіцянках: що ще відкрите, що реально під ризиком і що ти хочеш закрити наступним."
                        : "Скажи прямо, як ти зараз читаєш мій стан, що це означає для тону взаємодії і що тобі важливо не проґавити в найближчому контакті.",
            });
        }

        return cards.slice(0, 4);
    });

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
                    ? "Вимкнути авто-озвучення (браузерне, лише у відкритій вкладці)"
                    : "Увімкнути авто-озвучення (браузерне, лише у відкритій вкладці)"}
            >
                {ttsAuto ? "🔊 Авто" : "🔇"}
            </button>
        {/if}
    </div>
</div>

{#if activeHandoff}
    <div class="handoff-strip">
        <div class="handoff-meta">
            <span class="handoff-kicker"
                >{handoffSourceLabel(activeHandoff.source)}</span
            >
            {#if activeHandoff.ts_ms}
                <span class="handoff-ts">{formatTs(activeHandoff.ts_ms)}</span>
            {/if}
            {#if activeHandoff.symbol}
                <span class="handoff-symbol">{activeHandoff.symbol}</span>
            {/if}
        </div>

        <div class="handoff-main">
            <span class="handoff-icon">{activeHandoff.icon}</span>
            <div class="handoff-copy">
                <div class="handoff-title-row">
                    <div class="handoff-title">{activeHandoff.title}</div>
                    {#if handoffDraftActive}
                        <span class="handoff-badge">у чернетці</span>
                    {/if}
                </div>
                <div class="handoff-summary">{activeHandoff.summary}</div>
            </div>
        </div>

        <div class="handoff-actions">
            <button
                class="handoff-btn"
                class:active={handoffDraftActive}
                onclick={() => restoreHandoffDraft(activeHandoff)}
            >
                Відновити чернетку
            </button>
            <button
                class="handoff-btn subtle"
                onclick={() => ondismisshandoff(activeHandoff.id)}
            >
                Сховати
            </button>
        </div>
    </div>
{/if}

{#if pulseCards.length > 0}
    <div class="pulse-board">
        <div class="pulse-board-head">
            <div>
                <div class="pulse-board-kicker">Living Platform</div>
                <div class="pulse-board-title">Pulse Rail</div>
            </div>
            <div class="pulse-board-summary">
                <div class="pulse-board-copy">
                    Живий контур shell: observation beat, остання зміна,
                    decision trail і care loop без виходу з conversation stage.
                </div>
                <div class="pulse-board-status" data-tone={pulseFreshness.tone}>
                    {pulseFreshness.label}
                </div>
            </div>
        </div>

        {#if pulseFreshness.detail}
            <div class="pulse-board-warning">{pulseFreshness.detail}</div>
        {/if}

        <div class="pulse-cards">
            {#each pulseCards as card (card.id)}
                <article class={`pulse-card ${card.tone}`}>
                    <div class="pulse-card-top">
                        <span class="pulse-card-kicker">{card.kicker}</span>
                        {#if card.meta}
                            <span class="pulse-card-meta">{card.meta}</span>
                        {/if}
                    </div>

                    <div class="pulse-card-title">{card.title}</div>
                    <div class="pulse-card-detail">{card.detail}</div>

                    <button
                        class="pulse-card-btn"
                        onclick={() => primeDraft(card.prompt)}
                    >
                        У чернетку
                    </button>
                </article>
            {/each}
        </div>
    </div>
{/if}

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
                    <div class="bubble-text prose">
                        {@html renderMessageHtml(msg)}
                    </div>
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

<!-- ── Input Bar ── -->
<div class="input-bar" class:focused={inputFocused}>
    {#if !inputFocused && !inputText.trim() && contextActions.length > 0}
        <div class="quick-actions compact">
            {#each contextActions as act}
                <button
                    class="qa-btn"
                    onclick={() => primeDraft(act.text)}
                    title={act.text}
                >
                    <span class="qa-icon">{act.icon}</span>
                    <span class="qa-label">{act.label}</span>
                </button>
            {/each}
        </div>
    {/if}
    {#if error}<div class="input-error">{error}</div>{/if}
    <div class="input-hint" class:accent={!!quickActionHint}>
        {quickActionHint || "Enter — новий рядок · Ctrl/Cmd+Enter — відправити"}
    </div>
    <div class="input-row">
        <textarea
            class="chat-input"
            bind:this={textareaEl}
            bind:value={inputText}
            oninput={() => {
                autoGrow();
                ondraftchange(inputText);
            }}
            onkeydown={handleKeydown}
            onfocus={() => {
                inputFocused = true;
                tick().then(() => applyComposerHeight());
            }}
            onblur={() => {
                setTimeout(() => {
                    inputFocused = false;
                }, 150);
            }}
            placeholder="Повідомлення…"
            rows={1}
            enterkeyhint="enter"
            spellcheck="true"
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
                title="Відправити (Ctrl/Cmd+Enter)"
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

    /* ── Handoff strip ── */
    .handoff-strip {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 10px 16px;
        background: color-mix(in srgb, var(--surface) 94%, var(--bg));
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
    }
    .handoff-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        font-size: 10px;
        color: var(--text-muted);
    }
    .handoff-kicker {
        color: var(--accent);
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    .handoff-symbol {
        font-weight: 700;
        letter-spacing: 0.04em;
        color: var(--text);
    }
    .handoff-main {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        min-width: 0;
    }
    .handoff-icon {
        font-size: 18px;
        line-height: 1;
        margin-top: 1px;
        flex-shrink: 0;
    }
    .handoff-copy {
        min-width: 0;
        flex: 1;
    }
    .handoff-title-row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        margin-bottom: 4px;
    }
    .handoff-title {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .handoff-badge {
        flex-shrink: 0;
        padding: 2px 6px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
        color: var(--accent);
        font-size: 10px;
        font-weight: 700;
    }
    .handoff-summary {
        font-size: 12px;
        color: var(--text-muted);
        line-height: 1.45;
        display: -webkit-box;
        line-clamp: 2;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .handoff-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .handoff-btn {
        padding: 6px 11px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, var(--surface2));
        color: var(--text);
        cursor: pointer;
        font-size: 11px;
        font-weight: 600;
        transition:
            border-color 0.15s,
            background 0.15s,
            color 0.15s;
    }
    .handoff-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 42%, transparent);
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
    }
    .handoff-btn.active {
        color: var(--accent);
    }
    .handoff-btn.subtle {
        border-color: color-mix(in srgb, var(--border) 92%, transparent);
        background: transparent;
        color: var(--text-muted);
    }
    .handoff-btn.subtle:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--border) 72%, transparent);
        background: var(--surface2);
    }

    /* ── Pulse rail ── */
    .pulse-board {
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 12px 16px 14px;
        border-bottom: 1px solid var(--border);
        background: radial-gradient(
                circle at top right,
                rgba(255, 180, 90, 0.11),
                transparent 36%
            ),
            radial-gradient(
                circle at left center,
                rgba(92, 205, 180, 0.09),
                transparent 34%
            ),
            color-mix(in srgb, var(--surface) 95%, var(--bg));
        flex-shrink: 0;
    }
    .pulse-board-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
    }
    .pulse-board-kicker {
        font-size: 10px;
        color: #f6a84a;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .pulse-board-title {
        font-size: 14px;
        font-weight: 600;
        color: var(--text);
    }
    .pulse-board-copy {
        max-width: 420px;
        font-size: 12px;
        line-height: 1.45;
        color: var(--text-muted);
    }
    .pulse-board-summary {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 6px;
    }
    .pulse-board-status {
        align-self: flex-end;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
        background: color-mix(in srgb, var(--surface2) 88%, var(--bg));
        color: var(--text-muted);
    }
    .pulse-board-status[data-tone="live"] {
        border-color: rgba(92, 205, 180, 0.28);
        color: #53caae;
    }
    .pulse-board-status[data-tone="pending"] {
        border-color: rgba(255, 180, 90, 0.28);
        color: #f6a84a;
    }
    .pulse-board-status[data-tone="stale"] {
        border-color: rgba(239, 95, 71, 0.3);
        color: #ef5f47;
    }
    .pulse-board-warning {
        padding: 9px 11px;
        border-radius: 12px;
        border: 1px solid rgba(239, 95, 71, 0.18);
        background: rgba(239, 95, 71, 0.08);
        color: var(--text);
        font-size: 11px;
        line-height: 1.45;
    }
    .pulse-cards {
        display: grid;
        gap: 10px;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    }
    .pulse-card {
        display: flex;
        flex-direction: column;
        gap: 8px;
        min-height: 146px;
        padding: 12px;
        border-radius: 16px;
        border: 1px solid color-mix(in srgb, var(--border) 86%, transparent);
        background: linear-gradient(
                180deg,
                rgba(255, 255, 255, 0.03),
                rgba(255, 255, 255, 0)
            ),
            color-mix(in srgb, var(--surface2) 88%, var(--bg));
        box-shadow: 0 12px 24px rgba(6, 10, 18, 0.08);
    }
    .pulse-card.live {
        border-color: rgba(92, 205, 180, 0.26);
    }
    .pulse-card.change {
        border-color: rgba(255, 180, 90, 0.28);
    }
    .pulse-card.ledger {
        border-color: rgba(120, 164, 255, 0.28);
    }
    .pulse-card.care {
        border-color: rgba(255, 123, 158, 0.28);
    }
    .pulse-card.risk {
        border-color: rgba(239, 95, 71, 0.32);
    }
    .pulse-card-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
    }
    .pulse-card-kicker {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-muted);
    }
    .pulse-card-meta {
        font-size: 10px;
        color: var(--text-muted);
        text-align: right;
    }
    .pulse-card-title {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        line-height: 1.4;
        display: -webkit-box;
        line-clamp: 3;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .pulse-card-detail {
        flex: 1;
        font-size: 12px;
        color: var(--text-muted);
        line-height: 1.55;
        display: -webkit-box;
        line-clamp: 4;
        -webkit-line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .pulse-card-btn {
        align-self: flex-start;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, var(--surface2));
        color: var(--text);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition:
            border-color 0.15s,
            background 0.15s,
            transform 0.15s;
    }
    .pulse-card-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 44%, transparent);
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
        transform: translateY(-1px);
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
        line-clamp: 2;
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
        overscroll-behavior-y: contain;
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
    .bubble-text :global(p) {
        margin: 0 0 0.55em;
    }
    .bubble-text :global(p:last-child) {
        margin-bottom: 0;
    }
    .bubble-text :global(strong) {
        font-weight: 650;
    }
    .bubble-text :global(blockquote) {
        margin: 0.45em 0;
        padding-left: 10px;
        border-left: 2px solid
            color-mix(in srgb, var(--accent) 45%, transparent);
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
        padding: 0 0 10px;
        margin: 0;
        background: transparent;
        flex-shrink: 0;
        overflow-x: auto;
        scrollbar-width: none;
    }
    .quick-actions.compact {
        padding-top: 2px;
    }
    .quick-actions::-webkit-scrollbar {
        display: none;
    }
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
        box-shadow: 0 -14px 32px rgba(0, 0, 0, 0.18);
    }
    .input-bar.focused {
        box-shadow: 0 -18px 36px rgba(0, 0, 0, 0.24);
    }
    .input-error {
        font-size: 12px;
        color: #e05555;
        margin-bottom: 4px;
        padding: 0 4px;
    }
    .input-hint {
        font-size: 11px;
        color: var(--text-muted);
        margin-bottom: 6px;
        padding: 0 4px;
    }
    .input-hint.accent {
        color: var(--accent);
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
        .pulse-board-head {
            flex-direction: column;
        }
        .pulse-board-summary {
            align-items: flex-start;
        }
        .pulse-board-status {
            align-self: flex-start;
        }
        .pulse-cards {
            grid-template-columns: none;
            grid-auto-flow: column;
            grid-auto-columns: minmax(228px, 78vw);
            overflow-x: auto;
            padding-bottom: 2px;
            scroll-snap-type: x proximity;
        }
        .pulse-card {
            scroll-snap-align: start;
        }
        .handoff-strip {
            padding: 10px 12px 9px;
            gap: 7px;
        }
        .handoff-title {
            font-size: 12px;
        }
        .handoff-summary {
            font-size: 11px;
        }
        .bubble-row {
            max-width: 88%;
        }
        .emoji-panel {
            width: 260px;
            right: -12px;
        }
        .pinned-thought {
            padding: 6px 12px;
            max-height: 34px;
        }
        .pt-text {
            font-size: 11px;
            line-clamp: 1;
            -webkit-line-clamp: 1;
        }

        /* ── Mobile input: textarea grows big, actions wrap below ── */
        .input-bar {
            padding: 8px 10px calc(10px + env(safe-area-inset-bottom)) 10px;
        }
        .quick-actions {
            padding-bottom: 8px;
        }
        .input-row {
            flex-wrap: wrap;
            gap: 6px;
        }
        .chat-input {
            flex-basis: 100%; /* full width, wraps alone on first line */
            max-height: min(28vh, 196px);
            min-height: 52px;
            font-size: 16px; /* prevents iOS zoom on focus */
            border-radius: 18px;
            padding: 14px 16px;
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
