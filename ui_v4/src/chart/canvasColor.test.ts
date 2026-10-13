// src/chart/canvasColor.test.ts
import { describe, expect, it } from 'vitest';
import { withAlpha } from './canvasColor';

describe('withAlpha', () => {
  it('hex-токен → rgba з альфою (з # і без, регістр не важить)', () => {
    expect(withAlpha('#22CC8F', 0.08, '128,128,128')).toBe('rgba(34,204,143,0.08)');
    expect(withAlpha('ed4554', 1, '128,128,128')).toBe('rgba(237,69,84,1)');
    expect(withAlpha('  #5487ff ', 0.5, '0,0,0')).toBe('rgba(84,135,255,0.5)');
  });

  it('не hex — запасний колір шару з тією ж альфою', () => {
    expect(withAlpha('', 0.3, '128,128,128')).toBe('rgba(128,128,128,0.3)');
    expect(withAlpha('rgba(1,2,3,1)', 0.2, '84,135,255')).toBe('rgba(84,135,255,0.2)');
  });
});
