/**
 * hearthHelpers — Pure helpers для Mode Hearth + Pulse Board деривацій.
 *
 * Contains:
 *   - Types: HearthTone, HearthAction, ModeHearth, PulseCardTone, PulseCard,
 *            PulseFreshness, PulseFreshnessTone
 *   - Scalar helpers: toNumber, truncateText, firstNonEmptyText, relativeMinutes,
 *                     countdownLabel, thinkingTimestampMs, normalizeCallType, formatTs
 *   - Derivation helpers: modeToneFromCallType, modeLabelFromCallType,
 *                         currentModeModel, hearthReasonCopy, hearthActions
 *   - Directives readers: getMarketMentalModel, getMetacognition, getDecisionEntries,
 *                         getActivePromises, decisionCategoryLabel, userSignalCopy,
 *                         getBias, getWatchLevels
 *   - Composite builders:
 *       buildModeHearth(dir, state, thinking, errors, loading)
 *       buildPulseFreshness(lastDirSyncMs, lastStateSyncMs, errors)
 *       buildPulseCards(dir, state)
 *
 * Invariants:
 *   - Pure: жодних side effects, жодного доступу до runes store / DOM.
 *   - Ідемпотентний: однакові inputs → однакові outputs.
 *   - Degraded-but-loud: жодних silent defaults; `null` повертається коли snapshot порожній.
 */
import type {
    AgentState,
    DecisionEntry,
    Directives,
    MarketMentalModel,
    MetaCognition,
    ThinkingEntry,
    TrackedPromise,
} from "../../../lib/types";

// ═══ Types ═══

export type HearthTone = "home" | "work" | "quiet" | "bridge" | "degraded";

export type HearthAction = {
    label: string;
    prompt: string;
    subtle?: boolean;
};

export type ModeHearth = {
    tone: HearthTone;
    title: string;
    detail: string;
    reason: string;
    modeLabel: string;
    modelLabel?: string;
    meta: string[];
    actions: HearthAction[];
    warning?: string;
};

export type PulseCardTone = "live" | "change" | "ledger" | "care" | "risk";

export type PulseCard = {
    id: string;
    kicker: string;
    title: string;
    detail: string;
    meta?: string;
    tone: PulseCardTone;
    prompt: string;
};

export type PulseFreshnessTone = "live" | "pending" | "stale";

export type PulseFreshness = {
    label: string;
    tone: PulseFreshnessTone;
    detail: string;
};

export type BiasInfo = { label: string; color: string };
export type WatchLevels = { up: number | null; down: number | null };

// ═══ Scalar helpers ═══

export function toNumber(value: unknown): number | null {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
    }
    return null;
}

export function truncateText(text: string, max = 120): string {
    const compact = text.trim().replace(/\s+/g, " ");
    if (!compact) return "";
    if (compact.length <= max) return compact;
    return `${compact.slice(0, max - 1).trimEnd()}…`;
}

export function firstNonEmptyText(
    ...values: Array<string | null | undefined>
): string {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return "";
}

export function relativeMinutes(minutes: number): string {
    if (minutes <= 0) return "щойно";
    if (minutes === 1) return "1 хв тому";
    return `${minutes} хв тому`;
}

export function countdownLabel(targetMs: number): string {
    const diffMinutes = Math.round((targetMs - Date.now()) / 60000);
    if (diffMinutes <= 0) return "wake вже настав";
    if (diffMinutes === 1) return "wake через 1 хв";
    return `wake через ${diffMinutes} хв`;
}

export function thinkingTimestampMs(entry: ThinkingEntry | null): number | null {
    if (!entry) return null;
    if (typeof entry.ts === "number" && Number.isFinite(entry.ts)) {
        return Math.round(entry.ts * 1000);
    }
    const parsed = toNumber(entry.ts);
    return parsed !== null ? Math.round(parsed * 1000) : null;
}

export function normalizeCallType(value: string | undefined): string {
    return value?.trim().toLowerCase() ?? "";
}

export function formatTs(ts_ms: number): string {
    const d = new Date(ts_ms);
    const h = d.getHours().toString().padStart(2, "0");
    const m = d.getMinutes().toString().padStart(2, "0");
    return `${h}:${m}`;
}

// ═══ Derivation helpers ═══

