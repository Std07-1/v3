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

// ─── Constants (V3 parity: chart_adapter_lite.js:155-156) ───
const VOLUME_UP = 'rgba(38, 166, 154, 0.32)';
const VOLUME_DOWN = 'rgba(239, 83, 80, 0.32)';
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
    M1: 5000, M3: 4000, M5: 3000, M15: 2000,
    M30: 1500, H1: 1200, H4: 800, D1: 500,
  };

  // ─── TF state (needed for D1 offset: chart_adapter_lite.js:258-280) ───
  private _tfS: number = 300; // default M5 (matches ws_server default)

  // ─── rAF throttle (V3 parity: chart_adapter_lite.js:837-900) ───
  private _rafPending: Candle | null = null;
  private _rafId: number | null = null;

  // ─── Crosshair callback ───
  private _onCrosshair: (data: CrosshairData) => void;

  constructor(
    container: HTMLElement,
    onCrosshairMove: (data: CrosshairData) => void,
    onScrollback: (oldest_ms: T_MS) => void,
  ) {
    this._onCrosshair = onCrosshairMove;

    // ─── Chart options (V3 parity: DARK_CHART_OPTIONS, chart_adapter_lite.js:101-148) ───
    this.chart = createChart(container, {
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
        maxBarSpacing: 12,
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
      color: c.c >= c.o ? VOLUME_UP : VOLUME_DOWN,
    };
  }

  // ─── setData: full frame + follow mode (V3: chart_adapter_lite.js:960-989) ───
  setData(bars: Candle[]): void {
    this.isFetchingScrollback = false;
    this.lastScrollbackToMs = null;
    this._totalBars = bars.length;
    this._setScrollbackState('idle');

    const mapped = bars.map((c) => this._mapCandle(c));
    const volMapped = bars.map((c, i) => this._mapVolume(c, mapped[i].time));

    this.series.setData(mapped);
    this.volumeSeries.setData(volMapped);

    // Follow mode: auto-scroll to latest (V3: scrollToRealTimeWithOffset)
    if (bars.length > 0) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  // ─── update: live delta with rAF throttle (V3: chart_adapter_lite.js:837-900) ───
  update(bar: Candle): void {
    this._rafPending = bar;
    if (!this._rafId) {
      this._rafId = requestAnimationFrame(() => this._flushUpdate());
    }
  }

  private _flushUpdate(): void {
    this._rafId = null;
    const bar = this._rafPending;
    if (!bar) return;
    this._rafPending = null;

    const mapped = this._mapCandle(bar);
    this.series.update(mapped);
    this.volumeSeries.update(this._mapVolume(bar, mapped.time));
  }

  // ─── prependData: scrollback (V4 + P3.15: wall detection + bar count) ───
  prependData(bars: Candle[]): void {
    this.isFetchingScrollback = false;
    if (bars.length === 0) {
      // Empty response = wall reached
      this._setScrollbackState('wall');
      return;
    }

    this._totalBars += bars.length;
    this._setScrollbackState('idle');

    const mapped = bars.map((c) => this._mapCandle(c));
    const volMapped = bars.map((c, i) => this._mapVolume(c, mapped[i].time));

    const currentCandle = this.series.data();
    const currentVol = this.volumeSeries.data();

    this.series.setData([...mapped, ...currentCandle]);
    this.volumeSeries.setData([...volMapped, ...currentVol]);
  }

  // ─── clearAll: for no-data + switch (V3: app.js:1594-1606) ───
  clearAll(): void {
    this.series.setData([]);
    this.volumeSeries.setData([]);
    this.isFetchingScrollback = false;
    this.lastScrollbackToMs = null;
    this._totalBars = 0;
    this._setScrollbackState('idle');
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

  // ─── P3.11: Multi-theme switching (V3: setTheme, chart_adapter_lite.js:1212-1224) ───
  applyTheme(name: ThemeName): void {
    const t = THEMES[name];
    if (!t) return;
    this.chart.applyOptions(t.chart as any);
    saveTheme(name);
  }

  // ─── P3.12: Candle style presets (V3: CANDLE_STYLES, chart_adapter_lite.js:162-198) ───
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
    this.chart.remove();
  }
}

// ─── Export TF mapping for ChartPane use ───
export { TF_TO_S };
export type { ThemeName, CandleStyleName };