// src/chart/drawings/tools/HLineTool.ts
// ADR-0074 T1: Horizontal line tool — full-width line at user-specified price.
//
// Domain: 1-point drawing (pointsRequired: 1). Time-coordinate of point[0]
// ігнорується для render — лінія тягнеться на повну ширину canvas. t_ms
// у point[0] зберігається для consistency з Drawing schema (всі типи мають
// >=1 DrawingPoint). Hit-test = distance до Y=lineY у screen-space.

import type { Drawing } from '../../../types';
import { distToHLine } from '../../interaction/geometry';
import { dashPattern } from '../lineStyles';
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
        // ADR-0078: color-preserving hover/select (див. TrendTool). Committed —
        // свій колір + glow; draft — accent-пунктир.
        const highlight = !isDraft && (isHovered || isSelected);
        ctx.strokeStyle = isDraft ? accentColor : baseColor;
        ctx.lineWidth = (d.meta?.lineWidth ?? 1) + (highlight ? 1 : 0);
        ctx.setLineDash(isDraft ? [4, 4] : dashPattern(d.meta?.lineStyle, d.meta?.lineWidth ?? 1));
        ctx.lineCap = d.meta?.lineStyle === 'dotted' ? 'round' : 'butt';
        ctx.shadowColor = highlight ? baseColor : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;

        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(cssW, y);
        ctx.stroke();
        ctx.shadowBlur = 0; // reset — не протікати тінню на наступні фігури

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
