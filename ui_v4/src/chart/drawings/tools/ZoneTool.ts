// src/chart/drawings/tools/ZoneTool.ts
// ADR-0111 §3.7 (власник 19.10: «Так усе»): зона OB/FVG — прямокутник, що тягнеться вправо до першої свічки, яка
// тінню зайшла в зону (або до шкали, якщо не заходила), з лінією 50% (CE для FVG, mean threshold для OB) — на ціні,
// посередині між межами, які поставив трейдер. Domain: 2 якорі, як у прямокутника (діагональні кути). Дотик —
// свічки поточного TF після правого якоря (RenderContext.firstTouchMs, zoneTouch.ts); це геометрія над фігурою
// користувача, не серверна зона (X28). Колір — роль, яку обрав трейдер (бик / ведмідь), автоматично не вгадується.

import type { Drawing } from '../../../types';
import { withAlpha } from '../../canvasColor';
import { distToRectEdge } from '../../interaction/geometry';
import { dashPattern } from '../lineStyles';
import { BOX_GROUP } from './groups';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

const DRAFT_FILL = 'rgba(61, 154, 255, 0.10)';
/** Заливка самої зони — колір фігури (прямокутник лишається нейтральним, `rectFill`). */
const ZONE_FILL_ALPHA = 0.12;
/** Продовження після правого якоря — тихіше за саму зону: «зона ще жива». */
const EXTENSION_FILL_ALPHA = 0.06;
const MIDLINE_ALPHA = 0.7;
/** Запасний колір, якщо колір фігури — не hex (той самий нейтральний, що --drawing-base-color). */
const FALLBACK_RGB = '200,205,214';

/** Ціна лінії 50% між межами зони. */
export function zoneMidPrice(a: number, b: number): number {
    return (a + b) / 2;
}

type Box = { x1: number; x2: number; yTop: number; yBottom: number };

function boxOf(d: Drawing, toX: (t_ms: number) => number | null, toY: (price: number) => number | null): Box | null {
    if (d.points.length < 2) return null;
    const xa = toX(d.points[0].t_ms);
    const xb = toX(d.points[1].t_ms);
    const ya = toY(d.points[0].price);
    const yb = toY(d.points[1].price);
    if (xa === null || xb === null || ya === null || yb === null) return null;
    return { x1: Math.min(xa, xb), x2: Math.max(xa, xb), yTop: Math.min(ya, yb), yBottom: Math.max(ya, yb) };
}

/** Правий край продовження: x першого дотику після правого якоря або шкала. */
function extensionEnd(d: Drawing, rc: RenderContext, box: Box): number {
    const rightMs = Math.max(d.points[0].t_ms, d.points[1].t_ms);
    const lo = Math.min(d.points[0].price, d.points[1].price);
    const hi = Math.max(d.points[0].price, d.points[1].price);
    const touchMs = rc.firstTouchMs?.(rightMs, lo, hi) ?? null;
    const x = touchMs === null ? null : rc.toX(touchMs);
    return Math.max(box.x2, Math.min(x ?? rc.cssW, rc.cssW));
}

export const ZoneTool: ToolModule = {
    id: 'zone',
    pointsRequired: 2,
    label: 'Зона OB/FVG',
    icon: '<rect x="3" y="6" width="11" height="12" rx="1"/><path d="M3 12h11" stroke-dasharray="2 2"/><path d="M14 9h4"/><path d="M14 15h4"/><path d="m18 9 3 3-3 3"/>',
    hotkey: 'z',
    styleable: true,
    group: BOX_GROUP,
    interiorIsBody: true,

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        const b = boxOf(d, rc.toX, rc.toY);
        if (!b) return null;
        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected } = rc;
        const highlight = !isDraft && (isHovered || isSelected);
        const color = isDraft ? accentColor : baseColor;
        const end = isDraft ? b.x2 : extensionEnd(d, rc, b);
        const h = b.yBottom - b.yTop;

        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        ctx.fillStyle = isDraft ? DRAFT_FILL : withAlpha(color, ZONE_FILL_ALPHA, FALLBACK_RGB);
        ctx.fillRect(b.x1, b.yTop, b.x2 - b.x1, h);
        if (end > b.x2) {
            ctx.fillStyle = withAlpha(color, EXTENSION_FILL_ALPHA, FALLBACK_RGB);
            ctx.fillRect(b.x2, b.yTop, end - b.x2, h);
        }

        // межі зони — від лівого якоря до кінця продовження; 50% — пунктиром
        ctx.strokeStyle = color;
        ctx.lineWidth = (d.meta?.lineWidth ?? 1) + (highlight ? 1 : 0);
        ctx.shadowColor = highlight ? baseColor : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;
        ctx.setLineDash(isDraft ? [4, 4] : dashPattern(d.meta?.lineStyle, d.meta?.lineWidth ?? 1));
        ctx.beginPath();
        ctx.moveTo(b.x1, b.yTop);
        ctx.lineTo(end, b.yTop);
        ctx.moveTo(b.x1, b.yBottom);
        ctx.lineTo(end, b.yBottom);
        ctx.moveTo(b.x1, b.yTop);
        ctx.lineTo(b.x1, b.yBottom);
        ctx.stroke();
        ctx.shadowBlur = 0;

        const yMid = rc.toY(zoneMidPrice(d.points[0].price, d.points[1].price));
        if (yMid !== null) {
            ctx.globalAlpha = MIDLINE_ALPHA;
            ctx.lineWidth = 1;
            ctx.setLineDash(dashPattern('dashed', 1));
            ctx.beginPath();
            ctx.moveTo(b.x1, yMid);
            ctx.lineTo(end, yMid);
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        ctx.setLineDash([]);
        return { minX: b.x1, maxX: end, minY: b.yTop, maxY: b.yBottom };
    },

    /** × — у центрі самої зони (між якорями), не продовження. */
    deleteAnchor(d, toX, toY) {
        const b = boxOf(d, toX, toY);
        return b ? { x: (b.x1 + b.x2) / 2, y: (b.yTop + b.yBottom) / 2 } : null;
    },

    /** Грань зони або межа продовження. Довжину продовження (до дотику чи шкали) хіт-тест не знає — її обмежує рамка
     *  кадру з `render` (drawingHitTest відсікає вказівник поза нею). */
    hitTest(d, cursorX, cursorY, tolerance, toX, toY): HitTestResult {
        const b = boxOf(d, toX, toY);
        if (!b) return { hit: false };
        const edge = distToRectEdge(cursorX, cursorY, b.x1, b.yTop, b.x2, b.yBottom, tolerance);
        if (edge !== Infinity) return { hit: true, distance: edge };
        if (cursorX <= b.x2) return { hit: false };
        const distance = Math.min(Math.abs(cursorY - b.yTop), Math.abs(cursorY - b.yBottom));
        return distance <= tolerance ? { hit: true, distance } : { hit: false };
    },
};
