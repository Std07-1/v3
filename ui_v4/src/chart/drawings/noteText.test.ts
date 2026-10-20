// src/chart/drawings/noteText.test.ts — ADR-0111 §3.7: текст нотатки з поля вводу і подвійний клік по ній.
import { describe, expect, it } from 'vitest';
import { createDoubleClickTracker, NOTE_MAX_CHARS, normalizeNoteText } from './noteText';

describe('normalizeNoteText', () => {
  it('один рядок: переноси й зайві пробіли — один пробіл, краї обрізано', () => {
    expect(normalizeNoteText('  H4 OB\n чекаю   зняття  ')).toBe('H4 OB чекаю зняття');
  });

  it('порожній чи лише пробіли — null (нотатки нема, текст не змінюється)', () => {
    expect(normalizeNoteText('')).toBeNull();
    expect(normalizeNoteText(' \n\t ')).toBeNull();
  });

  it('довгий — до NOTE_MAX_CHARS символів; емодзі не рветься навпіл', () => {
    expect(normalizeNoteText('x'.repeat(80))).toHaveLength(NOTE_MAX_CHARS);
    const cut = normalizeNoteText('🎯'.repeat(70))!;
    expect(Array.from(cut)).toHaveLength(NOTE_MAX_CHARS);
    expect(cut.endsWith('🎯')).toBe(true);
  });
});

describe('createDoubleClickTracker', () => {
  it('другий клік тієї самої нотатки за 400 мс — подвійний', () => {
    const isDouble = createDoubleClickTracker();
    expect(isDouble('n1', 1000)).toBe(false);
    expect(isDouble('n1', 1300)).toBe(true);
  });

  it('пізно, інша нотатка або клік повз — не подвійний', () => {
    const isDouble = createDoubleClickTracker();
    isDouble('n1', 1000);
    expect(isDouble('n1', 1500)).toBe(false);
    expect(isDouble('n2', 1600)).toBe(false);
    expect(isDouble(null, 1700)).toBe(false);
    expect(isDouble('n2', 1800)).toBe(false);
  });

  it('після подвійного — з нуля: третій клік поспіль не відкриває поле вдруге', () => {
    const isDouble = createDoubleClickTracker();
    isDouble('n1', 1000);
    expect(isDouble('n1', 1200)).toBe(true);
    expect(isDouble('n1', 1300)).toBe(false);
  });
});
