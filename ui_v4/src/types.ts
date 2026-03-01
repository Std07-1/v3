// src/types.ts
// SSOT типи для UI v4 (Slices 0–5). Українські коментарі. Без silent fallback.

export type T_MS = number;  // Доменний час: Unix milliseconds
export type T_SEC = number; // LWC час: UTCTimestamp seconds

// -------------------- Bars --------------------
export interface Candle {
  t_ms: T_MS;
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number;
}

// -------------------- SMC overlays --------------------
export interface SmcPoint {
  t_ms: T_MS;
  price: number;
}

export interface SmcZone {
  id: string;
  start_ms: T_MS;
  end_ms?: T_MS; // open-ended дозволено
  high: number;
  low: number;
  // ADR-0024 §5.1: 'ob_bull'|'ob_bear'|'fvg_bull'|'fvg_bear'|'premium'|'discount'|
  // legacy: 'fvg'|'ob'|'liquidity'. Üбережемо як string для backward-compat (§6.1a)
  kind: string;
  status?: string;   // 'active'|'tested'|'mitigated'|'partially_filled'|'filled'
  strength?: number; // 0.0–1.0
}

export interface SmcSwing {
  id: string;
  kind: string;     // F7: 'hh'|'hl'|'lh'|'ll'|'bos_bull'|'bos_bear'|'choch_bull'|'choch_bear'|...
  time_ms: T_MS;
  price: number;
  label?: string;
}

export interface SmcLevel {
  id: string;
  kind?: string;     // ADR-0024b: рівень kind для per-kind styling (pdh, pdl, h1_h, eq_highs, ...)
  price: number;
  t_ms?: T_MS;      // опційно (час формування рівня)
}

export interface SmcData {
  zones: SmcZone[];
  swings: SmcSwing[];
  levels: SmcLevel[];
  trend_bias?: string | null;  // F8: 'bullish'|'bearish'|'neutral'|null
}

/**
 * ADR-0024 §5: Wire format інкрементальної дельти SMC (WS delta frame).
 * Відповідає SmcDelta.to_wire() в runtime/smc/smc_runner.py.
 */
export interface SmcDeltaWire {
  new_zones: SmcZone[];
  mitigated_zone_ids: string[];
  updated_zones: SmcZone[];
  new_swings: SmcSwing[];
  new_levels: SmcLevel[];
  removed_level_ids: string[];
  trend_bias: string | null;
}

// -------------------- Drawings --------------------
export type DrawingType = 'hline' | 'trend' | 'rect';

export interface DrawingPoint {
  t_ms: T_MS;
  price: number;
}

export interface Drawing {
  id: string; // SSOT: UUID згенерований клієнтом
  type: DrawingType;
  points: DrawingPoint[];
  meta?: {
    color?: string;
    lineWidth?: number;
    locked?: boolean;
  };
}

export type ActiveTool = 'hline' | 'trend' | 'rect' | 'eraser' | null;

// -------------------- Warnings --------------------
export type UiWarningCode =
  | 'overlay_coord_null'
  | 'drawing_coord_null'
  | 'drawing_state_inconsistent'
  | 'stale_frame'
  | 'schema_mismatch';

export interface UiWarning {
  code: UiWarningCode;
  kind: 'overlay' | 'drawing' | 'router' | 'action';
  id: string;
  details: string;
}

// -------------------- WS protocol --------------------
export type FrameType =
  | 'full'
  | 'delta'
  | 'scrollback'
  | 'drawing_ack'
  | 'replay'
  | 'heartbeat'
  | 'warming';

export interface RenderFrame {
  type: 'render_frame';
  frame_type: FrameType;

  symbol?: string;
  tf?: string;

  candles?: Candle[];
  zones?: SmcZone[];
  swings?: SmcSwing[];
  levels?: SmcLevel[];
  /** ADR-0024: інкрементальні зміни SMC в delta кадрах */
  smc_delta?: SmcDeltaWire;
  /** F8: trend bias у full/replay frames */
  trend_bias?: string | null;
  drawings?: Drawing[];

  replay?: {
    mode: 'off' | 'on';
    pos_ms: T_MS;
    playing: boolean;
  };

  meta: {
    schema_v: 'ui_v4_v2';
    seq: number;
    server_ts_ms: T_MS;

    status: 'connecting' | 'live' | 'warming' | 'error';
    latency_ms?: number;
    ready_pct?: number;

    // server-originated warnings
    warnings: string[];
  };
}

// -------------------- WS actions --------------------
export type WsAction =
  | { action: 'switch'; symbol: string; tf: string }
  | { action: 'scrollback'; to_ms: number }
  | { action: 'drawing_add'; drawing: Drawing }
  | { action: 'drawing_update'; drawing: Drawing }
  | { action: 'drawing_remove'; id: string }
  | { action: 'overlay_toggle'; layer: string; visible: boolean }
  | { action: 'replay_seek'; to_ms: number }
  | { action: 'replay_step'; delta_bars: number }
  | { action: 'replay_play' }
  | { action: 'replay_pause' }
  | { action: 'replay_exit' };
