// src/stores/smcStore.ts
// Pure helpers для застосування SMC даних з WS-кадрів.
// applySmcFull: full/replay frame → нова SmcData (повна заміна)
// applySmcDelta: delta frame з smc_delta → SmcData (інкрементальне оновлення)
//
// ADR-0024 §6: I4 — Один update-потік для UI.
// Патч: виправляє баг де delta-кадри очищали SMC overlay через patch(buildSmc(deltaFrame)).

import type { SmcData, SmcZone, SmcSwing, SmcLevel, SmcDeltaWire, ZoneGradeInfo } from '../types';

// F1: UI swing cap — запобігає необмеженому росту масиву swings
const MAX_UI_SWINGS = 40;

// ── Pure helpers ──────────────────────────────────────────────

/**
 * Будує SmcData з full/replay frame (повна заміна).
 * Безпечно: undefined поля → порожні масиви.
 */
export function applySmcFull(
    zones: SmcZone[] | undefined,
    swings: SmcSwing[] | undefined,
    levels: SmcLevel[] | undefined,
    trend_bias?: string | null,
    zone_grades?: Record<string, ZoneGradeInfo>,
    bias_map?: Record<string, string>,
    momentum_map?: Record<string, { b: number; r: number }>,
): SmcData {
    return {
        zones: zones ?? [],
        swings: swings ?? [],
        levels: levels ?? [],
        trend_bias: trend_bias ?? null,
        zone_grades,
        bias_map: bias_map ?? {},
        momentum_map: momentum_map ?? {},
    };
}

/**
 * Застосовує SmcDelta до поточного SmcData (інкрементально).
 *
 * Z1 rail: немає side-effects, повертає нову SmcData.
 * Порядок операцій відповідає to_wire() у backend:
 *   1. Додати new_zones
 *   2. Замінити updated_zones (by id)
 *   3. Оновити status у mitigated_zone_ids → 'mitigated'
 *   4. Додати new_swings
 *   5. Додати new_levels
 *   6. Видалити removed_level_ids
 */
export function applySmcDelta(current: SmcData, delta: SmcDeltaWire): SmcData {
    let zones = current.zones;
    let swings = current.swings;
    let levels = current.levels;

    // Updated zones: оновлюємо статус за id (замінюємо повністю)
    if (delta.updated_zones && delta.updated_zones.length > 0) {
        const updMap = new Map<string, SmcZone>(delta.updated_zones.map(z => [z.id, z]));
        zones = zones.map(z => updMap.has(z.id) ? updMap.get(z.id)! : z);
    }

    // Mitigated: оновлюємо status → 'mitigated'
    if (delta.mitigated_zone_ids && delta.mitigated_zone_ids.length > 0) {
        const mitSet = new Set<string>(delta.mitigated_zone_ids);
        zones = zones.map(z => mitSet.has(z.id) ? { ...z, status: 'mitigated' } : z);
    }

    // New zones: додаємо (уникаємо дублікатів за id)
    if (delta.new_zones && delta.new_zones.length > 0) {
        const existingIds = new Set<string>(zones.map(z => z.id));
        const fresh = delta.new_zones.filter(z => !existingIds.has(z.id));
        if (fresh.length > 0) zones = [...zones, ...fresh];
    }

    // New swings (F1: cap after adding)
    if (delta.new_swings && delta.new_swings.length > 0) {
        const existingSwingIds = new Set<string>(swings.map(s => s.id));
        const freshSwings = delta.new_swings.filter(s => !existingSwingIds.has(s.id));
        if (freshSwings.length > 0) swings = [...swings, ...freshSwings];
    }
    // F1: hard cap — тримаємо тільки останні MAX_UI_SWINGS
    if (swings.length > MAX_UI_SWINGS) {
        swings = swings.slice(-MAX_UI_SWINGS);
    }

    // Levels: видалення + нові
    if (delta.removed_level_ids && delta.removed_level_ids.length > 0) {
        const rmSet = new Set<string>(delta.removed_level_ids);
        levels = levels.filter(l => !rmSet.has(l.id));
    }
    if (delta.new_levels && delta.new_levels.length > 0) {
        const existingLevelIds = new Set<string>(levels.map(l => l.id));
        const freshLevels = delta.new_levels.filter(l => !existingLevelIds.has(l.id));
        if (freshLevels.length > 0) levels = [...levels, ...freshLevels];
    }

    return { zones, swings, levels, trend_bias: delta.trend_bias ?? current.trend_bias ?? null, bias_map: current.bias_map, momentum_map: current.momentum_map };
}

/**
 * F2: Видаляє mitigated зони з SmcData.
 * Викликається після applySmcDelta/applySmcFull коли hide_mitigated=true.
 * Чистий хелпер без side-effects.
 */
export function filterMitigatedZones(data: SmcData): SmcData {
    const filtered = data.zones.filter(z => z.status !== 'mitigated');
    if (filtered.length === data.zones.length) return data;
    return { ...data, zones: filtered };
}

/** Порожній SmcData — для ініціалізації та reset. */
export const EMPTY_SMC_DATA: SmcData = Object.freeze({ zones: [], swings: [], levels: [], trend_bias: null, bias_map: {}, momentum_map: {} } as SmcData);
