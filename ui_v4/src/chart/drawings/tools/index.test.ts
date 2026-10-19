// src/chart/drawings/tools/index.test.ts — ADR-0111 §3.3: реєстр інструментів як єдине джерело (панель, App, клавіатура).
import { describe, expect, it } from 'vitest';
import { ALERTABLE_TOOL_TYPES, STYLEABLE_TOOL_TYPES, TOOL_REGISTRY, alertLevelOf, toolForHotkey } from './index';

/** Клавіші, зайняті не фігурами: гумка й магніт (stores/keyboard.svelte.ts), Focus і рівні (ChartPane.svelte). */
const RESERVED_KEYS = ['e', 'g', 'f', 'l'];

describe('TOOL_REGISTRY', () => {
    it('одноразові (ставляться раз і вимикаються) — позиції (власник 15.10) і нотатка (як у TV, ADR-0111 §3.7)', () => {
        expect([...TOOL_REGISTRY.values()].filter((tool) => tool.oneShot).map((tool) => tool.id)).toEqual(['long', 'short', 'text']);
    });

    it('зі стилю лише колір — нотатка; текст питають — лише в нотатки', () => {
        expect([...TOOL_REGISTRY.values()].filter((tool) => tool.colorOnly).map((tool) => tool.id)).toEqual(['text']);
        expect([...TOOL_REGISTRY.values()].filter((tool) => tool.askText).map((tool) => tool.id)).toEqual(['text']);
    });

    it('ключ = id модуля; порядок = порядок кнопок панелі', () => {
        for (const [type, tool] of TOOL_REGISTRY) expect(tool.id).toBe(type);
        expect([...TOOL_REGISTRY.keys()]).toEqual(['hline', 'hray', 'trend', 'ray', 'rect', 'zone', 'fib', 'long', 'short', 'text', 'measure']);
    });

    it('кожен модуль несе підпис, SVG-розмітку іконки і клавішу з одного символу', () => {
        for (const tool of TOOL_REGISTRY.values()) {
            expect(tool.label.length).toBeGreaterThan(0);
            expect(tool.icon).toMatch(/^<(path|polyline|rect|circle|line)\b/);
            expect(tool.hotkey).toHaveLength(1);
        }
    });

    it('клавіші й псевдоніми не перетинаються між собою і з клавішами не-фігур', () => {
        const keys = [...TOOL_REGISTRY.values()].flatMap((tool) =>
            [tool.hotkey, ...(tool.hotkeyAliases ?? [])].map((key) => key.toLowerCase()),
        );
        expect(new Set(keys).size).toBe(keys.length);
        for (const key of keys) expect(RESERVED_KEYS).not.toContain(key);
    });

    it('стиль мають усі фігури, крім ефемерної лінійки', () => {
        expect(STYLEABLE_TOOL_TYPES).toEqual(['hline', 'hray', 'trend', 'ray', 'rect', 'zone', 'fib', 'text']);
        const ephemeral = [...TOOL_REGISTRY.values()].filter((tool) => tool.ephemeral).map((tool) => tool.id);
        expect(ephemeral).toEqual(['measure']);
        for (const tool of TOOL_REGISTRY.values()) if (tool.ephemeral) expect(tool.styleable).toBe(false);
    });
});

describe('toolForHotkey', () => {
    it('регістр не важить, псевдонім працює, чужа клавіша — null', () => {
        expect(toolForHotkey('H')).toBe('hline');
        expect(toolForHotkey('\\')).toBe('trend');
        expect(toolForHotkey('t')).toBe('trend');
        expect(toolForHotkey('Y')).toBe('ray');
        expect(toolForHotkey('r')).toBe('rect');
        expect(toolForHotkey('M')).toBe('measure');
        expect(toolForHotkey('b')).toBe('fib');
        expect(toolForHotkey('e')).toBeNull();
        expect(toolForHotkey('Escape')).toBeNull();
    });
});

describe('лінія-сповіщення (ADR-0114)', () => {
    it('сповіщають горизонталь (рівень без якоря) і промінь рівня (якір — його початок); решта — ні', () => {
        expect(ALERTABLE_TOOL_TYPES).toEqual(['hline', 'hray']);
        expect(alertLevelOf({ id: 'k', type: 'hray', points: [{ t_ms: 1_700_000_000_000, price: 157.25 }] })).toEqual({
            level: 157.25,
            anchorMs: 1_700_000_000_000,
        });
        const line = { id: 'd1', type: 'hline' as const, points: [{ t_ms: 1 as never, price: 4380.25 }] };
        expect(alertLevelOf(line)).toEqual({ level: 4380.25, anchorMs: null });
        expect(alertLevelOf({ ...line, type: 'trend' })).toBeNull();
    });
});
