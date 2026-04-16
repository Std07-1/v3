// src/types.ts
// SSOT С‚РёРїРё РґР»СЏ UI v4 (Slices 0вЂ“5). РЈРєСЂР°С—РЅСЃСЊРєС– РєРѕРјРµРЅС‚Р°СЂС–. Р‘РµР· silent fallback.

export type T_MS = number;  // Р”РѕРјРµРЅРЅРёР№ С‡Р°СЃ: Unix milliseconds
export type T_SEC = number; // LWC С‡Р°СЃ: UTCTimestamp seconds

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
  end_ms?: T_MS; // open-ended РґРѕР·РІРѕР»РµРЅРѕ
  high: number;
  low: number;
  // ADR-0024 В§5.1: 'ob_bull'|'ob_bear'|'fvg_bull'|'fvg_bear'|'premium'|'discount'|
  // legacy: 'fvg'|'ob'|'liquidity'. ГњР±РµСЂРµР¶РµРјРѕ СЏРє string РґР»СЏ backward-compat (В§6.1a)
  kind: string;
  status?: string;   // 'active'|'tested'|'mitigated'|'partially_filled'|'filled'
  strength?: number; // 0.0вЂ“1.0
  // ADR-0024c Phase 2: cross-TF zone identification + Context Stack
  tf_s?: number;                // Origin TF (seconds)
  context_layer?: string;       // 'institutional'|'intraday'|'local' (Context Stack layer)
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
  kind?: string;     // ADR-0024b: СЂС–РІРµРЅСЊ kind РґР»СЏ per-kind styling (pdh, pdl, h1_h, eq_highs, ...)
  price: number;
  t_ms?: T_MS;      // РѕРїС†С–Р№РЅРѕ (С‡Р°СЃ С„РѕСЂРјСѓРІР°РЅРЅСЏ СЂС–РІРЅСЏ)
}

/** ADR-0041: Premium/Discount badge state (always-on when calc_enabled). */
export interface PdState {
  range_high: number;
  range_low: number;
  equilibrium: number;
  pd_percent: number;   // 0.0вЂ“100.0
  label: 'PREMIUM' | 'DISCOUNT' | 'EQ';
}

export interface SmcData {
  zones: SmcZone[];
  swings: SmcSwing[];
  levels: SmcLevel[];
  trend_bias?: string | null;  // F8: 'bullish'|'bearish'|null
  zone_grades?: Record<string, ZoneGradeInfo>;  // ADR-0029: confluence scoring
  bias_map?: Record<string, string>;  // ADR-0031: per-TF bias {"900":"bullish", ...}
  momentum_map?: Record<string, { b: number; r: number }>;  // Directional displacement count
  pd_state?: PdState | null;  // ADR-0041: P/D badge + EQ line
}

/** ADR-0029: zone confluence grade info (full frame only). */
export interface ZoneGradeInfo {
  score: number;      // 0-11
  grade: string;      // 'A+'|'A'|'B'|'C'
  factors: string[];  // e.g. ['sweep +2', 'htf_align +2']
}

/**
 * ADR-0024 В§5: Wire format С–РЅРєСЂРµРјРµРЅС‚Р°Р»СЊРЅРѕС— РґРµР»СЊС‚Рё SMC (WS delta frame).
 * Р’С–РґРїРѕРІС–РґР°С” SmcDelta.to_wire() РІ runtime/smc/smc_runner.py.
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

// -------------------- Narrative (ADR-0033) --------------------

/** ADR-0033: РѕРґРёРЅ actionable scenario РґР»СЏ С‚СЂРµР№РґРµСЂР° (max 2: primary + alternative). */
export interface ActiveScenario {
  zone_id: string;
  direction: 'long' | 'short';
  entry_desc: string;
  trigger: 'approaching' | 'in_zone' | 'triggered' | 'ready';
  trigger_desc: string;
  target_desc: string | null;    // null СЏРєС‰Рѕ target РЅРµРІС–РґРѕРјРёР№ (BH-4)
  invalidation: string;
}

/** ADR-0033 + ADR-0035: РїРѕРІРЅРёР№ narrative block РґР»СЏ РѕРґРЅРѕРіРѕ symbol+viewer_tf. */
// ADR-0049: Archi thesis layer (from NarrativeEnricher)
export interface ArchiThesis {
  thesis: string;
  conviction: 'high' | 'medium' | 'low';
  key_level: string;
  invalidation: string;
  freshness: 'fresh' | 'aging' | 'stale';
  updated_at_ms: number;
}

// ADR-0049: Archi presence status (from WakeEngine)
export interface ArchiPresence {
  status: string;           // "sleeping" | "watching" | "analyzing" | "active"
  focus: string;            // what Archi is focused on
  silence_h: number;        // hours since last analysis
  next_wake: string;        // human-readable next wake condition
  conditions: number;       // active wake conditions count
  accumulator: number;      // awareness accumulator score
  accumulator_threshold: number;
}

