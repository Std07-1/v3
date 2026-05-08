// src/chart/themes.ts
// P3.11 Multi-theme + P3.12 Candle styles — SSOT визначення.
// V3 parity: chart_adapter_lite.js:102-153 (теми), :158-199 (стилі свічок).
// Persistence: localStorage ('v4_theme', 'v4_candle_style').
//
// ADR-0066 PATCH 02b: dark theme palette MIRRORS ui_v4/src/styles/tokens.css `:root`.
// LWC applyOptions() consumes raw hex (cannot resolve CSS vars at runtime), so values
// are duplicated by necessity. **Edit BOTH places** when tokens shift; mismatch = drift.
// Black/light theme alignment → PATCH 02c (will use [data-theme="..."] overrides in tokens.css).

import { CrosshairMode, LineStyle } from 'lightweight-charts';

// ─── Theme definitions (Entry 078: dark / black / light) ───

export type ThemeName = 'dark' | 'black' | 'light';

export interface ThemeDef {
    label: string;
    chart: {
        layout: { background: { color: string }; textColor: string };
        grid: { vertLines: { color: string }; horzLines: { color: string } };
        crosshair: {
            mode: number;
            vertLine: { color: string; width: number; style: number };
            horzLine: { color: string; width: number; style: number };
        };
    };
    /** CSS фон для .app-layout */
    appBg: string;
    /** Фон для HUD overlay (transparent = без фону) */
    hudBg: string;
    /** Текстовий колір для HUD елементів (адаптивний відносно фону) */
    hudText: string;
    /** Бордер для HUD */
    hudBorder: string;
    /** Фон для StatusBar */
    statusBarBg: string;
    /** Фон для dropdown меню */
    menuBg: string;
    /** Бордер для dropdown меню */
    menuBorder: string;
    // ─── ADR-0007: Drawing Toolbar + DrawingsRenderer colors ───
    /** Базовий колір малювань (WCAG AA на відповідному фоні) */
    drawingColor: string;
    /** Fill для rect drawings */
    drawingRectFill: string;
    /** Glass background для DrawingToolbar */
    toolbarBg: string;
    /** Rim border для DrawingToolbar */
    toolbarBorder: string;
    /** Hover background для кнопок тулбара */
    toolbarHoverBg: string;
    /** Active accent color (інструмент обраний) */
    toolbarActiveColor: string;
    // ─── ADR-0041: P/D EQ line color ───
    /** 40% opacity gray for equilibrium dashed line */
    pdEqLineColor: string;
    // ─── ADR-0041: P/D Badge colors (WCAG AA ≥ 4.5:1) ───
    pdBadgeDiscountBg: string;
    pdBadgeDiscountText: string;
    pdBadgePremiumBg: string;
    pdBadgePremiumText: string;
    pdBadgeEqBg: string;
    pdBadgeEqText: string;
}

