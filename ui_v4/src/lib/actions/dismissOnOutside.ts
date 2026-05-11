// src/lib/actions/dismissOnOutside.ts
//
// ════════════════════════════════════════════════════════════════════════════
//  🔒 SSOT для outside-dismiss поведінки у всьому UI 🔒
// ════════════════════════════════════════════════════════════════════════════
//  Owner-confirmed working 2026-05-11. Single source of truth для
//  click/touchend/Escape dismiss поверх dropdowns, panels, popovers.
//
//  Поточні consumers (станом на 2026-05-11):
//    1. App.svelte                — ☰ overflow menu (.tr-overflow-wrap)
//    2. ChartPane.svelte          — SMC layer panel (.smc-panel)
//    3. ChartHud.svelte           — symbol/TF dropdowns + micro-card (.hud-stack)
//    4. NarrativePanel.svelte     — expanded narrative
//
//  Заборонені правки без owner approval:
//    - видалення touchend listener (mobile chart taps перестануть закривати panels)
//    - видалення setTimeout(0) attach guard (opening click → self-dismiss)
//    - заміна document-level listener на window:click у Svelte (втратимо capture)
//    - обхід через ad-hoc handlers у конкретному компоненті (дрифт від pattern)
//
//  Дозволені правки:
//    - нові опції (наприклад ignoreSelector, як зараз)
//    - bugfixes у isInside логіці
//    - performance optimization (e.g. capture-once on attach)
// ════════════════════════════════════════════════════════════════════════════
//
// Svelte 5 action для уніфікованого outside-click + outside-touch + Escape
// dismiss патерну. Замінює фрагментовані per-component handlers
// (svelte:window onclick, document.addEventListener, ad-hoc bubbling).
//
// ── Чому action а не store/composable ───────────────────────────────────
// Action прив'язаний до конкретного DOM node → автоматично знає що "всередині"
// (event.target ∈ node.contains). Це cleanest API для Svelte 5: декларативно,
// no manual ref management, auto cleanup на unmount.
//
// ── Чому click + touchend + escape ──────────────────────────────────────
//   click       — desktop mouse + mobile native click синтез (працює коли
//                 LWC canvas не блокує, тобто поза канвасом)
//   touchend    — mobile fallback ОБОВ'ЯЗКОВО: LWC `preventDefault()` на
//                 канвасі блокує синтез click event для chart taps. Без
//                 touchend dropdowns не закриваються від tap'у на чарт.
//   keydown Esc — keyboard accessibility (a11y), free with same pattern.
//
// ── Opening-click guard ──────────────────────────────────────────────────
// setTimeout(0) на attach: якщо action вмикається в обробці того ж самого
// click що відкрив panel, без guard listener одразу зловив би цей click і
// закрив panel ту ж мить. Defer на наступний tick → opening click встигає
// завершитись до того як listener почне слухати.
//
// ── Inside-click suppression ─────────────────────────────────────────────
// node.contains(event.target) визначає "всередині". Це означає що SUB-elements
// (buttons, links у panel) не закривають panel — це expected. Якщо потрібно
// особливе виключення (e.g. ігнорувати click на конкретний trigger button що
// не є нащадком node), використовуй `ignoreSelector` опцію.
//
// ── Usage ────────────────────────────────────────────────────────────────
// <div use:dismissOnOutside={{
//   enabled: panelOpen,
//   onDismiss: () => panelOpen = false,
//   ignoreSelector: '.toggle-trigger',  // optional
// }}>
//   ...panel content
// </div>
//
// `enabled` reactive: action автоматично attach/detach коли prop міняється.

import type { Action } from 'svelte/action';

export interface DismissOnOutsideOptions {
    /** Чи слухати events. False → action детачить listener. */
    enabled: boolean;
    /** Callback викликається коли клік/тап поза node або натиснуто Escape. */
    onDismiss: () => void;
    /** Опціональний CSS selector для elements, click на які НЕ повинен dismiss
     *  (наприклад зовнішня trigger button що toggle-ить panel). Перевіряється
     *  через event.target.closest(ignoreSelector). */
    ignoreSelector?: string;
}

/**
 * Svelte 5 action: dismiss panel on outside click/touch/Escape.
 *
 * Auto attach/detach based on `opts.enabled` reactive prop. Listeners
 * registered на `document` (capture=false, тобто bubble phase) → fires
 * AFTER any inside-component handler що може stopPropagation.
 *
 * @example
 *   <div use:dismissOnOutside={{
 *     enabled: panelOpen,
 *     onDismiss: () => panelOpen = false,
 *   }}>
 *
 * @example з виключенням trigger button
 *   <div use:dismissOnOutside={{
 *     enabled: panelOpen,
 *     onDismiss: () => panelOpen = false,
 *     ignoreSelector: '.external-toggle-btn',
 *   }}>
 */
export const dismissOnOutside: Action<HTMLElement, DismissOnOutsideOptions> = (
    node,
    initialOpts,
) => {
    let opts = initialOpts;
    let attachTimer: number | null = null;
    let attached = false;

    function isInside(target: EventTarget | null): boolean {
        if (!(target instanceof Node)) return false;
        if (node.contains(target)) return true;
        if (opts.ignoreSelector && target instanceof Element) {
            if (target.closest(opts.ignoreSelector)) return true;
        }
        return false;
    }

    function handlePointer(e: Event): void {
        if (isInside(e.target)) return;
        opts.onDismiss();
    }

    function handleKey(e: KeyboardEvent): void {
        if (e.key === 'Escape') {
            opts.onDismiss();
        }
    }

    function attach(): void {
        if (attached) return;
        attached = true;
        // Defer attach так щоб opening click/touch завершився до того як
        // listener почне слухати — інакше self-dismiss на тому ж event.
        attachTimer = window.setTimeout(() => {
            attachTimer = null;
            document.addEventListener('click', handlePointer);
            document.addEventListener('touchend', handlePointer);
            document.addEventListener('keydown', handleKey);
        }, 0);
    }

    function detach(): void {
        if (attachTimer != null) {
            window.clearTimeout(attachTimer);
            attachTimer = null;
        }
        if (!attached) return;
        attached = false;
        document.removeEventListener('click', handlePointer);
        document.removeEventListener('touchend', handlePointer);
        document.removeEventListener('keydown', handleKey);
    }

    function sync(): void {
        if (opts.enabled) attach();
        else detach();
    }

    sync();

    return {
        update(newOpts: DismissOnOutsideOptions) {
            opts = newOpts;
            sync();
        },
        destroy() {
            detach();
        },
    };
};
