// src/chart/drawings/tools/types.ts
// ADR-0074 T1: Tool Registry contract.
//
// Кожен drawing tool — declarative модуль що реалізує цей інтерфейс.
// DrawingsRenderer тримає Map<DrawingType, ToolModule> і delegating
// викликає render/hitTest/drawHandles замість inline switches по `d.type`.
//
// Open/closed: додавання нового tool у ADR-B (h_ray, ray, channel, text,
// fib_retracement) = новий файл з ToolModule + index entry, БЕЗ редагування
// DrawingsRenderer-а.
//
// Performance contract: render/hitTest/drawHandles викликаються per-frame
// per-drawing — НЕ alloc-ити об'єкти у hot path (use plain numbers / shared
// scratch buffers). FP10: НЕ викликати `getComputedStyle` тут — renderer
// caches CSS-vars і передає numeric values через RenderContext.

import type { Drawing, DrawingType } from '../../../types';

/** Контекст рендеру переданий tool-у з DrawingsRenderer.
 *  toX/toY повертають null коли точка поза visible range — tool MUST
 *  early-return у такому випадку, не намагатись малювати з placeholder. */
export interface RenderContext {
    /** Canvas 2D context (DPR-scaled, CSS-px coordinate space). */
    readonly ctx: CanvasRenderingContext2D;
    /** Time-coordinate transform. Returns null for off-screen times. */
    readonly toX: (t_ms: number) => number | null;
    /** Price-coordinate transform. Returns null for off-screen prices. */
    readonly toY: (price: number) => number | null;
    /** Resolved color для idle stroke (theme-aware via App.svelte tokens). */
    readonly baseColor: string;
    /** Accent color для draft / hovered / selected states. */
    readonly accentColor: string;
    /** Fill для rect-shaped tools (semi-transparent). */
    readonly rectFill: string;
    /** ADR-0084 (optional, additive): напрямні кольори для MeasureTool
     *  (bull/bear токени theme-aware з roleColors renderer-а). */
    readonly bullColor?: string;
    readonly bearColor?: string;
    /** ADR-0111 §3.2 (optional, additive): знаки ціни символу з конфігу сервера
     *  (`price_display`, ChartPane.symbolPriceDigits) для підпису MeasureTool.
     *  null/відсутнє → евристика за величиною ціни (displayPriceDigits). */
    readonly priceDigits?: number | null;
    /** Render-state hints. Tool varies stroke/fill based on these. */
    readonly isDraft: boolean;
    readonly isHovered: boolean;
    readonly isSelected: boolean;
    /** Canvas CSS-px dimensions (для tools що тягнуться full-width like hline). */
    readonly cssW: number;
    readonly cssH: number;
}

/** Axis-aligned bounding box у CSS-px screen-coords. Returned from render
 *  для cache → fast hit-test rejection БЕЗ повторного coordinate transform. */
export interface ScreenAabb {
    readonly minX: number;
    readonly minY: number;
    readonly maxX: number;
    readonly maxY: number;
}

/** Hit-test result. distance у screen CSS-px. */
export type HitTestResult =
    | { readonly hit: true; readonly distance: number }
    | { readonly hit: false };

/** Контракт одного drawing tool. Stateless: всі state живуть у Drawing
 *  або у DrawingsRenderer; tool-instances singleton-и у TOOL_REGISTRY. */
export interface ToolModule {
    /** Discriminator у Drawing.type і ключ у TOOL_REGISTRY (повноту реєстру тримає тип у tools/index.ts). */
    readonly id: DrawingType;

    /** Скільки точок ставить користувач (створення — drawingDraft.ts):
     *  1 = фігура готова з першого кліку, до кліку видно під курсором (hline)
     *  2 = клік-клік: перший ставить початок, другий завершує (trend, rect, ray, measure) */
    readonly pointsRequired: 1 | 2;

    /** ADR-0111 §3.3: кнопка панелі «Малювання» — з цих полів (одне джерело підпису, клавіші й іконки). */
    readonly label: string;
    /** Внутрішня розмітка SVG 24×24 (stroke=currentColor; Lucide v0.460, MIT) — панель вставляє її в <svg>. */
    readonly icon: string;
    /** Гаряча клавіша: одна літера або '\\' (trend); у підказці панелі — великою. */
    readonly hotkey: string;
    /** Додаткові клавіші того самого інструмента (trend: 't' поруч із '\\'). */
    readonly hotkeyAliases?: readonly string[];
    /** Має стиль (колір/товщина/лінія): правий клік на іконці → flyout, дефолт нових фігур у App. */
    readonly styleable: boolean;
    /** ADR-0084 D2: фігура живе лише як чернетка — 2-й клік заморожує результат на екрані; не комітиться, не
     *  зберігається, стилів не має (measure). */
    readonly ephemeral?: boolean;

    /**
     * Render drawing у переданому контексті. MUST early-return null коли
     * any-point coordinate transform повернув null (off-screen). Setting
     * stroke/fill/lineDash на ctx — відповідальність tool-а; renderer не
     * робить save/restore між викликами per-tool (performance).
     *
     * @returns ScreenAabb для hit-test cache, або null якщо drawing skipped.
     */
    render(d: Drawing, rc: RenderContext): ScreenAabb | null;

    /** ADR-0084 polish: індекси «вторинних» якорів (рендеряться делікатніше —
     *  менші/тьмяніші кільця). Ray: [1] — p2 лише задає напрямок, візуально
     *  підпорядкований стартовій точці. Відсутнє → всі якорі первинні. */
    readonly secondaryHandles?: readonly number[];

    /** ADR-0084 polish (audit v2 confirmed): інтер'єр AABB = тіло фігури.
     *  Rect: true — hover/drag/keep-alive до × працюють ЗСЕРЕДИНИ (грань-only
     *  hit лишав × недосяжним). Для лінійних фігур НЕ ставити (AABB ≠ фігура). */
    readonly interiorIsBody?: boolean;

    /**
     * ADR-0084 polish: позиція delete-× афорданса (ADR-0079). Відсутнє →
     * renderer використовує центр AABB (коректно для trend/hline/rect).
     * Ray перевизначає: середина ВИДИМОГО сегмента — центр кліпнутого AABB
     * не лежить на лінії, × «висів у пустоті».
     */
    deleteAnchor?(
        d: Drawing,
        toX: (t_ms: number) => number | null,
        toY: (price: number) => number | null,
        cssW: number,
        cssH: number,
    ): { x: number; y: number } | null;

    /**
     * Hit-test проти drawing з cursor у CSS-px. tolerance = caller's hit
     * tolerance budget (renderer caches з CSS-var, default 10px desktop /
     * 16px mobile per ADR-0074 T2).
     */
    hitTest(
        d: Drawing,
        cursorX: number,
        cursorY: number,
        tolerance: number,
        toX: (t_ms: number) => number | null,
        toY: (price: number) => number | null,
    ): HitTestResult;
}
