// src/chart/overlay/OverlayRenderer.ts
// Коментарі українською. Без silent fallback. Screen-space рендер.

import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import type { SmcData, SmcZone, SmcLevel, SmcSwing, UiWarning } from '../../types';

type HorzScaleItem = number | { year: number; month: number; day: number };

function timeToSec(time: HorzScaleItem): number {
  if (typeof time === 'number') return time;
  return Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0, 0) / 1000;
}

function normalizeSmcData(d?: SmcData | null): Required<SmcData> {
  return {
    zones: d?.zones ?? [],
    swings: d?.swings ?? [],
    levels: d?.levels ?? [],
  };
}

export type OverlayRendererOptions = {
  onUiWarning?: (w: UiWarning) => void;
  warningThrottleMs?: number;
};

export class OverlayRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;

  private readonly chartApi: IChartApi;
  private readonly seriesApi: ISeriesApi<'Candlestick'>;

  private frame: Required<SmcData> = { zones: [], swings: [], levels: [] };

  private dpr = 1;
  private cssW = 0;
  private cssH = 0;

  private rafPending = false;

  private readonly onUiWarning?: (w: UiWarning) => void;
  private readonly warningThrottleMs: number;
  private readonly warnLastTs = new Map<string, number>();

  constructor(
    canvas: HTMLCanvasElement,
    chartApi: IChartApi,
    seriesApi: ISeriesApi<'Candlestick'>,
    opts: OverlayRendererOptions = {},
  ) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('OverlayRenderer: неможливо отримати 2D контекст canvas');
    this.ctx = ctx;

    this.chartApi = chartApi;
    this.seriesApi = seriesApi;

    this.onUiWarning = opts.onUiWarning;
    this.warningThrottleMs = opts.warningThrottleMs ?? 1000;

    this.bindTriggers();
  }

  patch(overlays?: SmcData | null): void {
    this.frame = normalizeSmcData(overlays);
    this.scheduleRender();
  }

  resize(cssW: number, cssH: number, dpr: number): void {
    if (cssW <= 0 || cssH <= 0) return;

    this.cssW = cssW;
    this.cssH = cssH;
    this.dpr = dpr;

    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    this.canvas.style.width = `${cssW}px`;
    this.canvas.style.height = `${cssH}px`;

    // DPR rail
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    this.scheduleRender();
  }

  private bindTriggers(): void {
    this.chartApi.subscribeCrosshairMove(() => this.scheduleRender());
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(() => this.scheduleRender());
  }

  private warnOnce(key: string, w: UiWarning): void {
    if (!this.onUiWarning) return;
    const now = Date.now();
    const last = this.warnLastTs.get(key) ?? 0;
    if (now - last < this.warningThrottleMs) return;
    this.warnLastTs.set(key, now);
    this.onUiWarning(w);
  }

  private scheduleRender(): void {
    if (this.rafPending) return;
    this.rafPending = true;
    requestAnimationFrame(() => {
      this.rafPending = false;
      this.render();
    });
  }

  private toX(t_ms: number): number | null {
    return this.chartApi.timeScale().timeToCoordinate((t_ms / 1000) as import('lightweight-charts').Time);
  }

  private toY(price: number): number | null {
    // Rail: координата ціни тільки через seriesApi
    return this.seriesApi.priceToCoordinate(price);
  }

  private canTreatEndAsRightEdge(end_ms: number): boolean {
    const vr = this.chartApi.timeScale().getVisibleRange();
    if (!vr) return false;
    const toSec = timeToSec(vr.to as unknown as HorzScaleItem);
    return (end_ms / 1000) >= toSec;
  }

  private render(): void {
    if (this.cssW <= 0 || this.cssH <= 0) return;

    this.ctx.clearRect(0, 0, this.cssW, this.cssH);

    this.renderZones(this.frame.zones);
    this.renderLevels(this.frame.levels);
    this.renderSwings(this.frame.swings);
  }

  private zoneColor(kind: SmcZone['kind']): string {
    if (kind === 'fvg') return '#2ecc71';
    if (kind === 'ob') return '#e67e22';
    return '#9b59b6'; // liquidity
  }

  private renderZones(zones: SmcZone[]): void {
    const width = this.cssW;

    for (const z of zones) {
      const x1 = this.toX(z.start_ms);
      if (x1 === null) {
        this.warnOnce(`zone_x1_null:${z.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: z.id,
          details: 'zone.start_ms поза видимою областю або timeScale не готовий',
        });
        continue;
      }

      let xRight: number | null = null;

      if (z.end_ms == null) {
        xRight = width;
      } else {
        const x2 = this.toX(z.end_ms);
        if (x2 !== null) xRight = x2;
        else {
          if (this.canTreatEndAsRightEdge(z.end_ms)) xRight = width;
          else {
            this.warnOnce(`zone_x2_null:${z.id}`, {
              code: 'overlay_coord_null',
              kind: 'overlay',
              id: z.id,
              details: 'zone.end_ms поза видимою областю; зона пропущена',
            });
            continue;
          }
        }
      }

      if (xRight === null) continue;
      if (xRight <= x1 + 0.5) continue;

      const yTop = this.toY(z.high);
      const yBot = this.toY(z.low);

      if (yTop === null || yBot === null) {
        this.warnOnce(`zone_y_null:${z.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: z.id,
          details: 'zone.high/low поза price scale або серія не готова',
        });
        continue;
      }

      const top = Math.min(yTop, yBot);
      const h = Math.abs(yBot - yTop);
      const w = xRight - x1;

      const color = this.zoneColor(z.kind);

      this.ctx.save();
      this.ctx.globalAlpha = 0.10;
      this.ctx.fillStyle = color;
      this.ctx.fillRect(x1, top, w, h);

      this.ctx.globalAlpha = 0.35;
      this.ctx.strokeStyle = color;
      this.ctx.lineWidth = 1;
      this.ctx.strokeRect(x1, top, w, h);
      this.ctx.restore();
    }
  }

  private renderLevels(levels: SmcLevel[]): void {
    for (const lvl of levels) {
      const y = this.toY(lvl.price);

      if (y === null) {
        this.warnOnce(`level_y_null:${lvl.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: lvl.id,
          details: 'level.price поза price scale або серія не готова',
        });
        continue;
      }

      this.ctx.save();
      this.ctx.globalAlpha = 0.65;
      this.ctx.strokeStyle = lvl.color ?? '#f1c40f';
      this.ctx.lineWidth = 1;
      this.ctx.beginPath();
      this.ctx.moveTo(0, y);
      this.ctx.lineTo(this.cssW, y);
      this.ctx.stroke();
      this.ctx.restore();
    }
  }

  private renderSwings(swings: SmcSwing[]): void {
    for (const s of swings) {
      const x1 = this.toX(s.a.t_ms);
      const y1 = this.toY(s.a.price);
      const x2 = this.toX(s.b.t_ms);
      const y2 = this.toY(s.b.price);

      if (x1 === null || y1 === null || x2 === null || y2 === null) {
        this.warnOnce(`swing_xy_null:${s.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: s.id,
          details: 'swing координати поза видимою областю; пропущено',
        });
        continue;
      }

      this.ctx.save();
      this.ctx.globalAlpha = 0.55;
      this.ctx.strokeStyle = '#3498db';
      this.ctx.lineWidth = 1;
      this.ctx.beginPath();
      this.ctx.moveTo(x1, y1);
      this.ctx.lineTo(x2, y2);
      this.ctx.stroke();
      this.ctx.restore();
    }
  }
}
