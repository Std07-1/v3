/**
 * ADR-0104 §3.7 (S2c): вигляд рівня за станом від сервера (`SmcLevel.state`), а не за kind.
 *
 * Kind лишає колір і назву (родина, TF — LEVEL_STYLES в OverlayRenderer), стан задає рисунок лінії:
 *   fixed   — суцільна лінія повної ваги: період завершено, рівень уже не зсунеться;
 *   forming — тонша, прозоріша, штрих і «…» у підписі: період іде, рівень ще може зсунутись.
 * Чому окремий модуль: чисте правило представлення, яке перевіряється тестом без canvas.
 * Ліквідність (EQH/EQL) лишає свій пунктир — це ознака родини, а не періоду. Без `state` (стара відповідь сервера)
 * або зі станом, для якого вигляду ще немає (swept — S8, no_data — S3), — вигляд kind, як раніше.
 */

import type { SmcLevel } from '../../types';

export type LevelStyle = { color: string; dash: number[]; width: number; alpha: number; label: string; fontSize: number };

const FIXED_WIDTH_MIN = 1.5;    // завершений рівень не тонший за PDH/PDL
const FIXED_ALPHA_MIN = 0.8;
const FORMING_DASH = [3, 2];
const FORMING_WIDTH = 1.0;
const FORMING_ALPHA_FACTOR = 0.75;  // «прозоріша» відносно кольору kind
const FORMING_MARK = '…';           // позначка руху в підписі

export function applyLevelState(style: LevelStyle, level: SmcLevel): LevelStyle {
  if (level.family === 'liquidity') return style;
  if (level.state === 'fixed') {
    return { ...style, dash: [], width: Math.max(style.width, FIXED_WIDTH_MIN), alpha: Math.max(style.alpha, FIXED_ALPHA_MIN) };
  }
  if (level.state === 'forming') {
    return {
      ...style,
      dash: FORMING_DASH,
      width: FORMING_WIDTH,
      alpha: style.alpha * FORMING_ALPHA_FACTOR,
      label: style.label + FORMING_MARK,
    };
  }
  return style;
}
