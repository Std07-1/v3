// src/chart/themes.ts
// P3.11 Multi-theme + P3.12 Candle styles — SSOT визначення.
// V3 parity: chart_adapter_lite.js:102-153 (теми), :158-199 (стилі свічок).
// Persistence: localStorage ('v4_theme', 'v4_candle_style').

import { CrosshairMode, LineStyle } from 'lightweight-charts';

// ─── Theme definitions (V3: DARK_CHART_OPTIONS / DARK_GRAY_CHART_OPTIONS) ───

export type ThemeName = 'dark' | 'dark-gray' | 'blue';

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
    /** CSS фон для .app-layout (підтримка різних тем ззовні графіка) */
    appBg: string;
    /** Фон для HUD overlay */
    hudBg: string;
    /** Текстовий колір для HUD елементів */
    hudText: string;
    /** Бордер для HUD */
    hudBorder: string;
    /** Фон для StatusBar */
    statusBarBg: string;
}

export const THEMES: Record<ThemeName, ThemeDef> = {
    dark: {
        label: 'Dark',
        chart: {
            layout: { background: { color: 'transparent' }, textColor: '#d5d5d5' },
            grid: {
                vertLines: { color: 'rgba(43, 56, 70, 0.4)' },
                horzLines: { color: 'rgba(43, 56, 70, 0.4)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(213, 213, 213, 0.35)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(213, 213, 213, 0.35)', width: 1, style: LineStyle.Dashed },
            },
        },
        appBg: '#131722',
        hudBg: 'rgba(30, 34, 45, 0.72)',
        hudText: '#d1d4dc',
        hudBorder: 'rgba(255, 255, 255, 0.06)',
        statusBarBg: '#1e222d',
    },
    'dark-gray': {
        label: 'Gray',
        chart: {
            layout: { background: { color: '#2a3036' }, textColor: '#d0d3d8' },
            grid: {
                vertLines: { color: 'rgba(90, 96, 104, 0.35)' },
                horzLines: { color: 'rgba(90, 96, 104, 0.35)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(178, 206, 247, 0.45)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(42, 46, 52, 0.45)', width: 1, style: LineStyle.Dashed },
            },
        },
        appBg: '#2a3036',
        hudBg: 'rgba(50, 56, 64, 0.78)',
        hudText: '#d0d3d8',
        hudBorder: 'rgba(255, 255, 255, 0.08)',
        statusBarBg: '#333940',
    },
    blue: {
        label: 'Blue',
        chart: {
            layout: { background: { color: '#0d1b2a' }, textColor: '#c8d6e5' },
            grid: {
                vertLines: { color: 'rgba(30, 72, 120, 0.35)' },
                horzLines: { color: 'rgba(30, 72, 120, 0.35)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(74, 144, 217, 0.45)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(74, 144, 217, 0.45)', width: 1, style: LineStyle.Dashed },
            },
        },
        appBg: '#0d1b2a',
        hudBg: 'rgba(13, 27, 42, 0.82)',
        hudText: '#c8d6e5',
        hudBorder: 'rgba(74, 144, 217, 0.15)',
        statusBarBg: '#122238',
    },
};

export const THEME_NAMES: ThemeName[] = ['dark', 'dark-gray', 'blue'];

// ─── Candle style presets (V3: CANDLE_STYLES, chart_adapter_lite.js:162-198) ───

export type CandleStyleName = 'classic' | 'gray' | 'stealth' | 'white' | 'hollow';

export interface CandleStyleDef {
    label: string;
    upColor: string;
    downColor: string;
    borderUpColor: string;
    borderDownColor: string;
    wickUpColor: string;
    wickDownColor: string;
}

export const CANDLE_STYLES: Record<CandleStyleName, CandleStyleDef> = {
    classic: {
        label: 'Classic',
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    },
    gray: {
        label: 'Gray',
        upColor: '#9aa0a6', downColor: 'rgba(255,255,255,0)',
        borderUpColor: '#9aa0a6', borderDownColor: '#5f6368',
        wickUpColor: '#9aa0a6', wickDownColor: '#5f6368',
    },
    stealth: {
        label: 'Stealth',
        upColor: '#3a3f44', downColor: '#1f2327',
        borderUpColor: '#3a3f44', borderDownColor: '#1f2327',
        wickUpColor: '#3a3f44', wickDownColor: '#1f2327',
    },
    white: {
        label: 'White',
        upColor: '#e2e5e9', downColor: '#2f3338',
        borderUpColor: '#e2e5e9', borderDownColor: '#2f3338',
        wickUpColor: '#e2e5e9', wickDownColor: '#2f3338',
    },
    hollow: {
        label: 'Hollow',
        upColor: 'rgba(255,255,255,0)', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    },
};

export const CANDLE_STYLE_NAMES: CandleStyleName[] = ['classic', 'gray', 'stealth', 'white', 'hollow'];

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
