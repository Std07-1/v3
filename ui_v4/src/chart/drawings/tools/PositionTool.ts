// src/chart/drawings/tools/PositionTool.ts
// ADR-0111 §3.6: довга / коротка позиція з R:R — як у TradingView, у нашій мові взаємодії.
//
// Domain: 3 якорі — points[0] вхід (час = лівий край), points[1] стоп, points[2] ціль (обидва з часом правого краю).
// Один клік ставить вхід; стоп — 1 ATR поточного TF від входу (ATR — серверний, X28), ціль — 2R, ширина — 20 барів
// (власник 15.10: «стоп і ціль за замовчуванням — норм»). Далі тягнеш три ручки: вхід, стоп, ціль (стоп і ціль
// тягнуть і правий край — спільний). Зони: зелена — від входу до цілі, червона — від входу до стопу (кольори ролей
// bull / bear теми). Підписи: «TP ціна · відстань (%)», «SL …», «R:R 2.00» (власник: «достатньо R:R і пунктів»).
// Long і Short відрізняються лише початковим розміщенням; R:R — арифметика над якорями користувача, без оцінки.

import type { Drawing, DrawingPoint } from '../../../types';
import { msg } from '../../../lib/i18n/i18n.svelte';
import { displayPriceDigits } from '../../../lib/priceDigits';
import { withAlpha } from '../../canvasColor';
import { POSITION_GROUP } from './groups';
import type { HitTestResult, PlacementEnv, RenderContext, ScreenAabb, ToolModule } from './types';

export type PositionSide = 'long' | 'short';

/** Стоп за замовчуванням — стільки ATR від входу. */
export const DEFAULT_STOP_ATR = 1;
/** Ціль за замовчуванням — стільки R (відстаней до стопу). */
export const DEFAULT_REWARD_R = 2;
/** Ширина позиції за замовчуванням — стільки барів поточного TF. */
export const DEFAULT_WIDTH_BARS = 20;

const ENTRY = 0;
const STOP = 1;
const TARGET = 2;

/** Заливка зон: ледь помітна, щоб свічки під нею читались; чернетка — ще тихіше. */
const ZONE_FILL_ALPHA = 0.16;
const DRAFT_FILL_ALPHA = 0.1;
const LABEL_FONT = '10px ui-monospace, SFMono-Regular, Consolas, monospace';
const LABEL_PAD_X = 4;
/** Висота рядка підпису: тонша зона — підпис виходить за її межу, щоб не налазити на вхід. */
const LABEL_LINE_PX = 13;
const FALLBACK_PROFIT = '#22CC8F';
const FALLBACK_LOSS = '#ED4554';
/** Запасні RGB, коли колір ролі — не hex (ті самі, що FALLBACK_PROFIT / FALLBACK_LOSS). */
const PROFIT_RGB = '34,204,143';
const LOSS_RGB = '237,69,84';

/** Якорі позиції з точки кліку: вхід — у клік, стоп — DEFAULT_STOP_ATR × priceUnit проти напрямку, ціль — DEFAULT_REWARD_R
 *  стопів за напрямком; стоп і ціль — на правому краї (DEFAULT_WIDTH_BARS барів праворуч). */
export function positionPlacement(side: PositionSide, at: DrawingPoint, env: PlacementEnv): DrawingPoint[] {
    const dir = side === 'long' ? 1 : -1;
    const risk = env.priceUnit * DEFAULT_STOP_ATR;
    const right = at.t_ms + env.barMs * DEFAULT_WIDTH_BARS;
    return [
        { t_ms: at.t_ms, price: at.price },
        { t_ms: right, price: at.price - dir * risk },
        { t_ms: right, price: at.price + dir * risk * DEFAULT_REWARD_R },
    ];
}

export interface PositionStats {
    readonly risk: number;
    readonly reward: number;
    /** reward / risk; null — стоп на вході (ризику нема). */
    readonly rr: number | null;
}

