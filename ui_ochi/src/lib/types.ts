/**
 * Контракт «Очі Арчі» — wire-типи read-side спостережуваності агента.
 *
 * SSOT сервера: runtime/ws/wake_cards.py (join wake_log + trace + thinking) та
 * /api/archi/now (presence + directives + thesis + armed). Фронт = dumb renderer
 * (X28): показує value як є, домен (category/alert/Δ/delta_pct/thesis-age/stale)
 * рахує бекенд. УСІ enrichment-поля тут optional — старі wakes не мають
 * trace/thinking (companion-логи додані пізніше, trader-v3/ADR-097).
 */

// ═══════════════════════════════════════════════════════════════
// WAKE FILM — /api/archi/wakes
// ═══════════════════════════════════════════════════════════════

/** trace-компаньйон (v3_wake_trace.jsonl) — дзеркало того, що Арчі реально побачив. */
export interface WakeTrace {
    /** Повний mirror-блок (те, що влетіло в контекст Арчі). null → недоступний. */
    mirror?: string | null;
    /** true → mirror = light-версія (без важких секцій). */
    mirror_light?: boolean;
    ack?: string;
    emit_warning?: string;
    /** Текст надісланого повідомлення (може бути порожнім при msg_len==0). */
    message?: string;
}

/**
 * Одна картка кіноплівки = усі поля v3_wake_log.jsonl verbatim
 * + enrichment {category, alert, trace, thinking, thinking_ts}.
 * Захисно: усе крім `ts` optional — старі рядки логу неповні.
 */
export interface WakeCard {
    /** epoch seconds (NOT ms). Єдине гарантоване поле. */
    ts: number;
    /** Стабільний ключ dedupe. Порожній у старих — fallback на ts. */
    wake_id?: string;
    reason?: string;
    /** platform_wake | proactive | reactive | ... */
    call_type?: string;
    model?: string;
    /** input tokens */
    in?: number;
    /** output tokens */
    out?: number;
    cache_read?: number;
    truncated?: boolean;
    cost?: number;
    ack?: string;
    emit_warning?: string;
    /** к-сть watch-рівнів на момент пробудження */
    watch?: number;
    wake_conditions?: number;
    wake_at?: number;
    scenario?: string | null;
    /** virtual position snapshot (форма визначається ботом) */
    vp?: unknown | null;
    delivered?: boolean;
    /** довжина надісланого повідомлення; 0 → «мовчазне пробудження» */
    msg_len?: number;
    price?: number;

    // ── enrichment (server-side, X28) ──
    /** класифікація тригера (watch | heartbeat | price_cross | ...) — з бекенда */
    category?: string;
    /** true → «гучне» пробудження (алярм-бейдж) — з бекенда */
    alert?: boolean;
    /** join по wake_id (fallback exact-ts). null → трейсу немає. */
    trace?: WakeTrace | null;
    /** nearest-join з thinking-архіву (|Δ|≤600s + call_type). null → не знайдено. */
    thinking?: string | null;
    thinking_ts?: number | null;
}

export interface WakesResponse {
    wakes: WakeCard[];
    total: number;
    /** ts найстарішої картки у відповіді — курсор для keyset before_ts. */
    oldest_ts: number;
}

// ═══════════════════════════════════════════════════════════════
// PRESENCE — /api/archi/now
// ═══════════════════════════════════════════════════════════════

/** agent:state HGETALL — Redis повертає все рядками. Фронт форматує на місці. */
export interface NowState {
    ts_ms?: string;
    health?: string;
    model_current?: string;
    next_wake_ms?: string;
    next_wake_reason?: string;
    budget_today_usd?: string;
    budget_pct?: string;
    calls_today?: string;
    has_virtual_position?: string;
    circuit_breaker?: string;
    inner_thought?: string;
    mood?: string;
    market_session?: string;
    last_error?: string;
    [key: string]: string | undefined;
}

/** Активний сценарій Арчі (те, що він зараз відпрацьовує). */
export interface ActiveScenario {
    id: string;
    /** short | long */
    direction: string;
    thesis: string;
    entry_zone_low: number;
    entry_zone_high: number;
    invalidation: number;
    targets: number[];
    confidence: number;
    /** waiting | active | ... */
    status: string;
}

export interface ThoughtHistoryItem {
    ts: number;
    text: string;
    mood?: string;
}

export interface WatchLevel {
    id: string;
    price: number;
    /** above | below */
    direction: string;
}

export interface WakeAtItem {
    id: string;
    time_epoch: number;
    reason: string;
}

export interface WakeCondition {
    id: string;
    /** price_cross | session_open | volatility_spike | max_silence | ... */
    kind: string;
    params: Record<string, unknown>;
}

export interface TokenUsageToday {
    input: number;
    output: number;
}

export interface NowDirectives {
    mood?: string;
    inner_thought?: string;
    active_scenario?: ActiveScenario | null;
    virtual_position?: unknown | null;
    kill_switch_active?: boolean;
    consecutive_errors?: number;
    budget_strategy?: string;
    next_check_minutes?: number;
    next_check_reason?: string;
    thought_history?: ThoughtHistoryItem[];
    watch_levels?: WatchLevel[];
    wake_at?: WakeAtItem[];
    wake_conditions?: WakeCondition[];
    last_emit_warning?: string;
    token_usage_today?: TokenUsageToday;
    [key: string]: unknown;
}

export interface NowThesis {
    thesis: string;
    conviction: string;
    key_level: string;
    invalidation: string;
    key_level_price: number;
    invalidation_price: number;
    updated_at_ms: number;
    /** вік тези у ms — рахує сервер (X28). */
    age_ms: number;
}

/**
 * Армований тригер, готовий спрацювати. delta / delta_pct рахує СЕРВЕР (X28),
 * фронт лише форматує число. Масив приходить sorted nearest-first.
 */
export interface ArmedTrigger {
    level: number;
    /** above | below */
    direction: string;
    /** wake_condition | watch_level */
    source: string;
    kind: string;
    id: string;
    /** знакова відстань до ціни (server-computed) */
    delta: number;
    /** те саме у частках (0.01 = 1%) */
    delta_pct: number;
}

export interface NowResponse {
    symbol: string;
    generated_ms: number;
    price: number;
    /** true → agent:state старіший 15хв (сервер вирішує). */
    stale: boolean;
    state: NowState;
    directives: NowDirectives;
    thesis: NowThesis | null;
    armed: ArmedTrigger[];
    /** причини деградації read-side (redis_not_configured | state_stale | ...). */
    degraded: string[];
}
