// src/chart/drawings/tools/HRayTool.ts
// ADR-0111 §3.7 (власник 19.10: «Так усе»): промінь рівня — горизонталь від точки (свінг, ліквідність) праворуч до
// шкали, а не на всю ширину: ліворуч від свінгу рівень нічого не означає, а графік лишається чистішим. Не те саме, що
// промінь ADR-0084 (дві точки, будь-який кут). Domain: 1 якір (час — початок, ціна — рівень).

import type { Drawing } from '../../../types';
import { msg } from '../../../lib/i18n/i18n.svelte';
import { dashPattern } from '../lineStyles';
import { LEVEL_GROUP } from './groups';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Крапка на початку променя — видно, звідки рівень. */
const ORIGIN_DOT_PX = 2;

function anchorOf(
    d: Drawing,
    toX: (t_ms: number) => number | null,
    toY: (price: number) => number | null,
): { x: number; y: number } | null {
    const x = toX(d.points[0].t_ms);
    const y = toY(d.points[0].price);
    return x === null || y === null ? null : { x, y };
}

export const HRayTool: ToolModule = {
    id: 'hray',
    pointsRequired: 1,
    label: msg('Промінь рівня'),
    icon: '<circle cx="5" cy="12" r="1.6"/><path d="M7.5 12H21"/>',
    hotkey: 'k',
    styleable: true,
    group: LEVEL_GROUP,

    // ADR-0114 §4.1: лінія-сповіщення від свого початку — сервер рахує перетин з якоря (у майбутньому) або з моменту
    // взведення (якір у минулому: перетин у минулому не рахується)
    alertLevel: (d: Drawing) => ({ level: d.points[0].price, anchorMs: d.points[0].t_ms }),

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        const a = anchorOf(d, rc.toX, rc.toY);
        if (!a) return null;
        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected, cssW } = rc;
        const highlight = !isDraft && (isHovered || isSelected);
        const color = isDraft ? accentColor : baseColor;
        const end = Math.max(a.x, cssW);
        ctx.strokeStyle = color;
        ctx.lineWidth = (d.meta?.lineWidth ?? 1) + (highlight ? 1 : 0);
        ctx.setLineDash(isDraft ? [4, 4] : dashPattern(d.meta?.lineStyle, d.meta?.lineWidth ?? 1));
        ctx.lineCap = d.meta?.lineStyle === 'dotted' ? 'round' : 'butt';
        ctx.shadowColor = highlight ? baseColor : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(end, a.y);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(a.x, a.y, ORIGIN_DOT_PX, 0, Math.PI * 2);
        ctx.fill();
        return { minX: a.x, maxX: end, minY: a.y, maxY: a.y };
    },

    hitTest(d, cursorX, cursorY, tolerance, toX, toY): HitTestResult {
        const a = anchorOf(d, toX, toY);
        if (!a || cursorX < a.x - tolerance) return { hit: false };
        const distance = cursorX >= a.x ? Math.abs(cursorY - a.y) : Math.hypot(cursorX - a.x, cursorY - a.y);
        return distance <= tolerance ? { hit: true, distance } : { hit: false };
    },
};
