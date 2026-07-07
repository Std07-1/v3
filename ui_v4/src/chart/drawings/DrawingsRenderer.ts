// src/chart/drawings/DrawingsRenderer.ts
import { v4 as uuidv4 } from 'uuid';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { MismatchDirection } from 'lightweight-charts';

import type { Drawing, ActiveTool, WsAction, T_MS, UiWarning, DrawingContextRequest, DrawingType, DrawingColorRole } from '../../types';
import { CommandStack, type CommandAction } from './CommandStack';
import { buildRoleColorMap } from './colorRoles';
import { timeToFractionalIndex, fractionalIndexToTime } from './timeMap';
import {
  HIT_TOLERANCE_PX,
  HANDLE_RADIUS_PX,
  distToPoint,
} from '../interaction/geometry';
import { getToolModule } from './tools';
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

// ADR-0080 (surface-2): частковий meta-патч для preview/commit (колір/товщина/стиль).
type DrawingMetaPatch = Partial<NonNullable<Drawing['meta']>>;

type SnapConfig = {
  enabled: boolean;
  mode: 'ohlc' | 'hl' | 'close';
};

// Концепт «делікатний об'єкт»: наведення проявляє редагування + видалення на
// самому об'єкті. × прив'язане до ЦЕНТРУ лінії; радіус = клікабельна+реакційна зона.
// Радіуси трохи більші за візуал → реагує ~1мм РАНІШЕ (не «майже поклав курсор»).
const DELETE_BTN_RADIUS_PX = 13;
const HANDLE_REACT_MARGIN_PX = 4;

// Приціл на кінцях: тонкий crosshair з ВІДКРИТИМ центром (видно точку на свічці),
// напівпрозорий, з темним halo для читабельності на світлих свічках. Hotspot = центр.
const CURSOR_RETICLE =
  "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'>" +
  "<g fill='none' stroke-linecap='round'>" +
  "<path d='M12 4v5M12 15v5M4 12h5M15 12h5' stroke='black' stroke-opacity='.45' stroke-width='3'/>" +
  "<path d='M12 4v5M12 15v5M4 12h5M15 12h5' stroke='white' stroke-opacity='.92' stroke-width='1.3'/>" +
  "</g></svg>\") 12 12, crosshair";

