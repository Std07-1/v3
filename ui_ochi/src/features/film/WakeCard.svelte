<!--
    WakeCard — одна картка кіноплівки пробудження.

    Хронологія одного wake: ⏰ причина + бейдж тригера → 👁 дзеркало (те, що Арчі
    реально побачив; collapsed) → 🧠 thinking (collapsed) → ✅ ack/emit_warning →
    📣 надіслане повідомлення → 💰 модель·токени·cost.

    Правила рендера (з контракту): mirror null → «дзеркало недоступне»; thinking
    null → «мислення не знайдено»; msg_len==0 → «мовчазне пробудження»;
    delivered=false → приглушена картка; ⚠ truncated бейдж. X28: category/alert
    приходять з бекенда — фронт лише форматує час/числа, не класифікує.
-->
<script lang="ts">
    import type { WakeCard } from "../../lib/types";
    import { timeAgo, absTime, fmtTokens, fmtCost } from "../../lib/format";

    let { card, nowMs }: { card: WakeCard; nowMs: number } = $props();

    let tsMs = $derived((card.ts ?? 0) * 1000);
    let ago = $derived(timeAgo(tsMs, nowMs));
    let abs = $derived(absTime(tsMs));

    let delivered = $derived(card.delivered !== false);
    let silent = $derived((card.msg_len ?? 0) === 0);

    let mirror = $derived(card.trace?.mirror ?? null);
    let mirrorLight = $derived(card.trace?.mirror_light === true);
    let thinking = $derived(card.thinking ?? null);
    let ack = $derived((card.ack ?? card.trace?.ack ?? "").trim());
    let warning = $derived((card.emit_warning ?? card.trace?.emit_warning ?? "").trim());
    let message = $derived((card.trace?.message ?? "").trim());

    const CATEGORY_LABEL: Record<string, string> = {
        watch: "нагляд",
        heartbeat: "пульс",
        price_cross: "пробій рівня",
        session_open: "відкриття сесії",
        volatility_spike: "сплеск волатильності",
        max_silence: "тиша",
        wake_condition: "умова",
    };
    const CALL_TYPE_LABEL: Record<string, string> = {
        platform_wake: "platform",
        proactive: "proactive",
        reactive: "reactive",
    };

    let categoryLabel = $derived(
        card.category ? (CATEGORY_LABEL[card.category] ?? card.category) : "",
    );
    let callTypeLabel = $derived(
        card.call_type ? (CALL_TYPE_LABEL[card.call_type] ?? card.call_type) : "",
    );

    // Причина часто = «timer:xxx — Людський опис»: людську частину — в заголовок,
    // технічний id — дрібним чипом (суто presentation-спліт рядка, не домен).
    let reasonHuman = $derived.by(() => {
        const raw = (card.reason ?? "—").trim();
        const sep = raw.indexOf(" — ");
        return sep > 0 ? raw.slice(sep + 3).trim() : raw;
    });
    let reasonTech = $derived.by(() => {
        const raw = (card.reason ?? "").trim();
        const sep = raw.indexOf(" — ");
        return sep > 0 ? raw.slice(0, sep).trim() : "";
    });
</script>