export function modeToneFromCallType(
    callType: string,
    nextWakeMs: number | null,
): HearthTone {
    if (
        callType === "reactive_casual" ||
        callType === "manual" ||
        callType === "casual"
    ) {
        return "home";
    }
    if (
        callType === "reactive_market" ||
        callType === "proactive" ||
        callType === "full" ||
        callType === "observation" ||
        callType === "daily_review" ||
        callType === "ritual_weekly" ||
        callType === "system_upgrade" ||
        callType === "tda"
    ) {
        return "work";
    }
    if (callType === "reactive" || callType === "consciousness_recovery") {
        return "bridge";
    }
    if (nextWakeMs !== null && nextWakeMs > Date.now()) return "quiet";
    return "quiet";
}

export function modeLabelFromCallType(callType: string, tone: HearthTone): string {
    switch (callType) {
        case "reactive_casual":
            return "Casual Contact";
        case "reactive_market":
            return "Market Contact";
        case "proactive":
            return "Proactive Analyst";
        case "full":
            return "Full Context";
        case "observation":
            return "Observation";
        case "reactive":
            return "Reactive Contact";
        case "consciousness_recovery":
            return "Recovery";
        case "daily_review":
            return "Daily Review";
        case "ritual_weekly":
            return "Weekly Ritual";
        case "manual":
            return "Manual Contact";
        case "system_upgrade":
            return "System Upgrade";
        case "tda":
            return "TDA Review";
        default:
            switch (tone) {
                case "home":
                    return "Casual Presence";
                case "work":
                    return "Analyst Contour";
                case "bridge":
                    return "Live Contact";
                case "degraded":
                    return "Unavailable";
                case "quiet":
                default:
                    return "Standby";
            }
    }
}

export function currentModeModel(
    entry: ThinkingEntry | null,
    state: AgentState | null,
): string {
    return firstNonEmptyText(
        entry?.model,
        typeof state?.model_last_call === "string"
            ? (state.model_last_call as string)
            : "",
        typeof state?.model_current === "string"
            ? (state.model_current as string)
            : "",
    );
}

export function hearthReasonCopy(
    callType: string,
    nextWakeReason: string,
    nextWakeMs: number | null,
    observationEnabled: boolean,
): string {
    if (nextWakeReason) {
        return `Наступний wake: ${truncateText(nextWakeReason, 112)}${
            nextWakeMs ? ` · ${countdownLabel(nextWakeMs)}` : ""
        }`;
    }

    if (callType === "reactive_casual" || callType === "manual") {
        return "Shell читає це як людський контакт без важкого analyst bootstrap: спочатку присутність, потім аналіз за потреби.";
    }
    if (
        callType === "reactive_market" ||
        callType === "proactive" ||
        callType === "full" ||
        callType === "observation" ||
        callType === "daily_review" ||
        callType === "ritual_weekly" ||
        callType === "system_upgrade" ||
        callType === "tda"
    ) {
        return "Є ринковий привід для структурованої присутності: Archi тримає analyst contour і готує brief замість дружнього small talk.";
    }
    if (callType === "reactive" || callType === "consciousness_recovery") {
        return "Останній archived call дає лише signal живого контакту: це ще не доказ чистого casual mode і не гарантія повного analyst frame.";
    }

    return observationEnabled
        ? "Observation beat лишається увімкненим, але shell поки не має явного wake reason для наступної ескалації."
        : "Observation beat зараз мовчить; Archi чекає зовнішній trigger або ручний контакт.";
}

