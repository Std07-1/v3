// src/lib/chartSnapshot.test.ts — знімок графіка (ADR-0109, крок 1): текст, ім'я файла, спосіб доставки, підсумок
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  chooseDelivery,
  deliverSnapshot,
  loadSnapshotWide,
  saveSnapshotWide,
  snapshotFileName,
  snapshotFrame,
  snapshotHeaderText,
  snapshotSupersample,
  snapshotResultLabel,
  snapshotUsesPreview,
  type SnapshotMeta,
} from './chartSnapshot';

const AT_1430 = Date.UTC(2026, 9, 10, 14, 30, 59);
const META: SnapshotMeta = { symbol: 'XAU/USD', tf: 'H1', utcMs: AT_1430, price: 4195.48, priceDigits: 2 };

describe('snapshotHeaderText', () => {
  it('символ · TF · час UTC · ціна зі знаками з конфігу', () => {
    expect(snapshotHeaderText(META)).toBe('XAU/USD · H1 · 2026-10-10 14:30 UTC · 4195.48');
    expect(snapshotHeaderText({ ...META, symbol: 'USD/JPY', price: 158.267, priceDigits: 3 })).toBe(
      'USD/JPY · H1 · 2026-10-10 14:30 UTC · 158.267',
    );
  });

  it('ціни ще нема — рядок без неї, а не «null»', () => {
    expect(snapshotHeaderText({ ...META, price: null })).toBe('XAU/USD · H1 · 2026-10-10 14:30 UTC');
  });

  it('символ без знаків у конфігу — запасна точність за величиною (як HUD)', () => {
    expect(snapshotHeaderText({ ...META, price: 60.6671, priceDigits: null })).toMatch(/· 60\.667$/);
  });
});

describe('snapshotFileName', () => {
  it('лише латиниця, цифри, _ і - (без «/» символу)', () => {
    expect(snapshotFileName(META)).toBe('v3_XAUUSD_H1_20261010-1430UTC.png');
  });
});

describe('snapshotFrame', () => {
  it.each([
    [375, 786, { width: 1280, height: 720 }], // телефон портрет — горизонтальна рамка, більше свічок
    [844, 340, { width: 1280, height: 720 }], // телефон ландшафт
    [768, 900, { width: 1280, height: 720 }], // планшет портрет
    [1024, 768, null], // ПК / широкий — як на екрані
    [1920, 900, null],
    [0, 0, null], // графік ще не змонтований
  ] as const)('%d×%d → %j', (w, h, frame) => {
    expect(snapshotFrame(w, h)).toEqual(frame);
  });
});

describe('snapshotUsesPreview', () => {
  it.each([
    [375, 786, true, true], // телефон — передогляд з «Широкий»
    [768, 900, true, true], // планшет у портреті
    [1024, 768, true, false], // планшет у ландшафті — графік уже широкий
    [800, 700, false, false], // ПК у вузькому вікні — як на екрані, без вікна
    [1920, 900, false, false],
  ] as const)('%d×%d дотик=%s → %s', (w, h, coarse, preview) => {
    expect(snapshotUsesPreview(w, h, coarse)).toBe(preview);
  });
});

describe('snapshotSupersample', () => {
  it.each([
    [1, 2], // звичайний монітор — удвічі більше пікселів
    [1.25, 1.6], // ноутбук з масштабом 125%
    [2, 1], // ретіна / телефон — уже 2×
    [3, 1],
    [0, 2], // кривий DPR — як 1
  ])('DPR %d → ×%d', (dpr, k) => {
    expect(snapshotSupersample(dpr)).toBeCloseTo(k, 6);
  });
});

describe('loadSnapshotWide / saveSnapshotWide', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('за замовчуванням широкий; вибір запам\'ятовується', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    });
    expect(loadSnapshotWide()).toBe(true);
    saveSnapshotWide(false);
    expect(loadSnapshotWide()).toBe(false);
    saveSnapshotWide(true);
    expect(loadSnapshotWide()).toBe(true);
  });

  it('сховище недоступне — широкий і без винятку', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
    });
    expect(loadSnapshotWide()).toBe(true);
    expect(() => saveSnapshotWide(false)).not.toThrow();
  });
});

describe('chooseDelivery', () => {
  it('телефон із системним «Поділитися» — лише меню', () => {
    expect(chooseDelivery({ canShareFiles: true, canWriteClipboard: true, coarsePointer: true })).toEqual(['share']);
  });

  it('ПК — у буфер і файлом, навіть коли браузер уміє share', () => {
    expect(chooseDelivery({ canShareFiles: true, canWriteClipboard: true, coarsePointer: false })).toEqual([
      'clipboard',
      'download',
    ]);
  });

  it('без буфера (Firefox без ClipboardItem) і без меню — лише файл', () => {
    expect(chooseDelivery({ canShareFiles: false, canWriteClipboard: false, coarsePointer: true })).toEqual([
      'download',
    ]);
  });
});

describe('snapshotResultLabel', () => {
  it.each([
    [{ done: ['share'], failed: [], cancelled: false }, 'Надіслано'],
    [{ done: ['clipboard', 'download'], failed: [], cancelled: false }, 'Скопійовано й збережено'],
    [{ done: ['download'], failed: [{ via: 'clipboard', error: 'x' }], cancelled: false }, 'Збережено у файл'],
    [{ done: [], failed: [], cancelled: true }, 'Скасовано'],
    [{ done: [], failed: [{ via: 'download', error: 'x' }], cancelled: false }, 'Не вдалося віддати знімок'],
  ] as const)('%j → %s', (result, label) => {
    expect(snapshotResultLabel({ ...result, done: [...result.done], failed: [...result.failed] })).toBe(label);
  });
});

describe('deliverSnapshot', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('закрите людиною меню «Поділитися» — скасування, не помилка', async () => {
    const abort = Object.assign(new Error('closed'), { name: 'AbortError' });
    vi.stubGlobal('DOMException', class extends Error {});
    Object.setPrototypeOf(abort, (globalThis as any).DOMException.prototype);
    vi.stubGlobal('navigator', { share: vi.fn().mockRejectedValue(abort) });
    const result = await deliverSnapshot(new Blob(['png']), 'v3.png', ['share']);
    expect(result).toEqual({ done: [], failed: [], cancelled: true });
  });

  it('збій буфера не скасовує файл, і причина лишається в результаті', async () => {
    const click = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { write: vi.fn().mockRejectedValue(new Error('denied')) } });
    vi.stubGlobal('ClipboardItem', class {
      constructor(public items: unknown) {}
    });
    vi.stubGlobal('document', { createElement: () => ({ click, href: '', download: '' }) });
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    const result = await deliverSnapshot(new Blob(['png']), 'v3.png', ['clipboard', 'download']);
    expect(result.done).toEqual(['download']);
    expect(result.failed).toEqual([{ via: 'clipboard', error: 'Error: denied' }]);
    expect(click).toHaveBeenCalledOnce();
  });
});
