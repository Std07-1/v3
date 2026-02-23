<!-- src/layout/ChartHud.svelte -->
<!-- P3.1-P3.2: Frosted-glass HUD overlay (V3 parity: index.html .hud-stack)
     P3.11: Theme picker. P3.12: Candle style picker. P3.13: Favorites.
     Symbol dropdown · TF pills · Live price · Streaming dot · UTC label -->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import type { ThemeName, CandleStyleName } from "../chart/lwc";
    import {
        THEMES,
        THEME_NAMES,
        CANDLE_STYLES,
        CANDLE_STYLE_NAMES,
        loadTheme,
        loadCandleStyle,
    } from "../chart/lwc";
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
        onThemeChange,
        onCandleStyleChange,
        themeBg = "transparent",
        themeText = "#d1d4dc",
        themeBorder = "transparent",
        menuBg = "rgba(30, 34, 45, 0.92)",
        menuBorder = "rgba(255, 255, 255, 0.08)",
    }: {
        symbols: string[];
        tfs: string[];
        currentSymbol: string;
        currentTf: string;
        lastPrice: number | null;
        lastBarOpen: number | null;
        lastBarTs: number | null; // epoch ms of last bar/update
        onSwitch: (symbol: string, tf: string) => void;
        onThemeChange?: (name: ThemeName) => void;
        onCandleStyleChange?: (name: CandleStyleName) => void;
        themeBg?: string;
        themeText?: string;
        themeBorder?: string;
        menuBg?: string;
        menuBorder?: string;
    } = $props();

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

    // ─── UTC clock ───
    let utcStr = $derived(
        (() => {
            const d = new Date(now);
            const hh = String(d.getUTCHours()).padStart(2, "0");
            const mm = String(d.getUTCMinutes()).padStart(2, "0");
            return `${hh}:${mm}`;
        })(),
    );

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
        themeOpen = false;
        styleOpen = false;
    }

    // ─── P3.11/P3.12: Theme + Candle style pickers ───
    let themeOpen = $state(false);
    let styleOpen = $state(false);
    let activeTheme: ThemeName = $state(loadTheme());
    let activeStyle: CandleStyleName = $state(loadCandleStyle());

    function toggleTheme(e: MouseEvent) {
        e.stopPropagation();
        symbolOpen = false;
        tfOpen = false;
        styleOpen = false;
        themeOpen = !themeOpen;
    }
    function toggleStyle(e: MouseEvent) {
        e.stopPropagation();
        symbolOpen = false;
        tfOpen = false;
        themeOpen = false;
        styleOpen = !styleOpen;
    }
    function selectTheme(name: ThemeName) {
        themeOpen = false;
        activeTheme = name;
        onThemeChange?.(name);
    }
    function selectStyle(name: CandleStyleName) {
        styleOpen = false;
        activeStyle = name;
        onCandleStyleChange?.(name);
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
                class="hud-slot hud-slot-sm hud-star"
                class:faved={isFaved}
                onclick={toggleFav}
                title={isFaved ? "Remove from favorites" : "Add to favorites"}
                >{isFaved ? "★" : "☆"}</button
            >

            <span class="hud-sep">·</span>

            <!-- P3.11: Theme picker -->
            <button
                class="hud-slot hud-slot-sm"
                onclick={toggleTheme}
                title="Theme">🎨</button
            >

            <!-- P3.12: Candle style picker -->
            <button
                class="hud-slot hud-slot-sm"
                onclick={toggleStyle}
                title="Candle style">🕯</button
            >
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

    <!-- P3.11: Theme dropdown -->
    {#if themeOpen}
        <div
            class="hud-menu hud-menu-tf"
            role="listbox"
            tabindex="-1"
            style:background={menuBg}
            style:border-color={menuBorder}
            style:color={themeText}
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => e.key === "Escape" && (themeOpen = false)}
        >
            {#each THEME_NAMES as t}
                <button
                    class="hud-menu-item"
                    class:active={t === activeTheme}
                    onclick={() => selectTheme(t)}
                >
                    {THEMES[t].label}
                </button>
            {/each}
        </div>
    {/if}

    <!-- P3.12: Candle style dropdown -->
    {#if styleOpen}
        <div
            class="hud-menu hud-menu-tf"
            role="listbox"
            tabindex="-1"
            style:background={menuBg}
            style:border-color={menuBorder}
            style:color={themeText}
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => e.key === "Escape" && (styleOpen = false)}
        >
            {#each CANDLE_STYLE_NAMES as cs}
                <button
                    class="hud-menu-item"
                    class:active={cs === activeStyle}
                    onclick={() => selectStyle(cs)}
                >
                    <span
                        class="candle-swatch"
                        style:background={CANDLE_STYLES[cs].upColor}
                    ></span>
                    {CANDLE_STYLES[cs].label}
                </button>
            {/each}
        </div>
    {/if}
</div>

<style>
    /* ─── HUD: Frosted glass (V3: .hud-stack / .hud) ─── */
    .hud-stack {
        position: absolute;
        top: 8px;
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

    /* P3.11/P3.12: Small icon buttons for theme/style pickers */
    .hud-slot-sm {
        font-size: 12px;
        padding: 1px 4px;
        opacity: 0.7;
    }
    .hud-slot-sm:hover {
        opacity: 1;
    }

    /* P3.12: Color swatch in candle style menu */
    .candle-swatch {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 2px;
        margin-right: 4px;
        vertical-align: middle;
    }

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
</style>
