// src/chart/overlay/OverlayRenderer.ts
// Коментарі українською. Без silent fallback. Screen-space рендер.
//
// ═══ LWC OVERLAY RENDER RULE (ADR-0024 §18.7) ═════════════════════
// LWC потребує >=2 RAF кадрів для price scale auto-fit
// після zoom/range change. Під час першого кадру
// priceToCoordinate() повертає STALE Y координати.
//
// ПРАВИЛО для БУДЬ-ЯКОГО canvas overlay на LWC:
// 1. crosshairMove          → синхронно (renderNow)
// 2. visibleTimeRangeChange  → double RAF (scheduleDoubleRaf)
// 3. visibleLogicalRangeChange → double RAF
// 4. patch/resize            → single RAF
// 5. Y-axis manual change    → double RAF
// НІКОЛИ не рендерити синхронно з range/zoom triggers!
// ═══════════════════════════════════════════════════
//
// ═══ LEVEL RENDERING RULES (ADR-0026) ══════════════════════════════
// L1: Підписи НІКОЛИ не ховаються — кожна група має видиму мітку
// L2: Merge тільки при фізичному overlap (|Y₁-Y₂|≤1px AND X-intersect)
// L3: Лінії НІКОЛИ не full-width (LINE_PX=120 або NOTCH_PX=20)
// L4: D1 kinds (pdh/pdl/dh/dl) мають пріоритет у sort
// L5: Max 12 видимих рівнів після priority sort
// L6: Per-kind кольори з LEVEL_STYLES dict (SSOT)
// ═══════════════════════════════════════════════════

import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import type { SmcData, SmcZone, SmcLevel, SmcSwing, UiWarning } from '../../types';

type HorzScaleItem = number | { year: number; month: number; day: number };

// ── ADR-0024b: Per-kind рівень стилізація ──────────────────────────
// Кольори розділені за TF-шаром: D1=orange, H4=purple, H1=blue, M30=teal, M15=cyan, EQ=red/green
type LevelStyle = { color: string; dash: number[]; width: number; alpha: number; label: string; fontSize: number };

