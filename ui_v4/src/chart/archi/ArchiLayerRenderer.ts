// src/chart/archi/ArchiLayerRenderer.ts
// ADR-0085 P2/P3: read-only шар Арчі на чарті — числові будильники з wire
// (frame.archi_chart, ADR-0085 D1) → лінії/зони на ціновій осі.
//
// СВІДОМО окремий від DrawingsRenderer: інший життєвий цикл (frame-driven,
// не localStorage), нуль interaction (pointer-events:none, без hit-test у v1),
// нуль команд/undo. Джерело істини = wire; Арчі-об'єкти НІКОЛИ не потрапляють
// у user-стор (ADR-0085 §6.7).
//
// Візуальна мова (D3, owner: --info синій): dash-dot «сигнальний дріт» для
// рівнів, напівпрозора смуга для зон, лейбл «⏰ 4380 · Арчі» (halo-техніка
// ADR-0079), freshness-альфа (старі будильники тьмяніють, нічого не миготить).
// Pane-clip: шар не заходить на цінову шкалу/часову вісь (патерн ADR-0084).

import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import type { ArchiChartCondition, ArchiChartData } from '../../types';

// Dash-dot: «сигнальний дріт» — відрізняється і від SMC-levels, і від
// user-стилів (solid/dashed/dotted) → лінію Арчі впізнаєш миттєво.
const DASH_DOT = [7, 3, 2, 3];

// Freshness (дзеркалить пороги NarrativeEnricher: <1г fresh, 1-4г aging).
const FRESH_H = 1;
const AGING_H = 4;
const ALPHA_FRESH = 1.0;
const ALPHA_AGING = 0.7;
const ALPHA_STALE = 0.45;

const ZONE_FILL_ALPHA = 0.06;
const ZONE_BORDER_ALPHA = 0.4;
const LABEL_FONT = '10px ui-monospace, SFMono-Regular, Consolas, monospace';

// TF-лейбл для candle_close (config tf_allowlist — 8 значень, локальна мапа).
const TF_LABELS: Record<number, string> = {
  60: 'M1', 180: 'M3', 300: 'M5', 900: 'M15',
  1800: 'M30', 3600: 'H1', 14400: 'H4', 86400: 'D1',
};

