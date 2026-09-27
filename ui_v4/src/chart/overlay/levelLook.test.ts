// ADR-0104 S2c: вигляд рівня за станом (fixed / forming), колір і назва — за kind.
import { describe, expect, it } from 'vitest';
import type { SmcLevel } from '../../types';
import { applyLevelState, type LevelStyle } from './levelLook';

const LONDON: LevelStyle = { color: '#FF9800', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'London Hi', fontSize: 10 };
const EQH: LevelStyle = { color: '#e91e63', dash: [2, 2], width: 1.0, alpha: 0.75, label: 'EQH', fontSize: 10 };

function level(extra: Partial<SmcLevel>): SmcLevel {
  return { id: 'lon_h_XAU_USD_86400_431568', kind: 'lon_h', price: 4315.68, family: 'session', ...extra };
}

describe('applyLevelState (ADR-0104 S2c)', () => {
  it('завершена сьогоднішня сесія — суцільна лінія повної ваги, назва та сама', () => {
    const look = applyLevelState(LONDON, level({ state: 'fixed' }));
    expect(look).toMatchObject({ dash: [], width: 1.5, alpha: 0.8, label: 'London Hi', color: '#FF9800' });
  });

  it('сесія, що йде, — тонший штрих, прозоріша, «…» у підписі', () => {
    const look = applyLevelState(LONDON, level({ state: 'forming' }));
    expect(look).toMatchObject({ dash: [3, 2], width: 1.0, label: 'London Hi…', color: '#FF9800' });
    expect(look.alpha).toBeCloseTo(0.4875);
  });

  it('EQ лишає пунктир родини ліквідності', () => {
    expect(applyLevelState(EQH, level({ kind: 'eq_highs', family: 'liquidity', state: 'fixed' }))).toEqual(EQH);
  });

  it('без стану або зі станом без вигляду — стиль kind без змін', () => {
    expect(applyLevelState(LONDON, level({ state: undefined }))).toEqual(LONDON);
    expect(applyLevelState(LONDON, level({ state: 'swept' }))).toEqual(LONDON);
  });

  it('не змінює вхідний стиль (LEVEL_STYLES — спільний словник)', () => {
    const before = structuredClone(LONDON);
    applyLevelState(LONDON, level({ state: 'forming' }));
    expect(LONDON).toEqual(before);
  });
});
