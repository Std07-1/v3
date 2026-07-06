// src/chart/drawings/lineStyles.test.ts
// ADR-0080 (surface-2, крок 4): специфікація SSOT стилів лінії + dash-візерунка.

import { describe, it, expect } from 'vitest';
import { DRAWING_LINE_STYLES, dashPattern } from './lineStyles';

describe('DRAWING_LINE_STYLES (SSOT)', () => {
  it('містить solid/dashed/dotted у канонічному порядку', () => {
    expect(DRAWING_LINE_STYLES.map((s) => s.style)).toEqual([
      'solid',
      'dashed',
      'dotted',
    ]);
  });
  it('кожен стиль має непорожній label', () => {
    for (const s of DRAWING_LINE_STYLES) expect(s.label.length).toBeGreaterThan(0);
  });
});

describe('dashPattern', () => {
  it('solid/undefined → порожній масив (суцільна)', () => {
    expect(dashPattern('solid', 2)).toEqual([]);
    expect(dashPattern(undefined, 2)).toEqual([]);
  });

  it('dashed → штрих×пропуск, масштабовані товщиною', () => {
    expect(dashPattern('dashed', 1)).toEqual([4, 3]);
    expect(dashPattern('dashed', 2)).toEqual([8, 6]);
  });

  it('dotted → нульовий штрих (canonical round-cap крапка) + пропуск', () => {
    const p = dashPattern('dotted', 2);
    expect(p[0]).toBe(0); // нульовий dash → круг з lineCap=round
    expect(p[1]).toBeGreaterThan(0);
  });

  it('gap dotted зростає з товщиною (крапки не зливаються)', () => {
    expect(dashPattern('dotted', 4)[1]).toBeGreaterThan(dashPattern('dotted', 1)[1]);
  });

  it('lineWidth < 1 підлоговується до 1 (нема нульового візерунка)', () => {
    expect(dashPattern('dashed', 0)).toEqual([4, 3]);
  });
});
