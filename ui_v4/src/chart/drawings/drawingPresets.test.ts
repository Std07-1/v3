// src/chart/drawings/drawingPresets.test.ts
// ADR-0080 (наміри-перші): специфікація SSOT пресетів + matchPreset.

import { describe, it, expect } from 'vitest';
import { DRAWING_PRESETS, matchPreset } from './drawingPresets';

describe('DRAWING_PRESETS (SSOT)', () => {
  it('містить 4 наміри у канонічному порядку', () => {
    expect(DRAWING_PRESETS.map((p) => p.id)).toEqual([
      'thesis',
      'level',
      'note',
      'alert',
    ]);
  });

  it('кожен пресет = повна трійця (роль+товщина 1-4+стиль) + непорожня назва', () => {
    for (const p of DRAWING_PRESETS) {
      expect(p.label.length).toBeGreaterThan(0);
      expect(['neutral', 'accent', 'bull', 'bear', 'info', 'warn']).toContain(p.colorRole);
      expect(p.lineWidth).toBeGreaterThanOrEqual(1);
      expect(p.lineWidth).toBeLessThanOrEqual(4);
      expect(['solid', 'dashed', 'dotted']).toContain(p.lineStyle);
    }
  });

  it('трійці пресетів унікальні (matchPreset детермінований)', () => {
    const keys = DRAWING_PRESETS.map((p) => `${p.colorRole}|${p.lineWidth}|${p.lineStyle}`);
    expect(new Set(keys).size).toBe(DRAWING_PRESETS.length);
  });
});

describe('matchPreset', () => {
  it('повертає id пресета за точним збігом трійці', () => {
    const p = DRAWING_PRESETS[0];
    expect(matchPreset(p.colorRole, p.lineWidth, p.lineStyle)).toBe(p.id);
  });

  it('повертає null для кастомної комбінації (не пресет)', () => {
    // bull + 3px + dashed навмисно не збігається з жодним first-pass пресетом
    expect(matchPreset('bull', 3, 'dashed')).toBeNull();
  });

  it('часткові збіги не матчаться (потрібні всі три поля)', () => {
    const p = DRAWING_PRESETS[0]; // thesis: accent/2/solid
    expect(matchPreset(p.colorRole, p.lineWidth, 'dotted')).toBeNull();
  });
});
