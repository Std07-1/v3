// src/chart/drawings/tools/groups.ts
// ADR-0111 §3.8 / ADR-0112 D11 (власник 19.10: «Так усе»): групи варіантів інструментів — однакові в панелі ПК і в
// капсулі телефона. Кнопка групи бере останній обраний варіант, варіант перемикається поруч; клавіші обирають варіант
// напряму. Підписи груп — тут, одним місцем (переклад — окремий зріз).

import type { ToolGroup } from './types';

/** Горизонталь · промінь рівня. */
export const LEVEL_GROUP: ToolGroup = { id: 'level', label: 'Рівень' };

/** Тренд · промінь (ADR-0084). */
export const LINE_GROUP: ToolGroup = { id: 'line', label: 'Лінія' };

/** Прямокутник · зона OB/FVG. */
export const BOX_GROUP: ToolGroup = { id: 'box', label: 'Прямокутник' };

/** Довга · коротка позиція (ADR-0112 D9, власник 15.10: «одна кнопка «Позиція» з перемикачем»). */
export const POSITION_GROUP: ToolGroup = { id: 'position', label: 'Позиція' };
