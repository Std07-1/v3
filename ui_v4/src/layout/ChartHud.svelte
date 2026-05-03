<!-- src/layout/ChartHud.svelte -->
<!-- P3.1-P3.2: Frosted-glass HUD overlay (V3 parity: index.html .hud-stack)
     P3.11: Theme picker. P3.12: Candle style picker.
     Symbol dropdown · TF pills · Live price · Streaming dot -->

<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import { derivePdBadge } from "../stores/shellState";
    import PdBadge from "./PdBadge.svelte";
    import { BIAS_TF_LABELS, BIAS_TF_ORDER } from "../constants/tfLabels";

    const {
        symbols,
        tfs,
        currentSymbol,
        currentTf,
        lastPrice,
        lastBarOpen,
        lastBarTs,
        onSwitch,
        themeText = "#d1d4dc",
        menuBg = "rgba(30, 34, 45, 0.92)",
        menuBorder = "rgba(255, 255, 255, 0.08)",
        biasMap = {} as Record<string, string>,
        momentumMap = {} as Record<string, { b: number; r: number }>,
        narrative = null as import("../types").NarrativeBlock | null,
        shell = null as import("../types").ShellPayload | null,
        pdState = null as import("../types").PdState | null,
        utcTime = "" as string,
    }: {
        symbols: string[];
        tfs: string[];
        currentSymbol: string;
        currentTf: string;
        lastPrice: number | null;
        lastBarOpen: number | null;
        lastBarTs: number | null; // epoch ms of last bar/update
        onSwitch: (symbol: string, tf: string) => void;
        themeText?: string;
        menuBg?: string;
        menuBorder?: string;
        biasMap?: Record<string, string>;
        momentumMap?: Record<string, { b: number; r: number }>;
        narrative?: import("../types").NarrativeBlock | null;
        shell?: import("../types").ShellPayload | null;
        pdState?: import("../types").PdState | null;
        utcTime?: string;
    } = $props();

    // ─── Bias pills (ADR-0031, ADR-0043 D-15): SSOT у constants/tfLabels.ts ───

    function momInfo(m: { b: number; r: number } | undefined): {
        dots: string;
        cls: string;
    } {
        if (!m) return { dots: "", cls: "" };
        const max = Math.max(m.b, m.r);
        if (max <= 0) return { dots: "", cls: "" };
        const dots = max <= 2 ? "·" : max <= 5 ? "··" : "···";
        const cls =
            m.b > m.r ? "bull-mom" : m.r > m.b ? "bear-mom" : "neutral-mom";
        return { dots, cls };
    }

    let biasPills = $derived(
        BIAS_TF_ORDER.filter((k) => biasMap[k] != null).map((k) => {
            const mi = momInfo(momentumMap[k]);
            return {
                label: BIAS_TF_LABELS[k],
                bias: biasMap[k] as "bullish" | "bearish",
                arrow: biasMap[k] === "bullish" ? "▲" : "▼",
                momDots: mi.dots,
                momCls: mi.cls,
            };
        }),
    );

    let biasVisible = $state(true);

    // ─── ADR-0041 §5a: P/D chip with directional coloring ───
    let pdBadge = $derived(
        derivePdBadge(pdState, narrative?.scenarios?.[0]?.direction ?? null),
    );

    // ─── Shell state (ADR-0036) ───
    let microCardOpen = $state(false);
    function toggleMicroCard() {
        microCardOpen = !microCardOpen;
    }

    // Shell stage CSS class
    let shellStageClass = $derived(shell ? `st-${shell.stage}` : "");

    // Session label for sub-row (from narrative)
    const SESSION_SHORT: Record<string, string> = {
        newyork: "NY",
        london: "LDN",
        asia: "ASIA",
        closed: "",
    };
    let sessionLabel = $derived.by(() => {
        if (!narrative) return "";
        const s = narrative.current_session ?? "";
        if (!s) return "";
        const short = SESSION_SHORT[s.toLowerCase()] ?? s.toUpperCase();
        if (!short) return "";
        const kz = narrative.in_killzone ? " KZ" : "";
        return `${short}${kz}`;
    });

    function toggleBias(e: MouseEvent) {
        e.stopPropagation();
        biasVisible = !biasVisible;
    }

    // ─── Dropdown state ───
    let symbolOpen = $state(false);
    let tfOpen = $state(false);

    // ─── Streaming dot (V3: updateStreamingIndicator, 12s recency) ───
    const STALE_MS = 12_000;
    let now = $state(Date.now());
    let tickInterval: ReturnType<typeof setInterval> | null = null;

    onMount(() => {
        tickInterval = setInterval(() => {
            now = Date.now();
        }, 1000);
    });
    onDestroy(() => {
        if (tickInterval) clearInterval(tickInterval);
    });

    let streamState = $derived<"streaming" | "paused" | "idle">(
        lastBarTs == null
            ? "idle"
            : now - lastBarTs < STALE_MS
              ? "streaming"
              : "paused",
    );

    // Entry 078 §5: Price color — green/red when streaming, theme text when idle
    let priceColor = $derived.by(() => {
        if (streamState !== "streaming") return themeText;
        if (
            lastPrice == null ||
            lastBarOpen == null ||
            !Number.isFinite(lastPrice) ||
            !Number.isFinite(lastBarOpen)
        )
            return themeText;
        return lastPrice >= lastBarOpen ? "#26a69a" : "#ef5350";
    });

    // Price change (current candle delta)
    let priceChange = $derived.by(() => {
        if (
            lastPrice == null ||
            lastBarOpen == null ||
            !Number.isFinite(lastPrice) ||
            !Number.isFinite(lastBarOpen) ||
            lastBarOpen === 0
        )
            return null;
        const diff = lastPrice - lastBarOpen;
        const pct = (diff / lastBarOpen) * 100;
        return { diff, pct, up: diff >= 0 };
    });

    function fmtChange(diff: number): string {
        // Precision matches price: use lastPrice magnitude, not delta magnitude
        const ref = lastPrice ?? Math.abs(diff);
        const abs = Math.abs(diff);
        if (ref >= 100) return abs.toFixed(2);
        if (ref >= 10) return abs.toFixed(3);
        return abs.toFixed(5);
    }

    // ─── Pulse animation on change ───
    let pulseSymbol = $state(false);
    let pulseTf = $state(false);

    $effect(() => {
        if (currentSymbol) {
            pulseSymbol = true;
            const t = setTimeout(() => {
                pulseSymbol = false;
            }, 400);
            return () => clearTimeout(t);
        }
    });

    $effect(() => {
        if (currentTf) {
            pulseTf = true;
            const t = setTimeout(() => {
                pulseTf = false;
            }, 400);
            return () => clearTimeout(t);
        }
    });

    // ─── Price formatting ───
    function fmtPrice(price: number | null): string {
        if (price == null || !Number.isFinite(price)) return "—";
        // Автоматично визначаємо кількість знаків
        if (price >= 100) return price.toFixed(2);
        if (price >= 10) return price.toFixed(3);
        return price.toFixed(5);
    }

    // ─── Handlers ───
    function selectSymbol(sym: string) {
        symbolOpen = false;
        if (sym !== currentSymbol) {
            onSwitch(sym, currentTf);
        }
    }

    function selectTf(tf: string) {
        tfOpen = false;
        if (tf !== currentTf) {
            onSwitch(currentSymbol, tf);
        }
    }

    function toggleSymbol(e: MouseEvent) {
        e.stopPropagation();
        tfOpen = false;
        symbolOpen = !symbolOpen;
    }

    function toggleTf(e: MouseEvent) {
        e.stopPropagation();
        symbolOpen = false;
        tfOpen = !tfOpen;
    }

    // Close dropdowns + micro-card on outside click
    function handleWindowClick() {
        symbolOpen = false;
        tfOpen = false;
        microCardOpen = false;
    }

    // ─── Wheel cycling (V3: attachHudWheelControls) ───
    function handleSymbolWheel(e: WheelEvent) {
        e.preventDefault();
        if (symbols.length < 2) return;
        const idx = symbols.indexOf(currentSymbol);
        const next =
            e.deltaY > 0
                ? (idx + 1) % symbols.length
                : (idx - 1 + symbols.length) % symbols.length;
        onSwitch(symbols[next], currentTf);
    }

    function handleTfWheel(e: WheelEvent) {
        e.preventDefault();
        if (tfs.length < 2) return;
        const idx = tfs.indexOf(currentTf);
        const next =
            e.deltaY > 0
                ? (idx + 1) % tfs.length
                : (idx - 1 + tfs.length) % tfs.length;
        onSwitch(currentSymbol, tfs[next]);
    }
