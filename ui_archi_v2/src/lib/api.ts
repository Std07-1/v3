const TOKEN_KEY = 'archi_token';

export function getToken(): string {
    return localStorage.getItem(TOKEN_KEY) ?? '';
}

export function setToken(token: string): void {
    localStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearToken(): void {
    localStorage.removeItem(TOKEN_KEY);
}

async function apiFetch<T>(
    path: string,
    params?: Record<string, string | number>,
    init?: RequestInit,
): Promise<T> {
    const token = getToken();
    const url = new URL(path, window.location.origin);
    if (params) {
        for (const [k, v] of Object.entries(params)) {
            url.searchParams.set(k, String(v));
        }
    }
    const headers: Record<string, string> = {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    };
    const res = await fetch(url.toString(), {
        ...init,
        headers: { ...headers, ...(init?.headers as Record<string, string> ?? {}) },
    });
    if (res.status === 401) throw new ApiError(401, 'unauthorized');
    if (res.status === 204) return {} as T;
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.error ?? 'request_failed');
    }
    return res.json();
}

export class ApiError extends Error {
    constructor(public status: number, public code: string) {
        super(`API error ${status}: ${code}`);
    }
}

/** Redis HGETALL returns all values as strings. Transform numeric fields. */
const AGENT_STATE_NUMERIC_KEYS = [
    'ts_ms', 'next_wake_ms', 'budget_today_usd', 'budget_limit_usd',
    'budget_pct', 'calls_today', 'messages_sent_today',
] as const;

function transformAgentState(raw: Record<string, unknown>): import('./types').AgentState {
    const result = { ...raw };
    for (const key of AGENT_STATE_NUMERIC_KEYS) {
        if (key in result && result[key] != null) {
            const n = Number(result[key]);
            if (!Number.isNaN(n)) result[key] = n;
        }
    }
    return result as import('./types').AgentState;
}

export const api = {
    thinking: (limit = 50, offset = 0) =>
        apiFetch<import('./types').ThinkingResponse>('/api/archi/thinking', { limit, offset }),

    feed: (limit = 100) =>
        apiFetch<import('./types').FeedResponse>('/api/archi/feed', { limit }),

    directives: (brief = true) =>
        apiFetch<import('./types').Directives>('/api/archi/directives', { brief: brief ? '1' : '0' }),

    relationship: () =>
        apiFetch<import('./types').RelationshipMemo>('/api/archi/relationship'),

    agentState: async () =>
        transformAgentState(await apiFetch<Record<string, unknown>>('/api/agent/state')),

    chatHistory: (limit = 50) =>
        apiFetch<import('./types').ChatHistory>('/api/archi/chat', { limit }),

    chatSend: (message: string) =>
        apiFetch<{ ok: boolean; message: import('./types').ChatMessage; archi_response?: import('./types').ChatMessage }>(
            '/api/archi/chat',
            undefined,
            { method: 'POST', body: JSON.stringify({ message }) },
        ),

    logs: (lines = 80, level: 'all' | 'error' | 'warn' = 'all') =>
        apiFetch<import('./types').LogsResponse>('/api/archi/logs', { lines, level }),

    ownerNote: () =>
        apiFetch<import('./types').OwnerNote>('/api/archi/owner-note'),

    saveOwnerNote: (note: { text: string; mood?: string; status?: string }) =>
        apiFetch<import('./types').OwnerNote & { ok: boolean }>(
            '/api/archi/owner-note',
            undefined,
            { method: 'POST', body: JSON.stringify(note) },
        ),

    // ADR-028 P3 J5: approve or reject a pending improvement_proposal
    reviewProposal: (id: string, approved: boolean) =>
        apiFetch<{ ok: boolean; id: string; approved: boolean }>(
            '/api/archi/proposals/review',
            undefined,
            { method: 'POST', body: JSON.stringify({ id, approved }) },
        ),

    // ADR-0053 S4: publish a reaction (like/pin/star) to feedback:chat stream
    chatReact: (msg_id: string, type: 'like' | 'pin' | 'star', action: 'add' | 'remove') =>
        apiFetch<{ ok: boolean; entry_id: string }>(
            '/api/archi/chat/react',
            undefined,
            { method: 'POST', body: JSON.stringify({ msg_id, type, action }) },
        ),
};
