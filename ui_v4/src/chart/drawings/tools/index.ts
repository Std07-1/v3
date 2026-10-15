// src/chart/drawings/tools/index.ts
// ADR-0074 T1: TOOL_REGISTRY — central Map<DrawingType, ToolModule>.
//
// SSOT for tool discovery. DrawingsRenderer queries TOOL_REGISTRY.get(d.type)
// замість inline if/else по type. Додавання нового tool у ADR-B = новий файл
// з ToolModule + entry тут, БЕЗ редагування renderer-а (open/closed).
//
// FP9 reminder: localStorage НЕ touched here — DrawingsRenderer власник
// persistence до ADR-B.

import type { Drawing, DrawingType } from '../../../types';
import { HLineTool } from './HLineTool';
import { TrendTool } from './TrendTool';
import { RectTool } from './RectTool';
import { RayTool } from './RayTool';
import { MeasureTool } from './MeasureTool';
import { FibTool } from './FibTool';
import { LongPositionTool, ShortPositionTool } from './PositionTool';
import type { AlertLevelSpec, ToolModule } from './types';

/** ADR-0111 §3.3: повнота реєстру — на рівні типів (D15.2): пропущений або зайвий DrawingType — помилка
 *  компіляції, а не тихо відсутня кнопка. Порядок ключів = порядок кнопок панелі (hline → trend → ray → rect →
 *  fib → long → short → measure). */
const TOOLS_BY_TYPE = {
    hline: HLineTool,
    trend: TrendTool,
    ray: RayTool,
    rect: RectTool,
    fib: FibTool,
    long: LongPositionTool,
    short: ShortPositionTool,
    measure: MeasureTool,
} satisfies Record<DrawingType, ToolModule>;

/** Read-only registry. Iteration order = порядок TOOLS_BY_TYPE (deterministic toolbar rendering). */
export const TOOL_REGISTRY: ReadonlyMap<DrawingType, ToolModule> = new Map(
    (Object.keys(TOOLS_BY_TYPE) as DrawingType[]).map((type) => [type, TOOLS_BY_TYPE[type]]),
);

/** Інструменти зі стилем — flyout на іконці і дефолти нових фігур (App). */
export const STYLEABLE_TOOL_TYPES: readonly DrawingType[] = [...TOOL_REGISTRY.values()]
    .filter((tool) => tool.styleable)
    .map((tool) => tool.id);

/** Клавіша в нижньому регістрі → інструмент: `hotkey` і `hotkeyAliases` кожного модуля. */
const TOOL_BY_HOTKEY: ReadonlyMap<string, DrawingType> = new Map(
    [...TOOL_REGISTRY.values()].flatMap((tool) =>
        [tool.hotkey, ...(tool.hotkeyAliases ?? [])].map((key) => [key.toLowerCase(), tool.id] as const),
    ),
);

/** Інструмент за гарячою клавішею (`KeyboardEvent.key`, регістр не важить); null — клавіша не інструмента. */
export function toolForHotkey(key: string): DrawingType | null {
    return TOOL_BY_HOTKEY.get(key.toLowerCase()) ?? null;
}

/** Type-safe lookup. Throws у dev mode для unknown type щоб виявити drift
 *  між Drawing.type union і registry keys. Production: returns undefined. */
export function getToolModule(type: DrawingType): ToolModule | undefined {
    return TOOL_REGISTRY.get(type);
}

/** ADR-0114: типи фігур, що можуть бути лінією-сповіщенням (перемикач «Сповіщати» — лише для них). */
export const ALERTABLE_TOOL_TYPES: readonly DrawingType[] = [...TOOL_REGISTRY.values()]
    .filter((tool) => tool.alertLevel !== undefined)
    .map((tool) => tool.id);

/** ADR-0114: рівень сповіщення фігури з її модуля; null — тип не може сповіщати. */
export function alertLevelOf(d: Drawing): AlertLevelSpec | null {
    return getToolModule(d.type)?.alertLevel?.(d) ?? null;
}

export type {
    ToolModule,
    RenderContext,
    ScreenAabb,
    HitTestResult,
    PlacementEnv,
    ToolGroup,
    AlertLevelSpec,
} from './types';
