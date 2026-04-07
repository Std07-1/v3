<script lang="ts">
  // src/App.svelte — Root wiring: WS + DiagState + frameRouter + UI shell.
  // Без бізнес-логіки у UI. Thin controller.

  import { onMount, onDestroy } from "svelte";
  import { WSConnection } from "./ws/connection";
  import { createActions } from "./ws/actions";

  import ChartPane from "./layout/ChartPane.svelte";
  import DrawingToolbar from "./layout/DrawingToolbar.svelte";
  import ChartHud from "./layout/ChartHud.svelte";
  import StatusOverlay from "./layout/StatusOverlay.svelte";
  import DiagPanel from "./layout/DiagPanel.svelte";
  import ReplayBar from "./layout/ReplayBar.svelte";

  // ADR-0027: Client-side replay store
  import { replayStore } from "./stores/replayStore.svelte";

  // P3.11/P3.12: Theme + candle style imports
  import type { ThemeName, CandleStyleName } from "./chart/lwc";
  import {
    THEMES,
    THEME_NAMES,
    CANDLE_STYLES,
    CANDLE_STYLE_NAMES,
    loadTheme,
    loadCandleStyle,
    applyThemeCssVars,
  } from "./chart/lwc";

  import {
    handleWSFrame,
    currentFrame,
    currentPair,
    resetFrameRouter,
    serverConfig,
    setBootIdChangeCallback,
  } from "./app/frameRouter";
  import { diagStore } from "./app/diagState";
  import { mainStatus } from "./app/diagSelectors";
  import type { StatusInfo } from "./app/diagSelectors";
  import { stopEdgeProbe, probeNow } from "./app/edgeProbe";
  import { metaStore } from "./stores/meta";

  import type { T_MS, UiWarning, RenderFrame, ActiveTool } from "./types";

  // --- WS URL: same-origin у prod, explicit у dev (Правило §11) ---
  // Dev (Vite :5173): import.meta.env.DEV=true → ws://localhost:8000/ws
  // Prod (aiohttp :8000 serves dist/): window.location.host → same-origin
  // Prod always uses wss: even if page loaded via HTTP (CF might serve HTTP before redirect)
  const WS_URL =
    import.meta.env.VITE_WS_URL ??
    (import.meta.env.DEV
      ? "ws://localhost:8000/ws"
      : `wss://${window.location.host}/ws`);

  let ws: WSConnection | null = null;
  let actions: ReturnType<typeof createActions> | null = null;

  let activeTool: ActiveTool = $state(null);

  // Symbol/TF persistence (drawing_tools_v1)
  // Priority: URL query params > localStorage
  // Seconds→label map for URL params (screenshot bot sends ?tf=3600)
  const _S_TO_LABEL: Record<string, string> = {
    "60": "M1",
    "180": "M3",
    "300": "M5",
    "900": "M15",
    "1800": "M30",
    "3600": "H1",
    "14400": "H4",
    "86400": "D1",
  };
  function loadLastPair(): { symbol: string; tf: string } | null {
    // URL params have highest priority (used by screenshot bot, deep links)
    try {
      const params = new URLSearchParams(window.location.search);
      const qs = params.get("symbol");
      let qt = params.get("tf");
      if (qs && qt) {
        // Accept both labels ("H1") and seconds ("3600")
        qt = _S_TO_LABEL[qt] ?? qt;
        return { symbol: qs, tf: qt };
      }
    } catch {}
    try {
      const raw = localStorage.getItem("v4_last_pair");
      if (!raw) return null;
      const p = JSON.parse(raw);
      return p && p.symbol && p.tf ? p : null;
    } catch {
      return null;
    }
  }
  function saveLastPair(symbol: string, tf: string): void {
    try {
      localStorage.setItem("v4_last_pair", JSON.stringify({ symbol, tf }));
    } catch {}
  }

  // Magnet mode: snap drawings to candle OHLC (drawing_tools_v1, PATCH 3)
  function loadMagnet(): boolean {
    try {
      return localStorage.getItem("v4_magnet_enabled") === "1";
    } catch {
      return false;
    }
  }
  let magnetEnabled = $state(loadMagnet());

  // P3.11/P3.12: ChartPane ref for theme/style delegation
  let chartPaneRef: any = $state(null);
  let activeTheme: ThemeName = $state(loadTheme());
  let appBg = $derived(THEMES[activeTheme]?.appBg ?? "#131722");
  let hudText = $derived(THEMES[activeTheme]?.hudText ?? "#d1d4dc");
  let menuBg = $derived(
    (THEMES[activeTheme] as any)?.menuBg ?? "rgba(30, 34, 45, 0.92)",
  );
  let menuBorder = $derived(
    (THEMES[activeTheme] as any)?.menuBorder ?? "rgba(255, 255, 255, 0.08)",
  );

  // Entry 078 §3a: Brightness control (0.80 - 1.20)
  function loadBrightness(): number {
    try {
      const v = parseFloat(localStorage.getItem("v4_brightness") ?? "1");
      if (Number.isFinite(v) && v >= 0.8 && v <= 1.2) return v;
    } catch {}
    return 1.0;
  }
  function saveBrightness(v: number): void {
    try {
      localStorage.setItem("v4_brightness", String(v));
    } catch {}
  }
  let brightness = $state(loadBrightness());
  let brightnessIcon = $derived(brightness >= 1.0 ? "☀" : "🌙");

  // Theme + Candle style pickers (moved from ChartHud to top-right-bar)
  let trThemeOpen = $state(false);
  let trStyleOpen = $state(false);
  let activeCandleStyle: CandleStyleName = $state(loadCandleStyle());

  function trToggleTheme(e: MouseEvent) {
    e.stopPropagation();
    trStyleOpen = false;
    trThemeOpen = !trThemeOpen;
  }
  function trToggleStyle(e: MouseEvent) {
    e.stopPropagation();
    trThemeOpen = false;
    trStyleOpen = !trStyleOpen;
  }
  function trSelectTheme(name: ThemeName) {
    trThemeOpen = false;
    handleThemeChange(name);
  }
  function trSelectStyle(name: CandleStyleName) {
    trStyleOpen = false;
    activeCandleStyle = name;
    handleCandleStyleChange(name);
  }

  function handleBrightnessWheel(e: WheelEvent) {
    e.preventDefault();
    e.stopPropagation();
    const delta = e.deltaY > 0 ? -0.02 : 0.02;
    brightness = Math.max(0.8, Math.min(1.2, +(brightness + delta).toFixed(2)));
    saveBrightness(brightness);
  }

  // Entry 077: Top-right compact bar (clock + health dot + diag toggle)
  const STATUS_COLORS: Record<string, string> = {
    HEALTHY: "#26a69a",
    CONNECTING: "#f0b90b",
    STALLED: "#ef5350",
    WS_UNAVAILABLE: "#ef5350",
    EDGE_BLOCKED: "#ef5350",
    OFFLINE: "#ef5350",
    FRONTEND_ERROR: "#ef5350",
  };
  let clockNow = $state(Date.now());
  let clockInterval: ReturnType<typeof setInterval> | null = null;
  let utcStr = $derived(
    (() => {
      const d = new Date(clockNow);
      const hh = String(d.getUTCHours()).padStart(2, "0");
      const mm = String(d.getUTCMinutes()).padStart(2, "0");
      return `${hh}:${mm}`;
    })(),
  );

  function handleThemeChange(name: ThemeName) {
    chartPaneRef?.applyTheme(name);
    activeTheme = name;
    // ADR-0007: оновити CSS custom properties для toolbar/drawings
    applyThemeCssVars(name);
  }
  function handleCandleStyleChange(name: CandleStyleName) {
    chartPaneRef?.applyCandleStyle(name);
  }

  // P3.14: Diagnostic panel toggle (Ctrl+Shift+D)
  let diagVisible = $state(false);
  function handleGlobalKeydown(e: KeyboardEvent) {
    if (e.ctrlKey && e.shiftKey && e.key === "D") {
      e.preventDefault();
      diagVisible = !diagVisible;
      return;
    }
    // Drawing hotkeys (drawing_tools_v1: audit T1 розблоковано)
    if (
      e.target instanceof HTMLInputElement ||
      e.target instanceof HTMLTextAreaElement
    )
      return;
    if (e.ctrlKey && e.key === "z") {
      e.preventDefault();
      chartPaneRef?.undo();
      return;
    }
    if (e.ctrlKey && e.key === "y") {
      e.preventDefault();
      chartPaneRef?.redo();
      return;
    }
    if (e.key === "Escape") {
      activeTool = null;
      chartPaneRef?.cancelDraft();
      return;
    }
    const k = e.key.toLowerCase();
    if (k === "t") {
      activeTool = activeTool === "trend" ? null : "trend";
      return;
    }
    if (k === "h") {
      activeTool = activeTool === "hline" ? null : "hline";
      return;
    }
    if (k === "r") {
      activeTool = activeTool === "rect" ? null : "rect";
      return;
    }
    if (k === "e") {
      activeTool = activeTool === "eraser" ? null : "eraser";
      return;
    }
    // DEFERRED: magnet hotkey disabled (drawing_tools_v1)
    // if (k === "g") { toggleMagnet(); return; }
  }

  // P3.1: HUD tracking — symbol/tf/price/timestamp from last frame
  let hudSymbol = $state("");
  let hudTf = $state("");
  let lastPrice: number | null = $state(null);
  let lastBarTs: number | null = $state(null);
  let lastBarOpen: number | null = $state(null);

  // --- Reactive subscriptions ---
  const unsubPair = currentPair.subscribe((p) => {
    if (p) {
      hudSymbol = p.symbol;
      hudTf = p.tf;
    }
  });

  let frame: RenderFrame | null = $state(null);
  let cachedBiasMap: Record<string, string> = $state({});
  let cachedMomentumMap: Record<string, { b: number; r: number }> = $state({});
  let cachedNarrative: import("./types").NarrativeBlock | null = $state(null);
  let cachedShell: import("./types").ShellPayload | null = $state(null);
  let cachedPdState: import("./types").PdState | null = $state(null);
  let statusInfo: StatusInfo = $state({
    status: "CONNECTING" as const,
    detail: "",
    critical: false,
  });

  let _pairRestored = false; // restore saved symbol/TF on first full frame per connection

  /** Called on every WS (re)connect — resets seq guard + pair restore flag */
  function _onWsOpen(): void {
    resetFrameRouter();
    _pairRestored = false;
  }

  const unsubFrame = currentFrame.subscribe((f) => {
    if (f && f.frame_type === "full" && !_pairRestored) {
      _pairRestored = true;
      const saved = loadLastPair();
      if (saved && (saved.symbol !== f.symbol || saved.tf !== f.tf)) {
        // Send switch immediately — server will send correct full frame
        actions?.switchSymbolTf(saved.symbol, saved.tf);
        // Speculatively patch HUD to avoid flicker until correct full frame arrives
        hudSymbol = saved.symbol;
        hudTf = saved.tf;
        return; // Don't render this default frame
      }
    }
    frame = f;
    // Persist bias/momentum — only full frames carry bias_map; deltas don't
    if (f?.bias_map && Object.keys(f.bias_map).length > 0)
      cachedBiasMap = f.bias_map;
    if (f?.momentum_map && Object.keys(f.momentum_map).length > 0)
      cachedMomentumMap = f.momentum_map;
    // ADR-0033+ADR-0035: narrative from full frame or delta (if present)
    if ((f as any)?.narrative != null)
      cachedNarrative = (f as any).narrative ?? null;
    // ADR-0036: shell payload caching (delta frames without shell don't reset it)
    if ((f as any)?.shell !== undefined) cachedShell = (f as any).shell ?? null;
    // ADR-0041 §5a: P/D state for thesis bar chip
    if (f?.pd_state !== undefined) cachedPdState = f.pd_state ?? null;
    // Track price/time from frames for HUD
    if (f) {
      const candles = f.candles;
      if (candles && candles.length > 0) {
        const last = candles[candles.length - 1];
        lastPrice = last.c;
        lastBarOpen = last.o;
        lastBarTs = Date.now(); // time of last WS frame, not candle open
      }
    }
  });
  const unsubStatus = mainStatus.subscribe((s) => {
    statusInfo = s;
  });

  // P2: SSOT symbols/tfs з сервера → picker props
  let cfgSymbols: string[] = $state([]);
  let cfgTfs: string[] = $state([]);
  const unsubConfig = serverConfig.subscribe((c) => {
    cfgSymbols = c.symbols;
    cfgTfs = c.tfs;
  });

  // --- Actions ---

  function scrollback(ms: T_MS) {
    actions?.scrollback(ms);
  }

  function addUiWarning(w: UiWarning) {
    metaStore.addUiWarning(w);
  }

  function handleReconnect() {
    if (ws) {
      ws.close();
    }
    // Note: resetFrameRouter + _pairRestored reset happen in _onWsOpen callback
    // Re-probe HTTP шар при ручному reconnect
    probeNow();
    ws = new WSConnection(WS_URL, handleWSFrame, _onWsOpen);
    ws.connect();
    actions = createActions(ws);
  }

  function handleReload() {
    window.location.reload();
  }

  // ADR-0027: Client-side replay
  function handleEnterReplay() {
    chartPaneRef?.enterReplay();
  }
  function handleExitReplay() {
    chartPaneRef?.exitReplay();
    // Request fresh full frame для поточного symbol/tf
    if (hudSymbol && hudTf) {
      actions?.switchSymbolTf(hudSymbol, hudTf);
    }
  }

  // Drawing hotkeys: merged into handleGlobalKeydown (drawing_tools_v1)

  // --- Lifecycle ---

  onMount(() => {
    // 1. Global error handler → DiagState
    window.addEventListener("error", onGlobalError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);

    // 2. Network offline/online → DiagState
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    diagStore.setNetOffline(!navigator.onLine);

    // 3. WS connect (onOpen resets frameRouter + pair restore on every reconnect)
    ws = new WSConnection(WS_URL, handleWSFrame, _onWsOpen);
    ws.connect();
    actions = createActions(ws);

    // ADR-0043 P3: boot_id change → очистити UI кеші (D5 fix)
    // Захищає від stale overlay при server restart перед першим full frame
    setBootIdChangeCallback(() => {
      cachedNarrative = null;
      cachedShell = null;
      cachedBiasMap = {};
      cachedMomentumMap = {};
      cachedPdState = null;
    });

    // 4. Clock tick for top-right UTC display
    clockInterval = setInterval(() => {
      clockNow = Date.now();
    }, 1000);

    // ADR-0007: initial CSS custom properties для поточної теми
    applyThemeCssVars(activeTheme);
  });

  onDestroy(() => {
    unsubPair();
    unsubFrame();
    unsubStatus();
    unsubConfig();
    ws?.close();
    stopEdgeProbe();
    if (clockInterval) clearInterval(clockInterval);
    window.removeEventListener("error", onGlobalError);
    window.removeEventListener("unhandledrejection", onUnhandledRejection);
    window.removeEventListener("online", onOnline);
    window.removeEventListener("offline", onOffline);
  });

  // --- Event handlers ---

  function onGlobalError(ev: ErrorEvent) {
    // LWC 5.0.0 internal render bug: ensureNotNull() throws "Value is null"
    // when Candlestick color resolver encounters whitespace data at time gaps.
    // The chart self-heals on next render — safe to suppress.
    if (
      ev.message === "Value is null" ||
      ev.message === "Uncaught Error: Value is null"
    ) {
      ev.preventDefault();
      console.debug(
        "[LWC] suppressed render crash: Value is null (whitespace gap)",
      );
      return;
    }
    const message = ev.message;
    diagStore.setFeError({
      message: message,
      stack: ev.error?.stack,
      ts_ms: Date.now(),
    });
  }

  function onUnhandledRejection(ev: PromiseRejectionEvent) {
    const msg =
      ev.reason?.message ?? String(ev.reason ?? "unhandled rejection");
    diagStore.setFeError({
      message: msg,
      stack: ev.reason?.stack,
      ts_ms: Date.now(),
    });
  }

  function onOnline() {
    diagStore.setNetOffline(false);
  }

  function onOffline() {
    diagStore.setNetOffline(true);
  }