export const THEMES: Record<ThemeName, ThemeDef> = {
    dark: {
        label: 'Dark',
        chart: {
            // ADR-0066: textColor = --text-2 (#9B9BB0); grid = --border @ 0.4 (#30363D)
            layout: { background: { color: 'transparent' }, textColor: '#9B9BB0' },
            grid: {
                vertLines: { color: 'rgba(48, 54, 61, 0.6)' },
                horzLines: { color: 'rgba(48, 54, 61, 0.6)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(213, 213, 213, 0.35)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(213, 213, 213, 0.35)', width: 1, style: LineStyle.Dashed },
            },
        },
        // ADR-0066 token mirror: --bg / --elev / --card / --border / --text-1 / --accent
        appBg: '#0D1117',                              // --bg
        hudBg: 'transparent',
        hudText: '#E6EDF3',                            // --text-1
        hudBorder: 'transparent',
        statusBarBg: '#161B22',                        // --elev
        menuBg: 'rgba(28, 33, 40, 0.92)',              // --card @ 0.92
        menuBorder: 'rgba(48, 54, 61, 0.8)',           // --border
        drawingColor: '#E6EDF3',                       // --text-1
        drawingRectFill: 'rgba(230, 237, 243, 0.10)',
        toolbarBg: 'rgba(13, 17, 23, 0.6)',            // --bg @ 0.6
        toolbarBorder: 'rgba(48, 54, 61, 0.8)',        // --border
        toolbarHoverBg: 'rgba(230, 237, 243, 0.08)',
        toolbarActiveColor: '#D4A017',                 // --accent (gold, replaces #3d9aff blue)
        pdEqLineColor: 'rgba(255, 255, 255, 0.40)',
        pdBadgeDiscountBg: 'rgba(46, 204, 113, 0.15)',
        pdBadgeDiscountText: '#2ecc71',
        pdBadgePremiumBg: 'rgba(239, 83, 80, 0.15)',
        pdBadgePremiumText: '#ef5350',
        pdBadgeEqBg: 'rgba(255, 255, 255, 0.10)',
        pdBadgeEqText: 'rgba(255, 255, 255, 0.60)',
    },
    black: {
        label: 'Black',
        chart: {
            // ADR-0066 PATCH 02c: text/grid use --text-2 / --border tokens for cross-theme harmony
            layout: { background: { color: '#000000' }, textColor: '#9B9BB0' },
            grid: {
                vertLines: { color: 'rgba(48, 54, 61, 0.4)' },
                horzLines: { color: 'rgba(48, 54, 61, 0.4)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(213, 213, 213, 0.40)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(213, 213, 213, 0.40)', width: 1, style: LineStyle.Dashed },
            },
        },
        // ADR-0066 token mirror (black variant): true black canvas, --elev/--card stay
        appBg: '#000000',                              // true black identity
        hudBg: 'transparent',
        hudText: '#E6EDF3',                            // --text-1
        hudBorder: 'transparent',
        statusBarBg: '#0A0A0A',                        // black-tinted --elev
        menuBg: 'rgba(20, 20, 20, 0.92)',
        menuBorder: 'rgba(48, 54, 61, 0.6)',           // --border @ 0.6
        drawingColor: '#E6EDF3',                       // --text-1
        drawingRectFill: 'rgba(230, 237, 243, 0.10)',
        toolbarBg: 'rgba(0, 0, 0, 0.6)',
        toolbarBorder: 'rgba(48, 54, 61, 0.6)',        // --border
        toolbarHoverBg: 'rgba(230, 237, 243, 0.08)',
        toolbarActiveColor: '#D4A017',                 // --accent (gold)
        pdEqLineColor: 'rgba(255, 255, 255, 0.40)',
        pdBadgeDiscountBg: 'rgba(46, 204, 113, 0.18)',
        pdBadgeDiscountText: '#27ae60',
        pdBadgePremiumBg: 'rgba(239, 83, 80, 0.18)',
        pdBadgePremiumText: '#e74c3c',
        pdBadgeEqBg: 'rgba(255, 255, 255, 0.12)',
        pdBadgeEqText: 'rgba(255, 255, 255, 0.65)',
    },
    light: {
        label: 'Light',
        chart: {
            // ADR-0066 PATCH 02c: dark text on white, neutral grid — keeps mirror discipline
            layout: { background: { color: '#FFFFFF' }, textColor: '#45455A' },     // --text-4 inverted role
            grid: {
                vertLines: { color: 'rgba(48, 54, 61, 0.10)' },
                horzLines: { color: 'rgba(48, 54, 61, 0.10)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(60, 60, 80, 0.40)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(60, 60, 80, 0.40)', width: 1, style: LineStyle.Dashed },
            },
        },
        // ADR-0066 token mirror (light variant): inverted scale; --accent-soft for AA contrast on white
        appBg: '#FFFFFF',
        hudBg: 'transparent',
        hudText: '#0D1117',                            // mirrors --bg as text on white
        hudBorder: 'transparent',
        statusBarBg: '#F4F6F8',                        // soft elev on white
        menuBg: 'rgba(255, 255, 255, 0.94)',
        menuBorder: 'rgba(48, 54, 61, 0.18)',
        drawingColor: '#1C2128',                       // --card as ink on white (high contrast)
        drawingRectFill: 'rgba(28, 33, 40, 0.08)',
        toolbarBg: 'rgba(244, 246, 248, 0.7)',
        toolbarBorder: 'rgba(48, 54, 61, 0.12)',
        toolbarHoverBg: 'rgba(13, 17, 23, 0.06)',
        toolbarActiveColor: '#B8881A',                 // --accent-soft (gold AA on white)
        pdEqLineColor: 'rgba(0, 0, 0, 0.35)',
        pdBadgeDiscountBg: 'rgba(39, 174, 96, 0.12)',
        pdBadgeDiscountText: '#1e8449',
        pdBadgePremiumBg: 'rgba(231, 76, 60, 0.12)',
        pdBadgePremiumText: '#c0392b',
        pdBadgeEqBg: 'rgba(0, 0, 0, 0.06)',
        pdBadgeEqText: 'rgba(0, 0, 0, 0.50)',
    },
};

