// src/chart/drawings/colorRoles.ts
// ADR-0080 (surface-2): семантичні кольори-ролі для малювань — SSOT.
//
// Колір фігури зберігається як РОЛЬ (не concrete hex), що резолвиться у
// token-хекс при рендері → theme-aware (колір адаптується під тему) + заголовок
// flyout-у «смисл кольору» тривіальний. Один список тут ітерують ОБА споживачі:
//   • DrawingStyleFlyout.svelte — палітра + заголовок (CSS `var(--cssVar)`);
//   • DrawingsRenderer.ts — canvas roleColors cache (getComputedStyle(cssVar)).
// D15.2: єдине джерело → CSS-палітра й canvas-рендер не можуть розсинхронитись.

import type { DrawingColorRole } from '../../types';

// Тип живе у types.ts (SSOT типів); re-export тут для co-located споживачів.
export type { DrawingColorRole } from '../../types';

/** Опис однієї ролі: людський «смисл», CSS-змінна токена (ADR-0066) + hex-fallback
 *  (коли getComputedStyle порожній — до applyThemeCssVars або поза DOM). */
export interface RoleSpec {
  readonly role: DrawingColorRole;
  readonly label: string;   // заголовок-«смисл» у flyout, тонований у колір ролі
  readonly cssVar: string;  // токен ADR-0066 (SSOT кольору)
  readonly fallback: string;
}

/** Порядок = порядок палітри у flyout (нейтраль → акцент → бик/ведмідь → інфо/увага). */
export const DRAWING_COLOR_ROLES: readonly RoleSpec[] = [
  { role: 'neutral', label: 'Нейтраль', cssVar: '--drawing-base-color', fallback: '#c8cdd6' },
  { role: 'accent',  label: 'Акцент',   cssVar: '--accent',             fallback: '#d4a017' },
  { role: 'bull',    label: 'Бик',      cssVar: '--bull',               fallback: '#22cc8f' },
  { role: 'bear',    label: 'Ведмідь',  cssVar: '--bear',               fallback: '#ed4554' },
  { role: 'info',    label: 'Інфо',     cssVar: '--info',               fallback: '#5487ff' },
  { role: 'warn',    label: 'Увага',    cssVar: '--warn',               fallback: '#ffb347' },
];

/** Fast lookup за роллю (заголовок, cssVar). undefined для невідомої ролі. */
export function roleSpec(role: DrawingColorRole): RoleSpec | undefined {
  return DRAWING_COLOR_ROLES.find((r) => r.role === role);
}

/** Побудувати роль→hex мапу з CSS custom properties. `lookup` = getPropertyValue
 *  (DrawingsRenderer передає з getComputedStyle(canvas)). Canvas не читає CSS-vars
 *  напряму → резолвимо тут ОДИН раз/тему й кешуємо. Fallback = RoleSpec.fallback
 *  коли змінна порожня (до applyThemeCssVars або поза DOM). */
export function buildRoleColorMap(
  lookup: (cssVar: string) => string,
): Record<DrawingColorRole, string> {
  const out = {} as Record<DrawingColorRole, string>;
  for (const spec of DRAWING_COLOR_ROLES) {
    out[spec.role] = lookup(spec.cssVar).trim() || spec.fallback;
  }
  return out;
}
