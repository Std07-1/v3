<!--
    Home — «Обсерваторія»: сцена довкола живого кільця (кільце дає PresenceLayer,
    центр екрана, r = 0.2·min(w,h) — НЕ стискається і НЕ накривається текстом).

    Композиція (десктоп):
      ┌ ГОРН + одне речення     ······················  XAU/USD ▼ ШОРТ ┐
      │                                                    │ 4348 інвалідація
      │                ( КІЛЬЦЕ )            промінь ────▶ ▓ 4335 зона входу
      │                                                    │ 4305 ціль
      │            статус · думка Арчі                     │ 4267 ціль
      └ ● прокинеться за … ····························· демо-рядок ┘

    Права вертикальна цінова вісь = армовані рівні зі знімка (мова трейдера —
    price scale). Рівень, на який Арчі зараз дивиться (entry/trigger перший),
    підсвічений на самій осі (.gaze) — без окремого променя (owner 12.07: промінь
    прибрано, не тягнув на дизайн-якість).

    Мобілка (≤768px): НЕ стиснута копія десктопу. Вертикальна вісь збоку від кільця
    не має де стояти на вузькому екрані (кільце займає центр по горизонталі) — тому
    рівні йдуть горизонтальним рядом ПІД кільцем, разом з тезою, одна колонка.

    X28: dumb renderer. Рівні + підписи приходять готові з /api/public/snapshot;
    тут лише геометрія і directional coloring. Нуль ручного контенту.
-->
<script lang="ts">
    import { presenceModeLabel, type PresenceMode } from "../features/presence/presenceState";
    import {
        getSnapshot,
        getLastSnapshotSyncMs,
        type ArmedLevel,
    } from "../lib/publicSnapshot.svelte";
    import { onMount } from "svelte";

    let { mode = "sleep" as PresenceMode } = $props<{ mode?: PresenceMode }>();

    let snapshot = $derived(getSnapshot());
    let presence = $derived(snapshot?.presence ?? null);
    let focus = $derived(snapshot?.focus ?? null);
    let watching = $derived(snapshot?.watching ?? []);

    let asleep = $derived(mode === "sleep");

    const fmt = (n: number) => n.toLocaleString("en-US").replace(/,/g, " ");
    const DIR_LABEL: Record<string, string> = { long: "▲ лонг", short: "▼ шорт" };
    // напрям рівня: "" (синтезований фолбек без direction) → нема стрілки, не вигадуємо (X28)
    const arrowFor = (d: string) => (d === "above" ? "↑" : d === "below" ? "↓" : "");

    // ── чесність зв'язку (аудит S2): «немає даних» ≠ «Арчі спить» ──
    // connecting: знімок ще не приїхав · lost: полінг мовчить > 3 інтервали.
    const LINK_LOST_MS = 45_000;
    let nowMs = $state(Date.now());
    onMount(() => {
        const id = setInterval(() => (nowMs = Date.now()), 5_000);
        return () => clearInterval(id);
    });
    let link = $derived.by((): "connecting" | "live" | "lost" => {
        if (!snapshot) return "connecting";
        const sync = getLastSnapshotSyncMs();
        return sync && nowMs - sync > LINK_LOST_MS ? "lost" : "live";
    });

    // ── жива «прокинеться за …» ──
    let wakeInLabel = $derived.by(() => {
        const t = Number(presence?.next_wake_ms) || 0;
        if (!t) return "";
        const dMin = Math.round((t - nowMs) / 60_000);
        // заморожений час далеко в минулому = застиглий знімок, не «ось-ось»
        if (dMin < -3) return "";
        if (dMin <= 0) return "ось-ось";
        if (dMin < 60) return `за ~${dMin} хв`;
        const h = Math.floor(dMin / 60), m = dMin % 60;
        return m ? `за ~${h} год ${m} хв` : `за ~${h} год`;
    });

    // ── цінова вісь: мапінг цін у відсотки висоти шкали ──
    // домен = усі армовані рівні + межі зони входу (щоб діапазон входу був у кадрі)
    let axis = $derived.by(() => {
        if (!watching.length) return null;
        const prices = watching.map((w) => w.level);
        const ez = focus?.entry_zone;
        if (ez) prices.push(ez[0], ez[1]);
        const lo = Math.min(...prices), hi = Math.max(...prices);
        const span = hi - lo || 1;
        const pad = span * 0.14;
        const top = hi + pad, range = span + 2 * pad;
        const yPct = (p: number) => ((top - p) / range) * 100;
        return {
            yPct,
            levels: watching.map((w) => ({ ...w, y: yPct(w.level) })),
            zone: ez ? { top: yPct(Math.max(ez[0], ez[1])), bot: yPct(Math.min(ez[0], ez[1])) } : null,
        };
    });

    // куди Арчі «дивиться» зараз: entry/trigger перший, інакше — найпріоритетніший рівень.
    // Підсвічується прямо на осі/чіпі (.gaze) — без окремого променя (owner: приберіть,
    // не тягнув на дизайн-якість).
    let gazeLevel = $derived.by((): (ArmedLevel & { y: number }) | null => {
        if (!axis) return null;
        return (
            axis.levels.find((l) => l.role === "entry" || l.role === "trigger") ??
            axis.levels[0] ??
            null
        );
    });
