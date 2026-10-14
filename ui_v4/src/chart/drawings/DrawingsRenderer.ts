// src/chart/drawings/DrawingsRenderer.ts
import { v4 as uuidv4 } from 'uuid';
import type { DataChangedScope, IChartApi, ISeriesApi, Logical, Time } from 'lightweight-charts';
import { MismatchDirection } from 'lightweight-charts';

import type { Drawing, ActiveTool, WsAction, T_MS, UiWarning, DrawingContextRequest, DrawingType, DrawingColorRole } from '../../types';
import { CommandStack, type CommandAction } from './CommandStack';
import { buildRoleColorMap } from './colorRoles';
import { timeToFractionalIndex, fractionalIndexToTime, medianStep, fractionalLogicalToX, shiftTimeByBars } from './timeMap';
import {
  HIT_TOLERANCE_PX,
  HANDLE_RADIUS_PX,
  dragStarted,
} from '../interaction/geometry';
import { getToolModule } from './tools';
import { isCorruptBackupKey, loadDrawings, saveDrawings } from './drawingsStorage';
import { cloneDrawing, completeDraft, firstClick, newDraft, previewsOnHover, tipIndex } from './drawingDraft';
import { dragBody, dragHandle, grabOffsetFor, type DragSpace } from './drawingDrag';
import { hitTestDrawings, type DrawingHit } from './drawingHitTest';
import { copyCanvas } from '../../lib/chartSnapshot';
import type { RenderContext, ToolModule } from './tools';

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

type ScreenAabb = { minX: number; minY: number; maxX: number; maxY: number };

// ADR-0080 (surface-2): частковий meta-патч для preview/commit (колір/товщина/стиль).
type DrawingMetaPatch = Partial<NonNullable<Drawing['meta']>>;

