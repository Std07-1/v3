// src/chart/drawings/tools/FibTool.ts
// ADR-0111 §3.4 (1): Fibonacci retracement — рівні відкату імпульсу між двома якорями.
//
// Domain: 2 точки (клік-клік, як trend). points[0] — початок імпульсу (рівень 1), points[1] — кінець (рівень 0);
// ціна рівня = кінець + (початок − кінець) × рівень, тож OTE 0.62–0.79 — глибокий відкат до початку імпульсу (ICT).
// X28: лише числа рівнів і ціни, жодних «premium/discount» (серверний P/D — окремий SSOT).
// Вигляд — пропозиція, з якою власник сказав «го, спробуємо» (13.10); підлаштовуємо після живого перегляду:
// лінії рівнів від лівого якоря вправо до цінової шкали (відкат іде ПІСЛЯ кінця імпульсу); 0.5 — пунктиром
// (рівновага); ледь помітна смуга OTE; тонка діагональ між якорями — напрямок; підпис «0.705 · 4161.30» над лівим
// кінцем кожної лінії. Колір/товщина/стиль — з meta фігури (той самий flyout, що в інших інструментів).

import type { Drawing } from '../../../types';
import { msg } from '../../../lib/i18n/i18n.svelte';
import { displayPriceDigits } from '../../../lib/priceDigits';
import { withAlpha } from '../../canvasColor';
import { distToSegment } from '../../interaction/geometry';
import { dashPattern } from '../lineStyles';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Рівні відкату (ADR-0111 §3.4): 0 — кінець імпульсу, 1 — початок; рівновага 0.5; OTE 0.62 / 0.705 / 0.79. */
export const FIB_LEVELS: readonly number[] = [0, 0.5, 0.62, 0.705, 0.79, 1];
const EQUILIBRIUM_LEVEL = 0.5;
/** Смуга OTE (optimal trade entry): від 0.62 до 0.79. */
const OTE_FROM_LEVEL = 0.62;
const OTE_TO_LEVEL = 0.79;
/** Альфа смуги OTE — ледь помітна, щоб не сперечатися із зонами SMC. */
const OTE_FILL_ALPHA = 0.06;
/** Діагональ між якорями — підпорядкована рівням. */
const DIAGONAL_ALPHA = 0.55;
const LABEL_FONT = '10px ui-monospace, SFMono-Regular, Consolas, monospace';
const LABEL_PAD_X = 4;
const LABEL_LIFT_Y = 3;
/** Запасний колір смуги, якщо колір фігури — не hex (той самий нейтральний, що в tokens.css --drawing-base-color). */
const FALLBACK_RGB = '200,205,214';

/** Ціна рівня: кінець імпульсу + (початок − кінець) × рівень. */
export function fibLevelPrice(startPrice: number, endPrice: number, level: number): number {
    return endPrice + (startPrice - endPrice) * level;
}

/** Підпис рівня: «0.705 · 4161.30» — число рівня як є і ціна знаками символу. */
export function fibLevelLabel(level: number, price: number, priceDigits: number | null | undefined): string {
    return `${level} · ${price.toFixed(displayPriceDigits(priceDigits ?? null, price))}`;
}

type Anchors = { x1: number; y1: number; x2: number; y2: number };

function anchorsOf(
    d: Drawing,
    toX: (t_ms: number) => number | null,
    toY: (price: number) => number | null,
): Anchors | null {
    if (d.points.length < 2) return null;
    const x1 = toX(d.points[0].t_ms);
    const y1 = toY(d.points[0].price);
    const x2 = toX(d.points[1].t_ms);
    const y2 = toY(d.points[1].price);
    if (x1 === null || y1 === null || x2 === null || y2 === null) return null;
    return { x1, y1, x2, y2 };
}

