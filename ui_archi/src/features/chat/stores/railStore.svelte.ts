/**
 * railStore — Shell rail state: thinking signal snapshot + pinned-thought visibility timer.
 *
 * Invariants:
 *   - latestThinking — останній ThinkingEntry[0] з /thinking API (pre-S5: Chat-local poll)
 *   - lastThinkingSyncMs — ts успішного оновлення; 0 до першого fetch
 *   - thinkingSyncError — degraded-but-loud текст, який Mode Hearth показує як warning
 *   - contextVisible — керує .faded клоном на pinned-thought (5s idle → fade)
 *
 * Degraded-but-loud (I7):
 *   - fetch fail НЕ кидає exception; thinkingSyncError виставляється з поясненням
 *   - poll interval 30s (узгоджено з попередньою Chat.svelte поведінкою)
 *
 * Lifecycle:
 *   - init() — виконує initial load + стартує 30s poll. Викликати у Chat.svelte onMount.
 *   - shutdown() — зупиняє poll + очищує context timer. onDestroy.
 *   - resetContextTimer() — викликати на user interaction (наразі тільки на init).
 *
 * Consumers: Chat.svelte (shell-layer rail), ModeHearth derive logic через hearthHelpers.
 */
import type { ThinkingEntry } from "../../../lib/types";
import { api } from "../../../lib/api";
import { refreshAll } from "../../../lib/state.svelte";

const POLL_MS = 30_000;
const CONTEXT_IDLE_MS = 5_000;

class RailStore {
    latestThinking = $state<ThinkingEntry | null>(null);
    lastThinkingSyncMs = $state(0);
    thinkingSyncError = $state("");
    contextVisible = $state(true);

    private pollId: ReturnType<typeof setInterval> | null = null;
    private contextTimer: ReturnType<typeof setTimeout> | null = null;

    /** Initial load + старт 30s poll. Shell-level — викликати у Chat.svelte onMount. */
    async init(): Promise<void> {
        await this.loadShellData();
        this.resetContextTimer();
        this.pollId = setInterval(() => {
            void this.refreshShellData();
        }, POLL_MS);
    }

    /** Зупинити polling + очистити timer. Викликати у onDestroy. */
    shutdown(): void {
        if (this.pollId) {
            clearInterval(this.pollId);
            this.pollId = null;
        }
        if (this.contextTimer) {
            clearTimeout(this.contextTimer);
            this.contextTimer = null;
        }
    }

    /** Показати pinned-thought знов + запустити 5s fade timer. */
    resetContextTimer(): void {
        this.contextVisible = true;
        if (this.contextTimer) clearTimeout(this.contextTimer);
        this.contextTimer = setTimeout(() => {
            this.contextVisible = false;
            this.contextTimer = null;
        }, CONTEXT_IDLE_MS);
    }

    private async loadLatestThinking(): Promise<{
        ok: boolean;
        entry: ThinkingEntry | null;
    }> {
        try {
            const result = await api.thinking(1, 0);
            return { ok: true, entry: result.entries?.[0] ?? null };
        } catch {
            return { ok: false, entry: null };
        }
    }

    private recordSync(
        entry: ThinkingEntry | null,
        ok: boolean,
        phase: "initial" | "refresh",
    ): void {
        if (ok) {
            this.latestThinking = entry;
            this.lastThinkingSyncMs = Date.now();
            this.thinkingSyncError = "";
            return;
        }
        this.thinkingSyncError =
            phase === "initial"
                ? "Thinking journal snapshot не завантажився. Mode Hearth читає режим без last-call signal."
                : "Thinking journal snapshot не оновився. Mode Hearth може спиратись на попередній call signal.";
    }

    private async loadShellData(): Promise<void> {
        try {
            const [, next] = await Promise.all([
                refreshAll(false),
                this.loadLatestThinking(),
            ]);
            this.recordSync(next.entry, next.ok, "initial");
        } catch {
            this.thinkingSyncError =
                "Thinking journal snapshot не завантажився. Початковий shell sync не вдався.";
        }
    }

    private async refreshShellData(): Promise<void> {
        try {
            const [, next] = await Promise.all([
                refreshAll(false),
                this.loadLatestThinking(),
            ]);
            this.recordSync(next.entry, next.ok, "refresh");
        } catch {
            this.thinkingSyncError =
                "Thinking journal snapshot не оновився. Показано останній успішний call signal.";
        }
    }
}

/** Singleton — один rail-стан на вкладку. */
export const railStore = new RailStore();