</script>

<div class="scene" class:asleep>
    <!-- ambient глибина (тонка, тече з mood-accent) -->
    <div class="ambient" aria-hidden="true"></div>

    <!-- кути порожні свідомо (owner 12.07): нуль хрому — кільце і є ідентичність -->

    <!-- права цінова вісь: армовані рівні (де він прокинеться) -->
    {#if axis}
        <aside class="axis" class:dimmed={asleep} aria-label="Рівні, на яких Арчі прокинеться">
            <div class="axis-h mono">озброєно</div>
            <div class="scale" bind:this={scaleEl}>
                <div class="rail"></div>
                {#if axis.zone}
                    <div class="zone" style="top:{axis.zone.top}%;height:{Math.max(axis.zone.bot - axis.zone.top, 1.5)}%"></div>
                {/if}
                {#each axis.levels as l (l.level)}
                    <div class="lv lv-{l.role}" class:gaze={l.level === gazeLevel?.level} style="top:{l.y}%">
                        <span class="ll">{l.label}</span>
                        <span class="tick"></span>
                        <span class="lp mono">{fmt(l.level)}</span>
                        <span class="ld mono">{arrowFor(l.direction)}</span>
                    </div>
                {/each}
            </div>
        </aside>
    {/if}

    <!-- під кільцем: стан одним словом + ТЕЗА як публічний голос (owner 12.07:
         сирі думки не для інвесторів; поза ринком — без приписок, ми не виправдовуємось).
         connecting/lost ≠ сон: «немає даних» кажемо чесно (аудит S2). -->
    <div class="voice">
        {#if link === "connecting"}
            <div class="status asleep">з'єднуюсь…</div>
        {:else if link === "lost"}
            <div class="status asleep">зв'язок втрачено · знімок застиг</div>
            {#if focus?.thesis}<p class="thesis-main stale">{focus.thesis}</p>{/if}
        {:else}
            <div class="status" class:asleep>
                {focus ? presenceModeLabel(mode) : "поза ринком"}
            </div>
            {#if focus}
                <div class="focus-line mono" class:dozing={asleep}>
                    <span class="sym">{focus.symbol}</span>
                    {#if focus.direction}
                        <span class="dir dir-{focus.direction}">{DIR_LABEL[focus.direction] ?? focus.direction}</span>
                    {/if}
                    {#if focus.grade}<span class="dim-sep">·</span><span class="meta-i">грейд {focus.grade}</span>{/if}
                    {#if focus.confidence_pct != null}<span class="dim-sep">·</span><span class="meta-i">{focus.confidence_pct}%</span>{/if}
                </div>
                {#if focus.thesis}
                    <p class="thesis-main" class:stale={asleep}>{focus.thesis}</p>
                {/if}
                {#if axis}
                    <!-- мобілка: рівні горизонтальним рядом під тезою (десктоп ховає — там своя вісь) -->
                    <div class="watch-mobile mono" class:dozing={asleep}>
                        {#each axis.levels as l (l.level)}
                            <div class="wc wc-{l.role}" class:gaze={l.level === gazeLevel?.level}>
                                <span class="wc-a">{arrowFor(l.direction)}</span>
                                <span class="wc-p">{fmt(l.level)}</span>
                            </div>
                        {/each}
                    </div>
                {/if}
            {/if}
        {/if}
    </div>

    <!-- низ ліворуч: він має план (тільки наживо — застигле «ось-ось» = брехня) -->
    {#if link === "live" && presence?.next_wake_reason && wakeInLabel}
        <div class="corner bl mono">
            <span class="wk-dot" aria-hidden="true"></span>
            прокинеться {wakeInLabel} · {presence.next_wake_reason}
        </div>
    {/if}

    <!-- низ праворуч: мінімальний захисний рядок (без «демо» — дані живі) -->
    <div class="corner br mono">не є фінансовою порадою</div>
</div>

<style>
    .scene {
        position: absolute;
        inset: 0;
        overflow: hidden;
        pointer-events: auto;
    }
    .mono {
        font-family: var(--font-mono);
        font-variant-numeric: tabular-nums;
    }

    /* ── ambient: тонке верхнє світло від mood (глибина сцени) ── */
    .ambient {
        position: absolute;
        inset: 0;
        pointer-events: none;
        background:
            radial-gradient(900px 480px at 18% -8%,
                color-mix(in srgb, var(--accent) 7%, transparent), transparent 60%),
            radial-gradient(700px 420px at 100% 108%,
                color-mix(in srgb, var(--accent) 4%, transparent), transparent 55%);
        transition: opacity 1.2s ease;
    }
    .scene.asleep .ambient { opacity: 0.35; }

    /* ── кути (лишились тільки нижні) ── */
    .corner { position: absolute; z-index: 3; }
    .bl { bottom: 22px; left: 30px; }
    .br { bottom: 22px; right: 30px; }

    /* фокус-рядок над тезою: символ + напрям + грейд/впевненість */
    .focus-line {
        display: flex;
        align-items: baseline;
        justify-content: center;
        gap: 10px;
        font-size: 13px;
        flex-wrap: wrap;
    }
    .sym {
        color: var(--text);
        font-weight: 600;
        letter-spacing: 0.04em;
    }
    .dir { font-weight: 600; letter-spacing: 0.08em; }
    .dir-long { color: var(--positive); }
    .dir-short { color: var(--danger); }
    .dim-sep { color: var(--text-muted); opacity: 0.5; }
    .meta-i { color: var(--text-muted); font-size: 11.5px; }
    .focus-line.dozing { opacity: 0.55; }

    /* ── права цінова вісь ── */
    .axis {
        position: absolute;
        z-index: 2;
        right: 30px;
        top: 50%;
        transform: translateY(-50%);
        height: min(56vh, 520px);
        display: flex;
        flex-direction: column;
        transition: opacity 0.8s ease;
    }
    /* сон гасить світло, не правду: рівні лишаються читабельними */
    .axis.dimmed { opacity: 0.5; }
    .corner.dozing { opacity: 0.55; transition: opacity 0.8s ease; }
    .axis-h {
        font-size: 9.5px;
        letter-spacing: 0.3em;
        text-transform: uppercase;
        color: color-mix(in srgb, var(--accent) 60%, var(--text-muted));
        text-align: right;
        margin-bottom: 12px;
        transition: color 1.2s ease;
    }
    .scale {
        position: relative;
        flex: 1;
        min-height: 0;
        width: 240px;
    }
    .rail {
        position: absolute;
        right: 74px;
        top: 0;
        bottom: 0;
        width: 1px;
        background: linear-gradient(
            to bottom,
            transparent,
            color-mix(in srgb, var(--text-muted) 42%, transparent) 12%,
            color-mix(in srgb, var(--text-muted) 42%, transparent) 88%,
            transparent
        );
    }
    /* зона входу: діапазон на шкалі + м'яке світло вліво (де він чекає ціну) */
    .zone {
        position: absolute;
        right: 74px;
        width: 3px;
        border-radius: 2px;
        background: color-mix(in srgb, var(--accent) 75%, transparent);
        box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 45%, transparent);
        transition: background 1.2s ease;
    }
    .zone::before {
        content: "";
        position: absolute;
        right: 3px;
        top: 0;
        bottom: 0;
        width: 90px;
        background: linear-gradient(to left,
            color-mix(in srgb, var(--accent) 14%, transparent), transparent);
    }
    .lv {
        position: absolute;
        left: 0;
        right: 0;
        display: flex;
        align-items: center;
        gap: 8px;
        transform: translateY(-50%);
        white-space: nowrap;
    }
    .ll {
        flex: 1;
        text-align: right;
        font-size: 11.5px;
        color: var(--text-muted);
    }
    .tick {
        width: 10px;
        height: 1px;
        background: var(--text-muted);
        opacity: 0.7;
        flex: none;
    }
    .lp {
        width: 46px;
        font-size: 13px;
        font-weight: 600;
        text-align: left;
        color: var(--text);
        flex: none;
    }
    .ld {
        font-size: 10px;
        color: var(--text-muted);
        opacity: 0.7;
        flex: none;
        width: 10px;
    }
    /* directional coloring за роллю (роль прийшла зі знімка) */
    .lv-invalidation .lp { color: var(--danger); }
    .lv-invalidation .tick { background: var(--danger); }
    .lv-target .lp { color: var(--positive); }
    .lv-target .tick { background: var(--positive); }
    .lv-entry .lp, .lv-trigger .lp {
        color: color-mix(in srgb, var(--accent) 85%, var(--text));
    }
    .lv-entry .tick, .lv-trigger .tick {
        background: color-mix(in srgb, var(--accent) 80%, transparent);
    }
    /* рівень, на який він дивиться — ледь підсвічений */
    .lv.gaze .ll { color: var(--text); }
    .lv.gaze .lp { text-shadow: 0 0 12px color-mix(in srgb, var(--accent) 55%, transparent); }

    /* ── голос: статус + думка (під кільцем, НЕ на ньому) ── */
    .voice {
        position: absolute;
        z-index: 2;
        left: 50%;
        transform: translateX(-50%);
        /* низ кільця = 50vh + 20vmin (R=0.2·min) → стаємо нижче з запасом на дихання */
        top: calc(50vh + 22vmin + 20px);
        /* право думки не повинно заходити під вісь (240+30 справа) на середніх екранах */
        width: clamp(300px, 100vw - 640px, 560px);
        text-align: center;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 10px;
    }
    .status {
        font-size: 11px;
        letter-spacing: 0.26em;
        text-transform: uppercase;
        color: color-mix(in srgb, var(--accent) 70%, var(--text-muted));
        transition: color 1.2s ease;
    }
    .status.asleep { color: var(--text-muted); }
    /* теза = публічний голос (owner 12.07: транслюємо тези, не внутрішній монолог) */
    .thesis-main {
        margin: 0;
        font-size: 17.5px;
        line-height: 1.55;
        font-weight: 350;
        color: var(--text);
        text-shadow: 0 0 34px rgba(0, 0, 0, 0.45);
        display: -webkit-box;
        -webkit-line-clamp: 4;
        line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    /* застигла теза (сон / втрачений зв'язок) — видима, але явно не «щойно» */
    .thesis-main.stale {
        opacity: 0.55;
    }

    /* ── мобільний рядок рівнів (прихований на десктопі — там вертикальна вісь) ── */
    .watch-mobile {
        display: none;
    }

    /* ── низ ── */
    .bl {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 11.5px;
        color: var(--text-muted);
    }
    .wk-dot {
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: var(--accent);
        transition: background 1.2s ease;
        flex: none;
    }
    @media (prefers-reduced-motion: no-preference) {
        .wk-dot { animation: wk-pulse 2.6s ease-in-out infinite; }
        @keyframes wk-pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }
    }
    .br {
        font-size: 10.5px;
        color: var(--text-muted);
        opacity: 0.55;
    }

    /* ── мобілка: ВЛАСНА композиція, не стиснутий десктоп (owner 12.07).
         Вертикальна вісь збоку від кільця не має де стояти на вузькому екрані —
         кільце лишається центром по горизонталі, весь контент іде ОДНІЄЮ колонкою
         ПІД кільцем (кільце center+R не займає нижню третину екрана). ── */
    @media (max-width: 768px) {
        .axis { display: none; }

        .voice {
            /* top успадковано з базового правила (50vh+22vmin+20px — рахує зазор від
               кільця на БУДЬ-ЯКОМУ екрані, ламати його тут не треба); тут лише ширина
               і безпечна висота зі скролом, щоб не наїхати на bl/br унизу. */
            width: calc(100vw - 32px);
            max-height: calc(100dvh - 50vh - 22vmin - 100px);
            overflow-y: auto;
            gap: 12px;
        }
        .thesis-main { font-size: 15px; -webkit-line-clamp: 3; line-clamp: 3; }

        /* рівні: горизонтальний ряд чіпів під тезою — своя мобільна мова,
           не стиснута вертикальна вісь */
        .watch-mobile {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 8px 14px;
        }
        .watch-mobile.dozing { opacity: 0.55; }
        .wc {
            display: flex;
            align-items: baseline;
            gap: 4px;
            font-size: 13px;
        }
        .wc-a { font-size: 10px; color: var(--text-muted); opacity: 0.75; }
        .wc-p { font-weight: 600; color: var(--text); }
        .wc-invalidation .wc-p { color: var(--danger); }
        .wc-target .wc-p { color: var(--positive); }
        .wc-entry .wc-p, .wc-trigger .wc-p {
            color: color-mix(in srgb, var(--accent) 85%, var(--text));
        }
        .wc.gaze .wc-p {
            text-shadow: 0 0 10px color-mix(in srgb, var(--accent) 55%, transparent);
        }

        .bl { bottom: 34px; left: 16px; right: 16px; }
        /* захисний рядок лишається і на мобілці */
        .br {
            bottom: 12px;
            left: 16px;
            right: 16px;
            text-align: left;
            font-size: 9.5px;
        }
    }
</style>
