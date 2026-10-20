import { describe, expect, it } from 'vitest';
import { compactPillText } from './agentState';
import type { AgentState, NarrativeBlock } from '../types';

const IDLE = 'idle' as unknown as AgentState;
const systemOnly = {
    mode: 'wait',
    headline: '⬜ Немає зони рівня A — чекаємо сетап',
    scenarios: [],
} as unknown as NarrativeBlock;

describe('compactPillText — пігулка в куті Арчі', () => {
    it('без тези Арчі показує Idle, а не заголовок системного наративу (власник 19.10, ADR-0070 Rev 5)', () => {
        expect(compactPillText(systemOnly, IDLE)).toBe('Idle');
    });

    it('свіжа теза Арчі — дослівно', () => {
        const withThesis = { ...systemOnly, archi_thesis: { thesis: 'Чекаю зняття Азії', freshness: 'fresh' } };
        expect(compactPillText(withThesis as unknown as NarrativeBlock, IDLE)).toBe('Чекаю зняття Азії');
    });

    it('застаріла теза без presence — Idle', () => {
        const stale = { ...systemOnly, archi_thesis: { thesis: 'стара', freshness: 'stale' } };
        expect(compactPillText(stale as unknown as NarrativeBlock, IDLE)).toBe('Idle');
    });

    it('кадру нема — Idle', () => {
        expect(compactPillText(null, IDLE)).toBe('Idle');
    });
});
