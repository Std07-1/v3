// src/chart/archi/ArchiLayerRenderer.ts
// ADR-0085 P2/P3: read-only шар Арчі на чарті — числові будильники з wire
// (frame.archi_chart, ADR-0085 D1) → лінії/зони на ціновій осі.
//
// СВІДОМО окремий від DrawingsRenderer: інший життєвий цикл (frame-driven,
// не localStorage), нуль interaction (pointer-events:none, без hit-test у v1),
// нуль команд/undo. Джерело істини = wire; Арчі-об'єкти НІКОЛИ не потрапляють
// у user-стор (ADR-0085 §6.7).
//
// Візуальна мова (D3, owner: --info синій): тонкий делікатний dash-dot «дріт»
// для рівнів, напівпрозора смуга для зон, frosted-таблетка праворуч (house-мова
// flyout/tooltip ADR-0080/0081) з монограм-ромбом Арчі замість тексту, freshness-
// альфа (старі будильники тьмяніють, нічого не миготить). Лінії йдуть НЕ на весь
// екран, а від свічки-якоря (created_at/thesis_updated — момент, де Арчі поставив
// рівень) праворуч: timeToFractionalIndex (як малювання, gap-safe) → x-старту.
// Pane-clip: шар не заходить на цінову шкалу/часову вісь (патерн ADR-0084).

import type { IChartApi, ISeriesApi, Time, Logical } from 'lightweight-charts';
import type { ArchiChartCondition, ArchiChartData } from '../../types';
import { timeToFractionalIndex, medianStep } from '../drawings/timeMap';

/** LWC bar.time → сек (number | 'YYYY-MM-DD' | BusinessDay). */
function timeToSec(t: Time): number {
  if (typeof t === 'number') return t;
  if (typeof t === 'string') return new Date(t).getTime() / 1000;
  return Date.UTC(t.year, t.month - 1, t.day, 0, 0, 0, 0) / 1000;
}

// Dash-dot: «сигнальний дріт» — відрізняється і від SMC-levels, і від
// user-стилів (solid/dashed/dotted) → лінію Арчі впізнаєш миттєво. Тонкий
// крок (owner tune: «тонші й делікатніші»).
const DASH_DOT = [5, 4, 1, 4];
const LINE_W = 0.75;          // тонша лінія (owner tune)
const LINE_ALPHA_MULT = 0.78; // делікатніша — не домінує над свічками

// Freshness (дзеркалить пороги NarrativeEnricher: <1г fresh, 1-4г aging).
const FRESH_H = 1;
const AGING_H = 4;
const ALPHA_FRESH = 1.0;
const ALPHA_AGING = 0.7;
const ALPHA_STALE = 0.45;

const ZONE_FILL_ALPHA = 0.1; // owner tune: «зону підніми трохи»
const ZONE_BORDER_ALPHA = 0.4;
const PILL_FONT = 'ui-sans-serif, -apple-system, "Segoe UI", Inter, sans-serif';

