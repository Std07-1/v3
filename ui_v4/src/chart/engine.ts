import { createChart, type IChartApi, type ISeriesApi, type Time } 
from 'lightweight-charts'; 
import type { Candle, T_MS } from '../types'; 
 
export class ChartEngine { 
  chart: IChartApi; 
  series: ISeriesApi<"Candlestick">; 
   
  // Захист від подвійних запитів (single-inflight rule) 
  private isFetchingScrollback = false; 
 
  constructor( 
    container: HTMLElement, 
    onCrosshairMove: (param: any) => void, 
    onScrollback: (oldest_ms: T_MS) => void 
  ) { 
    this.chart = createChart(container, { 
      layout: { background: { color: 'transparent' }, textColor: 
'#c8cdd6' }, 
      grid: { vertLines: { color: '#252930' }, horzLines: { color: 
'#252930' } }, 
      crosshair: { mode: 0 }, 
      timeScale: { timeVisible: true, secondsVisible: false }, 
    }); 
 
    this.series = this.chart.addCandlestickSeries({ 
      upColor: '#26a69a', downColor: '#ef5350', 
      borderVisible: false, wickUpColor: '#26a69a', wickDownColor: 
'#ef5350', 
    }); 
 
    this.chart.subscribeCrosshairMove(onCrosshairMove); 
 
    // RAIL: Scrollback trigger (Debounce + Single-inflight + Dedupe) 
    let debounceTimer: ReturnType<typeof setTimeout>; 
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((range) 
=> { 
      if (range && range.from < 10) { 
        clearTimeout(debounceTimer); 
        debounceTimer = setTimeout(() => { 
          if (this.isFetchingScrollback) return; // Чекаємо на 
попередню відповідь 
           
          const barsInfo = this.series.barsInLogicalRange(range); 
          if (barsInfo && barsInfo.barsBefore < 5) { 
            const data = this.series.data(); 
            if (data.length > 0) { 
              const oldestTimeSec = data[0].time as number; 
              this.isFetchingScrollback = true; 
              onScrollback(oldestTimeSec * 1000); // T_SEC -> T_MS 
            } 
          } 
        }, 150); 
      } 
    }); 
  } 
 
  // RAIL: Time Domain conversion (T_MS -> T_SEC) 
  private mapCandle(c: Candle) { 
    return { 
      time: Math.floor(c.t_ms / 1000) as Time, 
      open: c.o, high: c.h, low: c.l, close: c.c 
    }; 
  } 
 
  setData(bars: Candle[]) { 
    this.isFetchingScrollback = false; // Скидаємо лок при отриманні 
'full' 
    this.series.setData(bars.map(this.mapCandle)); 
  } 
 
  update(bar: Candle) { 
    this.series.update(this.mapCandle(bar)); 
  } 
 
  prependData(bars: Candle[]) { 
    this.isFetchingScrollback = false; // Скидаємо лок 
    if (bars.length === 0) return; 
     
    // LWC v5: setData повністю замінює масив, але зберігає позицію 
скролу, якщо ми просто "дошиваємо" зліва 
    const mapped = bars.map(this.mapCandle); 
    const currentData = this.series.data(); 
    this.series.setData([...mapped, ...currentData]); 
  } 
 
  destroy() { 
    this.chart.remove(); 
  } 
}