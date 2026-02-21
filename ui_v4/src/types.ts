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
  kind: 'fvg' | 'ob' | 'liquidity';
}

export interface SmcSwing {
  id: string;
  a: SmcPoint;
  b: SmcPoint;
  label?: string;
}

export interface SmcLevel {
  id: string;
  price: number;
  t_ms?: T_MS;      // опційно (якщо треба прив'язка)
  color?: string;
}

export interface SmcData {
  zones: SmcZone[];
  swings: SmcSwing[];
  levels: SmcLevel[];
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
  kind: 'overlay' | 'drawing' | 'router';
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
