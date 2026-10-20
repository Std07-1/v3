// ADR-0043 D-15: канонічна TF label map — єдине джерело правди.
// Використовується: BiasBanner.svelte, ChartHud.svelte, (майбутнє: OverlayRenderer.ts)

export const BIAS_TF_LABELS: Record<string, string> = {
    "86400": "D1",
    "14400": "H4",
    "3600":  "H1",
    "900":   "M15",
};

export const BIAS_TF_ORDER: string[] = ["86400", "14400", "3600", "900"];

// Мітка TF графіка → секунди: дзеркало `ws_server._TF_CANONICAL_LABELS` (сервер шле мітки в config-кадрі й кадрах).
// W1/MN (ADR-0115) — проєкція D1 на сервері; секунди — ідентифікатори (MN = 30 діб), не тривалість бару:
// межі тижня й місяця, як і таймер до закриття, приходять з бекенда (`frame.bar_close_ms`).
export const TF_LABEL_TO_S: Readonly<Record<string, number>> = {
    M1: 60, M3: 180, M5: 300, M15: 900,
    M30: 1800, H1: 3600, H4: 14400, D1: 86400,
    W1: 604800, MN: 2592000,
};

/** Календарні TF (ADR-0115): межі бару — лише з бекенда, UI їх не рахує сіткою tf. */
export const CALENDAR_TF_LABELS: ReadonlySet<string> = new Set(['W1', 'MN']);

/** Секунди (рядком, як у `?tf=3600`) → мітка TF. */
export const TF_S_TO_LABEL: Readonly<Record<string, string>> = Object.fromEntries(
    Object.entries(TF_LABEL_TO_S).map(([label, s]) => [String(s), label]),
);
