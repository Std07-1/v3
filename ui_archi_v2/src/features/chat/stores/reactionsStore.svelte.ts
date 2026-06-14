/**
 * reactionsStore — hover-reactions на chat bubbles (ADR-0053 S1 + S4).
 *
 * Dual-write архітектура:
 *   1) localStorage = SSOT для UI (instant toggle, survive reload, per-browser)
 *   2) POST /api/archi/chat/react → XADD feedback:chat (training signal для Арчі)
 *
 * API виклик — fire-and-forget: UX не блокується мережею і НЕ rollback-ить
 * оптимістичний state при fail (I7 loud через console.warn, не через flicker).
 * Бот консумить stream коли хоче; localStorage переживає і fail, і offline.
 */
import { api } from "../../../lib/api";

const STORAGE_KEY = "archi_chat_reactions_v1";

export type ReactionType = "like" | "pin" | "star";

type ReactionMap = Record<string, ReactionType[]>;

function loadAll(): ReactionMap {
    if (typeof localStorage === "undefined") return {};
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") return parsed as ReactionMap;
    } catch {
        /* degraded-but-loud: corrupted localStorage → reset silently, next save overwrites */
    }
    return {};
}

function persist(map: ReactionMap): void {
    if (typeof localStorage === "undefined") return;
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
    } catch {
        /* quota exceeded → accept loss, don't crash UI */
    }
}

class ReactionsStore {
    private state = $state<ReactionMap>(loadAll());

    has(msgId: string, type: ReactionType): boolean {
        return this.state[msgId]?.includes(type) ?? false;
    }

    list(msgId: string): ReactionType[] {
        return this.state[msgId] ?? [];
    }

    toggle(msgId: string, type: ReactionType): void {
        const current = this.state[msgId] ?? [];
        const wasSet = current.includes(type);
        const next = wasSet
            ? current.filter((t) => t !== type)
            : [...current, type];
        if (next.length === 0) {
            const { [msgId]: _, ...rest } = this.state;
            this.state = rest;
        } else {
            this.state = { ...this.state, [msgId]: next };
        }
        persist(this.state);

        // Fire-and-forget publish до feedback:chat stream (ADR-0053 S4).
        // Ні await, ні rollback: localStorage лишається SSOT UI state.
        const action: "add" | "remove" = wasSet ? "remove" : "add";
        void api
            .chatReact(msgId, type, action)
            .catch((err) => {
                // Degraded-but-loud: console warn, без UI-noise.
                // eslint-disable-next-line no-console
                console.warn(
                    `[reactions] publish failed msg=${msgId} type=${type} action=${action}`,
                    err,
                );
            });
    }
}

export const reactionsStore = new ReactionsStore();
