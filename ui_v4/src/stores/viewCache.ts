// src/stores/viewCache.ts
// P3.9: Visible range cache per (symbol, tf) key.
// V3 parity: app.js:67-69 uiCacheByKey, cacheLru.
//
// При switch symbol/TF — зберігає scroll position.
// При поверненні — відновлює, щоб графік не "стрибав".
// SSOT: цей файл — єдине місце збереження visible range.

import type { LogicalRange } from 'lightweight-charts';

const MAX_ENTRIES = 20; // LRU cap — достатньо для всіх symbol×TF комбінацій

const cache = new Map<string, LogicalRange>();

function makeKey(symbol: string, tf: string): string {
    return `${symbol}|${tf}`;
}

/**
 * Зберегти поточний visible range для symbol+tf.
 * Викликається перед switch (full frame).
 */
export function saveVisibleRange(
    symbol: string,
    tf: string,
    range: LogicalRange | null,
): void {
    if (!range) return;
    const key = makeKey(symbol, tf);
    // Delete+set = move to end (LRU: most recently used at the end)
    cache.delete(key);
    cache.set(key, range);
    // LRU eviction
    if (cache.size > MAX_ENTRIES) {
        const oldest = cache.keys().next().value;
        if (oldest != null) cache.delete(oldest);
    }
}

/**
 * Відновити visible range для symbol+tf.
 * Повертає null якщо немає збереженого range.
 */
export function loadVisibleRange(
    symbol: string,
    tf: string,
): LogicalRange | null {
    return cache.get(makeKey(symbol, tf)) ?? null;
}

/**
 * Очистити весь кеш (наприклад при reconnect/reload).
 */
export function clearViewCache(): void {
    cache.clear();
}
