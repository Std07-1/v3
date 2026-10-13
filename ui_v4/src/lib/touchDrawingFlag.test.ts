// src/lib/touchDrawingFlag.test.ts — ADR-0112 D1: прапорець пробного режиму малювання на телефоні.
import { describe, expect, it, vi } from 'vitest';
import { resolveTouchDrawing, touchDrawingFromQuery } from './touchDrawingFlag';

function memory() {
  const store = new Map<string, string>();
  return {
    store,
    storage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
  };
}

describe('touchDrawingFromQuery', () => {
  it('?draw=1 / ?draw=0 / нема або інше значення', () => {
    expect(touchDrawingFromQuery('?symbol=XAU/USD&draw=1')).toBe(true);
    expect(touchDrawingFromQuery('?draw=0')).toBe(false);
    expect(touchDrawingFromQuery('?symbol=XAU/USD')).toBeNull();
    expect(touchDrawingFromQuery('?draw=yes')).toBeNull();
  });
});

describe('resolveTouchDrawing', () => {
  it('?draw=1 запам’ятовує на пристрої; далі діє без параметра; ?draw=0 знімає', () => {
    const { store, storage } = memory();
    expect(resolveTouchDrawing('', storage)).toBe(false);
    expect(resolveTouchDrawing('?draw=1', storage)).toBe(true);
    expect(store.get('v4_touch_drawing')).toBe('1');
    expect(resolveTouchDrawing('?symbol=NAS100', storage)).toBe(true);
    expect(resolveTouchDrawing('?draw=0', storage)).toBe(false);
    expect(resolveTouchDrawing('', storage)).toBe(false);
  });

  it('без сховища або коли воно кидає — лише параметр цієї сторінки, з попередженням', () => {
    expect(resolveTouchDrawing('?draw=1', null)).toBe(true);
    expect(resolveTouchDrawing('', null)).toBe(false);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const throwing = {
      getItem: () => { throw new Error('SecurityError'); },
      setItem: () => { throw new Error('SecurityError'); },
      removeItem: () => { throw new Error('SecurityError'); },
    };
    expect(resolveTouchDrawing('?draw=1', throwing)).toBe(true);
    expect(resolveTouchDrawing('', throwing)).toBe(false);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
