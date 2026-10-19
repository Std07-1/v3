// src/chart/drawings/toolGroups.ts
// ADR-0112 D9/D11: інструменти однієї групи (рівень, лінія, прямокутник, позиція — tools/groups.ts) займають одну
// кнопку — у капсулі телефона і в панелі ПК однаково; кнопка бере останній обраний варіант, варіант перемикається
// поруч. Чисті функції над реєстром (група — у ToolModule.group, одне джерело).

import type { ActiveTool, DrawingType } from '../../types';
import type { ToolModule } from './tools';

/** Кнопка капсули: окремий інструмент або група (підпис групи, іконка й дія — обраного варіанта). */
export interface CapsuleEntry {
  readonly key: string;
  readonly label: string;
  readonly tool: ToolModule;
}

/** Кнопки капсули в порядку реєстру: група — на місці першого свого варіанта; `lastInGroup` — останній обраний варіант
 *  кожної групи (нема або не з цієї групи — перший). */
export function capsuleEntries(
  tools: readonly ToolModule[],
  lastInGroup: Readonly<Record<string, DrawingType>>,
): CapsuleEntry[] {
  const entries: CapsuleEntry[] = [];
  const seenGroups = new Set<string>();
  for (const tool of tools) {
    const group = tool.group;
    if (!group) {
      entries.push({ key: tool.id, label: tool.label, tool });
      continue;
    }
    if (seenGroups.has(group.id)) continue;
    seenGroups.add(group.id);
    const chosen = tools.find((t) => t.id === lastInGroup[group.id] && t.group?.id === group.id) ?? tool;
    entries.push({ key: group.id, label: group.label, tool: chosen });
  }
  return entries;
}

/** Кнопка «тримає» обраний інструмент: будь-який варіант її групи або сам інструмент без групи. */
export function entryHolds(entry: CapsuleEntry, active: ActiveTool, tools: readonly ToolModule[]): boolean {
  if (active === null || active === 'eraser') return false;
  if (entry.tool.id === active) return true;
  const group = entry.tool.group;
  return group !== undefined && tools.find((t) => t.id === active)?.group?.id === group.id;
}

/** Обраний інструмент (кнопкою, перемикачем чи клавішею) — останній варіант своєї групи; без змін — той самий об'єкт. */
export function rememberVariant(
  lastInGroup: Readonly<Record<string, DrawingType>>,
  active: ActiveTool,
  tools: readonly ToolModule[],
): Readonly<Record<string, DrawingType>> {
  if (active === null || active === 'eraser') return lastInGroup;
  const group = tools.find((t) => t.id === active)?.group;
  if (!group || lastInGroup[group.id] === active) return lastInGroup;
  return { ...lastInGroup, [group.id]: active };
}

/** Варіанти групи інструмента (для перемикача); інструмент без групи — порожньо. */
export function groupVariants(tools: readonly ToolModule[], tool: ToolModule): ToolModule[] {
  const groupId = tool.group?.id;
  return groupId ? tools.filter((t) => t.group?.id === groupId) : [];
}