export function hearthActions(tone: HearthTone): HearthAction[] {
    switch (tone) {
        case "home":
            return [
                {
                    label: "Як ти зараз?",
                    prompt: "Скажи просто і по-людськи: ти зараз вдома як друг чи вже напівв роботі, що в тебе на думці і що мені важливо знати без зайвого формалізму?",
                },
                {
                    label: "Коли розбудиш?",
                    prompt: "Поясни, який наступний trigger або wake_reason переведе тебе з дружнього режиму в analyst mode і як ти це мені подаси.",
                    subtle: true,
                },
            ];
        case "work":
            return [
                {
                    label: "Operator brief",
                    prompt: "Дай operator brief на зараз: bias, active thesis, risk trigger, next wake condition і що конкретно може змінити сценарій.",
                },
                {
                    label: "Переклади просто",
                    prompt: "Переклади свій поточний analyst mode людською мовою: що це означає для мене прямо зараз без зайвого noise.",
                    subtle: true,
                },
            ];
        case "bridge":
            return [
                {
                    label: "Що перемикає режим?",
                    prompt: "Поясни, між якими режимами ти зараз стоїш, що саме тригерить перемикання і яка ознака, що пора ввімкнути аналітика повністю.",
                },
                {
                    label: "Коротко по-людськи",
                    prompt: "Дай короткий статус як друг, але збережи головний risk context: що ти зараз відчуваєш у ринку і чому це важливо або не важливо.",
                    subtle: true,
                },
            ];
        case "quiet":
        default:
            return [
                {
                    label: "Чому тиша?",
                    prompt: "Поясни, чому зараз тиша є правильною, що ти все одно тримаєш у фоні і який наступний wake trigger важливий.",
                },
                {
                    label: "Легкий статус",
                    prompt: "Дай короткий домашній статус у двох-трьох реченнях: як ти зараз, що з ринком у фоні і коли чекати наступний осмислений signal.",
                    subtle: true,
                },
            ];
        case "degraded":
            return [];
    }
}

// ═══ Directives readers ═══

export function getMarketMentalModel(
    dir: Directives | null,
): MarketMentalModel | null {
    const model = dir?.market_mental_model;
    return model && typeof model === "object" && !Array.isArray(model)
        ? (model as MarketMentalModel)
        : null;
}

export function getMetacognition(dir: Directives | null): MetaCognition | null {
    const meta = dir?.metacognition;
    return meta && typeof meta === "object" && !Array.isArray(meta)
        ? (meta as MetaCognition)
        : null;
}

export function getDecisionEntries(dir: Directives | null): DecisionEntry[] {
    return Array.isArray(dir?.decision_log)
        ? (dir.decision_log as DecisionEntry[])
        : [];
}

export function getActivePromises(dir: Directives | null): TrackedPromise[] {
    const promises = getMetacognition(dir)?.tracked_promises;
    if (!Array.isArray(promises)) return [];
    return promises.filter(
        (entry) => entry?.status === "active" && !!entry.text,
    );
}

export function decisionCategoryLabel(category: string | undefined): string {
    switch (category) {
        case "risk":
            return "Ризик";
        case "exit":
            return "Вихід";
        case "rule":
            return "Правило";
        case "skip":
            return "Пропуск";
        case "scenario":
        default:
            return "Рішення";
    }
}

export function userSignalCopy(signal: string): {
    title: string;
    detail: string;
} {
    switch (signal) {
        case "active_focus":
            return {
                title: "Фокус і прямота",
                detail: "Archi читає контакт як концентрований: потрібні короткі ясні відповіді без шуму.",
            };
        case "absence":
            return {
                title: "Повернення після паузи",
                detail: "Контур очікує м'якого re-entry: що змінилось, що важливо і що не потребує паніки.",
            };
        case "reflective":
            return {
                title: "Рефлексивний режим",
                detail: "Важливо не лише сказати що робити, а й чому це зараз має сенс.",
            };
        case "post_loss":
            return {
                title: "Після втрати",
                detail: "Пріоритет зміщується з дії на стабілізацію, ясність і контроль ризику.",
            };
        case "pre_session":
            return {
                title: "Передсесійна підготовка",
                detail: "Контур чекає чіткий brief: рівні, умова активації, ризик і wake plan.",
            };
        default:
            return {
                title: "Контакт стабільний",
                detail: "Спеціальний user-signal не зафіксований; Archi тримає звичайний тон супроводу.",
            };
    }
}

export function getBias(dir: Directives | null): BiasInfo {
    const bm = dir?.bias_map as Record<string, unknown> | null;
    if (!bm) return { label: "", color: "" };

    const priority = ["H4", "D1", "H1"];
    for (const tf of priority) {
        const val = bm[tf];
        if (typeof val === "string") {
            const lower = val.toLowerCase();
            if (lower.includes("bull") || lower.includes("long")) {
                return { label: `${tf}: bull`, color: "bull" };
            }
            if (lower.includes("bear") || lower.includes("short")) {
                return { label: `${tf}: bear`, color: "bear" };
            }
        }
    }

    return { label: "", color: "" };
}

