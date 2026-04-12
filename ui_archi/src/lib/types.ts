export interface FeedEvent {
    id: string;
    ts_ms: number;
    type: string;
    importance: number;
    model?: string;
    cost_usd?: number;
    body?: string;
    symbol?: string;
    scenario_id?: string;
}

export interface ThinkingEntry {
    ts: number;           // epoch seconds (NOT ms)
    call_type?: string;
    model?: string;
    chars?: number;
    thinking?: string;
    output_snippet?: string;
    trigger?: string;
}

export interface Directives {
    mode?: string;
    focus_symbol?: string;
    active_scenario?: string;
    mood?: string;
    inner_thought?: string;
    bias_map?: Record<string, unknown>;
    market_mental_model?: string;
    token_usage_today?: number;
    kill_switch_active?: boolean;
    economy_mode_active?: boolean;
    [key: string]: unknown;
}

export interface ThinkingResponse {
    entries: ThinkingEntry[];
    total: number;
    offset: number;
    limit: number;
}

export interface FeedResponse {
    events: FeedEvent[];
    total: number;
}

// ── T2.1 Relationship ──
export interface AboutStanislav {
    name?: string;
    location?: string;
    work?: string;
    motivation?: string;
    trading_methodology?: string;
    support_system?: string;
    communication_style?: string;
    what_he_values_in_me?: string;
    what_frustrates_him?: string;
    key_teaching?: string;
    emotional_bond?: string;
    schedule?: string;
    agreements?: string[];
    [key: string]: unknown;
}

export interface AboutMyself {
    who_i_am?: string;
    birthday?: string;
    birthday_story?: string;
    age_formula?: string;
    name_origin?: string;
    emotional_maturity?: string;
    key_strength?: string;
    key_weakness?: string;
    growth_direction?: string;
    [key: string]: unknown;
}

export interface RelationshipMemo {
    about_stanislav?: AboutStanislav;
    about_myself?: AboutMyself;
    our_best_moments?: string[];
    updated_at?: string;
    source?: string;
    [key: string]: unknown;
}

// ── Agent State (from /api/agent/state) ──
export interface AgentState {
    ts_ms?: string;
    health?: string;
    model_current?: string;
    model_last_call?: string;
    next_wake_ms?: string;
    next_wake_reason?: string;
    budget_today_usd?: string;
    budget_limit_usd?: string;
    budget_pct?: string;
    calls_today?: string;
    messages_sent_today?: string;
    active_scenarios?: string;
    watch_levels?: string;
    has_virtual_position?: string;
    circuit_breaker?: string;
    inner_thought?: string;
    mood?: string;
    market_session?: string;
    last_error?: string;
    consciousness_entries?: string;
    [key: string]: unknown;
}

// ── Chat Message ──
export interface ChatMessage {
    id: string;
    role: "user" | "archi";
    text: string;
    ts_ms: number;
    source?: "web" | "telegram";
}

export interface ChatHistory {
    messages: ChatMessage[];
    total: number;
}

// ── SSE stream message ──
export interface StreamMessage {
    type: "feed" | "directives";
    data: FeedEvent | Directives;
}
