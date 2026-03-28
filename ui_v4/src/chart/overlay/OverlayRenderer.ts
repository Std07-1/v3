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
import type { SmcData, SmcZone, SmcLevel, SmcSwing, UiWarning, ZoneGradeInfo } from '../../types';
import { applyBudget, DEFAULT_BUDGET, type BudgetConfig, type DisplayMode, type ZoneDisplayProps } from './DisplayBudget';

// ── ADR-0043 P1: Canvas Safe Zones — overlay елементи не рендеряться під HUD ──
const CANVAS_SAFE_TOP_Y = 75;    // HUD + OHLCV tooltip clearance (px)
const CANVAS_SAFE_BOTTOM_Y = 30; // Time axis clearance (px)
// CANVAS_SAFE_RIGHT_X = динамічний через getChartAreaWidth() — без hardcode

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
  // ADR-0035: Session levels — Asia=#CE93D8, London=#FF9800, NY=#42A5F5
  as_h: { color: '#CE93D8', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'Asia Hi', fontSize: 10 },
  as_l: { color: '#CE93D8', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'Asia Lo', fontSize: 10 },
  p_as_h: { color: '#CE93D8', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev Asia Hi', fontSize: 9 },
  p_as_l: { color: '#CE93D8', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev Asia Lo', fontSize: 9 },
  lon_h: { color: '#FF9800', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'London Hi', fontSize: 10 },
  lon_l: { color: '#FF9800', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'London Lo', fontSize: 10 },
  p_lon_h: { color: '#FF9800', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev Lon Hi', fontSize: 9 },
  p_lon_l: { color: '#FF9800', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev Lon Lo', fontSize: 9 },
  ny_h: { color: '#42A5F5', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'NY Hi', fontSize: 10 },
  ny_l: { color: '#42A5F5', dash: [3, 2], width: 1.0, alpha: 0.65, label: 'NY Lo', fontSize: 10 },
  p_ny_h: { color: '#42A5F5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev NY Hi', fontSize: 9 },
  p_ny_l: { color: '#42A5F5', dash: [6, 3], width: 1.5, alpha: 0.80, label: 'Prev NY Lo', fontSize: 9 },
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
    zone_grades: d?.zone_grades ?? {},
    bias_map: d?.bias_map ?? {},
    momentum_map: d?.momentum_map ?? {},
    pd_state: d?.pd_state ?? null,
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
  fractal_high: 'Fractal High ▲ — Williams fractal (максимум п’яти свічок)',
  fractal_low: 'Fractal Low ▼ — Williams fractal (мінімум п’яти свічок)',
  displacement_bull: 'Displacement ▲ — сильна бича свічка (інституційний ордер-флоу)',
  displacement_bear: 'Displacement ▼ — сильна ведмежа свічка (інституційний ордер-флоу)',
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

  private frame: Required<SmcData> = { zones: [], swings: [], levels: [], trend_bias: null, zone_grades: {}, bias_map: {}, momentum_map: {}, pd_state: null };

  private cssW = 0;
  private cssH = 0;

  private rendering = false;
  private rafId: number | null = null;

  // ── ADR-0029: Grade cache (E4: full frame only, delta removes mitigated) ──
  private _gradeCache: Record<string, ZoneGradeInfo> = {};

  // ── ADR-0024c: Layer isolation ────────────────────────────────────
  private layerVisible = { levels: true, swings: true, structure: true, fractals: true, displacement: true };
  private zoneKindVisible = { ob: true, fvg: true };

  // ── ADR-0030-alt: TF Sovereignty (viewer TF for projection detection) ──
  private viewerTfS: number = 900;

  // ── ADR-0028 Φ0: Display budget (client-side presentation filter) ──
  private displayMode: DisplayMode = 'focus';
  private budgetConfig: BudgetConfig = DEFAULT_BUDGET;
  private _zoneProps: Map<string, ZoneDisplayProps> = new Map();

  // ── Theme awareness (light background → transparent pills) ──────
  private _isLightTheme = false;
  // ── ADR-0041: EQ line color (theme-dependent) ──────────────────
  private _pdEqLineColor = 'rgba(255, 255, 255, 0.40)';

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
    // ADR-0029 E4: update grade cache from full frame
    const newGrades = this.frame.zone_grades ?? {};
    if (Object.keys(newGrades).length > 0) {
      this._gradeCache = { ...newGrades };
    }
    this.scheduleRender();
  }

  /** ADR-0029 E4: remove mitigated zones from grade cache (delta path). */
  removeMitigatedGrades(ids: string[]): void {
    for (const id of ids) {
      delete this._gradeCache[id];
    }
  }

  /** Theme-aware pills: on light background pills are invisible. */
  setLightTheme(isLight: boolean): void {
    if (this._isLightTheme !== isLight) {
      this._isLightTheme = isLight;
      this.scheduleRender();
    }
  }

  /** ADR-0041: set EQ line color from theme. */
  setPdEqLineColor(color: string): void {
    this._pdEqLineColor = color;
    this.scheduleRender();
  }

  resize(cssW: number, cssH: number, dpr: number): void {
    if (cssW <= 0 || cssH <= 0) return;

    this.cssW = cssW;
    this.cssH = cssH;
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    this.canvas.style.width = `${cssW}px`;
    this.canvas.style.height = `${cssH}px`;

    // DPR rail
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    this.scheduleRender();
  }

  private bindTriggers(): void {
    // ADR-0032 P1: crosshairMove guard — якщо double-RAF pending (zoom/scroll),
    // skip синхронний рендер щоб не малювати overlay зі stale Y координатами.
    // Без zoom/scroll — zero-lag як і раніше.
    this.chartApi.subscribeCrosshairMove(() => {
      if (this.rafId !== null) return; // double-RAF pending → skip stale render
      this.renderNow();
    });
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

  /** ADR-0032 P3: Cleanup pending RAF + tooltip DOM on unmount. */
  destroy(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this._tooltipEl?.remove();
    this._tooltipEl = null;
  }

  /** ADR-0008 parity: Y-axis manual zoom/pan — координати змінились.
   *  Double RAF бо applyManualRange спричиняє requestPriceScaleSync. */
  notifyPriceRangeChanged(): void {
    this.scheduleDoubleRaf();
  }

  // ── ADR-0030-alt: TF Sovereignty ──────────────────────────────────
  setViewerTfS(tfS: number): void {
    if (this.viewerTfS !== tfS) {
      this.viewerTfS = tfS;
      this.scheduleRender();
    }
  }

  private isProjection(z: SmcZone): boolean {
    return (z.tf_s || 0) > this.viewerTfS;
  }

  // ── ADR-0024c: Layer isolation API ─────────────────────────────────
  // Toggle одного шару → scheduleRender() → render pass перевіряє flags.
  // Frame data не перестворюється — zero allocation overhead.

  /** Встановити видимість шару (levels / swings / structure). Не чіпає zone layer. */
  setLayerVisible(layer: 'levels' | 'swings' | 'structure' | 'fractals' | 'displacement', visible: boolean): void {
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

  // ── ADR-0028 Φ0: Display mode toggle ─────────────────────────────
  setDisplayMode(mode: DisplayMode): void {
    if (mode !== this.displayMode) {
      this.displayMode = mode;
      this.scheduleRender();
    }
  }

  getDisplayMode(): DisplayMode {
    return this.displayMode;
  }

  /** Opacity for zone from last budget computation (default 1.0). */
  getZoneOpacity(zoneId: string): number {
    return this._zoneProps.get(zoneId)?.opacity ?? 1.0;
  }

  /** Adaptive marker scale: 8px barSpacing = 1.0 (desktop default). */
  private getBarScale(): number {
    const lr = this.chartApi.timeScale().getVisibleLogicalRange();
    if (!lr) return 1;
    const bars = Math.max(1, lr.to - lr.from);
    const px = this.getChartAreaWidth() / bars;
    return Math.max(0.5, Math.min(2.0, px / 8));
  }

  private render(): void {
    if (this.cssW <= 0 || this.cssH <= 0) return;

    this.ctx.clearRect(0, 0, this.cssW, this.cssH);
    this._hitAreas = []; // Reset tooltip hit areas each frame

    // ADR-0028 Φ0: client-side budget filter (D3: budget ≤ cap)
    // ADR-0029: pass grades for grade-aware Focus/Research filter
    const budget = applyBudget(
      this.frame.zones, this.frame.levels, this.frame.swings,
      this.displayMode, this.budgetConfig, this._gradeCache,
    );
    this._zoneProps = budget.zoneProps;

    // ADR-0024c: кожен шар незалежний — toggle одного не чіпає інші
    const scale = this.getBarScale();

    this.renderZones(budget.zones);
    if (this.layerVisible.levels) this.renderLevels(budget.levels);
    this.renderPdEqLine(budget.levels);
    if (this.layerVisible.swings || this.layerVisible.structure || this.layerVisible.fractals || this.layerVisible.displacement) this.renderSwings(budget.swings, scale);
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
      // ADR-0028 Φ0: budget-driven opacity (strength→alpha mapping)
      const budgetOpacity = this._zoneProps.get(z.id)?.opacity ?? 1.0;
      // ADR-0030-alt: projection fade (cross-TF zones = background context)
      const projMult = this.isProjection(z) ? 0.35 : 1.0;
      const alphaMult = dimMult * budgetOpacity * projMult;

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

      // ── Alpha (dimMult + budgetOpacity reduce alphas) ──
      const originAlpha = (0.025 + 0.035 * s) * alphaMult;
      const bodyAlpha = (0.030 + 0.065 * s) * proximity * alphaMult;
      const borderBase = 0.08 + 0.25 * s;
      const borderAlpha = borderBase * (0.25 + 0.75 * proximity) * alphaMult;
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
      // ADR-0030-alt: dotted border for projections
      if (this.isProjection(z)) this.ctx.setLineDash([4, 3]);
      this.ctx.beginPath();
      this.ctx.moveTo(x1, top);
      this.ctx.lineTo(x1 + Math.min(w, renderPx), top);
      this.ctx.stroke();

      this.ctx.beginPath();
      this.ctx.moveTo(x1, top + h);
      this.ctx.lineTo(x1 + Math.min(w, renderPx), top + h);
      this.ctx.stroke();
      this.ctx.setLineDash([]);

      // Left edge: solid vertical bar (origin marker)
      this.ctx.strokeStyle = _rgba(color, Math.max(originAlpha * 4, borderAlpha));
      this.ctx.lineWidth = Math.max(borderW, 1.5);
      this.ctx.beginPath();
      this.ctx.moveTo(x1, top);
      this.ctx.lineTo(x1, top + h);
      this.ctx.stroke();

      this.ctx.restore();

      // ── Zone label: видимість модулюється proximity ──
      if (h > 3 && w > 25) {
        let label = _zoneLabel(z);
        // Dimmed zones: add status marker
        if (isDimmed) label = `${label} ✗`;
        if (label) {
          const fs = 9;
          this.ctx.save();
          this.ctx.font = `${fs}px monospace`;

          // ADR-0029: Grade integrated into label pill (A+/A/B, C hidden)
          // ADR-0030-alt: no grade badge on projections (context zones, not action zones)
          const gradeInfo = this.isProjection(z) ? undefined : this._gradeCache[z.id];
          const gradeSuffix = (gradeInfo && gradeInfo.grade !== 'C') ? ` ${gradeInfo.grade}` : '';
          const fullText = label + gradeSuffix;

          const labelTm = this.ctx.measureText(label);
          const fullTm = this.ctx.measureText(fullText);
          const pad = 2;

          const lblX = Math.min(x1 + 3, xRight - fullTm.width - pad * 2);
          const lblY = top + 1;

          // ADR-0043 P1: Y-guard — не рендеримо label під HUD або над time axis
          if (!(lblY < CANVAS_SAFE_TOP_Y || lblY > (this.cssH - CANVAS_SAFE_BOTTOM_Y))) {
            // Single pill background — skip on light theme (dark pills distract)
            if (!this._isLightTheme) {
              const pillAlpha = Math.max(0.15, (0.20 + 0.55 * proximity) * dimMult);  // ADR-0042 P3: floor
              this.ctx.globalAlpha = pillAlpha;
              this.ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
              this.ctx.fillRect(lblX - pad, lblY, fullTm.width + pad * 2, fs + 2);
            }

            // Label text
            const textAlpha = (0.30 + 0.60 * proximity) * dimMult;
            this.ctx.globalAlpha = Math.min(1.0, textAlpha);
            this.ctx.fillStyle = isDimmed ? '#888' : color;
            this.ctx.textAlign = 'left';
            this.ctx.textBaseline = 'top';
            this.ctx.fillText(label, lblX, lblY + 1);

            // Grade suffix in its own color within same pill
            if (gradeSuffix) {
              const gc: Record<string, string> = { 'A+': '#ffd700', 'A': '#fff', 'B': '#999' };
              this.ctx.fillStyle = gc[gradeInfo!.grade] ?? '#999';
              this.ctx.fillText(gradeSuffix, lblX + labelTm.width, lblY + 1);
            }
          }
          this.ctx.restore();
        }
      }

      // ── Tooltip hit area ──
      const statusDesc = _STATUS_TOOLTIP[st] ?? st;
      const kindDesc = _ZONE_TOOLTIP[z.kind] ?? z.kind;
      const tfLabel = z.tf_s ? (_TF_NAMES[z.tf_s] ?? `${z.tf_s}s`) : '';
      const gi = this._gradeCache[z.id];
      const gradeBlock = gi
        ? `  ${gi.grade} (${gi.score}/11)\n${gi.factors.map(f => f.replace(/ \+\d+$/, '')).join(' · ')}\n\n`
        : '\n';
      this._hitAreas.push({
        rect: { x: x1, y: top, w: Math.min(w, renderPx), h: Math.max(h, 8) },
        tooltip: `${tfLabel} ${_KIND_SHORT[z.kind] ?? z.kind}${gradeBlock}${statusDesc}\nStrength: ${Math.round(s * 100)}%\n\n${kindDesc}`,
      });
    }
  }

  /** ADR-0041 P3: Dashed EQ line (equilibrium between swing SH/SL).
   *  D8 coincidence rule: hide EQ if any PDH/PDL level is within threshold. */
  private renderPdEqLine(levels: SmcLevel[]): void {
    const pd = this.frame.pd_state;
    if (!pd) return;

    const chartW = this.getChartAreaWidth();
    if (chartW <= 10) return;

    const y = this.toY(pd.equilibrium);
    if (y === null) return;
    if (y < -5 || y > this.cssH + 5) return;

    // D8 coincidence: skip EQ line if PDH or PDL is nearby
    const PDH_PDL_KINDS = new Set(['pdh', 'pdl']);
    for (const lvl of levels) {
      if (!PDH_PDL_KINDS.has(lvl.kind ?? '')) continue;
      const range = pd.range_high - pd.range_low;
      if (range <= 0) return;
      // coincidence threshold: 5% of total SH-SL range (compact heuristic)
      if (Math.abs(lvl.price - pd.equilibrium) < range * 0.05) return;
    }

    this.ctx.save();
    this.ctx.strokeStyle = this._pdEqLineColor;
    this.ctx.lineWidth = 1;
    this.ctx.setLineDash([4, 4]);
    this.ctx.beginPath();
    this.ctx.moveTo(0, y);
    this.ctx.lineTo(chartW, y);
    this.ctx.stroke();

    // Tiny label "EQ" at right edge
    const fs = 9;
    this.ctx.font = `${fs}px monospace`;
    this.ctx.fillStyle = this._pdEqLineColor;
    this.ctx.textAlign = 'right';
    this.ctx.textBaseline = 'bottom';
    this.ctx.fillText('EQ', chartW - 2, y - 2);
    this.ctx.restore();
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

    // ── 2. Priority sort: D1 levels first, then prev-session, then proximity ──
    const D1_KINDS = new Set(['pdh', 'pdl', 'dh', 'dl']);
    const PREV_SESSION = new Set(['p_as_h', 'p_as_l', 'p_lon_h', 'p_lon_l', 'p_ny_h', 'p_ny_l']);
    scored.sort((a, b) => {
      const ak = a.lvl.kind ?? '';
      const bk = b.lvl.kind ?? '';
      const aTier = D1_KINDS.has(ak) ? 0 : PREV_SESSION.has(ak) ? 1 : 2;
      const bTier = D1_KINDS.has(bk) ? 0 : PREV_SESSION.has(bk) ? 1 : 2;
      if (aTier !== bTier) return aTier - bTier;
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
      // ADR-0043 P1: Y-guard для level labels — не рендеримо під HUD або над time axis
      if (g.y < CANVAS_SAFE_TOP_Y || g.y > (this.cssH - CANVAS_SAFE_BOTTOM_Y)) continue;

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

      // Background pill — skip on light theme
      if (!this._isLightTheme) {
        this.ctx.globalAlpha = 0.55;
        this.ctx.fillStyle = '#141720';
        this.ctx.fillRect(pillX, pillY, pillW, pillH);
      }

      // Text — use primary color
      this.ctx.globalAlpha = Math.min(1.0, alpha + 0.2);
      this.ctx.fillStyle = primary.style.color;
      this.ctx.textAlign = align;
      this.ctx.textBaseline = 'bottom';
      this.ctx.fillText(txt, drawX, g.y - 1);

      this.ctx.restore();
    }
  }

  private renderSwings(swings: SmcSwing[], scale: number = 1): void {
    // Build bar lookup for candle-anchored rendering
    let barMap: Map<number, any> | null = null;
    if (this.layerVisible.structure || this.layerVisible.displacement || this.layerVisible.swings) {
      try {
        const allData = this.seriesApi.data() as any[];
        barMap = new Map();
        for (const d of allData) {
          barMap.set(timeToSec(d.time as unknown as HorzScaleItem), d);
        }
      } catch { /* series not ready */ }
    }

    // Capped scale for markers/labels — prevents visual bloat at max zoom
    const mScale = Math.min(1.4, scale);

    for (const s of swings) {
      const isBos = s.kind?.startsWith('bos_') ?? false;
      const isChoch = s.kind?.startsWith('choch_') ?? false;
      const isInducement = s.kind?.startsWith('inducement_') ?? false;
      const isFractal = s.kind?.startsWith('fractal_') ?? false;
      const isDisplacement = s.kind?.startsWith('displacement_') ?? false;
      const isStructure = isBos || isChoch;

      // Toggle check: structure events vs swing points vs fractals vs displacement
      if (isStructure && !this.layerVisible.structure) continue;
      if (isFractal && !this.layerVisible.fractals) continue;
      if (isDisplacement && !this.layerVisible.displacement) continue;
      if (!isStructure && !isInducement && !isFractal && !isDisplacement && !this.layerVisible.swings) continue;
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
        // ── BOS/CHoCH: candle-anchored label (font capped at 12px) ──
        const label = isChoch ? 'CHoCH' : 'BOS';
        const fs = Math.min(12, Math.round((isChoch ? 10 : 9) * Math.max(0.7, mScale)));
        const chochColor = isChoch ? '#ffa726' : color;

        let yAnchor = yLevel;
        if (barMap) {
          const bar = barMap.get(s.time_ms / 1000);
          if (bar) {
            const candleY = isBull
              ? this.toY(bar.high ?? bar.close ?? s.price)
              : this.toY(bar.low ?? bar.close ?? s.price);
            if (candleY !== null) yAnchor = candleY;
          }
        }

        // ADR-0043 P1: Y-guard для BOS/CHoCH labels
        const yOffBase = Math.max(3, Math.round(8 * mScale));
        const yOff = isBull ? -yOffBase : yOffBase;
        const lblRenderY = yAnchor + yOff;
        if (lblRenderY < CANVAS_SAFE_TOP_Y || lblRenderY > (this.cssH - CANVAS_SAFE_BOTTOM_Y)) {
          // Поза safe zone — пропускаємо label, але hit area додається нижче
        } else {
          this.ctx.save();
          this.ctx.font = `bold ${fs}px monospace`;
          this.ctx.textAlign = 'center';
          this.ctx.textBaseline = isBull ? 'bottom' : 'top';

          const tm = this.ctx.measureText(label);
          const px = 3, py = 1;
          const pillX = x - tm.width / 2 - px;
          const pillY = isBull ? lblRenderY - fs - py : lblRenderY - py;
          if (!this._isLightTheme) {
            this.ctx.globalAlpha = 0.40;
            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
            this.ctx.fillRect(pillX, pillY, tm.width + px * 2, fs + py * 2);
          }

          this.ctx.globalAlpha = isChoch ? 0.85 : 0.70;
          this.ctx.fillStyle = chochColor;
          this.ctx.fillText(label, x, lblRenderY);

          this.ctx.restore();
        }

        this._hitAreas.push({
          rect: { x: x - 20, y: Math.min(lblRenderY - fs - 2, yAnchor - fs - 2), w: 40, h: fs + 12 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? `${s.kind}`,
        });
      } else if (isInducement) {
        // ── Inducement: × marker, anchored to candle extreme ──
        const sz = Math.max(2, Math.min(5, Math.round(3 * mScale)));
        let yInd = yLevel;
        if (barMap) {
          const bar = barMap.get(s.time_ms / 1000);
          if (bar) {
            const anchorY = isBull
              ? this.toY(bar.high ?? s.price)
              : this.toY(bar.low ?? s.price);
            if (anchorY !== null) yInd = anchorY;
          }
        }
        const indOff = Math.round(6 * mScale);
        yInd += isBull ? -indOff : indOff;

        this.ctx.save();
        this.ctx.strokeStyle = '#ffa726';
        this.ctx.lineWidth = 1.0;
        this.ctx.globalAlpha = 0.50;
        this.ctx.beginPath();
        this.ctx.moveTo(x - sz, yInd - sz); this.ctx.lineTo(x + sz, yInd + sz);
        this.ctx.moveTo(x + sz, yInd - sz); this.ctx.lineTo(x - sz, yInd + sz);
        this.ctx.stroke();
        this.ctx.restore();

        this._hitAreas.push({
          rect: { x: x - sz - 2, y: yInd - sz - 2, w: sz * 2 + 4, h: sz * 2 + 4 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? 'Inducement',
        });
      } else if (isFractal) {
        // ── Williams Fractal: ▲/▼ (clean margin offset) ──
        const isHigh = s.kind === 'fractal_high';
        const sz = Math.max(2, Math.min(4, Math.round(2.5 * mScale)));
        const fOff = Math.max(4, Math.min(14, Math.round(10 * mScale)));
        this.ctx.save();
        this.ctx.globalAlpha = 0.35;
        this.ctx.fillStyle = isHigh ? '#ab47bc' : '#7e57c2';
        this.ctx.beginPath();
        if (isHigh) {
          this.ctx.moveTo(x, yLevel - sz - fOff);
          this.ctx.lineTo(x + sz, yLevel - fOff + 1);
          this.ctx.lineTo(x - sz, yLevel - fOff + 1);
        } else {
          this.ctx.moveTo(x, yLevel + sz + fOff);
          this.ctx.lineTo(x + sz, yLevel + fOff - 1);
          this.ctx.lineTo(x - sz, yLevel + fOff - 1);
        }
        this.ctx.closePath();
        this.ctx.fill();
        this.ctx.restore();

        this._hitAreas.push({
          rect: { x: x - sz - 2, y: isHigh ? yLevel - sz - fOff - 2 : yLevel + fOff - 2, w: sz * 2 + 4, h: sz + fOff + 4 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? 'Fractal',
        });
      } else if (isDisplacement) {
        // ── Displacement: candle body wash + directional chevron ──
        // Body wash = subtle gradient overlay on candle body (energy from within).
        // Chevron = tiny ▸ above/below candle to indicate direction.
        const bar = barMap?.get(s.time_ms / 1000);
        const accentColor = isBull ? '#00e676' : '#ff5252';
        const halfBar = Math.max(3, Math.round(scale * 3.5));

        if (bar) {
          const yHigh = this.toY(bar.high ?? s.price);
          const yLow = this.toY(bar.low ?? s.price);
          const bodyOpen = bar.open ?? s.price;
          const bodyClose = bar.close ?? s.price;
          const yBodyTop = this.toY(Math.max(bodyOpen, bodyClose));
          const yBodyBot = this.toY(Math.min(bodyOpen, bodyClose));

          if (yHigh !== null && yLow !== null && yBodyTop !== null && yBodyBot !== null) {
            // 1) Body wash: horizontal gradient across candle body
            const bodyH = Math.max(2, yBodyBot - yBodyTop);
            const washX = x - halfBar;
            const washW = halfBar * 2;
            const grad = this.ctx.createLinearGradient(washX, 0, washX + washW, 0);
            grad.addColorStop(0, accentColor);
            grad.addColorStop(1, 'transparent');
            this.ctx.save();
            this.ctx.globalAlpha = 0.18;
            this.ctx.fillStyle = grad;
            this.ctx.fillRect(washX, yBodyTop, washW, bodyH);
            this.ctx.restore();

            // 2) Directional chevron above/below wick
            const chevronSz = Math.max(2, Math.min(4, Math.round(2.5 * mScale)));
            const chevronOff = Math.round(8 * mScale);
            const chevronY = isBull ? yHigh - chevronOff : yLow + chevronOff;
            this.ctx.save();
            this.ctx.globalAlpha = 0.50;
            this.ctx.fillStyle = accentColor;
            this.ctx.beginPath();
            if (isBull) {
              // Upward chevron ▲
              this.ctx.moveTo(x, chevronY - chevronSz);
              this.ctx.lineTo(x + chevronSz, chevronY + 1);
              this.ctx.lineTo(x - chevronSz, chevronY + 1);
            } else {
              // Downward chevron ▼
              this.ctx.moveTo(x, chevronY + chevronSz);
              this.ctx.lineTo(x + chevronSz, chevronY - 1);
              this.ctx.lineTo(x - chevronSz, chevronY - 1);
            }
            this.ctx.closePath();
            this.ctx.fill();
            this.ctx.restore();

            this._hitAreas.push({
              rect: { x: washX - 1, y: yHigh - chevronOff - chevronSz - 2, w: washW + 2, h: Math.max(8, yLow - yHigh + chevronOff * 2 + chevronSz * 2 + 4) },
              tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? 'Displacement',
            });
          }
        }
      } else {
        // ── HH/HL/LH/LL: diamond marker (offset from wick tip) ──
        const r = Math.max(2, Math.min(4, Math.round(2.5 * mScale)));
        const isSwingHigh = s.kind === 'hh' || s.kind === 'lh';
        const swOff = Math.round(5 * mScale);
        const yDiamond = isSwingHigh ? yLevel - swOff : yLevel + swOff;

        this.ctx.save();
        this.ctx.globalAlpha = 0.40;
        this.ctx.fillStyle = color;
        this.ctx.beginPath();
        this.ctx.moveTo(x, yDiamond - r);
        this.ctx.lineTo(x + r, yDiamond);
        this.ctx.lineTo(x, yDiamond + r);
        this.ctx.lineTo(x - r, yDiamond);
        this.ctx.closePath();
        this.ctx.fill();
        this.ctx.restore();

        this._hitAreas.push({
          rect: { x: x - r - 2, y: yDiamond - r - 2, w: r * 2 + 4, h: r * 2 + 4 },
          tooltip: _SWING_TOOLTIP[s.kind ?? ''] ?? (s.kind?.toUpperCase() ?? 'Swing'),
        });
      }
    }
  }
}
