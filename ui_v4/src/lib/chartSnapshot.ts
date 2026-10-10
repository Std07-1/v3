// src/lib/chartSnapshot.ts
// Знімок графіка (ADR-0109, крок 1 — лише в браузері): PNG з тим, що трейдер бачить на графіку, — свічки й шкали
// (LWC), шар SMC і власні малювання — плюс рядок «символ · TF · час UTC · ціна» над графіком і знак V3 у закріпленому
// слоті ADR-0068 (зліва внизу над шкалою часу). Наратив, шар Арчі, HUD і перехрестя на знімок не йдуть (рішення
// власника 10.10). Ціну й точність UI не рахує (X28): ціна — остання з кадру, знаки — з config сервера.

import { displayPriceDigits } from './priceDigits';

/** Що бачить трейдер у мить знімка: символ і TF графіка, час (UTC мс) і остання ціна кадру. */
export interface SnapshotMeta {
  symbol: string;
  tf: string;
  utcMs: number;
  price: number | null;
  priceDigits: number | null;
}

/** Спосіб віддати знімок: системне меню «Поділитися» (телефон), буфер обміну, файл. */
export type SnapshotDelivery = 'share' | 'clipboard' | 'download';

/** Можливості браузера, від яких залежить спосіб (перевіряє `browserSnapshotCaps`). */
export interface SnapshotCaps {
  canShareFiles: boolean;
  canWriteClipboard: boolean;
  coarsePointer: boolean;
}

/** Геометрія оформлення в CSS-пікселях; на полотні множиться на масштаб знімка (DPR графіка). */
export const SNAPSHOT_LAYOUT = {
  headerHeightPx: 30,
  headerPadXPx: 12,
  headerFontPx: 13,
  // Слот знака як у BrandWatermark (ADR-0068: десктоп bottom 36 / left 12, телефон bottom 30 / left 6, розмір 20)
  markSizePx: 20,
  markLeftPx: 12,
  markBottomPx: 36,
  markLeftMobilePx: 6,
  markBottomMobilePx: 30,
  markOpacity: 0.62,
} as const;

/** Віртуальна рамка знімка (рішення власника 10.10 «робимо обидва»): вузький графік (телефон, планшет у портреті)
 *  знімається так, ніби він 1280×720 CSS — горизонтальна картинка з більшою кількістю свічок, у щільності пристрою.
 *  Широкий графік (ПК) — у своєму розмірі, як на екрані. */
export const SNAPSHOT_FRAME = {
  widthPx: 1280,
  heightPx: 720,
  minChartWidthPx: 1000,
} as const;

/** Рамка для знімка графіка `cssW`×`cssH`; null — знімати у власному розмірі. */
export function snapshotFrame(cssW: number, cssH: number): { width: number; height: number } | null {
  if (cssW <= 0 || cssH <= 0 || cssW >= SNAPSHOT_FRAME.minChartWidthPx) return null;
  return { width: SNAPSHOT_FRAME.widthPx, height: SNAPSHOT_FRAME.heightPx };
}

/** Копія полотна (знімок шару в ту мить, поки полотно ще в розмірі знімка). */
export function copyCanvas(source: HTMLCanvasElement): HTMLCanvasElement {
  const copy = document.createElement('canvas');
  copy.width = source.width;
  copy.height = source.height;
  copy.getContext('2d')?.drawImage(source, 0, 0);
  return copy;
}

const _pad2 = (n: number): string => String(n).padStart(2, '0');

function _utcParts(utcMs: number): { date: string; time: string; compact: string } {
  const d = new Date(utcMs);
  const date = `${d.getUTCFullYear()}-${_pad2(d.getUTCMonth() + 1)}-${_pad2(d.getUTCDate())}`;
  const time = `${_pad2(d.getUTCHours())}:${_pad2(d.getUTCMinutes())}`;
  return { date, time, compact: `${date.replace(/-/g, '')}-${time.replace(':', '')}` };
}

/** Рядок над графіком: «XAU/USD · H1 · 2026-10-10 14:30 UTC · 4195.48» (ціни нема — без неї). */
export function snapshotHeaderText(meta: SnapshotMeta): string {
  const { date, time } = _utcParts(meta.utcMs);
  const parts = [meta.symbol, meta.tf, `${date} ${time} UTC`];
  if (meta.price !== null && Number.isFinite(meta.price)) {
    parts.push(meta.price.toFixed(displayPriceDigits(meta.priceDigits, meta.price)));
  }
  return parts.join(' · ');
}

/** Ім'я файла: «v3_XAUUSD_H1_20261010-1430UTC.png» — лише латиниця, цифри, `_` і `-`. */
export function snapshotFileName(meta: SnapshotMeta): string {
  const symbol = meta.symbol.replace(/[^A-Za-z0-9]/g, '');
  const tf = meta.tf.replace(/[^A-Za-z0-9]/g, '');
  return `v3_${symbol}_${tf}_${_utcParts(meta.utcMs).compact}UTC.png`;
}

/** Спосіб(и) доставки: телефон із системним «Поділитися» — меню; ПК — у буфер і файлом; інакше — файл. */
export function chooseDelivery(caps: SnapshotCaps): SnapshotDelivery[] {
  if (caps.coarsePointer && caps.canShareFiles) return ['share'];
  return caps.canWriteClipboard ? ['clipboard', 'download'] : ['download'];
}

