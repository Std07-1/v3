// src/chart/drawings/toolGroups.test.ts — ADR-0112 D9/D11: групи варіантів — одна кнопка з перемикачем (ПК і телефон).
import { describe, expect, it } from 'vitest';
import { capsuleEntries, groupVariants } from './toolGroups';
import { TOOL_REGISTRY, getToolModule } from './tools';

const tools = [...TOOL_REGISTRY.values()];

describe('capsuleEntries', () => {
  it('сім кнопок: групи — на місці першого варіанта (Рівень · Лінія · Прямокутник · Позиція), решта — як у реєстрі', () => {
    const entries = capsuleEntries(tools, {});
    expect(entries.map((e) => e.key)).toEqual(['level', 'line', 'box', 'fib', 'position', 'text', 'measure']);
    expect(entries.map((e) => e.label)).toEqual(['Рівень', 'Лінія', 'Прямокутник', 'Фібоначчі', 'Позиція', 'Нотатка', 'Лінійка']);
    expect(entries.map((e) => e.tool.id)).toEqual(['hline', 'trend', 'rect', 'fib', 'long', 'text', 'measure']);
  });

  it('кнопка групи бере останній обраний варіант (іконка й дія — короткої)', () => {
    expect(capsuleEntries(tools, { position: 'short' }).find((e) => e.key === 'position')?.tool.id).toBe('short');
  });

  it('чужий інструмент як «останній» групи ігнорується — перший варіант', () => {
    expect(capsuleEntries(tools, { position: 'hline' }).find((e) => e.key === 'position')?.tool.id).toBe('long');
  });
});

describe('groupVariants', () => {
  it('варіанти групи — у порядку реєстру; без групи — порожньо', () => {
    expect(groupVariants(tools, getToolModule('short')!).map((t) => t.id)).toEqual(['long', 'short']);
    expect(groupVariants(tools, getToolModule('hray')!).map((t) => t.id)).toEqual(['hline', 'hray']);
    expect(groupVariants(tools, getToolModule('ray')!).map((t) => t.id)).toEqual(['trend', 'ray']);
    expect(groupVariants(tools, getToolModule('zone')!).map((t) => t.id)).toEqual(['rect', 'zone']);
    expect(groupVariants(tools, getToolModule('fib')!)).toEqual([]);
  });
});