/** Ризик і прибуток — відстані від входу до стопу й цілі; R:R — їх відношення. */
export function positionStats(points: readonly DrawingPoint[]): PositionStats {
    const entry = points[ENTRY].price;
    const risk = Math.abs(entry - points[STOP].price);
    const reward = Math.abs(points[TARGET].price - entry);
    return { risk, reward, rr: risk > 0 ? reward / risk : null };
}

/** «TP 158.420 · 0.309 (0.20%)» — ціна ноги, відстань від входу і її частка від ціни входу; знаки — символу. */
export function legLabel(tag: 'TP' | 'SL', price: number, entry: number, priceDigits: number | null | undefined): string {
    const digits = displayPriceDigits(priceDigits ?? null, entry);
    const dist = Math.abs(price - entry);
    const pct = entry !== 0 ? (dist / Math.abs(entry)) * 100 : 0;
    return `${tag} ${price.toFixed(digits)} · ${dist.toFixed(digits)} (${pct.toFixed(2)}%)`;
}

export function rrLabel(stats: PositionStats): string {
    return stats.rr === null ? 'R:R —' : `R:R ${stats.rr.toFixed(2)}`;
}

type Box = { left: number; right: number; yEntry: number; yStop: number; yTarget: number };

function boxOf(d: Drawing, toX: (t_ms: number) => number | null, toY: (price: number) => number | null): Box | null {
    if (d.points.length < 3) return null;
    const xEntry = toX(d.points[ENTRY].t_ms);
    const xEdge = toX(d.points[STOP].t_ms);
    const yEntry = toY(d.points[ENTRY].price);
    const yStop = toY(d.points[STOP].price);
    const yTarget = toY(d.points[TARGET].price);
    if (xEntry === null || xEdge === null || yEntry === null || yStop === null || yTarget === null) return null;
    return { left: Math.min(xEntry, xEdge), right: Math.max(xEntry, xEdge), yEntry, yStop, yTarget };
}

/** Базова лінія підпису ноги: усередині зони біля її зовнішньої лінії; тонка зона — зовні. */
function legLabelY(yLeg: number, yEntry: number): number {
    const above = yLeg < yEntry;
    const roomy = Math.abs(yLeg - yEntry) >= LABEL_LINE_PX + 3;
    if (above) return roomy ? yLeg + LABEL_LINE_PX - 2 : yLeg - 4;
    return roomy ? yLeg - 4 : yLeg + LABEL_LINE_PX - 2;
}

