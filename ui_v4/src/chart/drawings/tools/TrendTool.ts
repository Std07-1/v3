// src/chart/drawings/tools/TrendTool.ts
// ADR-0074 T1: Trend line — straight segment between two user-clicked points.
//
// Domain: 2-point drawing (pointsRequired: 2). Render = line segment;
// hit-test = perpendicular distance до segment.
// Hotkey: `\` (graphic-icon, replaces conflict-prone `T` per ADR-0074 §6).

import type { Drawing } from '../../../types';
import { distToSegment } from '../../interaction/geometry';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

export const TrendTool: ToolModule = {
    id: 'trend',
    pointsRequired: 2,
    label: 'Трендова лінія',
    icon: 'trending-up', // Lucide icon name (T3 wires it)
    hotkey: '\\',

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        if (d.points.length < 2) return null;
        const x1 = rc.toX(d.points[0].t_ms);
        const y1 = rc.toY(d.points[0].price);
        const x2 = rc.toX(d.points[1].t_ms);
        const y2 = rc.toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;

        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected } = rc;
        // ADR-0078: committed-фігури ЗБЕРІГАЮТЬ свій колір на hover/select — стан
        // показуємо glow+товщиною, не accent-перефарбуванням. Раніше золото ховало
        // справжній колір і колізувало з палітрою (меню кольору «брехало»). Draft
        // (ще без кольору) лишається accent-пунктиром.
        const highlight = !isDraft && (isHovered || isSelected);
        ctx.strokeStyle = isDraft ? accentColor : baseColor;
        ctx.lineWidth = (d.meta?.lineWidth ?? 1) + (highlight ? 1 : 0);
        ctx.setLineDash(isDraft ? [4, 4] : []);
        ctx.shadowColor = highlight ? baseColor : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;

        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.shadowBlur = 0; // reset — не протікати тінню на наступні фігури

        return {
            minX: Math.min(x1, x2),
            maxX: Math.max(x1, x2),
            minY: Math.min(y1, y2),
            maxY: Math.max(y1, y2),
        };
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

        const distance = distToSegment(cursorX, cursorY, x1, y1, x2, y2);
        if (distance > tolerance) return { hit: false };
        return { hit: true, distance };
    },
};
