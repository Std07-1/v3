// src/chart/drawings/touchLoupe.ts
// ADR-0112 D6: лупа над кільцем курсора, поки палець веде його, — збільшене вікно графіка навколо точки (свічки,
// рівні, фігури, перехрестя), щоб влучити в хай чи лоу. Не перемальовує нічого: копіює ділянку наявних полотен
// графіка (drawImage) у своє полотно поверх панелі, на кадрі після кадру LWC і шарів.

import type { CursorBounds, CursorPoint } from './touchDrawCursor';

/** Діаметр лупи, CSS px. */
export const LOUPE_SIZE_PX = 104;
/** Збільшення: свічка M15 на телефоні (~6 px) стає ~12 px — видно тіло й тінь. */
export const LOUPE_ZOOM = 2;
/** Відступ лупи від точки: кільце (22 px) і повітря між ними — лупа не закриває саму точку. */
export const LOUPE_GAP_PX = 28;

export interface LoupeBox {
  readonly left: number;
  readonly top: number;
}

/** Де стоїть лупа для точки p у панелі b: над точкою; згори нема місця (вище minTop) — під нею; по горизонталі —
 *  по центру точки, у межах панелі. */
export function loupePlacement(p: CursorPoint, b: CursorBounds, minTop = 0): LoupeBox {
  const above = p.y - LOUPE_GAP_PX - LOUPE_SIZE_PX;
  const below = Math.min(p.y + LOUPE_GAP_PX, Math.max(minTop, b.height - LOUPE_SIZE_PX));
  return {
    left: Math.min(Math.max(p.x - LOUPE_SIZE_PX / 2, 0), Math.max(0, b.width - LOUPE_SIZE_PX)),
    top: above >= minTop ? above : below,
  };
}

export interface SourceRect {
  readonly sx: number;
  readonly sy: number;
  readonly sw: number;
  readonly sh: number;
}

/** Ділянка полотна у ЙОГО пікселях під квадратом side×side CSS px з центром у (cx, cy) екрана; null — полотно без
 *  розміру або квадрат повз нього (нічого копіювати). */
export function sourceRect(
  rect: { readonly left: number; readonly top: number; readonly width: number; readonly height: number },
  canvasPx: { readonly width: number; readonly height: number },
  cx: number,
  cy: number,
  side: number,
): SourceRect | null {
  if (rect.width <= 0 || rect.height <= 0 || canvasPx.width <= 0 || canvasPx.height <= 0) return null;
  const left = cx - side / 2;
  const top = cy - side / 2;
  if (left >= rect.left + rect.width || top >= rect.top + rect.height || left + side <= rect.left || top + side <= rect.top) {
    return null;
  }
  const kx = canvasPx.width / rect.width;
  const ky = canvasPx.height / rect.height;
  return { sx: (left - rect.left) * kx, sy: (top - rect.top) * ky, sw: side * kx, sh: side * ky };
}

/** Рамка лупи: вільний рух — ледь помітна; прилипло до рівня — золото теми (show). */
const LOUPE_BORDER = 'rgba(255,255,255,0.14)';
const LOUPE_STYLE = [
  'position:absolute',
  'left:0',
  'top:0',
  `width:${LOUPE_SIZE_PX}px`,
  `height:${LOUPE_SIZE_PX}px`,
  'border-radius:50%',
  `border:1px solid ${LOUPE_BORDER}`,
  'box-shadow:0 4px 20px rgba(0,0,0,0.45), 0 0 0 1px rgba(0,0,0,0.35)',
  'pointer-events:none',
  'display:none',
  'z-index:24', // над шарами графіка й кільцем (≤ 21), під індикатором довантаження (25), панеллю SMC і меню
].join(';');

/** Шар полотна (`.layer` у ChartPane або саме полотно): за ним — порядок накладання і CSS-фільтр яскравості. */
function layerOf(canvas: HTMLCanvasElement): Element {
  return canvas.closest('.layer') ?? canvas;
}

/** Верхнє полотно LWC (inline z-index 2 — там перехрестя). У лупу його не беремо, перехрестя малюємо самі через
 *  центр: LWC перемальовує його на кадр пізніше за лупу, і в ній воно відставало б на крок пальця (стенд 13.10). */
function isLwcCrosshairCanvas(canvas: HTMLCanvasElement): boolean {
  return canvas.style.zIndex === '2' && canvas.closest('.lwc-layer') !== null;
}

const TRANSPARENT = new Set(['transparent', 'rgba(0, 0, 0, 0)']);
/** Тло під графіком: тло LWC прозоре (під ним — тло застосунку), тож беремо перше непрозоре від контейнера вгору. */
function backdropOf(el: Element): string {
  for (let node: Element | null = el; node; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    if (color && !TRANSPARENT.has(color)) return color;
  }
  return '#131722';
}

export class TouchLoupe {
  private readonly el: HTMLCanvasElement;
  /** Полотна графіка в порядку накладання, з їхнім фільтром — збираються на початку жесту (не на кожен кадр). */
  private sources: Array<{ canvas: HTMLCanvasElement; filter: string }> = [];
  /** Золото теми (--accent, як у кільця) і тло під графіком — читаються на початку жесту (тема могла змінитись). */
  private accent = '#d4a017';
  private background = '#131722';
  private frame = 0;
  private visible = false;
  private warned = false;

