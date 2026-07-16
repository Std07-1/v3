<!--
    ArmedTriggers — армовані рівні, готові розбудити Арчі, з відстанню Δ до ціни.

    X28: level / delta / delta_pct рахує сервер (nearest-first sorting теж). Фронт
    лише форматує число + directional coloring (напрям вище/нижче = стрілка ↑/↓ +
    колір), НЕ перераховує домен. Стрілка (shape) дублює колір → WCAG для colorblind.
-->
<script lang="ts">
    import type { ArmedTrigger } from "../../lib/types";
    import { fmtPrice, fmtDelta, fmtPct } from "../../lib/format";

    let { armed = [] }: { armed: ArmedTrigger[] } = $props();

    const SOURCE_LABEL: Record<string, string> = {
        wake_condition: "умова",
        watch_level: "нагляд",
    };

    function sourceLabel(source: string): string {
        return SOURCE_LABEL[source] ?? source;
    }
</script>

{#if armed.length > 0}
    <div class="armed">
        <div class="armed-head">Армовані тригери</div>
        <ul class="armed-list">
            {#each armed as trig (trig.id)}
                {@const up = trig.direction === "above"}
                <li class="armed-row" class:up class:down={!up}>
                    <span class="arrow" aria-hidden="true">{up ? "↑" : "↓"}</span>
                    <span class="level">{fmtPrice(trig.level)}</span>
                    <span class="delta">
                        {fmtDelta(trig.delta)}
                        <span class="pct">({fmtPct(trig.delta_pct)})</span>
                    </span>
                    <span class="src">{sourceLabel(trig.source)}</span>
                </li>
            {/each}
        </ul>
    </div>
{/if}

<style>
    .armed {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .armed-head {
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-muted);
    }
    .armed-list {
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .armed-row {
        display: grid;
        grid-template-columns: 18px auto 1fr auto;
        align-items: baseline;
        gap: 8px;
        padding: 6px 10px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 8px;
        font-family: var(--font-mono);
        font-size: 12.5px;
    }
    .arrow {
        font-size: 13px;
        font-weight: 700;
        text-align: center;
    }
    .armed-row.up .arrow,
    .armed-row.up .delta {
        color: var(--positive);
    }
    .armed-row.down .arrow,
    .armed-row.down .delta {
        color: var(--danger);
    }
    .level {
        color: var(--text);
        font-weight: 600;
    }
    .delta {
        justify-self: start;
        white-space: nowrap;
    }
    .pct {
        color: var(--text-muted);
        font-size: 11px;
    }
    .src {
        justify-self: end;
        color: var(--text-muted);
        font-size: 11px;
        font-family: var(--font-ui);
    }
</style>
