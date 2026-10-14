// src/chart/drawings/figureTouchGuard.test.ts — ADR-0112 D8: поки палець тягне фігуру, дотики не йдуть далі контейнера
// (замок перехрестя 🔒 і LWC слухають нижче); без перетягування — проходять як завжди.
import { describe, expect, it } from 'vitest';
import { guardFigureTouches } from './figureTouchGuard';

/** Контейнер графіка: guard у capture-фазі, «нижче» — слухач, що імітує longPressLock / LWC. */
function setup(owns: { value: boolean }) {
  const container = new EventTarget() as unknown as HTMLElement;
  const cleanup = guardFigureTouches(container, () => owns.value);
  const seen: string[] = [];
  for (const type of ['touchstart', 'touchmove', 'touchend', 'touchcancel']) {
    container.addEventListener(type, (e) => seen.push(e.type), { capture: true });
  }
  const fire = (type: string) => {
    const e = new Event(type, { cancelable: true });
    container.dispatchEvent(e);
    return e.defaultPrevented;
  };
  return { seen, fire, cleanup };
}

describe('guardFigureTouches', () => {
  it('палець тягне фігуру — touchstart/move/end не доходять далі, рух не прокручує сторінку', () => {
    const { seen, fire } = setup({ value: true });
    expect(['touchstart', 'touchmove', 'touchend', 'touchcancel'].map(fire)).toEqual([true, true, true, true]);
    expect(seen).toEqual([]);
  });

  it('без перетягування — дотики проходять (прокрутка, довгий дотик ADR-0108 як були)', () => {
    const { seen, fire } = setup({ value: false });
    expect(fire('touchstart')).toBe(false);
    expect(fire('touchmove')).toBe(false);
    expect(seen).toEqual(['touchstart', 'touchmove']);
  });

  it('власність перевіряється на кожну подію: відпустив фігуру — далі проходить', () => {
    const owns = { value: true };
    const { seen, fire } = setup(owns);
    fire('touchmove');
    owns.value = false;
    fire('touchend');
    expect(seen).toEqual(['touchend']);
  });

  it('зняття — слухачів нема', () => {
    const { seen, fire, cleanup } = setup({ value: true });
    cleanup();
    fire('touchmove');
    expect(seen).toEqual(['touchmove']);
  });
});
