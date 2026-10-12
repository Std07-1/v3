// src/chart/drawings/tools/index.test.ts — ADR-0111 §3.3: реєстр інструментів як єдине джерело (панель, App, клавіатура).
import { describe, expect, it } from 'vitest';
import { STYLEABLE_TOOL_TYPES, TOOL_REGISTRY, toolForHotkey } from './index';

/** Клавіші, зайняті не фігурами: гумка й магніт (stores/keyboard.svelte.ts), Focus і рівні (ChartPane.svelte). */
const RESERVED_KEYS = ['e', 'g', 'f', 'l'];

describe('TOOL_REGISTRY', () => {
    it('ключ = id модуля; порядок = порядок кнопок панелі', () => {
        for (const [type, tool] of TOOL_REGISTRY) expect(tool.id).toBe(type);
        expect([...TOOL_REGISTRY.keys()]).toEqual(['hline', 'trend', 'ray', 'rect', 'measure']);
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
        expect(STYLEABLE_TOOL_TYPES).toEqual(['hline', 'trend', 'ray', 'rect']);
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
        expect(toolForHotkey('e')).toBeNull();
        expect(toolForHotkey('Escape')).toBeNull();
    });
});