/** rgba з hex-токена (canvas не читає CSS var/color-mix). */
function withAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return `rgba(84,135,255,${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

function fmtPrice(level: number): string {
  return level.toFixed(2).replace(/\.00$/, '');
}

function freshnessAlpha(createdAtMs: number): number {
  if (!createdAtMs) return ALPHA_STALE;
  const ageH = (Date.now() - createdAtMs) / 3_600_000;
  if (ageH < FRESH_H) return ALPHA_FRESH;
  if (ageH < AGING_H) return ALPHA_AGING;
  return ALPHA_STALE;
}

export class ArchiLayerRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly chartApi: IChartApi;
  private readonly seriesApi: ISeriesApi<'Candlestick'>;

  private data: ArchiChartData | null = null;
  private visible = true; // P6: ☰-toggle «Арчі на чарті» (default ON — owner)

  private dpr = 1;
  private rafId: number | null = null;
  private ro: ResizeObserver;
  private onVisibleRangeChange: () => void;

  // Кольори з токенів (кеш; canvas не читає var() — патерн DrawingsRenderer).
  private infoColor = '#5487FF';

  constructor(
    canvas: HTMLCanvasElement,
    chartApi: IChartApi,
    seriesApi: ISeriesApi<'Candlestick'>,
  ) {
    this.canvas = canvas;
    this.chartApi = chartApi;
    this.seriesApi = seriesApi;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('ArchiLayerRenderer: неможливо отримати 2D контекст');
    this.ctx = ctx;
    this.canvas.style.pointerEvents = 'none'; // read-only шар, без interaction

    // DPR-rail (патерн DrawingsRenderer)
    this.ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.floor(width * this.dpr);
      this.canvas.height = Math.floor(height * this.dpr);
      this.canvas.style.width = `${Math.floor(width)}px`;
      this.canvas.style.height = `${Math.floor(height)}px`;
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.scheduleRender();
    });
    if (this.canvas.parentElement) this.ro.observe(this.canvas.parentElement);

    this.onVisibleRangeChange = () => this.renderSync();
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(this.onVisibleRangeChange);

    this.refreshThemeColors();
  }

  /** frame.archi_chart: present → застосувати (порожній = очистити);
   *  null = очистити явно (full frame без поля / вимкнено). */
  setData(data: ArchiChartData | null): void {
    this.data = data;
    this.scheduleRender();
  }

  /** P6: клієнтський show/hide (☰ «Арчі на чарті»), без перерахунку даних. */
  setVisible(v: boolean): void {
    this.visible = v;
    this.scheduleRender();
  }

  /** Тема змінилась — перечитати токени (кличе ChartPane applyTheme-шлях). */
  refreshThemeColors(): void {
    const s = getComputedStyle(this.canvas);
    this.infoColor = s.getPropertyValue('--info').trim() || '#5487FF';
    this.scheduleRender();
  }

  /** ADR-0008-патерн: Y-вісь змінилась (з interaction.ts callback). */
  notifyPriceRangeChanged(): void {
    this.scheduleRender();
  }

  private scheduleRender(): void {
    if (this.rafId !== null) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = null;
      this.forceRender();
    });
  }

  private renderSync(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.forceRender();
  }

  private forceRender(): void {
    const canvasW = this.canvas.width / this.dpr;
    const canvasH = this.canvas.height / this.dpr;
    this.ctx.clearRect(0, 0, canvasW, canvasH);

    const conds = this.data?.conditions;
    if (!this.visible || !conds || conds.length === 0) return;

    // Pane-clip (ADR-0084): шар не заходить на шкали. Fallback → повний canvas.
    let paneW = canvasW;
    let paneH = canvasH;
    try {
      const psw = this.chartApi.priceScale('right').width();
      const tsh = this.chartApi.timeScale().height();
      if (psw > 0 && psw < canvasW) paneW = canvasW - psw;
      if (tsh > 0 && tsh < canvasH) paneH = canvasH - tsh;
    } catch { /* до першого layout LWC */ }

    this.ctx.save();
    this.ctx.beginPath();
    this.ctx.rect(0, 0, paneW, paneH);
    this.ctx.clip();

    for (const c of conds) {
      const alpha = freshnessAlpha(c.created_at_ms);
      if ((c.kind === 'price_cross' || c.kind === 'candle_close') && c.level != null) {
        this.renderLevel(c, paneW, alpha);
      } else if (c.kind === 'price_zone_touch' && c.zone_high != null && c.zone_low != null) {
        this.renderZone(c, paneW, alpha);
      }
    }

    this.ctx.restore();
  }

  private renderLevel(c: ArchiChartCondition, paneW: number, alpha: number): void {
    const y = this.seriesApi.priceToCoordinate(c.level!);
    if (y === null) return;
    const ctx = this.ctx;
    ctx.strokeStyle = withAlpha(this.infoColor, alpha);
    ctx.lineWidth = 1;
    ctx.setLineDash(DASH_DOT);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(paneW, y);
    ctx.stroke();
    ctx.setLineDash([]);

    const tf = c.kind === 'candle_close' && c.tf_s ? TF_LABELS[c.tf_s] ?? '' : '';
    this.renderLabel(`⏰ ${fmtPrice(c.level!)}${tf ? ` ${tf}` : ''} · Арчі`, paneW, y, alpha);
  }

  private renderZone(c: ArchiChartCondition, paneW: number, alpha: number): void {
    const yHigh = this.seriesApi.priceToCoordinate(c.zone_high!);
    const yLow = this.seriesApi.priceToCoordinate(c.zone_low!);
    if (yHigh === null || yLow === null) return;
    const top = Math.min(yHigh, yLow);
    const h = Math.abs(yLow - yHigh);
    const ctx = this.ctx;
    ctx.fillStyle = withAlpha(this.infoColor, ZONE_FILL_ALPHA * alpha);
    ctx.fillRect(0, top, paneW, h);
    ctx.strokeStyle = withAlpha(this.infoColor, ZONE_BORDER_ALPHA * alpha);
    ctx.lineWidth = 1;
    ctx.setLineDash(DASH_DOT);
    ctx.beginPath();
    ctx.moveTo(0, top);
    ctx.lineTo(paneW, top);
    ctx.moveTo(0, top + h);
    ctx.lineTo(paneW, top + h);
    ctx.stroke();
    ctx.setLineDash([]);

    this.renderLabel(`⏰ зона ${fmtPrice(c.zone_low!)}–${fmtPrice(c.zone_high!)} · Арчі`, paneW, top, alpha);
  }

  /** Лейбл праворуч біля краю пани; halo-тінь (техніка delete-× ADR-0079) —
   *  читабельний на свічках будь-якої теми без фонової плашки. */
  private renderLabel(text: string, paneW: number, y: number, alpha: number): void {
    const ctx = this.ctx;
    ctx.font = LABEL_FONT;
    const w = ctx.measureText(text).width;
    const x = Math.max(4, paneW - w - 8);
    const ly = Math.max(10, y - 5);
    ctx.shadowColor = 'rgba(0, 0, 0, 0.7)';
    ctx.shadowBlur = 3;
    ctx.fillStyle = withAlpha(this.infoColor, Math.min(1, alpha + 0.15));
    ctx.fillText(text, x, ly);
    ctx.shadowBlur = 0;
  }

  destroy(): void {
    this.ro.disconnect();
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);
    this.chartApi.timeScale().unsubscribeVisibleTimeRangeChange(this.onVisibleRangeChange);
  }
}
