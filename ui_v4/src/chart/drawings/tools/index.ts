// src/chart/drawings/tools/index.ts
// ADR-0074 T1: TOOL_REGISTRY — central Map<DrawingType, ToolModule>.
//
// SSOT for tool discovery. DrawingsRenderer queries TOOL_REGISTRY.get(d.type)
// замість inline if/else по type. Додавання нового tool у ADR-B = новий файл
// з ToolModule + entry тут, БЕЗ редагування renderer-а (open/closed).
//
// FP9 reminder: localStorage НЕ touched here — DrawingsRenderer власник
// persistence до ADR-B.

import type { DrawingType } from '../../../types';
import { HLineTool } from './HLineTool';
import { TrendTool } from './TrendTool';
import { RectTool } from './RectTool';
import type { ToolModule } from './types';

/** Read-only registry. Iteration order = insertion order (hline → trend → rect)
 *  для deterministic toolbar rendering у T3. */
export const TOOL_REGISTRY: ReadonlyMap<DrawingType, ToolModule> = new Map<
    DrawingType,
    ToolModule
>([
    [HLineTool.id, HLineTool],
    [TrendTool.id, TrendTool],
    [RectTool.id, RectTool],
]);

/** Type-safe lookup. Throws у dev mode для unknown type щоб виявити drift
 *  між Drawing.type union і registry keys. Production: returns undefined. */
export function getToolModule(type: DrawingType): ToolModule | undefined {
    return TOOL_REGISTRY.get(type);
}

export type { ToolModule, RenderContext, ScreenAabb, HitTestResult } from './types';
