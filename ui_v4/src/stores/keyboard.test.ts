// ADR-0074 T6 — keyboard store unit tests.
// Coverage: mapKeyToAction для всіх 6 KeyboardAction kinds × focus states +
// Ctrl-combos (працюють навіть у text input) + lowercase/uppercase parity.
//
// Without DOM dep: mapKeyToAction приймає focus-check injection. Tests
// passing custom predicate (no document/jsdom required).

import { describe, it, expect } from 'vitest';
import { mapKeyToAction, type KeyInput } from './keyboard.svelte';

// Test helpers — build minimal KeyInput shape.
function key(
    k: string,
    opts: { ctrl?: boolean; shift?: boolean } = {},
): KeyInput {
    return {
        key: k,
        ctrlKey: opts.ctrl ?? false,
        shiftKey: opts.shift ?? false,
    };
}

const notFocused = () => false;
const focused = () => true;

describe('mapKeyToAction — drawing tools', () => {
    it('H → set_tool hline', () => {
        expect(mapKeyToAction(key('h'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'hline',
        });
        expect(mapKeyToAction(key('H'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'hline',
        });
    });

    it('T → set_tool trend (legacy hotkey)', () => {
        expect(mapKeyToAction(key('t'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'trend',
        });
    });

    it('\\ → set_tool trend (ADR-0074 §6 new hotkey)', () => {
        expect(mapKeyToAction(key('\\'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'trend',
        });
    });

    it('R → set_tool rect', () => {
        expect(mapKeyToAction(key('r'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'rect',
        });
    });

    it('E → set_tool eraser', () => {
        expect(mapKeyToAction(key('e'), notFocused)).toEqual({
            kind: 'set_tool',
            tool: 'eraser',
        });
    });

    it('G → toggle_magnet', () => {
        expect(mapKeyToAction(key('g'), notFocused)).toEqual({
            kind: 'toggle_magnet',
        });
    });
});

describe('mapKeyToAction — special keys', () => {
    it('Escape → cancel_draft (працює навіть у text input)', () => {
        expect(mapKeyToAction(key('Escape'), notFocused)).toEqual({
            kind: 'cancel_draft',
        });
        expect(mapKeyToAction(key('Escape'), focused)).toEqual({
            kind: 'cancel_draft',
        });
    });
});

describe('mapKeyToAction — Ctrl combos (focus-guard NOT applied)', () => {
    it('Ctrl+Z → undo (з focused input працює)', () => {
        expect(mapKeyToAction(key('z', { ctrl: true }), focused)).toEqual({
            kind: 'undo',
        });
        expect(mapKeyToAction(key('Z', { ctrl: true }), notFocused)).toEqual({
            kind: 'undo',
        });
    });

    it('Ctrl+Y → redo', () => {
        expect(mapKeyToAction(key('y', { ctrl: true }), notFocused)).toEqual({
            kind: 'redo',
        });
    });

    it('Ctrl+Shift+D → open_diagnostics', () => {
        expect(
            mapKeyToAction(key('D', { ctrl: true, shift: true }), notFocused),
        ).toEqual({ kind: 'open_diagnostics' });
    });
});

describe('mapKeyToAction — focus-guard suppress', () => {
    it('drawing hotkeys SUPPRESSED коли input focused', () => {
        expect(mapKeyToAction(key('h'), focused)).toBeNull();
        expect(mapKeyToAction(key('t'), focused)).toBeNull();
        expect(mapKeyToAction(key('\\'), focused)).toBeNull();
        expect(mapKeyToAction(key('r'), focused)).toBeNull();
        expect(mapKeyToAction(key('e'), focused)).toBeNull();
        expect(mapKeyToAction(key('g'), focused)).toBeNull();
    });

    it('Ctrl combos NOT suppressed (allow undo у text input)', () => {
        expect(mapKeyToAction(key('z', { ctrl: true }), focused)).not.toBeNull();
        expect(mapKeyToAction(key('y', { ctrl: true }), focused)).not.toBeNull();
        expect(
            mapKeyToAction(key('D', { ctrl: true, shift: true }), focused),
        ).not.toBeNull();
    });
});

describe('mapKeyToAction — unbound keys', () => {
    it('Random char → null', () => {
        expect(mapKeyToAction(key('a'), notFocused)).toBeNull();
        expect(mapKeyToAction(key('1'), notFocused)).toBeNull();
        expect(mapKeyToAction(key('ArrowLeft'), notFocused)).toBeNull();
    });

    it('Plain Ctrl без letter → null', () => {
        expect(mapKeyToAction(key('Control', { ctrl: true }), notFocused)).toBeNull();
    });
});
