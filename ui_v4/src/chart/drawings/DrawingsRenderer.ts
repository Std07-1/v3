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
  distToHLine,
  distToPoint,
  distToSegment,
  distToRectEdge,
} from '../interaction/geometry';

type HorzScaleItem = Time;

function timeToSec(time: HorzScaleItem): number {
  if (typeof time === 'number') return time;
  return Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0, 0) / 1000;
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

  // snap
  private snapConfig: SnapConfig = { enabled: true, mode: 'ohlc', radius_px: 12 };

  // warning throttle
  private warnLastTs = new Map<string, number>();
  private warningThrottleMs = 800;

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
      this.dpr = window.devicePixelRatio || 1;

      this.canvas.width = Math.floor(width * this.dpr);
      this.canvas.height = Math.floor(height * this.dpr);
      this.canvas.style.width = `${Math.floor(width)}px`;
      this.canvas.style.height = `${Math.floor(height)}px`;

      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      this.scheduleRender();
    });

    if (this.canvas.parentElement) this.ro.observe(this.canvas.parentElement);

    // Range trigger
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(() => this.scheduleRender());

    // IMPORTANT: canvas лишається pointer-events:none завжди (арбітраж через capture на interactionEl)
    this.canvas.style.pointerEvents = 'none';

    this.setupInteractionsCapture();
  }

  setAll(drawings: Drawing[]): void {
    this.drawings = drawings ?? [];
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
    return this.chartApi.timeScale().timeToCoordinate(t_ms / 1000);
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

    return bestDist <= this.snapConfig.radius_px ? bestPrice : rawPrice;
  }

  // ---- CommandStack локальне застосування ----
  private applyLocally(cmd: CommandAction, isUndo: boolean): void {
    if (cmd.type === 'ADD') {
      this.drawings = isUndo
        ? this.drawings.filter((d) => d.id !== cmd.drawing.id)
        : [...this.drawings, cmd.drawing];
      this.scheduleRender();
      return;
    }
    if (cmd.type === 'DELETE') {
      this.drawings = isUndo
        ? [...this.drawings, cmd.drawing]
        : this.drawings.filter((d) => d.id !== cmd.drawing.id);
      this.scheduleRender();
      return;
    }

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
    this.scheduleRender();
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

          if (distToPoint(cursorX, cursorY, hx, hy) <= HIT_TOLERANCE_PX) {
            return { id: d.id, handleIdx: j };
          }
        }
      }
    }

    // 2) body scan (z-index: останній зверху)
    let best: HitState | null = null;
    let minDist = HIT_TOLERANCE_PX;

    for (let i = this.drawings.length - 1; i >= 0; i--) {
      const d = this.drawings[i];

      // AABB reject
      const aabb = this.aabbById.get(d.id);
      if (aabb) {
        if (
          cursorX < aabb.minX - HIT_TOLERANCE_PX ||
          cursorX > aabb.maxX + HIT_TOLERANCE_PX ||
          cursorY < aabb.minY - HIT_TOLERANCE_PX ||
          cursorY > aabb.maxY + HIT_TOLERANCE_PX
        ) {
          continue;
        }
      }

      let dist = Infinity;

      if (d.type === 'hline') {
        const y = this.toY(d.points[0].price);
        if (y !== null) dist = distToHLine(cursorY, y);
      } else if (d.type === 'trend') {
        const x1 = this.toX(d.points[0].t_ms), y1 = this.toY(d.points[0].price);
        const x2 = this.toX(d.points[1].t_ms), y2 = this.toY(d.points[1].price);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
          dist = distToSegment(cursorX, cursorY, x1, y1, x2, y2);
        }
      } else if (d.type === 'rect') {
        const x1 = this.toX(d.points[0].t_ms), y1 = this.toY(d.points[0].price);
        const x2 = this.toX(d.points[1].t_ms), y2 = this.toY(d.points[1].price);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
          const minX = Math.min(x1, x2);
          const maxX = Math.max(x1, x2);
          const minY = Math.min(y1, y2);
          const maxY = Math.max(y1, y2);
          dist = distToRectEdge(cursorX, cursorY, minX, minY, maxX, maxY, HIT_TOLERANCE_PX);
        }
      }

      if (dist <= minDist) {
        minDist = dist;
        best = { id: d.id, handleIdx: null };
        if (dist === 0) break;
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

      // Tool: створення
      if (this.activeTool && this.activeTool !== 'eraser') {
        e.preventDefault();
        e.stopPropagation();
        this.interactionEl.setPointerCapture(e.pointerId);
        this.handleToolPointerDown(e.pointerId, x, y);
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
        // commit draft при tool?
        if (this.draft && this.activeTool && this.activeTool !== 'eraser') {
          e.preventDefault();
          e.stopPropagation();
          this.finishDraft();
        }
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
  private handleToolPointerDown(pointerId: number, x: number, y: number): void {
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
      this.setTool(null);
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

    const t_ms = this.fromX(x);
    const rawPrice = this.fromY(y);

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
    this.setTool(null);
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

    for (let i = 0; i < start.points.length; i++) {
      const oldX = this.toX(start.points[i].t_ms);
      const oldY = this.toY(start.points[i].price);

      if (oldX === null || oldY === null) {
        this.warnOnce('drag_body_old_null', 'drawing_coord_null', 'drag body: old point out of view');
        return;
      }

      const new_t_ms = this.fromX(oldX + dx);
      const new_price = this.fromY(oldY + dy);

      if (new_t_ms === null || new_price === null) {
        this.warnOnce('drag_body_new_null', 'drawing_coord_null', 'drag body: new point cannot map');
        return;
      }

      next.points[i] = { t_ms: new_t_ms, price: new_price };
    }

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

    const drawItem = (d: Drawing, isDraft = false) => {
      const baseColor = d.meta?.color ?? '#c8cdd6';
      const hovered = this.hovered?.id === d.id;
      const selected = this.selectedId === d.id;

      this.ctx.strokeStyle = isDraft ? '#3d9aff' : (hovered || selected ? '#3d9aff' : baseColor);
      this.ctx.lineWidth = d.meta?.lineWidth ?? 1;
      this.ctx.setLineDash(isDraft ? [4, 4] : []);

      // AABB helpers
      const aabb = (points: { x: number; y: number }[]) => {
        const xs = points.map((p) => p.x);
        const ys = points.map((p) => p.y);
        return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
      };

      if (d.type === 'hline') {
        const y = toY(d.points[0].price);
        if (y === null) return;

        this.aabbById.set(d.id, { minX: 0, maxX: cssW, minY: y, maxY: y });

        this.ctx.beginPath();
        this.ctx.moveTo(0, y);
        this.ctx.lineTo(cssW, y);
        this.ctx.stroke();
        return;
      }

      if (d.type === 'trend' && d.points.length === 2) {
        const x1 = toX(d.points[0].t_ms), y1 = toY(d.points[0].price);
        const x2 = toX(d.points[1].t_ms), y2 = toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return;

        this.aabbById.set(d.id, aabb([{ x: x1, y: y1 }, { x: x2, y: y2 }]));

        this.ctx.beginPath();
        this.ctx.moveTo(x1, y1);
        this.ctx.lineTo(x2, y2);
        this.ctx.stroke();
        return;
      }

      if (d.type === 'rect' && d.points.length === 2) {
        const x1 = toX(d.points[0].t_ms), y1 = toY(d.points[0].price);
        const x2 = toX(d.points[1].t_ms), y2 = toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return;

        const minX = Math.min(x1, x2);
        const minY = Math.min(y1, y2);
        const w = Math.abs(x2 - x1);
        const h = Math.abs(y2 - y1);

        this.aabbById.set(d.id, { minX, minY, maxX: minX + w, maxY: minY + h });

        this.ctx.fillStyle = isDraft ? 'rgba(61, 154, 255, 0.10)' : 'rgba(200, 205, 214, 0.10)';
        this.ctx.fillRect(minX, minY, w, h);
        this.ctx.strokeRect(minX, minY, w, h);
        return;
      }
    };

    for (const d of this.drawings) drawItem(d, false);
    if (this.draft) drawItem(this.draft, true);

    // 2) handles для selected
    if (this.selectedId) {
      const d = this.drawings.find((q) => q.id === this.selectedId);
      if (d) this.renderHandles(d);
    }
  }

  private renderHandles(d: Drawing): void {
    this.ctx.save();
    this.ctx.setLineDash([]);
    this.ctx.fillStyle = '#3d9aff';
    this.ctx.strokeStyle = '#ffffff';
    this.ctx.lineWidth = 1;

    for (let i = 0; i < d.points.length; i++) {
      const x = this.toX(d.points[i].t_ms);
      const y = this.toY(d.points[i].price);
      if (x === null || y === null) continue;

      const isHoverHandle = this.hovered?.id === d.id && this.hovered.handleIdx === i;
      const r = isHoverHandle ? HANDLE_RADIUS_HOVER_PX : HANDLE_RADIUS_PX;

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