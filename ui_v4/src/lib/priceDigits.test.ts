// src/lib/priceDigits.test.ts — точність ціни з конфігу сервера (ADR-0054 rev 7 §3.4.1 S3)
import { describe, it, expect } from 'vitest';
import { configuredPriceDigits, displayPriceDigits, parsePriceDigits } from './priceDigits';

describe('parsePriceDigits', () => {
  it('мапа цілих 0..8 — як є', () => {
    expect(parsePriceDigits({ 'XAU/USD': 2, 'XAG/USD': 3 })).toEqual({ 'XAU/USD': 2, 'XAG/USD': 3 });
  });

  it.each([[{ 'XAU/USD': 2.5 }], [{ 'XAU/USD': -1 }], [{ 'XAU/USD': 9 }], [{ 'XAU/USD': '2' }], [[2, 3]], [null], ['2']])(
    'не той вигляд %j — null',
    (raw) => {
      expect(parsePriceDigits(raw)).toBeNull();
    },
  );
});

describe('displayPriceDigits', () => {
  it('знаки з конфігу перемагають величину ціни: XAG 61.151 → 3, USD/JPY 157.822 → 3, XAU 4140.64 → 2', () => {
    const digits = { 'XAG/USD': 3, 'USD/JPY': 3, 'XAU/USD': 2 };
    expect(displayPriceDigits(configuredPriceDigits(digits, 'XAG/USD'), 61.151)).toBe(3);
    expect(displayPriceDigits(configuredPriceDigits(digits, 'USD/JPY'), 157.822)).toBe(3);
    expect(displayPriceDigits(configuredPriceDigits(digits, 'XAU/USD'), 4140.64)).toBe(2);
  });

  it('символу нема в конфігу або конфіг не прийшов — стара евристика за величиною', () => {
    expect(configuredPriceDigits({ 'XAU/USD': 2 }, 'NGAS')).toBeNull();
    expect(configuredPriceDigits(undefined, 'XAU/USD')).toBeNull();
    expect([displayPriceDigits(null, 4140.6), displayPriceDigits(null, 61.1), displayPriceDigits(null, 3.27)]).toEqual([2, 3, 5]);
    expect(displayPriceDigits(null, -0.53)).toBe(5);
  });
});
