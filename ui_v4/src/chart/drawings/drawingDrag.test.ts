// src/chart/drawings/drawingDrag.test.ts — перетягування ручки й тіла (винесено з DrawingsRenderer, 13.10).
import { describe, expect, it } from 'vitest';
import { dragBody, dragHandle, grabOffsetFor, type DragSpace } from './drawingDrag';
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

describe('ручка пальцем — зі зсувом захоплення (ADR-0112 D8, 14.10)', () => {
  it('grabOffsetFor: дотик — ручка мінус палець; миша, перо чи ручка поза видом — без зсуву', () => {
    expect(grabOffsetFor('touch', { x: 102.5, y: 850 }, 114.5, 846)).toEqual({ x: -12, y: 4 });
    expect(grabOffsetFor('mouse', { x: 102.5, y: 850 }, 114.5, 846)).toBeUndefined();
    expect(grabOffsetFor('pen', { x: 102.5, y: 850 }, 114.5, 846)).toBeUndefined();
    expect(grabOffsetFor('touch', { x: null, y: 850 }, 114.5, 846)).toBeUndefined();
  });

  it('палець узяв ручку на 12 px вище — ручка не стрибає під палець, а йде з тим самим зсувом', () => {
    // Якір P1 — x 102.5, y 850 (ціна 4150). Палець — на 12 px вище (y 838), далі на 3 px вниз.
    const grab = { startX: 102.5, startY: 838, startObj: TREND, grabOffset: { x: 0, y: 12 } };
    const step = dragHandle(TREND, grab, 0, 102.5, 841, space());
    expect(step.kind === 'moved' && step.next.points[0]).toEqual({ t_ms: 10.25 * H1, price: 4147 });
    // Без зсуву той самий рух ставив якір під палець: +9 за ціною замість −3 (стрибок)
    const jump = dragHandle(TREND, origin(102.5, 838), 0, 102.5, 841, space());
    expect(jump.kind === 'moved' && jump.next.points[0]).toEqual({ t_ms: 10.25 * H1, price: 4159 });
  });

  it('палець пройшов два бари — ручка теж, магніт бере точку ручки, не пальця', () => {
    const grab = { startX: 114.5, startY: 850, startObj: TREND, grabOffset: { x: -12, y: 6 } };
    const seen: number[][] = [];
    const step = dragHandle(TREND, grab, 0, 134.5, 850, space({ snapPrice: (x, y, raw) => (seen.push([x, y]), raw) }));
    expect(step.kind === 'moved' && step.next.points[0]).toEqual({ t_ms: 12.25 * H1, price: 4144 });
    expect(seen).toEqual([[122.5, 856]]);
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
