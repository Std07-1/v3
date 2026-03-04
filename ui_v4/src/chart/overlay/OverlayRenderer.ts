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
  pdh: { color: '#ff9800', dash: [6, 3], width: 1.5, alpha: 0.85, label: 'PDH', fontSize: 9 },
  pdl: { color: '#ff9800', dash: [6, 3], width: 1.5, alpha: 0.85, label: 'PDL', fontSize: 9 },
  dh: { color: '#ffb74d', dash: [3, 2], width: 1.0, alpha: 0.70, label: 'HOD', fontSize: 10 },
  dl: { color: '#ffb74d', dash: [3, 2], width: 1.0, alpha: 0.70, label: 'LOD', fontSize: 10 },
  // H4 — purple
  p_h4_h: { color: '#ab47bc', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 4H Hi', fontSize: 9 },
  p_h4_l: { color: '#ab47bc', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 4H Lo', fontSize: 9 },
  h4_h: { color: '#ce93d8', dash: [3, 2], width: 1.0, alpha: 0.65, label: '4H Hi', fontSize: 10 },
  h4_l: { color: '#ce93d8', dash: [3, 2], width: 1.0, alpha: 0.65, label: '4H Lo', fontSize: 10 },
  // H1 — blue (контекст + аналіз)
  p_h1_h: { color: '#42a5f5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 1H Hi', fontSize: 9 },
  p_h1_l: { color: '#42a5f5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev 1H Lo', fontSize: 9 },
  h1_h: { color: '#90caf9', dash: [3, 2], width: 1.0, alpha: 0.65, label: '1H Hi', fontSize: 10 },
  h1_l: { color: '#90caf9', dash: [3, 2], width: 1.0, alpha: 0.65, label: '1H Lo', fontSize: 10 },
  // Liquidity (EQ Highs/Lows) — red/green
  eq_highs: { color: '#e91e63', dash: [2, 2], width: 1.0, alpha: 0.75, label: 'EQH', fontSize: 10 },
  eq_lows: { color: '#4caf50', dash: [2, 2], width: 1.0, alpha: 0.75, label: 'EQL', fontSize: 10 },
  // Fallback
  _default: { color: '#f1c40f', dash: [4, 2], width: 1.0, alpha: 0.60, label: 'LVL', fontSize: 9 },
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

// ── Fog Zones: hex→rgba for gradient color stops ────────────────────
/** Convert hex color + alpha to rgba string. Gradient stops require embedded alpha. */
function _rgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
}

// ── ADR-0024c: Zone render helpers ──────────────────────────────────

const _TF_NAMES: Record<number, string> = {
  60: 'M1', 180: 'M3', 300: 'M5', 900: 'M15', 1800: 'M30',
  3600: 'H1', 14400: 'H4', 86400: 'D1',
};

const _KIND_SHORT: Record<string, string> = {
  ob_bull: 'OB▲', ob_bear: 'OB▼',
  fvg_bull: 'FVG▲', fvg_bear: 'FVG▼',
  premium: 'Premium', discount: 'Discount',
};

// ── Tooltip descriptions (trader-friendly language) ─────────────────
const _ZONE_TOOLTIP: Record<string, string> = {
  ob_bull: 'Bullish Order Block — зона попиту інституціоналів.\nОстання ведмежа свічка перед імпульсом вгору.\nЦіна часто повертається сюди для ретесту перед продовженням.',
  ob_bear: 'Bearish Order Block — зона пропозиції інституціоналів.\nОстання бича свічка перед імпульсом вниз.\nЦіна часто повертається сюди для ретесту перед падінням.',
  fvg_bull: 'Bullish Fair Value Gap — цінова неефективність.\nРозрив між свічкою 1 і 3 створює magnetic zone.\nЦіна тяжіє до заповнення цього gap перед рухом далі.',
  fvg_bear: 'Bearish Fair Value Gap — цінова неефективність.\nРозрив між свічкою 1 і 3 створює magnetic zone.\nЦіна тяжіє до заповнення цього gap перед рухом далі.',
};
const _STATUS_TOOLTIP: Record<string, string> = {
  active: 'Активна — ціна ще не торкалась',
  tested: 'Тестована — ціна торкнулась, bounce',
  partially_filled: 'Частково заповнена',
  mitigated: '✗ Мітигована — ціна пробила зону',
  filled: '✗ Заповнена — gap повністю закритий',
};
const _SWING_TOOLTIP: Record<string, string> = {
  hh: 'Higher High — вищий максимум (бичий сигнал)',
  hl: 'Higher Low — вищий мінімум (підтвердження висхідного тренду)',
  lh: 'Lower High — нижчий максимум (ведмежий сигнал)',
  ll: 'Lower Low — нижчий мінімум (підтвердження низхідного тренду)',
  bos_bull: 'BOS ▲ — Break of Structure вгору.\nЦіна закрилась вище попереднього HH.\nПідтвердження бичого тренду.',
  bos_bear: 'BOS ▼ — Break of Structure вниз.\nЦіна закрилась нижче попереднього LL.\nПідтвердження ведмежого тренду.',
  choch_bull: 'CHoCH ▲ — Change of Character вгору.\nЦіна закрилась вище HH при ведмежому тренді.\nМожливий розворот на бичий!',
  choch_bear: 'CHoCH ▼ — Change of Character вниз.\nЦіна закрилась нижче LL при бичому тренді.\nМожливий розворот на ведмежий!',
  inducement_bull: 'Inducement ▲ — хибний пробій ліквідності.\nStop-hunt нижче мінімуму з різким відновленням.',
  inducement_bear: 'Inducement ▼ — хибний пробій ліквідності.\nStop-hunt вище максимуму з різким відновленням.',
};

/** Hit area for tooltip system */
type HitArea = {
  rect: { x: number; y: number; w: number; h: number };
  tooltip: string;
};

/** Render order: P/D bg (0) → institutional (1) → intraday (2) → local (3) */
function _zoneRenderOrder(z: SmcZone): number {
  if (z.kind === 'premium' || z.kind === 'discount') return 0;
  if (z.context_layer === 'institutional') return 1;
  if (z.context_layer === 'intraday') return 2;
  return 3; // local or undefined
}

/** Border width per context layer (§4.3) */
function _zoneBorderWidth(layer: string | undefined, isPD: boolean): number {
  if (isPD) return 0.5;
  if (layer === 'institutional') return 2.5;
  if (layer === 'intraday') return 1.5;
  return 1; // local / default
}

/** Zone label: "H4 OB▲" / "M15 FVG▼" */
function _zoneLabel(z: SmcZone): string {
  const tfName = z.tf_s ? (_TF_NAMES[z.tf_s] ?? `${z.tf_s}s`) : '';
  const kindShort = _KIND_SHORT[z.kind] ?? z.kind;
  return tfName ? `${tfName} ${kindShort}` : kindShort;
}

export class OverlayRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;

  private readonly chartApi: IChartApi;
  private readonly seriesApi: ISeriesApi<'Candlestick'>;

  private frame: Required<SmcData> = { zones: [], swings: [], levels: [], trend_bias: null };

  private dpr = 1;
  private cssW = 0;
  private cssH = 0;

  private rendering = false;
  private rafId: number | null = null;

  // ── ADR-0024c: Layer isolation ────────────────────────────────────
  private layerVisible = { levels: true, swings: true, structure: true };
  private zoneKindVisible = { ob: true, fvg: true };

  // ── Tooltip system ────────────────────────────────────────────────
  private _hitAreas: HitArea[] = [];
  private _tooltipEl: HTMLDivElement | null = null;
  private _tooltipVisible = false;

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
    this._initTooltip();
  }

  // ── Tooltip init ──────────────────────────────────────────────────
  private _initTooltip(): void {
    // Create tooltip DOM element
    const tip = document.createElement('div');
    tip.style.cssText = `
      position:absolute; pointer-events:none; z-index:100;
      background:rgba(20,23,32,0.92); color:#c8cad0; border:1px solid rgba(255,255,255,0.12);
      border-radius:4px; padding:6px 10px; font:11px/1.4 monospace;
      max-width:280px; white-space:pre-wrap; display:none;
      box-shadow:0 2px 8px rgba(0,0,0,0.4);
    `;
    this.canvas.parentElement?.appendChild(tip);
    this._tooltipEl = tip;

    // BUG-FIX: canvas has pointer-events:none (so chart zoom/pan pass through).
    // Mouse events NEVER fire on canvas. Listen on parent container instead —
    // same coordinate origin (both position:absolute inset:0).
    const hitTarget = this.canvas.parentElement ?? this.canvas;

    // Mousemove handler
    hitTarget.addEventListener('mousemove', (e: MouseEvent) => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      let found: HitArea | null = null;
      for (const ha of this._hitAreas) {
        const r = ha.rect;
        if (mx >= r.x && mx <= r.x + r.w && my >= r.y && my <= r.y + r.h) {
          found = ha;
          break;
        }
      }
      if (found && this._tooltipEl) {
        this._tooltipEl.textContent = found.tooltip;
        this._tooltipEl.style.display = 'block';
        // Position: right of cursor, flip if near edge
        let tx = mx + 12;
        let ty = my - 10;
        if (tx + 280 > this.cssW) tx = mx - 290;
        if (ty < 0) ty = my + 14;
        this._tooltipEl.style.left = `${tx}px`;
        this._tooltipEl.style.top = `${ty}px`;
        this._tooltipVisible = true;
      } else if (this._tooltipVisible && this._tooltipEl) {
        this._tooltipEl.style.display = 'none';
        this._tooltipVisible = false;
      }
    });
    hitTarget.addEventListener('mouseleave', () => {
      if (this._tooltipEl) {
        this._tooltipEl.style.display = 'none';
        this._tooltipVisible = false;
      }
    });
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

  // ── ADR-0024c: Layer isolation API ─────────────────────────────────
  // Toggle одного шару → scheduleRender() → render pass перевіряє flags.
  // Frame data не перестворюється — zero allocation overhead.

  /** Встановити видимість шару (levels / swings / structure). Не чіпає zone layer. */
  setLayerVisible(layer: 'levels' | 'swings' | 'structure', visible: boolean): void {
    if (this.layerVisible[layer] === visible) return;
    this.layerVisible[layer] = visible;
    this.scheduleRender();
  }

  /** Встановити видимість zone sub-kind (ob / fvg). Не чіпає levels/swings. */
  setZoneKindVisible(kind: 'ob' | 'fvg', visible: boolean): void {
    if (this.zoneKindVisible[kind] === visible) return;
    this.zoneKindVisible[kind] = visible;
    this.scheduleRender();
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
    this._hitAreas = []; // Reset tooltip hit areas each frame

    // ADR-0024c: кожен шар незалежний — toggle одного не чіпає інші
    this.renderZones(this.frame.zones);
    if (this.layerVisible.levels) this.renderLevels(this.frame.levels);
    if (this.layerVisible.swings || this.layerVisible.structure) this.renderSwings(this.frame.swings);
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
    const chartW = this.getChartAreaWidth();

    // ── Остання ціна серії (close останньої свічки) для proximity ──
    // НЕ центр екрану! Proximity = відстань реальної ціни від зони.
    let lastPrice = 0;
    try {
      const allData = this.seriesApi.data() as any[];
      if (allData.length > 0) {
        const last = allData[allData.length - 1];
        lastPrice = last.close ?? last.value ?? 0;
      }
    } catch { /* серія ще не ready */ }

    // ADR-0024c §4.1: Render order: institutional → intraday → local
    const sorted = [...zones].sort((a, b) => {
      const orderA = _zoneRenderOrder(a);
      const orderB = _zoneRenderOrder(b);
      return orderA - orderB;
    });

    for (const z of sorted) {
      // ADR-0024c: zone kind filter
      if (z.kind.startsWith('ob') && !this.zoneKindVisible.ob) continue;
      if (z.kind.startsWith('fvg') && !this.zoneKindVisible.fvg) continue;
      // P/D disabled via config — skip if they somehow arrive
      if (z.kind === 'premium' || z.kind === 'discount') continue;

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
        xRight = chartW;
      } else {
        const x2 = this.toX(z.end_ms);
        if (x2 !== null) xRight = x2;
        else {
          if (this.canTreatEndAsRightEdge(z.end_ms)) xRight = chartW;
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

      // ── Mitigated/filled FVG: dimmed rendering ──
      const st = z.status ?? 'active';
      const isDimmed = st === 'mitigated' || st === 'filled';
      const dimMult = isDimmed ? 0.35 : 1.0;  // 35% opacity for dead zones

      // ── Fog Zones v4.1: fixed-pixel dissolve + status memory ───
      //
      // ORIGIN — маркер зародження (завжди видно, ~30px)
      // BODY   — тіло зони, розширюється з proximity (до 350px)
      //
      // proximity: 0 = далеко → тільки origin
      //            1 = в зоні → body на повну
      //
      // STATUS BOOST: зони зі статусом tested/partially_filled
      // отримують proximity floor (вони вже були "проколені" ціною,
      // трейдер повинен бачити що зона працювала).

      const s = Math.max(0, Math.min(1, z.strength ?? 1.0));

      // ── Proximity від найближчого КРАЮ зони ──
      const zoneH = z.high - z.low || 1;
      const distFromEdge = lastPrice > 0
        ? Math.max(0, lastPrice - z.high, z.low - lastPrice)
        : zoneH * 3;
      const proxRange = Math.max(zoneH * 8, lastPrice * 0.015 || 80);
      const rawProx = lastPrice > 0
        ? Math.max(0, Math.min(1, 1.0 - distFromEdge / proxRange))
        : 0.25;
      let proximity = rawProx * (2 - rawProx);

      // Status boost: tested/partially_filled → мінімум 85%
      if (st === 'tested' || st === 'partially_filled') {
        proximity = Math.max(proximity, 0.85);
      }
      // Dimmed zones: force high proximity so shape is visible (but faded)
      if (isDimmed) proximity = Math.max(proximity, 0.70);

      // ── Фіксовані розміри в пікселях ──
      const ORIGIN_PX = 30;                          // маркер завжди видно
      const MAX_BODY_PX = 350;                        // максимальне розширення body
      const bodyPx = MAX_BODY_PX * proximity;          // 0..350px
      const totalPx = ORIGIN_PX + bodyPx;              // скільки пікселів від x1 заповнено
      const fadePx = 40;                               // zone→transparent в 40px

      // Clamp до фактичної ширини зони
      const renderPx = Math.min(totalPx + fadePx, w);

      // ── Alpha (dimMult reduces all alphas for mitigated/filled zones) ──
      const originAlpha = (0.025 + 0.035 * s) * dimMult;
      const bodyAlpha = (0.030 + 0.065 * s) * proximity * dimMult;
      const borderBase = 0.08 + 0.25 * s;
      const borderAlpha = borderBase * (0.25 + 0.75 * proximity) * dimMult;
      const borderW = _zoneBorderWidth(z.context_layer, false);

      this.ctx.save();

      // ── Fill gradient: fixed-pixel from x1 ──
      const xEnd = x1 + renderPx;
      const grad = this.ctx.createLinearGradient(x1, 0, xEnd, 0);

      // Gradient stops as fractions of renderPx
      const originStop = Math.min(ORIGIN_PX / renderPx, 0.95);
      const bodyStop = Math.min(totalPx / renderPx, 0.97);

      // Origin zone: always visible
      grad.addColorStop(0, _rgba(color, originAlpha + bodyAlpha));
      grad.addColorStop(originStop, _rgba(color, originAlpha * 0.3 + bodyAlpha));

      if (bodyPx > 2) {
        // Body sustain
        const sustStop = Math.min(originStop + (bodyStop - originStop) * 0.7, bodyStop - 0.01);
        grad.addColorStop(sustStop, _rgba(color, bodyAlpha * 0.60));
        // Body end
        grad.addColorStop(bodyStop, _rgba(color, bodyAlpha * 0.10));
      }

      // Fade to zero
      grad.addColorStop(1.0, _rgba(color, 0));

      this.ctx.fillStyle = grad;
      this.ctx.fillRect(x1, top, Math.min(w, renderPx), h);

      // ── Border gradient: matches fixed pixel range ──
      const bGrad = this.ctx.createLinearGradient(x1, 0, xEnd, 0);
      bGrad.addColorStop(0, _rgba(color, borderAlpha));
      bGrad.addColorStop(originStop, _rgba(color, borderAlpha * 0.80));
      if (bodyPx > 2) {
        bGrad.addColorStop(bodyStop, _rgba(color, borderAlpha * 0.08));
      }
      bGrad.addColorStop(1.0, _rgba(color, 0));

      this.ctx.strokeStyle = bGrad;
      this.ctx.lineWidth = borderW;
      this.ctx.beginPath();
      this.ctx.moveTo(x1, top);
      this.ctx.lineTo(x1 + Math.min(w, renderPx), top);
      this.ctx.stroke();

      this.ctx.beginPath();
      this.ctx.moveTo(x1, top + h);
      this.ctx.lineTo(x1 + Math.min(w, renderPx), top + h);
      this.ctx.stroke();

      // Left edge: solid vertical bar (origin marker)
      this.ctx.strokeStyle = _rgba(color, Math.max(originAlpha * 4, borderAlpha));
      this.ctx.lineWidth = Math.max(borderW, 1.5);
      this.ctx.beginPath();
      this.ctx.moveTo(x1, top);
      this.ctx.lineTo(x1, top + h);
      this.ctx.stroke();

      this.ctx.restore();

      // ── Zone label: видимість модулюється proximity ──
      if (h > 6 && w > 25) {
        let label = _zoneLabel(z);
        // Dimmed zones: add status marker
        if (isDimmed) label = `${label} ✗`;
        if (label) {
          const fs = 9;
          this.ctx.save();
          this.ctx.font = `${fs}px monospace`;
          const tm = this.ctx.measureText(label);
          const pad = 2;

          const lblX = Math.min(x1 + 3, xRight - tm.width - pad * 2);
          const lblY = top + 1;

          const pillAlpha = (0.20 + 0.55 * proximity) * dimMult;
          this.ctx.globalAlpha = pillAlpha;
          this.ctx.fillStyle = '#141720';
          this.ctx.fillRect(lblX - pad, lblY, tm.width + pad * 2, fs + 2);

          const textAlpha = (0.30 + 0.60 * proximity) * dimMult;
          this.ctx.globalAlpha = Math.min(1.0, textAlpha);
          this.ctx.fillStyle = isDimmed ? '#888' : color;
          this.ctx.textAlign = 'left';
          this.ctx.textBaseline = 'top';
          this.ctx.fillText(label, lblX, lblY + 1);
          this.ctx.restore();
        }
      }

      // ── Tooltip hit area ──
      const statusDesc = _STATUS_TOOLTIP[st] ?? st;
      const kindDesc = _ZONE_TOOLTIP[z.kind] ?? z.kind;
      const tfLabel = z.tf_s ? (_TF_NAMES[z.tf_s] ?? `${z.tf_s}s`) : '';
      this._hitAreas.push({
        rect: { x: x1, y: top, w: Math.min(w, renderPx), h: Math.max(h, 8) },
        tooltip: `${tfLabel} ${_KIND_SHORT[z.kind] ?? z.kind}\n${statusDesc}\nStrength: ${Math.round(s * 100)}%\n\n${kindDesc}`,
      });
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
    // Build bar lookup for candle-anchored BOS/CHoCH labels
    let barMap: Map<number, any> | null = null;
    if (this.layerVisible.structure) {
      try {
        const allData = this.seriesApi.data() as any[];
        barMap = new Map();
        for (const d of allData) {
          barMap.set(timeToSec(d.time as unknown as HorzScaleItem), d);
        }
      } catch { /* series not ready */ }
    }

    for (const s of swings) {
      const isBos = s.kind?.startsWith('bos_') ?? false;
      const isChoch = s.kind?.startsWith('choch_') ?? false;
      const isInducement = s.kind?.startsWith('inducement_') ?? false;
      const isStructure = isBos || isChoch;

      // Toggle check: structure events vs swing points
      if (isStructure && !this.layerVisible.structure) continue;
      if (!isStructure && !isInducement && !this.layerVisible.swings) continue;
      if (isInducement && !this.layerVisible.swings) continue;

      const x = this.toX(s.time_ms);
      const yLevel = this.toY(s.price);

      if (x === null || yLevel === null) {
        this.warnOnce(`swing_xy_null:${s.id}`, {
          code: 'overlay_coord_null',
          kind: 'overlay',
          id: s.id,
          details: 'swing координати поза видимою областю; пропущено',
        });
        continue;
      }

      const isBull = s.kind ? (s.kind.includes('bull') || s.kind === 'hh' || s.kind === 'hl') : false;
      const color = isBull ? '#26a69a' : '#ef5350';

      if (isStructure) {
        // ── BOS/CHoCH: candle-anchored label ──
        //   Bull → label ABOVE candle high
        //   Bear → label BELOW candle low
        const label = isChoch ? 'CHoCH' : 'BOS';
        const fs = isChoch ? 10 : 9;
        const chochColor = isChoch ? '#ffa726' : color;

        // Find candle at break time for anchoring
        let yAnchor = yLevel; // fallback to break level
        if (barMap) {
          const bar = barMap.get(s.time_ms / 1000);
          if (bar) {
            const candleY = isBull
              ? this.toY(bar.high ?? bar.close ?? s.price)
              : this.toY(bar.low ?? bar.close ?? s.price);
            if (candleY !== null) yAnchor = candleY;
          }
        }

        this.ctx.save();

        // Text label above/below candle
        this.ctx.font = `bold ${fs}px monospace`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = isBull ? 'bottom' : 'top';
        const yOff = isBull ? -6 : 6;

        // Pill background for readability
        const tm = this.ctx.measureText(label);
        const px = 3, py = 1;
        const pillX = x - tm.width / 2 - px;
        const pillY = isBull ? yAnchor + yOff - fs - py : yAnchor + yOff - py;
        this.ctx.globalAlpha = 0.50;
        this.ctx.fillStyle = '#141720';
        this.ctx.fillRect(pillX, pillY, tm.width + px * 2, fs + py * 2);

        // Label text
        this.ctx.globalAlpha = isChoch ? 0.95 : 0.80;
        this.ctx.fillStyle = chochColor;
        this.ctx.fillText(label, x, yAnchor + yOff);

        this.ctx.restore();

        // Tooltip hit area for BOS/CHoCH
        this._hitAreas.push({
          rect: { x: x - 20, y: Math.min(pillY, yAnchor + yOff - fs - 2), w: 40, h: fs + 12 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? `${s.kind}`,
        });
      } else if (isInducement) {
        // ── Inducement: × marker ──
        const sz = 4;
        this.ctx.save();
        this.ctx.strokeStyle = '#ffa726';
        this.ctx.lineWidth = 1.5;
        this.ctx.globalAlpha = 0.75;
        this.ctx.beginPath();
        this.ctx.moveTo(x - sz, yLevel - sz); this.ctx.lineTo(x + sz, yLevel + sz);
        this.ctx.moveTo(x + sz, yLevel - sz); this.ctx.lineTo(x - sz, yLevel + sz);
        this.ctx.stroke();
        this.ctx.restore();

        this._hitAreas.push({
          rect: { x: x - sz - 2, y: yLevel - sz - 2, w: sz * 2 + 4, h: sz * 2 + 4 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? 'Inducement',
        });
      } else {
        // ── HH/HL/LH/LL: diamond marker ──
        const r = 3;
        this.ctx.save();
        this.ctx.globalAlpha = 0.55;
        this.ctx.fillStyle = color;
        this.ctx.beginPath();
        this.ctx.moveTo(x, yLevel - r);
        this.ctx.lineTo(x + r, yLevel);
        this.ctx.lineTo(x, yLevel + r);
        this.ctx.lineTo(x - r, yLevel);
        this.ctx.closePath();
        this.ctx.fill();
        this.ctx.restore();

        // Tooltip hit area for swing point
        this._hitAreas.push({
          rect: { x: x - r - 2, y: yLevel - r - 2, w: r * 2 + 4, h: r * 2 + 4 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? (s.kind?.toUpperCase() ?? 'Swing'),
        });
      }
    }
  }
}