/** Path заокругленого прямокутника (canvas roundRect не всюди — робимо руками). */
function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.min(r, h / 2, w / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

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
  // Anti-collision лейблів у межах кадру: зайняті y-рядки лейблів (CSS-px).
  private labelYs: number[] = [];

  // P5: hover-reason. Цілі (y-рядок + повний reason) — оновлюються щокадру;
  // mousemove на контейнері показує tooltip коли курсор ≤ HOVER_TOL від лінії.
  // Гейт спільним ☰-перемикачем «Підказки» (ADR-0081, як зони/структури).
  private hoverTargets: { y: number; text: string }[] = [];
  private tooltipEl: HTMLDivElement | null = null;
  private hintsEnabled = false;
  private onContainerMove: ((e: MouseEvent) => void) | null = null;
  private onContainerLeave: (() => void) | null = null;
  private hoverContainer: HTMLElement | null = null;
  private static readonly HOVER_TOL = 6;

  private dpr = 1;
  private rafId: number | null = null;
  private ro: ResizeObserver;
  private onVisibleRangeChange: () => void;

  // Кольори з токенів (кеш; canvas не читає var() — патерн DrawingsRenderer).
  private infoColor = '#5487FF';
  private bearColor = '#ED4554';
  private cardColor = '#1c2128';

  // Часи барів (сек) для anchor-x лінії. Кеш throttleд — .data() щокадру під час
  // пану дорогий; перечитуємо лише коли змінилась довжина/останній бар.
  private barTimesSec: number[] = [];
  private barTimesLast = NaN;
  private barTimesCheckedAt = 0;
  private static readonly BAR_RECHECK_MS = 250;

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

    this.initTooltip();
    this.refreshThemeColors();
  }

  /** P5: hover-tooltip (frosted, house-токени — дзеркалить .smc-tooltip
   *  ADR-0081). Слухає mousemove на контейнері (canvas = pointer-events:none).
   *  Показ гейтиться `hintsEnabled` (спільний ☰-перемикач). */
  private initTooltip(): void {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const tip = document.createElement('div');
    tip.className = 'archi-tooltip';
    tip.style.cssText = `
      position:absolute; pointer-events:none; z-index:60;
      background:color-mix(in srgb, var(--card, #141720) 90%, transparent);
      -webkit-backdrop-filter:blur(14px) saturate(1.4); backdrop-filter:blur(14px) saturate(1.4);
      color:var(--text-1, #e6edf3);
      border:1px solid color-mix(in srgb, var(--info, #5487ff) 40%, transparent);
      border-radius:8px; padding:6px 10px; font:11px/1.4 var(--font-sans, sans-serif);
      max-width:280px; white-space:pre-wrap; display:none;
      box-shadow:0 6px 18px -10px rgba(0,0,0,0.5);
    `;
    parent.appendChild(tip);
    this.tooltipEl = tip;
    this.hoverContainer = parent;

    this.onContainerMove = (e: MouseEvent) => {
      if (!this.hintsEnabled || !this.visible || !this.tooltipEl) return;
      const rect = this.canvas.getBoundingClientRect();
      const my = e.clientY - rect.top;
      let hit: string | null = null;
      for (const t of this.hoverTargets) {
        if (Math.abs(my - t.y) <= ArchiLayerRenderer.HOVER_TOL) { hit = t.text; break; }
      }
      if (hit) {
        this.tooltipEl.textContent = hit;
        this.tooltipEl.style.display = 'block';
        const mx = e.clientX - rect.left;
        this.tooltipEl.style.left = `${Math.min(mx + 12, rect.width - 290)}px`;
        this.tooltipEl.style.top = `${my + 14}px`;
      } else if (this.tooltipEl.style.display !== 'none') {
        this.tooltipEl.style.display = 'none';
      }
    };
    this.onContainerLeave = () => {
      if (this.tooltipEl) this.tooltipEl.style.display = 'none';
    };
    parent.addEventListener('mousemove', this.onContainerMove);
    parent.addEventListener('mouseleave', this.onContainerLeave);
  }

  /** P5: спільний ☰-перемикач «Підказки» гейтить hover-reason (ADR-0081). */
  setHintsEnabled(on: boolean): void {
    this.hintsEnabled = on;
    if (!on && this.tooltipEl) this.tooltipEl.style.display = 'none';
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
    this.bearColor = s.getPropertyValue('--bear').trim() || '#ED4554';
    this.cardColor = s.getPropertyValue('--card').trim() || '#1c2128';
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
    // ПЕРЕД early-return (audit v3 confirmed): скидаємо hover/label-стани на
    // КОЖНОМУ кадрі, включно з порожнім/схованим — інакше hoverTargets
    // переживали clear → фантомний tooltip для зниклої лінії.
    this.labelYs = [];
    this.hoverTargets = [];

    const d = this.data;
    const conds = d?.conditions;
    const hasThesis =
      d != null && (d.key_level_price != null || d.invalidation_price != null);
    if (!this.visible || d == null || ((!conds || conds.length === 0) && !hasThesis))
      return;

    this.refreshBarTimes(); // anchor-x лінії від свічки-якоря

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

    for (const c of conds ?? []) {
      const alpha = freshnessAlpha(c.created_at_ms);
      if ((c.kind === 'price_cross' || c.kind === 'candle_close') && c.level != null) {
        this.renderLevel(c, paneW, alpha);
      } else if (c.kind === 'price_zone_touch' && c.zone_high != null && c.zone_low != null) {
        this.renderZone(c, paneW, alpha);
      }
    }

    // P4: рівні тези поверх будильників (головні рівні аналізу Арчі).
    if (hasThesis) {
      const tAlpha = freshnessAlpha(d!.thesis_updated_at_ms ?? 0);
      const tAnchor = d!.thesis_updated_at_ms;
      if (d!.key_level_price != null) {
        this.renderThesisLine(d!.key_level_price, this.infoColor, false, 'Ключовий рівень тези', paneW, tAlpha, tAnchor);
      }
      if (d!.invalidation_price != null) {
        this.renderThesisLine(d!.invalidation_price, this.bearColor, true, 'Рівень інвалідації тези', paneW, tAlpha, tAnchor);
      }
    }

    this.ctx.restore();
  }

  /** Часи барів (сек) для anchor-x. Throttleд: перечитуємо лише коли реально
   *  змінились (довжина або останній бар) — .data() під час пану дорогий. */
  private refreshBarTimes(): void {
    const now = performance.now();
    if (now - this.barTimesCheckedAt < ArchiLayerRenderer.BAR_RECHECK_MS) return;
    this.barTimesCheckedAt = now;
    const bars = this.seriesApi.data();
    const n = bars.length;
    if (n === 0) { this.barTimesSec = []; this.barTimesLast = NaN; return; }
    const lastT = timeToSec(bars[n - 1].time as Time);
    if (n === this.barTimesSec.length && lastT === this.barTimesLast) return;
    this.barTimesSec = bars.map((b) => timeToSec(b.time as Time));
    this.barTimesLast = lastT;
  }

  /** Момент (ms), де Арчі поставив рівень → x на чарті (gap-safe, як малювання).
   *  null → якір поза даними/невідомий → caller падає на повну ширину.
   *
   *  ⚠ LWC-квірк (verified 2026-07-11): `logicalToCoordinate(fractional)`
   *  повертає 0 — координата існує ЛИШЕ для цілого logical. Тому інтерполюємо
   *  вручну між двома цілими викликами (sub-bar точність без дробового аргу). */
  private anchorX(tMs: number | undefined): number | null {
    if (!tMs || this.barTimesSec.length === 0) return null;
    const step = medianStep(this.barTimesSec);
    const idx = timeToFractionalIndex(this.barTimesSec, tMs / 1000, step);
    if (idx === null) return null;
    const ts = this.chartApi.timeScale();
    const i0 = Math.floor(idx);
    const c0 = ts.logicalToCoordinate(i0 as Logical);
    if (c0 === null) return null;
    const frac = idx - i0;
    if (frac === 0) return c0;
    const c1 = ts.logicalToCoordinate((i0 + 1) as Logical);
    return c1 === null ? c0 : c0 + frac * (c1 - c0);
  }

  /** Старт лінії від свічки-якоря (клампнутий у пану). Якір невідомий → 0
   *  (повна ширина). Стоп-край справа = остання свічка: рівень НЕ починається у
   *  майбутній whitespace — якір за межами даних (stale-бари, де умова датована
   *  пізніше за останній бар) сідає на праву свічку, а не колапсує. */
  private lineStartX(tMs: number | undefined, paneW: number): number {
    const ax = this.anchorX(tMs);
    if (ax === null) return 0;
    const n = this.barTimesSec.length;
    const lastBarX = n > 0 ? this.chartApi.timeScale().logicalToCoordinate((n - 1) as Logical) : null;
    const cap = lastBarX !== null ? Math.min(lastBarX, paneW) : paneW - 24;
    return Math.max(0, Math.min(ax, cap));
  }

  private renderLevel(c: ArchiChartCondition, paneW: number, alpha: number): void {
    const y = this.seriesApi.priceToCoordinate(c.level!);
    if (y === null) return;
    const ctx = this.ctx;
    const x0 = this.lineStartX(c.created_at_ms, paneW);
    ctx.strokeStyle = withAlpha(this.infoColor, alpha * LINE_ALPHA_MULT);
    ctx.lineWidth = LINE_W;
    ctx.setLineDash(DASH_DOT);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(paneW, y);
    ctx.stroke();
    ctx.setLineDash([]);

    const tf = c.kind === 'candle_close' && c.tf_s ? ` ${TF_LABELS[c.tf_s] ?? ''}` : '';
    this.renderPill(`${fmtPrice(c.level!)}${tf}`, y, this.infoColor, alpha, paneW);
    this.hoverTargets.push({ y, text: c.reason || `Будильник ${fmtPrice(c.level!)}` });
  }

  private renderZone(c: ArchiChartCondition, paneW: number, alpha: number): void {
    const yHigh = this.seriesApi.priceToCoordinate(c.zone_high!);
    const yLow = this.seriesApi.priceToCoordinate(c.zone_low!);
    if (yHigh === null || yLow === null) return;
    const top = Math.min(yHigh, yLow);
    const h = Math.abs(yLow - yHigh);
    const ctx = this.ctx;
    const x0 = this.lineStartX(c.created_at_ms, paneW);
    ctx.fillStyle = withAlpha(this.infoColor, ZONE_FILL_ALPHA * alpha);
    ctx.fillRect(x0, top, paneW - x0, h);
    ctx.strokeStyle = withAlpha(this.infoColor, ZONE_BORDER_ALPHA * alpha);
    ctx.lineWidth = LINE_W;
    ctx.setLineDash(DASH_DOT);
    ctx.beginPath();
    ctx.moveTo(x0, top);
    ctx.lineTo(paneW, top);
    ctx.moveTo(x0, top + h);
    ctx.lineTo(paneW, top + h);
    ctx.stroke();
    ctx.setLineDash([]);

    this.renderPill(`${fmtPrice(c.zone_low!)}–${fmtPrice(c.zone_high!)}`, top, this.infoColor, alpha, paneW);
    const zText = c.reason || `Зона ${fmtPrice(c.zone_low!)}–${fmtPrice(c.zone_high!)}`;
    this.hoverTargets.push({ y: top, text: zText });
    this.hoverTargets.push({ y: top + h, text: zText });
  }

  /** P4: рівень тези — solid (key/ціль) або dashed (invalidation). Трохи товща
   *  за будильники (головні рівні аналізу Арчі), але лишається делікатною. */
  private renderThesisLine(
    price: number,
    color: string,
    dashed: boolean,
    hoverText: string,
    paneW: number,
    alpha: number,
    anchorMs: number | undefined,
  ): void {
    const y = this.seriesApi.priceToCoordinate(price);
    if (y === null) return;
    const ctx = this.ctx;
    const x0 = this.lineStartX(anchorMs, paneW);
    ctx.strokeStyle = withAlpha(color, alpha * LINE_ALPHA_MULT);
    ctx.lineWidth = dashed ? 0.85 : 1;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(paneW, y);
    ctx.stroke();
    ctx.setLineDash([]);
    this.renderPill(fmtPrice(price), y, color, alpha, paneW);
    this.hoverTargets.push({ y, text: `${hoverText} (${fmtPrice(price)})` });
  }

  /** Преміум-таблетка праворуч на лінії (house frosted-мова flyout/tooltip):
   *  міні-орб Арчі (ехо presence-orb з консолі ГОРН — один маленький знак
   *  замість тексту «Арчі») + ціна (колір лінії). Без emoji.
   *  Anti-collision: центр таблетки зсувається, якщо близько до попередньої. */
  private renderPill(priceText: string, y: number, color: string, alpha: number, paneW: number): void {
    const ctx = this.ctx;
    const H = 17;
    const R = 5;
    const PAD = 7;
    const ORB_SLOT = 8; // резерв під орб+гало
    const GAP = 6;

    ctx.font = '600 11px ' + PILL_FONT;
    const priceW = ctx.measureText(priceText).width;
    const pillW = PAD + ORB_SLOT + GAP + priceW + PAD;

    const x = Math.max(2, paneW - pillW - 4);
    // Anti-collision по центру таблетки (лінія лишається на своєму y).
    let cy = y;
    while (this.labelYs.some((prev) => Math.abs(prev - cy) < H + 2)) cy += H + 2;
    this.labelYs.push(cy);
    const top = cy - H / 2;

    // Frosted-плашка (color-mix недоступний на canvas → напівпрозорий card +
    // колірна рамка = преміум-скло без backdrop-blur).
    roundRectPath(ctx, x, top, pillW, H, R);
    ctx.fillStyle = withAlpha(this.cardColor, 0.9 * alpha + 0.08);
    ctx.shadowColor = 'rgba(0, 0, 0, 0.35)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 1;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
    roundRectPath(ctx, x, top, pillW, H, R);
    ctx.strokeStyle = withAlpha(color, 0.4 * alpha);
    ctx.lineWidth = 1;
    ctx.stroke();

    // Міні-орб Арчі: гало-кільце + ядро (колір лінії) = присутність, не текст.
    const ocx = x + PAD + ORB_SLOT / 2;
    ctx.beginPath();
    ctx.arc(ocx, cy, 4, 0, Math.PI * 2);
    ctx.strokeStyle = withAlpha(color, 0.35 * alpha);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ocx, cy, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = withAlpha(color, Math.min(1, alpha + 0.1));
    ctx.fill();

    // Ціна (колір лінії).
    ctx.textBaseline = 'middle';
    ctx.font = '600 11px ' + PILL_FONT;
    ctx.fillStyle = withAlpha(color, Math.min(1, alpha + 0.15));
    ctx.fillText(priceText, x + PAD + ORB_SLOT + GAP, cy + 0.5);
    ctx.textBaseline = 'alphabetic';
  }

  destroy(): void {
    this.ro.disconnect();
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);
    this.chartApi.timeScale().unsubscribeVisibleTimeRangeChange(this.onVisibleRangeChange);
    if (this.hoverContainer) {
      if (this.onContainerMove) this.hoverContainer.removeEventListener('mousemove', this.onContainerMove);
      if (this.onContainerLeave) this.hoverContainer.removeEventListener('mouseleave', this.onContainerLeave);
    }
    this.tooltipEl?.remove();
    this.tooltipEl = null;
  }
}