export const FibTool: ToolModule = {
    id: 'fib',
    pointsRequired: 2,
    label: msg('Фібоначчі'),
    icon: '<path d="M4 5h16"/><path d="M4 10h16"/><path d="M4 13.5h16"/><path d="M4 19h16"/><path d="M5 18 19 6" stroke-opacity=".45"/>',
    hotkey: 'b',
    styleable: true,

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        const a = anchorsOf(d, rc.toX, rc.toY);
        if (!a) return null;
        const [start, end] = d.points;
        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected, cssW } = rc;
        const color = isDraft ? accentColor : baseColor;
        // ADR-0078: стан (hover/select) — glow + товщина, колір фігури лишається.
        const highlight = !isDraft && (isHovered || isSelected);
        const lineWidth = d.meta?.lineWidth ?? 1;
        const left = Math.min(a.x1, a.x2);
        const right = Math.max(cssW, left);
        const levelY = (level: number) => rc.toY(fibLevelPrice(start.price, end.price, level));

        // 1) смуга OTE під лініями
        const yOteFrom = levelY(OTE_FROM_LEVEL);
        const yOteTo = levelY(OTE_TO_LEVEL);
        if (yOteFrom !== null && yOteTo !== null) {
            ctx.setLineDash([]);
            ctx.shadowBlur = 0;
            ctx.fillStyle = withAlpha(color, OTE_FILL_ALPHA, FALLBACK_RGB);
            ctx.fillRect(left, Math.min(yOteFrom, yOteTo), right - left, Math.abs(yOteTo - yOteFrom));
        }

        // 2) лінії рівнів: від лівого якоря до шкали; 0.5 — пунктиром, коли фігура суцільна
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth + (highlight ? 1 : 0);
        ctx.lineCap = d.meta?.lineStyle === 'dotted' ? 'round' : 'butt';
        ctx.shadowColor = highlight ? color : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;
        for (const level of FIB_LEVELS) {
            const y = levelY(level);
            if (y === null) continue;
            const style = level === EQUILIBRIUM_LEVEL && !d.meta?.lineStyle ? 'dashed' : d.meta?.lineStyle;
            ctx.setLineDash(isDraft ? [4, 4] : dashPattern(style, lineWidth));
            ctx.beginPath();
            ctx.moveTo(left, y);
            ctx.lineTo(right, y);
            ctx.stroke();
        }
        ctx.shadowBlur = 0;

        // 3) діагональ між якорями — напрямок імпульсу
        ctx.globalAlpha = DIAGONAL_ALPHA;
        ctx.lineWidth = 1;
        ctx.lineCap = 'round';
        ctx.setLineDash(dashPattern('dotted', 1));
        ctx.beginPath();
        ctx.moveTo(a.x1, a.y1);
        ctx.lineTo(a.x2, a.y2);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.setLineDash([]);

        // 4) підписи над лівим кінцем ліній, з ореолом для читабельності на будь-якому тлі
        ctx.font = LABEL_FONT;
        ctx.fillStyle = color;
        ctx.shadowColor = 'rgba(0, 0, 0, 0.75)';
        ctx.shadowBlur = 4;
        for (const level of FIB_LEVELS) {
            const price = fibLevelPrice(start.price, end.price, level);
            const y = rc.toY(price);
            if (y === null) continue;
            ctx.fillText(fibLevelLabel(level, price, rc.priceDigits), left + LABEL_PAD_X, y - LABEL_LIFT_Y);
        }
        ctx.shadowBlur = 0;

        return { minX: left, maxX: right, minY: Math.min(a.y1, a.y2), maxY: Math.max(a.y1, a.y2) };
    },

    /** × — посередині діагоналі: центр рамки (лінії тягнуться до шкали) не лежить на фігурі. */
    deleteAnchor(d, toX, toY) {
        const a = anchorsOf(d, toX, toY);
        return a ? { x: (a.x1 + a.x2) / 2, y: (a.y1 + a.y2) / 2 } : null;
    },

    hitTest(
        d: Drawing,
        cursorX: number,
        cursorY: number,
        tolerance: number,
        toX: (t_ms: number) => number | null,
        toY: (price: number) => number | null,
    ): HitTestResult {
        const a = anchorsOf(d, toX, toY);
        if (!a) return { hit: false };
        const [start, end] = d.points;
        const left = Math.min(a.x1, a.x2);
        // Лінії рівнів тягнуться вправо до шкали — праворуч від лівого якоря відстань лише по вертикалі.
        let best = distToSegment(cursorX, cursorY, a.x1, a.y1, a.x2, a.y2);
        for (const level of FIB_LEVELS) {
            const y = toY(fibLevelPrice(start.price, end.price, level));
            if (y === null) continue;
            const dist = cursorX >= left ? Math.abs(cursorY - y) : Math.hypot(cursorX - left, cursorY - y);
            if (dist < best) best = dist;
        }
        return best <= tolerance ? { hit: true, distance: best } : { hit: false };
    },
};