function positionTool(side: PositionSide): ToolModule {
    const long = side === 'long';
    return {
        id: side,
        pointsRequired: 1,
        label: long ? msg('Довга позиція') : msg('Коротка позиція'),
        icon: long
            ? '<path d="M5 19h14"/><path d="M5 13h14" stroke-opacity=".5"/><path d="M12 16V5"/><path d="m8 9 4-4 4 4"/>'
            : '<path d="M5 5h14"/><path d="M5 11h14" stroke-opacity=".5"/><path d="M12 8v11"/><path d="m8 15 4 4 4-4"/>',
        hotkey: long ? 'p' : 's',
        // Кольори — сенс (прибуток / ризик), не стиль: flyout стилю не має
        styleable: false,
        group: POSITION_GROUP,
        // Власник 15.10: «позиція ставиться один раз і вимикається, далі трейдер її собі підправляє»
        oneShot: true,

        place: (at, env) => positionPlacement(side, at, env),

        constrainHandle(d, handleIdx) {
            if (handleIdx !== STOP && handleIdx !== TARGET) return d;
            const edge = d.points[handleIdx].t_ms;
            return { ...d, points: d.points.map((p, i) => (i === STOP || i === TARGET ? { t_ms: edge, price: p.price } : p)) };
        },

        render(d: Drawing, rc: RenderContext): ScreenAabb | null {
            const b = boxOf(d, rc.toX, rc.toY);
            if (!b) return null;
            const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected } = rc;
            const profit = rc.bullColor ?? FALLBACK_PROFIT;
            const loss = rc.bearColor ?? FALLBACK_LOSS;
            const highlight = !isDraft && (isHovered || isSelected);
            const width = b.right - b.left;
            const fillAlpha = isDraft ? DRAFT_FILL_ALPHA : ZONE_FILL_ALPHA;

            // 1) зони: ціль — прибуток, стоп — ризик
            ctx.setLineDash([]);
            ctx.shadowBlur = 0;
            ctx.fillStyle = withAlpha(profit, fillAlpha, PROFIT_RGB);
            ctx.fillRect(b.left, Math.min(b.yEntry, b.yTarget), width, Math.abs(b.yTarget - b.yEntry));
            ctx.fillStyle = withAlpha(loss, fillAlpha, LOSS_RGB);
            ctx.fillRect(b.left, Math.min(b.yEntry, b.yStop), width, Math.abs(b.yStop - b.yEntry));

            // 2) лінії: ціль і стоп — кольором зони, вхід — кольором фігури; стан — glow + товщина (ADR-0078)
            const line = (y: number, color: string) => {
                ctx.strokeStyle = color;
                ctx.beginPath();
                ctx.moveTo(b.left, y);
                ctx.lineTo(b.right, y);
                ctx.stroke();
            };
            ctx.lineWidth = 1 + (highlight ? 1 : 0);
            ctx.setLineDash(isDraft ? [4, 4] : []);
            ctx.shadowColor = highlight ? baseColor : 'transparent';
            ctx.shadowBlur = highlight ? 6 : 0;
            line(b.yTarget, withAlpha(profit, 0.85, PROFIT_RGB));
            line(b.yStop, withAlpha(loss, 0.85, LOSS_RGB));
            line(b.yEntry, isDraft ? accentColor : baseColor);
            ctx.shadowBlur = 0;
            ctx.setLineDash([]);

            // 3) підписи з ореолом (як у Фібоначчі й лінійки)
            const [entry, stop, target] = d.points;
            ctx.font = LABEL_FONT;
            ctx.shadowColor = 'rgba(0, 0, 0, 0.75)';
            ctx.shadowBlur = 4;
            ctx.fillStyle = profit;
            ctx.fillText(legLabel('TP', target.price, entry.price, rc.priceDigits), b.left + LABEL_PAD_X, legLabelY(b.yTarget, b.yEntry));
            ctx.fillStyle = loss;
            ctx.fillText(legLabel('SL', stop.price, entry.price, rc.priceDigits), b.left + LABEL_PAD_X, legLabelY(b.yStop, b.yEntry));
            const rr = rrLabel(positionStats(d.points));
            ctx.fillStyle = baseColor;
            ctx.fillText(rr, Math.max(b.left + LABEL_PAD_X, b.right - LABEL_PAD_X - ctx.measureText(rr).width), b.yEntry - 3);
            ctx.shadowBlur = 0;

            return {
                minX: b.left,
                maxX: b.right,
                minY: Math.min(b.yEntry, b.yStop, b.yTarget),
                maxY: Math.max(b.yEntry, b.yStop, b.yTarget),
            };
        },

        /** Уся рамка — тіло (як у TV): усередині — найближча з трьох ліній, але не далі за допуск. */
        hitTest(d, cursorX, cursorY, tolerance, toX, toY): HitTestResult {
            const b = boxOf(d, toX, toY);
            if (!b) return { hit: false };
            const top = Math.min(b.yEntry, b.yStop, b.yTarget);
            const bottom = Math.max(b.yEntry, b.yStop, b.yTarget);
            if (cursorX < b.left - tolerance || cursorX > b.right + tolerance) return { hit: false };
            if (cursorY < top - tolerance || cursorY > bottom + tolerance) return { hit: false };
            const toLine = Math.min(Math.abs(cursorY - b.yEntry), Math.abs(cursorY - b.yStop), Math.abs(cursorY - b.yTarget));
            const inside = cursorX >= b.left && cursorX <= b.right && cursorY >= top && cursorY <= bottom;
            const distance = inside ? Math.min(toLine, tolerance) : toLine;
            return distance <= tolerance ? { hit: true, distance } : { hit: false };
        },
    };
}

export const LongPositionTool = positionTool('long');
export const ShortPositionTool = positionTool('short');
