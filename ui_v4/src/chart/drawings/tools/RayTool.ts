// src/chart/drawings/tools/RayTool.ts
// ADR-0084 D1: Ray (промінь) — сегмент від p1 крізь p2, продовжений у
// нескінченність (RAY_EXTEND_PX) у напрямку p1→p2.
//
// Domain: 2-point drawing (click-click як trend). Продовження на фіксовану
// довжину в px-просторі — самодостатньо в render І hitTest (контракт
// ToolModule без cssW у hitTest; canvas кліпає невидиме сам). AABB кліпнутий
// до canvas — інакше центр AABB (delete-× ADR-0079) опинявся б за екраном.

import type { Drawing } from '../../../types';
import { distToSegment } from '../../interaction/geometry';
import { dashPattern } from '../lineStyles';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Довжина продовження за p2 (px). Більша за будь-який реальний viewport —
 *  промінь завжди «до краю»; однакова в render+hitTest = консистентна геометрія. */
const RAY_EXTEND_PX = 10000;

/** Далека точка променя: p2 + normalize(p1→p2) * RAY_EXTEND_PX.
 *  Нульовий вектор (draft 1-го кліку) → сам p2. */
function farPoint(x1: number, y1: number, x2: number, y2: number): { x: number; y: number } {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    if (len === 0) return { x: x2, y: y2 };
    return { x: x2 + (dx / len) * RAY_EXTEND_PX, y: y2 + (dy / len) * RAY_EXTEND_PX };
}

export const RayTool: ToolModule = {
    id: 'ray',
    pointsRequired: 2,
    label: 'Промінь',
    icon: 'move-up-right',
    hotkey: 'y',
    // p2 лише задає напрямок — рендериться делікатніше за стартовий якір
    // (owner: «дві крапки на кінці» читались як шум; ієрархія лікує).
    secondaryHandles: [1],

    /** delete-× на СЕРЕДИНІ видимого сегмента: центр кліпнутого AABB не
     *  лежить на лінії (× «висів у пустоті» — owner-репорт + скрін). Кліп
     *  p1→far до canvas параметрично (Liang-Barsky lite), × при t середньому. */
    deleteAnchor(d, toX, toY, cssW, cssH) {
        if (d.points.length < 2) return null;
        const x1 = toX(d.points[0].t_ms);
        const y1 = toY(d.points[0].price);
        const x2 = toX(d.points[1].t_ms);
        const y2 = toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;
        const far = farPoint(x1, y1, x2, y2);
        const dx = far.x - x1;
        const dy = far.y - y1;
        let tMin = 0;
        let tMax = 1;
        const clip = (p: number, q: number): boolean => {
            // p*t <= q — оновити [tMin,tMax]; false → сегмент повністю поза.
            if (p === 0) return q >= 0;
            const r = q / p;
            if (p < 0) { if (r > tMax) return false; if (r > tMin) tMin = r; }
            else { if (r < tMin) return false; if (r < tMax) tMax = r; }
            return true;
        };
        if (!clip(-dx, x1) || !clip(dx, cssW - x1) || !clip(-dy, y1) || !clip(dy, cssH - y1)) return null;
        const tMid = (tMin + tMax) / 2;
        return { x: x1 + dx * tMid, y: y1 + dy * tMid };
    },

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        if (d.points.length < 2) return null;
        const x1 = rc.toX(d.points[0].t_ms);
        const y1 = rc.toY(d.points[0].price);
        const x2 = rc.toX(d.points[1].t_ms);
        const y2 = rc.toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;

        const far = farPoint(x1, y1, x2, y2);
        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected, cssW, cssH } = rc;
        // ADR-0078 color-preserving highlight (як TrendTool): стан = glow+товщина.
        const highlight = !isDraft && (isHovered || isSelected);
        ctx.strokeStyle = isDraft ? accentColor : baseColor;
        ctx.lineWidth = (d.meta?.lineWidth ?? 1) + (highlight ? 1 : 0);
        ctx.setLineDash(isDraft ? [4, 4] : dashPattern(d.meta?.lineStyle, d.meta?.lineWidth ?? 1));
        ctx.lineCap = d.meta?.lineStyle === 'dotted' ? 'round' : 'butt';
        ctx.shadowColor = highlight ? baseColor : 'transparent';
        ctx.shadowBlur = highlight ? 6 : 0;

        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(far.x, far.y);
        ctx.stroke();
        ctx.shadowBlur = 0; // reset — не протікати тінню на наступні фігури

        // AABB кліпнутий до видимого canvas: delete-× (центр AABB) лишається
        // на екрані; hit-reject коректний (курсор за межами canvas не буває).
        return {
            minX: Math.max(0, Math.min(x1, far.x)),
            maxX: Math.min(cssW, Math.max(x1, far.x)),
            minY: Math.max(0, Math.min(y1, far.y)),
            maxY: Math.min(cssH, Math.max(y1, far.y)),
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

        const far = farPoint(x1, y1, x2, y2);
        // Сегмент p1→far: перед p1 промінь НЕ існує (напрямленість).
        const distance = distToSegment(cursorX, cursorY, x1, y1, far.x, far.y);
        if (distance > tolerance) return { hit: false };
        return { hit: true, distance };
    },
};
