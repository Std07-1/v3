import { describe, expect, it } from 'vitest';
import { formatBarCountdown } from './barCountdown';
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

describe('TF_LABEL_TO_S — мітки графіка', () => {
  it('W1 і MN після D1, зворотна мапа для ?tf=секунди', () => {
    expect(Object.keys(TF_LABEL_TO_S).slice(-3)).toEqual(['D1', 'W1', 'MN']);
    expect(TF_S_TO_LABEL['604800']).toBe('W1');
    expect(TF_S_TO_LABEL['2592000']).toBe('MN');
    expect(TF_S_TO_LABEL['60']).toBe('M1');
  });
});