export const THEME_NAMES: ThemeName[] = ['dark', 'black', 'light'];

// ─── Candle style presets (V3: CANDLE_STYLES, chart_adapter_lite.js:162-198) ───
//
// ADR-0066 PATCH 02e (Tier 8): Theme×Candle adaptation.
// CANDLE_STYLES holds `default` palette per style + optional per-theme overrides.
// resolveCandleStyle(style, theme) returns the merged CandleStyleDef.
// All consumers (engine.applyCandleStyle, App.svelte picker swatch) MUST go
// through resolveCandleStyle — never read CANDLE_STYLES[name].default directly.

export type CandleStyleName = 'classic' | 'gray' | 'stealth' | 'white' | 'hollow';

/** Color-only candle variant (no label). Per-theme overrides are Partial<CandleVariant>. */
export interface CandleVariant {
    upColor: string;
    downColor: string;
    borderUpColor: string;
    borderDownColor: string;
    wickUpColor: string;
    wickDownColor: string;
}

/** Public style entry: label + default variant + optional per-theme overrides. */
export interface CandleStyleByTheme {
    label: string;
    default: CandleVariant;
    dark?: Partial<CandleVariant>;
    black?: Partial<CandleVariant>;
    light?: Partial<CandleVariant>;
}

/** Resolved candle style (label + merged variant) — return type of resolveCandleStyle. */
export interface CandleStyleDef extends CandleVariant {
    label: string;
}

export const CANDLE_STYLES: Record<CandleStyleName, CandleStyleByTheme> = {
    classic: {
        label: 'Classic',
        default: {
            upColor: '#26a69a', downColor: '#ef5350',
            borderUpColor: '#26a69a', borderDownColor: '#ef5350',
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        },
    },
    gray: {
        label: 'Gray',
        default: {
            upColor: '#9aa0a6', downColor: 'rgba(255,255,255,0)',
            borderUpColor: '#9aa0a6', borderDownColor: '#5f6368',
            wickUpColor: '#9aa0a6', wickDownColor: '#5f6368',
        },
        // ADR-0066 §579: black bg makes #5f6368 down-border ~3.5:1 (WCAG fail).
        black: { borderDownColor: '#9aa0a6', wickDownColor: '#9aa0a6' },
    },
    stealth: {
        label: 'Stealth',
        default: {
            upColor: '#3a3f44', downColor: '#1f2327',
            borderUpColor: '#3a3f44', borderDownColor: '#1f2327',
            wickUpColor: '#3a3f44', wickDownColor: '#1f2327',
        },
        // ADR-0066 §580: dark grays on dark bg = invisible. Brighten for dark/black.
        dark: {
            upColor: '#7c8189', downColor: '#5d6268',
            borderUpColor: '#7c8189', borderDownColor: '#5d6268',
            wickUpColor: '#7c8189', wickDownColor: '#5d6268',
        },
        black: {
            upColor: '#7c8189', downColor: '#5d6268',
            borderUpColor: '#7c8189', borderDownColor: '#5d6268',
            wickUpColor: '#7c8189', wickDownColor: '#5d6268',
        },
    },
    white: {
        label: 'White',
        default: {
            upColor: '#e2e5e9', downColor: '#2f3338',
            borderUpColor: '#e2e5e9', borderDownColor: '#2f3338',
            wickUpColor: '#e2e5e9', wickDownColor: '#2f3338',
        },
        // ADR-0066 §581: #e2e5e9 on white bodies = ~1.06:1 invisible.
        light: {
            upColor: '#cfd2d6', downColor: '#2f3338',
            borderUpColor: '#cfd2d6', borderDownColor: '#2f3338',
            wickUpColor: '#cfd2d6', wickDownColor: '#2f3338',
        },
    },
    hollow: {
        label: 'Hollow',
        default: {
            upColor: 'rgba(255,255,255,0)', downColor: '#ef5350',
            borderUpColor: '#26a69a', borderDownColor: '#ef5350',
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        },
        // ADR-0066 §582: up border #26a69a on white reads ~2.4:1, marginal.
        light: {
            borderUpColor: '#1a8580', wickUpColor: '#1a8580',
            borderDownColor: '#c7423f', wickDownColor: '#c7423f',
        },
    },
};

