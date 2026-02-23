// src/chart/engine.ts
// V3→V4 Parity: Dark theme, Volume, D1 offset, UTC formatters,
// Follow mode, rAF throttle, scrollback rails.
// P3.11: Multi-theme (applyTheme). P3.12: Candle styles (applyCandleStyle).
// SSOT: chart_adapter_lite.js (V3 reference, 1254 LOC)

import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts';
import {
  type ThemeName, type CandleStyleName,
  THEMES, CANDLE_STYLES,
  loadTheme, saveTheme, loadCandleStyle, saveCandleStyle,
} from './themes';
import type { Candle, T_MS } from '../types';

// ─── Helpers ───

/** Apply alpha to hex (#rrggbb) or rgba() color string */
function _withAlpha(color: string, alpha: number): string {
  if (color.startsWith('#') && color.length >= 7) {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  if (color.startsWith('rgba(')) {
    return color.replace(/,\s*[\d.]+\)$/, `, ${alpha})`);
  }
  return color;
}

// Default volume colors (classic style)
const VOLUME_ALPHA = 0.32;
const D1_OFFSET_MS = 10_800_000; // +3h: FXCM D1 open 22:00/21:00 UTC → nominal date

// ─── UTC formatters (V3 parity: chart_adapter_lite.js:13-28) ───
function _fmtUtcTime(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

function _fmtUtcDate(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  const yy = d.getUTCFullYear();
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${yy}-${mo}-${dd}`;
}

function _fmtUtcFull(epochSec: number): string {
  return `${_fmtUtcDate(epochSec)} ${_fmtUtcTime(epochSec)} UTC`;
}

// ─── TF label → seconds mapping ───
const TF_TO_S: Record<string, number> = {
  M1: 60, M3: 180, M5: 300, M15: 900,
  M30: 1800, H1: 3600, H4: 14400, D1: 86400,
};

export type CrosshairData = {
  time: Time | null;
  o: number; h: number; l: number; c: number;
  v: number;
  x: number; y: number;
  inRange: boolean;
};

export class ChartEngine {
  chart: IChartApi;
  series: ISeriesApi<'Candlestick'>;
  volumeSeries: ISeriesApi<'Histogram'>;

  // ─── Scrollback rails (V4 original, preserved) ───
  private isFetchingScrollback = false;
  private lastScrollbackToMs: T_MS | null = null;

  // P3.15: Scrollback policy — state + wall detection + bar cap
  private _scrollbackState: 'idle' | 'loading' | 'wall' = 'idle';
  private _onScrollbackStateChange: ((state: 'idle' | 'loading' | 'wall') => void) | null = null;
  private _totalBars = 0;
  // Max bars per TF to prevent memory bloat (configurable)
  static readonly MAX_BARS: Record<string, number> = {
    M1: 10000, M3: 8000, M5: 6000, M15: 4000,
    M30: 3000, H1: 2500, H4: 1500, D1: 1000,
  };

  // ─── TF state (needed for D1 offset: chart_adapter_lite.js:258-280) ───
  private _tfS: number = 300; // default M5 (matches ws_server default)

  // ─── P3.7: Follow mode (V3 parity: chart_adapter_lite.js:947-1001) ───
  private _followEnabled = true; // user toggle: auto-scroll on new bars

  // ─── rAF throttle + head guard (V3 parity: chart_adapter_lite.js:837-900) ───
  private _rafQueue: Candle[] = [];
  private _rafId: number | null = null;
  private _headTMs: number = 0;  // найвищий t_ms, відправлений в LWC via update()

  // ─── Left edge visibility (for "no more history" indicator) ───
  private _leftEdgeVisible = false;
  private _onLeftEdgeChange: ((v: boolean) => void) | null = null;

  // ─── Volume colors (sync with candle style, Entry 078) ───
  private _volUpColor: string;
  private _volDownColor: string;

  // ─── Idle auto-recenter timer (Entry 078: §6) ───
  private _idleTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly IDLE_RECENTER_MS = 15_000; // 15s

  // ─── Crosshair callback ───
  private _onCrosshair: (data: CrosshairData) => void;

  constructor(
    container: HTMLElement,
    onCrosshairMove: (data: CrosshairData) => void,
    onScrollback: (oldest_ms: T_MS) => void,
  ) {
    this._onCrosshair = onCrosshairMove;

    // Init volume colors from default candle style
    this._volUpColor = _withAlpha(CANDLE_STYLES.classic.upColor, VOLUME_ALPHA);
    this._volDownColor = _withAlpha(CANDLE_STYLES.classic.downColor, VOLUME_ALPHA);

    // ─── Chart options (V3 parity: DARK_CHART_OPTIONS, chart_adapter_lite.js:101-148) ───
    this.chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#d5d5d5',
      },
      grid: {
        vertLines: { color: 'rgba(43, 56, 70, 0.4)' },
        horzLines: { color: 'rgba(43, 56, 70, 0.4)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(213, 213, 213, 0.35)',
          width: 1,
          style: LineStyle.Dashed,
        },
        horzLine: {
          color: 'rgba(213, 213, 213, 0.35)',
          width: 1,
          style: LineStyle.Dashed,
        },
      },
      // V3 parity: chart_adapter_lite.js:52-66
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        vertTouchDrag: true,
        horzTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
      // V3 parity: chart_adapter_lite.js:67-75
      rightPriceScale: {
        borderVisible: true,
        ticksVisible: true,
        autoScale: true,
        scaleMargins: { top: 0.12, bottom: 0.18 },
      },
      // V3 parity: chart_adapter_lite.js:76-98
      timeScale: {
        borderVisible: false,
        rightOffset: 3, // follow mode: ~3 bars right padding
        barSpacing: 8,
        maxBarSpacing: 50,
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: false,
        fixRightEdge: false,
        lockVisibleTimeRangeOnResize: false,
        tickMarkFormatter: (time: Time) => {
          if (typeof time === 'string') return ''; // D1: LWC renders date natively
          if (typeof time === 'number') return _fmtUtcTime(time);
          return '';
        },
      },
      localization: {
        timeFormatter: (time: Time) => {
          if (typeof time === 'string') return time as string; // D1
          if (typeof time === 'number') return _fmtUtcFull(time);
          return '';
        },
      },
    });

    // ─── Candlestick series (V3 classic style: chart_adapter_lite.js:158-166) ───
    this.series = this.chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderUpColor: '#26a69a',
      borderDownColor: '#ef5350',
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
      priceLineVisible: false,
      lastValueVisible: true,
    });

    // ─── Volume histogram (V3 parity: chart_adapter_lite.js:221-243) ───
    this.volumeSeries = this.chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    this.volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.895, bottom: 0 },
    });

    // ─── P3.11/P3.12: Restore saved theme + candle style from localStorage ───
    const savedTheme = loadTheme();
    if (savedTheme !== 'dark') this.applyTheme(savedTheme);
    const savedStyle = loadCandleStyle();
    if (savedStyle !== 'classic') this.applyCandleStyle(savedStyle);

    // ─── Crosshair (V3 parity: chart_adapter_lite.js:400-456) ───
    this.chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) {
        this._onCrosshair({
          time: null, o: 0, h: 0, l: 0, c: 0, v: 0,
          x: 0, y: 0, inRange: false,
        });
        return;
      }
      const bar = param.seriesData.get(this.series);
      if (!bar || !('open' in bar)) {
        this._onCrosshair({
          time: null, o: 0, h: 0, l: 0, c: 0, v: 0,
          x: 0, y: 0, inRange: false,
        });
        return;
      }
      const point = param.point;
      // V3 parity: isPointInCandleRange guard (chart_adapter_lite.js:385-398)
      let inRange = true;
      if (point) {
        const yH = this.series.priceToCoordinate(bar.high);
        const yL = this.series.priceToCoordinate(bar.low);
        if (yH !== null && yL !== null) {
          const top = Math.min(yH, yL);
          const bottom = Math.max(yH, yL);
          inRange = point.y >= top - 20 && point.y <= bottom + 20;
        }
      }
      // Volume lookup
      const volBar = param.seriesData.get(this.volumeSeries);
      const v = (volBar && 'value' in volBar) ? (volBar as any).value ?? 0 : 0;

      this._onCrosshair({
        time: param.time,
        o: bar.open, h: bar.high, l: bar.low, c: bar.close,
        v,
        x: point?.x ?? 0,
        y: point?.y ?? 0,
        inRange,
      });
    });

    // ─── Scrollback rail (V4 + P3.15 policy: dedupe + wall + bar cap) ───
    let debounceTimer: ReturnType<typeof setTimeout>;
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      // Track left edge visibility for "no more history" display
      const leftVis = range != null && range.from < 1;
      if (leftVis !== this._leftEdgeVisible) {
        this._leftEdgeVisible = leftVis;
        this._onLeftEdgeChange?.(leftVis);
      }
      // Entry 078 §6: Reset idle auto-recenter timer on any range change
      this._resetIdleTimer();
      if (range && range.from < 10) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          if (this.isFetchingScrollback) return;
          if (this._scrollbackState === 'wall') return; // wall reached, stop
          // P3.15: Bar cap check
          const tfLabel = Object.entries(TF_TO_S).find(([, v]) => v === this._tfS)?.[0] ?? 'M5';
          const maxBars = ChartEngine.MAX_BARS[tfLabel] ?? 3000;
          if (this._totalBars >= maxBars) {
            this._setScrollbackState('wall');
            return;
          }
          const barsInfo = this.series.barsInLogicalRange(range);
          if (barsInfo && barsInfo.barsBefore < 5) {
            const data = this.series.data();
            if (data.length > 0) {
              const targetMs = this._timeToMs(data[0].time);
              if (!Number.isFinite(targetMs) || targetMs <= 0) return;
              // Dedupe rail (LWC_Engine_dedupe spec)
              if (targetMs === this.lastScrollbackToMs) return;
              this.lastScrollbackToMs = targetMs;
              this.isFetchingScrollback = true;
              this._setScrollbackState('loading');
              // Safety timeout: reset flag after 3s if no response (aggressive drag recovery)
              setTimeout(() => {
                this.isFetchingScrollback = false;
                if (this._scrollbackState === 'loading') this._setScrollbackState('idle');
              }, 3_000);
              onScrollback(targetMs);
            }
          }
        }, 150);
      }
    });
  }

  // ─── TF setter (called by ChartPane on switch/full frame) ───
  setTfS(tfS: number): void {
    this._tfS = tfS;
  }

  // ─── RAIL: Time Domain mapping (V3: chart_adapter_lite.js:260-308) ───
  //
  // D1 (≥86400s): LWC requires 'YYYY-MM-DD' string.
  //   FXCM D1 open = 22:00 UTC (winter) / 21:00 UTC (DST).
  //   +3h offset: 22:00→01:00 / 21:00→00:00 next day = correct nominal date.
  //   Evidence: chart_adapter_lite.js:263-275
  //
  // Intraday: epoch seconds (number).
  private _mapCandle(c: Candle): {
    time: Time; open: number; high: number; low: number; close: number;
  } {
    let time: Time;
    if (this._tfS >= 86400) {
      const d = new Date(c.t_ms + D1_OFFSET_MS);
      const yyyy = d.getUTCFullYear();
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
      const dd = String(d.getUTCDate()).padStart(2, '0');
      time = `${yyyy}-${mm}-${dd}` as unknown as Time;
    } else {
      time = Math.floor(c.t_ms / 1000) as Time;
    }
    return { time, open: c.o, high: c.h, low: c.l, close: c.c };
  }

  private _mapVolume(c: Candle, mappedTime: Time): {
    time: Time; value: number; color: string;
  } {
    return {
      time: mappedTime,
      value: c.v ?? 0,
      color: c.c >= c.o ? this._volUpColor : this._volDownColor,
    };
  }

  // ─── setData: full frame + follow mode (V3: chart_adapter_lite.js:960-989) ───
  setData(bars: Candle[]): void {
    this.isFetchingScrollback = false;
    this.lastScrollbackToMs = null;
    this._totalBars = bars.length;
    this._setScrollbackState('idle');

    // Reset delta state on full frame (prevent stale updates from previous symbol/tf)
    this._rafQueue = [];
    this._headTMs = bars.length > 0 ? bars[bars.length - 1].t_ms : 0;

    const mapped = bars.map((c) => this._mapCandle(c));
    const volMapped = bars.map((c, i) => this._mapVolume(c, mapped[i].time));

    this.series.setData(mapped);
    this.volumeSeries.setData(volMapped);

    // P3.7: Follow mode — auto-scroll only if follow enabled
    // On setData (full frame), always scroll to latest regardless of isAtEnd
    // because user explicitly switched symbol/TF.
    if (bars.length > 0 && this._followEnabled) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  // ─── update: live delta with rAF queue + try/catch for LWC safety ───
  //
  // LWC series.update() може оновити тільки останній бар або додати новий.
  // Якщо bar.time < head → "Cannot update oldest data" exception.
  // Рішення: акумулюємо бари в queue, dedup по t_ms, сортуємо, і
  // для late bars (t_ms < head) — targeted data replacement через setData().
  update(bar: Candle): void {
    this._rafQueue.push(bar);
    if (!this._rafId) {
      this._rafId = requestAnimationFrame(() => this._flushUpdate());
    }
  }

  private _flushUpdate(): void {
    this._rafId = null;
    const queue = this._rafQueue;
    this._rafQueue = [];
    if (queue.length === 0) return;

    // Dedup by t_ms: keep last entry per t_ms (final overwrites preview)
    const deduped = new Map<number, Candle>();
    for (const c of queue) deduped.set(c.t_ms, c);

    // Sort ascending by t_ms for monotonic LWC update order
    const sorted = [...deduped.values()].sort((a, b) => a.t_ms - b.t_ms);

    // Collect late bars for batch replacement
    const lateBars: Array<{ bar: Candle; mapped: ReturnType<ChartEngine['_mapCandle']> }> = [];

    for (const bar of sorted) {
      const mapped = this._mapCandle(bar);
      if (bar.t_ms < this._headTMs) {
        // Late bar (final arriving after preview of next minute) — collect for batch replace
        lateBars.push({ bar, mapped });
        continue;
      }
      // Normal path: update() for head or newer bars
      try {
        this.series.update(mapped);
        this.volumeSeries.update(this._mapVolume(bar, mapped.time));
        if (bar.t_ms > this._headTMs) this._headTMs = bar.t_ms;
      } catch {
        // Safety: if update() still fails (edge case), use replacement path
        lateBars.push({ bar, mapped });
      }
    }

    // Batch-replace all late bars via data splice + setData
    if (lateBars.length > 0) {
      this._replacePastBars(lateBars);
    }

    // Entry 078 §6: no auto-scroll on live updates.
    // User controls scroll position; realign only on price-axis dblclick
    // or idle timer (15s, slight offset).
  }

  /**
   * Замінює бари в минулому через series.setData() зі збереженням viewport.
   * Викликається для late finals (final N приходить після preview N+1).
   * Batch: одна операція setData() для всіх late bars.
   */
  private _replacePastBars(
    items: Array<{ bar: Candle; mapped: ReturnType<ChartEngine['_mapCandle']> }>,
  ): void {
    // Filter WhitespaceData (no OHLC) to prevent "Value is null" crash (Entry 076)
    const data = (this.series.data() as any[])
      .filter((d) => 'open' in d && d.open != null)
      .map(d => ({ ...d }));
    const volData = (this.volumeSeries.data() as any[])
      .filter((d) => 'value' in d && d.value != null)
      .map(d => ({ ...d }));

    let replaced = 0;
    for (const { bar, mapped } of items) {
      // Find existing bar by time
      const idx = data.findIndex(
        (d: any) => d.time === mapped.time,
      );
      if (idx < 0) continue; // bar not on chart — skip
      data[idx] = mapped;
      if (idx < volData.length) {
        volData[idx] = this._mapVolume(bar, mapped.time);
      }
      replaced++;
    }
    if (replaced === 0) return;

    // Preserve viewport position
    const range = this.chart.timeScale().getVisibleLogicalRange();
    try {
      this.series.setData(data);
      this.volumeSeries.setData(volData);
    } catch (err) {
      console.error('[ChartEngine] _replacePastBars setData crashed:', err);
      return;
    }
    if (range) {
      this.chart.timeScale().setVisibleLogicalRange(range);
    }
  }

  // ─── prependData: scrollback (V4 + P3.15: wall detection + bar count) ───
  // Guard: dedup overlapping bars (server to_open_ms is inclusive),
  // filter WhitespaceData, move state reset AFTER setData to prevent
  // infinite crash loop (Entry 076).
  prependData(bars: Candle[]): void {
    if (bars.length === 0) {
      this.isFetchingScrollback = false;
      this._setScrollbackState('wall');
      return;
    }

    const mapped = bars.map((c) => this._mapCandle(c));
    const volMapped = bars.map((c, i) => this._mapVolume(c, mapped[i].time));

    // Dedup: server to_open_ms is inclusive → scrollback may return bars
    // already on chart. Remove overlapping times from current data.
    // Also filter WhitespaceData items ({time} only, no OHLC) that may
    // appear after failed setData or LWC internal state corruption.
    const newTimes = new Set(mapped.map((b) => b.time));
    const currentCandle = (this.series.data() as any[]).filter(
      (d) => 'open' in d && d.open != null && !newTimes.has(d.time),
    );
    const currentVol = (this.volumeSeries.data() as any[]).filter(
      (d) => 'value' in d && d.value != null && !newTimes.has(d.time),
    );

    try {
      this.series.setData([...mapped, ...currentCandle]);
      this.volumeSeries.setData([...volMapped, ...currentVol]);
    } catch (err) {
      console.error('[ChartEngine] prependData setData crashed:', err);
      this.isFetchingScrollback = false;
      this._setScrollbackState('wall');
      return;
    }

    this._totalBars += bars.length;
    this.isFetchingScrollback = false;
    this._setScrollbackState('idle');
  }

  // ─── clearAll: for no-data + switch (V3: app.js:1594-1606) ───
  clearAll(): void {
    this.series.setData([]);
    this.volumeSeries.setData([]);
    this.isFetchingScrollback = false;
    this.lastScrollbackToMs = null;
    this._totalBars = 0;
    this._setScrollbackState('idle');
    this._rafQueue = [];
    this._headTMs = 0;
  }

  // ─── P3.15: Scrollback state management ───
  private _setScrollbackState(state: 'idle' | 'loading' | 'wall'): void {
    if (this._scrollbackState === state) return;
    this._scrollbackState = state;
    this._onScrollbackStateChange?.(state);
  }

  /** P3.15: Register callback for scrollback state changes */
  onScrollbackState(cb: (state: 'idle' | 'loading' | 'wall') => void): void {
    this._onScrollbackStateChange = cb;
  }

  /** P3.15: Current scrollback state */
  get scrollbackState(): 'idle' | 'loading' | 'wall' {
    return this._scrollbackState;
  }

  /** Register callback for left-edge visibility changes (Entry 077) */
  onLeftEdgeChange(cb: (visible: boolean) => void): void {
    this._onLeftEdgeChange = cb;
  }

  /** Whether the left edge of the chart data is currently visible */
  get leftEdgeVisible(): boolean {
    return this._leftEdgeVisible;
  }

  // ─── P3.7: Follow toggle + isAtEnd (V3 parity: chart_adapter_lite.js:991-1001) ───

  /**
   * Чи юзер знаходиться біля правого краю графіка.
   * V3 reference: isAtEnd(thresholdBars) in chart_adapter_lite.js:991-995.
   * Використовується для auto-scroll guard: якщо юзер скролив вліво — не скролимо назад.
   */
  isAtEnd(thresholdBars = 3): boolean {
    const pos = this.chart.timeScale().scrollPosition();
    // pos < 0 = scrolled left, pos 0 = at right edge, pos > 0 = past right edge
    if (!Number.isFinite(pos)) return true; // no data or not scrollable
    return pos >= -thresholdBars;
  }

  /** P3.7: Follow mode toggle (user checkbox) */
  get followEnabled(): boolean {
    return this._followEnabled;
  }
  set followEnabled(val: boolean) {
    this._followEnabled = val;
    // If re-enabled, immediately scroll to real-time
    if (val) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  // ─── Time → epoch ms (handles both D1 string "YYYY-MM-DD" and intraday epoch sec) ───
  private _timeToMs(time: Time): number {
    if (typeof time === 'number') return time * 1000;
    if (typeof time === 'string') {
      // D1: "YYYY-MM-DD" → undo the +3h FXCM offset applied in _mapCandle
      const ms = Date.parse(time + 'T00:00:00Z');
      return Number.isFinite(ms) ? ms - D1_OFFSET_MS : 0;
    }
    return 0;
  }

  // ─── Entry 078 §6: Idle auto-recenter (15s, slight left offset only) ───
  private _resetIdleTimer(): void {
    if (this._idleTimer) clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => this._tryIdleRecenter(), ChartEngine.IDLE_RECENTER_MS);
  }

  private _tryIdleRecenter(): void {
    this._idleTimer = null;
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const pos = this.chart.timeScale().scrollPosition();
    if (!Number.isFinite(pos)) return;
    // Only recenter when slightly shifted left (within 15% of visible area)
    const visibleBars = range.to - range.from;
    const threshold = visibleBars * 0.15;
    // pos < 0 → scrolled left, pos > 0 → past right edge
    // Recenter only when slightly left: -threshold < pos < 0
    if (pos >= -threshold && pos < 0) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  // ─── Entry 078 §2: Refresh volume colors to match candle style ───
  private _refreshVolumeColors(): void {
    const cData = this.series.data() as any[];
    const vData = (this.volumeSeries.data() as any[]).map(d => ({ ...d }));
    for (let i = 0; i < Math.min(cData.length, vData.length); i++) {
      const c = cData[i];
      if ('close' in c && 'open' in c) {
        vData[i] = { ...vData[i], color: c.close >= c.open ? this._volUpColor : this._volDownColor };
      }
    }
    this.volumeSeries.setData(vData);
  }

  // ─── P3.11: Multi-theme switching (V3: setTheme, chart_adapter_lite.js:1212-1224) ───
  applyTheme(name: ThemeName): void {
    const t = THEMES[name];
    if (!t) return;
    this.chart.applyOptions(t.chart as any);
    saveTheme(name);
  }

  // ─── P3.12: Candle style presets + Entry 078 §2 volume color sync ───
  applyCandleStyle(name: CandleStyleName): void {
    const s = CANDLE_STYLES[name];
    if (!s) return;
    this.series.applyOptions({
      upColor: s.upColor,
      downColor: s.downColor,
      borderUpColor: s.borderUpColor,
      borderDownColor: s.borderDownColor,
      wickUpColor: s.wickUpColor,
      wickDownColor: s.wickDownColor,
    });
    // Entry 078: sync volume colors with candle style
    this._volUpColor = _withAlpha(s.upColor, VOLUME_ALPHA);
    this._volDownColor = _withAlpha(s.downColor, VOLUME_ALPHA);
    this._refreshVolumeColors();
    saveCandleStyle(name);
  }

  // ─── Getters for current settings ───
  get currentTheme(): ThemeName { return loadTheme(); }
  get currentCandleStyle(): CandleStyleName { return loadCandleStyle(); }

  // ─── destroy ───
  destroy(): void {
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._idleTimer) {
      clearTimeout(this._idleTimer);
      this._idleTimer = null;
    }
    this._rafQueue = [];
    this.chart.remove();
  }
}

// ─── Export TF mapping for ChartPane use ───
export { TF_TO_S };
export type { ThemeName, CandleStyleName };