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

export interface MarketMentalModel {
    macro_bias?: string;
    macro_reasoning?: string;
    structure_bias?: string;
    structure_reasoning?: string;
    key_levels?: string[];
    current_narrative?: string;
    what_changed?: string;
    what_watching?: string;
    updated_at?: number;
    assessment_count?: number;
}

export interface DecisionEntry {
    id: string;
    decision: string;
    alternatives?: string[];
    reasoning?: string;
    category?: string;
    linked_scenario_id?: string;
    created_at?: number;
}

export interface TrackedPromise {
    id: string;
    text: string;
    status?: "active" | "kept" | "broken" | "expired";
    created_at?: number;
    resolved_at?: number;
    resolution_note?: string;
    check_count?: number;
}

export interface MetaCognition {
    scenario_accuracy?: number;
    scenarios_evaluated?: number;
    avg_confidence_error?: number;
    recurring_lessons?: string[];
    mood_streak?: number;
    mood_streak_type?: string;
    tracked_promises?: TrackedPromise[];
    last_self_audit_at?: number;
    audit_count?: number;
    accuracy_recent?: number;
    accuracy_previous?: number;
    learning_trend?: string;
    confidence_trend?: string;
}

export interface Directives {
    mode?: string;
    focus_symbol?: string;
    active_scenario?: string;
    mood?: string;
    inner_thought?: string;
    bias_map?: Record<string, unknown>;
    market_mental_model?: MarketMentalModel | string;
    metacognition?: MetaCognition | null;
    decision_log?: DecisionEntry[];
    token_usage_today?: number;
    kill_switch_active?: boolean;
    economy_mode_active?: boolean;
    user_signal?: string;
    observation_enabled?: boolean;
    observation_interval_minutes?: number;
    last_observation_ts?: number;
    next_check_reason?: string;
    last_market_status?: string;
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
// Backend returns all values as strings (Redis HGETALL).
// api.ts transforms numeric fields at fetch time.
export interface AgentState {
    ts_ms?: number;
    health?: string;
    model_current?: string;
    model_last_call?: string;
    next_wake_ms?: number;
    next_wake_reason?: string;
    budget_today_usd?: number;
    budget_limit_usd?: number;
    budget_pct?: number;
    calls_today?: number;
    messages_sent_today?: number;
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

// ── ADR-044: Workspace Item + ADR-045: Task Queue fields ──
export interface WorkspaceItem {
    id: string;
    kind: "pin" | "note" | "briefing" | "scenario_map" | "alert";
    title: string;
    content: string;
    priority: number;
    created_at: number;
    expires_at?: number | null;
    tags: string[];
    pinned: boolean;
    status: "active" | "archived" | "superseded";
    superseded_by?: string;
    linked_scenario_id?: string;
    // ADR-045: Workspace-as-TaskQueue
    next_step?: string | null;          // Plan for next wake (null → passive item)
    progress_log?: string[];            // Chronological steps (max 5, oldest-drop)
    wake_condition_id?: string | null;  // FK → wake_conditions[*].id
}

export interface ChatHandoff {
    id: string;
    source: "feed" | "thinking" | "relationship" | "mind" | "logs";
    icon: string;
    title: string;
    summary: string;
    prompt: string;
    ts_ms?: number;
    symbol?: string;
}

// ── Chat Message ──
export interface ChatMessage {
    id: string;
    role: "user" | "archi";
    text: string;
    ts_ms: number;
    source?: "web" | "telegram";
    /** ADR-0053: optional action suggestions rendered as chips under an archi bubble. */
    chips?: string[];
    /** ADR-0053: true while a streaming reply is still being appended. */
    streaming?: boolean;
}

// ── ADR-028 P3 J5: Self-Improvement Proposal ──
export interface ImprovementProposal {
    id: string;
    ts: number;
    type: "add_rule" | "modify_rule" | "remove_rule";
    proposed_rule: string;
    evidence: string;
    reasoning: string;
    alternatives_considered?: string[];
    status: "pending" | "approved" | "rejected";
    resolved_at?: number;
}

export interface ChatHistory {
    messages: ChatMessage[];
    total: number;
}

// ── Log Entry ──
export interface LogLine {
    text: string;
    level: "ERROR" | "WARN" | "INFO" | "DEBUG";
}

export interface LogsResponse {
    lines: LogLine[];
    source: string;
    total: number;
    error?: string;
}

// ── Owner Note ──
export interface OwnerNote {
    text: string;
    mood: string;
    status: string;
    updated_at: number | string;
}

// ── SSE stream message ──
export interface StreamMessage {
    type: "feed" | "directives";
    data: FeedEvent | Directives;
}
