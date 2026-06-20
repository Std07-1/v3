/**
 * use:onScrollEnd — кличе callback коли скрол-контейнер догорнули близько до низу.
 *
 * Для infinite-scroll довгих списків: вішаємо на сам контейнер; він докладає
 * наступну порцію за THRESHOLD px до кінця. Нативний scroll + 3 читання = дешево
 * й надійно, на відміну від IntersectionObserver (вимагає реального compositing,
 * не спрацьовує у headless-прев'ю → неможливо верифікувати).
 */
export function onScrollEnd(node: HTMLElement, callback: () => void) {
    let cb = callback;
    const THRESHOLD = 400; // px до низу → докладаємо заздалегідь, щоб не було видно «дна»
    function check(): void {
        if (node.scrollHeight - node.scrollTop - node.clientHeight < THRESHOLD) cb();
    }
    node.addEventListener("scroll", check, { passive: true });
    return {
        update(next: () => void) {
            cb = next;
        },
        destroy() {
            node.removeEventListener("scroll", check);
        },
    };
}
