<!--
    PresenceHeader — зона «стан зараз»: те, чим Арчі живе цю мить.

    Ієрархія (scenario-first, без dashboard-soup): mood-орб + жива думка (герой) →
    активний сценарій → армовані тригери + наступний план → health/бюджет.

    Offline (nowStore.isOffline): fetch впав АБО now.stale → банер «😴 Арчі спить /
    офлайн» замість тіла. X28: mood/scenario/thesis/armed/stale — усе з бекенда;
    фронт деривує лише колір орба (directional) + форматує час/числа.
-->
<script lang="ts">
    import type { NowResponse } from "../../lib/types";
    import { fmtPrice, timeAgo, absTime, timeUntil } from "../../lib/format";
    import ArmedTriggers from "./ArmedTriggers.svelte";
    import LoopHealth from "./LoopHealth.svelte";

    let {
        now,
        offline,
        nowMs,
    }: { now: NowResponse | null; offline: boolean; nowMs: number } = $props();

    let state = $derived(now?.state ?? {});
    let directives = $derived(now?.directives ?? {});
    let scenario = $derived(directives.active_scenario ?? null);
    let thesis = $derived(now?.thesis ?? null);

    let mood = $derived(directives.mood ?? state.mood ?? "");
    let session = $derived(state.market_session ?? "");
    let innerThought = $derived(
        (directives.inner_thought ?? state.inner_thought ?? "").trim(),
    );

    // ── вік думки: свіжий thought_history, інакше штамп agent:state (форматування) ──
    let thoughtTsMs = $derived.by(() => {
        const hist = directives.thought_history;
        if (hist && hist.length > 0) {
            const last = hist[hist.length - 1];
            if (typeof last.ts === "number" && last.ts > 0) return last.ts * 1000;
        }
        const stateTs = Number(state.ts_ms ?? 0);
        return Number.isFinite(stateTs) ? stateTs : 0;
    });
    let thoughtAgo = $derived(thoughtTsMs ? timeAgo(thoughtTsMs, nowMs) : "");
    let thoughtAbs = $derived(thoughtTsMs ? absTime(thoughtTsMs) : "");

    // ── напрям сценарію → shape + колір (directional, не домен) ──
    let dirLong = $derived(scenario?.direction === "long");
    let dirShort = $derived(scenario?.direction === "short");

    // ── наступний план: конкретний next_wake_ms, інакше next_check_minutes ──
    let nextWakeMs = $derived(Number(state.next_wake_ms ?? 0));
    let nextWakeLabel = $derived.by(() => {
        if (nextWakeMs > 0) return timeUntil(nextWakeMs, nowMs);
        const mins = directives.next_check_minutes;
        if (typeof mins === "number" && mins > 0) return `за ~${mins} хв`;
        return "";
    });
    let nextWakeReason = $derived(
        state.next_wake_reason ?? directives.next_check_reason ?? "",
    );

    function pctOf(fraction: number | undefined): string {
        if (fraction == null || !Number.isFinite(fraction)) return "";
        return `${Math.round(fraction * 100)}%`;
    }

    const SCEN_STATUS: Record<string, string> = {
        waiting: "чекає",
        active: "активний",
        triggered: "спрацював",
        invalidated: "інвалідований",
    };
</script>

