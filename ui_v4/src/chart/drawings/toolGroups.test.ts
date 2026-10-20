// src/chart/drawings/toolGroups.test.ts — ADR-0112 D9/D11: групи варіантів — одна кнопка з перемикачем (ПК і телефон).
import { describe, expect, it } from 'vitest';
import { capsuleEntries, entryHolds, groupVariants, rememberVariant } from './toolGroups';
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

describe('entryHolds', () => {
  const entries = capsuleEntries(tools, {});
  const box = entries.find((e) => e.key === 'box')!;

  it('кнопка групи активна з будь-яким своїм варіантом; інша кнопка — ні', () => {
    expect(entryHolds(box, 'rect', tools)).toBe(true);
    expect(entryHolds(box, 'zone', tools)).toBe(true);
    expect(entryHolds(box, 'hline', tools)).toBe(false);
  });

  it('без групи — лише сам інструмент; курсор і гумка — нічия', () => {
    const fib = entries.find((e) => e.key === 'fib')!;
    expect(entryHolds(fib, 'fib', tools)).toBe(true);
    expect(entryHolds(fib, null, tools)).toBe(false);
    expect(entryHolds(box, 'eraser', tools)).toBe(false);
  });
});

describe('rememberVariant', () => {
  it('клавіша Z — зона стає останнім варіантом «Прямокутника»; кнопка бере її', () => {
    const last = rememberVariant({}, 'zone', tools);
    expect(last).toEqual({ box: 'zone' });
    expect(capsuleEntries(tools, last).find((e) => e.key === 'box')?.tool.id).toBe('zone');
  });

  it('без змін — той самий об’єкт (ефект панелі не зациклюється)', () => {
    const last = { box: 'zone' as const };
    expect(rememberVariant(last, 'zone', tools)).toBe(last);
    expect(rememberVariant(last, 'fib', tools)).toBe(last);
    expect(rememberVariant(last, null, tools)).toBe(last);
  });
});