</script>

<svelte:window onclick={handleWindowClick} />

<div class="hud-stack {shellStageClass}">
    <div class="hud" style:color={themeText}>
        <div class="hud-row">
            <!-- Symbol slot -->
            <button
                class="hud-slot"
                class:pulse={pulseSymbol}
                onclick={toggleSymbol}
                onwheel={handleSymbolWheel}
                title="Scroll to cycle symbols"
            >
                {currentSymbol || "…"}
            </button>

            <!-- TF slot -->
            <button
                class="hud-slot"
                class:pulse={pulseTf}
                onclick={toggleTf}
                onwheel={handleTfWheel}
                title="Scroll to cycle timeframes"
            >
                {currentTf || "…"}
            </button>

            <!-- Price -->
            <span class="hud-price" style:color={priceColor}
                >{fmtPrice(lastPrice)}</span
            >

            <!-- Price change (candle delta) -->
            {#if priceChange}
                <span
                    class="hud-chg"
                    class:up={priceChange.up}
                    class:dn={!priceChange.up}
                >
                    {priceChange.up ? "+" : "-"}{fmtChange(priceChange.diff)}
                    {priceChange.up ? "▲" : "▼"}
                </span>
            {/if}

            <!-- Streaming dot -->
            <span
                class="hud-dot"
                class:streaming={streamState === "streaming"}
                class:paused={streamState === "paused"}
                title={streamState === "streaming"
                    ? "Live"
                    : streamState === "paused"
                      ? "Stale (>12s)"
                      : "No data"}
            ></span>

            <!-- Clock (mobile only — replaces .top-right-bar clock) -->
            {#if utcTime}
                <span class="hud-clock">{utcTime}</span>
            {/if}

            <!-- ADR-0033: Narrative inline (hidden when shell active) -->
            {#if narrative && !shell}
                <span
                    class="hud-narrative"
                    class:trade={narrative.mode === "trade"}
                    class:wait={narrative.mode === "wait"}
                >
                    <span class="narr-mode"
                        >{narrative.mode === "trade" ? "TRADE" : "WAIT"}</span
                    >
                    {#if narrative.scenarios.length > 0}
                        <span
                            class="narr-dir"
                            class:long={narrative.scenarios[0].direction ===
                                "long"}
                            class:short={narrative.scenarios[0].direction ===
                                "short"}
                            >{narrative.scenarios[0].direction === "long"
                                ? "▲"
                                : "▼"}</span
                        >
                        <span
                            class="narr-trigger narr-trigger-{narrative
                                .scenarios[0].trigger}"
                            >{narrative.scenarios[0].trigger}</span
                        >
                    {/if}
                    {#if narrative.market_phase === "trending_up" || narrative.market_phase === "trending_down"}
                        <span class="narr-phase"
                            >{narrative.market_phase === "trending_up"
                                ? "↑"
                                : "↓"}</span
                        >
                    {/if}
                    {#if narrative.in_killzone}
                        <span class="narr-kz">KZ</span>
                    {/if}
                    <!-- Styled tooltip on hover -->
                    <span class="narr-tooltip">
                        <div class="ntt-headline">{narrative.headline}</div>
                        <div class="ntt-bias">{narrative.bias_summary}</div>
                        {#each narrative.scenarios as sc, i}
                            <div class="ntt-scenario" class:alt={i > 0}>
                                <span
                                    class="ntt-dir"
                                    class:long={sc.direction === "long"}
                                    class:short={sc.direction === "short"}
                                    >{sc.direction === "long" ? "▲" : "▼"}
                                    {sc.entry_desc}
                                    <span class="ntt-expand"
                                        >{sc.direction === "long"
                                            ? "BUY — вхід у лонг від зони попиту"
                                            : "SELL — вхід у шорт від зони пропозиції"}</span
                                    >
                                </span>
                                <span class="ntt-trig ntt-trig-{sc.trigger}">
                                    {sc.trigger_desc}
                                    <span class="ntt-expand"
                                        >{sc.trigger === "approaching"
                                            ? "Ціна наближається до зони — чекаємо реакцію"
                                            : sc.trigger === "in_zone"
                                              ? "Ціна в зоні — потрібне підтвердження для входу"
                                              : sc.trigger === "triggered"
                                                ? "Сигнал входу підтверджено структурою"
                                                : "Сетап готовий до виконання"}</span
                                    >
                                </span>
                                {#if sc.target_desc}<span class="ntt-target">
                                        → {sc.target_desc}
                                        <span class="ntt-expand"
                                            >Найближча ціль — рівень take-profit</span
                                        >
                                    </span>{/if}
                                <span class="ntt-inv">
                                    ✕ {sc.invalidation}
                                    <span class="ntt-expand"
                                        >Якщо ціна досягне цього рівня — сетап
                                        скасовано</span
                                    >
                                </span>
                            </div>
                        {/each}
                        {#if narrative.scenarios.length === 0}
                            <div class="ntt-wait">
                                {narrative.next_area || "Awaiting setup..."}
                            </div>
                        {/if}
                        {#if narrative.fvg_context}
                            <div class="ntt-fvg">
                                {narrative.fvg_context}
                                <span class="ntt-expand"
                                    >FVG (Fair Value Gap) — незаповнений гап у
                                    ціні, зона магніту</span
                                >
                            </div>
                        {/if}
                        {#if narrative.warnings.length > 0}
                            <div class="ntt-warn">
                                ⚠ {narrative.warnings.join(", ")}
                            </div>
                        {/if}
                        {#if narrative.session_context}
                            <div class="ntt-session">
                                🕐 {narrative.session_context}
                            </div>
                        {:else if narrative.current_session}
                            <div class="ntt-session">
                                🕐 {narrative.current_session}{narrative.in_killzone
                                    ? " (KZ)"
                                    : ""}
                            </div>
                        {/if}
                    </span>
                </span>
            {/if}

            <!-- ADR-0036: Shell stage badge + dropdown micro-card -->
            {#if shell}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    class="shell-stage-wrap"
                    role="presentation"
                    onclick={(e) => e.stopPropagation()}
                >
                    <button
                        class="shell-stage"
                        onclick={toggleMicroCard}
                        type="button"
                        title="Натисніть для деталей"
                    >
                        <span class="shell-stlbl">{shell.stage_label}</span>
                        {#if shell.stage_context}
                            <span class="shell-stctx"
                                >{shell.stage_context}</span
                            >
                        {/if}
                    </button>
                    <!-- Micro-card dropdown -->
                    {#if shell.micro_card && microCardOpen}
                        <!-- svelte-ignore a11y_no_static_element_interactions -->
                        <!-- svelte-ignore a11y_click_events_have_key_events -->
                        <div
                            class="mc-backdrop"
                            onclick={() => (microCardOpen = false)}
                        ></div>
                        <div class="shell-mc open">
                            <div class="mc-grip"><span></span></div>
                            <div class="mc-grid">
                                <div
                                    class="mc-field"
                                    title="Поточний торговий режим"
                                >
                                    <div class="mc-label">Режим</div>
                                    <div class="mc-val acc">
                                        {shell.micro_card.mode_text}
                                    </div>
                                </div>
                                <div
                                    class="mc-field"
                                    title="Причина поточного режиму"
                                >
                                    <div class="mc-label">Чому</div>
                                    <div class="mc-val">
                                        {shell.micro_card.why_text}
                                    </div>
                                </div>
                                <div
                                    class="mc-field"
                                    title="Яка умова потрібна для переходу"
                                >
                                    <div class="mc-label">Що потрібно</div>
                                    <div class="mc-val">
                                        {shell.micro_card.what_needed}
                                    </div>
                                </div>
                                {#if shell.micro_card.what_cancels && shell.micro_card.what_cancels !== "—"}
                                    <div
                                        class="mc-field"
                                        title="Умова, яка скасує поточний сценарій"
                                    >
                                        <div class="mc-label">Що скасує</div>
                                        <div class="mc-val red">
                                            {shell.micro_card.what_cancels}
                                        </div>
                                    </div>
                                {/if}
                                {#if shell.micro_card.warning}
                                    <div class="mc-warn">
                                        ⚠ {shell.micro_card.warning}
                                    </div>
                                {/if}
                            </div>
                            <!-- ADR-0039: Signal panel inside micro-card -->
                            {#if shell.signal}
                                {@const sig = shell.signal}
                                <div class="mc-sig-sep"></div>
                                <div class="mc-sig">
                                    <div class="mc-sig-head">
                                        <span
                                            class="mc-sig-dir"
                                            class:long={sig.direction ===
                                                "long"}
                                            class:short={sig.direction ===
                                                "short"}
                                        >
                                            {sig.direction === "long"
                                                ? "▲ LONG"
                                                : sig.direction === "short"
                                                  ? "▼ SHORT"
                                                  : "— —"}
                                        </span>
                                        <span class="mc-sig-state"
                                            >{sig.state?.toUpperCase() ??
                                                "—"}</span
                                        >
                                        <span
                                            class="mc-sig-conf"
                                            title="Confidence score"
                                        >
                                            {sig.confidence ?? 0}%
                                        </span>
                                    </div>
                                    <div class="mc-grid mc-sig-grid">
                                        <div
                                            class="mc-field"
                                            title="Ціна входу ({sig.entry_method ??
                                                'unknown'})"
                                        >
                                            <div class="mc-label">Entry</div>
                                            <div class="mc-val entry">
                                                {sig.entry_price?.toFixed(2) ??
                                                    "—"}
                                            </div>
                                        </div>
                                        <div
                                            class="mc-field"
                                            title="R:R відношення ризик/прибуток"
                                        >
                                            <div class="mc-label">R:R</div>
                                            <div class="mc-val">
                                                {sig.risk_reward?.toFixed(1) ??
                                                    "—"}:1
                                            </div>
                                        </div>
                                        <div class="mc-field" title="Стоп-лосс">
                                            <div class="mc-label">SL</div>
                                            <div class="mc-val sl">
                                                {sig.stop_loss?.toFixed(2) ??
                                                    "—"}
                                            </div>
                                        </div>
                                        <div
                                            class="mc-field"
                                            title="Тейк-профіт"
                                        >
                                            <div class="mc-label">TP</div>
                                            <div class="mc-val tp">
                                                {sig.take_profit?.toFixed(2) ??
                                                    "—"}
                                            </div>
                                        </div>
                                    </div>
                                    {#if sig.warnings && sig.warnings.length > 0}
                                        <div class="mc-warn">
                                            ⚠ {sig.warnings[0]}
                                        </div>
                                    {/if}
                                </div>
                            {/if}
                        </div>
                    {/if}
                </div>
            {/if}
        </div>
    </div>

    <!-- Sub-row: session + P/D (left) + bias pills (right) — compact, always anchored -->
    <div class="hud-sub">
        {#if sessionLabel}
            <span class="tact-session">{sessionLabel}</span>
            {#if pdBadge || (biasVisible && biasPills.length > 0)}
                <span class="sub-sep">│</span>
            {/if}
        {/if}
        {#if pdBadge}
            <PdBadge badge={pdBadge} />
        {/if}
        {#if pdBadge && biasVisible && biasPills.length > 0}
            <span class="sub-sep">│</span>
        {/if}
        {#if biasVisible && biasPills.length > 0}
            <button
                class="hud-bias-area"
                onclick={toggleBias}
                title="Hide HTF bias"
                type="button"
            >
                {#each biasPills as p (p.label)}
                    <span
                        class="hud-bias-pill"
                        class:bull={p.bias === "bullish"}
                        class:bear={p.bias === "bearish"}
                        >{p.label}<span class="bias-arrow">{p.arrow}</span
                        >{#if p.momDots}<span class="bias-mom {p.momCls}"
                                >{p.momDots}</span
                            >{/if}</span
                    >
                {/each}
            </button>
        {:else}
            <button
                class="hud-bias-toggle"
                onclick={toggleBias}
                title="Show HTF bias">║</button
            >
        {/if}
    </div>

    <!-- Symbol dropdown -->
    {#if symbolOpen}
        <div
            class="hud-menu"
            role="listbox"
            tabindex="-1"
            style:background={menuBg}
            style:border-color={menuBorder}
            style:color={themeText}
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => e.key === "Escape" && (symbolOpen = false)}
        >
            {#each symbols as sym}
                <button
                    class="hud-menu-item"
                    class:active={sym === currentSymbol}
                    onclick={() => selectSymbol(sym)}
                >
                    {sym}
                </button>
            {/each}
        </div>
    {/if}

    <!-- TF dropdown -->
    {#if tfOpen}
        <div
            class="hud-menu hud-menu-tf"
            role="listbox"
            tabindex="-1"
            style:background={menuBg}
            style:border-color={menuBorder}
            style:color={themeText}
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => e.key === "Escape" && (tfOpen = false)}
        >
            {#each tfs as tf}
                <button
                    class="hud-menu-item"
                    class:active={tf === currentTf}
                    onclick={() => selectTf(tf)}
                >
                    {tf}
                </button>
            {/each}
        </div>
    {/if}
</div>

<style>
    /* ─── HUD: Frosted glass (V3: .hud-stack / .hud) ─── */
    .hud-stack {
        position: absolute;
        top: 1px;
        left: 8px;
        z-index: 35;
        display: inline-flex;
        flex-direction: column;
        gap: 4px;
        pointer-events: auto;
    }

    .hud {
        display: inline-flex;
        padding: 6px 12px;
        background: transparent;
        border: none;
        border-radius: 10px;
    }

    .hud-row {
        display: flex;
        align-items: center;
        gap: 8px;
        white-space: nowrap;
    }

    /* ─── HUD slots (clickable) ─── */
    .hud-slot {
        all: unset;
        cursor: pointer;
        font-size: 13px;
        font-weight: 600;
        color: inherit;
        padding: 2px 6px;
        border-radius: 4px;
        transition: background 0.15s;
        user-select: none;
    }

    .hud-slot:hover {
        background: rgba(255, 255, 255, 0.08);
    }

    /* ─── Pulse animation (V3: keyframes hudPulse) ─── */
    .hud-slot.pulse {
        animation: hudPulse 0.35s ease-out;
    }

    @keyframes hudPulse {
        0% {
            transform: scale(1);
        }
        50% {
            transform: scale(1.1);
            color: #4a90d9;
        }
        100% {
            transform: scale(1);
        }
    }

    .hud-sep {
        opacity: 0.35;
        font-size: 13px;
        user-select: none;
        display: none;
    }

    .hud-price {
        font-size: 13px;
        font-weight: 600;
        font-family: "Roboto Mono", monospace, sans-serif;
    }

    /* ─── Streaming dot (V3: .hud-stream) ─── */
    .hud-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #5d6068;
        flex-shrink: 0;
        transition: background 0.3s;
    }

    .hud-dot.streaming {
        background: #26a69a;
        box-shadow: 0 0 6px rgba(38, 166, 154, 0.5);
    }

    .hud-dot.paused {
        background: #ef5350;
    }

    /* ─── Dropdown menus (V3: .hud-menu) ─── */
    .hud-menu {
        display: flex;
        flex-direction: column;
        min-width: 120px;
        max-height: 300px;
        overflow-y: auto;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid;
        border-radius: 8px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        padding: 4px;
    }

    .hud-menu-tf {
        flex-direction: row;
        flex-wrap: wrap;
        min-width: 0;
        max-height: none;
        gap: 2px;
    }

    .hud-menu-item {
        all: unset;
        cursor: pointer;
        padding: 5px 10px;
        font-size: 12px;
        color: inherit;
        border-radius: 4px;
        transition: background 0.12s;
        white-space: nowrap;
    }

    .hud-menu-item:hover {
        background: rgba(128, 128, 128, 0.15);
    }

    .hud-menu-item.active {
        background: rgba(74, 144, 217, 0.2);
        color: #4a90d9;
        font-weight: 600;
    }

    /* Scrollbar for long lists */
    .hud-menu::-webkit-scrollbar {
        width: 4px;
    }
    .hud-menu::-webkit-scrollbar-track {
        background: transparent;
    }
    .hud-menu::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.15);
        border-radius: 2px;
    }

    /* P3.12: Color swatch in candle style menu */

    /* Price change indicator */
    .hud-chg {
        font-size: 11px;
        font-weight: 500;
        font-family: "Roboto Mono", monospace, sans-serif;
        letter-spacing: -0.3px;
    }
    .hud-chg.up {
        color: #26a69a;
    }
    .hud-chg.dn {
        color: #ef5350;
    }

    /* ADR-0031: Inline bias pills */
    .hud-bias-pill {
        font-size: 9px;
        font-weight: 600;
        padding: 1px 4px;
        border-radius: 3px;
        line-height: 1.3;
        letter-spacing: 0.3px;
        display: inline-flex;
        align-items: center;
        gap: 1px;
        pointer-events: none;
    }
    .hud-bias-pill.bull {
        color: #26a69a;
    }
    .hud-bias-pill.bear {
        color: #ef5350;
    }
    .bias-arrow {
        font-size: 7px;
        margin-left: 1px;
    }
    .bias-mom {
        font-size: 8px;
        margin-left: 1px;
        opacity: 0.7;
    }
    .bull-mom {
        color: #26a69a;
    }
    .bear-mom {
        color: #ef5350;
    }
    .neutral-mom {
        opacity: 0.4;
    }
    .hud-bias-area {
        background: none;
        border: none;
        color: inherit;
        display: inline-flex;
        align-items: center;
        gap: 3px;
        cursor: pointer;
        pointer-events: auto;
        border-radius: 4px;
        padding: 1px 3px;
        transition: background 0.15s ease;
    }
    .hud-bias-area:hover {
        background: rgba(255, 255, 255, 0.06);
    }
    .hud-bias-toggle {
        background: none;
        border: none;
        color: #c8cdd6;
        opacity: 0.6;
        cursor: pointer;
        width: 20px;
        height: 20px;
        border-radius: 4px;
        transition: all 0.15s ease;
        font-size: 11px;
        line-height: 20px;
        text-align: center;
        padding: 0;
        pointer-events: auto;
        user-select: none;
    }
    .hud-bias-toggle:hover {
        opacity: 1;
        background: rgba(255, 255, 255, 0.08);
        box-shadow: 0 0 4px rgba(255, 255, 255, 0.08);
    }

    /* ADR-0033: Inline narrative (consistent with bias pills) */
    .hud-narrative {
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 3px;
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 0.3px;
        pointer-events: auto;
        cursor: default;
        border-radius: 4px;
        padding: 1px 4px;
        transition: opacity 0.15s ease;
    }
    .hud-narrative.trade {
        color: #4a90d9;
    }
    .hud-narrative.wait {
        color: #8b8f9a;
        opacity: 0.7;
    }
    .narr-mode {
        text-transform: uppercase;
    }
    .narr-dir.long {
        color: #26a69a;
    }
    .narr-dir.short {
        color: #ef5350;
    }
    .narr-trigger {
        font-size: 8px;
        opacity: 0.8;
    }
    .narr-trigger-ready {
        color: #26a69a;
    }
    .narr-trigger-triggered {
        color: #ffa726;
    }
    .narr-trigger-in_zone {
        color: #42a5f5;
    }
    .narr-trigger-approaching {
        color: #8b8f9a;
    }
    .narr-phase {
        font-size: 7px;
        opacity: 0.6;
    }
    .narr-kz {
        font-size: 7px;
        font-weight: 600;
        color: #ff9800;
        background: rgba(255, 152, 0, 0.15);
        padding: 0 3px;
        border-radius: 2px;
    }

    /* Narrative hover tooltip */
    .narr-tooltip {
        display: none;
        position: absolute;
        top: 100%;
        left: 0;
        margin-top: 4px;
        min-width: 260px;
        max-width: 380px;
        padding: 6px 10px;
        background: rgba(19, 23, 34, 0.92);
        backdrop-filter: blur(8px);
        border: 1px solid rgba(120, 123, 134, 0.2);
        border-radius: 6px;
        font-size: 10px;
        font-weight: 400;
        color: #a0a4b0;
        white-space: normal;
        line-height: 1.5;
        z-index: 100;
        pointer-events: auto;
    }
    .hud-narrative:hover .narr-tooltip {
        display: flex;
        flex-direction: column;
        gap: 3px;
    }
    .ntt-headline {
        font-weight: 600;
        color: #c0c4cc;
        font-size: 11px;
    }
    .ntt-bias {
        color: #8b8f9a;
        font-style: italic;
    }
    .ntt-scenario {
        display: flex;
        flex-direction: column;
        gap: 1px;
        padding: 3px 0;
        border-top: 1px solid rgba(120, 123, 134, 0.1);
        border-radius: 4px;
        transition: background 0.12s ease;
    }
    .ntt-scenario:hover {
        background: rgba(255, 255, 255, 0.04);
        padding: 4px 5px;
        margin: 0 -5px;
    }
    .ntt-scenario.alt {
        opacity: 0.7;
    }
    .ntt-scenario.alt:hover {
        opacity: 1;
    }
    .ntt-dir {
        font-weight: 600;
    }
    .ntt-dir.long {
        color: #26a69a;
    }
    .ntt-dir.short {
        color: #ef5350;
    }
    .ntt-trig {
        font-size: 9px;
        padding: 1px 4px;
        border-radius: 3px;
        background: rgba(120, 123, 134, 0.08);
        width: fit-content;
        cursor: default;
        transition: all 0.12s ease;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 220px;
        white-space: nowrap;
    }
    .ntt-trig:hover {
        max-width: none;
        white-space: normal;
        background: rgba(120, 123, 134, 0.15);
    }
    .ntt-trig-ready {
        color: #26a69a;
        background: rgba(38, 166, 154, 0.1);
    }
    .ntt-trig-triggered {
        color: #ffa726;
        background: rgba(255, 167, 38, 0.1);
    }
    .ntt-trig-in_zone {
        color: #42a5f5;
        background: rgba(66, 165, 245, 0.1);
    }
    .ntt-trig-approaching {
        color: #8b8f9a;
    }
    .ntt-target {
        color: #4a90d9;
        font-size: 9px;
        cursor: default;
        transition: opacity 0.12s ease;
        opacity: 0.8;
    }
    .ntt-target:hover {
        opacity: 1;
    }
    .ntt-inv {
        color: #ef5350;
        font-size: 9px;
        opacity: 0.5;
        cursor: default;
        transition: opacity 0.12s ease;
    }
    .ntt-inv:hover {
        opacity: 1;
    }
    .ntt-wait {
        color: #5d6068;
    }
    .ntt-fvg {
        color: #2ecc71;
        font-size: 9px;
        opacity: 0.8;
        cursor: default;
        transition: opacity 0.12s ease;
    }
    .ntt-fvg:hover {
        opacity: 1;
    }
    .ntt-warn {
        color: #ff9800;
        font-size: 9px;
        opacity: 0.8;
        cursor: default;
        transition: opacity 0.12s ease;
    }
    .ntt-warn:hover {
        opacity: 1;
    }
    .ntt-session {
        color: #42a5f5;
        font-size: 9px;
        opacity: 0.8;
        margin-top: 2px;
    }
    /* Expandable description on hover */
    .ntt-expand {
        display: none;
        font-size: 8px;
        font-weight: 400;
        font-style: italic;
        color: rgba(210, 215, 225, 0.7);
        margin-top: 2px;
        padding: 2px 4px;
        border-left: 2px solid rgba(120, 123, 134, 0.2);
        white-space: normal;
        line-height: 1.3;
    }
    .ntt-dir:hover .ntt-expand,
    .ntt-trig:hover .ntt-expand,
    .ntt-target:hover .ntt-expand,
    .ntt-inv:hover .ntt-expand,
    .ntt-fvg:hover .ntt-expand {
        display: block;
    }
    .ntt-dir,
    .ntt-trig,
    .ntt-target,
    .ntt-inv,
    .ntt-fvg {
        position: relative;
    }

    /* ═══ ADR-0036: Shell Stage System ═══ */

    /* Stage CSS custom properties (5 stages) */
    .st-wait {
        --sb: rgba(255, 255, 255, 0.07);
        --sa: rgba(255, 255, 255, 0.15);
        --st: rgba(255, 255, 255, 0.42);
        --ss: none;
    }
    .st-prepare {
        --sb: rgba(251, 191, 36, 0.22);
        --sa: rgba(251, 191, 36, 0.6);
        --st: rgba(251, 191, 36, 0.9);
        --ss: none;
    }
    .st-ready {
        --sb: rgba(52, 211, 153, 0.28);
        --sa: rgba(52, 211, 153, 0.75);
        --st: rgba(52, 211, 153, 0.95);
        --ss: 0 1px 0 rgba(52, 211, 153, 0.07);
    }
    .st-triggered {
        --sb: rgba(99, 179, 237, 0.38);
        --sa: rgba(99, 179, 237, 0.85);
        --st: rgba(99, 179, 237, 1);
        --ss: 0 1px 0 rgba(99, 179, 237, 0.1),
            0 2px 8px rgba(99, 179, 237, 0.04);
    }
    .st-stayout {
        --sb: rgba(252, 129, 129, 0.2);
        --sa: rgba(252, 129, 129, 0.55);
        --st: rgba(252, 129, 129, 0.8);
        --ss: none;
    }

    /* Shell stage badge (inline, same row as HUD) */
    .shell-stage {
        all: unset;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        cursor: pointer;
        padding: 2px 6px;
        border-radius: 4px;
        transition: background 0.15s;
    }
    .shell-stage:hover {
        background: rgba(255, 255, 255, 0.06);
    }
    .shell-stlbl {
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--st, rgba(255, 255, 255, 0.42));
    }
    .shell-stctx {
        font-size: 9px;
        color: rgba(255, 255, 255, 0.35);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        max-width: 220px;
    }

    .tact-session {
        font-size: 9px;
        font-weight: 700;
        font-family: "SF Mono", "Cascadia Code", "Consolas", monospace;
        color: #ff9800;
        background: rgba(255, 152, 0, 0.1);
        border: 1px solid rgba(255, 152, 0, 0.3);
        padding: 1px 5px;
        border-radius: 3px;
        letter-spacing: 0.5px;
    }

    /* ─── Micro-card backdrop (mobile only) ─── */
    .mc-backdrop {
        display: none;
    }
    .mc-grip {
        display: none;
    }

    /* ─── Micro-card (dropdown from shell-stage) ─── */
    .shell-stage-wrap {
        position: relative;
        display: inline-flex;
        align-items: center;
    }
    .shell-mc {
        position: absolute;
        top: calc(100% + 4px);
        left: 0;
        min-width: 260px;
        overflow: hidden;
        background: rgba(13, 15, 21, 0.25);
        backdrop-filter: blur(32px);
        -webkit-backdrop-filter: blur(32px);
        border-left: 2px solid var(--sa, rgba(255, 255, 255, 0.15));
        border-bottom: 0.5px solid var(--sb, rgba(255, 255, 255, 0.07));
        border-radius: 0 6px 6px 6px;
        z-index: 90;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
        animation: mc-drop 180ms cubic-bezier(0.22, 1, 0.36, 1);
    }
    @keyframes mc-drop {
        from {
            opacity: 0;
            transform: translateY(-4px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    .mc-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 5px 14px;
        padding: 7px 10px 7px 8px;
    }
    .mc-field {
        display: flex;
        flex-direction: column;
        cursor: default;
        border-radius: 3px;
        padding: 2px 3px;
        transition: background 0.12s;
    }
    .mc-field:hover {
        background: rgba(255, 255, 255, 0.04);
    }
    .mc-label {
        font-size: 8px;
        text-transform: uppercase;
        font-weight: 500;
        letter-spacing: 0.1em;
        color: rgba(255, 255, 255, 0.25);
        margin-bottom: 2px;
    }
    .mc-val {
        font-size: 10px;
        color: rgba(255, 255, 255, 0.72);
        line-height: 1.4;
    }
    .mc-val.acc {
        color: var(--st, rgba(255, 255, 255, 0.42));
    }
    .mc-val.red {
        color: rgba(252, 129, 129, 0.8);
    }
    .mc-warn {
        grid-column: span 2;
        font-size: 9px;
        color: rgba(251, 191, 36, 0.6);
        border-top: 0.5px solid rgba(255, 255, 255, 0.06);
        margin-top: 4px;
        padding-top: 5px;
    }

    /* ─── ADR-0039: Signal section inside micro-card ─── */
    .mc-sig-sep {
        height: 0.5px;
        background: rgba(255, 255, 255, 0.08);
        margin: 2px 8px 0;
    }
    .mc-sig {
        padding: 5px 10px 7px 8px;
    }
    .mc-sig-head {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-bottom: 5px;
    }
    .mc-sig-dir {
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 0.06em;
    }
    .mc-sig-dir.long {
        color: rgba(52, 211, 153, 0.95);
    }
    .mc-sig-dir.short {
        color: rgba(252, 129, 129, 0.95);
    }
    .mc-sig-state {
        font-size: 8px;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: rgba(255, 255, 255, 0.35);
    }
    .mc-sig-conf {
        margin-left: auto;
        font-size: 9px;
        font-weight: 600;
        color: var(--st, rgba(255, 255, 255, 0.42));
    }
    .mc-sig-grid {
        margin: 0;
    }
    .mc-val.entry {
        color: rgba(52, 211, 153, 0.85);
    }
    .mc-val.sl {
        color: rgba(252, 129, 129, 0.8);
    }
    .mc-val.tp {
        color: rgba(96, 165, 250, 0.85);
    }

    /* Sub-row: P/D + bias pills (compact, under price row) */
    .hud-sub {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 0 12px;
        min-height: 14px;
        white-space: nowrap;
    }
    .sub-sep {
        font-size: 10px;
        opacity: 0.18;
        user-select: none;
        line-height: 1;
    }

    /* Clock — hidden on desktop, visible on mobile */
    .hud-clock {
        display: none;
    }

    /* ═══ P5: Mobile responsive (768px breakpoint) ═══ */
    @media (max-width: 768px) {
        .hud-stack {
            left: 2px;
            top: 0;
        }
        .hud-clock {
            display: block;
            position: fixed;
            bottom: 4px;
            right: 4px;
            font-size: 10px;
            font-family: "SF Mono", "Cascadia Code", "Consolas", monospace;
            opacity: 0.55;
            white-space: nowrap;
            z-index: 35;
            pointer-events: none;
        }
        .hud {
            padding: 3px 2px;
        }
        .hud-row {
            gap: 5px;
        }
        .hud-slot {
            font-size: 12px;
            padding: 2px 4px;
        }
        /* Flush left edge of first slot with sub-row content */
        .hud-row > .hud-slot:first-child {
            padding-left: 1px;
        }
        .hud-price {
            font-size: 12px;
        }
        .hud-chg {
            font-size: 10px;
        }
        /* Hide narrative inline on mobile — too much info for small screen */
        .hud-narrative {
            display: none;
        }
        /* Hide shell context text on mobile, keep stage label */
        .shell-stctx {
            display: none;
        }
        .shell-stlbl {
            font-size: 9px;
        }
        /* Compact bias pills */
        .hud-bias-pill {
            font-size: 8px;
            padding: 0 3px;
        }
        .hud-sub {
            padding: 0 3px;
            gap: 3px;
        }
        /* Dropdowns: wider on mobile for touch targets */
        .hud-menu {
            min-width: 140px;
        }
        .hud-menu-item {
            padding: 8px 12px;
            font-size: 14px;
        }
        .hud-menu-tf .hud-menu-item {
            padding: 8px 10px;
            font-size: 13px;
        }
        /* ─── Bottom-sheet micro-card on mobile ─── */
        .mc-backdrop {
            display: block;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.45);
            z-index: 199;
            animation: mc-fade 180ms ease;
        }
        @keyframes mc-fade {
            from {
                opacity: 0;
            }
            to {
                opacity: 1;
            }
        }
        .shell-mc {
            position: fixed;
            top: auto;
            left: 0;
            right: 0;
            bottom: 0;
            min-width: unset;
            width: 100%;
            border-radius: 14px 14px 0 0;
            border-left: none;
            border-bottom: none;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            z-index: 200;
            max-height: 60vh;
            overflow-y: auto;
            animation: mc-slide-up 220ms cubic-bezier(0.22, 1, 0.36, 1);
        }
        @keyframes mc-slide-up {
            from {
                opacity: 0;
                transform: translateY(100%);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        .mc-grip {
            display: flex;
            justify-content: center;
            padding: 8px 0 4px;
        }
        .mc-grip span {
            width: 32px;
            height: 4px;
            border-radius: 2px;
            background: rgba(255, 255, 255, 0.18);
        }
        .mc-grid {
            gap: 8px 16px;
            padding: 8px 16px 12px;
        }
        .mc-label {
            font-size: 9px;
        }
        .mc-val {
            font-size: 12px;
        }
    }
</style>
