/**
 * ADR-0107: розкладка підписів SMC у скупченні — текст лише там, де є місце.
 *
 * Чистий модуль (без canvas): отримує прямокутники підписів у CSS px, повертає, хто з них малюється текстом.
 * Решта стискається до мітки з тією ж підказкою — подія не зникає мовчки.
 */

/** Прямокутник підпису на канві (CSS px), як його малює рендер — разом із плашкою. */
export interface LabelBox {
    x: number;
    y: number;
    w: number;
    h: number;
}

/** LB1: мінімальний просвіт між плашками; менше — це вже скупчення. */
export const LABEL_GAP_PX = 2;

/** Чи налазять дві плашки одна на одну з урахуванням просвіту `gapPx`. */
export function boxesOverlap(a: LabelBox, b: LabelBox, gapPx: number = LABEL_GAP_PX): boolean {
    return a.x < b.x + b.w + gapPx && b.x < a.x + a.w + gapPx && a.y < b.y + b.h + gapPx && b.y < a.y + a.h + gapPx;
}

/** Підпис події структури (CHoCH/BOS), що претендує на текст. */
export interface StructureLabelCandidate {
    id: string;
    timeMs: number;
    isChoch: boolean;
    box: LabelBox;
}

/**
 * ADR-0107 S1 (LB2–LB3): хто з подій структури малюється текстом.
 *
 * Від найновішої до найстаршої (на рівний час — CHoCH перед BOS, далі id): текст отримує подія, чия плашка не налазить
 * на вже поставлений текст; решта — штрих. Розтягнутий графік (плашки не перетинаються) — текст у всіх.
 */
export function structureLabelsWithText(
    candidates: ReadonlyArray<StructureLabelCandidate>,
    gapPx: number = LABEL_GAP_PX,
): Set<string> {
    const newestFirst = [...candidates].sort(
        (a, b) => b.timeMs - a.timeMs || Number(b.isChoch) - Number(a.isChoch) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const placed: LabelBox[] = [];
    const withText = new Set<string>();
    for (const candidate of newestFirst) {
        if (placed.some((box) => boxesOverlap(candidate.box, box, gapPx))) continue;
        placed.push(candidate.box);
        withText.add(candidate.id);
    }
    return withText;
}
