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

/** Світлий колір (перцептивна яскравість понад половину) — для контрастного тла під текстом; не hex — null. */
export function isLightColor(hex: string): boolean | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  const luma = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return luma > 0.5;
}
