// src/chart/drawings/colorRoles.test.ts
// ADR-0080 (surface-2): специфікація SSOT семантичних кольорів-ролей.

import { describe, it, expect } from 'vitest';
import {
  DRAWING_COLOR_ROLES,
  roleSpec,
  buildRoleColorMap,
} from './colorRoles';

describe('DRAWING_COLOR_ROLES (SSOT)', () => {
  it('містить рівно 6 ролей у канонічному порядку', () => {
    expect(DRAWING_COLOR_ROLES.map((r) => r.role)).toEqual([
      'neutral',
      'accent',
      'bull',
      'bear',
      'info',
      'warn',
    ]);
  });

  it('кожна роль має label, cssVar-токен і hex-fallback', () => {
    for (const spec of DRAWING_COLOR_ROLES) {
      expect(spec.label.length).toBeGreaterThan(0);
      expect(spec.cssVar.startsWith('--')).toBe(true);
      expect(spec.fallback).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

describe('roleSpec', () => {
  it('повертає spec для відомої ролі', () => {
    expect(roleSpec('bull')?.cssVar).toBe('--bull');
  });
  it('повертає undefined для невідомої ролі', () => {
    // @ts-expect-error навмисно невалідна роль
    expect(roleSpec('magenta')).toBeUndefined();
  });
});

describe('buildRoleColorMap', () => {
  it('використовує значення з lookup коли CSS-змінна задана', () => {
    const map = buildRoleColorMap((v) => (v === '--bull' ? '#00ff00' : ''));
    expect(map.bull).toBe('#00ff00');
  });

  it('падає на RoleSpec.fallback коли lookup порожній (до applyThemeCssVars)', () => {
    const map = buildRoleColorMap(() => '');
    expect(map.accent).toBe('#d4a017');
    expect(map.bear).toBe('#ed4554');
  });

  it('покриває всі 6 ролей (жодна не undefined)', () => {
    const map = buildRoleColorMap(() => '');
    for (const spec of DRAWING_COLOR_ROLES) {
      expect(map[spec.role]).toBeTruthy();
    }
  });

  it('тримає пробіли з CSS getPropertyValue (trim)', () => {
    const map = buildRoleColorMap((v) => (v === '--info' ? '  #123456  ' : ''));
    expect(map.info).toBe('#123456');
  });
});
