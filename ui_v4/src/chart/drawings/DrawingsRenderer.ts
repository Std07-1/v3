// src/chart/drawings/DrawingsRenderer.ts
import { v4 as uuidv4 } from 'uuid';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { MismatchDirection } from 'lightweight-charts';

import type { Drawing, ActiveTool, WsAction, T_MS, UiWarning } from '../../types';
import { CommandStack, type CommandAction } from './CommandStack';
import {
  HIT_TOLERANCE_PX,
  HANDLE_RADIUS_PX,
  HANDLE_RADIUS_HOVER_PX,
  distToPoint,
} from '../interaction/geometry';
import { getToolModule, TOOL_REGISTRY } from './tools';
import type { RenderContext } from './tools';

type HorzScaleItem = Time;

function timeToSec(time: HorzScaleItem): number {
  if (typeof time === 'number') return time;
  if (typeof time === 'string') return new Date(time).getTime() / 1000;
  return Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0, 0) / 1000;
}

/** Parse a CSS custom property як positive number (px-without-unit).
 *  ADR-0074 T2: tokens.css declarates `--drawing-hit-tolerance-px: 10`
 *  (raw number, no `px` suffix). parseFloat tolerates trailing whitespace +
 *  optional unit. Returns fallback якщо missing/invalid (degraded-but-loud
 *  не required — це pure perceptual UX value, not data correctness). */
