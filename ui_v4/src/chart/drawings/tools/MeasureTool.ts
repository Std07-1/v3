// src/chart/drawings/tools/MeasureTool.ts
// ADR-0084 D2: Measure (лінійка) — EPHEMERAL вимір між двома точками.
//
// Показує Δціна (знак), % і тривалість (Δt людською мовою — універсально
// між TF, на відміну від «кількости барів»). НІКОЛИ не комітиться: живе
// лише як draft (renderer: finishDraft → freeze, наступний клік → новий
// вимір). Тому hitTest завжди {hit:false}, AABB = null — фігура не
// selectable, не erasable, не в сторі.

import type { Drawing } from '../../../types';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Людський формат тривалості: 45м · 3г 20м · 2д 5г. */
function fmtDuration(ms: number): string {
    const totalMin = Math.round(Math.abs(ms) / 60_000);
    if (totalMin < 60) return `${totalMin}м`;
    const totalH = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (totalH < 24) return m ? `${totalH}г ${m}м` : `${totalH}г`;
    const d = Math.floor(totalH / 24);
    const h = totalH % 24;
    return h ? `${d}д ${h}г` : `${d}д`;
}

/** rgba з hex-токена з заданою альфою (canvas не вміє color-mix). */
function withAlpha(hex: string, alpha: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return `rgba(128,128,128,${alpha})`;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

export const MeasureTool: ToolModule = {
    id: 'measure',
    pointsRequired: 2,
    label: 'Лінійка',
    icon: 'ruler',
    hotkey: 'm',

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        if (d.points.length < 2) return null;
        const x1 = rc.toX(d.points[0].t_ms);
        const y1 = rc.toY(d.points[0].price);
        const x2 = rc.toX(d.points[1].t_ms);
        const y2 = rc.toY(d.points[1].price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;

        const { ctx, cssW } = rc;
        const p1 = d.points[0];
        const p2 = d.points[1];
        const dPrice = p2.price - p1.price;
        const up = dPrice >= 0;
        const color = up ? (rc.bullColor ?? '#22CC8F') : (rc.bearColor ?? '#ED4554');

        // Зона виміру: делікатна заливка напрямку + межі-пунктир.
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        ctx.fillStyle = withAlpha(color, 0.08);
        ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));

        // Діагональ виміру.
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.setLineDash([]);

        // Лейбл: "+12.34 (+0.29%) · 5г 30м" біля p2, clamp у canvas, halo
        // для читабельности на будь-якому тлі (техніка delete-× ADR-0079).
        const pct = p1.price !== 0 ? (dPrice / p1.price) * 100 : 0;
        const sign = up ? '+' : '−';
        const label =
            `${sign}${Math.abs(dPrice).toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)` +
            ` · ${fmtDuration(p2.t_ms - p1.t_ms)}`;
        ctx.font = '11px ui-monospace, SFMono-Regular, Consolas, monospace';
        const w = ctx.measureText(label).width;
        const lx = Math.max(4, Math.min(x2 + 10, cssW - w - 6));
        const ly = Math.max(14, y2 - 10);
        ctx.shadowColor = 'rgba(0, 0, 0, 0.75)';
        ctx.shadowBlur = 4;
        ctx.fillStyle = color;
        ctx.fillText(label, lx, ly);
        ctx.shadowBlur = 0;

        // Ephemeral: не selectable — AABB не кешуємо.
        return null;
    },

    hitTest(): HitTestResult {
        // Ephemeral вимір ніколи не в this.drawings — сюди не потрапляє;
        // контрактна заглушка (не selectable за визначенням).
        return { hit: false };
    },
};
