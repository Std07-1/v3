// src/chart/drawings/toolGroups.ts
// ADR-0112 D9: капсула телефона — інструменти однієї групи (позиція: довга / коротка) займають одну кнопку; тап по ній
// бере останній обраний варіант, а в режимі інструмента варіант перемикається поруч із магнітом. Чисті функції над
// реєстром (група — у ToolModule.group, одне джерело); панель ПК групи не згортає.

import type { DrawingType } from '../../types';
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

/** Варіанти групи інструмента (для перемикача); інструмент без групи — порожньо. */
export function groupVariants(tools: readonly ToolModule[], tool: ToolModule): ToolModule[] {
  const groupId = tool.group?.id;
  return groupId ? tools.filter((t) => t.group?.id === groupId) : [];
}