// SMC-стандартні назви: PDH/PDL, HOD/LOD, Prev 4H Hi, 4H Hi, EQH/EQL
const LEVEL_STYLES: Record<string, LevelStyle> & { _default: LevelStyle } = {
  // D1 — orange (глобальний контекст)
  pdh:       { color: '#ff9800', dash: [6, 3], width: 1.5, alpha: 0.85, label: 'PDH',         fontSize: 9 },
  pdl:       { color: '#ff9800', dash: [6, 3], width: 1.5, alpha: 0.85, label: 'PDL',         fontSize: 9 },
  dh:        { color: '#ffb74d', dash: [3, 2], width: 1.0, alpha: 0.70, label: 'HOD',         fontSize: 10 },
  dl:        { color: '#ffb74d', dash: [3, 2], width: 1.0, alpha: 0.70, label: 'LOD',         fontSize: 10 },
  // H4 — purple
  p_h4_h:    { color: '#ab47bc', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 4H Hi',  fontSize: 9 },
  p_h4_l:    { color: '#ab47bc', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 4H Lo',  fontSize: 9 },
  h4_h:      { color: '#ce93d8', dash: [3, 2], width: 1.0, alpha: 0.65, label: '4H Hi',       fontSize: 10 },
  h4_l:      { color: '#ce93d8', dash: [3, 2], width: 1.0, alpha: 0.65, label: '4H Lo',       fontSize: 10 },
  // H1 — blue (контекст + аналіз)
  p_h1_h:    { color: '#42a5f5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 1H Hi',  fontSize: 9 },
  p_h1_l:    { color: '#42a5f5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 1H Lo',  fontSize: 9 },
  h1_h:      { color: '#90caf9', dash: [3, 2], width: 1.0, alpha: 0.65, label: '1H Hi',       fontSize: 10 },
  h1_l:      { color: '#90caf9', dash: [3, 2], width: 1.0, alpha: 0.65, label: '1H Lo',       fontSize: 10 },
  // M30 — teal
  p_m30_h:   { color: '#26a69a', dash: [6, 3], width: 1.0, alpha: 0.70, label: 'Prev M30 Hi', fontSize: 9 },
  p_m30_l:   { color: '#26a69a', dash: [6, 3], width: 1.0, alpha: 0.70, label: 'Prev M30 Lo', fontSize: 9 },
  m30_h:     { color: '#80cbc4', dash: [3, 2], width: 1.0, alpha: 0.55, label: 'M30 Hi',      fontSize: 10 },
  m30_l:     { color: '#80cbc4', dash: [3, 2], width: 1.0, alpha: 0.55, label: 'M30 Lo',      fontSize: 10 },
  // M15 — cyan
  p_m15_h:   { color: '#00bcd4', dash: [4, 2], width: 1.0, alpha: 0.65, label: 'Prev M15 Hi', fontSize: 9 },
  p_m15_l:   { color: '#00bcd4', dash: [4, 2], width: 1.0, alpha: 0.65, label: 'Prev M15 Lo', fontSize: 9 },
  m15_h:     { color: '#80deea', dash: [2, 2], width: 1.0, alpha: 0.50, label: 'M15 Hi',      fontSize: 10 },
  m15_l:     { color: '#80deea', dash: [2, 2], width: 1.0, alpha: 0.50, label: 'M15 Lo',      fontSize: 10 },
  // Liquidity (EQ Highs/Lows) — red/green
  eq_highs:  { color: '#e91e63', dash: [2, 2], width: 1.0, alpha: 0.75, label: 'EQH',         fontSize: 10 },
  eq_lows:   { color: '#4caf50', dash: [2, 2], width: 1.0, alpha: 0.75, label: 'EQL',         fontSize: 10 },
  // Fallback
  _default:  { color: '#f1c40f', dash: [4, 2], width: 1.0, alpha: 0.60, label: 'LVL',         fontSize: 9 },
};

function timeToSec(time: HorzScaleItem): number {
  if (typeof time === 'number') return time;
  return Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0, 0) / 1000;
}

function normalizeSmcData(d?: SmcData | null): Required<SmcData> {
  return {
    zones: d?.zones ?? [],
    swings: d?.swings ?? [],
    levels: d?.levels ?? [],
    trend_bias: d?.trend_bias ?? null,
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

  private rendering = false;
  private rafId: number | null = null;

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
    // Crosshair move — координати вже актуальні, рендеримо синхронно (без лагу).
    this.chartApi.subscribeCrosshairMove(() => this.renderNow());
    // Visible range change (zoom/scroll) — LWC потребує >=2 кадри щоб завершити
    // price scale auto-fit після зміни time range. Один RAF недостатній:
    // priceToCoordinate() повертає старі Y-координати → елементи "висять у повітрі".
    // Рішення: double-RAF (2 послідовні requestAnimationFrame) + синхронний рендер.
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(() => this.scheduleDoubleRaf());
    // Logical range change — додаткове покриття при scrollToPosition / zoom
    this.chartApi.timeScale().subscribeVisibleLogicalRangeChange(() => this.scheduleDoubleRaf());
  }

  private warnOnce(key: string, w: UiWarning): void {
    if (!this.onUiWarning) return;
    const now = Date.now();
    const last = this.warnLastTs.get(key) ?? 0;
    if (now - last < this.warningThrottleMs) return;
    this.warnLastTs.set(key, now);
    this.onUiWarning(w);
  }

  /** Синхронний рендер — для crosshairMove (координати вже осаджені).
   *  Recursion guard на випадок render → LWC callback → render. */
  private renderNow(): void {
    if (this.rendering) return;
    this.rendering = true;
    try {
      this.render();
    } finally {
      this.rendering = false;
    }
  }

  /** Single RAF — для patch/resize (layout вже стабільний). */
  private scheduleRaf(): void {
    if (this.rafId !== null) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = null;
      this.renderNow();
    });
  }

  /** Double RAF — для zoom/range change: LWC потребує 2 кадри щоб
   *  завершити price scale auto-fit. Перший RAF = LWC layout pass,
   *  другий RAF = overlay рендер з актуальними координатами. */
  private scheduleDoubleRaf(): void {
    if (this.rafId !== null) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = null;
      // Другий RAF: тепер priceToCoordinate() повертає актуальні Y
      requestAnimationFrame(() => this.renderNow());
    });
  }

  /** Зовнішній виклик (patch/resize) — single RAF достатній. */
  private scheduleRender(): void {
    this.scheduleRaf();
  }

  /** ADR-0008 parity: Y-axis manual zoom/pan — координати змінились.
   *  Double RAF бо applyManualRange спричиняє requestPriceScaleSync. */
  notifyPriceRangeChanged(): void {
    this.scheduleDoubleRaf();
  }

  private toX(t_ms: number): number | null {
    return this.chartApi.timeScale().timeToCoordinate((t_ms / 1000) as import('lightweight-charts').Time);
  }

  private toY(price: number): number | null {
    // Rail: координата ціни тільки через seriesApi
    return this.seriesApi.priceToCoordinate(price);
  }

  /** Ширина chart plotting area (без price scale справа). */
  private getChartAreaWidth(): number {
    try {
      const psW = this.chartApi.priceScale('right').width();
      if (psW && psW > 0) return Math.max(0, this.cssW - psW);
    } catch { /* LWC ще не готовий */ }
    return Math.max(0, this.cssW - 65); // fallback: типова ширина price scale
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

  private zoneColor(kind: string): string {
    // D3: bull/bear-aware colors (ADR-0024 §6.1a)
    if (kind.includes('bull') && kind.startsWith('ob')) return '#26a69a';  // teal (bullish OB)
    if (kind.includes('bear') && kind.startsWith('ob')) return '#ef5350';  // red  (bearish OB)
    if (kind.startsWith('ob')) return '#e67e22';  // orange fallback (legacy 'ob')
    if (kind.includes('bull') && kind.startsWith('fvg')) return '#2ecc71'; // green (bullish FVG)
    if (kind.includes('bear') && kind.startsWith('fvg')) return '#e74c3c'; // red   (bearish FVG)
    if (kind.startsWith('fvg')) return '#2ecc71'; // green fallback
    if (kind === 'premium') return '#cc3333';
    if (kind === 'discount') return '#3399cc';
    if (kind.startsWith('liquidity')) return '#9b59b6';
    return '#888888';
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

      // N3: strength-based opacity (R-02: clamp 0..1)
      const s = Math.max(0, Math.min(1, z.strength ?? 1.0));
      const fillAlpha = 0.04 + 0.11 * s;     // range [0.04 .. 0.15]
      const borderAlpha = 0.10 + 0.40 * s;   // range [0.10 .. 0.50]

      this.ctx.save();
      this.ctx.globalAlpha = fillAlpha;
      this.ctx.fillStyle = color;
      this.ctx.fillRect(x1, top, w, h);

      this.ctx.globalAlpha = borderAlpha;
      this.ctx.strokeStyle = color;
      this.ctx.lineWidth = 1;
      this.ctx.strokeRect(x1, top, w, h);
      this.ctx.restore();
    }
  }

  private renderLevels(levels: SmcLevel[]): void {
    const chartW = this.getChartAreaWidth();
    if (chartW <= 10) return;

    // Visible price range — фільтруємо нерелевантні рівні
    let midPrice = 0;
    let rangeH = 1;
    try {
      const topP = this.seriesApi.coordinateToPrice(0);
      const botP = this.seriesApi.coordinateToPrice(this.cssH);
      if (topP != null && botP != null) {
        midPrice = (topP + botP) / 2;
        rangeH = Math.abs(topP - botP) || 1;
      }
    } catch { /* серія ще не ready */ }

    const MAX_LEVELS = 12;
    const LINE_PX = 120;         // formation-attached лінія
    const NOTCH_PX = 20;         // sticky notch при edge-stuck

    // ── 1. Collect visible levels with Y coordinates ──
    type ScoredLevel = { lvl: SmcLevel; y: number; dist: number; style: LevelStyle; sticky: boolean; xStart: number; xEnd: number };
    const scored: ScoredLevel[] = [];

    for (const lvl of levels) {
      const y = this.toY(lvl.price);
      if (y === null) continue;
      const margin = this.cssH * 0.1;
      if (y < -margin || y > this.cssH + margin) continue;
      const dist = midPrice !== 0 ? Math.abs(lvl.price - midPrice) / rangeH : 0;
      const style = LEVEL_STYLES[lvl.kind ?? ''] ?? LEVEL_STYLES._default;

      // X positioning: formation visible → long line; off-screen → short notch
      let xStart: number;
      let sticky = false;

      if (lvl.t_ms != null) {
        const xF = this.toX(lvl.t_ms);
        if (xF !== null && xF >= 0 && xF < chartW) {
          xStart = xF;
        } else {
          // Formation поза екраном → sticky notch біля правого краю
          sticky = true;
          xStart = Math.max(0, chartW - NOTCH_PX);
        }
      } else {
        sticky = true;
        xStart = Math.max(0, chartW - NOTCH_PX);
      }

      const xEnd = sticky
        ? chartW  // notch до edge
        : Math.min(chartW, xStart + LINE_PX);

      if (xEnd - xStart < 2) continue;
      scored.push({ lvl, y, dist, style, sticky, xStart, xEnd });
    }

    // ── 2. Priority sort: D1 levels (pdh/pdl/dh/dl) first, then proximity ──
    const D1_KINDS = new Set(['pdh', 'pdl', 'dh', 'dl']);
    scored.sort((a, b) => {
      const aD1 = D1_KINDS.has(a.lvl.kind ?? '') ? 0 : 1;
      const bD1 = D1_KINDS.has(b.lvl.kind ?? '') ? 0 : 1;
      if (aD1 !== bD1) return aD1 - bD1;
      return a.dist - b.dist;
    });
    const visible = scored.slice(0, MAX_LEVELS);

    // ── 3. Render lines (all lines always drawn) ──
    for (const item of visible) {
      const proxA = Math.max(0.4, 1.0 - item.dist * 0.6);
      const alpha = item.style.alpha * proxA;
      const lineW = item.sticky ? Math.max(0.5, item.style.width * 0.5) : Math.max(0.5, item.style.width * 0.7);

      this.ctx.save();
      this.ctx.globalAlpha = alpha;
      this.ctx.strokeStyle = item.style.color;
      this.ctx.lineWidth = lineW;
      if (item.style.dash.length > 0) this.ctx.setLineDash(item.style.dash);

      this.ctx.beginPath();
      this.ctx.moveTo(item.xStart, item.y);
      this.ctx.lineTo(item.xEnd, item.y);
      this.ctx.stroke();
      this.ctx.restore();
    }

    // ── 4. Group lines that physically overlap → merge labels ──
    // Two lines overlap when |Y₁-Y₂| ≤ 1px AND their X ranges intersect
    const LINE_Y_MERGE = 1;  // px — threshold for "same line"

    type LabelGroup = { items: ScoredLevel[]; y: number; xStart: number; xEnd: number };
    const groups: LabelGroup[] = [];

    for (const item of visible) {
      // Try to join an existing group
      let merged = false;
      for (const g of groups) {
        const yClose = Math.abs(item.y - g.y) <= LINE_Y_MERGE;
        const xOverlap = item.xStart < g.xEnd && item.xEnd > g.xStart;
        if (yClose && xOverlap) {
          g.items.push(item);
          g.y = (g.y * (g.items.length - 1) + item.y) / g.items.length; // average Y
          g.xStart = Math.min(g.xStart, item.xStart);
          g.xEnd = Math.max(g.xEnd, item.xEnd);
          merged = true;
          break;
        }
      }
      if (!merged) {
        groups.push({ items: [item], y: item.y, xStart: item.xStart, xEnd: item.xEnd });
      }
    }

    // ── 5. Render one label per group (all labels always visible) ──
    for (const g of groups) {
      // Merge label text: "PDH | Prev H4 Hi"
      const uniqueLabels: string[] = [];
      for (const it of g.items) {
        if (!uniqueLabels.includes(it.style.label)) uniqueLabels.push(it.style.label);
      }
      const txt = uniqueLabels.join(' | ');

      // Use the highest-priority item's style (first in group, since sorted)
      const primary = g.items[0];
      const proxA = Math.max(0.4, 1.0 - primary.dist * 0.6);
      const alpha = primary.style.alpha * proxA;
      const fs = Math.max(8, primary.style.fontSize - 1);

      this.ctx.save();
      this.ctx.font = `${fs}px monospace`;

      const tm = this.ctx.measureText(txt);
      const pad = 2;

      // Label position: right of group's rightmost point, or left if no room
      const labelX = g.xEnd + 3;
      const labelRight = labelX + tm.width + pad;

      let drawX: number;
      let align: CanvasTextAlign;
      if (labelRight < chartW) {
        drawX = labelX;
        align = 'left';
      } else {
        drawX = g.xStart - 3;
        align = 'right';
      }

      // Pill geometry
      const pillX = align === 'left' ? drawX - pad : drawX - tm.width - pad;
      const pillW = tm.width + pad * 2;
      const pillH = fs + 2;
      const pillY = g.y - fs - 2;

      // Background pill
      this.ctx.globalAlpha = 0.55;
      this.ctx.fillStyle = '#141720';
      this.ctx.fillRect(pillX, pillY, pillW, pillH);

      // Text — use primary color
      this.ctx.globalAlpha = Math.min(1.0, alpha + 0.2);
      this.ctx.fillStyle = primary.style.color;
      this.ctx.textAlign = align;
      this.ctx.textBaseline = 'bottom';
      this.ctx.fillText(txt, drawX, g.y - 1);

      this.ctx.restore();
    }
  }

  private renderSwings(swings: SmcSwing[]): void {
    for (const s of swings) {
      // F7: point format — рендер як diamond marker
      const x = this.toX(s.time_ms);
      const y = this.toY(s.price);

      if (x === null || y === null) {
        this.warnOnce(`swing_xy_null:${s.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: s.id,
          details: 'swing координати поза видимою областю; пропущено',
        });
        continue;
      }

      // Diamond marker (розмір 4px)
      const r = 4;
      const isBull = s.kind ? (s.kind.includes('bull') || s.kind === 'hh' || s.kind === 'hl') : false;
      const color = isBull ? '#26a69a' : '#ef5350';

      this.ctx.save();
      this.ctx.globalAlpha = 0.7;
      this.ctx.fillStyle = color;
      this.ctx.beginPath();
      this.ctx.moveTo(x, y - r);
      this.ctx.lineTo(x + r, y);
      this.ctx.lineTo(x, y + r);
      this.ctx.lineTo(x - r, y);
      this.ctx.closePath();
      this.ctx.fill();
      this.ctx.restore();
    }
  }
}
