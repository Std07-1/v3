// src/chart/drawings/touchDrawCursor.ts
// ADR-0112 D3: курсор-перехрестя для малювання пальцем. Поки обрано інструмент, однопальцеві дотики на контейнері
// графіка (capture-фаза; контейнер — предок longPressLock і LWC, тож ті їх не бачать) не йдуть далі: палець будь-де
// веде курсор 1:1 (відносно, як тачпад — палець не закриває точку), точку ставить кнопка «Поставити» (place()). Два
// пальці — далі до LWC (масштаб). autoScale на час режиму заморожено (як у longPressLock): тіки не зсувають ціну під
// курсором. Курсор стає на бар (той самий, що показує перехрестя LWC з ціною й часом на осях).
// longPressLock.ts (🔒) не змінюється: режим живе лише поки інструмент обрано (ChartPane створює/знищує).
// D6: поки палець веде кільце — лупа поряд (touchLoupe.ts). D7: з магнітом кільце стає на ціну свічки, до якої
// прилипло, і на кожен новий рівень — onSnap (ChartPane: коротка вібрація).

import type { IChartApi, ISeriesApi, SeriesType } from 'lightweight-charts';
import { TouchLoupe } from './touchLoupe';

/** Що зробило «Поставити»: 'draft' — перша точка, фігура в роботі; 'done' — фігура готова (або вимір заморожено);
 *  'none' — нічого (дві точки в одному місці або точку не перевести) — інструмент лишається. */
export type CursorPlaceResult = 'draft' | 'done' | 'none';

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

/** Тап = поставити точку в кільці: палець майже не рушив і відпущений швидко (як короткий тап виходу ADR-0108). */
const TAP_SLOP_PX = 8;
const TAP_MAX_MS = 300;

/** Дотик був тапом (точка), а не веденням кільця: пройдений шлях < 8 px і тривалість < 300 мс. */
export function isTap(travelPx: number, durationMs: number): boolean {
  return travelPx < TAP_SLOP_PX && durationMs < TAP_MAX_MS;
}

/** Кільце прилипло до НОВОГО рівня (з вільного руху або з іншого рівня) — привід «клацнути». */
export function isNewSnapLevel(prev: number | null, next: number | null): boolean {
  return next !== null && next !== prev;
}

const POINTER_TYPES = ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'] as const;

/** Дотик по самому графіку (полотна LWC і шарів), а не по кнопках поверх нього. */
function onChartSurface(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('canvas') !== null;
}

/** Що курсор робить з фігурою (DrawingsRenderer): той самий шлях, що рух і клік мишею. cursorMove повертає ціну,
 *  до якої прилип магніт, або null. */
export interface TouchCursorTarget {
  cursorMove(x: number, y: number): number | null;
  cursorPlace(x: number, y: number): CursorPlaceResult;
}

/** Відгук курсора для ChartPane: тап по графіку — поставити точку; кільце прилипло до нового рівня. */
export interface TouchCursorHooks {
  readonly onTap?: () => void;
  readonly onSnap?: () => void;
}

/** Кільце курсора поверх графіка (DOM — без змін у canvas-шарах; підписи на осях дає перехрестя LWC). Прилипле до
 *  рівня — світиться яскравіше. */
const RETICLE_GLOW = '0 0 0 1px rgba(0,0,0,0.55), 0 0 12px rgba(212,160,23,0.28)';
const RETICLE_GLOW_SNAPPED = '0 0 0 1px rgba(0,0,0,0.55), 0 0 14px 2px rgba(212,160,23,0.6)';
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
  'background:radial-gradient(circle, var(--accent, #d4a017) 0 1.6px, transparent 2.2px)',
  `box-shadow:${RETICLE_GLOW}`,
  'pointer-events:none',
  'z-index:21', // над шаром малювань (20) — чернетка не закриває кільце
].join(';');

