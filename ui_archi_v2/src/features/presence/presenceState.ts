/**
 * presenceState — чиста деривація стану присутності ГОРНа з реальних полів.
 *
 * SSOT досвіду: ui_archi_v2/PRESENCE_CONCEPT.md · самоопис: trader-v3/docs/archi_self_image.md
 *
 * Інваріанти:
 *   - Pure: нуль side effects / DOM / fetch. Однакові inputs → однаковий output.
 *   - X28: НЕ вигадуємо стан. Кожен mode = реальне поле AgentState/Directives.
 *   - Імпульс = зміна `inner_thought` (новий рядок = Арчі щойно подумав → прокинувся).
 *     Між імпульсами думка не змінюється → тиша → сон («жевріє, чекає»).
 */
import type { AgentState, Directives } from "../../lib/types";

export type PresenceMode = "calm" | "think" | "setup" | "position" | "alert" | "sleep";

// Пороги (мс). Heartbeat стану ~30s, тож «сон» = відсутність НОВОЇ думки, не відсутність стану.
export const THINK_WINDOW_MS = 90_000;       // свіжий імпульс → ще «думає» (нерівний пульс)
export const QUIET_SLEEP_MS = 20 * 60_000;   // нема нової думки 20хв → жевріє (сон)
export const STATE_STALE_MS = 45 * 60_000;   // стан застарів (бот мовчить) → точно сон

/**
 * Виводить режим присутності.
 * @param lastImpulseMs коли востаннє змінилася думка (epoch ms); 0 = невідомо.
 */
export function derivePresenceMode(
    state: AgentState | null,
    directives: Directives | null,
    lastImpulseMs: number,
    now: number,
): PresenceMode {
    const tsMs = typeof state?.ts_ms === "number" ? state.ts_ms : 0;
    const stateStale = !tsMs || now - tsMs > STATE_STALE_MS;
    const quiet = !lastImpulseMs || now - lastImpulseMs > QUIET_SLEEP_MS;

    // «Між імпульсами мене немає» — нема свіжого стану АБО давно не думав
    if (stateStale || quiet) return "sleep";

    // Тривога — будь-яка реальна проблема (його червоне)
    if ((state?.health ?? "ok") !== "ok") return "alert";
    if (state?.circuit_breaker === "1") return "alert";
    if (directives?.kill_switch_active === true) return "alert";
    if ((state?.last_error ?? "") !== "") return "alert";

    // Структурні стани
    if (state?.has_virtual_position === "1") return "position";
    const scenarios = Number(state?.active_scenarios) || 0;
    if (scenarios > 0 || !!directives?.active_scenario) return "setup";

    // Щойно подумав → «думка що не дає спокою»
    if (now - lastImpulseMs < THINK_WINDOW_MS) return "think";

    return "calm";
}

/** Коротке слово стану (для тихого підпису, не домен-перерахунок). */
export function presenceModeLabel(mode: PresenceMode): string {
    switch (mode) {
        case "alert": return "тривога";
        case "position": return "у позиції";
        case "setup": return "бачу setup";
        case "think": return "думаю";
        case "sleep": return "чекаю";
        case "calm":
        default: return "спостерігаю";
    }
}
