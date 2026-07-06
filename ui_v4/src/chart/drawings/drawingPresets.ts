// src/chart/drawings/drawingPresets.ts
// ADR-0080 (surface-2, наміри-перші): іменовані пресети інструментів малювання.
//
// Кожен пресет = НАМІР трейдера (Теза/Рівень/Нотатка/Увага) → готова трійця
// колір-роль + товщина + стиль. Клік пресета застосовує всі три поля разом
// (дефолт нових фігур АБО live на вибраній фігурі). SSOT — і flyout (рендер
// chips + прев'ю), і матч активного пресета читають цей список (D15.2).
//
// ⚠ МАПІНГ (яка трійця за наміром) — first-pass, узгоджується live з owner.

import type { DrawingColorRole, DrawingLineStyle } from '../../types';

export interface DrawingPreset {
  readonly id: string;
  readonly label: string;      // назва наміру у chip
  readonly colorRole: DrawingColorRole;
  readonly lineWidth: number;
  readonly lineStyle: DrawingLineStyle;
}

/** Порядок = порядок chips у flyout. Мапінг first-pass (owner-tunable). */
export const DRAWING_PRESETS: readonly DrawingPreset[] = [
  { id: 'thesis', label: 'Теза',    colorRole: 'accent',  lineWidth: 2, lineStyle: 'solid' },
  { id: 'level',  label: 'Рівень',  colorRole: 'neutral', lineWidth: 1, lineStyle: 'dashed' },
  { id: 'note',   label: 'Нотатка', colorRole: 'info',    lineWidth: 1, lineStyle: 'dotted' },
  { id: 'alert',  label: 'Увага',   colorRole: 'bear',    lineWidth: 2, lineStyle: 'solid' },
];

/** id пресета, чия трійця точно збігається з поточним станом (colorRole+width+
 *  style), або null (кастом). Дає flyout підсвітити активний намір. */
export function matchPreset(
  colorRole: DrawingColorRole,
  lineWidth: number,
  lineStyle: DrawingLineStyle,
): string | null {
  const p = DRAWING_PRESETS.find(
    (x) => x.colorRole === colorRole && x.lineWidth === lineWidth && x.lineStyle === lineStyle,
  );
  return p ? p.id : null;
}
