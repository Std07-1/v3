// src/chart/drawings/tools/HLineTool.ts
// ADR-0074 T1: Horizontal line tool — full-width line at user-specified price.
//
// Domain: 1-point drawing (pointsRequired: 1). Time-coordinate of point[0]
// ігнорується для render — лінія тягнеться на повну ширину canvas. t_ms
// у point[0] зберігається для consistency з Drawing schema (всі типи мають
// >=1 DrawingPoint). Hit-test = distance до Y=lineY у screen-space.

import type { Drawing } from '../../../types';
import { distToHLine } from '../../interaction/geometry';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

export const HLineTool: ToolModule = {
    id: 'hline',
    pointsRequired: 1,
    label: 'Горизонтальна лінія',
    icon: 'minus', // Lucide icon name (T3 wires it)
    hotkey: 'h',

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        const y = rc.toY(d.points[0].price);
        if (y === null) return null;

        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected, cssW } = rc;
        ctx.strokeStyle = isDraft || isHovered || isSelected ? accentColor : baseColor;
        ctx.lineWidth = d.meta?.lineWidth ?? 1;
        ctx.setLineDash(isDraft ? [4, 4] : []);

        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(cssW, y);
        ctx.stroke();

        return { minX: 0, maxX: cssW, minY: y, maxY: y };
    },

    hitTest(
        d: Drawing,
        _cursorX: number,
        cursorY: number,
        tolerance: number,
        _toX: (t_ms: number) => number | null,
        toY: (price: number) => number | null,
    ): HitTestResult {
        const y = toY(d.points[0].price);
        if (y === null) return { hit: false };

        const distance = distToHLine(cursorY, y);
        if (distance > tolerance) return { hit: false };

        return { hit: true, distance };
    },
};
