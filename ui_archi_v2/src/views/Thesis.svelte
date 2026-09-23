<!--
    Thesis — публічна трейдерська/інвесторська поверхня ГОРНа.

    Не дашборд, який хтось для тебе наповнив картками — знімок процесу, що триває
    незалежно від того, дивишся ти на нього чи ні. Тому: без скляних карток/тіней,
    структура тримається на лінійках-розділювачах і монопростіровому metadata,
    як у сирому виводі процесу, а не у вітрині.

    X28: рендерить лише те, що прийшло у ThesisSnapshot (thesisData.ts) — жодного
    домен-перерахунку тут. Зараз дані демонстраційні (DEMO_THESES); реальне джерело —
    санітизований публічний знімок з trader-v3 (ще не збудовано, окремий ADR).
-->
<script lang="ts">
    import {
        DEMO_THESES,
        DEMO_TRACK_RECORD,
        THESIS_SYMBOLS,
        type ThesisSymbol,
        type PriceLadder,
    } from "../features/thesis/thesisData";
    import { sanitizeHtml } from "../lib/sanitize";

    let symbol = $state<ThesisSymbol>("XAU/USD");
    let snapshot = $derived(DEMO_THESES[symbol]);

    const STATE_LABEL: Record<string, string> = {
        active: "веде тезу",
        waiting: "чекає входу",
        flat: "поза ринком",
    };
    // локальний колір стану — НЕ чіпає глобальний --accent (те належить приватному mood-контуру)
    const STATE_COLOR: Record<string, string> = {
        active: "var(--gold)",
        waiting: "#2dd4bf",
        flat: "var(--text-muted)",
    };
    const SIDE_FLAG: Record<string, string> = {
        long: "[ ▲ ЛОНГ ]",
        short: "[ ▼ ШОРТ ]",
    };

    const fmt = (n: number) => n.toLocaleString("en-US").replace(/,/g, " ");

    const LADDER_H = 320;
    const LADDER_PAD = 12;
    function ladderY(price: number, ladder: PriceLadder): number {
        return LADDER_PAD + ((ladder.max - price) / (ladder.max - ladder.min)) * (LADDER_H - 2 * LADDER_PAD);
    }
    function ladderZoneTop(ladder: PriceLadder): number {
        return Math.min(ladderY(ladder.entryZone[1], ladder), ladderY(ladder.entryZone[0], ladder));
    }
    function ladderZoneHeight(ladder: PriceLadder): number {
        const span = Math.abs(ladderY(ladder.entryZone[1], ladder) - ladderY(ladder.entryZone[0], ladder));
        return Math.max(span, 28);
    }
</script>