export class TouchDrawCursor {
  private point: CursorPoint;
  private lastTouch: { x: number; y: number } | null = null;
  /** Початок поточного дотику і пройдений шлях — щоб відрізнити тап (точка) від ведення кільця. */
  private touchStart: { at: number; travel: number } | null = null;
  private readonly reticle: HTMLDivElement;
  private readonly loupe: TouchLoupe;
  /** Рівень, до якого кільце прилипло на попередньому кроці, — щоб «клацати» лише на новому. */
  private lastSnapPrice: number | null = null;
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
    private readonly hooks: TouchCursorHooks = {},
    /** Верхня межа лупи (під рядками HUD), CSS px панелі. */
    loupeMinTop = 0,
  ) {
    const b = this.bounds();
    this.point = { x: b.width / 2, y: b.height / 2 };
    this.reticle = document.createElement('div');
    this.reticle.className = 'touch-draw-reticle';
    this.reticle.setAttribute('style', RETICLE_STYLE);
    container.appendChild(this.reticle);
    this.loupe = new TouchLoupe(container, chart.options().crosshair.horzLine.color, loupeMinTop);
    this.prevAutoScale = series.priceScale().options().autoScale;
    series.priceScale().applyOptions({ autoScale: false });
    container.addEventListener('touchstart', this.onTouchStart, { capture: true, passive: false });
    container.addEventListener('touchmove', this.onTouchMove, { capture: true, passive: false });
    container.addEventListener('touchend', this.onTouchEnd, { capture: true, passive: false });
    container.addEventListener('touchcancel', this.onTouchEnd, { capture: true, passive: false });
    // Pointer-події того самого пальця — окремий потік: без цього interaction.ts (вертикальна прокрутка / масштаб осі
    // на хості LWC) рахував би рух пальця в ручний діапазон ціни, і на виході з режиму графік стрибав би (рецензія D-1).
    for (const type of POINTER_TYPES) container.addEventListener(type, this.onPointer, { capture: true, passive: false });
    chart.timeScale().subscribeVisibleLogicalRangeChange(this.onRangeChange);
    this.resizeObserver = new ResizeObserver(this.onResize);
    this.resizeObserver.observe(container);
    this.sync();
  }

  /** «Поставити» в точці курсора. */
  place(): CursorPlaceResult {
    const p = this.snapped();
    return this.target.cursorPlace(p.x, p.y);
  }

  destroy(): void {
    this.container.removeEventListener('touchstart', this.onTouchStart, true);
    this.container.removeEventListener('touchmove', this.onTouchMove, true);
    this.container.removeEventListener('touchend', this.onTouchEnd, true);
    this.container.removeEventListener('touchcancel', this.onTouchEnd, true);
    for (const type of POINTER_TYPES) this.container.removeEventListener(type, this.onPointer, true);
    this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.onRangeChange);
    this.resizeObserver.disconnect();
    cancelAnimationFrame(this.resizeFrame);
    this.chart.clearCrosshairPosition();
    this.loupe.destroy();
    // Повертаємо autoScale, лише якщо його ніхто не змінив за час режиму (longPressLock на виході зі свого режиму
    // вмикає його сам — тоді не перетирати, рецензія D-4).
    if (this.series.priceScale().options().autoScale === false) {
      this.series.priceScale().applyOptions({ autoScale: this.prevAutoScale });
    }
    this.reticle.remove();
  }

  private readonly onTouchStart = (e: TouchEvent): void => {
    if (e.touches.length !== 1) {
      this.lastTouch = null; // два пальці — масштаб LWC
      this.touchStart = null;
      this.loupe.hide();
      return;
    }
    if (!onChartSurface(e.target)) return; // панель SMC тощо всередині контейнера — їхні кнопки працюють (D-5)
    this.swallow(e);
    this.lastTouch = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    this.touchStart = { at: e.timeStamp, travel: 0 };
  };

  private readonly onTouchMove = (e: TouchEvent): void => {
    if (e.touches.length !== 1 || !this.lastTouch) {
      this.lastTouch = null;
      return;
    }
    this.swallow(e);
    const t = e.touches[0];
    if (this.touchStart) this.touchStart.travel += Math.hypot(t.clientX - this.lastTouch.x, t.clientY - this.lastTouch.y);
    this.point = moveCursor(this.point, t.clientX - this.lastTouch.x, t.clientY - this.lastTouch.y, this.bounds());
    this.lastTouch = { x: t.clientX, y: t.clientY };
    this.sync(true);
  };

  private readonly onTouchEnd = (e: TouchEvent): void => {
    if (this.lastTouch) this.swallow(e);
    const start = this.touchStart;
    if (e.touches.length === 0) {
      this.lastTouch = null;
      this.touchStart = null;
      this.loupe.hide();
    }
    if (e.type === 'touchend' && start && isTap(start.travel, e.timeStamp - start.at)) {
      this.hooks.onTap?.();
    }
  };

  /** Дотикові pointer-події по графіку — не далі (рух веде touch-обробник); миша й перо — як завжди. */
  private readonly onPointer = (e: PointerEvent): void => {
    if (e.pointerType === 'touch' && onChartSurface(e.target)) e.stopImmediatePropagation();
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

  /** Кільце, прев'ю фігури, перехрестя; fromFinger — крок пальця (лупа й «клац»; масштаб і поворот — без них). */
  private sync(fromFinger = false): void {
    const p = this.snapped();
    const snapPrice = this.target.cursorMove(p.x, p.y);
    const snapY = snapPrice === null ? null : this.series.priceToCoordinate(snapPrice);
    const shown: CursorPoint = { x: p.x, y: snapY ?? p.y };
    this.reticle.style.transform = `translate(${shown.x}px, ${shown.y}px)`;
    this.reticle.style.boxShadow = snapY === null ? RETICLE_GLOW : RETICLE_GLOW_SNAPPED;
    if (fromFinger && isNewSnapLevel(this.lastSnapPrice, snapPrice)) this.hooks.onSnap?.();
    this.lastSnapPrice = snapPrice;
    // Лупа — лише коли палець справді веде кільце (тремтіння тапу її не блимає)
    if (fromFinger && this.touchStart && this.touchStart.travel >= TAP_SLOP_PX) this.loupe.show(shown, this.bounds());
    const price = snapPrice ?? this.series.coordinateToPrice(p.y);
    const time = this.chart.timeScale().coordinateToTime(p.x);
    // Майбутнє (праворуч від останнього бару) — у LWC часу нема: перехрестя знімаємо, курсор-кільце лишається.
    if (price !== null && time !== null) this.chart.setCrosshairPosition(price, time, this.series);
    else this.chart.clearCrosshairPosition();
  }
}
