// src/chart/drawings/touchDrawCursor.ts
// ADR-0112 D3: курсор-перехрестя для малювання пальцем. Поки обрано інструмент, однопальцеві дотики на контейнері
// графіка (capture-фаза; контейнер — предок longPressLock і LWC, тож ті їх не бачать) не йдуть далі: палець будь-де
// веде курсор 1:1 (відносно, як тачпад — палець не закриває точку), точку ставить кнопка «Поставити» (place()). Два
// пальці — далі до LWC (масштаб). autoScale на час режиму заморожено (як у longPressLock): тіки не зсувають ціну під
// курсором. Курсор стає на бар (той самий, що показує перехрестя LWC з ціною й часом на осях).
// longPressLock.ts (🔒) не змінюється: режим живе лише поки інструмент обрано (ChartPane створює/знищує).

import type { IChartApi, ISeriesApi, SeriesType } from 'lightweight-charts';

export interface CursorPoint {
  readonly x: number;
  readonly y: number;
}

export interface CursorBounds {
  readonly width: number;
  readonly height: number;
}

/** Курсор, зсунутий на (dx, dy) і втиснутий у панель [0 .. width−1] × [0 .. height−1]. */
export function moveCursor(p: CursorPoint, dx: number, dy: number, b: CursorBounds): CursorPoint {
  return {
    x: Math.min(Math.max(p.x + dx, 0), Math.max(0, b.width - 1)),
    y: Math.min(Math.max(p.y + dy, 0), Math.max(0, b.height - 1)),
  };
}

/** Що курсор робить з фігурою (DrawingsRenderer): той самий шлях, що рух і клік мишею. */
export interface TouchCursorTarget {
  cursorMove(x: number, y: number): void;
  cursorPlace(x: number, y: number): 'draft' | 'done';
}

/** Кільце курсора поверх графіка (DOM — без змін у canvas-шарах; підписи на осях дає перехрестя LWC). */
const RETICLE_PX = 22;
const RETICLE_STYLE = [
  'position:absolute',
  'left:0',
  'top:0',
  `width:${RETICLE_PX}px`,
  `height:${RETICLE_PX}px`,
  'margin:-11px 0 0 -11px',
  'border-radius:50%',
  'border:1.5px solid var(--accent, #d4a017)',
  'box-shadow:0 0 0 1px rgba(0,0,0,0.55)',
  'pointer-events:none',
  'z-index:6',
].join(';');

export class TouchDrawCursor {
  private point: CursorPoint;
  private lastTouch: { x: number; y: number } | null = null;
  private readonly reticle: HTMLDivElement;
  private readonly prevAutoScale: boolean;
  /** Поворот телефона / зміна розміру: відповідність ціна↔піксель міняється, а курсор стоїть на місці екрана —
   *  перерахувати прев'ю й перехрестя (LWC застосовує новий розмір лише на своєму кадрі, тому через два кадри). */
  private readonly resizeObserver: ResizeObserver;
  private resizeFrame = 0;

  constructor(
    private readonly container: HTMLElement,
    private readonly chart: IChartApi,
    private readonly series: ISeriesApi<SeriesType>,
    private readonly target: TouchCursorTarget,
  ) {
    const b = this.bounds();
    this.point = { x: b.width / 2, y: b.height / 2 };
    this.reticle = document.createElement('div');
    this.reticle.className = 'touch-draw-reticle';
    this.reticle.setAttribute('style', RETICLE_STYLE);
    container.appendChild(this.reticle);
    this.prevAutoScale = series.priceScale().options().autoScale;
    series.priceScale().applyOptions({ autoScale: false });
    container.addEventListener('touchstart', this.onTouchStart, { capture: true, passive: false });
    container.addEventListener('touchmove', this.onTouchMove, { capture: true, passive: false });
    container.addEventListener('touchend', this.onTouchEnd, { capture: true, passive: false });
    container.addEventListener('touchcancel', this.onTouchEnd, { capture: true, passive: false });
    chart.timeScale().subscribeVisibleLogicalRangeChange(this.onRangeChange);
    this.resizeObserver = new ResizeObserver(this.onResize);
    this.resizeObserver.observe(container);
    this.sync();
  }

