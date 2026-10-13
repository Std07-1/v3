// src/chart/interaction/geometry.test.ts — поріг перетягування фігур (ADR-0111, 13.10).
import { describe, expect, it } from 'vitest';
import { DRAG_START_PX_MOUSE, DRAG_START_PX_TOUCH, dragStarted } from './geometry';

describe('dragStarted', () => {
  it('миша: тремтіння на 1–2 px — це клік, від 3 px — перетягування', () => {
    expect(dragStarted(1, 0, 'mouse')).toBe(false);
    expect(dragStarted(1, 1, 'mouse')).toBe(false);
    expect(dragStarted(2, 2, 'mouse')).toBe(false); // ≈2.83 px
    expect(dragStarted(DRAG_START_PX_MOUSE, 0, 'mouse')).toBe(true);
    expect(dragStarted(0, -4, 'mouse')).toBe(true);
  });

  it('палець і перо — поріг більший (8 px), як у довгого дотику', () => {
    expect(dragStarted(6, 0, 'touch')).toBe(false);
    expect(dragStarted(5, 5, 'pen')).toBe(false); // ≈7.07 px
    expect(dragStarted(0, DRAG_START_PX_TOUCH, 'touch')).toBe(true);
  });

  it('невідомий тип вказівника — як миша', () => {
    expect(dragStarted(3, 0, '')).toBe(true);
    expect(dragStarted(2, 0, '')).toBe(false);
  });
});
