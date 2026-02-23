// src/chart/themes.ts
// P3.11 Multi-theme + P3.12 Candle styles — SSOT визначення.
// V3 parity: chart_adapter_lite.js:102-153 (теми), :158-199 (стилі свічок).
// Persistence: localStorage ('v4_theme', 'v4_candle_style').

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
        hudBg: 'transparent',
        hudText: '#d1d4dc',
        hudBorder: 'transparent',
        statusBarBg: '#1e222d',
        menuBg: 'rgba(30, 34, 45, 0.92)',
        menuBorder: 'rgba(255, 255, 255, 0.08)',
    },
    black: {
        label: 'Black',
        chart: {
            layout: { background: { color: '#000000' }, textColor: '#d5d5d5' },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(255, 255, 255, 0.25)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(255, 255, 255, 0.25)', width: 1, style: LineStyle.Dashed },
            },
        },
        appBg: '#000000',
        hudBg: 'transparent',
        hudText: '#d1d4dc',
        hudBorder: 'transparent',
        statusBarBg: '#111111',
        menuBg: 'rgba(20, 20, 20, 0.92)',
        menuBorder: 'rgba(255, 255, 255, 0.08)',
    },
    light: {
        label: 'Light',
        chart: {
            layout: { background: { color: '#ffffff' }, textColor: '#333333' },
            grid: {
                vertLines: { color: 'rgba(0, 0, 0, 0.06)' },
                horzLines: { color: 'rgba(0, 0, 0, 0.06)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(0, 0, 0, 0.2)', width: 1, style: LineStyle.Dashed },
                horzLine: { color: 'rgba(0, 0, 0, 0.2)', width: 1, style: LineStyle.Dashed },
            },
        },
        appBg: '#ffffff',
        hudBg: 'transparent',
        hudText: '#131722',
        hudBorder: 'transparent',
        statusBarBg: '#f0f0f0',
        menuBg: 'rgba(255, 255, 255, 0.92)',
        menuBorder: 'rgba(0, 0, 0, 0.08)',
    },
};

export const THEME_NAMES: ThemeName[] = ['dark', 'black', 'light'];

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
