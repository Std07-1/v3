// src/chart/canvasColor.ts
// Колір токена з прозорістю для canvas: canvas не читає CSS var і не вміє color-mix, тож hex-токен (theme-aware,
// резолвиться renderer-ом) перетворюється на rgba. Одне джерело для шарів малювань і Арчі (раніше — дві копії).

/** rgba з hex-токена `#rrggbb` з альфою `alpha`. Не hex (порожній токен, іменований колір) — `fallbackRgb` з тією
 *  ж альфою: кожен шар має свій нейтральний запасний колір. */
export function withAlpha(hex: string, alpha: number, fallbackRgb: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return `rgba(${fallbackRgb},${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}
