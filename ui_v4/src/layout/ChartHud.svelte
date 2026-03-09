<!-- src/layout/ChartHud.svelte -->
<!-- P3.1-P3.2: Frosted-glass HUD overlay (V3 parity: index.html .hud-stack)
     P3.11: Theme picker. P3.12: Candle style picker. P3.13: Favorites.
     Symbol dropdown · TF pills · Live price · Streaming dot · UTC label -->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import { favoritesStore, type FavPair } from "../stores/favorites";

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
    } = $props();

    // ─── Bias pills (ADR-0031) ───
    const BIAS_TF_LABELS: Record<string, string> = {
        "86400": "D1",
        "14400": "H4",
        "3600": "H1",
        "900": "M15",
    };
    const BIAS_TF_ORDER = ["86400", "14400", "3600", "900"];

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
    let priceColor = $derived(
        streamState !== "streaming" || lastPrice == null || lastBarOpen == null
            ? themeText
            : lastPrice >= lastBarOpen
              ? "#26a69a"
              : "#ef5350",
    );

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

    // Close dropdowns on outside click
    function handleWindowClick() {
        symbolOpen = false;
        tfOpen = false;
    }

    // ─── P3.13: Favorites ───
    let favs: FavPair[] = $state([]);
    const unsubFavs = favoritesStore.subscribe((f) => {
        favs = f;
    });
    onDestroy(() => {
        unsubFavs();
    });

    let isFaved = $derived(
        favs.some((f) => f.symbol === currentSymbol && f.tf === currentTf),
    );

    function toggleFav(e: MouseEvent) {
        e.stopPropagation();
        favoritesStore.toggle(currentSymbol, currentTf);
    }

    function selectFav(f: FavPair) {
        symbolOpen = false;
        tfOpen = false;
        onSwitch(f.symbol, f.tf);
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

<div class="hud-stack">
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

            <span class="hud-sep">·</span>

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

            <span class="hud-sep">·</span>

            <!-- Price -->
            <span class="hud-price" style:color={priceColor}
                >{fmtPrice(lastPrice)}</span
            >

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

            <!-- P3.13: Favorite star -->
            <button
                class="hud-slot hud-star"
                class:faved={isFaved}
                onclick={toggleFav}
                title={isFaved ? "Remove from favorites" : "Add to favorites"}
                >{isFaved ? "★" : "☆"}</button
            >

            <!-- ADR-0031: HTF bias toggle + pills -->
            <span class="hud-sep">·</span>
            {#if biasVisible && biasPills.length > 0}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <span
                    class="hud-bias-area"
                    onclick={toggleBias}
                    title="Hide HTF bias"
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
                </span>
            {:else}
                <button
                    class="hud-bias-toggle"
                    onclick={toggleBias}
                    title="Show HTF bias">›</button
                >
            {/if}

            <!-- ADR-0033: Narrative inline (same row as HTF bias) -->
            {#if narrative}
                <span class="hud-sep">·</span>
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
                    {#if narrative.market_phase !== "ranging"}
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
        </div>
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
            <!-- P3.13: Favorites section -->
            {#if favs.length > 0}
                <div class="fav-section-label">★ Favorites</div>
                {#each favs as fav}
                    <button
                        class="hud-menu-item fav-item"
                        class:active={fav.symbol === currentSymbol &&
                            fav.tf === currentTf}
                        onclick={() => selectFav(fav)}
                    >
                        {fav.symbol} · {fav.tf}
                    </button>
                {/each}
                <div class="fav-divider"></div>
            {/if}
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
    /* P3.13: Favorite star + favorites section */
    .hud-star {
        opacity: 0.4;
        transition:
            color 0.15s,
            opacity 0.15s;
    }
    .hud-star.faved {
        color: #f0b90b;
    }
    .hud-star:hover {
        color: #f0b90b;
    }

    .fav-section-label {
        font-size: 10px;
        color: #f0b90b;
        padding: 3px 10px 1px;
        font-weight: 600;
        letter-spacing: 0.5px;
        user-select: none;
    }
    .fav-item {
        font-style: italic;
    }
    .fav-divider {
        height: 1px;
        background: rgba(255, 255, 255, 0.06);
        margin: 4px 0;
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
</style>
