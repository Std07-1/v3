/**
 * createLazyList — windowing для довгих списків.
 *
 * ГОРН-ефективність: у DOM тримаємо лише `count` видимих елементів, докладаємо
 * `step` коли користувач догортав до низу (sentinel + use:onVisible). Це сильніше
 * за `content-visibility:auto` — Svelte взагалі не створює компоненти поза вікном,
 * а не лише пропускає їхній paint.
 *
 * Кожен виклик = власний реактивний `count` (per-view). Чистий factory, нуль DOM.
 *
 * Патерн застосування у view:
 *   const lazy = createLazyList<T>({ initial: 20, step: 20 });
 *   $effect(() => { void filterA; void filterB; lazy.reset(); }); // фільтр змінився → з початку
 *   const visible = $derived(lazy.slice(filteredItems));
 *   {#each visible as item (item.id)} … {/each}
 *   {#if lazy.hasMore(filteredItems.length)}
 *     <div use:onVisible={() => lazy.more(filteredItems.length)}></div>
 *   {/if}
 */
export function createLazyList<T>(opts: { initial?: number; step?: number } = {}) {
    const initial = opts.initial ?? 20;
    const step = opts.step ?? 20;
    let count = $state(initial);

    return {
        /** Видимий зріз масиву (рендеримо тільки його). */
        slice(items: readonly T[]): T[] {
            return items.slice(0, count) as T[];
        },
        /** Чи лишилось що докладати. */
        hasMore(total: number): boolean {
            return count < total;
        },
        /** Догорнули до низу → докласти ще `step` (не більше за total). */
        more(total: number): void {
            if (count < total) count = Math.min(count + step, total);
        },
        /** Зміна фільтра/набору даних → почати знову з `initial`. */
        reset(): void {
            count = initial;
        },
    };
}
