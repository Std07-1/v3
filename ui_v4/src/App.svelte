<script lang="ts">
  // src/App.svelte — Root wiring: WS + DiagState + frameRouter + UI shell.
  // Без бізнес-логіки у UI. Thin controller.

  import { onMount, onDestroy } from "svelte";
  import { WSConnection } from "./ws/connection";
  import { createActions } from "./ws/actions";

  import ChartPane from "./layout/ChartPane.svelte";
  // DISABLED: trading tools deferred (audit T1)
  // import DrawingToolbar from "./layout/DrawingToolbar.svelte";
  import ChartHud from "./layout/ChartHud.svelte";
  import StatusOverlay from "./layout/StatusOverlay.svelte";
  import DiagPanel from "./layout/DiagPanel.svelte";

  // P3.11/P3.12: Theme + candle style imports
  import type { ThemeName, CandleStyleName } from "./chart/lwc";
  import { THEMES, loadTheme } from "./chart/lwc";

  import {
    handleWSFrame,
    currentFrame,
    resetFrameRouter,
    uiWarnings as routerUiWarnings,
    serverConfig,
  } from "./app/frameRouter";
  import { diagStore } from "./app/diagState";
  import { mainStatus } from "./app/diagSelectors";
  import type { StatusInfo } from "./app/diagSelectors";
  import { stopEdgeProbe, probeNow } from "./app/edgeProbe";
  import { metaStore } from "./stores/meta";

  import type { WsAction, T_MS, UiWarning, RenderFrame } from "./types";

  // --- WS URL: same-origin у prod, explicit у dev (Правило §11) ---
  // Dev (Vite :5173): import.meta.env.DEV=true → ws://localhost:8000/ws
  // Prod (aiohttp :8000 serves dist/): window.location.host → same-origin
  const WS_URL =
    import.meta.env.VITE_WS_URL ??
    (import.meta.env.DEV
      ? "ws://localhost:8000/ws"
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`);

  let ws: WSConnection | null = null;
  let actions: ReturnType<typeof createActions> | null = null;

  // DISABLED: trading tools deferred (audit T1)
  // let activeTool: ActiveTool = $state(null);

  // P3.11/P3.12: ChartPane ref for theme/style delegation
  let chartPaneRef: any = $state(null);
  let activeTheme: ThemeName = $state(loadTheme());
  let appBg = $derived(THEMES[activeTheme]?.appBg ?? "#131722");
  let hudBg = $derived(THEMES[activeTheme]?.hudBg ?? "transparent");
  let hudText = $derived(THEMES[activeTheme]?.hudText ?? "#d1d4dc");
  let hudBorder = $derived(THEMES[activeTheme]?.hudBorder ?? "transparent");
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
    }
  }

  // P3.1: HUD tracking — symbol/tf/price/timestamp from last frame
  let hudSymbol = $state("");
  let hudTf = $state("");
  let lastPrice: number | null = $state(null);
  let lastBarTs: number | null = $state(null);
  let lastBarOpen: number | null = $state(null);

  // --- Reactive subscriptions ---
  let frame: RenderFrame | null = $state(null);
  let statusInfo: StatusInfo = $state({
    status: "CONNECTING" as const,
    detail: "",
    critical: false,
  });

  const unsubFrame = currentFrame.subscribe((f) => {
    frame = f;
    // P3.1: Track symbol/tf/price from frames for HUD
    if (f) {
      if (f.symbol) hudSymbol = f.symbol;
      if (f.tf) hudTf = f.tf;
      const candles = f.candles;
      if (candles && candles.length > 0) {
        const last = candles[candles.length - 1];
        lastPrice = last.c;
        lastBarOpen = last.o;
        lastBarTs = last.t_ms;
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

  function sendRawAction(a: WsAction) {
    ws?.sendAction(a);
  }

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
    resetFrameRouter();
    // Re-probe HTTP шар при ручному reconnect
    probeNow();
    ws = new WSConnection(WS_URL, handleWSFrame);
    ws.connect();
    actions = createActions(ws);
  }

  function handleReload() {
    window.location.reload();
  }

  // DISABLED: drawing keyboard shortcuts deferred (audit T1)
  // Hotkeys: T(trend), H(hline), R(rect), E(eraser), Escape, Ctrl+Z/Y

  // --- Lifecycle ---

  onMount(() => {
    // 1. Global error handler → DiagState
    window.addEventListener("error", onGlobalError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);

    // 2. Network offline/online → DiagState
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    diagStore.setNetOffline(!navigator.onLine);

    // 3. WS connect
    ws = new WSConnection(WS_URL, handleWSFrame);
    ws.connect();
    actions = createActions(ws);

    // 4. Clock tick for top-right UTC display
    clockInterval = setInterval(() => {
      clockNow = Date.now();
    }, 1000);
  });

  onDestroy(() => {
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
    diagStore.setFeError({
      message: ev.message ?? "unknown error",
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

<!-- DISABLED: <svelte:window onkeydown={handleKeydown} /> — trading tools deferred (audit T1) -->
<svelte:window onkeydown={handleGlobalKeydown} />

<main class="app-layout" style:background={appBg}>
  <!-- Main content area -->
  <div class="main-content">
    <!-- DISABLED: trading tools deferred (audit T1) -->
    <!-- <DrawingToolbar {activeTool} onSelectTool={(t) => (activeTool = t)} /> -->
    <div class="chart-wrapper">
      <ChartPane
        bind:this={chartPaneRef}
        currentFrame={frame}
        {sendRawAction}
        {scrollback}
        {addUiWarning}
        {brightness}
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
        onSwitch={(sym, tf) => actions?.switchSymbolTf(sym, tf)}
        onThemeChange={handleThemeChange}
        onCandleStyleChange={handleCandleStyleChange}
        themeBg={hudBg}
        themeText={hudText}
        themeBorder={hudBorder}
        {menuBg}
        {menuBorder}
      />
    </div>
  </div>

  <!-- Entry 078: Compact top-right bar (health dot + brightness + diag + clock) -->
  <div class="top-right-bar">
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
    height: 100vh;
    overflow: hidden;
    /* background set dynamically via style:background= for theme switching */
    color: #d1d4dc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      sans-serif;
  }

  .main-content {
    flex: 1 1 auto;
    display: flex;
    min-height: 0;
  }

  .chart-wrapper {
    flex: 1 1 auto;
    min-width: 0;
    position: relative;
  }

  /* Entry 078: Compact top-right bar — no bg, shifted left from price axis */
  .top-right-bar {
    position: fixed;
    top: 8px;
    right: 64px;
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
</style>
