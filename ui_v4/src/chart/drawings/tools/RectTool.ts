// src/chart/drawings/tools/RectTool.ts
// ADR-0074 T1: Rectangle — axis-aligned box defined by two diagonal corners.
//
// Domain: 2-point drawing (pointsRequired: 2). points[0] і points[1] = два
// діагональні corners (будь-який order). Render = filled + stroked rect;
// hit-test = distToRectEdge (повертає 0 на ребрі inside tolerance, Infinity
// in body OR outside expanded rect).

import type { Drawing } from '../../../types';
import { distToRectEdge } from '../../interaction/geometry';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Draft fill — використовуємо semi-transparent blue для контрасту з final
 *  rectFill (theme-resolved). Static const бо темо-незалежний (draft state). */
const DRAFT_FILL = 'rgba(61, 154, 255, 0.10)';

export const RectTool: ToolModule = {
    id: 'rect',
    pointsRequired: 2,
    label: 'Прямокутник',
    icon: 'square', // Lucide icon name (T3 wires it)
    hotkey: 'r',

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        if (d.points.length < 2) return null;
        const x1 = rc.toX(d.points[0].t_ms);
        const y1 = rc.toY(d.points[0].price);
        const x2 = rc.toX(d.points[1].t_ms);
        const y2 = rc.toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;

        const minX = Math.min(x1, x2);
        const minY = Math.min(y1, y2);
        const w = Math.abs(x2 - x1);
        const h = Math.abs(y2 - y1);

        const { ctx, baseColor, accentColor, rectFill, isDraft, isHovered, isSelected } = rc;
        ctx.strokeStyle = isDraft || isHovered || isSelected ? accentColor : baseColor;
        ctx.fillStyle = isDraft ? DRAFT_FILL : rectFill;
        ctx.lineWidth = d.meta?.lineWidth ?? 1;
        ctx.setLineDash(isDraft ? [4, 4] : []);

        ctx.fillRect(minX, minY, w, h);
        ctx.strokeRect(minX, minY, w, h);

        return { minX, minY, maxX: minX + w, maxY: minY + h };
    },

    hitTest(
        d: Drawing,
        cursorX: number,
        cursorY: number,
        tolerance: number,
        toX: (t_ms: number) => number | null,
        toY: (price: number) => number | null,
    ): HitTestResult {
        if (d.points.length < 2) return { hit: false };
        const x1 = toX(d.points[0].t_ms);
        const y1 = toY(d.points[0].price);
        const x2 = toX(d.points[1].t_ms);
        const y2 = toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return { hit: false };

        const minX = Math.min(x1, x2);
        const maxX = Math.max(x1, x2);
        const minY = Math.min(y1, y2);
        const maxY = Math.max(y1, y2);

        const distance = distToRectEdge(cursorX, cursorY, minX, minY, maxX, maxY, tolerance);
        if (distance === Infinity) return { hit: false };
        return { hit: true, distance };
    },
};
