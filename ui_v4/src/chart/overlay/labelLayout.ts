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

/** Ширина свічки рахується діленням ширини графіка на логічний діапазон — типова ширина виходить із похибкою float. */
export const BAR_SPACING_TOLERANCE_PX = 0.05;

/**
 * LB7: стиснутий вигляд — свічка вужча за типову ширину графіка (`DEFAULT_BAR_SPACING_PX`) більше ніж на допуск.
 * `barSpacingPx` ≤ 0 — діапазону ще нема, вигляд не стиснутий.
 */
export function isCompactView(barSpacingPx: number, defaultBarSpacingPx: number): boolean {
    return barSpacingPx > 0 && barSpacingPx < defaultBarSpacingPx - BAR_SPACING_TOLERANCE_PX;
}

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

/** Підпис FVG, що претендує на текст у стиснутому вигляді. */
export interface ZoneLabelCandidate {
    id: string;
    /** TF зони в секундах: старший важливіший */
    tfS: number;
    /** Відстань від ціни до найближчого краю зони: ближча важливіша */
    distance: number;
    box: LabelBox;
}

/**
 * ADR-0107 S2 (LB8–LB10): хто з FVG лишається з текстом у стиснутому вигляді.
 *
 * `taken` — тексти, що вже стоять і не поступаються (структура, OB). Далі FVG від старшого TF до молодшого, на рівний
 * TF — ближча до ціни, далі id: текст отримує той, чия плашка не налазить на вже поставлене. Решта — без тексту
 * (зона лишається, підпис у підказці).
 */
export function zoneLabelsWithText(
    candidates: ReadonlyArray<ZoneLabelCandidate>,
    taken: ReadonlyArray<LabelBox>,
    gapPx: number = LABEL_GAP_PX,
): Set<string> {
    const byImportance = [...candidates].sort(
        (a, b) => b.tfS - a.tfS || a.distance - b.distance || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const placed: LabelBox[] = [...taken];
    const withText = new Set<string>();
    for (const candidate of byImportance) {
        if (placed.some((box) => boxesOverlap(candidate.box, box, gapPx))) continue;
        placed.push(candidate.box);
        withText.add(candidate.id);
    }
    return withText;
}
