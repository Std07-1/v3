// src/stores/favorites.ts
// P3.13: Favorites — localStorage-based sym/TF pair quick-access.
// SSOT: v4_favorites ключ у localStorage.

import { writable, get } from 'svelte/store';

const LS_KEY = 'v4_favorites';

/** Обраний пара sym+tf */
export interface FavPair {
    symbol: string;
    tf: string;
}

function loadFavorites(): FavPair[] {
    try {
        const raw = localStorage.getItem(LS_KEY);
        if (raw) {
            const arr = JSON.parse(raw);
            if (Array.isArray(arr)) return arr.filter(f => f.symbol && f.tf);
        }
    } catch { /* noop */ }
    return [];
}

function saveFavorites(favs: FavPair[]): void {
    try { localStorage.setItem(LS_KEY, JSON.stringify(favs)); } catch { /* noop */ }
}

function createFavoritesStore() {
    const store = writable<FavPair[]>(loadFavorites());
    const { subscribe, update } = store;

    return {
        subscribe,

        /** Додати/видалити пару з обраних (toggle) */
        toggle(symbol: string, tf: string): void {
            update(favs => {
                const idx = favs.findIndex(f => f.symbol === symbol && f.tf === tf);
                let next: FavPair[];
                if (idx >= 0) {
                    next = [...favs.slice(0, idx), ...favs.slice(idx + 1)];
                } else {
                    next = [...favs, { symbol, tf }].slice(0, 20); // макс 20 обраних
                }
                saveFavorites(next);
                return next;
            });
        },

        /** Чи є пара в обраних */
        has(symbol: string, tf: string): boolean {
            return get(store).some(f => f.symbol === symbol && f.tf === tf);
        },

        /** Отримати поточний знімок */
        snapshot(): FavPair[] {
            return get(store);
        },
    };
}

export const favoritesStore = createFavoritesStore();