  /** «Поставити» в точці курсора. */
  place(): 'draft' | 'done' {
    const p = this.snapped();
    return this.target.cursorPlace(p.x, p.y);
  }

  destroy(): void {
    this.container.removeEventListener('touchstart', this.onTouchStart, true);
    this.container.removeEventListener('touchmove', this.onTouchMove, true);
    this.container.removeEventListener('touchend', this.onTouchEnd, true);
    this.container.removeEventListener('touchcancel', this.onTouchEnd, true);
    this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.onRangeChange);
    this.resizeObserver.disconnect();
    cancelAnimationFrame(this.resizeFrame);
    this.chart.clearCrosshairPosition();
    this.series.priceScale().applyOptions({ autoScale: this.prevAutoScale });
    this.reticle.remove();
  }

  private readonly onTouchStart = (e: TouchEvent): void => {
    if (e.touches.length !== 1) {
      this.lastTouch = null; // два пальці — масштаб LWC
      return;
    }
    this.swallow(e);
    this.lastTouch = { x: e.touches[0].clientX, y: e.touches[0].clientY };
  };

  private readonly onTouchMove = (e: TouchEvent): void => {
    if (e.touches.length !== 1 || !this.lastTouch) {
      this.lastTouch = null;
      return;
    }
    this.swallow(e);
    const t = e.touches[0];
    this.point = moveCursor(this.point, t.clientX - this.lastTouch.x, t.clientY - this.lastTouch.y, this.bounds());
    this.lastTouch = { x: t.clientX, y: t.clientY };
    this.sync();
  };

  private readonly onTouchEnd = (e: TouchEvent): void => {
    if (this.lastTouch) this.swallow(e);
    if (e.touches.length === 0) this.lastTouch = null;
  };

  /** Після масштабу/прокрутки курсор лишається на тому самому місці екрана — перерахувати прев'ю й перехрестя. */
  private readonly onRangeChange = (): void => this.sync();

  private readonly onResize = (): void => {
    cancelAnimationFrame(this.resizeFrame);
    this.resizeFrame = requestAnimationFrame(() => {
      this.resizeFrame = requestAnimationFrame(() => {
        this.point = moveCursor(this.point, 0, 0, this.bounds()); // курсор лишається в межах нової панелі
        this.sync();
      });
    });
  };

  private swallow(e: TouchEvent): void {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  /** Панель графіка без цінової шкали й осі часу (там, де малюються фігури). */
  private bounds(): CursorBounds {
    const rect = this.container.getBoundingClientRect();
    let width = rect.width;
    let height = rect.height;
    try {
      width -= this.chart.priceScale('right').width();
      height -= this.chart.timeScale().height();
    } catch (err) {
      console.warn('[touchDrawCursor] TOUCH_CURSOR_BOUNDS_FALLBACK — повний контейнер', err);
    }
    return { width: Math.max(0, width), height: Math.max(0, height) };
  }

  /** x курсора — на центр бару (як перехрестя LWC), y — як є. */
  private snapped(): CursorPoint {
    const timeScale = this.chart.timeScale();
    const logical = timeScale.coordinateToLogical(this.point.x);
    const x = logical === null ? null : timeScale.logicalToCoordinate(logical);
    return { x: x ?? this.point.x, y: this.point.y };
  }

  private sync(): void {
    const p = this.snapped();
    this.reticle.style.transform = `translate(${p.x}px, ${p.y}px)`;
    this.target.cursorMove(p.x, p.y);
    const price = this.series.coordinateToPrice(p.y);
    const time = this.chart.timeScale().coordinateToTime(p.x);
    // Майбутнє (праворуч від останнього бару) — у LWC часу нема: перехрестя знімаємо, курсор-кільце лишається.
    if (price !== null && time !== null) this.chart.setCrosshairPosition(price, time, this.series);
    else this.chart.clearCrosshairPosition();
  }
}
