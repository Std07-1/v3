// src/chart/drawings/drawingDraft.test.ts — ADR-0111 §3.3 (крок 3b): створення фігури за метаданими реєстру.
import { describe, expect, it } from 'vitest';
import { cloneDrawing, completeDraft, firstClick, newDraft, previewsOnHover, tipIndex } from './drawingDraft';
import { TOOL_REGISTRY } from './tools';
import type { Drawing, DrawingType } from '../../types';

const tool = (type: DrawingType) => TOOL_REGISTRY.get(type)!;
const AT = { t_ms: 1791800100000, price: 4150 };
const META: Drawing['meta'] = { colorRole: 'bull', lineWidth: 2 };

describe('створення фігури за реєстром', () => {
  it('під курсором до кліку видно лише однокрокові (hline, позиції — ADR-0111 §3.6)', () => {
    expect([...TOOL_REGISTRY.values()].filter(previewsOnHover).map((t) => t.id)).toEqual(['hline', 'hray', 'long', 'short', 'text']);
  });

  it('перший клік: hline — одразу готова з однією точкою і стилем', () => {
    expect(firstClick(tool('hline'), 'id-1', AT, META)).toEqual({
      kind: 'commit',
      drawing: { id: 'id-1', type: 'hline', points: [AT], meta: META },
    });
  });

  it('перший клік: двоточкові — чернетка з обома точками в місці кліку; лінійка — без стилю', () => {
    for (const type of ['trend', 'ray', 'rect'] as const) {
      expect(firstClick(tool(type), 'id-2', AT, META)).toEqual({
        kind: 'draft',
        draft: { id: 'id-2', type, points: [AT, AT], meta: META },
      });
    }
    const measure = firstClick(tool('measure'), 'id-3', AT, META);
    expect(measure.kind).toBe('draft');
    expect(measure.kind === 'draft' && measure.draft.meta).toBeUndefined();
  });

  it('точки чернетки — окремі об’єкти (рух другої не тягне першу)', () => {
    const d = newDraft(tool('trend'), 'id-4', AT, undefined);
    expect(d.points[0]).not.toBe(d.points[1]);
    expect(d.points[0]).not.toBe(AT);
  });

  it('за курсором іде остання точка', () => {
    expect(tipIndex(newDraft(tool('hline'), 'a', AT, undefined))).toBe(0);
    expect(tipIndex(newDraft(tool('rect'), 'b', AT, undefined))).toBe(1);
  });
});

describe('completeDraft', () => {
  const moved = (type: DrawingType): Drawing => ({
    id: 'id-5',
    type,
    points: [AT, { t_ms: AT.t_ms + 3_600_000, price: 4180 }],
    meta: META,
  });

  it('лінійка заморожується, а не комітиться', () => {
    expect(completeDraft(tool('measure'), moved('measure'))).toEqual({ kind: 'freeze' });
  });

  it('двоточкова з точками в одному місці — відкидається', () => {
    expect(completeDraft(tool('trend'), newDraft(tool('trend'), 'z', AT, META))).toEqual({ kind: 'discard' });
  });

  it('готова фігура комітиться копією (чернетку далі не змінити через undo-стек)', () => {
    const draft = moved('rect');
    const done = completeDraft(tool('rect'), draft);
    expect(done).toEqual({ kind: 'commit', drawing: draft });
    expect(done.kind === 'commit' && done.drawing).not.toBe(draft);
    expect(done.kind === 'commit' && done.drawing.points[1]).not.toBe(draft.points[1]);
  });

  it('hline з підказки під курсором комітиться як є', () => {
    const preview = newDraft(tool('hline'), 'h', AT, META);
    expect(completeDraft(tool('hline'), preview)).toEqual({ kind: 'commit', drawing: preview });
  });
});

describe('cloneDrawing', () => {
  it('глибока копія точок і meta', () => {
    const d: Drawing = { id: 'c', type: 'trend', points: [AT, { ...AT, price: 1 }], meta: { colorRole: 'warn' } };
    const c = cloneDrawing(d);
    expect(c).toEqual(d);
    expect(c.points[0]).not.toBe(d.points[0]);
    expect(c.meta).not.toBe(d.meta);
  });
});
