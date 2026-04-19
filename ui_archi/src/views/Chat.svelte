<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import { marked } from "marked";
    import { api } from "../lib/api";
    import {
        getDirectives,
        getAgentState,
        getLastDirectivesSyncMs,
        getLastAgentStateSyncMs,
        getDirectivesError,
        getAgentStateError,
        refreshAll,
    } from "../lib/state.svelte";
    import type {
        AgentState,
        ChatHandoff,
        ChatMessage,
        DecisionEntry,
        Directives,
        MarketMentalModel,
        MetaCognition,
        ThinkingEntry,
        TrackedPromise,
    } from "../lib/types";
    import { sanitizeHtml } from "../lib/sanitize";
    import { chatStore } from "../features/chat/stores/chatStore.svelte";
    import MessageList from "../features/chat/components/MessageList.svelte";

    type HearthTone = "home" | "work" | "quiet" | "bridge" | "degraded";
    type HearthAction = {
        label: string;
        prompt: string;
        subtle?: boolean;
    };
    type ModeHearth = {
        tone: HearthTone;
        title: string;
        detail: string;
        reason: string;
        modeLabel: string;
        modelLabel?: string;
        meta: string[];
        actions: HearthAction[];
        warning?: string;
    };
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
    // messages/sending/loading/error/awaitingReply/lastSentId живуть у chatStore
    // (ADR-0052 S2). Тут лише локальний input + DOM-concerns.
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
    let latestThinking = $state<ThinkingEntry | null>(null);
    let lastThinkingSyncMs = $state(0);
    let thinkingSyncError = $state("");

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
    let messageListRef = $state<{
        scrollToBottom: () => void;
        isNearBottom: () => boolean;
        isSelectingMessageText: () => boolean;
    } | null>(null);
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

    function firstNonEmptyText(
        ...values: Array<string | null | undefined>
    ): string {
        for (const value of values) {
            if (typeof value === "string" && value.trim()) return value.trim();
        }
        return "";
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

    function thinkingTimestampMs(entry: ThinkingEntry | null): number | null {
        if (!entry) return null;
        if (typeof entry.ts === "number" && Number.isFinite(entry.ts)) {
            return Math.round(entry.ts * 1000);
        }
        const parsed = toNumber(entry.ts);
        return parsed !== null ? Math.round(parsed * 1000) : null;
    }

    function normalizeCallType(value: string | undefined): string {
        return value?.trim().toLowerCase() ?? "";
    }

    function modeToneFromCallType(
        callType: string,
        nextWakeMs: number | null,
    ): HearthTone {
        if (
            callType === "reactive_casual" ||
            callType === "manual" ||
            callType === "casual"
        ) {
            return "home";
        }
        if (
            callType === "reactive_market" ||
            callType === "proactive" ||
            callType === "full" ||
            callType === "observation" ||
            callType === "daily_review" ||
            callType === "ritual_weekly" ||
            callType === "system_upgrade" ||
            callType === "tda"
        ) {
            return "work";
        }
        if (callType === "reactive" || callType === "consciousness_recovery") {
            return "bridge";
        }
        if (nextWakeMs !== null && nextWakeMs > Date.now()) return "quiet";
        return "quiet";
    }

    function modeLabelFromCallType(callType: string, tone: HearthTone): string {
        switch (callType) {
            case "reactive_casual":
                return "Casual Contact";
            case "reactive_market":
                return "Market Contact";
            case "proactive":
                return "Proactive Analyst";
            case "full":
                return "Full Context";
            case "observation":
                return "Observation";
            case "reactive":
                return "Reactive Contact";
            case "consciousness_recovery":
                return "Recovery";
            case "daily_review":
                return "Daily Review";
            case "ritual_weekly":
                return "Weekly Ritual";
            case "manual":
                return "Manual Contact";
            case "system_upgrade":
                return "System Upgrade";
            case "tda":
                return "TDA Review";
            default:
                switch (tone) {
                    case "home":
                        return "Casual Presence";
                    case "work":
                        return "Analyst Contour";
                    case "bridge":
                        return "Live Contact";
                    case "degraded":
                        return "Unavailable";
                    case "quiet":
                    default:
                        return "Standby";
                }
        }
    }

    function currentModeModel(
        entry: ThinkingEntry | null,
        state: AgentState | null,
    ): string {
        return firstNonEmptyText(
            entry?.model,
            typeof state?.model_last_call === "string"
                ? state.model_last_call
                : "",
            typeof state?.model_current === "string" ? state.model_current : "",
        );
    }

    function hearthReasonCopy(
        callType: string,
        nextWakeReason: string,
        nextWakeMs: number | null,
        observationEnabled: boolean,
    ): string {
        if (nextWakeReason) {
            return `Наступний wake: ${truncateText(nextWakeReason, 112)}${
                nextWakeMs ? ` · ${countdownLabel(nextWakeMs)}` : ""
            }`;
        }

        if (callType === "reactive_casual" || callType === "manual") {
            return "Shell читає це як людський контакт без важкого analyst bootstrap: спочатку присутність, потім аналіз за потреби.";
        }
        if (
            callType === "reactive_market" ||
            callType === "proactive" ||
            callType === "full" ||
            callType === "observation" ||
            callType === "daily_review" ||
            callType === "ritual_weekly" ||
            callType === "system_upgrade" ||
            callType === "tda"
        ) {
            return "Є ринковий привід для структурованої присутності: Archi тримає analyst contour і готує brief замість дружнього small talk.";
        }
        if (callType === "reactive" || callType === "consciousness_recovery") {
            return "Останній archived call дає лише signal живого контакту: це ще не доказ чистого casual mode і не гарантія повного analyst frame.";
        }

        return observationEnabled
            ? "Observation beat лишається увімкненим, але shell поки не має явного wake reason для наступної ескалації."
            : "Observation beat зараз мовчить; Archi чекає зовнішній trigger або ручний контакт.";
    }

    function hearthActions(tone: HearthTone): HearthAction[] {
        switch (tone) {
            case "home":
                return [
                    {
                        label: "Як ти зараз?",
                        prompt: "Скажи просто і по-людськи: ти зараз вдома як друг чи вже напівв роботі, що в тебе на думці і що мені важливо знати без зайвого формалізму?",
                    },
                    {
                        label: "Коли розбудиш?",
                        prompt: "Поясни, який наступний trigger або wake_reason переведе тебе з дружнього режиму в analyst mode і як ти це мені подаси.",
                        subtle: true,
                    },
                ];
            case "work":
                return [
                    {
                        label: "Operator brief",
                        prompt: "Дай operator brief на зараз: bias, active thesis, risk trigger, next wake condition і що конкретно може змінити сценарій.",
                    },
                    {
                        label: "Переклади просто",
                        prompt: "Переклади свій поточний analyst mode людською мовою: що це означає для мене прямо зараз без зайвого noise.",
                        subtle: true,
                    },
                ];
            case "bridge":
                return [
                    {
                        label: "Що перемикає режим?",
                        prompt: "Поясни, між якими режимами ти зараз стоїш, що саме тригерить перемикання і яка ознака, що пора ввімкнути аналітика повністю.",
                    },
                    {
                        label: "Коротко по-людськи",
                        prompt: "Дай короткий статус як друг, але збережи головний risk context: що ти зараз відчуваєш у ринку і чому це важливо або не важливо.",
                        subtle: true,
                    },
                ];
            case "quiet":
            default:
                return [
                    {
                        label: "Чому тиша?",
                        prompt: "Поясни, чому зараз тиша є правильною, що ти все одно тримаєш у фоні і який наступний wake trigger важливий.",
                    },
                    {
                        label: "Легкий статус",
                        prompt: "Дай короткий домашній статус у двох-трьох реченнях: як ти зараз, що з ринком у фоні і коли чекати наступний осмислений signal.",
                        subtle: true,
                    },
                ];
            case "degraded":
                return [];
        }
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

    // recordPulseSync is no longer needed — directives & agentState
    // are managed by the shared state module (state.svelte.ts).
    // Thinking signal remains Chat-specific.

    function recordThinkingSync(
        nextThinking: ThinkingEntry | null,
        ok: boolean,
        phase: "initial" | "refresh",
    ) {
        if (ok) {
            latestThinking = nextThinking;
            lastThinkingSyncMs = Date.now();
            thinkingSyncError = "";
            return;
        }

        thinkingSyncError =
            phase === "initial"
                ? "Thinking journal snapshot не завантажився. Mode Hearth читає режим без last-call signal."
                : "Thinking journal snapshot не оновився. Mode Hearth може спиратись на попередній call signal.";
    }

    async function loadLatestThinkingSignal(): Promise<{
        ok: boolean;
        entry: ThinkingEntry | null;
    }> {
        try {
            const result = await api.thinking(1, 0);
            return {
                ok: true,
                entry: result.entries?.[0] ?? null,
            };
        } catch {
            return {
                ok: false,
                entry: null,
            };
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
    async function loadShellData() {
        // chatStore.init() завантажує historyу + стартує polling — див. onMount.
        // Тут — лише shell-sync (directives, agent state, thinking signal).
        try {
            const [, nextThinkingSignal] = await Promise.all([
                refreshAll(false),
                loadLatestThinkingSignal(),
            ]);
            recordThinkingSync(
                nextThinkingSignal.entry,
                nextThinkingSignal.ok,
                "initial",
            );
        } catch {
            thinkingSyncError =
                "Thinking journal snapshot не завантажився. Початковий shell sync не вдався.";
        }
    }

    async function refreshShellData() {
        try {
            const [, nextThinkingSignal] = await Promise.all([
                refreshAll(false),
                loadLatestThinkingSignal(),
            ]);
            recordThinkingSync(
                nextThinkingSignal.entry,
                nextThinkingSignal.ok,
                "refresh",
            );
        } catch {
            thinkingSyncError =
                "Thinking journal snapshot не оновився. Показано останній успішний call signal.";
        }
    }

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
        quickActionHint = "";
        if (quickActionHintTimer) {
            clearTimeout(quickActionHintTimer);
            quickActionHintTimer = null;
        }
        inputText = "";
        ondraftchange("");
        if (textareaEl) textareaEl.style.height = "auto";

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

    // ── polling for new messages (логіка у chatStore, тут лишається тільки
    // Thinking-signal poll — Chat-specific, не shared) ──
    let thinkingPollId: ReturnType<typeof setInterval> | null = null;

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
        if (lastArchi) lastSpokenId = lastArchi.id;
        _ttsInitialized = true;
    });

    // ── lifecycle ──
    onMount(() => {
        // chatStore.init() = загрузка history + старт normal polling (8s) + fast-poll готовий.
        void chatStore.init();
        void loadShellData();
        initVoice();
        resetContextTimer();
        tick().then(() => applyComposerHeight());
        // Thinking signal is Chat-specific — poll separately (не messages).
        thinkingPollId = setInterval(refreshShellData, 30_000);

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
        chatStore.shutdown();
        if (thinkingPollId) clearInterval(thinkingPollId);
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
    const modeHearth = $derived.by(() => {
        const syncWarnings = [
            directivesSyncError,
            agentStateSyncError,
            thinkingSyncError,
        ].filter(Boolean);
        const hasSnapshot = !!(directives || agentState || latestThinking);

        if (!hasSnapshot) {
            if (loading || syncWarnings.length === 0) return null;

            return {
                tone: "degraded",
                title: "Mode Hearth недоступний",
                detail: "Shell не отримав жодного валідного snapshot для presence layer, тому режим не буде вигаданий поверх порожнечі.",
                reason: "Поки немає мінімального shell snapshot, Chat не робить висновок про quiet, casual чи analyst presence.",
                modeLabel: "Unavailable",
                meta: [],
                actions: [],
                warning: syncWarnings.join(" · "),
            } satisfies ModeHearth;
        }

        const mentalModel = getMarketMentalModel(directives);
        const observationEnabled = directives?.observation_enabled !== false;
        const nextWakeMs = toNumber(agentState?.next_wake_ms);
        const nextWakeReason = firstNonEmptyText(
            typeof agentState?.next_wake_reason === "string"
                ? agentState.next_wake_reason
                : "",
            typeof directives?.next_check_reason === "string"
                ? directives.next_check_reason
                : "",
        );
        const callType = normalizeCallType(latestThinking?.call_type);
        const tone = modeToneFromCallType(callType, nextWakeMs);
        const modeLabel = modeLabelFromCallType(callType, tone);
        const lastCallMs = thinkingTimestampMs(latestThinking);
        const lastCallMinutes =
            lastCallMs !== null
                ? Math.max(0, Math.round((Date.now() - lastCallMs) / 60000))
                : null;
        const focusLine = truncateText(
            firstNonEmptyText(
                latestThinking?.output_snippet,
                mentalModel?.current_narrative,
                mentalModel?.what_watching,
                mentalModel?.what_changed,
                typeof agentState?.inner_thought === "string"
                    ? agentState.inner_thought
                    : "",
                typeof directives?.inner_thought === "string"
                    ? directives.inner_thought
                    : "",
                latestThinking?.trigger,
            ),
            176,
        );

        const title =
            tone === "home"
                ? "Арчі в casual presence"
                : tone === "work"
                  ? "Арчі в analyst contour"
                  : tone === "bridge"
                    ? "Арчі на live contact між режимами"
                    : "Арчі тримає тишу між wake-подіями";
        const detail =
            focusLine ||
            (tone === "home"
                ? "Контакт зараз ближчий до друга: Archi не заводить важкий briefing без реального приводу, але лишається поруч."
                : tone === "work"
                  ? "Контур зібраний для операторського читання: shell чекає не small talk, а структурований brief або перевірку сценарію."
                  : tone === "bridge"
                    ? "Останній signal говорить лише про живий контакт або recovery: shell не вдає, ніби вже точно знає casual чи analyst режим без додаткового підтвердження."
                    : "Тиша тут не порожнеча: Archi лишається на фоні й чекає осмислений trigger замість тривожних фальстартів.");

        const meta: string[] = [];
        if (lastCallMinutes !== null) {
            meta.push(`last call ${relativeMinutes(lastCallMinutes)}`);
        } else if (lastThinkingSyncMs > 0) {
            meta.push("call journal ще тихий");
        }
        if (nextWakeMs) {
            meta.push(countdownLabel(nextWakeMs));
        } else if (observationEnabled && tone !== "home") {
            meta.push("wake без таймера");
        }

        const modelLabel = currentModeModel(latestThinking, agentState);
        if (
            typeof directives?.focus_symbol === "string" &&
            directives.focus_symbol
        ) {
            meta.push(directives.focus_symbol);
        }
        if (directives?.economy_mode_active) meta.push("economy mode");

        const warnings = [
            directives?.kill_switch_active
                ? "Kill switch активний — Archi лишається в розмові, але торгова ескалація може бути навмисно стриманою."
                : "",
            ...syncWarnings,
        ].filter(Boolean);

        return {
            tone,
            title,
            detail,
            reason: hearthReasonCopy(
                callType,
                nextWakeReason,
                nextWakeMs,
                observationEnabled,
            ),
            modeLabel,
            modelLabel,
            meta,
            actions: hearthActions(tone),
            warning: warnings.join(" · "),
        } satisfies ModeHearth;
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

<!-- ── Context Rail: scrollable on mobile so input is always reachable ── -->
<div class="chat-context-rail">

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

{#if modeHearth}
    <div class="mode-hearth" data-tone={modeHearth.tone}>
        <div class="mode-hearth-head">
            <div>
                <div class="mode-hearth-kicker">Dual Mode</div>
                <div class="mode-hearth-title">{modeHearth.title}</div>
            </div>

            <div class="mode-hearth-badges">
                <span class="mode-hearth-badge emphasis">
                    {modeHearth.modeLabel}
                </span>
                {#if modeHearth.modelLabel}
                    <span class="mode-hearth-badge">
                        {modeHearth.modelLabel}
                    </span>
                {/if}
            </div>
        </div>

        <div class="mode-hearth-copy">{modeHearth.detail}</div>
        <div class="mode-hearth-reason">{modeHearth.reason}</div>

        {#if modeHearth.meta.length > 0}
            <div class="mode-hearth-meta">
                {#each modeHearth.meta as item}
                    <span class="mode-hearth-pill">{item}</span>
                {/each}
            </div>
        {/if}

        {#if modeHearth.actions.length > 0}
            <div class="mode-hearth-actions">
                {#each modeHearth.actions as action}
                    <button
                        class="mode-hearth-btn"
                        class:secondary={action.subtle}
                        onclick={() => primeDraft(action.prompt)}
                    >
                        {action.label}
                    </button>
                {/each}
            </div>
        {/if}

        {#if modeHearth.warning}
            <div class="mode-hearth-warning">{modeHearth.warning}</div>
        {/if}
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

</div><!-- /chat-context-rail -->

<!-- ── Messages Area (ADR-0052 S3) ── -->
<MessageList
    bind:this={messageListRef}
    {messages}
    {loading}
    {awaitingReply}
    innerThought={directives?.inner_thought ?? ""}
    {ttsSupported}
    onspeak={(text) => speak(text)}
/>

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

    /* ── Mode hearth ── */
    .mode-hearth {
        --hearth-accent: rgba(246, 168, 74, 0.28);
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 14px 16px 16px;
        border-bottom: 1px solid var(--border);
        background: radial-gradient(
                circle at top left,
                rgba(246, 168, 74, 0.16),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(92, 205, 180, 0.1),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
        flex-shrink: 0;
    }
    .mode-hearth[data-tone="work"] {
        --hearth-accent: rgba(120, 164, 255, 0.3);
        background: radial-gradient(
                circle at top left,
                rgba(120, 164, 255, 0.16),
                transparent 36%
            ),
            radial-gradient(
                circle at right center,
                rgba(92, 205, 180, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="bridge"] {
        --hearth-accent: rgba(150, 132, 255, 0.28);
        background: radial-gradient(
                circle at top left,
                rgba(150, 132, 255, 0.16),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(246, 168, 74, 0.1),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="quiet"] {
        --hearth-accent: rgba(92, 205, 180, 0.22);
        background: radial-gradient(
                circle at top left,
                rgba(92, 205, 180, 0.12),
                transparent 32%
            ),
            radial-gradient(
                circle at right center,
                rgba(120, 164, 255, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="degraded"] {
        --hearth-accent: rgba(239, 95, 71, 0.3);
        background: radial-gradient(
                circle at top left,
                rgba(239, 95, 71, 0.14),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(246, 168, 74, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
    }
    .mode-hearth-kicker {
        font-size: 10px;
        color: #f6a84a;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .mode-hearth-title {
        font-size: 15px;
        font-weight: 650;
        color: var(--text);
        line-height: 1.35;
    }
    .mode-hearth-badges {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-badge {
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--border) 84%, transparent);
        background: color-mix(in srgb, var(--surface2) 90%, var(--bg));
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }
    .mode-hearth-badge.emphasis {
        border-color: var(--hearth-accent);
        color: var(--text);
    }
    .mode-hearth-copy {
        font-size: 13px;
        line-height: 1.6;
        color: var(--text);
        max-width: 760px;
    }
    .mode-hearth-reason {
        font-size: 12px;
        line-height: 1.55;
        color: var(--text-muted);
        max-width: 760px;
    }
    .mode-hearth-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-pill {
        padding: 5px 9px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--surface2) 90%, var(--bg));
        border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 600;
    }
    .mode-hearth-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-btn {
        padding: 7px 11px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
        background: color-mix(in srgb, var(--accent) 11%, var(--surface2));
        color: var(--text);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition:
            border-color 0.15s,
            background 0.15s,
            transform 0.15s;
    }
    .mode-hearth-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 44%, transparent);
        background: color-mix(in srgb, var(--accent) 17%, var(--surface2));
        transform: translateY(-1px);
    }
    .mode-hearth-btn.secondary {
        border-color: color-mix(in srgb, var(--border) 82%, transparent);
        background: transparent;
        color: var(--text-muted);
    }
    .mode-hearth-btn.secondary:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--border) 66%, transparent);
        background: color-mix(in srgb, var(--surface2) 92%, var(--bg));
    }
    .mode-hearth-warning {
        padding: 9px 11px;
        border-radius: 12px;
        border: 1px solid rgba(246, 168, 74, 0.2);
        background: rgba(246, 168, 74, 0.08);
        color: var(--text);
        font-size: 11px;
        line-height: 1.45;
    }
    @media (max-width: 720px) {
        .mode-hearth-head {
            flex-direction: column;
        }
        .mode-hearth-badges {
            justify-content: flex-start;
        }
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

    /* ── Context Rail (cards above messages) ── */
    .chat-context-rail {
        flex-shrink: 1;
        overflow-y: auto;
        overflow-x: hidden;
        overscroll-behavior-y: contain;
    }

    /* ── Messages area & bubbles: owned by MessageList/MessageBubble (ADR-0052 S3) ── */

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
        .emoji-panel {
            width: 260px;
            right: -12px;
        }
        .chat-context-rail {
            max-height: 40vh;
            /* Fade hint at bottom when scrollable */
            mask-image: linear-gradient(to bottom, black calc(100% - 16px), transparent 100%);
            -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 16px), transparent 100%);
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
