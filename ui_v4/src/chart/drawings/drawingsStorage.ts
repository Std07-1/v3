// src/chart/drawings/drawingsStorage.ts
// ADR-0111 §3.2 (I5, аудит 12.10.2026): збереження малювань у localStorage без тихих втрат. Раніше помилка квоти
// ковталась мовчки (фігури зникали після перезавантаження), а битий JSON ставав [] і перетирався першою ж новою
// фігурою. Винесено з DrawingsRenderer (X33 — файл під межею 1500 рядків); I/O лише через глобальний localStorage
// всередині try (доступ до нього кидає в приватному режимі / sandbox).

import type { Drawing } from '../../types';

const CORRUPT_BACKUP_MARKER = '__corrupt_';

/** Ключ копії битого рядка. Одне джерело з `isCorruptBackupKey`: копія підпадає під префікс legacy per-TF сторів
 *  (`v4_drawings_{symbol}_`), і міграція ADR-0082 D4 без цієї перевірки злила б її й видалила (рецензія 12.10, D-1). */
export function corruptBackupKey(key: string, nowMs: number): string {
  return `${key}${CORRUPT_BACKUP_MARKER}${nowMs}`;
}

export function isCorruptBackupKey(key: string): boolean {
  return key.includes(CORRUPT_BACKUP_MARKER);
}

export interface LoadedDrawings {
  drawings: Drawing[];
  /** null — прочитано чисто (або ключа нема); інакше причина для гучного попередження. */
  problem: string | null;
}

/** Записує фігури символу; null — успіх, інакше причина (квота, приватний режим). */
export function saveDrawings(key: string, drawings: readonly Drawing[]): string | null {
  try {
    localStorage.setItem(key, JSON.stringify(drawings));
    return null;
  } catch (err) {
    return String(err);
  }
}

/** Читає фігури символу. Битий JSON або не масив — копія сирого рядка в `<key>__corrupt_<nowMs>` ДО того, як
 *  наступне збереження перетре основний ключ, і причина в `problem`; фігур тоді нема (як і раніше). Після вдалої
 *  копії основний ключ стає `[]` — інакше кожне перезавантаження плодило б нову копію того самого рядка; копію
 *  зберегти не вдалося — основний ключ лишається як був, але перша ж нова фігура його перезапише (як і до 12.10;
 *  тепер — з попередженням). */
export function loadDrawings(key: string, nowMs: number): LoadedDrawings {
  let raw: string | null;
  try {
    raw = localStorage.getItem(key);
  } catch (err) {
    return { drawings: [], problem: `read: ${String(err)}` };
  }
  if (!raw) return { drawings: [], problem: null };

  let parsed: unknown = null;
  let reason: string | null = null;
  try {
    parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) reason = 'not an array';
  } catch (err) {
    reason = String(err);
  }
  if (reason === null) return { drawings: parsed as Drawing[], problem: null };

  const backupKey = corruptBackupKey(key, nowMs);
  try {
    localStorage.setItem(backupKey, raw);
  } catch {
    return { drawings: [], problem: `${reason}; копію зберегти не вдалося` };
  }
  try {
    localStorage.setItem(key, '[]');
  } catch {
    /* копія вже є; основний ключ лишиться битим до наступного збереження — лише повторне попередження */
  }
  return { drawings: [], problem: `${reason}; копія сирого рядка — ${backupKey}` };
}
