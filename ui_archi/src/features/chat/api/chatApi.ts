/**
 * chatApi — Типізований HTTP-шар для розмови з trader-v3 ботом.
 *
 * Обгортка над `src/lib/api.ts` (базовий fetch + Bearer).
 * Дає dedicated поверхню контракту для майбутнього (CSRF, nonce, retry)
 * без правок у lib/api.ts глобально.
 *
 * Security (див. THREAT_MODEL_CHAT §3):
 *   - T1 (XSS): sanitize на render-боці (lib/sanitize.ts), не тут
 *   - T2 (Token theft): Bearer додається у lib/api.ts apiFetch
 *   - T3 (Rate abuse): клієнтський throttle у lib/rateLimit.ts (S7)
 *   - T4 (CSRF): додається у S8 (csrfToken параметр)
 *   - T5 (Prompt injection через handoff): whitelist у ContextRail (S5)
 *
 * I7 (Degraded-but-loud): усі помилки re-throw як ApiError — UI показує banner/toast.
 */
import { api, ApiError, getToken } from "../../../lib/api";
import type { ChatHistory, ChatMessage } from "../../../lib/types";

export interface SendResult {
    /** Server-assigned message (id + real ts_ms). */
    message: ChatMessage;
    /** Одразу повернута відповідь Arхі (reactive mode), якщо є. */
    archiResponse?: ChatMessage;
}

/**
 * Надіслати повідомлення. Throws ApiError на 4xx/5xx.
 *
 * @param text Сирий текст (markdown допускається). Sanitize — на render-боці.
 *             Server-side sanitize + length cap — у S8 (runtime/api/sanitizer.py).
 */
export async function sendMessage(text: string): Promise<SendResult> {
    const res = await api.chatSend(text);
    return {
        message: res.message,
        archiResponse: res.archi_response,
    };
}

/**
 * Завантажити історію чату (limit останніх повідомлень, sorted ASC by ts_ms).
 */
export async function loadHistory(limit = 80): Promise<ChatHistory> {
    return api.chatHistory(limit);
}

/**
 * Відкрити SSE stream для typing-effect на final reply (ADR-0053 S3).
 *
 * Fake-stream: сервер чекає final reply у Redis LIST (той самий шлях що
 * LRANGE обслуговує fast-poll), розбиває готовий text на chunks і віддає
 * як `event: delta` frames. UX overlay — якщо EventSource падає, fast-poll
 * все одно дотягне фінальне повідомлення (I7 degraded-but-loud).
 *
 * Auth: Bearer не можна передати у headers EventSource (browser обмеження),
 * тому token йде як `?token=` (сервер приймає обидва варіанти через _archi_auth).
 *
 * @param afterId  id user message — anchor, щоб уникнути race зі старою реплікою
 * @param timeoutS cap на очікування reply (5..240, default 120s)
 * @returns EventSource або null, якщо API недоступне (SSR / Node тест)
 */
export function openChatStream(afterId: string, timeoutS = 120): EventSource | null {
    if (typeof EventSource === "undefined") return null;
    const url = new URL("/api/archi/chat/stream", window.location.origin);
    url.searchParams.set("after_id", afterId);
    url.searchParams.set("timeout", String(timeoutS));
    const token = getToken();
    if (token) url.searchParams.set("token", token);
    return new EventSource(url.toString());
}

export { ApiError };