export class DrawingsRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;

  private readonly interactionEl: HTMLElement;
  private readonly chartApi: IChartApi;
  private readonly seriesApi: ISeriesApi<'Candlestick'>;

  private readonly sendAction: (action: WsAction) => void;
  private readonly addUiWarning: (w: UiWarning) => void;

  public readonly commandStack: CommandStack;

  /** ADR-0078: right-click на фігурі → UI-шар показує міні-меню (Видалити / Колір).
   *  null → меню вимкнено, renderer лишається повністю робочим. Встановлюється
   *  з ChartPane після конструювання (public seam, без constructor-churn). */
  public onContextMenu: ((req: DrawingContextRequest | null) => void) | null = null;

  private drawings: Drawing[] = [];
  private activeTool: ActiveTool = null;
  private draft: Drawing | null = null;

  // v3 selection state
  private selectedId: string | null = null;
  private hovered: HitState | null = null;

  // hover-афорданс: делікатне × по центру активної фігури (позиція + id для hit-test)
  private deleteBtn: { id: string; x: number; y: number } | null = null;

  // ADR-0080 (surface-2, 2b): live-preview кольору у style-flyout. Тримає ОРИГІНАЛ
  // meta фігури, поки flyout наводить ролі — щоб вихід без кліку відкотив точно.
  private previewOrig: { id: string; meta: Drawing['meta'] } | null = null;

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

  // ADR-0082 D6: кеш часів барів (сек) поточного TF для fractional time-мапінгу.
  // Оновлюється дешевим guard-ом (length+lastTime) у forceRender — O(n) rebuild
  // лише коли дані реально змінились, НЕ щокадру (FP10-дух: без churn у hot path).
  private barTimesSec: number[] = [];
  private barTimesLast = NaN;

  // RAF render
  private dpr = 1;
  private rafId: number | null = null;

  // hover compute via RAF (latest-wins)
  private lastCursor: { x: number; y: number } | null = null;
  private hoverDirty = false;

  // snap (default OFF — controlled by toolbar magnet toggle, persisted in localStorage)
  private snapConfig: SnapConfig = { enabled: false, mode: 'ohlc' };
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

  // ADR-0080 (surface-2): роль→hex мапа (theme-aware), кешована з CSS-vars у
  // refreshThemeColors (canvas не читає var() напряму). Резолв colorRole фігури.
  private roleColors: Record<DrawingColorRole, string> = buildRoleColorMap(() => '');

  // ADR-0080 (surface-2): дефолт-стиль per tool для НОВИХ фігур. Ставиться з
  // App через setToolDefaults (SSOT = localStorage v4_drawing_defaults). Впливає
  // лише на створення — існуючі фігури не чіпає.
  private toolDefaults: Partial<Record<DrawingType, DrawingMetaPatch>> = {};

  // ADR-0074 T2: hit-test geometry cached from CSS vars (--drawing-*).
  // Defaults match geometry.ts HIT_TOLERANCE_PX/HANDLE_RADIUS_PX constants
  // (fallback якщо tokens.css не loaded або CSS var missing).
  // FP10: НЕ read getComputedStyle у hit-test loop — refresh-ить per theme/init.
  private hitTolerancePx = HIT_TOLERANCE_PX;
  private handleRadiusPx = HANDLE_RADIUS_PX;

  // listeners
  private ro: ResizeObserver;
  private onStorageEvent!: (e: StorageEvent) => void;

  private onPointerMoveCapture!: (e: PointerEvent) => void;
  private onPointerDownCapture!: (e: PointerEvent) => void;
  private onPointerUpCapture!: (e: PointerEvent) => void;
  private onPointerCancelCapture!: (e: PointerEvent) => void;
  private onContextMenuCapture!: (e: MouseEvent) => void;

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

    // ADR-0082 D7: cross-tab конвергенція. Дві вкладки ділять per-symbol ключ,
    // але тримають НЕЗАЛЕЖНІ in-memory копії — save зі «застарілої» вкладки
    // мовчки перетирав фігури іншої (last-writer-wins, спіймано live: трендова
    // зникла без жодного вводу). Storage-event (шлеться лише ІНШИМ вкладкам)
    // → приймаємо зовнішній стан негайно → вкладки конвергують, вікно
    // розбіжності ~мс замість «до наступного save».
    this.onStorageEvent = (e) => {
      if (e.key !== this.storageKey || e.storageArea !== localStorage) return;
      this.loadFromStorage();
    };
    window.addEventListener('storage', this.onStorageEvent);

    // НЕ загружаємо тут — чекаємо на setStorageKey(symbol) від ChartPane
    // (ADR-0082: per-symbol стор, спільний усіма TF).
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

  /** ADR-0078: видалити фігуру за id (undoable DELETE, та сама команда, що й гумка). */
  deleteById(id: string): void {
    const d = this.drawings.find((x) => x.id === id);
    if (!d) return;
    this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(d) });
  }

  /** ADR-0080 (2b/3/4): live-preview meta-патчу на фігурі БЕЗ commit (hover у
   *  flyout — колір/товщина/стиль). patch=null → відкотити до оригіналу. Не чіпає
   *  commandStack/localStorage. Оригінал meta зберігається при першому дотику. */
  previewMeta(id: string, patch: DrawingMetaPatch | null): void {
    const d = this.drawings.find((x) => x.id === id);
    if (!d) return;
    if (!this.previewOrig || this.previewOrig.id !== id) {
      this.previewOrig = { id, meta: d.meta ? { ...d.meta } : undefined };
    }
    if (patch === null) {
      d.meta = this.previewOrig.meta ? { ...this.previewOrig.meta } : undefined;
      this.previewOrig = null;
    } else {
      d.meta = this.applyMetaPatch(d.meta, patch);
    }
    this.scheduleRender();
  }

  /** ADR-0080 (2b/3/4): закріпити meta-патч на фігурі (undoable UPDATE). Спершу
   *  знімає активний preview (щоб commit ішов від ОРИГІНАЛУ, не від прев'ю-стану),
   *  потім пушить UPDATE. */
  updateMetaById(id: string, patch: DrawingMetaPatch): void {
    if (this.previewOrig && this.previewOrig.id === id) {
      const d0 = this.drawings.find((x) => x.id === id);
      if (d0) d0.meta = this.previewOrig.meta ? { ...this.previewOrig.meta } : undefined;
      this.previewOrig = null;
    }
    const d = this.drawings.find((x) => x.id === id);
    if (!d) return;
    const prev = cloneDrawing(d);
    const next = cloneDrawing(d);
    next.meta = this.applyMetaPatch(next.meta, patch);
    this.commandStack.push({ type: 'UPDATE', prev, next });
  }

  /** Накласти meta-патч поверх поточного meta. colorRole перекриває legacy hex
   *  (роль стає SSOT кольору). Спільна логіка preview + commit. */
  private applyMetaPatch(base: Drawing['meta'], patch: DrawingMetaPatch): NonNullable<Drawing['meta']> {
    const meta = { ...(base ?? {}), ...patch };
    if (patch.colorRole !== undefined) delete meta.color;
    return meta;
  }

  /** Магнітний режим: прив'язка інструментів до OHLC свічок */
  setMagnetEnabled(enabled: boolean): void {
    this.snapConfig.enabled = enabled;
  }

  /** ADR-0080 (surface-2): дефолт-стиль per tool для нових фігур (з App state,
   *  SSOT localStorage). Впливає лише на створення — існуючі не перефарбовує. */
  setToolDefaults(defaults: Partial<Record<DrawingType, DrawingMetaPatch>>): void {
    this.toolDefaults = defaults;
  }

  /** Резолв кольору фігури: роль (theme-aware token) → legacy hex → база теми. */
  private resolveColor(meta: Drawing['meta']): string {
    if (meta?.colorRole) return this.roleColors[meta.colorRole] ?? this.themeBaseColor;
    if (meta?.color) return meta.color;
    return this.themeBaseColor;
  }

  /** Meta для НОВОЇ фігури з дефолту інструмента. Дефолтні значення (neutral колір
   *  = база теми, 1px товщина) не пишемо → фігура лишається без відповідного поля
   *  (theme-aware база, legacy-сумісно). */
  private defaultMeta(type: DrawingType): Drawing['meta'] | undefined {
    const d = this.toolDefaults[type];
    if (!d) return undefined;
    const meta: DrawingMetaPatch = {};
    if (d.colorRole && d.colorRole !== 'neutral') meta.colorRole = d.colorRole;
    if (d.lineWidth && d.lineWidth !== 1) meta.lineWidth = d.lineWidth;
    if (d.lineStyle && d.lineStyle !== 'solid') meta.lineStyle = d.lineStyle;
    return Object.keys(meta).length > 0 ? meta : undefined;
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
    // ADR-0080: роль→hex мапа з тих самих токенів (SSOT colorRoles.ts).
    this.roleColors = buildRoleColorMap((v) => s.getPropertyValue(v));
    // ADR-0074 T2: drawing UX geometry tokens (platform-aware via @media pointer:coarse)
    this.hitTolerancePx = parsePxToken(s, '--drawing-hit-tolerance-px', HIT_TOLERANCE_PX);
    this.handleRadiusPx = parsePxToken(s, '--drawing-handle-radius-px', HANDLE_RADIUS_PX);
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
  // ADR-0074 T1 bugfix: arrow class fields замість prototype methods —
  // arrow форма auto-binds `this`, тому ref-passing `this.toX` до ToolModule
  // не втрачає контекст. Регулярні методи треба було б wrapper-ити у
  // arrow closures на кожен виклик (alloc per hit-test).

  /** ADR-0082 D6: оновити кеш часів барів, якщо серія змінилась (guard:
   *  length + lastTime — O(1); rebuild O(n) лише на реальну зміну даних). */
  private refreshBarTimes(): void {
    const bars = this.seriesApi.data();
    const n = bars.length;
    const lastT = n > 0 ? timeToSec(bars[n - 1].time as HorzScaleItem) : NaN;
    if (n === this.barTimesSec.length && (lastT === this.barTimesLast || (Number.isNaN(lastT) && Number.isNaN(this.barTimesLast)))) return;
    this.barTimesSec = bars.map((b) => timeToSec(b.time as HorzScaleItem));
    this.barTimesLast = lastT;
  }

  // ADR-0082 D6: час → X через ДРОБОВИЙ logical-індекс (інтерполяція між
  // сусідніми барами + екстраполяція за краями). Раніше timeToCoordinate
  // повертав null для t, якого нема серед барів TF — якір з M15 (10:15)
  // ЗНИКАВ на H1 (бар лише 10:00); trend/rect «блимали/стрибали» між TF.
  // Кеш оновлюється у forceRender (раз/кадр); тут — лише читання (hot path:
  // рендер + hit-test на pointer events; стейлість ≤1 кадру прийнятна).
  private toX = (t_ms: number): number | null => {
    const idx = timeToFractionalIndex(this.barTimesSec, t_ms / 1000);
    if (idx === null) return null;
    return this.chartApi.timeScale().logicalToCoordinate(idx as import('lightweight-charts').Logical);
  };

  private toY = (price: number): number | null => {
    return this.seriesApi.priceToCoordinate(price);
  };

  // Інверсія — теж дробова: sub-bar точність якоря при малюванні/перетягуванні
  // (замість квантування до часу бару, симетрично до toX).
  private fromX(x: number): T_MS | null {
    const logical = this.chartApi.timeScale().coordinateToLogical(x);
    if (logical === null) return null;
    const tSec = fractionalIndexToTime(this.barTimesSec, logical as number);
    if (tSec === null) return null;
    return Math.round(tSec * 1000) as T_MS;
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

    // Find nearest OHLC by Y-pixel distance.
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

    // Snap only within threshold — prevents "flying" when cursor is outside the
    // bar's price range (e.g. cursor at bottom Y≈450 but all OHLC at Y≈150-200).
    // 120px covers normal hover-near-candle distances (debug: 6–104px) while
    // rejecting far mismatches (200–400px = cursor below candles snapping to top).
    // Old radius was 30px (too tight); "always snap" = no threshold (too loose).
    const SNAP_RADIUS_PX = 120;
    if (bestDist > SNAP_RADIUS_PX) {
      this.lastSnap = null;
      return rawPrice;
    }

    // Visual snap indicator
    const snapY = this.seriesApi.priceToCoordinate(bestPrice);
    this.lastSnap = snapY !== null ? { x, y: snapY } : null;
    return bestPrice;
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

  /** ADR-0082: перемкнути стор на СИМВОЛ (per-symbol, спільний усіма TF —
   *  cross-TF sync). Ключ не змінився (той самий символ, напр. TF-switch) →
   *  early-return: малювання лишаються в пам'яті й самі перемальовуються під
   *  новий TF (rAF). Новий символ → flush попереднього + завантаження нового
   *  (з one-time міграцією legacy per-TF сторів). `tf` більше не в ключі. */
  setStorageKey(symbol: string): void {
    const nextKey = `v4_drawings_${symbol}`;
    if (nextKey === this.storageKey) {
      // Той самий символ (TF-switch) — стан тримаємо, лише перемальовуємо під новий TF.
      this.scheduleRender();
      return;
    }
    if (this.drawings.length > 0) this.saveToStorage(); // flush попереднього символу
    this.storageKey = nextKey;
    this.migrateLegacyPerTf(symbol);
    this.loadFromStorage();
    // Legacy глобальний ключ без символу (до ADR-0007) — прибрати.
    try { localStorage.removeItem('v4_drawings'); } catch { /* ok */ }
  }

  /** ADR-0082 D4: one-time міграція — злити всі legacy per-TF стори символу
   *  (`v4_drawings_{symbol}_{tf}`) у per-symbol ключ по id (UUID → нуль колізій),
   *  далі видалити legacy. Ідемпотентно: після міграції legacy зникли → наступні
   *  завантаження просто читають per-symbol. Не втрачає жодної фігури. */
  private migrateLegacyPerTf(symbol: string): void {
    const legacyPrefix = `v4_drawings_${symbol}_`;
    const legacyKeys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(legacyPrefix)) legacyKeys.push(k);
    }
    if (legacyKeys.length === 0) return;
    try {
      const byId = new Map<string, Drawing>();
      // Наявний per-symbol стор — база (пріоритет над legacy при збігу id).
      const existingRaw = localStorage.getItem(this.storageKey);
      if (existingRaw) {
        const arr = JSON.parse(existingRaw);
        if (Array.isArray(arr)) for (const d of arr) if (d?.id) byId.set(d.id, d);
      }
      for (const lk of legacyKeys) {
        const raw = localStorage.getItem(lk);
        if (!raw) continue;
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) for (const d of arr) if (d?.id && !byId.has(d.id)) byId.set(d.id, d);
      }
      localStorage.setItem(this.storageKey, JSON.stringify([...byId.values()]));
      legacyKeys.forEach((k) => localStorage.removeItem(k));
      console.info(
        '[DrawingsRenderer] cross-TF migration: злито %d legacy per-TF сторів → %s (%d фігур)',
        legacyKeys.length, this.storageKey, byId.size,
      );
    } catch {
      // Corrupt legacy — не блокуємо завантаження (loadFromStorage прочитає що є).
    }
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
    // 0) delete-× keep-alive: над кнопкою × рахуємо як hit тіла активної фігури,
    //    щоб афорданси не зникали, поки курсор тягнеться до × (× стоїть НАД лінією).
    if (this.deleteBtn && distToPoint(cursorX, cursorY, this.deleteBtn.x, this.deleteBtn.y) <= DELETE_BTN_RADIUS_PX) {
      return { id: this.deleteBtn.id, handleIdx: null };
    }

    // 1) handles активної фігури (під курсором АБО вибраної) — хапати кінці
    const activeId = this.hovered?.id ?? this.selectedId;
    if (activeId) {
      const d = this.drawings.find((x) => x.id === activeId);
      if (d) {
        for (let j = 0; j < d.points.length; j++) {
          const hx = this.toX(d.points[j].t_ms);
          const hy = this.toY(d.points[j].price);
          if (hx === null || hy === null) continue;

          if (distToPoint(cursorX, cursorY, hx, hy) <= this.hitTolerancePx + HANDLE_REACT_MARGIN_PX) {
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
      // geometry math живе у tools/*Tool.ts модулях. this.toX/this.toY —
      // arrow class fields, auto-binded → safe передавати як ref без wrap.
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
        if (!this.draft) {
          // hline: create preview draft on hover so user sees where line will land.
          // trend/rect: draft is created only on 1st click — but ми все ж compute
          // snap preview якщо magnet ON, щоб green dot indicator біг за курсором
          // показуючи де snap приземлиться при click. TradingView-mobile pattern.
          if (this.activeTool === 'hline') {
            const rawP = this.fromY(y);
            const t = this.fromX(x);
            if (rawP !== null && t !== null) {
              const price = this.getSnappedPrice(x, y, rawP);
              this.draft = { id: uuidv4(), type: 'hline', points: [{ t_ms: t, price }], meta: this.defaultMeta('hline') };
              e.preventDefault();
              e.stopPropagation();
              this.scheduleRender();
            }
          } else if (this.snapConfig.enabled) {
            // trend/rect hover preview: compute snap target → updates lastSnap
            // as side-effect → render shows green dot. Без створення draft —
            // draft starts on 1st click як before.
            const rawP = this.fromY(y);
            if (rawP !== null) {
              this.getSnappedPrice(x, y, rawP);
              this.scheduleRender();
            }
          }
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        this.updateDraft(x, y);
        return;
      }

      // No active tool → clear stale snap indicator (left over from previous
      // tool session). Без цього green dot "застрягав" після Esc.
      if (this.lastSnap !== null) {
        this.lastSnap = null;
        this.scheduleRender();
      }

      // Eraser hover (але не блокуємо LWC)
      // Selection hover (але не блокуємо LWC)
      this.lastCursor = { x, y };
      this.hoverDirty = true;
      this.scheduleRender();
    };

    this.onPointerDownCapture = (e) => {
      // ADR-0078: лише ліва кнопка малює/вибирає/тягне. Без цього guard
      // right-click (button 2) під час draft потрапляв у finishDraft() і
      // КОМІТив фігуру мимоволі; middle-click (button 1) так само стартував
      // draft/selection. Touch/pen primary contact = button 0 → проходить.
      if (e.button !== 0) return;

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

      // Delete × (hover-афорданс по центру) — пріоритет над вибором/drag.
      if (this.deleteBtn && distToPoint(x, y, this.deleteBtn.x, this.deleteBtn.y) <= DELETE_BTN_RADIUS_PX) {
        e.preventDefault();
        e.stopPropagation();
        const target = this.drawings.find((q) => q.id === this.deleteBtn!.id);
        if (target) this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(target) });
        this.deleteBtn = null;
        this.selectedId = null;
        this.hovered = null;
        this.updateCursor();
        this.scheduleRender();
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

    // ADR-0078: right-click граматика. Owner-рішення: right-click на чарті
    // НІКОЛИ не показує native-меню браузера (консистентний pro-tool-філ).
    // Пріоритет: draft-cancel → фігура→міні-меню → порожньо (просто душимо).
    this.onContextMenuCapture = (e) => {
      e.preventDefault();
      // draft активний → скасувати незакомічену фігуру (симетрія до Escape).
      if (this.draft) {
        this.cancelDraft();
        return;
      }
      // Меню лише якщо right-click влучив у фігуру і UI-шар підписаний.
      if (!this.onContextMenu) return;
      const rect = this.interactionEl.getBoundingClientRect();
      const hit = this.performHitTest(e.clientX - rect.left, e.clientY - rect.top);
      // Промах по порожньому — закриваємо будь-яке відкрите меню (null), бо
      // dismissOnOutside реагує на click/Escape, але не на right-click.
      if (!hit) {
        this.onContextMenu(null);
        return;
      }
      const d = this.drawings.find((x) => x.id === hit.id);
      if (!d) {
        this.onContextMenu(null);
        return;
      }
      // Ціль природно під курсором (hover) → glow-підсвітка; НЕ виділяємо
      // примусово (без lingering selection після закриття меню). Справжній
      // колір фігури зберігається завдяки color-preserving hover/select у
      // tool-render (ADR-0078) — палітра flyout й полотно завжди збігаються.
      this.onContextMenu({ id: d.id, screenX: e.clientX, screenY: e.clientY, colorRole: d.meta?.colorRole ?? null, lineWidth: d.meta?.lineWidth ?? 1, lineStyle: d.meta?.lineStyle ?? 'solid' });
    };

    this.interactionEl.addEventListener('pointermove', this.onPointerMoveCapture, { capture: true });
    this.interactionEl.addEventListener('pointerdown', this.onPointerDownCapture, { capture: true });
    this.interactionEl.addEventListener('pointerup', this.onPointerUpCapture, { capture: true });
    this.interactionEl.addEventListener('pointercancel', this.onPointerCancelCapture, { capture: true });
    this.interactionEl.addEventListener('contextmenu', this.onContextMenuCapture);
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
      // hline draft was created during hover (pointermove) and finishDraft() commits it.
      // This path is reached only if user clicked without hovering (edge case).
      const d: Drawing = { id: uuidv4(), type: 'hline', points: [{ t_ms, price }], meta: this.defaultMeta('hline') };
      this.commandStack.push({ type: 'ADD', drawing: d });
      this.draft = null;
      return;
    }

    if (this.activeTool === 'trend' || this.activeTool === 'rect') {
      this.draft = {
        id: uuidv4(),
        type: this.activeTool,
        points: [{ t_ms, price }, { t_ms, price }],
        meta: this.defaultMeta(this.activeTool),
      };
      this.scheduleRender();
    }
  }

  private updateDraft(x: number, y: number): void {
    if (!this.draft) return;

    let t_ms = this.fromX(x);
    let rawPrice = this.fromY(y);

    // For hline (1-point draft), fallback to points[0]; for trend/rect to points[1].
    const lastIdx = this.draft.type === 'hline' ? 0 : 1;

    // ADR-0008: при null — fallback до останніх відомих координат (уникає "freeze" на краях)
    if (t_ms === null) t_ms = this.draft.points[lastIdx].t_ms;
    if (rawPrice === null) rawPrice = this.draft.points[lastIdx].price;
    if (t_ms === null || rawPrice === null) {
      this.warnOnce('tool_map_move_null', 'drawing_coord_null', `tool pointermove: cannot map x=${x},y=${y}`);
      return;
    }

    const price = this.getSnappedPrice(x, y, rawPrice);

    this.draft.points[lastIdx] = { t_ms, price };
    this.scheduleRender();
  }

  private finishDraft(): void {
    if (!this.draft) return;

    if (this.draft.points.length < 2) {
      // 1-point drawing (hline): always valid — commit as-is.
      this.commandStack.push({ type: 'ADD', drawing: cloneDrawing(this.draft) });
    } else {
      const a = this.draft.points[0];
      const b = this.draft.points[1];
      const isZero = a.t_ms === b.t_ms && a.price === b.price;
      if (!isZero) this.commandStack.push({ type: 'ADD', drawing: cloneDrawing(this.draft) });
    }

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
      const overX = !!this.deleteBtn && !!this.lastCursor &&
        distToPoint(this.lastCursor.x, this.lastCursor.y, this.deleteBtn.x, this.deleteBtn.y) <= DELETE_BTN_RADIUS_PX;
      if (this.hovered.handleIdx !== null) {
        // кінець → приціл (open-center reticle): бачиш точку на свічці, не затуляє
        this.interactionEl.style.cursor = CURSOR_RETICLE;
      } else if (overX) {
        // × → звичайний курсор (не рука) — тонка стрілка не ховає червоний ×
        this.interactionEl.style.cursor = 'default';
      } else {
        // тіло → рука-хват (взяв-повів)
        this.interactionEl.style.cursor = 'grab';
      }
      return;
    }
    this.interactionEl.style.cursor = '';
  }

  private forceRender(): void {
    // ADR-0082 D6: свіжий кеш часів барів — раз на кадр (guard всередині O(1)).
    this.refreshBarTimes();

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

      const baseColor = this.resolveColor(d.meta);
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

    // 2) snap indicator (magnet visual feedback).
    // Раніше gated `&& this.draft` — green dot з'являвся тільки коли user уже
    // почав малювати. Тепер показуємо щойно lastSnap встановлено (під час
    // hover preview для trend/rect, draft preview для hline, drag handles).
    // Очищення lastSnap при tool deactivate робиться в pointermove handler.
    if (this.lastSnap) {
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

    // 3) hover-афорданси (концепт: наведення проявляє редагування + видалення).
    //    Активна фігура = під курсором АБО вибрана; тільки у cursor-режимі.
    //    Крапки-кінці (renderHandles) + делікатне × по центру (видалити).
    this.deleteBtn = null;
    if (this.activeTool === null) {
      const activeId = this.hovered?.id ?? this.selectedId;
      if (activeId) {
        const d = this.drawings.find((q) => q.id === activeId);
        if (d) {
          this.renderHandles(d);
          const aabb = this.aabbById.get(d.id);
          if (aabb) {
            const cx = (aabb.minX + aabb.maxX) / 2;
            const cy = (aabb.minY + aabb.maxY) / 2;
            const overX = !!this.lastCursor && distToPoint(this.lastCursor.x, this.lastCursor.y, cx, cy) <= DELETE_BTN_RADIUS_PX;
            this.renderDeleteButton(cx, cy, overX);
            this.deleteBtn = { id: d.id, x: cx, y: cy };
          }
        }
      }
    }
  }

  private renderHandles(d: Drawing): void {
    this.ctx.save();
    this.ctx.setLineDash([]);
    this.ctx.lineWidth = 1.5;

    for (let i = 0; i < d.points.length; i++) {
      // Наведену крапку НЕ малюємо — там уже приціл (crosshair), кільце зайве.
      if (this.hovered?.id === d.id && this.hovered.handleIdx === i) continue;

      const x = this.toX(d.points[i].t_ms);
      const y = this.toY(d.points[i].price);
      if (x === null || y === null) continue;

      // Стала крапка (не росте на hover) — не затуляє точку прицілювання.
      const r = this.handleRadiusPx;

      // порожня крапка: ЛИШЕ золоте кільце, центр прозорий — видно свічку крізь нього
      this.ctx.beginPath();
      this.ctx.arc(x, y, r, 0, Math.PI * 2);
      this.ctx.strokeStyle = this.themeAccentColor;
      this.ctx.stroke();
    }

    this.ctx.restore();
  }

  /** Делікатне × по центру активної фігури (концепт: видалити на об'єкті).
   *  Без фону-кружка — лише хрестик; тонка тінь дає читабельність на лінії й на тлі.
   *  active (курсор над ×) → червоніє (--bear), семантика «видалити». */
  private renderDeleteButton(cx: number, cy: number, active: boolean): void {
    const ctx = this.ctx;
    ctx.save();
    ctx.setLineDash([]);
    ctx.lineCap = 'round';
    // хрестик — червоніє коли курсор над ним (реакція). Halo не потрібне: над ×
    // ставимо звичайний тонкий курсор (updateCursor), він не ховає червоний ×.
    ctx.shadowColor = 'rgba(0, 0, 0, 0.60)';
    ctx.shadowBlur = 3;
    ctx.strokeStyle = active ? '#ED4554' : 'rgba(232, 237, 244, 0.92)';
    ctx.lineWidth = active ? 1.7 : 1.5;
    const s = 3.5;
    ctx.beginPath();
    ctx.moveTo(cx - s, cy - s);
    ctx.lineTo(cx + s, cy + s);
    ctx.moveTo(cx + s, cy - s);
    ctx.lineTo(cx - s, cy + s);
    ctx.stroke();
    ctx.restore();
  }

  destroy(): void {
    this.ro.disconnect();
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);
    window.removeEventListener('storage', this.onStorageEvent);

    this.interactionEl.removeEventListener('pointermove', this.onPointerMoveCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerdown', this.onPointerDownCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerup', this.onPointerUpCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointercancel', this.onPointerCancelCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('contextmenu', this.onContextMenuCapture);
  }
}