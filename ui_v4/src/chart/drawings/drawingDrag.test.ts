// src/chart/drawings/drawingDrag.test.ts — перетягування ручки й тіла (винесено з DrawingsRenderer, 13.10).
import { describe, expect, it } from 'vitest';
import { dragBody, dragHandle, type DragSpace } from './drawingDrag';
import type { Drawing } from '../../types';

const H1 = 3_600_000;
// Простір: бар H1 = 10 px (logical = floor(x / 10)), ціна: y = 5000 − price.
function space(overrides: Partial<DragSpace> = {}): DragSpace {
  return {
    logicalAt: (x) => Math.floor(x / 10),
    fromX: (x) => (x / 10) * H1,
    fromY: (y) => 5000 - y,
    toY: (p) => 5000 - p,
    snapPrice: (_x, _y, raw) => raw,
    shiftTimeByBars: (tSec, bars) => tSec + bars * 3600,
    ...overrides,
  };
}

// Тренд із якорем з M15 (10:15 = 10.25 H1) і другим на 13:00.
const TREND: Drawing = {
  id: 't',
  type: 'trend',
  points: [
    { t_ms: 10.25 * H1, price: 4150 },
    { t_ms: 13 * H1, price: 4180 },
  ],
};
const origin = (x: number, y: number) => ({ startX: x, startY: y, startObj: TREND });

describe('dragHandle', () => {
  it('у тому самому барі час якоря не змінюється, ціна — під курсором', () => {
    const step = dragHandle(TREND, origin(102, 850), 0, 107, 840, space());
    expect(step).toEqual({
      kind: 'moved',
      next: { ...TREND, points: [{ t_ms: 10.25 * H1, price: 4160 }, TREND.points[1]] },
    });
  });

  it('інший бар — час під курсором; магніт застосовується до ціни', () => {
    const step = dragHandle(TREND, origin(102, 850), 1, 155, 800, space({ snapPrice: () => 4199.5 }));
    expect(step.kind === 'moved' && step.next.points[1]).toEqual({ t_ms: 15.5 * H1, price: 4199.5 });
  });

  it('не вдалось перевести координату — пропуск із причиною, фігура та сама', () => {
    expect(dragHandle(TREND, origin(102, 850), 0, 107, 840, space({ fromY: () => null }))).toEqual({
      kind: 'skip',
      warnKey: 'drag_handle_null',
      detail: 'drag handle: cannot map x=107,y=840',
    });
  });
});

describe('dragBody', () => {
  it('зсув на ціле число барів і пікселі ціни; частка бару якоря з іншого TF лишається', () => {
    const step = dragBody(TREND, origin(105, 840), 127, 830, space());
    expect(step.kind === 'moved' && step.next.points).toEqual([
      { t_ms: 12.25 * H1, price: 4160 },
      { t_ms: 15 * H1, price: 4190 },
    ]);
  });

  it('у межах одного бару час не змінюється', () => {
    const step = dragBody(TREND, origin(101, 840), 108, 840, space());
    expect(step.kind === 'moved' && step.next.points).toEqual(TREND.points);
  });

  it('logical під курсором невідомий або старий якір поза видом — пропуск', () => {
    expect(dragBody(TREND, origin(105, 840), 127, 830, space({ logicalAt: () => null })).kind).toBe('skip');
    const step = dragBody(TREND, origin(105, 840), 127, 830, space({ toY: () => null }));
    expect(step).toEqual({ kind: 'skip', warnKey: 'drag_body_old_null', detail: 'drag body: old point out of view' });
  });

  it('точка, яку не перевести, лишається на поточному місці', () => {
    const step = dragBody(TREND, origin(105, 840), 127, 830, space({ shiftTimeByBars: () => null, fromY: () => null }));
    expect(step.kind === 'moved' && step.next.points).toEqual(TREND.points);
  });
});
