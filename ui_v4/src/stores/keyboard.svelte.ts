// src/stores/keyboard.svelte.ts
// ADR-0074 T5: Keyboard store з focus-guard.
//
// Винесено з App.svelte:handleGlobalKeydown (47 LOC mixed-abstraction
// X37 violation: Ctrl-combos + drawing hotkeys + diagnostics + magnet
// toggle одним if-ланцюгом). Розділ на дві фази:
//
//   1) mapKeyToAction(e) — PURE mapper, KeyboardEvent → Action | null.
//      Без side-effects, легко testable з mock event objects.
//
//   2) setupKeyboard(handler) — IMPURE side-effect: реєструє window
//      listener, dispatches Action до consumer-handler. Returns cleanup.
//
// Focus-guard (ADR-0074 D6 fix): drawing hotkeys (h/t/r/e/g/\) НЕ
// активуються коли user друкує у text-input. Розширено понад попередню
// (HTMLInputElement | HTMLTextAreaElement) → також contenteditable +
// HTMLSelectElement (Esc/Enter не повинні крадти у dropdown menus).
// Це підготовка до ADR-C text-annotations де contenteditable з'явиться.

import type { ActiveTool } from '../types';

/** Discriminated union — кожна можлива keyboard-driven дія у app.
 *  Легко extensible (новий feature = новий case + handler branch). */
export type KeyboardAction =
    | { kind: 'set_tool'; tool: ActiveTool }
    | { kind: 'cancel_draft' } // Esc — також скидає activeTool
    | { kind: 'toggle_magnet' }
    | { kind: 'undo' }
    | { kind: 'redo' }
    | { kind: 'open_diagnostics' };

/** Handler that receives mapped actions. Owner (App.svelte) provides side-effects. */
export type KeyboardHandler = (action: KeyboardAction) => void;

/** Перевіряє чи activeElement — text-input-подібний.
 *  Покриває:
 *    HTMLInputElement / HTMLTextAreaElement / HTMLSelectElement —
 *      native form widgets (Esc/Enter мають свою семантику).
 *    contentEditable — rich-text widgets (включно з ADR-C text annotations
 *      що ще не існують, але D6 forward-ref-ed).
 *  НЕ guards: <button>, <a>, generic <div> (focus там OK для drawing hotkeys). */
export function isTextInputFocused(): boolean {
    const el = typeof document !== 'undefined' ? document.activeElement : null;
    if (!el) return false;
    if (el instanceof HTMLInputElement) return true;
    if (el instanceof HTMLTextAreaElement) return true;
    if (el instanceof HTMLSelectElement) return true;
    if (el instanceof HTMLElement && el.isContentEditable) return true;
    return false;
}

/** Minimal KeyboardEvent shape потрібний для mapping. Вмисно вузький —
 *  uunit tests можуть passing plain object без DOM dependency.
 *  Real DOM KeyboardEvent assignable до цього shape. */
export interface KeyInput {
    readonly key: string;
    readonly ctrlKey: boolean;
    readonly shiftKey: boolean;
}

/** Pure key→action mapping. Renderer-агностичний.
 *
 *  Focus-guard injection (isInputFocused param) дає чисте unit-testing
 *  без DOM mocking — default читає document.activeElement, тест passes
 *  custom stub. Returns null коли key не bound (caller робить no-op). */
export function mapKeyToAction(
    e: KeyInput,
    isInputFocused: () => boolean = isTextInputFocused,
): KeyboardAction | null {
    // ── Combos з модифікаторами — НЕ guard-ed (Ctrl+Z working навіть у text input) ──
    if (e.ctrlKey && e.shiftKey && e.key === 'D') {
        return { kind: 'open_diagnostics' };
    }
    if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) {
        return { kind: 'undo' };
    }
    if (e.ctrlKey && (e.key === 'y' || e.key === 'Y')) {
        return { kind: 'redo' };
    }

    // Escape — semi-guarded: cancel працює навіть у text input (loss of focus
    // = expected UX). Drawing draft cancel + activeTool=null.
    if (e.key === 'Escape') {
        return { kind: 'cancel_draft' };
    }

    // ── Single-letter hotkeys — focus-guarded ──
    if (isInputFocused()) return null;

    const k = e.key.toLowerCase();
    if (k === 'h') return { kind: 'set_tool', tool: 'hline' };
    if (k === 't' || e.key === '\\') return { kind: 'set_tool', tool: 'trend' };
    // ↑ Display у toolbar показує `\` (ADR-0074 §6 + TrendTool.hotkey),
    //   binding підтримує BOTH `t` (legacy) + `\` (new) — soft transition
    //   без user-confusion. Future: deprecate `t` після 1-2 sprint.
    if (k === 'r') return { kind: 'set_tool', tool: 'rect' };
    if (k === 'e') return { kind: 'set_tool', tool: 'eraser' };
    if (k === 'g') return { kind: 'toggle_magnet' };
    return null;
}

/** Реєструє window keydown listener і дispatches Action до handler.
 *  preventDefault() лише для Ctrl-combos і Escape (browser default
 *  Ctrl+Z = back navigation у деяких контекстах).
 *
 *  Returns cleanup function — caller MUST call її у onDestroy / unmount. */
export function setupKeyboard(handler: KeyboardHandler): () => void {
    function onKey(e: KeyboardEvent): void {
        const action = mapKeyToAction(e);
        if (!action) return;

        // preventDefault для Ctrl-combos (browser shortcuts hijack risk)
        // і Escape (deselect/blur у деяких host environments).
        if (e.ctrlKey || e.key === 'Escape') e.preventDefault();

        handler(action);
    }

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
}