  constructor(
    private readonly container: HTMLElement,
    /** Колір лінії перехрестя LWC (тема) — ним малюємо перехрестя в лупі. */
    private readonly crosshairColor: string,
    /** Верхня межа лупи в панелі (під рядками HUD), CSS px. */
    private readonly minTop = 0,
  ) {
    this.el = document.createElement('canvas');
    this.el.className = 'touch-draw-loupe';
    this.el.setAttribute('style', LOUPE_STYLE);
    container.appendChild(this.el);
  }

  /** Показати / пересунути лупу для точки p (CSS px панелі b); малює на наступному кадрі. level — підпис рівня, до
   *  якого прилип магніт («H 4175.79», D7): тоді рамка лупи золота, а підпис — внизу лупи. */
  show(p: CursorPoint, b: CursorBounds, level: string | null = null): void {
    if (!this.visible) {
      this.sources = this.collectSources();
      this.accent = getComputedStyle(this.container).getPropertyValue('--accent').trim() || '#d4a017';
      this.background = backdropOf(this.container);
      this.el.style.display = 'block';
      this.visible = true;
    }
    const box = loupePlacement(p, b, this.minTop);
    this.el.style.transform = `translate(${box.left}px, ${box.top}px)`;
    this.el.style.borderColor = level === null ? LOUPE_BORDER : this.accent;
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => this.paint(p, level));
  }

  hide(): void {
    cancelAnimationFrame(this.frame);
    this.el.style.display = 'none';
    this.visible = false;
    this.sources = [];
  }

  destroy(): void {
    this.hide();
    this.el.remove();
  }

  private collectSources(): Array<{ canvas: HTMLCanvasElement; filter: string }> {
    const zOf = (el: Element): number => {
      const z = Number.parseInt(getComputedStyle(el).zIndex, 10);
      return Number.isFinite(z) ? z : 0;
    };
    return [...this.container.querySelectorAll('canvas')]
      .filter((c) => c !== this.el && !isLwcCrosshairCanvas(c))
      .map((canvas, order) => {
        const layer = layerOf(canvas);
        const filter = getComputedStyle(layer).filter;
        return { canvas, order, z: zOf(layer), filter: filter && filter !== 'none' ? filter : 'none' };
      })
      .sort((a, b) => a.z - b.z || a.order - b.order)
      .map(({ canvas, filter }) => ({ canvas, filter }));
  }

  private paint(p: CursorPoint, level: string | null): void {
    const dpr = window.devicePixelRatio || 1;
    const px = Math.round(LOUPE_SIZE_PX * dpr);
    if (this.el.width !== px || this.el.height !== px) {
      this.el.width = px;
      this.el.height = px;
    }
    const ctx = this.el.getContext('2d');
    if (!ctx) {
      if (!this.warned) console.warn('[touchLoupe] TOUCH_LOUPE_NO_CONTEXT — лупа без полотна, малювання без неї');
      this.warned = true;
      return;
    }
    const host = this.container.getBoundingClientRect();
    const cx = host.left + p.x;
    const cy = host.top + p.y;
    const side = LOUPE_SIZE_PX / LOUPE_ZOOM;
    // Спершу стерти попередній кадр: полотна графіка прозорі поза свічками — без очищення кадри наклались би «кашею»
    // (стенд 13.10)
    ctx.filter = 'none';
    ctx.clearRect(0, 0, px, px);
    ctx.fillStyle = this.background;
    ctx.fillRect(0, 0, px, px);
    for (const { canvas, filter } of this.sources) {
      const src = sourceRect(canvas.getBoundingClientRect(), canvas, cx, cy, side);
      if (!src) continue;
      ctx.filter = filter;
      ctx.drawImage(canvas, src.sx, src.sy, src.sw, src.sh, 0, 0, px, px);
    }
    ctx.filter = 'none';
    const c = px / 2;
    // Перехрестя через центр — пунктиром, як у LWC (його верхнє полотно в лупу не береться, див. вище)
    ctx.save();
    ctx.strokeStyle = this.crosshairColor;
    ctx.lineWidth = dpr;
    ctx.setLineDash([3 * dpr, 3 * dpr]);
    ctx.beginPath();
    ctx.moveTo(0, c);
    ctx.lineTo(px, c);
    ctx.moveTo(c, 0);
    ctx.lineTo(c, px);
    ctx.stroke();
    ctx.restore();
    // Центр — те саме кільце з крапкою, що на графіку (кільце — DOM, у полотнах його нема)
    ctx.strokeStyle = this.accent;
    ctx.fillStyle = this.accent;
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    ctx.arc(c, c, 7 * dpr, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(c, c, 1.6 * dpr, 0, Math.PI * 2);
    ctx.fill();
    if (level !== null) this.paintLevel(ctx, level, px, dpr);
  }

  /** Підпис рівня внизу лупи — темна капсула з золотим текстом (рівень, до якого прилип магніт). */
  private paintLevel(ctx: CanvasRenderingContext2D, level: string, px: number, dpr: number): void {
    ctx.save();
    ctx.font = `600 ${10 * dpr}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const w = ctx.measureText(level).width + 12 * dpr;
    const h = 16 * dpr;
    const x = (px - w) / 2;
    const y = px - h - 9 * dpr;
    ctx.fillStyle = 'rgba(10, 12, 18, 0.82)';
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, h / 2);
    ctx.fill();
    ctx.fillStyle = this.accent;
    ctx.fillText(level, px / 2, y + h / 2 + 0.5 * dpr);
    ctx.restore();
  }
}
