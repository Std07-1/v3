// ADR-0104 S2b: Focus = режим «Авто» для рівнів — auto=false (сервер) лишається Research-у.
import { describe, expect, it } from 'vitest';
import type { SmcLevel } from '../../types';
import { applyBudget, type BudgetConfig } from './DisplayBudget';

function level(id: string, auto?: boolean): SmcLevel {
  const base: SmcLevel = { id, kind: 'p_lon_h', price: 4287.94, state: 'fixed' };
  return auto === undefined ? base : { ...base, auto };
}

const ids = (levels: SmcLevel[]) => levels.map(l => l.id);

describe('applyBudget: рівні і режим «Авто» (ADR-0104 S2b)', () => {
  const levels = [level('prev_day', false), level('today', true), level('no_decision')];

  it('Focus ховає рівні, виведені сервером з «Авто»', () => {
    expect(ids(applyBudget([], levels, [], 'focus').levels)).toEqual(['today', 'no_decision']);
  });

  it('Research показує і рівні поза «Авто»', () => {
    expect(ids(applyBudget([], levels, [], 'research').levels)).toEqual(['prev_day', 'today', 'no_decision']);
  });

  it('рівень поза «Авто» не займає бюджет Focus', () => {
    const tight: BudgetConfig = { perSide: 3, total: 2, structureMax: 4 };
    expect(ids(applyBudget([], levels, [], 'focus', tight).levels)).toEqual(['today', 'no_decision']);
  });
});