<div class="thesis-page" style="--state-color: {STATE_COLOR[snapshot.state]}">
    <header class="status-line">
        <span class="pulse" aria-hidden="true"></span>
        <span class="brand">Горн</span>
        <span class="sep" aria-hidden="true">·</span>
        <span class="state-note">Арчі {STATE_LABEL[snapshot.state]}</span>
        <span class="fill"></span>
        <span class="updated mono">оновлено {snapshot.updatedAt}</span>
        <span class="demo-tag" title="Прототип — демо-дані">демо</span>
    </header>

    <nav class="instruments" aria-label="Інструменти">
        {#each THESIS_SYMBOLS as sym (sym)}
            <button
                class="mono"
                class:active={sym === symbol}
                aria-pressed={sym === symbol}
                onclick={() => (symbol = sym)}
            >
                <span class="marker" aria-hidden="true">{sym === symbol ? "▸" : ""}</span>{sym}
            </button>
        {/each}
    </nav>

    <section class="hero">
        <div class="line">
            <span class="price mono">{snapshot.price}</span>
            <span class="chg mono" class:up={snapshot.changeUp} class:down={!snapshot.changeUp}
                >{snapshot.changePct}</span
            >
            <span class="flag mono">{snapshot.side ? SIDE_FLAG[snapshot.side] : "[ ПОЗА РИНКОМ ]"}</span>
        </div>
        <p class="thesis-text">{@html sanitizeHtml(snapshot.thesisHtml)}</p>
        <div class="meta mono">
            <span>зміщення <b>{snapshot.bias}</b></span>
            <span>сесія <b>{snapshot.session}</b></span>
        </div>
        {#if snapshot.confidence}
            <div class="confidence">
                <div class="row mono">
                    <span>впевненість</span>
                    <span>{snapshot.confidence.pct}% · грейд {snapshot.confidence.grade}</span>
                </div>
                <div class="meter"><div class="fill" style="width:{snapshot.confidence.pct}%"></div></div>
                <p class="why">{snapshot.confidence.why}</p>
            </div>
        {/if}
    </section>

    {#if snapshot.ladder}
        {@const ladder = snapshot.ladder}
        <section>
            <h2>Сценарій у цінах</h2>
            <p class="sub">зона входу · цілі · де план скасовується</p>
            <div class="ladder-wrap">
                <div class="ladder" style="height:{LADDER_H}px">
                    <div class="rail"></div>
                    <div class="lv inv" style="top:{ladderY(ladder.invalidation, ladder)}px">
                        <span class="p mono">{fmt(ladder.invalidation)}</span><span class="tick"></span>
                        <span class="lab"><b>інвалідація</b> — тут теза скасовується</span>
                    </div>
                    <div class="zone" style="top:{ladderZoneTop(ladder)}px;height:{ladderZoneHeight(ladder)}px">
                        <span class="lab mono">зона входу</span>
                        <span class="pp mono">{fmt(ladder.entryZone[0])}–{fmt(ladder.entryZone[1])}</span>
                    </div>
                    {#each ladder.targets as t (t.label)}
                        <div class="lv tp" style="top:{ladderY(t.price, ladder)}px">
                            <span class="p mono">{fmt(t.price)}</span><span class="tick"></span>
                            <span class="lab"><b>{t.label}</b> ціль</span>
                        </div>
                    {/each}
                    <div class="now" style="top:{ladderY(ladder.now, ladder)}px">
                        <span class="ln"></span>
                        <span class="tag mono"><i></i>зараз {fmt(ladder.now)}</span>
                    </div>
                </div>
            </div>
            {#if snapshot.riskRewardHtml}
                <p class="rr mono">{@html sanitizeHtml(snapshot.riskRewardHtml)}</p>
            {/if}
        </section>
    {/if}

    <section>
        <h2>Трек-рекорд тез</h2>
        <p class="sub">кожна теза фіксується до руху ціни — і чесно закривається, без винятків</p>
        <div class="track">
            <span class="cells">
                {#each DEMO_TRACK_RECORD.cells as c, i (i)}
                    <i class={c ? "w" : "l"} title={c ? "теза справдилась" : "теза не справдилась"}></i>
                {/each}
            </span>
            <span class="stat mono"
                ><b>{DEMO_TRACK_RECORD.winRate}</b> тез справдились · {DEMO_TRACK_RECORD.windowLabel} · вибірка {
                    DEMO_TRACK_RECORD.sampleSize
                }</span
            >
        </div>
    </section>

    <section>
        <h2>Під наглядом</h2>
        <p class="sub">рівні, на яких Арчі прокинеться і перегляне тезу</p>
        <ul class="watch">
            {#each snapshot.watch as w, i (i)}
                <li>
                    <span class="p mono">{w.price}</span>
                    <span class="d mono">{w.direction}</span>
                    <span class="note">{w.note}</span>
                    {#if w.critical}<span class="prio mono">критичний</span>{/if}
                </li>
            {/each}
        </ul>
    </section>

    <section>
        <h2>Фактори впливу</h2>
        <p class="sub">що зараз тисне на ціну — макро і календар</p>
        <div class="facts">
            {#each snapshot.factors as f, i (i)}
                <div class="fact">
                    <span class="k mono">{f.label}</span>
                    <span class="v">{f.detail}</span>
                    <span class="tone {f.tone}">{f.tag}</span>
                </div>
            {/each}
        </div>
        <div class="armed">
            <span class="k mono">Що змінить план</span>
            <p>{@html sanitizeHtml(snapshot.armedHtml)}</p>
        </div>
    </section>

    <p class="manifesto">
        Ти нічим тут не керуєш — лише дивишся, як працює автономний розум. Арчі прокидається, коли
        ринок дає привід: злам структури, дотик рівня, вихід новини — перевіряє тезу і сам оновлює
        цю сторінку. Він не ховає програші і нічого не продає силою. Одна сторінка. Одна теза. Нуль
        шуму.
    </p>

    <footer>
        <div class="cta">
            <span class="link">→ сигнали в Telegram <i>· скоро</i></span>
            <span class="soon">чат з Арчі · скоро, за підпискою</span>
        </div>
        <p class="disclaimer">
            Арчі — експериментальний автономний агент. Тези — аналітичні сценарії, не інвестиційні
            рекомендації. Торгівля з кредитним плечем може призвести до втрати капіталу.
        </p>
    </footer>
</div>

<style>
    .thesis-page {
        min-height: var(--app-vh, 100vh);
        background: var(--bg);
        color: var(--text);
        padding: 22px 20px 64px;
    }
    .mono {
        font-family: var(--font-mono);
        font-variant-numeric: tabular-nums;
    }
    section,
    .status-line,
    .instruments,
    .hero,
    .manifesto,
    footer {
        max-width: 680px;
        margin-left: auto;
        margin-right: auto;
    }

    /* ── status line: сирий readout, не банер ── */
    .status-line {
        display: flex;
        align-items: center;
        gap: 9px;
        font-size: 12px;
        color: var(--text-muted);
        flex-wrap: wrap;
    }
    .pulse {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--state-color);
        flex: none;
    }
    @media (prefers-reduced-motion: no-preference) {
        .pulse {
            animation: pulse 2.6s ease-in-out infinite;
        }
        @keyframes pulse {
            0%,
            100% {
                opacity: 1;
            }
            50% {
                opacity: 0.35;
            }
        }
    }
    .brand {
        font-weight: 650;
        letter-spacing: 0.3em;
        text-transform: uppercase;
        color: var(--text);
        font-size: 12px;
    }
    .sep {
        opacity: 0.5;
    }
    .fill {
        flex: 1;
    }
    .demo-tag {
        font-size: 10px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--warning);
        border: 1px solid color-mix(in srgb, var(--warning) 40%, var(--border));
        border-radius: 4px;
        padding: 1px 6px;
    }

    /* ── instrument selector: не таби-пігулки, а перемикач каналу ── */
    .instruments {
        display: flex;
        gap: 18px;
        margin-top: 26px;
        padding-bottom: 18px;
        border-bottom: 1px solid var(--border);
        overflow-x: auto;
    }
    .instruments button {
        background: none;
        border: none;
        padding: 0;
        font-size: 12.5px;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        cursor: pointer;
        white-space: nowrap;
    }
    .instruments button.active {
        color: var(--text);
    }
    .instruments .marker {
        color: var(--state-color);
        display: inline-block;
        width: 12px;
    }
    .instruments button:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 3px;
    }

    /* ── hero ── */
    .hero {
        margin-top: 30px;
    }
    .line {
        display: flex;
        align-items: baseline;
        gap: 14px;
        flex-wrap: wrap;
        margin-bottom: 14px;
    }
    .price {
        font-size: 26px;
        font-weight: 600;
    }
    .chg {
        font-size: 13px;
    }
    .chg.up {
        color: var(--positive);
    }
    .chg.down {
        color: var(--danger);
    }
    .flag {
        font-size: 12px;
        color: var(--state-color);
        letter-spacing: 0.03em;
    }
    .thesis-text {
        font-size: 19px;
        line-height: 1.55;
        font-weight: 400;
        max-width: 58ch;
        text-wrap: balance;
    }
    .thesis-text :global(em) {
        font-style: normal;
        color: var(--state-color);
    }
    .meta {
        display: flex;
        gap: 8px 20px;
        flex-wrap: wrap;
        font-size: 12px;
        color: var(--text-muted);
        margin-top: 14px;
    }
    .meta b {
        color: var(--text);
        font-weight: 600;
    }
    .confidence {
        margin-top: 16px;
        max-width: 320px;
    }
    .confidence .row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        font-size: 11.5px;
        color: var(--text-muted);
        margin-bottom: 5px;
    }
    .confidence .meter {
        height: 2px;
        background: var(--surface2);
    }
    .confidence .fill {
        height: 100%;
        background: var(--state-color);
    }
    .confidence .why {
        font-size: 12.5px;
        color: var(--text-muted);
        margin-top: 7px;
    }

    /* ── sections: без карток — лінійки-розділювачі, monospace metadata ── */
    section {
        margin-top: 40px;
    }
    h2 {
        font-size: 11px;
        font-family: var(--font-mono);
        font-weight: 600;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--text-muted);
        margin: 0 0 4px;
    }
    .sub {
        font-size: 12.5px;
        color: var(--text-muted);
        opacity: 0.75;
        margin: 0 0 16px;
    }

    /* ── ladder ── */
    .ladder-wrap {
        border-left: 1px solid var(--border);
        padding-left: 18px;
    }
    .ladder {
        position: relative;
    }
    .rail {
        position: absolute;
        left: 100px;
        top: 0;
        bottom: 0;
        width: 1px;
        background: var(--border);
    }
    .lv {
        position: absolute;
        left: 0;
        right: 0;
        display: flex;
        align-items: center;
        gap: 10px;
        transform: translateY(-50%);
    }
    .lv .p {
        width: 82px;
        text-align: right;
        font-size: 13px;
        font-weight: 600;
        flex: none;
    }
    .lv .tick {
        width: 30px;
        height: 0;
        border-top: 2px solid var(--border);
        flex: none;
        margin-left: 6px;
    }
    .lv .lab {
        font-size: 12.5px;
        color: var(--text-muted);
    }
    .lv .lab b {
        color: var(--text);
        font-weight: 600;
    }
    .lv.inv .p {
        color: var(--danger);
    }
    .lv.inv .tick {
        border-top-style: dashed;
        border-top-color: var(--danger);
    }
    .lv.tp .p {
        color: var(--positive);
    }
    .lv.tp .tick {
        border-top-color: var(--positive);
    }
    .zone {
        position: absolute;
        left: 6px;
        right: 0;
        border-left: 3px solid var(--state-color);
        display: flex;
        align-items: center;
        padding: 0 0 0 10px;
        gap: 10px;
    }
    .zone .lab {
        font-size: 11.5px;
        color: var(--state-color);
    }
    .zone .pp {
        margin-left: auto;
        font-size: 12.5px;
        color: var(--state-color);
    }
    .now {
        position: absolute;
        left: 6px;
        right: 0;
        display: flex;
        align-items: center;
        gap: 8px;
        transform: translateY(-50%);
    }
    .now .ln {
        flex: 1;
        height: 0;
        border-top: 1px solid var(--text);
        opacity: 0.5;
    }
    .now .tag {
        font-size: 11.5px;
        background: var(--surface2);
        border: 1px solid var(--border);
        padding: 3px 9px;
        border-radius: 5px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .now .tag i {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--state-color);
        font-style: normal;
    }
    @media (prefers-reduced-motion: no-preference) {
        .now .tag i {
            animation: pulse 1.7s infinite;
        }
    }
    .rr {
        margin-top: 14px;
        font-size: 12.5px;
        color: var(--text-muted);
    }
    .rr :global(strong) {
        color: var(--text);
        font-weight: 600;
    }

    /* ── track record ── */
    .track {
        display: flex;
        align-items: center;
        gap: 14px;
        flex-wrap: wrap;
    }
    .cells {
        display: flex;
        gap: 3px;
    }
    .cells i {
        width: 14px;
        height: 14px;
        border-radius: 3px;
        font-style: normal;
        position: relative;
        display: inline-block;
    }
    .cells i.w {
        background: color-mix(in srgb, var(--positive) 22%, var(--surface2));
    }
    .cells i.l {
        background: color-mix(in srgb, var(--danger) 20%, var(--surface2));
    }
    .cells i.w::after {
        content: "✓";
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
        font-size: 8.5px;
        color: var(--positive);
    }
    .cells i.l::after {
        content: "✕";
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
        font-size: 8px;
        color: var(--danger);
    }
    .track .stat {
        font-size: 12.5px;
        color: var(--text-muted);
    }
    .track .stat b {
        color: var(--text);
        font-size: 14px;
    }

    /* ── watch levels ── */
    .watch {
        list-style: none;
        margin: 0;
        padding: 0;
    }
    .watch li {
        display: flex;
        align-items: baseline;
        gap: 12px;
        padding: 11px 0;
        border-top: 1px solid var(--border);
    }
    .watch li:first-child {
        border-top: none;
        padding-top: 0;
    }
    .watch .p {
        font-size: 13.5px;
        font-weight: 600;
        min-width: 82px;
        text-align: right;
    }
    .watch .d {
        font-size: 10.5px;
        color: var(--text-muted);
        opacity: 0.75;
        min-width: 46px;
    }
    .watch .note {
        font-size: 14px;
        color: var(--text-muted);
    }
    .watch .prio {
        font-size: 9.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--state-color);
        border: 1px solid color-mix(in srgb, var(--state-color) 45%, var(--border));
        border-radius: 4px;
        padding: 2px 7px;
        margin-left: auto;
        flex: none;
    }

    /* ── factors ── */
    .fact {
        display: flex;
        align-items: baseline;
        gap: 12px;
        padding: 11px 0;
        border-top: 1px solid var(--border);
    }
    .fact:first-child {
        border-top: none;
        padding-top: 0;
    }
    .fact .k {
        font-size: 12px;
        font-weight: 600;
        min-width: 112px;
        color: var(--text);
    }
    .fact .v {
        font-size: 14px;
        color: var(--text-muted);
    }
    .fact .tone {
        font-family: var(--font-mono);
        font-size: 10px;
        letter-spacing: 0.06em;
        padding: 2px 8px;
        border-radius: 4px;
        margin-left: auto;
        flex: none;
    }
    .tone.risk {
        color: var(--danger);
        border: 1px solid color-mix(in srgb, var(--danger) 45%, var(--border));
    }
    .tone.tailwind {
        color: var(--positive);
        border: 1px solid color-mix(in srgb, var(--positive) 45%, var(--border));
    }
    .tone.info {
        color: var(--text-muted);
        border: 1px solid var(--border);
    }

    .armed {
        margin-top: 18px;
        padding-top: 14px;
        border-top: 1px dashed var(--border);
    }
    .armed .k {
        font-size: 10.5px;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--state-color);
    }
    .armed p {
        margin: 7px 0 0;
        font-size: 14px;
        color: var(--text-muted);
    }
    .armed p :global(strong) {
        color: var(--text);
        font-weight: 600;
    }

    /* ── manifesto ── */
    .manifesto {
        margin-top: 48px;
        padding: 24px 0;
        border-top: 1px solid var(--border);
        border-bottom: 1px solid var(--border);
        font-size: 15px;
        color: var(--text-muted);
        line-height: 1.65;
        text-wrap: balance;
    }

    /* ── footer ── */
    footer {
        margin-top: 26px;
        font-size: 12.5px;
        color: var(--text-muted);
    }
    .cta {
        display: flex;
        align-items: baseline;
        gap: 20px;
        flex-wrap: wrap;
        margin-bottom: 16px;
    }
    .cta .link {
        font-family: var(--font-mono);
        font-size: 13px;
        font-weight: 600;
        color: var(--state-color);
    }
    .cta .link i {
        font-style: normal;
        font-weight: 400;
        opacity: 0.65;
    }
    .cta .soon {
        opacity: 0.7;
    }
    .disclaimer {
        line-height: 1.6;
        opacity: 0.85;
    }

    @media (max-width: 480px) {
        .thesis-text {
            font-size: 17px;
        }
        .price {
            font-size: 22px;
        }
        .rail {
            left: 88px;
        }
        .lv .p {
            width: 72px;
            font-size: 12px;
        }
        .fact .k {
            min-width: 92px;
        }
    }
</style>