<article class="wake" class:muted={!delivered}>
    <!-- ⏰ причина + бейджі -->
    <div class="head">
        <div class="reason-row">
            <span class="clock" aria-hidden="true">⏰</span>
            <span class="reason">{reasonHuman}</span>
        </div>
        <div class="badges">
            {#if card.alert}
                <span class="badge alert" title="гучне пробудження">алярм</span>
            {/if}
            {#if categoryLabel}
                <span class="badge cat">{categoryLabel}</span>
            {/if}
            <time class="ago" title={abs}>{ago}</time>
        </div>
    </div>
    {#if reasonTech || callTypeLabel}
        <div class="tech-row">
            {#if reasonTech}<span class="tech" title="технічний тригер">{reasonTech}</span>{/if}
            {#if callTypeLabel}<span class="tech" title="тип виклику">{callTypeLabel}</span>{/if}
        </div>
    {/if}

    <!-- 👁 дзеркало + 🧠 thinking (collapsed; відсутні — просто не рендеряться,
         межу «до/після запису дзеркал» показує один роздільник у плівці) -->
    {#if mirror}
        <details class="fold">
            <summary>
                <span class="ico" aria-hidden="true">👁</span> що побачив Арчі
                {#if mirrorLight}<span class="tag-light">light</span>{/if}
            </summary>
            <pre class="mono-block">{mirror}</pre>
        </details>
    {/if}
    {#if thinking}
        <details class="fold">
            <summary><span class="ico" aria-hidden="true">🧠</span> мислення</summary>
            <pre class="mono-block">{thinking}</pre>
        </details>
    {/if}

    <!-- ✅ ack / emit_warning -->
    {#if ack}
        <div class="line ack"><span class="ico" aria-hidden="true">✅</span> {ack}</div>
    {/if}
    {#if warning}
        <div class="line warn"><span class="ico" aria-hidden="true">⚠</span> {warning}</div>
    {/if}

    <!-- 📣 надіслане повідомлення -->
    {#if silent}
        <div class="line silent"><span class="ico" aria-hidden="true">🔇</span> мовчазне пробудження</div>
    {:else if message}
        <div class="line msg"><span class="ico" aria-hidden="true">📣</span> {message}</div>
    {:else}
        <div class="line msg absent-inline" title="повний текст повідомлень зберігається для нових пробуджень (ADR-097)">
            <span class="ico" aria-hidden="true">📣</span>
            {card.msg_len} симв.
        </div>
    {/if}

    <!-- 💰 модель · токени · cost -->
    <div class="meter">
        <span class="ico" aria-hidden="true">💰</span>
        {#if card.model}<span class="model">{card.model}</span>{/if}
        <span class="tok" title="input / output токени">
            {fmtTokens(card.in)} → {fmtTokens(card.out)}
        </span>
        {#if card.cache_read}
            <span class="cache" title="cache read токени">cache {fmtTokens(card.cache_read)}</span>
        {/if}
        <span class="cost">{fmtCost(card.cost)}</span>
        {#if card.truncated}
            <span class="trunc" title="контекст обрізано">⚠ truncated</span>
        {/if}
    </div>
</article>

<style>
    .wake {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 14px 16px;
        background: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 12px;
        box-shadow: var(--card-shadow);
    }
    /* delivered=false → приглушена картка (heartbeat/тихі wake) */
    .wake.muted {
        opacity: 0.6;
        background: color-mix(in srgb, var(--surface) 60%, transparent);
    }

    .head {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        flex-wrap: wrap;
    }
    .reason-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        min-width: 0;
        flex: 1;
    }
    .clock { font-size: 13px; flex: none; }
    .reason {
        font-size: 14px;
        font-weight: 600;
        color: var(--text);
        line-height: 1.35;
    }
    .badges {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
    }
    .badge {
        font-size: 10.5px;
        padding: 2px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        color: var(--text-muted);
        background: var(--surface2);
        white-space: nowrap;
        letter-spacing: 0.02em;
    }
    .badge.alert {
        color: var(--warning);
        border-color: color-mix(in srgb, var(--warning) 45%, var(--border));
        background: color-mix(in srgb, var(--warning) 12%, transparent);
        font-weight: 600;
        text-transform: uppercase;
    }
    .badge.cat {
        color: var(--accent);
        border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
    }
    .ago {
        font-size: 11.5px;
        color: var(--text-muted);
        white-space: nowrap;
    }
    .tech-row {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin-top: -4px;
    }
    .tech {
        font-family: var(--font-mono);
        font-size: 10px;
        color: var(--text-muted);
        opacity: 0.75;
        background: var(--surface);
        border-radius: 5px;
        padding: 1px 6px;
    }

    /* collapsed folds */
    .fold {
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface);
        overflow: hidden;
    }
    .fold summary {
        cursor: pointer;
        padding: 7px 10px;
        font-size: 12px;
        color: var(--text-muted);
        user-select: none;
        list-style: none;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .fold summary::-webkit-details-marker { display: none; }
    .fold summary:hover { color: var(--text); }
    .fold[open] summary {
        border-bottom: 1px solid var(--border);
        color: var(--text);
    }
    .tag-light {
        font-size: 9.5px;
        padding: 1px 6px;
        border-radius: 999px;
        background: var(--surface2);
        color: var(--text-muted);
    }
    .mono-block {
        margin: 0;
        padding: 10px 12px;
        font-family: var(--font-mono);
        font-size: 11.5px;
        line-height: 1.55;
        color: var(--text);
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 340px;
        overflow-y: auto;
    }

    /* lines */
    .line {
        display: flex;
        gap: 7px;
        font-size: 13px;
        line-height: 1.5;
        color: var(--text);
    }
    .line .ico { flex: none; }
    .line.ack { color: var(--positive); }
    .line.warn { color: var(--warning); }
    .line.silent { color: var(--text-muted); font-style: italic; }
    .line.msg { color: var(--text); }
    .line.absent-inline { color: var(--text-muted); }

    /* meter — службовий футер, максимально тихий */
    .meter {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        margin-top: 2px;
        padding-top: 7px;
        border-top: 1px solid var(--border);
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-muted);
        opacity: 0.85;
    }
    .meter .model { color: var(--text-muted); }
    .meter .cost { color: var(--gold); font-weight: 600; }
    .meter .trunc { color: var(--danger); }
</style>