export const CANDLE_STYLE_NAMES: CandleStyleName[] = ['classic', 'gray', 'stealth', 'white', 'hollow'];

/** Resolve a candle style for a given theme — merges default + per-theme overrides.
 *  Public API for all consumers (engine, picker swatch). */
export function resolveCandleStyle(
    style: CandleStyleName,
    theme: ThemeName,
): CandleStyleDef {
    const entry = CANDLE_STYLES[style];
    if (!entry) {
        // Fallback to classic if unknown style requested.
        const fallback = CANDLE_STYLES.classic;
        return { label: fallback.label, ...fallback.default };
    }
    return {
        label: entry.label,
        ...entry.default,
        ...(entry[theme] ?? {}),
    };
}

/** Volume bar alpha per theme. Light needs higher alpha to avoid ghosting on
 *  off-white bg (ADR-0066 Tier 8 §672, GAP-5). */
export const VOLUME_ALPHA_BY_THEME: Record<ThemeName, number> = {
    dark: 0.32,
    black: 0.36,
    light: 0.42,
};

// ─── localStorage persistence ───

const LS_THEME_KEY = 'v4_theme';
const LS_STYLE_KEY = 'v4_candle_style';

export function loadTheme(): ThemeName {
    try {
        const v = localStorage.getItem(LS_THEME_KEY);
        if (v && v in THEMES) return v as ThemeName;
    } catch { /* noop */ }
    return 'dark';
}

export function saveTheme(name: ThemeName): void {
    try { localStorage.setItem(LS_THEME_KEY, name); } catch { /* noop */ }
}

export function loadCandleStyle(): CandleStyleName {
    try {
        const v = localStorage.getItem(LS_STYLE_KEY);
        if (v && v in CANDLE_STYLES) return v as CandleStyleName;
    } catch { /* noop */ }
    return 'classic';
}

export function saveCandleStyle(name: CandleStyleName): void {
    try { localStorage.setItem(LS_STYLE_KEY, name); } catch { /* noop */ }
}

// ─── ADR-0007: CSS custom properties for cross-component theming ───

/** Встановити CSS custom properties на :root для DrawingToolbar + DrawingsRenderer.
 *  Викликається з App.svelte при зміні теми. */
export function applyThemeCssVars(name: ThemeName): void {
    const t = THEMES[name];
    if (!t) return;
    // ADR-0066 PATCH 02b: expose theme name to CSS so tokens.css can layer
    // [data-theme="black"] / [data-theme="light"] surface overrides (PATCH 02c).
    document.documentElement.dataset.theme = name;
    const s = document.documentElement.style;
    s.setProperty('--toolbar-btn-color', t.drawingColor);
    s.setProperty('--toolbar-bg', t.toolbarBg);
    s.setProperty('--toolbar-border', t.toolbarBorder);
    s.setProperty('--toolbar-hover-bg', t.toolbarHoverBg);
    s.setProperty('--toolbar-active-color', t.toolbarActiveColor);
    s.setProperty('--drawing-base-color', t.drawingColor);
    s.setProperty('--drawing-rect-fill', t.drawingRectFill);
}
