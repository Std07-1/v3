// src/lib/touchDrawingFlag.test.ts — ADR-0112 D1/D10: малювання на телефоні — для всіх, ?draw=0 вимикає на пристрої.
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
  it('за замовчуванням увімкнено; ?draw=0 вимикає й запам’ятовується; ?draw=1 вмикає знову', () => {
    const { store, storage } = memory();
    expect(resolveTouchDrawing('', storage)).toBe(true);
    expect(resolveTouchDrawing('?draw=0', storage)).toBe(false);
    expect(store.get('v4_touch_drawing')).toBe('0');
    expect(resolveTouchDrawing('?symbol=NAS100', storage)).toBe(false);
    expect(resolveTouchDrawing('?draw=1', storage)).toBe(true);
    expect(store.has('v4_touch_drawing')).toBe(false);
    expect(resolveTouchDrawing('', storage)).toBe(true);
  });

  it('пристрій із пробного періоду (збережене «1») лишається з малюванням', () => {
    const { store, storage } = memory();
    store.set('v4_touch_drawing', '1');
    expect(resolveTouchDrawing('', storage)).toBe(true);
  });

  it('без сховища або коли воно кидає — лише параметр цієї сторінки, з попередженням', () => {
    expect(resolveTouchDrawing('?draw=0', null)).toBe(false);
    expect(resolveTouchDrawing('', null)).toBe(true);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const throwing = {
      getItem: () => { throw new Error('SecurityError'); },
      setItem: () => { throw new Error('SecurityError'); },
      removeItem: () => { throw new Error('SecurityError'); },
    };
    expect(resolveTouchDrawing('?draw=0', throwing)).toBe(false);
    expect(resolveTouchDrawing('', throwing)).toBe(true);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