export function getWatchLevels(dir: Directives | null): WatchLevels {
    const wl = dir?.watch_levels;
    if (!Array.isArray(wl)) return { up: null, down: null };

    let up: number | null = null;
    let down: number | null = null;
    for (const lvl of wl as Array<{ direction?: string; price?: number }>) {
        if (lvl.direction === "above" && lvl.price) up = lvl.price;
        if (lvl.direction === "below" && lvl.price) down = lvl.price;
    }

    return { up, down };
}

// ═══ Composite builders ═══

export type ModeHearthInputs = {
    directives: Directives | null;
    agentState: AgentState | null;
    latestThinking: ThinkingEntry | null;
    lastThinkingSyncMs: number;
    directivesSyncError: string;
    agentStateSyncError: string;
    thinkingSyncError: string;
    loading: boolean;
};

export function buildModeHearth(inp: ModeHearthInputs): ModeHearth | null {
    const {
        directives,
        agentState,
        latestThinking,
        lastThinkingSyncMs,
        directivesSyncError,
        agentStateSyncError,
        thinkingSyncError,
        loading,
    } = inp;

    const syncWarnings = [
        directivesSyncError,
        agentStateSyncError,
        thinkingSyncError,
    ].filter(Boolean);
    const hasSnapshot = !!(directives || agentState || latestThinking);

    if (!hasSnapshot) {
        if (loading || syncWarnings.length === 0) return null;
        return {
            tone: "degraded",
            title: "Mode Hearth недоступний",
            detail: "Shell не отримав жодного валідного snapshot для presence layer, тому режим не буде вигаданий поверх порожнечі.",
            reason: "Поки немає мінімального shell snapshot, Chat не робить висновок про quiet, casual чи analyst presence.",
            modeLabel: "Unavailable",
            meta: [],
            actions: [],
            warning: syncWarnings.join(" · "),
        };
    }

    const mentalModel = getMarketMentalModel(directives);
    const observationEnabled = directives?.observation_enabled !== false;
    const nextWakeMs = toNumber(agentState?.next_wake_ms);
    const nextWakeReason = firstNonEmptyText(
        typeof agentState?.next_wake_reason === "string"
            ? agentState.next_wake_reason
            : "",
        typeof directives?.next_check_reason === "string"
            ? directives.next_check_reason
            : "",
    );
    const callType = normalizeCallType(latestThinking?.call_type);
    const tone = modeToneFromCallType(callType, nextWakeMs);
    const modeLabel = modeLabelFromCallType(callType, tone);
    const lastCallMs = thinkingTimestampMs(latestThinking);
    const lastCallMinutes =
        lastCallMs !== null
            ? Math.max(0, Math.round((Date.now() - lastCallMs) / 60000))
            : null;
    const focusLine = truncateText(
        firstNonEmptyText(
            latestThinking?.output_snippet,
            mentalModel?.current_narrative,
            mentalModel?.what_watching,
            mentalModel?.what_changed,
            typeof agentState?.inner_thought === "string"
                ? agentState.inner_thought
                : "",
            typeof directives?.inner_thought === "string"
                ? directives.inner_thought
                : "",
            latestThinking?.trigger,
        ),
        176,
    );

    const title =
        tone === "home"
            ? "Арчі в casual presence"
            : tone === "work"
              ? "Арчі в analyst contour"
              : tone === "bridge"
                ? "Арчі на live contact між режимами"
                : "Арчі тримає тишу між wake-подіями";
    const detail =
        focusLine ||
        (tone === "home"
            ? "Контакт зараз ближчий до друга: Archi не заводить важкий briefing без реального приводу, але лишається поруч."
            : tone === "work"
              ? "Контур зібраний для операторського читання: shell чекає не small talk, а структурований brief або перевірку сценарію."
              : tone === "bridge"
                ? "Останній signal говорить лише про живий контакт або recovery: shell не вдає, ніби вже точно знає casual чи analyst режим без додаткового підтвердження."
                : "Тиша тут не порожнеча: Archi лишається на фоні й чекає осмислений trigger замість тривожних фальстартів.");

    const meta: string[] = [];
    if (lastCallMinutes !== null) {
        meta.push(`last call ${relativeMinutes(lastCallMinutes)}`);
    } else if (lastThinkingSyncMs > 0) {
        meta.push("call journal ще тихий");
    }
    if (nextWakeMs) {
        meta.push(countdownLabel(nextWakeMs));
    } else if (observationEnabled && tone !== "home") {
        meta.push("wake без таймера");
    }

    const modelLabel = currentModeModel(latestThinking, agentState);
    if (typeof directives?.focus_symbol === "string" && directives.focus_symbol) {
        meta.push(directives.focus_symbol);
    }
    if (directives?.economy_mode_active) meta.push("economy mode");

    const warnings = [
        directives?.kill_switch_active
            ? "Kill switch активний — Archi лишається в розмові, але торгова ескалація може бути навмисно стриманою."
            : "",
        ...syncWarnings,
    ].filter(Boolean);

    return {
        tone,
        title,
        detail,
        reason: hearthReasonCopy(
            callType,
            nextWakeReason,
            nextWakeMs,
            observationEnabled,
        ),
        modeLabel,
        modelLabel,
        meta,
        actions: hearthActions(tone),
        warning: warnings.join(" · "),
    };
}