export interface NarrativeBlock {
  mode: 'trade' | 'wait';
  sub_mode: 'aligned' | 'reduced' | 'counter' | 'market_closed' | '';
  headline: string;
  bias_summary: string;
  scenarios: ActiveScenario[];    // max 2 (T-1)
  next_area: string;
  fvg_context: string;            // "" if none
  market_phase: 'trending_up' | 'trending_down' | 'ranging' | 'closed';
  warnings: string[];             // degraded signals (BH-4/BH-8)
  // ADR-0035: session context
  current_session?: string;       // "london" | "newyork" | "asia" | ""
  in_killzone?: boolean;          // true if inside killzone window
  session_context?: string;       // "London KZ active вЂ” high probability"
  // ADR-0049: Archi intelligence layer
  archi_thesis?: ArchiThesis;
  archi_presence?: ArchiPresence;
}

// -------------------- Shell (ADR-0036) --------------------

export type ShellStage = 'wait' | 'prepare' | 'ready' | 'triggered' | 'stayout';

export interface TfChip {
  tf_label: string;    // "D1" | "H4" | "H1" | "M15"
  direction: string;   // "bullish" | "bearish"
  chip_state: string;  // "normal" | "brk" | "cfl"
}

export interface TacticalStrip {
  alignment_type: string;            // "htf_aligned" | "mixed"
  alignment_direction: string | null; // "bullish" | "bearish" | null
  chips: TfChip[];
  tag_text: string;     // "РљРѕРЅС‚РµРєСЃС‚ С‡РёСЃС‚РёР№" | "H1 РїСЂРѕС‚Рё С‚СЂРµРЅРґСѓ"
  tag_variant: string;  // "ok_bull" | "ok_bear" | "warn" | "danger"
}

export interface MicroCard {
  mode_text: string;     // "Р§РµРєР°С”РјРѕ" | "Р“РѕС‚СѓС”РјРѕСЃСЊ" | "Р“РѕС‚РѕРІРёР№ РґРѕ РІС…РѕРґСѓ"
  why_text: string;      // bias_summary
  what_needed: string;   // trigger_desc or fallback
  what_cancels: string;  // invalidation or fallback
  warning: string | null;
}

export interface ShellPayload {
  stage: ShellStage;
  stage_label: string;    // "WAIT" | "SHORT В· READY" etc.
  stage_context: string;  // "Bearish HTF В· Inside supply В· Waiting CHoCH"
  micro_card: MicroCard;
  tactical_strip: TacticalStrip;
  signal: SignalSpec | null;  // ADR-0039: primary signal from signal engine
}

// -------------------- ADR-0039: Signal Engine --------------------
export interface SignalSpec {
  signal_id: string;
  zone_id: string;
  symbol: string;
  tf_s: number;
  direction: 'long' | 'short';
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  risk_reward: number;
  entry_method: string;
  entry_desc: string;
  confidence: number;       // 0вЂ“100
  confidence_factors: Record<string, number>;
  grade: string;
  state: string;            // pending|approaching|active|ready|invalidated|completed|expired
  state_reason: string;
  created_ms: number;
  updated_ms: number;
  bars_alive: number;
  session: string;
  in_killzone: boolean;
  warnings: string[];
}

export interface SignalAlert {
  signal_id: string;
  alert_type: string;
  headline: string;
  priority: string;
  ts_ms: number;
}

// -------------------- Drawings --------------------
export type DrawingType = 'hline' | 'trend' | 'rect';

export interface DrawingPoint {
  t_ms: T_MS;
  price: number;
}

export interface Drawing {
  id: string; // SSOT: UUID Р·РіРµРЅРµСЂРѕРІР°РЅРёР№ РєР»С–С”РЅС‚РѕРј
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
  | 'schema_mismatch'
  | 'server_error';

export interface UiWarning {
  code: UiWarningCode;
  kind: 'overlay' | 'drawing' | 'router' | 'action' | 'ws';
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
  | 'warming'
  | 'config'
  | 'error';

export interface RenderFrame {
  type: 'render_frame';
  frame_type: FrameType;

  symbol?: string;
  tf?: string;

  candles?: Candle[];
  zones?: SmcZone[];
  swings?: SmcSwing[];
  levels?: SmcLevel[];
  /** ADR-0024: С–РЅРєСЂРµРјРµРЅС‚Р°Р»СЊРЅС– Р·РјС–РЅРё SMC РІ delta РєР°РґСЂР°С… */
  smc_delta?: SmcDeltaWire;
  /** F8: trend bias Сѓ full/replay frames */
  trend_bias?: string | null;
  /** ADR-0029: confluence grade per zone (full + delta on complete bars, ADR-0042) */
  zone_grades?: Record<string, ZoneGradeInfo>;
  /** ADR-0031: per-TF trend bias map (full + delta on complete bars, ADR-0042) */
  bias_map?: Record<string, string>;
  /** Momentum: per-TF directional displacement (full + delta on complete bars, ADR-0042) */
  momentum_map?: Record<string, { b: number; r: number }>;
  /** ADR-0041: P/D badge state (full + delta on complete bars, ADR-0042) */
  pd_state?: PdState | null;
  /** ADR-0033+ADR-0035: narrative block (full frame + delta on complete bars) */
  narrative?: NarrativeBlock;
  /** ADR-0036: shell payload (full frame + delta on complete bars) */
  shell?: ShellPayload;
  /** ADR-0039: signal engine output (full frame) */
  signals?: SignalSpec[];
  /** ADR-0039: signal alerts on state transitions */
  signal_alerts?: SignalAlert[];
  /** ADR-0035: refreshed session levels in delta (full-replace session kinds) */
  session_levels?: SmcLevel[];
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
