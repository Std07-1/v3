// src/stores/viewCache.ts
// ADR-0032 P4: Time-center preserving TF switch.
// Зберігає center_ms (center timestamp) + bars_visible (zoom level)
// замість LogicalRange (bar indices), що дозволяє стабільне cross-TF switching.
// SSOT: цей файл — єдине місце збереження visible range.

export interface ViewSnapshot {
    center_ms: number;
    bars_visible: number;
}

const MAX_ENTRIES = 20; // LRU cap — достатньо для всіх symbol×TF комбінацій

const cache = new Map<string, ViewSnapshot>();

function makeKey(symbol: string, tf: string): string {
    return `${symbol}|${tf}`;
}

/**
 * Зберегти поточний view snapshot для symbol+tf.
 * Викликається перед switch (full frame).
 */
export function saveViewSnapshot(
    symbol: string,
    tf: string,
    snapshot: ViewSnapshot,
): void {
    const key = makeKey(symbol, tf);
    // Delete+set = move to end (LRU: most recently used at the end)
    cache.delete(key);
    cache.set(key, snapshot);
    // LRU eviction
    if (cache.size > MAX_ENTRIES) {
        const oldest = cache.keys().next().value;
        if (oldest != null) cache.delete(oldest);
    }
}

/**
 * Відновити view snapshot для symbol+tf.
 * Повертає null якщо немає збереженого snapshot.
 */
export function loadViewSnapshot(
    symbol: string,
    tf: string,
): ViewSnapshot | null {
    return cache.get(makeKey(symbol, tf)) ?? null;
}

/**
 * Очистити весь кеш (наприклад при reconnect/reload).
 */
export function clearViewCache(): void {
    cache.clear();
}
