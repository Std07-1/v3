// src/chart/lwc.ts
// Thin re-export для ChartEngine. SSOT: engine.ts, themes.ts
export { ChartEngine, TF_TO_S, type CrosshairData, type ThemeName, type CandleStyleName } from './engine';
export {
    THEMES, THEME_NAMES, CANDLE_STYLES, CANDLE_STYLE_NAMES,
    loadTheme, loadCandleStyle,
} from './themes';
