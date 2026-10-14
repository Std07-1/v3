// src/chart/drawings/figureTouchGuard.ts
// ADR-0112 D8: поки палець тягне фігуру, його дотикові події (touch*) не йдуть далі контейнера графіка. У пальця два
// окремі потоки подій: pointer-потік забирає DrawingsRenderer (preventDefault/stopPropagation), а touch-потік без
// цього доходив до замка перехрестя (longPressLock 🔒: 300 мс утримання → режим перехрестя, що лишався й після
// відпускання — наступний дотик вів перехрестя замість фігури) і до LWC. Слухач — у capture-фазі на `.chart-container`
// (предок longPressLock і LWC), як курсор малювання (touchDrawCursor.ts); longPressLock.ts не змінюється.

const TOUCH_TYPES = ['touchstart', 'touchmove', 'touchend', 'touchcancel'] as const;

/** Глушить дотики контейнера, поки `ownsTouch()` (renderer тягне фігуру пальцем). Повертає зняття слухачів. */
export function guardFigureTouches(container: HTMLElement, ownsTouch: () => boolean): () => void {
  const swallow = (e: Event): void => {
    if (!ownsTouch()) return;
    if (e.cancelable) e.preventDefault();
    e.stopImmediatePropagation();
  };
  const opts: AddEventListenerOptions = { capture: true, passive: false };
  for (const type of TOUCH_TYPES) container.addEventListener(type, swallow, opts);
  return () => {
    for (const type of TOUCH_TYPES) container.removeEventListener(type, swallow, opts);
  };
}