/** Можливості цього браузера для `chooseDelivery`; `probe` — файл, яким перевіряється `navigator.canShare`. */
export function browserSnapshotCaps(probe: File): SnapshotCaps {
  const nav = typeof navigator !== 'undefined' ? navigator : undefined;
  let canShareFiles = false;
  try {
    canShareFiles = !!nav?.canShare && nav.canShare({ files: [probe] });
  } catch {
    canShareFiles = false; // canShare кидає на незнайомих полях у старих браузерах — тоді просто без меню
  }
  return {
    canShareFiles,
    canWriteClipboard: !!nav?.clipboard?.write && typeof ClipboardItem !== 'undefined',
    coarsePointer: typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches,
  };
}

/** Колір теми з CSS-змінної (`--bg`, `--text-1`); нема — запасний. */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export interface ComposeInput {
  /** Свічки й шкали LWC (`takeScreenshot`) — задає розмір і масштаб знімка. */
  chart: HTMLCanvasElement;
  /** Полотна поверх графіка в порядку малювання (SMC, малювання); розтягуються на розмір `chart`. */
  layers: HTMLCanvasElement[];
  /** Ширина графіка в CSS-пікселях: масштаб = chart.width / cssWidth. */
  cssWidth: number;
  header: string;
  mark: CanvasImageSource | null;
  mobile: boolean;
  background: string;
  textColor: string;
  fontFamily: string;
}

/** Збирає знімок: тло теми, рядок зверху, графік, шари поверх, знак V3 у слоті ADR-0068. */
export function composeSnapshot(input: ComposeInput): HTMLCanvasElement {
  const L = SNAPSHOT_LAYOUT;
  const scale = input.cssWidth > 0 ? input.chart.width / input.cssWidth : 1;
  const headerH = Math.round(L.headerHeightPx * scale);
  const out = document.createElement('canvas');
  out.width = input.chart.width;
  out.height = input.chart.height + headerH;
  const ctx = out.getContext('2d');
  if (!ctx) throw new Error('snapshot_canvas_2d_unavailable');

  ctx.fillStyle = input.background; // тема dark дає LWC прозоре тло — без заливки PNG вийшов би прозорим
  ctx.fillRect(0, 0, out.width, out.height);

  ctx.fillStyle = input.textColor;
  ctx.font = `600 ${Math.round(L.headerFontPx * scale)}px ${input.fontFamily}`;
  ctx.textBaseline = 'middle';
  ctx.fillText(input.header, Math.round(L.headerPadXPx * scale), Math.round(headerH / 2));

  ctx.drawImage(input.chart, 0, headerH);
  for (const layer of input.layers) {
    if (layer.width > 0 && layer.height > 0) {
      ctx.drawImage(layer, 0, headerH, input.chart.width, input.chart.height);
    }
  }

  if (input.mark) {
    const size = Math.round(L.markSizePx * scale);
    const left = Math.round((input.mobile ? L.markLeftMobilePx : L.markLeftPx) * scale);
    const bottom = Math.round((input.mobile ? L.markBottomMobilePx : L.markBottomPx) * scale);
    ctx.globalAlpha = L.markOpacity;
    ctx.drawImage(input.mark, left, out.height - bottom - size, size, size);
    ctx.globalAlpha = 1;
  }
  return out;
}

/** PNG-blob полотна; `toBlob` повернув null — помилка, а не тихий порожній знімок (I5). */
export function canvasToPng(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('snapshot_png_encode_failed'))), 'image/png');
  });
}

/** Результат доставки: що спрацювало і що ні (UI показує підказку, збій — у консоль з причиною). */
export interface DeliveryResult {
  done: SnapshotDelivery[];
  failed: { via: SnapshotDelivery; error: string }[];
  cancelled: boolean;
}

/** Короткий підсумок для пігулки біля кнопки. */
export function snapshotResultLabel(result: DeliveryResult): string {
  const done = new Set(result.done);
  if (done.has('share')) return 'Надіслано';
  if (done.has('clipboard') && done.has('download')) return 'Скопійовано й збережено';
  if (done.has('clipboard')) return 'Скопійовано';
  if (done.has('download')) return 'Збережено у файл';
  if (result.cancelled) return 'Скасовано';
  return 'Не вдалося віддати знімок';
}

/** Віддає знімок обраними способами по черзі; збій одного способу не скасовує наступний. */
export async function deliverSnapshot(png: Blob, fileName: string, ways: SnapshotDelivery[]): Promise<DeliveryResult> {
  const result: DeliveryResult = { done: [], failed: [], cancelled: false };
  for (const via of ways) {
    try {
      if (via === 'share') {
        await navigator.share({ files: [new File([png], fileName, { type: 'image/png' })] });
      } else if (via === 'clipboard') {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      } else {
        const url = URL.createObjectURL(png);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      result.done.push(via);
    } catch (err) {
      if (via === 'share' && err instanceof DOMException && err.name === 'AbortError') {
        result.cancelled = true; // людина закрила системне меню — не помилка
        continue;
      }
      result.failed.push({ via, error: err instanceof Error ? `${err.name}: ${err.message}` : String(err) });
    }
  }
  return result;
}
