// src/chart/drawings/drawingsStorage.test.ts — ADR-0111 §3.2 (I5): збереження малювань без тихих втрат.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { corruptBackupKey, isCorruptBackupKey, loadDrawings, saveDrawings } from './drawingsStorage';
import type { Drawing } from '../../types';

const KEY = 'v4_drawings_XAU/USD';
const TREND: Drawing = {
  id: 'aaaaaaaa-0000-4000-8000-000000000001',
  type: 'trend',
  points: [
    { t_ms: 1791800100000 as Drawing['points'][number]['t_ms'], price: 4150 },
    { t_ms: 1791812700000 as Drawing['points'][number]['t_ms'], price: 4180 },
  ],
};

function memoryStorage(failOnSet = false) {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (failOnSet) throw new Error('QuotaExceededError');
      store.set(k, v);
    },
  });
  return store;
}

describe('saveDrawings / loadDrawings', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('збережене читається назад без проблем; ключа нема — порожньо і без проблеми', () => {
    memoryStorage();
    expect(saveDrawings(KEY, [TREND])).toBeNull();
    expect(loadDrawings(KEY, 1)).toEqual({ drawings: [TREND], problem: null });
    expect(loadDrawings('v4_drawings_NAS100', 1)).toEqual({ drawings: [], problem: null });
  });

  it('квота — причина повертається (а не мовчанка)', () => {
    memoryStorage(true);
    expect(saveDrawings(KEY, [TREND])).toMatch(/QuotaExceededError/);
  });

  it('битий JSON — копія сирого рядка в окремий ключ до перезапису і причина', () => {
    const store = memoryStorage();
    store.set(KEY, '[{"id":');
    const loaded = loadDrawings(KEY, 1791800000000);
    expect(loaded.drawings).toEqual([]);
    expect(loaded.problem).toMatch(/__corrupt_1791800000000/);
    expect(store.get(`${KEY}__corrupt_1791800000000`)).toBe('[{"id":');
    // основний ключ — порожній масив: наступне перезавантаження не плодить нову копію
    expect(store.get(KEY)).toBe('[]');
    expect(loadDrawings(KEY, 1791800000001)).toEqual({ drawings: [], problem: null });
    expect([...store.keys()].filter((k) => k.includes('__corrupt_'))).toHaveLength(1);
  });

  it('копію зберегти не вдалося — основний ключ лишається як був', () => {
    const store = new Map<string, string>([[KEY, '[{"id":']]);
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    });
    expect(loadDrawings(KEY, 7).problem).toMatch(/копію зберегти не вдалося/);
    expect(store.get(KEY)).toBe('[{"id":');
  });

  it('не масив — теж зберігаємо копію', () => {
    const store = memoryStorage();
    store.set(KEY, '{"id":"x"}');
    expect(loadDrawings(KEY, 5).problem).toMatch(/not an array/);
    expect(store.get(`${KEY}__corrupt_5`)).toBe('{"id":"x"}');
  });

  it('копію битого впізнає міграція legacy per-TF — і не плутає з legacy-ключем (D-1)', () => {
    const store = memoryStorage();
    store.set(KEY, '{"id":"x"}');
    loadDrawings(KEY, 9);
    const backup = [...store.keys()].find((k) => k !== KEY);
    expect(backup).toBe(corruptBackupKey(KEY, 9));
    // копія підпадає під префікс legacy `v4_drawings_{symbol}_` — тому окремий предикат
    expect(backup!.startsWith(`${KEY}_`)).toBe(true);
    expect(isCorruptBackupKey(backup!)).toBe(true);
    expect(isCorruptBackupKey(`${KEY}_900`)).toBe(false);
    expect(isCorruptBackupKey(KEY)).toBe(false);
  });

  it('сховище недоступне (приватний режим) — без винятку, з причиною', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => {
        throw new Error('SecurityError');
      },
    });
    expect(loadDrawings(KEY, 1)).toEqual({ drawings: [], problem: 'read: Error: SecurityError' });
    expect(saveDrawings(KEY, [])).toMatch(/SecurityError/);
  });
});