</script>

<svelte:window onkeydown={handleGlobalKeydown} />

<main class="app-layout" style:background={appBg}>
  <!-- Main content area -->
  <div class="main-content">
    <div class="chart-wrapper">
      <DrawingToolbar {activeTool} onSelectTool={(t) => (activeTool = t)} />
      <ChartPane
        bind:this={chartPaneRef}
        currentFrame={frame}
        {scrollback}
        {addUiWarning}
        {brightness}
        {activeTool}
        {magnetEnabled}
      />
      <!-- P3.1-P3.2: Frosted-glass HUD overlay -->
      <ChartHud
        symbols={cfgSymbols}
        tfs={cfgTfs}
        currentSymbol={hudSymbol}
        currentTf={hudTf}
        {lastPrice}
        {lastBarOpen}
        {lastBarTs}
        onSwitch={(sym, tf) => {
          actions?.switchSymbolTf(sym, tf);
          saveLastPair(sym, tf);
        }}
        themeText={hudText}
        {menuBg}
        {menuBorder}
        biasMap={cachedBiasMap}
        momentumMap={cachedMomentumMap}
        narrative={cachedNarrative}
        shell={cachedShell}
        pdState={cachedPdState}
        utcTime={utcStr + " UTC"}
      />
    </div>
    <!-- ADR-0027: Replay controls bar (visible only when replay active) -->
    {#if replayStore.active}
      <ReplayBar onExit={handleExitReplay} />
    {/if}
  </div>

  <!-- ADR-0027: Replay enter button (top-right, near clock bar) -->
  {#if !replayStore.active}
    <button
      class="replay-enter-btn"
      onclick={handleEnterReplay}
      title="Enter Replay Mode">Replay</button
    >
  {:else}
    <span class="replay-badge">REPLAY</span>
  {/if}

  <!-- Entry 078: Compact top-right bar (health dot + brightness + pickers + diag + clock) -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="top-right-bar"
    onclick={() => {
      trThemeOpen = false;
      trStyleOpen = false;
    }}
  >
    <!-- Theme picker -->
    <div class="tr-picker-wrap">
      <button class="tr-picker-btn" onclick={trToggleTheme} title="Theme"
        >◐</button
      >
      {#if trThemeOpen}
        <div class="tr-dropdown" onclick={(e) => e.stopPropagation()}>
          {#each THEME_NAMES as t}
            <button
              class="tr-dd-item"
              class:active={t === activeTheme}
              onclick={() => trSelectTheme(t)}>{THEMES[t].label}</button
            >
          {/each}
        </div>
      {/if}
    </div>
    <!-- Candle style picker -->
    <div class="tr-picker-wrap">
      <button class="tr-picker-btn" onclick={trToggleStyle} title="Candle style"
        >▮</button
      >
      {#if trStyleOpen}
        <div class="tr-dropdown" onclick={(e) => e.stopPropagation()}>
          {#each CANDLE_STYLE_NAMES as cs}
            <button
              class="tr-dd-item"
              class:active={cs === activeCandleStyle}
              onclick={() => trSelectStyle(cs)}
            >
              <span
                class="tr-swatch"
                style:background={CANDLE_STYLES[cs].upColor}
              ></span>
              {CANDLE_STYLES[cs].label}
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <span class="tr-sep"></span>
    <span
      class="tr-dot"
      style:background={STATUS_COLORS[statusInfo.status] ?? "#888"}
    ></span>
    <span
      class="tr-brightness"
      onwheel={handleBrightnessWheel}
      title={`Brightness ${Math.round(brightness * 100)}% — scroll to adjust`}
      >{brightnessIcon}</span
    >
    <span class="tr-sep"></span>
    <button
      class="tr-diag-btn"
      onclick={() => (diagVisible = !diagVisible)}
      title="Diagnostics (Ctrl+Shift+D)">🔧</button
    >
    <span class="tr-clock" style:color={hudText}>{utcStr} UTC</span>
  </div>

  <!-- Overlay for critical states -->
  <StatusOverlay
    {statusInfo}
    wsUrl={WS_URL}
    onReconnect={handleReconnect}
    onReload={handleReload}
  />

  <!-- P3.14: Diagnostic panel (Ctrl+Shift+D) -->
  <DiagPanel visible={diagVisible} />
</main>

<style>
  .app-layout {
    display: flex;
    flex-direction: column;
    width: 100vw;
    height: 100vh; /* fallback for older browsers */
    height: var(
      --app-vh,
      100dvh
    ); /* P1: JS-driven viewport height, dvh fallback */
    overflow: hidden;
    /* background set dynamically via style:background= for theme switching */
    color: #d1d4dc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      sans-serif;
  }

  .main-content {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .chart-wrapper {
    flex: 1 1 auto;
    min-width: 0;
    position: relative;
    touch-action: none; /* prevent browser gestures conflicting with chart pan/zoom */
    -webkit-touch-callout: none; /* disable iOS callout on long-press */
    -webkit-user-select: none;
    user-select: none;
  }

  /* Entry 078: Compact top-right bar — no bg, shifted left from price axis */
  /* ADR-0043 P5: right: 64px → 4px (усунення overlap з smc-panel, D7 fix) */
  .top-right-bar {
    position: fixed;
    top: 8px;
    right: 4px;
    z-index: 35;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 12px;
    background: transparent;
    pointer-events: auto;
  }
  .tr-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .tr-brightness {
    cursor: pointer;
    font-size: 14px;
    opacity: 0.6;
    transition: opacity 0.15s;
    user-select: none;
  }
  .tr-brightness:hover {
    opacity: 1;
  }
  .tr-diag-btn {
    all: unset;
    cursor: pointer;
    font-size: 13px;
    opacity: 0.5;
    transition: opacity 0.15s;
  }
  .tr-diag-btn:hover {
    opacity: 1;
  }
  .tr-clock {
    font-size: 11px;
    font-family: "Roboto Mono", monospace, sans-serif;
    white-space: nowrap;
  }

  /* ADR-0027: Replay enter button — prominent capsule */
  .replay-enter-btn {
    all: unset;
    position: fixed;
    bottom: 4px;
    right: 4px;
    z-index: 35;
    cursor: pointer;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 4px 10px;
    border-radius: 6px;
    color: #8b8f9a;
    background: rgba(30, 34, 45, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.06);
    backdrop-filter: blur(8px);
    transition: all 0.18s ease;
    pointer-events: auto;
  }
  .replay-enter-btn:hover {
    color: #f0b90b;
    background: rgba(240, 185, 11, 0.1);
    border-color: rgba(240, 185, 11, 0.25);
    box-shadow: 0 0 8px rgba(240, 185, 11, 0.1);
  }
  .replay-badge {
    position: fixed;
    bottom: 4px;
    right: 4px;
    z-index: 35;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 5px 12px;
    border-radius: 6px;
    color: #f0b90b;
    background: rgba(240, 185, 11, 0.08);
    border: 1px solid rgba(240, 185, 11, 0.3);
    pointer-events: none;
  }

  /* Theme/Candle picker: separator + inline dropdowns */
  .tr-sep {
    width: 1px;
    height: 12px;
    background: rgba(255, 255, 255, 0.08);
    flex-shrink: 0;
  }
  .tr-picker-wrap {
    position: relative;
  }
  .tr-picker-btn {
    all: unset;
    cursor: pointer;
    font-size: 13px;
    opacity: 0.45;
    transition: opacity 0.15s;
    padding: 0 2px;
    line-height: 1;
  }
  .tr-picker-btn:hover {
    opacity: 0.9;
  }
  .tr-dropdown {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    display: flex;
    flex-direction: row;
    gap: 2px;
    padding: 4px;
    background: rgba(30, 34, 45, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    backdrop-filter: blur(12px);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    z-index: 100;
    white-space: nowrap;
  }
  .tr-dd-item {
    all: unset;
    cursor: pointer;
    padding: 4px 8px;
    font-size: 11px;
    color: #8b8f9a;
    border-radius: 4px;
    transition: background 0.12s;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .tr-dd-item:hover {
    background: rgba(128, 128, 128, 0.15);
    color: #d1d4dc;
  }
  .tr-dd-item.active {
    background: rgba(74, 144, 217, 0.2);
    color: #4a90d9;
    font-weight: 600;
  }
  .tr-swatch {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 2px;
  }

  /* ═══ P5: Mobile responsive (768px breakpoint) ═══ */
  @media (max-width: 768px) {
    /* Hide entire top-right bar — clock moved to HUD row */
    .top-right-bar {
      display: none !important;
    }
    /* Hide replay controls on mobile */
    .replay-enter-btn,
    .replay-badge {
      display: none;
    }
  }
</style>
