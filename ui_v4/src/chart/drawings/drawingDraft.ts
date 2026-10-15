// src/chart/drawings/drawingDraft.ts
// ADR-0111 §3.3 (крок 3b): створення фігури — за `pointsRequired` і `ephemeral` інструмента з реєстру, без
// розгалужень за типом (`=== 'hline'`, `=== 'measure'`) у DrawingsRenderer. Чисті функції (без I/O і LWC): renderer
// віддає точку в часі/ціні (уже з магнітом) і id, модуль каже, що робити, — показати фігуру під курсором, почати
// чернетку, закомітити, заморозити чи відкинути. Новий інструмент із тим самим числом точок не чіпає ні renderer,
// ні цей файл; 3 точки (Long/Short, §3.4) — розширення тут.

import type { Drawing, DrawingPoint } from '../../types';
import type { PlacementEnv, ToolModule } from './tools';

/** Глибока копія фігури (точки й meta): undo-стек, чернетка і стор не ділять посилань. */
export function cloneDrawing(d: Drawing): Drawing {
  return {
    id: d.id,
    type: d.type,
    points: d.points.map((p) => ({ t_ms: p.t_ms, price: p.price })),
    meta: d.meta ? { ...d.meta } : undefined,
  };
}

/** Однокрокову фігуру (pointsRequired = 1, hline) видно під курсором ще до кліку — клік лише фіксує її. */
export function previewsOnHover(tool: ToolModule): boolean {
  return tool.pointsRequired === 1;
}

/** Нова чернетка: усі точки — у місці курсора (позиція — якорі від нього, `tool.place`); ефемерна (measure) — без
 *  стилю (ADR-0084 D2). */
export function newDraft(tool: ToolModule, id: string, at: DrawingPoint, meta: Drawing['meta'], env?: PlacementEnv): Drawing {
  const points = tool.place && env
    ? tool.place(at, env)
    : Array.from({ length: tool.pointsRequired }, () => ({ t_ms: at.t_ms, price: at.price }));
  return { id, type: tool.id, points, meta: tool.ephemeral ? undefined : meta };
}

/** Що робить перший клік інструментом. */
export type FirstClick =
  | { readonly kind: 'commit'; readonly drawing: Drawing } // однокрокова фігура готова
  | { readonly kind: 'draft'; readonly draft: Drawing }; // двоточкова: друга точка далі йде за курсором

export function firstClick(
  tool: ToolModule,
  id: string,
  at: DrawingPoint,
  meta: Drawing['meta'],
  env?: PlacementEnv,
): FirstClick {
  const drawing = newDraft(tool, id, at, meta, env);
  return tool.pointsRequired === 1 ? { kind: 'commit', drawing } : { kind: 'draft', draft: drawing };
}

/** Точка чернетки, що йде за курсором, — остання. */
export function tipIndex(draft: Drawing): number {
  return draft.points.length - 1;
}

/** Якір, яким чернетку веде курсор: позиція (`tool.place`) — вхід (уся фігура від нього), інші — кінчик. */
export function cursorIndex(tool: ToolModule, draft: Drawing): number {
  return tool.place ? 0 : tipIndex(draft);
}

/** Чернетка після руху курсора в `at`: позиція — переставлена вся від точки курсора (стоп і ціль за масштабом),
 *  інші — лише кінчик. Нова фігура, вихідна не змінюється. */
export function followCursor(tool: ToolModule, draft: Drawing, at: DrawingPoint, env?: PlacementEnv): Drawing {
  if (tool.place && env) return { ...draft, points: tool.place(at, env) };
  const points = draft.points.map((p) => ({ t_ms: p.t_ms, price: p.price }));
  points[tipIndex(draft)] = { t_ms: at.t_ms, price: at.price };
  return { ...draft, points };
}

/** Що робить клік, коли чернетка вже є. */
export type DraftCompletion =
  | { readonly kind: 'commit'; readonly drawing: Drawing } // фігура готова — копією в undo-стек
  | { readonly kind: 'freeze' } // ефемерна (measure): результат лишається на екрані, не комітиться
  | { readonly kind: 'discard' }; // усі точки в одному місці — нульова фігура

export function completeDraft(tool: ToolModule, draft: Drawing): DraftCompletion {
  if (tool.ephemeral) return { kind: 'freeze' };
  const [first, ...rest] = draft.points;
  const degenerate = rest.length > 0 && rest.every((p) => p.t_ms === first.t_ms && p.price === first.price);
  return degenerate ? { kind: 'discard' } : { kind: 'commit', drawing: cloneDrawing(draft) };
}
