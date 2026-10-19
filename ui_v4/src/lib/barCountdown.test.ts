import { describe, expect, it } from 'vitest';
import { barCountdownMs, formatBarCountdown } from './barCountdown';
import { TF_LABEL_TO_S, TF_S_TO_LABEL } from '../constants/tfLabels';

const H = 3_600_000;
const D = 24 * H;

describe('formatBarCountdown — таймер до закриття бару (ADR-0115 §4.4)', () => {
  it('від доби — дні й години «3д 04г»', () => {
    expect(formatBarCountdown(3 * D + 4 * H + 59 * 60_000)).toBe('3д 04г');
    expect(formatBarCountdown(D)).toBe('1д 00г');
  });

  it('менше доби — хв:сс, як на M1…D1', () => {
    expect(formatBarCountdown(754 * 60_000 + 12_000)).toBe('754:12');
    expect(formatBarCountdown(59_000)).toBe('00:59');
  });

  it('нема значення чи минуло — «—:—»', () => {
    expect(formatBarCountdown(null)).toBe('—:—');
    expect(formatBarCountdown(-1)).toBe('—:—');
  });
});

describe('barCountdownMs — до закриття бару', () => {
  const now = 1_792_400_000_000;

  it('W1/MN — лише кінець від бекенда; нема чи минув — null, без сітки tf', () => {
    const base = { tfS: 604800, anchorMs: 0, calendar: true, nowMs: now };
    expect(barCountdownMs({ ...base, barCloseMs: now + 2 * D })).toBe(2 * D);
    expect(barCountdownMs({ ...base, barCloseMs: null })).toBeNull(); // вихідні: торги тижня закрились
    expect(barCountdownMs({ ...base, barCloseMs: now - 1 })).toBeNull();
  });

  it('M1…D1 — сітка tf від якоря, як і раніше', () => {
    const anchorMs = 21 * H; // D1 відкривається о 21:00 UTC улітку
    const dayStart = Math.floor((now - anchorMs) / D) * D + anchorMs;
    expect(barCountdownMs({ tfS: 86400, anchorMs, barCloseMs: null, calendar: false, nowMs: now }))
      .toBe(dayStart + D - now);
    expect(barCountdownMs({ tfS: 86400, anchorMs: null, barCloseMs: null, calendar: false, nowMs: now })).toBeNull();
  });
});

describe('TF_LABEL_TO_S — мітки графіка', () => {
  it('W1 і MN після D1, зворотна мапа для ?tf=секунди', () => {
    expect(Object.keys(TF_LABEL_TO_S).slice(-3)).toEqual(['D1', 'W1', 'MN']);
    expect(TF_S_TO_LABEL['604800']).toBe('W1');
    expect(TF_S_TO_LABEL['2592000']).toBe('MN');
    expect(TF_S_TO_LABEL['60']).toBe('M1');
  });
});
