// src/chart/drawings/tools/MeasureTool.ts
// ADR-0084 D2: Measure (лінійка) — EPHEMERAL вимір між двома точками.
//
// Показує Δціна (знак), % і тривалість (Δt людською мовою — універсально
// між TF, на відміну від «кількости барів»). НІКОЛИ не комітиться: живе
// лише як draft (renderer: finishDraft → freeze, наступний клік → новий
// вимір). Тому hitTest завжди {hit:false}, AABB = null — фігура не
// selectable, не erasable, не в сторі.

import type { Drawing } from '../../../types';
import { msg, t } from '../../../lib/i18n/i18n.svelte';
import { displayPriceDigits } from '../../../lib/priceDigits';
import { withAlpha as tokenWithAlpha } from '../../canvasColor';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

/** Людський формат тривалості: 45м · 3г 20м · 2д 5г. */
function fmtDuration(ms: number): string {
    const totalMin = Math.round(Math.abs(ms) / 60_000);
    if (totalMin < 60) return t('{minutes}м', { minutes: totalMin });
    const totalH = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (totalH < 24) return m ? t('{hours}г {minutes}м', { hours: totalH, minutes: m }) : t('{hours}г', { hours: totalH });
    const d = Math.floor(totalH / 24);
    const h = totalH % 24;
    return h ? t('{days}д {hours}г', { days: d, hours: h }) : t('{days}д', { days: d });
}

/** rgba з hex-токена з заданою альфою (canvas не вміє color-mix); запасний — нейтральний сірий. */
const withAlpha = (hex: string, alpha: number): string => tokenWithAlpha(hex, alpha, '128,128,128');

export const MeasureTool: ToolModule = {
    id: 'measure',
    pointsRequired: 2,
    label: msg('Лінійка'),
    icon: '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z"/><path d="m14.5 12.5 2-2"/><path d="m11.5 9.5 2-2"/><path d="m8.5 6.5 2-2"/><path d="m17.5 15.5 2-2"/>', // Lucide ruler
    hotkey: 'm',
    styleable: false,
    ephemeral: true,

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
        // Δціни — знаками символу з конфігу (toFixed(2) губив третій знак XAG/USD
        // і USD/JPY, 12.10.2026); без конфігу — за величиною ціни, як рядок O/H/L/C.
        // Відсоток — завжди 2 знаки.
        const priceDigits = displayPriceDigits(rc.priceDigits ?? null, p1.price);
        const label =
            `${sign}${Math.abs(dPrice).toFixed(priceDigits)} (${sign}${Math.abs(pct).toFixed(2)}%)` +
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
