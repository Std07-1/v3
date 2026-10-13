// src/chart/drawings/touchDrawCursor.test.ts — ADR-0112 D3: курсор рухається 1:1 і не виходить за панель; тап ставить точку.
import { describe, expect, it } from 'vitest';
import { isNewSnapLevel, isTap, levelOf, moveCursor } from './touchDrawCursor';

const PANE = { width: 300, height: 500 };

describe('moveCursor', () => {
  it('рух 1:1 від того місця, де курсор стояв (не туди, де палець)', () => {
    expect(moveCursor({ x: 150, y: 250 }, 12, -30, PANE)).toEqual({ x: 162, y: 220 });
  });

  it('не виходить за панель графіка', () => {
    expect(moveCursor({ x: 290, y: 10 }, 50, -40, PANE)).toEqual({ x: 299, y: 0 });
    expect(moveCursor({ x: 5, y: 495 }, -20, 30, PANE)).toEqual({ x: 0, y: 499 });
  });

  it('панель ще без розміру — курсор у нулі, без NaN', () => {
    expect(moveCursor({ x: 0, y: 0 }, 5, 5, { width: 0, height: 0 })).toEqual({ x: 0, y: 0 });
  });
});

describe('isTap', () => {
  it('палець майже не рушив і відпущений швидко — тап, ставимо точку', () => {
    expect(isTap(0, 80)).toBe(true);
    expect(isTap(7.9, 299)).toBe(true);
  });

  it('палець вів кільце — не тап, точки нема', () => {
    expect(isTap(8, 80)).toBe(false);
    expect(isTap(40, 120)).toBe(false);
  });

  it('довге утримання без руху — не тап', () => {
    expect(isTap(0, 300)).toBe(false);
    expect(isTap(2, 900)).toBe(false);
  });
});

describe('isNewSnapLevel', () => {
  it('прилипло з вільного руху або до іншого рівня — «клац»', () => {
    expect(isNewSnapLevel(null, 4180.5)).toBe(true);
    expect(isNewSnapLevel(4180.5, 4182.1)).toBe(true);
  });

  it('той самий рівень або вільний рух — тиша', () => {
    expect(isNewSnapLevel(4180.5, 4180.5)).toBe(false);
    expect(isNewSnapLevel(4180.5, null)).toBe(false);
    expect(isNewSnapLevel(null, null)).toBe(false);
  });
});

describe('levelOf', () => {
  const bar = { open: 4170.1, high: 4176.46, low: 4168.2, close: 4175.79 };

  it('ціна магніту — підпис рівня свічки в лупі', () => {
    expect(levelOf(4176.46, bar)).toBe('H');
    expect(levelOf(4168.2, bar)).toBe('L');
    expect(levelOf(4170.1, bar)).toBe('O');
    expect(levelOf(4175.79, bar)).toBe('C');
  });

  it('збіг рівнів — за порядком магніту (хай раніше за закриття)', () => {
    expect(levelOf(4176.46, { ...bar, close: 4176.46 })).toBe('H');
  });

  it('не рівень цієї свічки або свічки нема — без літери', () => {
    expect(levelOf(4171, bar)).toBe('');
    expect(levelOf(4171, null)).toBe('');
  });
});
