/**
 * ADR-0028 Φ0: Client-side display budget filter.
 *
 * Architectural split (ADR-0028 §3.0):
 *   Server (_filter_for_display) = ELIGIBILITY (proximity, TTL, strength, mitigation)
 *   Client (this module)         = PRESENTATION (budget cap, opacity, mode toggle)
 *
 * Server відправляє research-size payload. Client застосовує Focus/Research budget.
 * Логіка НЕ дублюється: server не знає про Focus, client не фільтрує за proximity.
 *
 * Invariant D0: compute ≠ display. D3: budget ≤ cap.
 */

import type { SmcZone, SmcSwing, SmcLevel, ZoneGradeInfo } from '../../types';

// ── Types ──

export interface BudgetConfig {
    /** Max zones per side (supply/demand) in Focus mode */
    perSide: number;
    /** Max structure labels (BOS/CHoCH) in Focus mode */
    structureMax: number;
    /** ADR-0104 §3.7: у Focus — найближчі ціни рівнів на бік (над і під ціною), як perSide у зон */
    levelsPerSide: number;
}

export type DisplayMode = 'focus' | 'research';

export interface ZoneDisplayProps {
    zone_id: string;
    visible: boolean;
    opacity: number;
}

export interface FilteredPayload {
    zones: SmcZone[];
    levels: SmcLevel[];
    swings: SmcSwing[];
    zoneProps: Map<string, ZoneDisplayProps>;
}

// ── Defaults ──

export const DEFAULT_BUDGET: BudgetConfig = {
    perSide: 3,
    structureMax: 4,
    levelsPerSide: 3,
};

/**
 * ADR-0104 §3.7: Focus для рівнів — найближчі `perSide` цін над і під ціною серед уже увімкнених у меню.
 * Ранг близькості рахує сервер (ADR-0028: proximity = eligibility), тут лише бюджет. Рівень без рангу (ціни немає) —
 * показується, як до ADR-0104.
 */
export function nearestLevelsPerSide(levels: SmcLevel[], perSide: number): SmcLevel[] {
    const nearest = (sign: 1 | -1): number[] =>
        [...new Set(levels.map((l) => (l.proximity ?? 0) * sign).filter((r) => r > 0))]
            .sort((a, b) => a - b)
            .slice(0, perSide)
            .map((r) => r * sign);
    const keep = new Set<number>([...nearest(1), ...nearest(-1)]);
    return levels.filter((l) => l.proximity === undefined || keep.has(l.proximity));
}

// ── Strength → Opacity ──

export function strengthToOpacity(strength: number): number {
    if (strength >= 0.8) return 1.0;
    if (strength >= 0.5) return 0.7;
    if (strength >= 0.3) return 0.4;
    return 0.15;
}

// ── Side detection (errata F2: SmcZone має `kind`, НЕ `side`) ──

function isBearishZone(zone: SmcZone): boolean {
    return (zone.kind || '').includes('bear');
}

function isBullishZone(zone: SmcZone): boolean {
    return (zone.kind || '').includes('bull');
}

// ── Main filter ──

export function applyBudget(
    zones: SmcZone[],
    levels: SmcLevel[],
    swings: SmcSwing[],
    mode: DisplayMode,
    config: BudgetConfig = DEFAULT_BUDGET,
    grades: Record<string, ZoneGradeInfo> = {},
): FilteredPayload {
    const propsMap = new Map<string, ZoneDisplayProps>();

    // Research: show A+/A/B, hide C (ADR-0029 ER-6: grade filter client-only)
    if (mode === 'research') {
        const researchZones = zones.filter(z => {
            const g = grades[z.id]?.grade ?? '';
            // Non-OB zones (FVG etc) always show; OB: hide C grade
            if (!(z.kind || '').startsWith('ob_')) return true;
            return g !== 'C';
        });
        for (const z of researchZones) {
            propsMap.set(z.id, {
                zone_id: z.id,
                visible: true,
                opacity: strengthToOpacity(z.strength ?? 1.0),
            });
        }
        return { zones: researchZones, levels, swings, zoneProps: propsMap };
    }

    // ── Focus mode: strict budget ──

    // ADR-0029: grade filter BEFORE per-side slicing (Focus = A+/A only for OBs)
    const gradeFiltered = zones.filter(z => {
        if (!(z.kind || '').startsWith('ob_')) return true;  // non-OB always pass
        const g = grades[z.id]?.grade ?? 'C';
        return g === 'A+' || g === 'A';
    });

    // 1) Split by side, apply per-side budget
    //    Zones arrive sorted by _zone_rank (server-side)
    const supply = gradeFiltered.filter(isBearishZone).slice(0, config.perSide);
    const demand = gradeFiltered.filter(isBullishZone).slice(0, config.perSide);
    const budgetZones = [...supply, ...demand];

    // 2) Structure label cap (BOS/CHoCH = swings with structural kind)
    const structureSwings: SmcSwing[] = [];
    const plainSwings: SmcSwing[] = [];
    for (const s of swings) {
        if (s.kind.startsWith('bos_') || s.kind.startsWith('choch_')) {
            structureSwings.push(s);
        } else {
            plainSwings.push(s);
        }
    }
    const budgetStructure = structureSwings.slice(-config.structureMax);
    const budgetSwings = [...plainSwings, ...budgetStructure];

    // 3) Рівні не ділять місця із зонами й структурою (ADR-0104 §3.7, рішення 29.09): Focus — найближчі ціни на бік
    //    серед увімкнених у меню; Research — усі увімкнені
    const budgetLevels = nearestLevelsPerSide(levels, config.levelsPerSide);

    // 4) Compute opacity per zone
    for (const z of budgetZones) {
        propsMap.set(z.id, {
            zone_id: z.id,
            visible: true,
            opacity: strengthToOpacity(z.strength ?? 1.0),
        });
    }

    return {
        zones: budgetZones,
        levels: budgetLevels,
        swings: budgetSwings,
        zoneProps: propsMap,
    };
}
