// src/layout/toolIcons.ts
// Іконки дій панелей малювання (ПК — DrawingToolbar, телефон — MobileDrawBar, ADR-0112), одне джерело.
// Внутрішня розмітка SVG 24×24, stroke=currentColor; Lucide v0.460 (MIT): magnet / undo-2 / redo-2 / trash-2 / x /
// pencil / bell.
// Іконки фігур — у їхніх модулях (ToolModule.icon, ADR-0111 §3.3).

/** U-подібний магніт — snap до OHLC. */
export const ICON_MAGNET =
  '<path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/>';
export const ICON_UNDO =
  '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>';
export const ICON_REDO =
  '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/>';
export const ICON_TRASH =
  '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>';
export const ICON_CLOSE = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';
/** Олівець біля ☰ — вхід у малювання на телефоні (ADR-0112 D2). */
export const ICON_PENCIL =
  '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>';
/** Дзвіночок — лінія-сповіщення (ADR-0114): на лінії, у меню стилю, капсулі й спливному повідомленні. */
export const ICON_BELL =
  '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>';
