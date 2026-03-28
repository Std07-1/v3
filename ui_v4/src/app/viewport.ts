/**
 * Viewport utilities for mobile support.
 *
 * P1: JS-based viewport height CSS variable (--app-vh).
 * Problem: TG WebView and some mobile browsers don't support CSS `dvh` unit.
 * Static `100dvh` causes the chart to be hidden behind the URL bar or keyboard.
 * Solution: Measure actual visible height via `window.visualViewport.height`
 * (with `window.innerHeight` fallback) and set `--app-vh` CSS custom property
 * on `<html>`. CSS uses `var(--app-vh, 100dvh)` for graceful degradation.
 *
 * P2: Mobile detection via matchMedia → `body.is-mobile` class.
 * Breakpoint: 768px (industry standard, matches old UI_v2).
 * Reactive: responds to orientation change / resize.
 *
 * Reference: smc_v1 UI_v2/web_client/app.js — `updateMobileChartHeightVar()`,
 *            `isMobileLayoutV2Enabled()`.
 */

const MOBILE_BREAKPOINT = '(max-width: 768px)';

let _rafId: number | null = null;
let _cleanup: (() => void) | null = null;
let _mql: MediaQueryList | null = null;

/** Reactive mobile state — true when viewport ≤ 768px */
export let isMobile = false;

function _update(): void {
    const vh = window.visualViewport?.height ?? window.innerHeight;
    document.documentElement.style.setProperty('--app-vh', `${vh}px`);
}

function _scheduleUpdate(): void {
    if (_rafId !== null) return;
    _rafId = requestAnimationFrame(() => {
        _rafId = null;
        _update();
    });
}

function _onMobileChange(e: MediaQueryListEvent | MediaQueryList): void {
    const mobile = 'matches' in e ? e.matches : (e as MediaQueryListEvent).matches;
    isMobile = mobile;
    document.body.classList.toggle('is-mobile', mobile);
}

/**
 * Initialize viewport CSS vars and mobile detection.
 * Call once at app startup, before mount.
 * - Sets `--app-vh` immediately and keeps it updated on resize/orientation change.
 * - Sets `body.is-mobile` class based on 768px breakpoint.
 */
export function initViewportVars(): void {
    // P1: viewport height
    _update();

    const vv = window.visualViewport;
    if (vv) {
        vv.addEventListener('resize', _scheduleUpdate);
        vv.addEventListener('scroll', _scheduleUpdate);
    }
    window.addEventListener('resize', _scheduleUpdate);
    window.addEventListener('orientationchange', _scheduleUpdate);

    // P2: mobile detection
    _mql = window.matchMedia(MOBILE_BREAKPOINT);
    _onMobileChange(_mql); // set initial state
    _mql.addEventListener('change', _onMobileChange);

    _cleanup = () => {
        if (vv) {
            vv.removeEventListener('resize', _scheduleUpdate);
            vv.removeEventListener('scroll', _scheduleUpdate);
        }
        window.removeEventListener('resize', _scheduleUpdate);
        window.removeEventListener('orientationchange', _scheduleUpdate);
        _mql?.removeEventListener('change', _onMobileChange);
        if (_rafId !== null) {
            cancelAnimationFrame(_rafId);
            _rafId = null;
        }
    };
}

export function destroyViewportVars(): void {
    _cleanup?.();
    _cleanup = null;
}
