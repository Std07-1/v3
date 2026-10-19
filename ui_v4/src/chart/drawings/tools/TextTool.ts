// src/chart/drawings/tools/TextTool.ts
// ADR-0111 §3.7 (власник 19.10: «Так усе»): нотатка на графіку — текст у якорі («BSL», «H4 OB», «чекаю зняття
// Азії»). Текст — meta.text (поле вводу й правила — noteText.ts); малюється на полотні, тож потрапляє в знімок
// ADR-0109. Ставиться один раз (як у TV): після тексту інструмент вимкнено, нотатка виділена. Стиль — лише колір.

import type { Drawing } from '../../../types';
import { withAlpha } from '../../canvasColor';
import type { HitTestResult, RenderContext, ScreenAabb, ToolModule } from './types';

const TEXT_FONT = '12px Inter, system-ui, sans-serif';
const PAD_X = 6;
const CHIP_H = 20;
const BASELINE_LIFT = 6;
const CHIP_BG = 'rgba(14, 17, 23, 0.72)';
/** Запасний колір, якщо колір фігури — не hex (той самий нейтральний, що --drawing-base-color). */
const FALLBACK_RGB = '200,205,214';
/** Нотатка без тексту (прев'ю під курсором до кліку). */
const PLACEHOLDER = 'Нотатка';
/** Ширина символу для хіт-тесту без полотна (оцінка для 12px) — лише доки текст ще не малювався. */
const CHAR_W_ESTIMATE = 6.6;
/** Виміряна ширина тексту (той самий шрифт, що на полотні) — для хіт-тесту й × без полотна: оцінка за кількістю
 *  символів для кирилиці давала чіп на третину ширший (× висів у порожнечі, клік повз чіп виділяв нотатку). */
const measuredWidth = new Map<string, number>();
const MEASURED_LIMIT = 512;

/** Ширина тексту чіпа: виміряна при рендері, а до першого рендеру — оцінка. */
export function textWidthOf(text: string): number {
    return measuredWidth.get(text) ?? text.length * CHAR_W_ESTIMATE;
}

function rememberWidth(text: string, width: number): void {
    if (measuredWidth.size >= MEASURED_LIMIT) measuredWidth.clear();
    measuredWidth.set(text, width);
}
/** × видалення — праворуч від чіпа, а не в центрі (там він закривав би текст); відступ центру × від краю чіпа. */
const DELETE_GAP_PX = 12;

/** Рамка чіпа: якір — лівий нижній кут. */
export function chipBox(x: number, y: number, textWidth: number): ScreenAabb {
    return { minX: x, maxX: x + textWidth + PAD_X * 2, minY: y - CHIP_H, maxY: y };
}

function textOf(d: Drawing): string {
    return d.meta?.text?.trim() || PLACEHOLDER;
}

export const TextTool: ToolModule = {
    id: 'text',
    pointsRequired: 1,
    label: 'Нотатка',
    icon: '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/>',
    hotkey: 'n',
    styleable: true,
    colorOnly: true,
    askText: true,
    oneShot: true,

    render(d: Drawing, rc: RenderContext): ScreenAabb | null {
        const x = rc.toX(d.points[0].t_ms);
        const y = rc.toY(d.points[0].price);
        if (x === null || y === null) return null;
        const { ctx, baseColor, accentColor, isDraft, isHovered, isSelected } = rc;
        const color = isDraft ? accentColor : baseColor;
        const text = textOf(d);
        ctx.font = TEXT_FONT;
        const width = ctx.measureText(text).width;
        rememberWidth(text, width);
        const box = chipBox(x, y, width);
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        ctx.fillStyle = CHIP_BG;
        ctx.fillRect(box.minX, box.minY, box.maxX - box.minX, CHIP_H);
        ctx.strokeStyle = withAlpha(color, !isDraft && (isHovered || isSelected) ? 0.9 : 0.45, FALLBACK_RGB);
        ctx.lineWidth = 1;
        ctx.strokeRect(box.minX + 0.5, box.minY + 0.5, box.maxX - box.minX - 1, CHIP_H - 1);
        ctx.fillStyle = d.meta?.text ? color : withAlpha(color, 0.55, FALLBACK_RGB);
        ctx.fillText(text, x + PAD_X, y - BASELINE_LIFT);
        return box;
    },

    deleteAnchor(d, toX, toY) {
        const x = toX(d.points[0].t_ms);
        const y = toY(d.points[0].price);
        if (x === null || y === null) return null;
        const box = chipBox(x, y, textWidthOf(textOf(d)));
        return { x: box.maxX + DELETE_GAP_PX, y: (box.minY + box.maxY) / 2 };
    },

    hitTest(d, cursorX, cursorY, tolerance, toX, toY): HitTestResult {
        const x = toX(d.points[0].t_ms);
        const y = toY(d.points[0].price);
        if (x === null || y === null) return { hit: false };
        const box = chipBox(x, y, textWidthOf(textOf(d)));
        const inside = cursorX >= box.minX && cursorX <= box.maxX && cursorY >= box.minY && cursorY <= box.maxY;
        return inside ? { hit: true, distance: Math.min(tolerance, Math.abs(cursorY - y)) } : { hit: false };
    },
};
