// src/chart/drawings/toolGroups.test.ts — ADR-0112 D9: позиція в капсулі телефона — одна кнопка з перемикачем.
import { describe, expect, it } from 'vitest';
import { capsuleEntries, groupVariants } from './toolGroups';
import { TOOL_REGISTRY, getToolModule } from './tools';

const tools = [...TOOL_REGISTRY.values()];

describe('capsuleEntries', () => {
  it('довга й коротка — одна кнопка «Позиція» на місці довгої; решта — як у реєстрі', () => {
    const entries = capsuleEntries(tools, {});
    expect(entries.map((e) => e.key)).toEqual(['hline', 'trend', 'ray', 'rect', 'fib', 'position', 'measure']);
    const position = entries.find((e) => e.key === 'position');
    expect(position?.label).toBe('Позиція');
    expect(position?.tool.id).toBe('long');
  });

  it('кнопка групи бере останній обраний варіант (іконка й дія — короткої)', () => {
    expect(capsuleEntries(tools, { position: 'short' }).find((e) => e.key === 'position')?.tool.id).toBe('short');
  });

  it('чужий інструмент як «останній» групи ігнорується — перший варіант', () => {
    expect(capsuleEntries(tools, { position: 'hline' }).find((e) => e.key === 'position')?.tool.id).toBe('long');
  });
});

describe('groupVariants', () => {
  it('варіанти позиції — довга й коротка; без групи — порожньо', () => {
    expect(groupVariants(tools, getToolModule('short')!).map((t) => t.id)).toEqual(['long', 'short']);
    expect(groupVariants(tools, getToolModule('trend')!)).toEqual([]);
  });
});