<header class="presence">
    {#if offline}
        <!-- Degraded-but-loud: сервер stale або fetch впав -->
        <div class="offline">
            <span class="z">😴</span>
            <div class="offline-text">
                <div class="offline-title">Арчі спить / офлайн</div>
                <div class="offline-sub">
                    Немає свіжого стану. Спостереження відновиться, щойно петля прокинеться.
                </div>
            </div>
        </div>
    {:else if now}
        <!-- ── Герой: mood-орб + жива думка ── -->
        <div class="hero">
            <div class="orb-wrap" title={mood ? `настрій: ${mood}` : "настрій невідомий"}>
                <div class="orb"></div>
            </div>
            <div class="hero-body">
                <div class="hero-meta">
                    {#if mood}<span class="mood">{mood}</span>{/if}
                    {#if session}<span class="sep">·</span><span class="session">{session}</span>{/if}
                    <span class="sep">·</span>
                    <span class="price" title="Поточна ціна {now.symbol}">
                        {now.symbol} {fmtPrice(now.price)}
                    </span>
                </div>
                {#if innerThought}
                    <p class="thought">{innerThought}</p>
                    {#if thoughtAgo}
                        <div class="thought-age" title={thoughtAbs}>{thoughtAgo}</div>
                    {/if}
                {:else}
                    <p class="thought muted">Думка не озвучена.</p>
                {/if}
            </div>
        </div>

        <!-- ── Активний сценарій ── -->
        {#if scenario}
            <div class="scenario">
                <div class="scen-head">
                    <span
                        class="dir"
                        class:long={dirLong}
                        class:short={dirShort}
                        title="напрям сценарію"
                    >
                        <span class="dir-arrow" aria-hidden="true">
                            {dirLong ? "▲" : dirShort ? "▼" : "◆"}
                        </span>
                        {scenario.direction}
                    </span>
                    <span class="scen-status">
                        {SCEN_STATUS[scenario.status] ?? scenario.status}
                    </span>
                    {#if scenario.confidence != null}
                        <span class="conf" title="впевненість Арчі">
                            {pctOf(scenario.confidence)}
                        </span>
                    {/if}
                </div>
                {#if scenario.thesis}
                    <p class="scen-thesis">{scenario.thesis}</p>
                {/if}
                <div class="scen-levels">
                    <div class="lvl">
                        <span class="lvl-k">зона входу</span>
                        <span class="lvl-v">
                            {fmtPrice(scenario.entry_zone_low)}–{fmtPrice(scenario.entry_zone_high)}
                        </span>
                    </div>
                    <div class="lvl">
                        <span class="lvl-k">інвалідація</span>
                        <span class="lvl-v danger">{fmtPrice(scenario.invalidation)}</span>
                    </div>
                    {#if scenario.targets && scenario.targets.length > 0}
                        <div class="lvl">
                            <span class="lvl-k">цілі</span>
                            <span class="lvl-v positive">
                                {scenario.targets.map((t) => fmtPrice(t)).join(" · ")}
                            </span>
                        </div>
                    {/if}
                </div>
            </div>
        {:else if thesis}
            <!-- Немає активного сценарію → теза дня (не вигадуємо setup, N3) -->
            <div class="scenario thesis-only">
                <p class="scen-thesis">{thesis.thesis}</p>
                <div class="scen-levels">
                    <div class="lvl">
                        <span class="lvl-k">рівень</span>
                        <span class="lvl-v">{thesis.key_level}</span>
                    </div>
                    <div class="lvl">
                        <span class="lvl-k">інвалідація</span>
                        <span class="lvl-v danger">{thesis.invalidation}</span>
                    </div>
                    <div class="lvl">
                        <span class="lvl-k">переконаність</span>
                        <span class="lvl-v">{thesis.conviction}</span>
                    </div>
                </div>
            </div>
        {:else}
            <div class="no-scenario">Активного сценарію немає.</div>
        {/if}

        <!-- ── Тригери + наступний план ── -->
        <div class="grid">
            <ArmedTriggers armed={now.armed} />

            {#if nextWakeLabel || nextWakeReason}
                <div class="next">
                    <div class="next-head">Наступний план</div>
                    <div class="next-body">
                        {#if nextWakeLabel}<span class="next-when">{nextWakeLabel}</span>{/if}
                        {#if nextWakeReason}<span class="next-why">{nextWakeReason}</span>{/if}
                    </div>
                </div>
            {/if}
        </div>

        <!-- ── Health / бюджет / kill-switch ── -->
        <LoopHealth {state} {directives} />

        <!-- Degraded-but-loud: сервер повідомив причини, показуємо тихо-але-видимо -->
        {#if now.degraded && now.degraded.length > 0}
            <div class="degraded" title="Причини часткової деградації read-side">
                ⚠ {now.degraded.join(" · ")}
            </div>
        {/if}
    {:else}
        <div class="offline">
            <span class="z">◌</span>
            <div class="offline-text">
                <div class="offline-title">Підключення…</div>
            </div>
        </div>
    {/if}
</header>

<style>
    .presence {
        display: flex;
        flex-direction: column;
        gap: 16px;
        padding: 20px;
        background: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 16px;
        backdrop-filter: var(--card-glass);
        -webkit-backdrop-filter: var(--card-glass);
        box-shadow: var(--card-shadow);
    }

    /* ── offline ── */
    .offline {
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 8px 4px;
    }
    .offline .z {
        font-size: 30px;
        filter: grayscale(0.3);
    }
    .offline-title {
        font-size: 15px;
        font-weight: 600;
        color: var(--text);
    }
    .offline-sub {
        font-size: 12.5px;
        color: var(--text-muted);
        margin-top: 2px;
    }

    /* ── hero ── */
    .hero {
        display: flex;
        gap: 16px;
        align-items: flex-start;
    }
    .orb-wrap {
        flex: none;
        width: 52px;
        height: 52px;
        display: grid;
        place-items: center;
    }
    .orb {
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: radial-gradient(
            circle at 35% 30%,
            #fff 0%,
            color-mix(in srgb, var(--accent) 55%, #fff) 24%,
            var(--accent) 60%,
            color-mix(in srgb, var(--accent) 72%, #000) 100%
        );
        box-shadow:
            0 0 26px 3px color-mix(in srgb, var(--accent) 45%, transparent),
            0 4px 16px rgba(0, 0, 0, 0.35);
        animation: breathe 4.2s ease-in-out infinite;
    }
    /* Функціональна анімація: орб «дихає» = петля жива (не декор). */
    @keyframes breathe {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.06); }
    }
    @media (prefers-reduced-motion: reduce) {
        .orb { animation: none; }
    }
    .hero-body {
        flex: 1;
        min-width: 0;
    }
    .hero-meta {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
        font-size: 12px;
        color: var(--text-muted);
    }
    .hero-meta .mood {
        color: var(--accent);
        font-weight: 600;
        text-transform: capitalize;
    }
    .hero-meta .sep {
        opacity: 0.5;
    }
    .hero-meta .session {
        text-transform: capitalize;
    }
    .hero-meta .price {
        font-family: var(--font-mono);
        color: var(--text);
    }
    .thought {
        margin-top: 6px;
        font-size: 17px;
        line-height: 1.45;
        color: var(--text);
        letter-spacing: -0.01em;
    }
    .thought.muted {
        color: var(--text-muted);
        font-size: 15px;
    }
    .thought-age {
        margin-top: 4px;
        font-size: 11.5px;
        color: var(--text-muted);
    }

    /* ── scenario ── */
    .scenario {
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding: 14px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
    }
    .scen-head {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .dir {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        border: 1px solid var(--border);
        color: var(--text-muted);
    }
    .dir-arrow { font-size: 11px; }
    .dir.long {
        color: var(--positive);
        border-color: color-mix(in srgb, var(--positive) 40%, var(--border));
        background: color-mix(in srgb, var(--positive) 10%, transparent);
    }
    .dir.short {
        color: var(--danger);
        border-color: color-mix(in srgb, var(--danger) 40%, var(--border));
        background: color-mix(in srgb, var(--danger) 10%, transparent);
    }
    .scen-status {
        font-size: 12px;
        color: var(--text-muted);
    }
    .conf {
        margin-left: auto;
        font-family: var(--font-mono);
        font-size: 12.5px;
        font-weight: 600;
        color: var(--accent);
    }
    .scen-thesis {
        font-size: 13.5px;
        line-height: 1.5;
        color: var(--text);
    }
    .scen-levels {
        display: flex;
        flex-wrap: wrap;
        gap: 14px;
    }
    .lvl {
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .lvl-k {
        font-size: 10.5px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-muted);
    }
    .lvl-v {
        font-family: var(--font-mono);
        font-size: 13px;
        color: var(--text);
        font-weight: 600;
    }
    .lvl-v.danger { color: var(--danger); }
    .lvl-v.positive { color: var(--positive); }

    .no-scenario {
        padding: 12px 14px;
        background: var(--surface);
        border: 1px dashed var(--border);
        border-radius: 12px;
        font-size: 13px;
        color: var(--text-muted);
    }

    /* ── grid: armed + next ── */
    .grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 14px;
        align-items: start;
    }
    .next {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .next-head {
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-muted);
    }
    .next-body {
        display: flex;
        flex-direction: column;
        gap: 3px;
        padding: 8px 10px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 8px;
    }
    .next-when {
        font-family: var(--font-mono);
        font-size: 13px;
        color: var(--accent);
        font-weight: 600;
    }
    .next-why {
        font-size: 12px;
        color: var(--text-muted);
    }

    .degraded {
        font-size: 11.5px;
        color: var(--warning);
        font-family: var(--font-mono);
    }

    @media (max-width: 640px) {
        .grid {
            grid-template-columns: 1fr;
        }
    }
</style>
