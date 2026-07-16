/**
 * filmStore — кіноплівка пробуджень (/api/archi/wakes).
 *
 * Svelte 5 class-store з $state-полями (як chatStore у ui_archi_v2). Живе:
 *   - init(): перші 30 карток (newest-first) + старт poll
 *   - loadMore(): наступна сторінка старіших через before_ts = oldest_ts курсор
 *   - poll кожні 20s: тягне верхню сторінку, вливає нові картки
 *   - dedupe по wake_id (fallback ts) — poll+loadMore не задвоюють
 *
 * Degraded-but-loud (I5): fetch fail піднімає error (не тихий drop); наступний
 * poll/повтор чистить. X28: жодного домену — лише сортування/дедуп по ключах.
 */

import { api } from '../lib/api';
import type { WakeCard } from '../lib/types';

const PAGE_LIMIT = 30;
const POLL_INTERVAL_MS = 20_000;

/** Стабільний ключ картки: wake_id, або ts коли id порожній (старі рядки). */
function cardKey(card: WakeCard): string {
    return card.wake_id && card.wake_id.length > 0 ? card.wake_id : `ts:${card.ts}`;
}

class FilmStore {
    cards = $state<WakeCard[]>([]);
    loading = $state(true);
    loadingMore = $state(false);
    error = $state('');
    /** ще є старіші картки для «Показати ще». */
    hasMore = $state(true);

    private total = 0;
    private pollTimer: ReturnType<typeof setInterval> | null = null;

    /** Перша сторінка + старт полінгу верхівки. Викликати у onMount. */
    async init(): Promise<void> {
        this.loading = true;
        try {
            const res = await api.wakes(PAGE_LIMIT);
            this.cards = this.sortDesc(res.wakes);
            this.total = res.total;
            this.hasMore = this.cards.length < res.total;
            this.error = '';
        } catch (err) {
            this.error = err instanceof Error ? err.message : 'wakes_fetch_failed';
        } finally {
            this.loading = false;
        }
        this.startPoll();
    }

    /** Зупинити polling. Викликати у onDestroy. */
    shutdown(): void {
        if (this.pollTimer !== null) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
    }

    /** Наступна сторінка старіших (keyset before_ts = ts найстарішої наявної). */
    async loadMore(): Promise<void> {
        if (this.loadingMore || !this.hasMore || this.cards.length === 0) return;
        this.loadingMore = true;
        const oldestTs = this.cards[this.cards.length - 1].ts;
        try {
            const res = await api.wakes(PAGE_LIMIT, oldestTs);
            this.merge(res.wakes);
            this.total = res.total;
            // Порожня сторінка або дійшли total → більше немає.
            this.hasMore = res.wakes.length > 0 && this.cards.length < res.total;
            this.error = '';
        } catch (err) {
            this.error = err instanceof Error ? err.message : 'wakes_more_failed';
        } finally {
            this.loadingMore = false;
        }
    }

    private startPoll(): void {
        this.shutdown();
        this.pollTimer = setInterval(() => void this.pollNewer(), POLL_INTERVAL_MS);
    }

    /** Тягне верхню сторінку, вливає нові картки (dedupe). */
    private async pollNewer(): Promise<void> {
        try {
            const res = await api.wakes(PAGE_LIMIT);
            this.merge(res.wakes);
            this.total = Math.max(this.total, res.total);
            this.error = '';
        } catch {
            // Фонова операція — loudness у наступному poll (throttle-friendly).
        }
    }

    /** Влити картки в наявний набір: dedupe по ключу, re-sort newest-first. */
    private merge(incoming: WakeCard[]): void {
        if (incoming.length === 0) return;
        const byKey = new Map<string, WakeCard>();
        for (const card of this.cards) byKey.set(cardKey(card), card);
        for (const card of incoming) byKey.set(cardKey(card), card);
        this.cards = this.sortDesc([...byKey.values()]);
    }

    private sortDesc(cards: WakeCard[]): WakeCard[] {
        return [...cards].sort((a, b) => b.ts - a.ts);
    }
}

export const filmStore = new FilmStore();
