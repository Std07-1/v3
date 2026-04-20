/**
 * reactionsStore — hover-reactions на chat bubbles (ADR-0053 S1).
 *
 * Локальний UX layer: зберігає {msg_id → Set<reaction>} у localStorage одразу.
 * S4 (окремий slice) піде з backend: `POST /api/archi/chat/react` → Redis XADD
 * `feedback:chat` stream. Поки цього немає — reaction живе у браузері, бот
 * нічого не бачить. Це свідома тимчасова деградація (I7 loud — no-op, не
 * silent-drop).
 */

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
        const next = current.includes(type)
            ? current.filter((t) => t !== type)
            : [...current, type];
        if (next.length === 0) {
            const { [msgId]: _, ...rest } = this.state;
            this.state = rest;
        } else {
            this.state = { ...this.state, [msgId]: next };
        }
        persist(this.state);
    }
}

export const reactionsStore = new ReactionsStore();