type SnapConfig = {
  enabled: boolean;
  mode: 'ohlc' | 'hl' | 'close';
};

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
  /** ADR-0112 D2: після кожного кадру — телефон оновлює ↶ і «видалити» (активні лише коли є що, editState()). */
  public onRendered: (() => void) | null = null;

  private drawings: Drawing[] = [];
  private activeTool: ActiveTool = null;
  private draft: Drawing | null = null;
  // ADR-0084 D2: ефемерна чернетка (measure) — 2-й клік «заморожує» результат (draft лишається на екрані),
  // наступний клік чистить і починає новий. Ніколи не комітиться. Нова чернетка завжди скидає прапорець.
  private draftFrozen = false;

  // v3 selection state
  private selectedId: string | null = null;
  private hovered: DrawingHit | null = null;

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
      pointerType: string; // поріг початку перетягування залежить від вказівника (geometry.dragStarted)
      started: boolean; // поріг перейдено — до того це клік (вибір), фігура не змінюється
      grabOffset?: { x: number; y: number }; // дотик: ручка мінус палець при захопленні (ADR-0112 D8)
    } = null;

  // hit-test/cache
  private aabbById = new Map<string, ScreenAabb>();

  // ADR-0082 D6: кеш часів барів (сек) поточного TF для fractional time-мапінгу.
  // Оновлюється дешевим guard-ом (length+lastTime) у forceRender — O(n) rebuild
  // лише коли дані реально змінились, НЕ щокадру (FP10-дух: без churn у hot path).
  private barTimesSec: number[] = [];
  private barTimesFirst = NaN;
  private barTimesLast = NaN;
  // Перф (audit v2): LWC data() алокує O(n) на кожен виклик — між перевірками
  // тримаємо кеш до RECHECK_MS (пан/зум між тіками реюзає кеш безкоштовно;
  // новий бар підхоплюється ≤250мс — непомітно, рендер і так тікає частіше).
  private barTimesCheckedAt = 0;
  private static readonly BAR_TIMES_RECHECK_MS = 250;
  // ADR-0083: медіанний крок барів (сек) для екстраполяції за краями —
  // gap-стійкий (weekend/session крайова пара не «розтягує» future-якорі).
  private barStepSec = 0;

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

  // ADR-0111 §3.2: знаки ціни символу з конфігу сервера (ChartPane → setPriceDigits) для лінійки; null — конфігу нема.
  private priceDigits: number | null = null;

  // ADR-0074 T2: hit-test geometry cached from CSS vars (--drawing-*).
  // Defaults match geometry.ts HIT_TOLERANCE_PX/HANDLE_RADIUS_PX constants
  // (fallback якщо tokens.css не loaded або CSS var missing).
  // FP10: НЕ read getComputedStyle у hit-test loop — refresh-ить per theme/init.
  private hitTolerancePx = HIT_TOLERANCE_PX;
  private handleRadiusPx = HANDLE_RADIUS_PX;

  // listeners
  private ro: ResizeObserver;
  private onStorageEvent!: (e: StorageEvent) => void;
  private onVisibleRangeChange!: () => void;
  private onSeriesDataChanged!: (scope: DataChangedScope) => void;

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
      this.resizeTo(width, height, Math.min(window.devicePixelRatio || 1, 2));
      this.scheduleRender();
    });

    if (this.canvas.parentElement) this.ro.observe(this.canvas.parentElement);

    // Range trigger — renderSync (not rAF) to eliminate 1-frame lag during scroll/zoom; + rAF: у самій події
    // LWC ще не перерахував autoScale (ціна→y стара), кадром пізніше — нова (ADR-0111 §3.2, D-05b, стенд 12.10)
    this.onVisibleRangeChange = () => { this.renderSync(); this.scheduleRender(); };
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(this.onVisibleRangeChange);
    // Дані серії змінились без зміни видимого діапазону: тік розширив high/low → autoScale зсунув ціна→y (D-05);
    // 'full' (setData: старіша історія, заміна барів) зсуває індекси барів — кеш часів негайно (D-09)
    this.onSeriesDataChanged = (scope) => {
      if (scope === 'full') this.refreshBarTimes(true);
      this.scheduleRender();
    };
    this.seriesApi.subscribeDataChanged(this.onSeriesDataChanged);

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
      // Зовнішній стан = нова істина: активний preview-снапшот забуваємо,
      // інакше revert відкотив би СВІЖИЙ commit іншої вкладки (audit v2).
      this.previewOrig = null;
      this.loadFromStorage();
    };
    window.addEventListener('storage', this.onStorageEvent);

    // НЕ загружаємо тут — чекаємо на setStorageKey(symbol) від ChartPane
    // (ADR-0082: per-symbol стор, спільний усіма TF).
  }

  setAll(drawings: Drawing[]): void {
    // Full frame = ймовірна заміна серії (TF/symbol switch) — негайна
    // ревалідація кешу барів (audit v2: guard-протикання на switch).
    this.refreshBarTimes(true);
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
    this.draftFrozen = false;

    // UX: курсор тільки коли tool активний або є hovered/drag
    this.updateCursor();
    this.scheduleRender();
  }

  cancelDraft(): void {
    this.draft = null;
    this.draftFrozen = false;
    this.scheduleRender();
  }

  /** ADR-0078: видалити фігуру за id (undoable DELETE, та сама команда, що й гумка). */
  deleteById(id: string): void {
    const d = this.drawings.find((x) => x.id === id);
    if (!d) return;
    this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(d) });
  }

  /** Delete/Backspace (12.10.2026): видалити виділену фігуру — та сама undoable DELETE і той самий скид афордансів,
   *  що й у ×. Живий прев'ю стилю спершу знімається, щоб undo повернув фігуру з її справжнім meta.
   *  false — нічого не виділено. */
  deleteSelected(): boolean {
    const target = this.selectedId === null ? undefined : this.drawings.find((q) => q.id === this.selectedId);
    if (!target) return false;
    this.revertPreview();
    this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(target) });
    this.clearSelection();
    return true;
  }

  /** Esc / після видалення: зняти виділення — ручки, × і підсвітка гаснуть. */
  clearSelection(): void {
    if (this.selectedId === null && this.hovered === null && this.deleteBtn === null) return;
    this.selectedId = null;
    this.hovered = null;
    this.deleteBtn = null;
    this.updateCursor();
    this.scheduleRender();
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

  /** Audit v2: зняти активний live-preview (якщо є) — повернути фігурі
   *  оригінальний meta. Кличеться ПЕРЕД undo/redo (ChartPane): інакше
   *  applyLocally→saveToStorage персистив би незакомічений прев'ю-стан
   *  (і розсилав його cross-tab). */
  revertPreview(): void {
    if (!this.previewOrig) return;
    const d = this.drawings.find((x) => x.id === this.previewOrig!.id);
    if (d) d.meta = this.previewOrig.meta ? { ...this.previewOrig.meta } : undefined;
    this.previewOrig = null;
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

  /** ADR-0111 §3.2: знаки ціни символу (`price_display` з конфігу) → підпис лінійки (RenderContext.priceDigits). */
  setPriceDigits(digits: number | null): void {
    this.priceDigits = digits;
    this.scheduleRender();
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

  /** ADR-0082 D6 (+audit v2 hardening): оновити кеш часів барів, якщо серія
   *  змінилась. Guard = (length, FIRST, last) — first ловить symbol/TF-switch,
   *  де length+last випадково збіглись (різний інтер'єр). data() алокує O(n)
   *  на виклик → між перевірками кеш вважається свіжим RECHECK_MS (пан/зум
   *  реюзає задарма; `force` — негайна перевірка на відомих зламах кешу). */
  private refreshBarTimes(force = false): void {
    const now = performance.now();
    if (!force && now - this.barTimesCheckedAt < DrawingsRenderer.BAR_TIMES_RECHECK_MS) return;
    this.barTimesCheckedAt = now;

    const bars = this.seriesApi.data();
    const n = bars.length;
    const firstT = n > 0 ? timeToSec(bars[0].time as HorzScaleItem) : NaN;
    const lastT = n > 0 ? timeToSec(bars[n - 1].time as HorzScaleItem) : NaN;
    const same = (a: number, b: number) => a === b || (Number.isNaN(a) && Number.isNaN(b));
    if (n === this.barTimesSec.length && same(firstT, this.barTimesFirst) && same(lastT, this.barTimesLast)) return;
    this.barTimesSec = bars.map((b) => timeToSec(b.time as HorzScaleItem));
    this.barTimesFirst = firstT;
    this.barTimesLast = lastT;
    this.barStepSec = medianStep(this.barTimesSec);
  }

  // ADR-0082 D6: час → X через ДРОБОВИЙ logical-індекс (інтерполяція між
  // сусідніми барами + екстраполяція за краями). Раніше timeToCoordinate
  // повертав null для t, якого нема серед барів TF — якір з M15 (10:15)
  // ЗНИКАВ на H1 (бар лише 10:00); trend/rect «блимали/стрибали» між TF.
  // Індекс → x лише через fractionalLogicalToX: LWC рахує координату тільки для
  // цілого індексу, дробовий напряму давав x=0 (12.10.2026 — фігура з M15 на H1
  // стискалась у вертикаль на лівому краї).
  // Кеш оновлюється у forceRender (раз/кадр); тут — лише читання (hot path:
  // рендер + hit-test на pointer events; стейлість ≤1 кадру прийнятна).
  private toX = (t_ms: number): number | null => {
    const idx = timeToFractionalIndex(this.barTimesSec, t_ms / 1000, this.barStepSec);
    if (idx === null) return null;
    const timeScale = this.chartApi.timeScale();
    return fractionalLogicalToX(idx, (index) => timeScale.logicalToCoordinate(index as Logical));
  };

  private toY = (price: number): number | null => {
    return this.seriesApi.priceToCoordinate(price);
  };

  // Інверсія: coordinateToLogical у LWC — ЦІЛИЙ індекс (Math.ceil), тож новий якір
  // стає на бар поточного TF; частку бару зберігають лише якорі з іншого TF
  // (перетягування тіла — shiftTimeByBars, не через x).
  private fromX(x: number): T_MS | null {
    const logical = this.chartApi.timeScale().coordinateToLogical(x);
    if (logical === null) return null;
    const tSec = fractionalIndexToTime(this.barTimesSec, logical as number, this.barStepSec);
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
    // Палець (курсор ADR-0112 D7 і ручка D8) — «слабкий» магніт: прилипає лише поруч із рівнем, тож прилипання щось
    // означає («ти на хаї»); 120 px тримали б точку на рівнях майже завжди, а ручка стрибала б під пальцем на ~100 px.
    const SNAP_RADIUS_PX = this.touchCursorMode || this.dragState?.pointerType === 'touch' ? 16 : 120;
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
    // Чернетка не переїжджає на інший символ (аудит 12.10: точка тренда на XAU 2650 → NAS100 → клік писав її в NAS100)
    this.draft = null;
    this.draftFrozen = false;
    // Audit v2: undo-стек НЕ переживає символ — інакше Ctrl+Z матеріалізував
    // би фігури чужого символу в новий стор. Preview-снапшот теж чистимо.
    this.commandStack.clear();
    this.previewOrig = null;
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
      if (k && k.startsWith(legacyPrefix) && !isCorruptBackupKey(k)) legacyKeys.push(k); // копія битого — не legacy
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

  // I5 (ADR-0111 §3.2): збій збереження/битий JSON — гучно (консоль + діагностика), не мовчки (drawingsStorage.ts)
  private saveToStorage(): void {
    const problem = saveDrawings(this.storageKey, this.drawings);
    if (problem === null) return;
    console.warn('[DrawingsRenderer] DRAWINGS_SAVE_FAILED key=%s n=%d %s', this.storageKey, this.drawings.length, problem);
    this.warnOnce('storage_save', 'drawing_storage_failed', `save ${this.storageKey}: ${problem} — після перезавантаження не буде`);
  }

  private loadFromStorage(): void {
    const { drawings, problem } = loadDrawings(this.storageKey, Date.now());
    this.drawings = drawings;
    if (problem !== null) {
      console.warn('[DrawingsRenderer] DRAWINGS_LOAD_FAILED key=%s %s', this.storageKey, problem);
      this.warnOnce('storage_load', 'drawing_storage_failed', `load ${this.storageKey}: ${problem}`);
    }
    this.scheduleRender();
  }

  // ---- hit-testing (пріоритети — drawingHitTest.ts, ADR-0112 D8) ----
  private performHitTest(cursorX: number, cursorY: number): DrawingHit | null {
    return hitTestDrawings(
      {
        drawings: this.drawings,
        activeId: this.hovered?.id ?? this.selectedId,
        deleteBtn: this.deleteBtn,
        aabbById: this.aabbById,
        tolerancePx: this.hitTolerancePx,
        toX: this.toX,
        toY: this.toY,
      },
      cursorX,
      cursorY,
    );
  }

  // ---- interactions (capture on container) ----
  private setupInteractionsCapture(): void {
    this.onPointerMoveCapture = (e) => {
      if (this.ignoresPointer(e)) return;
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
      const tool = this.activeDrawingTool();
      if (tool) {
        if (this.toolMove(tool, x, y)) {
          e.preventDefault();
          e.stopPropagation();
        }
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
      if (this.ignoresPointer(e)) return;
      // ADR-0078: лише ліва кнопка малює/вибирає/тягне. Без цього guard
      // right-click (button 2) під час draft потрапляв у finishDraft() і
      // КОМІТив фігуру мимоволі; middle-click (button 1) так само стартував
      // draft/selection. Touch/pen primary contact = button 0 → проходить.
      if (e.button !== 0) return;

      const rect = this.interactionEl.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Tool: click-click state machine (TradingView-style)
      if (this.activeDrawingTool()) {
        e.preventDefault();
        e.stopPropagation();
        this.toolClick(x, y);
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

      const hit = this.performHitTest(x, y);

      // × (hover-афорданс по центру, ADR-0079) — коли вказівник ближче до нього, ніж до ручки (ADR-0112 D8).
      if (hit?.onDelete) {
        e.preventDefault();
        e.stopPropagation();
        const target = this.drawings.find((q) => q.id === hit.id);
        if (target) this.commandStack.push({ type: 'DELETE', drawing: cloneDrawing(target) });
        this.deleteBtn = null;
        this.selectedId = null;
        this.hovered = null;
        this.updateCursor();
        this.scheduleRender();
        return;
      }

      // Selection/Drag: тільки якщо реально hit
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
      const anchor = hit.handleIdx === null ? undefined : d.points[hit.handleIdx];

      this.interactionEl.setPointerCapture(e.pointerId);
      this.dragState = {
        id: hit.id,
        handleIdx: hit.handleIdx,
        pointerId: e.pointerId,
        startX: x,
        startY: y,
        startObj: cloneDrawing(d),
        pointerType: e.pointerType,
        started: false,
        grabOffset: anchor ? grabOffsetFor(e.pointerType, { x: this.toX(anchor.t_ms), y: this.toY(anchor.price) }, x, y) : undefined,
      };
    };

    this.onPointerUpCapture = (e) => {
      if (this.ignoresPointer(e)) return;
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
      if (this.ignoresPointer(e)) return;
      if (this.dragState) {
        e.preventDefault();
        e.stopPropagation();
        // Перерване перетягування — фігура вертається як на початку (інакше зсунута жила в пам'яті поза undo, D-13)
        const { startObj } = this.dragState;
        if (this.drawings.some((d) => d.id === startObj.id)) this.drawings = this.replaceById(cloneDrawing(startObj));
        this.dragState = null;
      }
      this.draft = null;
      this.draftFrozen = false; // прапорець живе лише разом із чернеткою (рецензія кроку 3)
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

  // ---- tool create (ADR-0111 §3.3: за pointsRequired/ephemeral з реєстру — drawingDraft.ts) ----
  /** Інструмент-фігура з реєстру, якщо вибрано його (не курсор і не гумка). */
  private activeDrawingTool(): ToolModule | undefined {
    const active = this.activeTool;
    return active && active !== 'eraser' ? getToolModule(active) : undefined;
  }

  /** Рух вказівника з обраним інструментом — однаково для миші й курсора на телефоні (ADR-0112): прев'ю
   *  однокрокової під курсором, кінчик чернетки двоточкової, магніт. true — подія наша (блокуємо LWC). */
  private toolMove(tool: ToolModule, x: number, y: number): boolean {
    if (!this.draft) {
      if (previewsOnHover(tool)) {
        const rawP = this.fromY(y);
        const t = this.fromX(x);
        if (rawP === null || t === null) return false;
        const price = this.getSnappedPrice(x, y, rawP);
        this.draft = newDraft(tool, uuidv4(), { t_ms: t, price }, this.defaultMeta(tool.id));
        this.draftFrozen = false;
        this.scheduleRender();
        return true;
      }
      // Двоточкові: чернетка лише з 1-го кліку, але з магнітом зелена точка біжить за курсором (lastSnap).
      if (this.snapConfig.enabled) {
        const rawP = this.fromY(y);
        if (rawP !== null) {
          this.getSnappedPrice(x, y, rawP);
          this.scheduleRender();
        }
      }
      return false;
    }
    // ADR-0084 D2: заморожений вимір не слідує за курсором; чарт дихає (без preventDefault).
    if (this.draftFrozen) return false;
    this.updateDraft(x, y);
    return true;
  }

  /** Клік інструментом: заморожений вимір → новий; є чернетка → завершити (measure → freeze); нема → перший клік. */
  private toolClick(x: number, y: number): void {
    if (this.draft && this.draftFrozen) {
      this.draft = null;
      this.draftFrozen = false;
      this.handleToolPointerDown(x, y);
    } else if (this.draft) {
      this.finishDraft();
    } else {
      this.handleToolPointerDown(x, y);
    }
  }

  // ---- ADR-0112: курсор-перехрестя на телефоні веде той самий шлях створення, що миша ----
  private touchCursorMode = false;

  /** Режим курсора: дотики пальцем до фігур не доходять — точку веде touchDrawCursor. */
  setTouchCursorMode(on: boolean): void {
    this.touchCursorMode = on;
  }

  private ignoresPointer(e: PointerEvent): boolean {
    return this.touchCursorMode && e.pointerType === 'touch';
  }

  /** Курсор у (x, y), CSS px панелі — як рух миші з обраним інструментом. Повертає ціну, до якої прилип магніт
   *  (кільце стає на неї, D7), або null — магніт вимкнено, рівень далеко чи вимір заморожено. */
  cursorMove(x: number, y: number): number | null {
    const tool = this.activeDrawingTool();
    if (!tool) return null;
    this.toolMove(tool, x, y);
    const raw = this.fromY(y);
    if (!this.snapConfig.enabled || raw === null || this.draftFrozen) return null;
    const price = this.getSnappedPrice(x, y, raw);
    return price === raw ? null : price;
  }

  /** «Поставити» в (x, y) — як клік мишею. 'draft' — фігура в роботі; 'done' — готова або вимір заморожено;
   *  'none' — нічого (нульова фігура відкинута або точку не перевести — інструмент лишається, рецензія D-3). */
  cursorPlace(x: number, y: number): 'draft' | 'done' | 'none' {
    if (!this.activeDrawingTool()) return 'none';
    const committedBefore = this.drawings.length;
    this.toolClick(x, y);
    if (this.draft) return this.draftFrozen ? 'done' : 'draft';
    return this.drawings.length > committedBefore ? 'done' : 'none';
  }

  private handleToolPointerDown(x: number, y: number): void {
    const tool = this.activeDrawingTool();
    if (!tool) return;
    const t_ms = this.fromX(x);
    const rawPrice = this.fromY(y);

    if (t_ms === null || rawPrice === null) {
      this.warnOnce('tool_map_down_null', 'drawing_coord_null', `tool pointerdown: cannot map x=${x},y=${y}`);
      return;
    }

    const price = this.getSnappedPrice(x, y, rawPrice);
    // Однокрокова без прев'ю під курсором (клік без руху миші) — готова одразу; двоточкова — чернетка.
    const step = firstClick(tool, uuidv4(), { t_ms, price }, this.defaultMeta(tool.id));
    this.draftFrozen = false;
    if (step.kind === 'commit') {
      this.commandStack.push({ type: 'ADD', drawing: step.drawing });
      this.draft = null;
      return;
    }
    this.draft = step.draft;
    this.scheduleRender();
  }

  private updateDraft(x: number, y: number): void {
    if (!this.draft) return;

    let t_ms = this.fromX(x);
    let rawPrice = this.fromY(y);

    // За курсором іде остання точка (однокрокова — єдина, двоточкова — друга).
    const lastIdx = tipIndex(this.draft);

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

    const tool = getToolModule(this.draft.type);
    if (!tool) {
      // Недосяжне, поки реєстр повний за типом (satisfies Record<DrawingType, ToolModule>) — але не тихо (I5).
      this.warnOnce('draft_unknown_type', 'drawing_state_inconsistent', `finishDraft: тип ${this.draft.type} не в реєстрі`);
      this.draft = null;
      return;
    }
    const step = completeDraft(tool, this.draft);
    // ADR-0084 D2: ефемерна (measure) НЕ комітиться — 2-й клік заморожує результат на
    // екрані (draft лишається), наступний клік почне новий вимір.
    if (step.kind === 'freeze') {
      this.draftFrozen = true;
      this.scheduleRender();
      return;
    }
    if (step.kind === 'commit') this.commandStack.push({ type: 'ADD', drawing: step.drawing });

    this.draft = null;
    // Tool stays active for continuous drawing (TradingView-style); Escape to exit
  }

  // ---- drag (математика — drawingDrag.ts) ----
  /** Простір координат для drawingDrag: LWC timeScale/series, магніт і кеш часів барів цього renderer-а. */
  private readonly dragSpace: DragSpace = {
    logicalAt: (x) => this.chartApi.timeScale().coordinateToLogical(x) as number | null,
    fromX: (x) => this.fromX(x),
    fromY: (y) => this.fromY(y),
    toY: (price) => this.toY(price),
    snapPrice: (x, y, rawPrice) => this.getSnappedPrice(x, y, rawPrice),
    shiftTimeByBars: (tSec, bars) => shiftTimeByBars(this.barTimesSec, tSec, bars, this.barStepSec),
  };

  private handleDragMove(x: number, y: number): void {
    const drag = this.dragState;
    if (!drag) return;
    // Поріг (ADR-0111, 13.10): поки зсув від натискання менший — це клік, ні ручку, ні тіло не чіпаємо.
    if (!drag.started) {
      if (!dragStarted(x - drag.startX, y - drag.startY, drag.pointerType)) return;
      drag.started = true;
    }
    const current = this.drawings.find((d) => d.id === drag.id);
    if (!current) return;
    // Ручка — під курсор (з магнітом); тіло — на ціле число барів і пікселі ціни (drawingDrag.ts).
    const step = drag.handleIdx !== null
      ? dragHandle(current, drag, drag.handleIdx, x, y, this.dragSpace)
      : dragBody(current, drag, x, y, this.dragSpace);
    if (step.kind === 'skip') {
      this.warnOnce(step.warnKey, 'drawing_coord_null', step.detail);
      return;
    }
    this.drawings = this.replaceById(step.next);
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
      if (this.hovered.handleIdx !== null) {
        // кінець → приціл (open-center reticle): бачиш точку на свічці, не затуляє
        this.interactionEl.style.cursor = CURSOR_RETICLE;
      } else if (this.hovered.onDelete) {
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

  /** Полотно до розміру `cssW`×`cssH` у щільності `dpr` (ResizeObserver і знімок ADR-0109). */
  private resizeTo(cssW: number, cssH: number, dpr: number): void {
    this.dpr = dpr;
    this.canvas.width = Math.floor(cssW * dpr);
    this.canvas.height = Math.floor(cssH * dpr);
    this.canvas.style.width = `${Math.floor(cssW)}px`;
    this.canvas.style.height = `${Math.floor(cssH)}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // ADR-0109: розмір і стани редагування до знімка — повертаються в restoreSnapshot
  private beforeSnapshot: {
    cssW: number;
    cssH: number;
    dpr: number;
    hovered: DrawingHit | null;
    selectedId: string | null;
    draft: Drawing | null;
    lastSnap: { x: number; y: number } | null;
    hoverDirty: boolean;
  } | null = null;

  /** ADR-0109: фігури для знімка в розмірі `cssW`×`cssH` і щільності `dpr` — без наведення, виділення, ручок,
   *  × видалення, чернетки і крапки магніту; повертає копію. Графік LWC мусить уже мати той самий розмір. Полотно і
   *  стани повертає `restoreSnapshot` — після повернення розміру графіка, у тому самому виклику. */
  snapshotAt(cssW: number, cssH: number, dpr: number): HTMLCanvasElement {
    this.beforeSnapshot ??= {
      cssW: this.canvas.width / this.dpr,
      cssH: this.canvas.height / this.dpr,
      dpr: this.dpr,
      hovered: this.hovered,
      selectedId: this.selectedId,
      draft: this.draft,
      lastSnap: this.lastSnap,
      hoverDirty: this.hoverDirty,
    };
    this.hovered = null;
    this.selectedId = null;
    this.draft = null;
    this.lastSnap = null;
    this.hoverDirty = false;
    this.resizeTo(cssW, cssH, dpr);
    this.renderSync();
    return copyCanvas(this.canvas);
  }

  /** ADR-0109: розмір і стани редагування до знімка — назад, і кадр одразу, без проміжного з координатами рамки. */
  restoreSnapshot(): void {
    const prev = this.beforeSnapshot;
    if (!prev) return;
    this.beforeSnapshot = null;
    this.hovered = prev.hovered;
    this.selectedId = prev.selectedId;
    this.draft = prev.draft;
    this.lastSnap = prev.lastSnap;
    this.hoverDirty = prev.hoverDirty;
    this.resizeTo(prev.cssW, prev.cssH, prev.dpr);
    this.renderSync();
  }

  /** Чи є що скасувати і чи виділено фігуру — для кнопок телефона (ADR-0112 D2). */
  /** Палець тягне фігуру (ADR-0112 D8): дотики цього жесту не йдуть до замка перехрестя і LWC (figureTouchGuard). */
  ownsTouch(): boolean {
    return this.dragState?.pointerType === 'touch';
  }

  editState(): { canUndo: boolean; hasSelection: boolean } {
    return { canUndo: this.commandStack.canUndo(), hasSelection: this.selectedId !== null };
  }

  private forceRender(): void {
    // ADR-0082 D6: свіжий кеш часів барів (guard + throttle всередині).
    this.refreshBarTimes();
    this.onRendered?.();

    const canvasW = this.canvas.width / this.dpr;
    const canvasH = this.canvas.height / this.dpr;

    // Audit v2 / owner-репорт (в): малювання НЕ заходить на цінову шкалу і
    // часову вісь — робоча площина = пана чарта. LWC віддає розміри осей
    // (дешеві getters); fallback → повний canvas (деградація видима, не тиха).
    let paneW = canvasW;
    let paneH = canvasH;
    try {
      const psw = this.chartApi.priceScale('right').width();
      const tsh = this.chartApi.timeScale().height();
      if (psw > 0 && psw < canvasW) paneW = canvasW - psw;
      if (tsh > 0 && tsh < canvasH) paneH = canvasH - tsh;
    } catch { /* до першого layout LWC — повний canvas */ }
    const cssW = paneW;
    const cssH = paneH;

    // 1) hover compute (latest-wins) — 1 раз/кадр
    if (this.hoverDirty && this.lastCursor) {
      this.hoverDirty = false;
      this.hovered = this.performHitTest(this.lastCursor.x, this.lastCursor.y);
      this.updateCursor();
    }

    this.ctx.clearRect(0, 0, canvasW, canvasH);
    this.aabbById.clear();

    // Belt до cssW/cssH вище: жоден штрих (glow/тінь/лейбл включно) не
    // протікає на шкали.
    this.ctx.save();
    this.ctx.beginPath();
    this.ctx.rect(0, 0, paneW, paneH);
    this.ctx.clip();

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
        bullColor: this.roleColors.bull,
        bearColor: this.roleColors.bear,
        priceDigits: this.priceDigits,
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
    if (this.lastSnap && !this.touchCursorMode) { // на телефоні прилипання показує саме кільце (D7)
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
    //    Крапки-кінці (renderHandles) + делікатне × (видалити). Позиція × —
    //    tool.deleteAnchor (ray: середина ВИДИМОГО сегмента) або центр AABB.
    this.deleteBtn = null;
    if (this.activeTool === null) {
      const activeId = this.hovered?.id ?? this.selectedId;
      if (activeId) {
        const d = this.drawings.find((q) => q.id === activeId);
        if (d) {
          this.renderHandles(d);
          const tool = getToolModule(d.type);
          let cx: number | null = null;
          let cy: number | null = null;
          const anchor = tool?.deleteAnchor?.(d, toX, toY, cssW, cssH);
          if (anchor) {
            cx = anchor.x;
            cy = anchor.y;
          } else {
            const aabb = this.aabbById.get(d.id);
            if (aabb) {
              cx = (aabb.minX + aabb.maxX) / 2;
              cy = (aabb.minY + aabb.maxY) / 2;
            }
          }
          if (cx !== null && cy !== null) {
            // Червоне — лише коли клік справді видалить (ручка поруч бере гору, drawingHitTest)
            this.renderDeleteButton(cx, cy, this.hovered?.id === d.id && this.hovered.onDelete);
            this.deleteBtn = { id: d.id, x: cx, y: cy };
          }
        }
      }
    }

    this.ctx.restore(); // pane-clip (шкали чисті)
  }

  private renderHandles(d: Drawing): void {
    this.ctx.save();
    this.ctx.setLineDash([]);
    this.ctx.lineWidth = 1.5;

    const secondary = getToolModule(d.type)?.secondaryHandles;

    for (let i = 0; i < d.points.length; i++) {
      // Наведену крапку НЕ малюємо — там уже приціл (crosshair), кільце зайве.
      if (this.hovered?.id === d.id && this.hovered.handleIdx === i) continue;

      const x = this.toX(d.points[i].t_ms);
      const y = this.toY(d.points[i].price);
      if (x === null || y === null) continue;

      // Вторинний якір (ray: напрямна точка) — менший і тьмяніший: ієрархія
      // замість «двох однакових крапок на кінці» (owner-репорт б).
      const isSecondary = secondary?.includes(i) ?? false;
      const r = isSecondary ? this.handleRadiusPx * 0.75 : this.handleRadiusPx;
      this.ctx.globalAlpha = isSecondary ? 0.55 : 1;

      // порожня крапка: ЛИШЕ золоте кільце, центр прозорий — видно свічку крізь нього
      this.ctx.beginPath();
      this.ctx.arc(x, y, r, 0, Math.PI * 2);
      this.ctx.strokeStyle = this.themeAccentColor;
      this.ctx.stroke();
    }
    this.ctx.globalAlpha = 1;

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
    this.chartApi.timeScale().unsubscribeVisibleTimeRangeChange(this.onVisibleRangeChange);
    this.seriesApi.unsubscribeDataChanged(this.onSeriesDataChanged);

    this.interactionEl.removeEventListener('pointermove', this.onPointerMoveCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerdown', this.onPointerDownCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointerup', this.onPointerUpCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('pointercancel', this.onPointerCancelCapture, { capture: true } as any);
    this.interactionEl.removeEventListener('contextmenu', this.onContextMenuCapture);
  }
}