// ADR-0043 D-15: канонічна TF label map — єдине джерело правди.
// Використовується: BiasBanner.svelte, ChartHud.svelte, (майбутнє: OverlayRenderer.ts)

export const BIAS_TF_LABELS: Record<string, string> = {
    "86400": "D1",
    "14400": "H4",
    "3600":  "H1",
    "900":   "M15",
};

export const BIAS_TF_ORDER: string[] = ["86400", "14400", "3600", "900"];
