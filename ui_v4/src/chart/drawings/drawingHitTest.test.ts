// src/chart/drawings/drawingHitTest.test.ts — що під вказівником (ADR-0112 D8, 14.10): ручка бере гору над тілом,
// інтер'єром прямокутника і над ×, якщо до неї ближче; ручки фігури доступні без наведення.
import { describe, expect, it } from 'vitest';
import { hitTestDrawings, type HitScene } from './drawingHitTest';
import type { Drawing } from '../../types';

// Простір 1:1: час = x px, ціна = y px — геометрія тестів читається в пікселях.
const RECT: Drawing = { id: 'r', type: 'rect', points: [{ t_ms: 100, price: 300 }, { t_ms: 240, price: 160 }] };
const TREND: Drawing = { id: 't', type: 'trend', points: [{ t_ms: 400, price: 300 }, { t_ms: 520, price: 200 }] };
const HLINE: Drawing = { id: 'h', type: 'hline', points: [{ t_ms: 153, price: 380 }] };
const RECT_BOX = { minX: 100, minY: 160, maxX: 240, maxY: 300 };

function scene(overrides: Partial<HitScene> = {}): HitScene {
  return {
    drawings: [RECT, TREND, HLINE],
    activeId: null,
    deleteBtn: null,
    aabbById: new Map([
      ['r', RECT_BOX],
      ['t', { minX: 400, minY: 200, maxX: 520, maxY: 300 }],
      ['h', { minX: 0, minY: 380, maxX: 315, maxY: 380 }],
    ]),
    tolerancePx: 16,
    toX: (t) => t,
    toY: (p) => p,
    ...overrides,
  };
}
/** Виділений прямокутник: × по центру AABB. */
const rectSelected = scene({ activeId: 'r', deleteBtn: { id: 'r', x: 170, y: 230 } });

describe('hitTestDrawings — прямокутник', () => {
  it('кут зсередини виділеного прямокутника — ручка, не вся фігура (корінь «перескакує» 14.10)', () => {
    expect(hitTestDrawings(rectSelected, 237, 163)).toEqual({ id: 'r', handleIdx: 1, onDelete: false });
  });

  it('кут ззовні — та сама ручка', () => {
    expect(hitTestDrawings(rectSelected, 243, 157)).toEqual({ id: 'r', handleIdx: 1, onDelete: false });
  });

  it('інтер\'єр далеко від кутів і × — тіло', () => {
    expect(hitTestDrawings(rectSelected, 140, 260)).toEqual({ id: 'r', handleIdx: null, onDelete: false });
  });

  it('× у центрі — видалення', () => {
    expect(hitTestDrawings(rectSelected, 172, 228)).toEqual({ id: 'r', handleIdx: null, onDelete: true });
  });

  it('невиділений: кут під пальцем — ручка без наведення (на дотику наведення нема)', () => {
    expect(hitTestDrawings(scene(), 238, 163)).toEqual({ id: 'r', handleIdx: 1, onDelete: false });
  });
});

describe('hitTestDrawings — × поруч із ручкою', () => {
  // Горизонталь, поставлена біля середини панелі: × (центр AABB, x = 157.5) за 4.5 px від ручки (x = 153).
  const hlineSelected = scene({ activeId: 'h', deleteBtn: { id: 'h', x: 157.5, y: 380 } });

  it('палець на ручці — ручка, не видалення', () => {
    expect(hitTestDrawings(hlineSelected, 153, 381)).toEqual({ id: 'h', handleIdx: 0, onDelete: false });
  });

  it('вказівник рівно на × — тап видалить, а повів — тягне ту саму ручку (× і ручка майже збігаються)', () => {
    expect(hitTestDrawings(hlineSelected, 158, 380)).toEqual({ id: 'h', handleIdx: 0, onDelete: true });
  });

  it('рівно посередині — ручка (вона нічого не руйнує)', () => {
    const tie = scene({ activeId: 'h', deleteBtn: { id: 'h', x: 163, y: 380 } });
    expect(hitTestDrawings(tie, 158, 380)).toEqual({ id: 'h', handleIdx: 0, onDelete: false });
  });
});

describe('hitTestDrawings — тіло й порожнеча', () => {
  it('середина тренду — тіло', () => {
    expect(hitTestDrawings(scene(), 460, 250)).toEqual({ id: 't', handleIdx: null, onDelete: false });
  });

  it('кінець тренду без наведення — найближча ручка', () => {
    expect(hitTestDrawings(scene(), 517, 204)).toEqual({ id: 't', handleIdx: 1, onDelete: false });
  });

  it('порожньо — null', () => {
    expect(hitTestDrawings(scene(), 330, 100)).toBeNull();
  });

  it('дві фігури під вказівником — верхня (остання в списку)', () => {
    const under: Drawing = { id: 'u', type: 'hline', points: [{ t_ms: 50, price: 250 }] };
    const over: Drawing = { id: 'o', type: 'hline', points: [{ t_ms: 300, price: 250 }] };
    const s = scene({
      drawings: [under, over],
      aabbById: new Map([
        ['u', { minX: 0, minY: 250, maxX: 315, maxY: 250 }],
        ['o', { minX: 0, minY: 250, maxX: 315, maxY: 250 }],
      ]),
    });
    expect(hitTestDrawings(s, 180, 251)?.id).toBe('o');
  });
});
