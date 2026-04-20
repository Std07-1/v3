/**
 * chatStore — Стан активного діалогу з Arхі.
 *
 * Invariants:
 *   - messages[] відсортовано за ts_ms asc
 *   - sending=true блокує повторний submit у InputBar
 *   - error=null означає clean state
 *   - messagesVersion інкрементується на кожну мутацію messages (для $effect)
 *
 * Degraded-but-loud (I7):
 *   - Mережевий fail у send() → error set + text повертається у {ok:false, text}
 *     для відновлення draft у Chat.svelte (БЕЗ silent drop)
 *   - Load fail у loadHistory() → error set, messages лишається порожнім
 *   - Poll fail → silent retry (це фонова операція, loudness у наступному poll)
 *
 * Lifecycle:
 *   - init() викликається у Chat.svelte onMount — завантажує історію + стартує poll
 *   - shutdown() у onDestroy — зупиняє всі таймери
 *
 * Consumers: Chat.svelte (композиція через $derived),
 *            пізніше MessageList + InputBar через props (S3/S4).
 */
import type { ChatMessage } from "../../../lib/types";
import * as chatApi from "../api/chatApi";

const NORMAL_POLL_MS = 8_000;
const FAST_POLL_MS = 2_000;
const FAST_POLL_MAX_MS = 90_000;
const HISTORY_LIMIT = 80;

class ChatStore {
    messages = $state<ChatMessage[]>([]);
    sending = $state(false);
    loading = $state(true);
    error = $state("");
    awaitingReply = $state(false);
    lastSentId = $state("");
    /** Інкрементується на кожну мутацію messages — для $effect у Chat.svelte. */
    messagesVersion = $state(0);

    private pollId: ReturnType<typeof setInterval> | null = null;
    private fastPollId: ReturnType<typeof setInterval> | null = null;
    private fastPollStartedAt = 0;
    /** SSE stream для typing-effect (ADR-0053 S3). null = не активний. */
    private streamSource: EventSource | null = null;
    /** id archi bubble, який зараз стрімиться — щоб poll не перетер її текст. */
    private streamingBubbleId: string | null = null;

    /** Завантажити історію + стартувати normal poll. Викликати у onMount. */
    async init(): Promise<void> {
        await this.loadHistory();
        this.startNormalPoll();
    }

    /** Зупинити всі таймери. Викликати у onDestroy. */
    shutdown(): void {
        this.stopNormalPoll();
        this.stopFastPoll();
        this.closeStream();
    }

    async loadHistory(): Promise<void> {
        try {
            const res = await chatApi.loadHistory(HISTORY_LIMIT);
            this.messages = res.messages ?? [];
            this.bumpVersion();
        } catch {
            // loud: banner у UI через error state
            this.error = "Історія чату не завантажилася.";
        } finally {
            this.loading = false;
        }
    }

    /**
     * Надіслати повідомлення з optimistic add + rollback on error.
     * @returns {ok:true} на успіх / {ok:false, text} для restore draft у UI.
     */
    async send(text: string): Promise<{ ok: true } | { ok: false; text: string }> {
        const trimmed = text.trim();
        if (!trimmed || this.sending) return { ok: true };

        this.sending = true;
        this.error = "";

        const tmpMsg: ChatMessage = {
            id: `u_tmp_${Date.now()}`,
            role: "user",
            text: trimmed,
            ts_ms: Date.now(),
        };
        this.messages = [...this.messages, tmpMsg];
        this.bumpVersion();

        try {
            const res = await chatApi.sendMessage(trimmed);
            this.messages = this.messages.map((m) =>
                m.id === tmpMsg.id ? res.message : m,
            );
            this.lastSentId = res.message.id;
            this.awaitingReply = true;
            this.bumpVersion();
            // SSE-first (ADR-0053 S3): сервер паcить final reply у typing-effect.
            // Якщо EventSource недоступне / падає — openStream самостійно запускає
            // fast-poll як fallback (I7 degraded-but-loud).
            this.openStream(res.message.id);
            return { ok: true };
        } catch {
            this.error = "Повідомлення не відправлено. Спробуй ще раз.";
            this.messages = this.messages.filter((m) => m.id !== tmpMsg.id);
            this.bumpVersion();
            return { ok: false, text: trimmed };
        } finally {
            this.sending = false;
        }
    }

