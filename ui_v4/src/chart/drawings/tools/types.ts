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

import type { Drawing } from '../../../types';

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
    /** Discriminator у Drawing.type. Має збігатись з ключем у TOOL_REGISTRY. */
    readonly id: 'hline' | 'trend' | 'rect';

    /** Скільки точок очікує draft state machine.
     *  1 = instant commit на 1-st click (e.g. hline)
     *  2 = click-click pattern (e.g. trend, rect) */
    readonly pointsRequired: 1 | 2;

    /** UI metadata для DrawingToolbar (Slice T3 використає). */
    readonly label: string;
    readonly icon: string; // Lucide name або inline SVG ref (T3 resolved)
    readonly hotkey: string; // single char або '\\' для trend

    /**
     * Render drawing у переданому контексті. MUST early-return null коли
     * any-point coordinate transform повернув null (off-screen). Setting
     * stroke/fill/lineDash на ctx — відповідальність tool-а; renderer не
     * робить save/restore між викликами per-tool (performance).
     *
     * @returns ScreenAabb для hit-test cache, або null якщо drawing skipped.
     */
    render(d: Drawing, rc: RenderContext): ScreenAabb | null;

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
