<!--
    LoopHealth — здоров'я петлі Арчі: health / бюджет дня / kill-switch / модель.

    X28: health, budget_pct, circuit_breaker приходять готовими з agent:state
    (сервер). Фронт форматує число/рядок + directional coloring статусу. Значення
    Redis = рядки → парсимо у число ЛИШЕ для форматування, не для домен-рішень.
-->
<script lang="ts">
    import type { NowState, NowDirectives } from "../../lib/types";

    let {
        state,
        directives,
    }: { state: NowState; directives: NowDirectives } = $props();

    const HEALTH_LABEL: Record<string, string> = {
        ok: "працює",
        degraded: "деградація",
        error: "помилка",
    };

    let health = $derived((state.health ?? "").toLowerCase());
    let healthLabel = $derived(HEALTH_LABEL[health] ?? state.health ?? "невідомо");
    let killActive = $derived(
        directives.kill_switch_active === true || state.circuit_breaker === "1",
    );
    let budgetUsd = $derived(state.budget_today_usd ?? "0");
    let budgetPct = $derived(Number(state.budget_pct ?? "0"));
    let callsToday = $derived(state.calls_today ?? "0");
    let model = $derived(state.model_current ?? directives.budget_strategy ?? "");
    let lastError = $derived((state.last_error ?? "").trim());
</script>

<div class="health">
    <div class="chips">
        <span class="chip" class:ok={health === "ok"} class:warn={health === "degraded"} class:err={health === "error"}>
            <span class="dot" aria-hidden="true"></span>
            {healthLabel}
        </span>

        {#if killActive}
            <span class="chip err">⛔ kill-switch</span>
        {/if}

        <span class="chip muted" title="Витрачено сьогодні / ліміт">
            💰 ${budgetUsd}
            {#if Number.isFinite(budgetPct) && budgetPct > 0}
                <span class="sub">· {budgetPct.toFixed(1)}%</span>
            {/if}
        </span>

        <span class="chip muted" title="Викликів моделі сьогодні">
            🔁 {callsToday}
        </span>

        {#if model}
            <span class="chip muted model" title="Поточна модель">{model}</span>
        {/if}
    </div>

    {#if Number.isFinite(budgetPct) && budgetPct > 0}
        <div class="bar" title="Використання денного бюджету">
            <div
                class="bar-fill"
                class:hot={budgetPct >= 80}
                style="width: {Math.min(100, Math.max(0, budgetPct))}%"
            ></div>
        </div>
    {/if}

    {#if lastError}
        <div class="err-line" title="Остання помилка петлі">⚠ {lastError}</div>
    {/if}
</div>

<style>
    .health {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }
    .chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 12px;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text);
        white-space: nowrap;
    }
    .chip.muted {
        color: var(--text-muted);
        font-family: var(--font-mono);
    }
    .chip.model {
        font-size: 11px;
    }
    .chip .sub {
        color: var(--text-muted);
    }
    .dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--text-muted);
    }
    .chip.ok .dot {
        background: var(--positive);
        box-shadow: 0 0 8px color-mix(in srgb, var(--positive) 60%, transparent);
    }
    .chip.warn {
        color: var(--warning);
        border-color: color-mix(in srgb, var(--warning) 40%, var(--border));
    }
    .chip.warn .dot {
        background: var(--warning);
    }
    .chip.err {
        color: var(--danger);
        border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
        background: color-mix(in srgb, var(--danger) 10%, var(--surface2));
    }
    .chip.err .dot {
        background: var(--danger);
    }
    .bar {
        height: 4px;
        border-radius: 2px;
        background: var(--surface2);
        overflow: hidden;
    }
    .bar-fill {
        height: 100%;
        background: var(--accent);
        border-radius: 2px;
        transition: width 0.6s ease;
    }
    .bar-fill.hot {
        background: var(--warning);
    }
    .err-line {
        font-size: 11.5px;
        color: var(--danger);
        font-family: var(--font-mono);
        opacity: 0.85;
    }
</style>
