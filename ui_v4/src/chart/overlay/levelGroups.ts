/**
 * ADR-0104 §3.7 (рішення власника 29.09): меню «Рівні» — рядок на групу рівнів, вибір трейдера окремо на кожному TF.
 *
 * Словник груп і типовий стан рядка на TF (`auto`) — сервер (X28). UI лише зберігає вибір трейдера поверх типового
 * і показує/ховає рівні. Запис у виборі є тільки там, де трейдер відійшов від типового, — тож «Скинути до типових»
 * активне рівно тоді, коли відхилення є.
 */

import type { LevelGroup, SmcLevel } from '../../types';

/** Рядки меню у порядку показу; підписи — копірайт UI. */
export const LEVEL_MENU_ROWS: ReadonlyArray<{ group: LevelGroup; label: string }> = [
    { group: 'day', label: 'День' },
    { group: 'week', label: 'Тиждень' },
    { group: 'open', label: 'Відкриття дня' },
    { group: 'open_week', label: 'Відкриття тижня' },
    { group: 'asia', label: 'Азія' },
    { group: 'london', label: 'Лондон' },
    { group: 'newyork', label: 'Нью-Йорк' },
    { group: 'sessions_prev', label: 'Сесії вчора' },
    { group: 'h4', label: 'Попередня H4' },
    { group: 'h1', label: 'Попередня H1' },
    { group: 'liquidity', label: 'Ліквідність EQ' },
];

/** Вибір трейдера на одному TF: група → показувати (true) / ховати (false); немає ключа — типове сервера. */
export type GroupOverrides = Partial<Record<LevelGroup, boolean>>;
/** Вибір по всіх TF: мітка TF кадру ("M5", "H1", …) → вибір. */
export type OverridesByTf = Record<string, GroupOverrides>;

export interface LevelMenuRow {
    group: LevelGroup;
    label: string;
    /** Скільки рівнів групи в кадрі; 0 — рядок сірий. */
    count: number;
    /** Рядок увімкнений (з урахуванням вибору трейдера). */
    on: boolean;
}

export const LEVEL_OVERRIDES_STORAGE_KEY = 'smc_level_groups';

/** Рівень видно, якщо трейдер увімкнув його групу, або без вибору — коли сервер каже «типово так». */
export function isLevelVisible(level: SmcLevel, overrides: GroupOverrides): boolean {
    if (!level.group) return true; // сервер без груп — показувати, як до меню
    const chosen = overrides[level.group];
    return chosen !== undefined ? chosen : level.auto !== false;
}

export function visibleLevels(levels: SmcLevel[], overrides: GroupOverrides): SmcLevel[] {
    return levels.filter((l) => isLevelVisible(l, overrides));
}

/** Типовий стан рядка на цьому TF — за рівнями кадру; рядок без рівнів типово вимкнений. */
function groupDefault(levels: SmcLevel[], group: LevelGroup): boolean {
    return levels.some((l) => l.group === group && l.auto !== false);
}

export function buildMenuRows(levels: SmcLevel[], overrides: GroupOverrides): LevelMenuRow[] {
    return LEVEL_MENU_ROWS.map(({ group, label }) => {
        const count = levels.filter((l) => l.group === group).length;
        const chosen = overrides[group];
        return { group, label, count, on: chosen !== undefined ? chosen : groupDefault(levels, group) };
    });
}

/** Перемкнути рядок; якщо новий стан збігся з типовим — запис вибору прибирається (відхилення немає). */
export function toggleGroup(levels: SmcLevel[], overrides: GroupOverrides, group: LevelGroup): GroupOverrides {
    const row = buildMenuRows(levels, overrides).find((r) => r.group === group);
    const next = { ...overrides };
    const want = !(row?.on ?? false);
    const hasLevels = (row?.count ?? 0) > 0;
    if (hasLevels && want === groupDefault(levels, group)) delete next[group];
    else next[group] = want;
    return next;
}

export function hasOverrides(overrides: GroupOverrides | undefined): boolean {
    return !!overrides && Object.keys(overrides).length > 0;
}

/** Вибір з localStorage; пошкоджене чи недоступне сховище — порожній вибір (типові сервера). */
export function loadOverrides(storage: Pick<Storage, 'getItem'> | undefined): OverridesByTf {
    try {
        const raw = storage?.getItem(LEVEL_OVERRIDES_STORAGE_KEY);
        if (!raw) return {};
        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return {};
        const known = new Set<string>(LEVEL_MENU_ROWS.map((r) => r.group));
        const out: OverridesByTf = {};
        for (const [tf, groups] of Object.entries(parsed as Record<string, unknown>)) {
            if (!groups || typeof groups !== 'object') continue;
            const clean: GroupOverrides = {};
            for (const [g, v] of Object.entries(groups as Record<string, unknown>)) {
                if (known.has(g) && typeof v === 'boolean') clean[g as LevelGroup] = v; // невідомі ключі — ігнор
            }
            if (hasOverrides(clean)) out[tf] = clean;
        }
        return out;
    } catch {
        return {};
    }
}

export function saveOverrides(storage: Pick<Storage, 'setItem'> | undefined, all: OverridesByTf): void {
    try {
        storage?.setItem(LEVEL_OVERRIDES_STORAGE_KEY, JSON.stringify(all));
    } catch {
        /* сховище недоступне (приватне вікно, квота) — вибір діє до перезавантаження */
    }
}