export function buildPulseFreshness(
    lastDirectivesSyncMs: number,
    lastAgentStateSyncMs: number,
    directivesSyncError: string,
    agentStateSyncError: string,
): PulseFreshness {
    const freshest = [lastDirectivesSyncMs, lastAgentStateSyncMs].filter(
        (value) => value > 0,
    );
    const syncedMinutes = freshest.length
        ? Math.max(0, Math.round((Date.now() - Math.min(...freshest)) / 60000))
        : null;
    const syncErrors = [directivesSyncError, agentStateSyncError].filter(Boolean);

    if (syncErrors.length > 0) {
        return {
            label:
                syncedMinutes !== null
                    ? `stale · sync ${relativeMinutes(syncedMinutes)}`
                    : "stale",
            tone: "stale",
            detail: syncErrors.join(" · "),
        };
    }

    if (syncedMinutes === null) {
        return { label: "sync pending", tone: "pending", detail: "" };
    }

    return {
        label:
            syncedMinutes === 0
                ? "sync щойно"
                : `sync ${relativeMinutes(syncedMinutes)}`,
        tone: "live",
        detail: "",
    };
}

export function buildPulseCards(
    directives: Directives | null,
    agentState: AgentState | null,
): PulseCard[] {
    const cards: PulseCard[] = [];
    const mentalModel = getMarketMentalModel(directives);
    const decisions = getDecisionEntries(directives);
    const activePromises = getActivePromises(directives);
    const userSignal =
        typeof directives?.user_signal === "string"
            ? directives.user_signal
            : "";
    const signalCopy = userSignalCopy(userSignal);

    const observationEnabled = directives?.observation_enabled !== false;
    const observationInterval = Math.max(
        5,
        toNumber(directives?.observation_interval_minutes) ?? 25,
    );
    const lastObservationTs = toNumber(directives?.last_observation_ts);
    const lastObservationMinutes =
        lastObservationTs !== null
            ? Math.max(
                  0,
                  Math.round((Date.now() / 1000 - lastObservationTs) / 60),
              )
            : null;
    const nextWakeMs = toNumber(agentState?.next_wake_ms);
    const nextWakeReason =
        typeof agentState?.next_wake_reason === "string" &&
        agentState.next_wake_reason.trim()
            ? agentState.next_wake_reason
            : typeof directives?.next_check_reason === "string"
              ? directives.next_check_reason
              : "";
    const observationOverdue =
        observationEnabled &&
        lastObservationMinutes !== null &&
        lastObservationMinutes > observationInterval + 5;
    const observationTitle = !observationEnabled
        ? "Observation beat вимкнений"
        : lastObservationMinutes === null
          ? "Observation beat ще не зафіксований"
          : observationOverdue
            ? `Observation прострочений на ${lastObservationMinutes - observationInterval} хв`
            : `Останній beat ${relativeMinutes(lastObservationMinutes)}`;
    cards.push({
        id: "pulse-observation",
        kicker: "Pulse Rail",
        title: observationTitle,
        detail: truncateText(
            mentalModel?.what_watching ||
                nextWakeReason ||
                (typeof directives?.inner_thought === "string"
                    ? directives.inner_thought
                    : "") ||
                "У shell ще нема короткого пояснення, що Арчі реально тримає у фокусі прямо зараз.",
        ),
        meta: nextWakeMs
            ? countdownLabel(nextWakeMs)
            : `cadence ${observationInterval} хв`,
        tone: observationOverdue ? "risk" : "live",
        prompt: observationEnabled
            ? "Поясни свій поточний pulse: коли ти востаннє перевіряв ринок, що зараз тримаєш у фокусі і коли повернешся до наступного observation beat?"
            : "Поясни, чому observation beat зараз вимкнений, який це створює ризик і що має повернути тебе назад у регулярний ритм спостереження.",
    });

    const changeTitle = truncateText(
        mentalModel?.what_changed || mentalModel?.current_narrative || "",
        92,
    );
    if (changeTitle) {
        const updatedAt = toNumber(mentalModel?.updated_at);
        const updatedMinutes =
            updatedAt !== null
                ? Math.max(0, Math.round((Date.now() / 1000 - updatedAt) / 60))
                : null;
        cards.push({
            id: "pulse-change",
            kicker: "Що змінилось",
            title: changeTitle,
            detail:
                truncateText(
                    mentalModel?.what_watching ||
                        mentalModel?.structure_reasoning ||
                        mentalModel?.macro_reasoning ||
                        "",
                ) ||
                "Archi зафіксував зміну, але ще не розгорнув її в окреме пояснення.",
            meta:
                updatedMinutes !== null
                    ? `оновлено ${relativeMinutes(updatedMinutes)}`
                    : undefined,
            tone: "change",
            prompt: "Розгорни, що саме змінилось у твоїй картині ринку, що це означає для поточного сценарію і що саме тепер потребує уваги.",
        });
    }

    const lastDecision = decisions[decisions.length - 1];
    if (lastDecision?.decision) {
        const createdAt = toNumber(lastDecision.created_at);
        const decisionMinutes =
            createdAt !== null
                ? Math.max(0, Math.round((Date.now() / 1000 - createdAt) / 60))
                : null;
        const alternativesText =
            Array.isArray(lastDecision.alternatives) &&
            lastDecision.alternatives.length > 0
                ? `Альтернативи: ${lastDecision.alternatives.join(" · ")}`
                : "";
        cards.push({
            id: "pulse-decision",
            kicker: decisionCategoryLabel(lastDecision.category),
            title: truncateText(lastDecision.decision, 92),
            detail: truncateText(
                lastDecision.reasoning ||
                    alternativesText ||
                    "Decision trail існує, але reasoning ще не розгорнуте.",
            ),
            meta:
                decisionMinutes !== null
                    ? `записано ${relativeMinutes(decisionMinutes)}`
                    : undefined,
            tone: "ledger",
            prompt: `Розбери останнє рішення: ${lastDecision.decision}\n\nПоясни reasoning, альтернативи і що може змусити тебе переглянути цей вибір.`,
        });
    }

    if (activePromises.length > 0 || userSignal) {
        const firstPromise = activePromises[0]?.text ?? "";
        const careMetaParts: string[] = [];
        if (userSignal) careMetaParts.push(signalCopy.title);
        if (typeof directives?.mood === "string" && directives.mood) {
            careMetaParts.push(`mood: ${directives.mood}`);
        }
        cards.push({
            id: "pulse-care",
            kicker: "Care Loop",
            title:
                activePromises.length > 0
                    ? `${activePromises.length} активні обіцянки`
                    : signalCopy.title,
            detail:
                truncateText(
                    activePromises.length > 0
                        ? `${firstPromise}${userSignal ? ` · Сигнал контакту: ${signalCopy.detail}` : ""}`
                        : signalCopy.detail,
                ) ||
                "Care loop активний, але shell ще не має короткого пояснення людського контуру.",
            meta: careMetaParts.join(" · ") || undefined,
            tone: "care",
            prompt:
                activePromises.length > 0
                    ? "Пройдися по активних обіцянках: що ще відкрите, що реально під ризиком і що ти хочеш закрити наступним."
                    : "Скажи прямо, як ти зараз читаєш мій стан, що це означає для тону взаємодії і що тобі важливо не проґавити в найближчому контакті.",
        });
    }

    return cards.slice(0, 4);
}