function parsePxToken(s: CSSStyleDeclaration, name: string, fallback: number): number {
  const raw = s.getPropertyValue(name).trim();
  if (!raw) return fallback;
  const n = parseFloat(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function cloneDrawing(d: Drawing): Drawing {
  return {
    id: d.id,
    type: d.type,
    points: d.points.map((p) => ({ t_ms: p.t_ms, price: p.price })),
    meta: d.meta ? { ...d.meta } : undefined,
  };
}

type ScreenAabb = { minX: number; minY: number; maxX: number; maxY: number };

type HitState = { id: string; handleIdx: number | null };

type SnapConfig = {
  enabled: boolean;
  mode: 'ohlc' | 'hl' | 'close';
  radius_px: number;
};

export class DrawingsRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;

  private readonly interactionEl: HTMLElement;
  private readonly chartApi: IChartApi;
  private readonly seriesApi: ISeriesApi<'Candlestick'>;

  private readonly sendAction: (action: WsAction) => void;
  private readonly addUiWarning: (w: UiWarning) => void;

  public readonly commandStack: CommandStack;

  private drawings: Drawing[] = [];
  private activeTool: ActiveTool = null;
  private draft: Drawing | null = null;

  // v3 selection state
  private selectedId: string | null = null;
  private hovered: HitState | null = null;

  private dragState:
    | null
    | {
      id: string;
      handleIdx: number | null; // null -> body drag
      pointerId: number;
      startX: number;
      startY: number;
      startObj: Drawing; // snapshot для UPDATE/undo
    } = null;

  // hit-test/cache
  private aabbById = new Map<string, ScreenAabb>();

  // RAF render
  private dpr = 1;
  private rafId: number | null = null;

  // hover compute via RAF (latest-wins)
  private lastCursor: { x: number; y: number } | null = null;
  private hoverDirty = false;

  // snap (default OFF — controlled by toolbar magnet toggle, persisted in localStorage)
  private snapConfig: SnapConfig = { enabled: false, mode: 'ohlc', radius_px: 30 };
  private lastSnap: { x: number; y: number } | null = null; // visual snap indicator

  // warning throttle
  private warnLastTs = new Map<string, number>();
  private warningThrottleMs = 800;

  // ADR-0007: theme-aware кольори (кешовані, оновлюються через refreshThemeColors)
  private themeBaseColor = '#c8cdd6';
  private themeRectFill = 'rgba(200, 205, 214, 0.10)';
  // ADR-0066 PATCH 06b: accent color for draft/hovered/selected drawings.
  // Default #D4A017 (gold) replaces the prior blue accent; dynamic value
  // refreshed from --toolbar-active-color CSS var via refreshThemeColors().
  private themeAccentColor = '#D4A017';

  // ADR-0074 T2: hit-test geometry cached from CSS vars (--drawing-*).
  // Defaults match geometry.ts HIT_TOLERANCE_PX/HANDLE_RADIUS_PX constants
  // (fallback якщо tokens.css не loaded або CSS var missing).
  // FP10: НЕ read getComputedStyle у hit-test loop — refresh-ить per theme/init.
  private hitTolerancePx = HIT_TOLERANCE_PX;
  private handleRadiusPx = HANDLE_RADIUS_PX;
  private handleRadiusHoverPx = HANDLE_RADIUS_HOVER_PX;

  // listeners
  private ro: ResizeObserver;

  private onPointerMoveCapture!: (e: PointerEvent) => void;
  private onPointerDownCapture!: (e: PointerEvent) => void;
  private onPointerUpCapture!: (e: PointerEvent) => void;
  private onPointerCancelCapture!: (e: PointerEvent) => void;

  constructor(
    canvas: HTMLCanvasElement,
    interactionEl: HTMLElement,
    chartApi: IChartApi,
    seriesApi: ISeriesApi<'Candlestick'>,
    sendAction: (action: WsAction) => void,
    addUiWarning: (w: UiWarning) => void,
  ) {
    this.canvas = canvas;
    this.interactionEl = interactionEl;
    this.chartApi = chartApi;
    this.seriesApi = seriesApi;
    this.sendAction = sendAction;
    this.addUiWarning = addUiWarning;

    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('DrawingsRenderer: неможливо отримати 2D контекст');
    this.ctx = ctx;

    this.commandStack = new CommandStack(this.sendAction, this.applyLocally.bind(this));

    // DPR rail
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

    // Range trigger — renderSync (not rAF) to eliminate 1-frame lag during scroll/zoom
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(() => this.renderSync());

    // ADR-0008: Y-axis sync — відбувається через callback notifyPriceRangeChanged(),
    // а не через wheel/dblclick listeners (ті блокуються interaction.ts:stopImmediatePropagation).

    // IMPORTANT: canvas лишається pointer-events:none завжди (арбітраж через capture на interactionEl)
    this.canvas.style.pointerEvents = 'none';

    this.setupInteractionsCapture();

    // ADR-0074 T2: prime hit-test geometry cache + theme colors з CSS-vars
    // на init. Без цього виклику mobile користувачі без theme-switch отримують
    // desktop fallback geometry (10/5/8) замість platform-aware (16/7/10).
    // Safe навіть якщо canvas yet not styled — fallbacks WCAG-compliant.
    this.refreshThemeColors();

    // НЕ загружаємо тут — чекаємо на setStorageKey(sym, tf) від ChartPane,
    // щоб уникнути витоку drawings з глобального ключа 'v4_drawings' у per-TF ключі.
  }

  setAll(drawings: Drawing[]): void {
    // Server-sync: only overwrite if server sends actual drawings.
    // If empty (client-only mode), keep localStorage drawings.
    if (!drawings || drawings.length === 0) return;
    this.drawings = drawings;
    this.saveToStorage();
    this.scheduleRender();
  }

  confirm(drawing: Drawing): void {
    const idx = this.drawings.findIndex((d) => d.id === drawing.id);
    if (idx >= 0) this.drawings[idx] = drawing;
    else this.drawings.push(drawing);
    this.scheduleRender();
  }

  setTool(tool: ActiveTool): void {
    this.activeTool = tool;
    this.draft = null;

    // UX: курсор тільки коли tool активний або є hovered/drag
    this.updateCursor();
    this.scheduleRender();
  }

  cancelDraft(): void {
    this.draft = null;
    this.scheduleRender();
  }

  /** Магнітний режим: прив'язка інструментів до OHLC свічок */
  setMagnetEnabled(enabled: boolean): void {
    this.snapConfig.enabled = enabled;
  }

  /** ADR-0007 + ADR-0074 T2: оновити кольори теми + hit-test geometry з CSS
   *  custom properties. Canvas не читає CSS vars напряму — кешуємо одноразово
   *  (на init, на change theme, на media query change). Hit-test loop читає
   *  з numeric fields (FP10 zero getComputedStyle у hot path). */
  refreshThemeColors(): void {
    const s = getComputedStyle(this.canvas);
    this.themeBaseColor = s.getPropertyValue('--drawing-base-color').trim() || '#c8cdd6';
    this.themeRectFill = s.getPropertyValue('--drawing-rect-fill').trim() || 'rgba(200, 205, 214, 0.10)';
    // ADR-0066 PATCH 06b: read --toolbar-active-color (set by themes.ts applyThemeCssVars)
    this.themeAccentColor = s.getPropertyValue('--toolbar-active-color').trim() || '#D4A017';
    // ADR-0074 T2: drawing UX geometry tokens (platform-aware via @media pointer:coarse)
    this.hitTolerancePx = parsePxToken(s, '--drawing-hit-tolerance-px', HIT_TOLERANCE_PX);
    this.handleRadiusPx = parsePxToken(s, '--drawing-handle-radius-px', HANDLE_RADIUS_PX);
    this.handleRadiusHoverPx = parsePxToken(s, '--drawing-handle-radius-hover-px', HANDLE_RADIUS_HOVER_PX);
    this.scheduleRender();
  }

  /** ADR-0008: Y-axis змінився (викликається з interaction.ts після applyManualRange).
   *  Використовуємо scheduleRender (rAF), а не renderSync,
   *  щоб LWC встиг оновити internal layout і priceToCoordinate повертав актуальні Y. */
  notifyPriceRangeChanged(): void {
    this.scheduleRender();
  }

  // ---- warnings ----
  private warnOnce(key: string, code: UiWarning['code'], details: string): void {
    const now = Date.now();
    const last = this.warnLastTs.get(key) ?? 0;
    if (now - last < this.warningThrottleMs) return;
    this.warnLastTs.set(key, now);

    this.addUiWarning({
      code,
      kind: 'drawing',
      id: key,
      details,
    });
  }

  // ---- converters ----
  private toX(t_ms: number): number | null {
    return this.chartApi.timeScale().timeToCoordinate((t_ms / 1000) as import('lightweight-charts').Time);
  }

  private toY(price: number): number | null {
    return this.seriesApi.priceToCoordinate(price);
  }

  private fromX(x: number): T_MS | null {
    const t = this.chartApi.timeScale().coordinateToTime(x);
    if (t === null) return null;
    return (timeToSec(t) * 1000) as T_MS;
  }

  private fromY(y: number): number | null {
    const p = this.seriesApi.coordinateToPrice(y);
    if (p === null || Number.isNaN(p as number)) return null;
    return p as number;
  }

  // ---- snap ----
  private getSnappedPrice(x: number, y: number, rawPrice: number): number {
    if (!this.snapConfig.enabled) return rawPrice;

    const logical = this.chartApi.timeScale().coordinateToLogical(x);
    if (logical === null) return rawPrice;

    const idx = Math.floor(logical);
    const bar: any = this.seriesApi.dataByIndex(idx, MismatchDirection.NearestLeft);
    if (!bar || typeof bar.open !== 'number') return rawPrice;

    const candidates: number[] =
      this.snapConfig.mode === 'close'
        ? [bar.close]
        : this.snapConfig.mode === 'hl'
          ? [bar.high, bar.low]
          : [bar.high, bar.low, bar.open, bar.close];

    let bestPrice = rawPrice;
    let bestDist = Infinity;

    for (const p of candidates) {
      const py = this.seriesApi.priceToCoordinate(p);
      if (py === null) continue;
      const dist = Math.abs(y - py);
      if (dist < bestDist) {
        bestDist = dist;
        bestPrice = p;
      }
    }

    if (bestDist <= this.snapConfig.radius_px) {
      // Visual snap indicator: mark snapped point
      const snapY = this.seriesApi.priceToCoordinate(bestPrice);
      this.lastSnap = snapY !== null ? { x, y: snapY } : null;
      return bestPrice;
    }
    this.lastSnap = null;
    return rawPrice;
  }

  // ---- CommandStack локальне застосування ----
  private applyLocally(cmd: CommandAction, isUndo: boolean): void {
    if (cmd.type === 'ADD') {
      this.drawings = isUndo
        ? this.drawings.filter((d) => d.id !== cmd.drawing.id)
        : [...this.drawings, cmd.drawing];
    } else if (cmd.type === 'DELETE') {
      this.drawings = isUndo
        ? [...this.drawings, cmd.drawing]
        : this.drawings.filter((d) => d.id !== cmd.drawing.id);
    } else {
      // UPDATE
      const next = isUndo ? cmd.prev : cmd.next;
      const idx = this.drawings.findIndex((d) => d.id === next.id);
      if (idx >= 0) {
        const copy = this.drawings.slice();
        copy[idx] = next;
        this.drawings = copy;
      } else {
        this.drawings = [...this.drawings, next];
        this.warnOnce(`update_missing:${next.id}`, 'drawing_state_inconsistent', 'UPDATE: drawing не знайдено, додано як new');
      }
    }
    this.scheduleRender();
    this.saveToStorage();
  }

  // ---- localStorage persistence ----
  private storageKey = 'v4_drawings';

  /** Оновити ключ збереження (при зміні symbol/TF). Зберігає поточні → завантажує нові. */
  setStorageKey(symbol: string, tf: string): void {
    // Save current drawings to OLD key (if we have any)
    if (this.drawings.length > 0) this.saveToStorage();
    // Switch key
    this.storageKey = `v4_drawings_${symbol}_${tf}`;
    // Load drawings for new key (always resets this.drawings)
    this.loadFromStorage();
    // Migration: видалити legacy глобальний ключ, що спричиняв витік drawings між TF
    try { localStorage.removeItem('v4_drawings'); } catch { /* ok */ }
  }

  /** Очистити ВСІ збережені drawings з localStorage (всі символи/TF).
   *  Виклик: drawingsRenderer.purgeAllDrawings() або через консоль: window.__purgeDrawings?.() */
  purgeAllDrawings(): void {
    const keysToRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('v4_drawings')) keysToRemove.push(key);
    }
    keysToRemove.forEach((k) => localStorage.removeItem(k));
    this.drawings = [];
    this.scheduleRender();
    console.info('[DrawingsRenderer] purgeAllDrawings: видалено %d ключів', keysToRemove.length);
  }

  private saveToStorage(): void {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.drawings));
    } catch { /* quota exceeded or private mode — silent */ }
  }

  private loadFromStorage(): void {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) {
        this.drawings = [];
        this.scheduleRender();
        return;
      }
      const parsed = JSON.parse(raw);
      this.drawings = Array.isArray(parsed) ? parsed : [];
      this.scheduleRender();
    } catch {
      this.drawings = [];
      this.scheduleRender();
    }
  }

  // ---- v3 hit-testing ----
  private performHitTest(cursorX: number, cursorY: number): HitState | null {
    // 1) handles для selectedId
    if (this.selectedId) {
      const d = this.drawings.find((x) => x.id === this.selectedId);
      if (d) {
        for (let j = 0; j < d.points.length; j++) {
          const hx = this.toX(d.points[j].t_ms);
          const hy = this.toY(d.points[j].price);
          if (hx === null || hy === null) continue;

          if (distToPoint(cursorX, cursorY, hx, hy) <= this.hitTolerancePx) {
            return { id: d.id, handleIdx: j };
          }
        }
      }
    }

    // 2) body scan (z-index: останній зверху)
    const tol = this.hitTolerancePx;
    let best: HitState | null = null;
    let minDist = tol;

    for (let i = this.drawings.length - 1; i >= 0; i--) {
      const d = this.drawings[i];

      // AABB reject
      const aabb = this.aabbById.get(d.id);
      if (aabb) {
        if (
          cursorX < aabb.minX - tol ||
          cursorX > aabb.maxX + tol ||
          cursorY < aabb.minY - tol ||
          cursorY > aabb.maxY + tol
        ) {
          continue;
        }
      }

      // ADR-0074 T1: delegate hit-test до TOOL_REGISTRY. Tool-specific
      // geometry math живе у tools/*Tool.ts модулях.
      const tool = getToolModule(d.type);
      if (!tool) continue;
      const result = tool.hitTest(d, cursorX, cursorY, tol, this.toX, this.toY);
      if (!result.hit) continue;

      if (result.distance <= minDist) {
        minDist = result.distance;
        best = { id: d.id, handleIdx: null };
        if (result.distance === 0) break;
      }
    }

    return best;
  }

  // ---- interactions (capture on container) ----
  private setupInteractionsCapture(): void {
    this.onPointerMoveCapture = (e) => {
      const rect = this.interactionEl.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Drag в процесі -> блокуємо LWC
      if (this.dragState) {
        e.preventDefault();
        e.stopPropagation();
        this.handleDragMove(x, y);
        return;
      }

      // Tool drawing (draft) -> блокуємо LWC
      if (this.activeTool && this.activeTool !== 'eraser') {
        if (!this.draft) return;
        e.preventDefault();
        e.stopPropagation();
        this.updateDraft(x, y);
        return;
      }

      // Eraser hover (але не блокуємо LWC)
      // Selection hover (але не блокуємо LWC)
      this.lastCursor = { x, y };
      this.hoverDirty = true;
      this.scheduleRender();
    };

    this.onPointerDownCapture = (e) => {
      const rect = this.interactionEl.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Tool: click-click state machine (TradingView-style)
      if (this.activeTool && this.activeTool !== 'eraser') {
        e.preventDefault();
        e.stopPropagation();
        if (this.draft) {
          // 2nd click: commit draft
          this.finishDraft();
        } else {
          // 1st click: create draft (hline is instant, trend/rect start preview)
          this.handleToolPointerDown(e.pointerId, x, y);
        }
        return;
      }

      // Eraser: delete по hit
      if (this.activeTool === 'eraser') {
        const hit = this.performHitTest(x, y);
        if (hit) {
          e.preventDefault();
          e.stopPropagation();
          const d = this.drawings.find((q) => q.id === hit.id);
          if (d) this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(d) });
        }
        return;
      }

      // Selection/Drag: тільки якщо реально hit
      const hit = this.performHitTest(x, y);

      if (!hit) {
        // Клік в пустоту: зняти selection, але НЕ ламати LWC
        if (this.selectedId !== null) {
          this.selectedId = null;
          this.hovered = null;
          this.updateCursor();
          this.scheduleRender();
        }
        return;
      }

      // Якщо hit є -> беремо interaction, блокуємо LWC
      e.preventDefault();
      e.stopPropagation();

      this.selectedId = hit.id;
      this.hovered = hit;
      this.updateCursor();
      this.scheduleRender();

      const d = this.drawings.find((q) => q.id === hit.id);
      if (!d) return;

      this.interactionEl.setPointerCapture(e.pointerId);
      this.dragState = {
        id: hit.id,
        handleIdx: hit.handleIdx,
        pointerId: e.pointerId,
        startX: x,
        startY: y,
        startObj: cloneDrawing(d),
      };
    };

    this.onPointerUpCapture = (e) => {
      if (!this.dragState) {
        // click-click: draft lives until 2nd pointerdown; no commit on pointerup
        return;
      }

      e.preventDefault();
      e.stopPropagation();

      const { id, startObj } = this.dragState;
      this.dragState = null;
      this.updateCursor();

      // commit UPDATE (якщо реально змінилося)
      const current = this.drawings.find((d) => d.id === id);
      if (!current) return;

      const changed =
        current.points.length !== startObj.points.length ||
        current.points.some((p, i) => p.t_ms !== startObj.points[i].t_ms || p.price !== startObj.points[i].price);

      if (changed) {
        this.commandStack.push({ type: 'UPDATE', prev: startObj, next: cloneDrawing(current) });
      }

      this.scheduleRender();
    };

    this.onPointerCancelCapture = (e) => {
      if (this.dragState) {
        e.preventDefault();
        e.stopPropagation();
        this.dragState = null;
      }
      if (this.draft) this.draft = null;
      this.updateCursor();
      this.scheduleRender();
    };

    this.interactionEl.addEventListener('pointermove', this.onPointerMoveCapture, { capture: true });
    this.interactionEl.addEventListener('pointerdown', this.onPointerDownCapture, { capture: true });
    this.interactionEl.addEventListener('pointerup', this.onPointerUpCapture, { capture: true });
    this.interactionEl.addEventListener('pointercancel', this.onPointerCancelCapture, { capture: true });
  }

  // ---- tool create (v2) ----
  private handleToolPointerDown(_pointerId: number, x: number, y: number): void {
    const t_ms = this.fromX(x);
    const rawPrice = this.fromY(y);

    if (t_ms === null || rawPrice === null) {
      this.warnOnce('tool_map_down_null', 'drawing_coord_null', `tool pointerdown: cannot map x=${x},y=${y}`);
      return;
    }

    const price = this.getSnappedPrice(x, y, rawPrice);

    if (this.activeTool === 'hline') {
      const d: Drawing = { id: uuidv4(), type: 'hline', points: [{ t_ms, price }] };
      this.commandStack.push({ type: 'ADD', drawing: d });
      // Tool stays active for continuous drawing; Escape to exit
      return;
    }

    if (this.activeTool === 'trend' || this.activeTool === 'rect') {
      this.draft = {
        id: uuidv4(),
        type: this.activeTool,
        points: [{ t_ms, price }, { t_ms, price }],
      };
      this.scheduleRender();
    }
  }

  private updateDraft(x: number, y: number): void {
    if (!this.draft) return;

    let t_ms = this.fromX(x);
    let rawPrice = this.fromY(y);

    // ADR-0008: при null — fallback до останніх відомих координат (уникає "freeze" на краях)
    if (t_ms === null) t_ms = this.draft.points[1].t_ms;
    if (rawPrice === null) rawPrice = this.draft.points[1].price;
    if (t_ms === null || rawPrice === null) {
      this.warnOnce('tool_map_move_null', 'drawing_coord_null', `tool pointermove: cannot map x=${x},y=${y}`);
      return;
    }

    const price = this.getSnappedPrice(x, y, rawPrice);

    this.draft.points[1] = { t_ms, price };
    this.scheduleRender();
  }

  private finishDraft(): void {
    if (!this.draft) return;

    const a = this.draft.points[0];
    const b = this.draft.points[1];
    const isZero = a.t_ms === b.t_ms && a.price === b.price;

    if (!isZero) this.commandStack.push({ type: 'ADD', drawing: cloneDrawing(this.draft) });

    this.draft = null;
    // Tool stays active for continuous drawing (TradingView-style); Escape to exit
  }

  // ---- drag math (v3) ----
  private handleDragMove(x: number, y: number): void {
    if (!this.dragState) return;

    const dIdx = this.drawings.findIndex((d) => d.id === this.dragState!.id);
    if (dIdx < 0) return;

    const current = this.drawings[dIdx];
    const start = this.dragState.startObj;

    if (this.dragState.handleIdx !== null) {
      // Handle drag: snap ON
      const t_ms = this.fromX(x);
      const rawPrice = this.fromY(y);
      if (t_ms === null || rawPrice === null) {
        this.warnOnce('drag_handle_null', 'drawing_coord_null', `drag handle: cannot map x=${x},y=${y}`);
        return;
      }
      const price = this.getSnappedPrice(x, y, rawPrice);

      const next = cloneDrawing(current);
      next.points[this.dragState.handleIdx] = { t_ms, price };

      this.drawings = this.replaceById(next);
      this.scheduleRender();
      return;
    }

    // Body drag: snap OFF (щоб не тремтіло)
    const dx = x - this.dragState.startX;
    const dy = y - this.dragState.startY;

    const next = cloneDrawing(current);
    let anyNull = false;

    for (let i = 0; i < start.points.length; i++) {
      const oldX = this.toX(start.points[i].t_ms);
      const oldY = this.toY(start.points[i].price);

      if (oldX === null || oldY === null) {
        this.warnOnce('drag_body_old_null', 'drawing_coord_null', 'drag body: old point out of view');
        anyNull = true;
        break;
      }

      const new_t_ms = this.fromX(oldX + dx);
      const new_price = this.fromY(oldY + dy);

      // ADR-0008: при null — зберегти поточну позицію точки (не рухати)
      next.points[i] = {
        t_ms: new_t_ms ?? current.points[i].t_ms,
        price: new_price ?? current.points[i].price,
      };
    }

    if (anyNull) return;

    this.drawings = this.replaceById(next);
    this.scheduleRender();
  }

  private replaceById(next: Drawing): Drawing[] {
    const idx = this.drawings.findIndex((d) => d.id === next.id);
    if (idx < 0) return [...this.drawings, next];
    const copy = this.drawings.slice();
    copy[idx] = next;
    return copy;
  }

  // ---- render ----
  private scheduleRender(): void {
    if (this.rafId !== null) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = null;
      this.forceRender();
    });
  }

  /** Синхронний рендер — для chart events (scroll/zoom). Убирає 1-frame lag. */
  private renderSync(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.forceRender();
  }

  private updateCursor(): void {
    // Мінімально: показуємо що “можна взаємодіяти”
    if (this.dragState) {
      this.interactionEl.style.cursor = 'grabbing';
      return;
    }
    if (this.activeTool) {
      this.interactionEl.style.cursor = 'crosshair';
      return;
    }
    if (this.hovered) {
      this.interactionEl.style.cursor = this.hovered.handleIdx !== null ? 'pointer' : 'grab';
      return;
    }
    this.interactionEl.style.cursor = '';
  }

  private forceRender(): void {
    const cssW = this.canvas.width / this.dpr;
    const cssH = this.canvas.height / this.dpr;

    // 1) hover compute (latest-wins) — 1 раз/кадр
    if (this.hoverDirty && this.lastCursor) {
      this.hoverDirty = false;
      this.hovered = this.performHitTest(this.lastCursor.x, this.lastCursor.y);
      this.updateCursor();
    }

    this.ctx.clearRect(0, 0, cssW, cssH);
    this.aabbById.clear();

    const toX = (ms: T_MS): number | null => this.toX(ms);
    const toY = (p: number): number | null => this.toY(p);

    // ADR-0074 T1: delegate render до TOOL_REGISTRY. Tool-specific canvas
    // operations (stroke, fill, lineDash) живуть у tools/*Tool.ts модулях.
    // Renderer лише оркеструє: prepare context, виклик tool.render(), cache AABB.
    const drawItem = (d: Drawing, isDraft = false) => {
      const tool = getToolModule(d.type);
      if (!tool) return;

      const baseColor = d.meta?.color ?? this.themeBaseColor;
      const rc: RenderContext = {
        ctx: this.ctx,
        toX,
        toY,
        baseColor,
        accentColor: this.themeAccentColor,
        rectFill: this.themeRectFill,
        isDraft,
        isHovered: this.hovered?.id === d.id,
        isSelected: this.selectedId === d.id,
        cssW,
        cssH,
      };
      const aabb = tool.render(d, rc);
      if (aabb) this.aabbById.set(d.id, aabb);
    };

    for (const d of this.drawings) drawItem(d, false);
    if (this.draft) drawItem(this.draft, true);

    // 2) snap indicator (magnet visual feedback)
    if (this.lastSnap && this.draft) {
      this.ctx.save();
      this.ctx.strokeStyle = '#00e676';
      this.ctx.fillStyle = 'rgba(0, 230, 118, 0.25)';
      this.ctx.lineWidth = 1.5;
      this.ctx.setLineDash([]);
      this.ctx.beginPath();
      this.ctx.arc(this.lastSnap.x, this.lastSnap.y, 6, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.stroke();
      this.ctx.restore();
    }

    // 3) handles для selected
    if (this.selectedId) {
      const d = this.drawings.find((q) => q.id === this.selectedId);
      if (d) this.renderHandles(d);
    }
  }

  private renderHandles(d: Drawing): void {
    this.ctx.save();
    this.ctx.setLineDash([]);
    this.ctx.fillStyle = this.themeAccentColor;
    this.ctx.strokeStyle = '#ffffff';
    this.ctx.lineWidth = 1;

    for (let i = 0; i < d.points.length; i++) {
      const x = this.toX(d.points[i].t_ms);
      const y = this.toY(d.points[i].price);
      if (x === null || y === null) continue;

      const isHoverHandle = this.hovered?.id === d.id && this.hovered.handleIdx === i;
      const r = isHoverHandle ? this.handleRadiusHoverPx : this.handleRadiusPx;

      this.ctx.beginPath();
      this.ctx.arc(x, y, r, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.stroke();
    }

    this.ctx.restore();
  }

  destroy(): void {
    this.ro.disconnect();
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);

    this.interactionEl.removeEventListener('pointermove', this.onPointerMoveCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerdown', this.onPointerDownCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerup', this.onPointerUpCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointercancel', this.onPointerCancelCapture, { capture: true } as any);
  }
}