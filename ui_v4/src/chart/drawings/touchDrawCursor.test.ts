// src/chart/drawings/touchDrawCursor.test.ts — ADR-0112 D3: курсор рухається 1:1 і не виходить за панель; тап ставить точку.
import { describe, expect, it } from 'vitest';
import { isTap, moveCursor } from './touchDrawCursor';

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