    private async pollMessages(): Promise<void> {
        try {
            const result = await chatApi.loadHistory(HISTORY_LIMIT);
            const newMsgs = result.messages ?? [];

            if (this.awaitingReply && this.lastSentId) {
                const sentTs =
                    this.messages.find((x) => x.id === this.lastSentId)?.ts_ms ?? 0;
                const hasReply = newMsgs.some(
                    (m) => m.role === "archi" && m.ts_ms > sentTs,
                );
                if (hasReply) {
                    this.awaitingReply = false;
                    this.lastSentId = "";
                    this.stopFastPoll();
                }
            }

            const lastNew = newMsgs[newMsgs.length - 1]?.id;
            const lastCur = this.messages[this.messages.length - 1]?.id;
            const changed =
                newMsgs.length !== this.messages.length || lastNew !== lastCur;
            if (changed) {
                // Якщо зараз стрімиться bubble — не перетираємо його частково
                // заповнений text фінальним знімком з poll. Merge: беремо newMsgs,
                // але для streaming id залишаємо локальну версію.
                if (this.streamingBubbleId) {
                    const streamingId = this.streamingBubbleId;
                    const local = this.messages.find((m) => m.id === streamingId);
                    if (local) {
                        this.messages = newMsgs.map((m) =>
                            m.id === streamingId ? local : m,
                        );
                    } else {
                        this.messages = newMsgs;
                    }
                } else {
                    this.messages = newMsgs;
                }
                this.bumpVersion();
            }
        } catch {
            // silent retry — loudness через відсутність оновлень, не через error state
        }
    }

    // ── SSE typing-effect stream (ADR-0053 S3) ──────────────────────────────
    private openStream(afterId: string): void {
        this.closeStream();
        const es = chatApi.openChatStream(afterId, 180);
        if (!es) {
            // Browser/test env без EventSource — одразу падаємо у fast-poll.
            this.startFastPoll();
            return;
        }
        this.streamSource = es;

        const onStart = (ev: MessageEvent) => {
            try {
                const d = JSON.parse(ev.data);
                const id = String(d.id ?? "");
                const ts = Number(d.ts_ms ?? Date.now());
                if (!id) return;
                // Guard проти дублю, якщо poll вже додав фінальне повідомлення.
                if (this.messages.some((m) => m.id === id)) {
                    this.messages = this.messages.map((m) =>
                        m.id === id ? { ...m, text: "", streaming: true } : m,
                    );
                } else {
                    this.messages = [
                        ...this.messages,
                        { id, role: "archi", text: "", ts_ms: ts, streaming: true },
                    ];
                }
                this.streamingBubbleId = id;
                this.bumpVersion();
            } catch {
                // malformed frame — ігноруємо, fallback на poll
            }
        };

        const onDelta = (ev: MessageEvent) => {
            if (!this.streamingBubbleId) return;
            try {
                const d = JSON.parse(ev.data);
                const chunk = String(d.text ?? "");
                if (!chunk) return;
                const id = this.streamingBubbleId;
                this.messages = this.messages.map((m) =>
                    m.id === id ? { ...m, text: m.text + chunk } : m,
                );
                this.bumpVersion();
            } catch {
                // malformed delta — skip
            }
        };

        const onDone = () => {
            if (this.streamingBubbleId) {
                const id = this.streamingBubbleId;
                this.messages = this.messages.map((m) =>
                    m.id === id ? { ...m, streaming: false } : m,
                );
                this.bumpVersion();
            }
            this.streamingBubbleId = null;
            this.awaitingReply = false;
            this.lastSentId = "";
            this.closeStream();
            this.stopFastPoll();
        };

        const onFallback = () => {
            // Викидаємо частково заповнений bubble — fast-poll принесе цілий.
            if (this.streamingBubbleId) {
                const id = this.streamingBubbleId;
                this.messages = this.messages.filter((m) => m.id !== id);
                this.bumpVersion();
            }
            this.streamingBubbleId = null;
            this.closeStream();
            this.startFastPoll();
        };

        es.addEventListener("start", onStart as EventListener);
        es.addEventListener("delta", onDelta as EventListener);
        es.addEventListener("done", onDone as EventListener);
        es.addEventListener("timeout", onFallback as EventListener);
        // Named "error" event з сервера + generic onerror (network drop / 5xx).
        es.addEventListener("error", onFallback as EventListener);
        es.onerror = onFallback;
    }

    private closeStream(): void {
        if (this.streamSource) {
            try {
                this.streamSource.close();
            } catch {
                // noop
            }
            this.streamSource = null;
        }
    }

    private startNormalPoll(): void {
        if (this.pollId) return;
        this.pollId = setInterval(() => this.pollMessages(), NORMAL_POLL_MS);
    }

    private stopNormalPoll(): void {
        if (this.pollId) {
            clearInterval(this.pollId);
            this.pollId = null;
        }
    }

    private startFastPoll(): void {
        if (this.fastPollId) return;
        this.fastPollStartedAt = Date.now();
        this.fastPollId = setInterval(() => {
            if (Date.now() - this.fastPollStartedAt > FAST_POLL_MAX_MS) {
                this.stopFastPoll();
                this.awaitingReply = false;
                this.lastSentId = "";
                return;
            }
            void this.pollMessages();
        }, FAST_POLL_MS);
    }

    private stopFastPoll(): void {
        if (this.fastPollId) {
            clearInterval(this.fastPollId);
            this.fastPollId = null;
        }
    }

    private bumpVersion(): void {
        this.messagesVersion = (this.messagesVersion + 1) | 0;
    }
}

/** Singleton — один чат на вкладку. */
export const chatStore = new ChatStore();
