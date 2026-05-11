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
  import ReplayBar from "./layout/ReplayBar.svelte";
  import InfoModal from "./layout/InfoModal.svelte";
  import BrandWatermark from "./layout/BrandWatermark.svelte";
  import CommandRailOverflow from "./layout/CommandRailOverflow.svelte";
  import Splash from "./layout/Splash.svelte";
  import NarrativePanel from "./layout/NarrativePanel.svelte";
  import CommandRail from "./layout/CommandRail.svelte";

  // ADR-0027: Client-side replay store
  import { replayStore } from "./stores/replayStore.svelte";

  // P3.11/P3.12: Theme + candle style imports
  import type { ThemeName, CandleStyleName } from "./chart/lwc";
  import {
    THEMES,
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
  import { dismissOnOutside } from "./lib/actions/dismissOnOutside";
  import type { StatusInfo } from "./app/diagSelectors";
  import { stopEdgeProbe, probeNow } from "./app/edgeProbe";
  import { metaStore } from "./stores/meta";

  import type { T_MS, UiWarning, RenderFrame, ActiveTool } from "./types";
  import type { DisplayMode } from "./chart/overlay/DisplayBudget";

  // --- WS URL: same-origin у prod, explicit у dev (Правило §11) ---
  // Dev (Vite :5173): import.meta.env.DEV=true → ws://localhost:8000/ws
  // Prod (aiohttp :8000 serves dist/): window.location.host → same-origin
  // Protocol: ws:// for http, wss:// for https (supports local testing via http://127.0.0.1:8000)
  const WS_URL =
    import.meta.env.VITE_WS_URL ??
    (import.meta.env.DEV
      ? "ws://localhost:8000/ws"
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`);

  let ws: WSConnection | null = null;
  let actions: ReturnType<typeof createActions> | null = null;

  let activeTool: ActiveTool = $state(null);

  // ADR-0065 Phase 1: SMC panel open + display mode lifted here from ChartPane.
  // CommandRailOverflow is the single trigger surface; ChartPane is a $bindable consumer.
  let smcPanelOpen = $state(false);
  let displayMode = $state<DisplayMode>("focus");

  // ADR-0073 option D — Crosshair toggle прибрано (2026-05-12).
  // LWC applyOptions після init не реактивує labelVisible (quirk).
  // `labelVisible: false` lock назавжди у engine.ts crosshair config.

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
  let hudText = $derived(THEMES[activeTheme]?.hudText ?? "var(--text-1)");
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

  // Active theme + candle style — passed to CommandRailOverflow as props.
  let activeCandleStyle: CandleStyleName = $state(loadCandleStyle());

  function handleBrightnessWheel(e: WheelEvent) {
    e.preventDefault();
    e.stopPropagation();
    const delta = e.deltaY > 0 ? -0.02 : 0.02;
    brightness = Math.max(0.8, Math.min(1.2, +(brightness + delta).toFixed(2)));
    saveBrightness(brightness);
  }
  // ADR-0065 UX fix: ◀/▶ step buttons — 5 steps across [0.8, 1.2] = 0.08 each
  function handleBrightnessStep(direction: number) {
    brightness = Math.max(
      0.8,
      Math.min(1.2, +(brightness + direction * 0.08).toFixed(2)),
    );
    saveBrightness(brightness);
  }

  // ADR-0065 rev 2: Top-right command rail UTC clock (no health dot — moved to ChartHud).
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
    activeCandleStyle = name;
    chartPaneRef?.applyCandleStyle(name);
  }

  // ADR-0065 rev 2 Tier 2: Overflow menu (☰) state.
  let overflowOpen = $state(false);
  function toggleOverflow(e: MouseEvent) {
    e.stopPropagation();
    overflowOpen = !overflowOpen;
    // Mutual exclusion: overflow and SMC panel must not overlap (ADR-0065 UX fix)
    if (overflowOpen) smcPanelOpen = false;
  }
  function closeOverflow() {
    overflowOpen = false;
  }

  // Outside-click/touch/Escape dismiss: централізована action
  // dismissOnOutside (lib/actions/dismissOnOutside.ts) — єдиний паттерн
  // для всіх dismissable panels у UI. Раніше тут був ad-hoc $effect що
  // дублював 4 окремі реалізації в App/ChartPane/ChartHud/NarrativePanel.

  // ADR-0068: Single InfoModal instance, defaultTab controlled by last opener.
  // - Brand watermark click → "about"
  // - Overflow menu Diagnostics item → "diagnostics"
  // - Ctrl+Shift+D → "diagnostics"
  let infoOpen = $state(false);
  let infoTab = $state<"about" | "credits" | "diagnostics">("about");
  function openAbout() {
    infoTab = "about";
    infoOpen = true;
  }
  function openDiagnostics() {
    infoTab = "diagnostics";
    infoOpen = true;
  }

  // ADR-0066 PATCH 04b: Splash overlay during initial WS warming.
  // Shown when no first frame has arrived yet AND status is non-fatal
  // (CONNECTING / HEALTHY-but-no-data). Auto-hides on first WS frame.
  let firstFrameArrived = $state(false);
  let splashVisible = $derived(
    !firstFrameArrived &&
      statusInfo.status !== "OFFLINE" &&
      statusInfo.status !== "EDGE_BLOCKED" &&
      statusInfo.status !== "WS_UNAVAILABLE" &&
      statusInfo.status !== "FRONTEND_ERROR",
  );

  // P3.14: Ctrl+Shift+D → open InfoModal[diagnostics] (legacy DiagPanel removed).
  function handleGlobalKeydown(e: KeyboardEvent) {
    if (e.ctrlKey && e.shiftKey && e.key === "D") {
      e.preventDefault();
      openDiagnostics();
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
      // ADR-0066 PATCH 04b: dismiss splash on first frame with data
      if (
        !firstFrameArrived &&
        (f.frame_type === "full" || (candles && candles.length > 0))
      ) {
        firstFrameArrived = true;
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

  // Dynamic tab title — `{SYMBOL} {PRICE}` (минімально для max info-density).
  // Strips `/` from forex symbols (XAU/USD -> XAUUSD per ADR §1100).
  // Фавікон лишається mark-v3.svg (ADR-0066), wordmark + TF з title видалено
  // для PWA standalone та узагалі економії місця у tab strip.
  // Fallback chain: sym+price → sym → "AI · ONE" (boot state).
  $effect(() => {
    const sym = hudSymbol.replace(/\//g, "");
    const price = lastPrice;
    if (sym && price != null && Number.isFinite(price)) {
      // Adaptive precision — XAU 2 decimals, BTC 2, smaller alts may need more.
      // For V1 keep 2 (matches priceFormatter default for major instruments).
      document.title = `${sym} ${price.toFixed(2)}`;
    } else if (sym) {
      document.title = sym;
    } else {
      document.title = "AI · ONE";
    }
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
      <!-- ADR-0068 Slice 3 / Part A: Brand watermark — locked slot bottom-left
           above LWC time axis. Click opens InfoModal[About]. DO NOT MOVE.
           See: ui_v4/src/layout/BrandWatermark.svelte header + memory file
           /memories/repo/brand-watermark-locked-slot.md -->
      <BrandWatermark onclick={openAbout} />
      <DrawingToolbar {activeTool} onSelectTool={(t) => (activeTool = t)} />
      <ChartPane
        bind:this={chartPaneRef}
        currentFrame={frame}
        {scrollback}
        {addUiWarning}
        {brightness}
        {activeTool}
        {magnetEnabled}
        bind:smcPanelOpen
        bind:displayMode
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

  <!-- ADR-0065 rev 2 Tier 1: Top-right inline command rail.
       Layout: [ATR · RV · M{tf}-cd · UTC]  |  ▶ replay  |  ☰ overflow
       Mobile <640px reflow: status row + ▶ hidden, lishaye [☰]. -->
  <div class="top-right-bar">
    <!-- ADR-0065 Phase 1: NarrativePanel as leftmost inline pill in the rail row.
         Compact by default; expanded body drops absolute below bar.
         .narrative-wrap hidden on mobile (see @media block below). -->
    <div class="narrative-wrap">
      <NarrativePanel
        narrative={cachedNarrative}
        shell={cachedShell}
        currentSymbol={hudSymbol}
        currentTf={hudTf}
        inline={true}
      />
    </div>

    <!-- Tier 1 status row: ATR + RV (backend SSOT, ADR-0070) + countdown + UTC clock.
         lastPrice passed for ATR % normalization (display arithmetic OK per X28).
         Countdown is display arithmetic (wallclock + tf bucket math) per X28. -->
    <div class="tr-status-row">
      <CommandRail
        atr={frame?.atr ?? null}
        rv={frame?.rv ?? null}
        lastPrice={lastPrice}
        currentTf={hudTf}
        nowMs={clockNow}
      />
      <span class="tr-clock" style:color={hudText}>{utcStr} UTC</span>
    </div>

    <span class="tr-sep tr-sep-status"></span>

    <!-- Replay enter button -->
    {#if !replayStore.active}
      <button
        class="tr-replay-btn"
        onclick={handleEnterReplay}
        title="Enter Replay Mode"
        aria-label="Enter replay mode">▶</button
      >
    {:else}
      <span class="tr-replay-badge">REPLAY</span>
    {/if}

    <!-- Tier 2: ☰ Overflow menu trigger -->
    <div
      class="tr-overflow-wrap"
      onclick={(e) => e.stopPropagation()}
      use:dismissOnOutside={{
        enabled: overflowOpen,
        onDismiss: closeOverflow,
      }}
    >
      <button
        class="tr-overflow-btn"
        class:open={overflowOpen}
        onclick={toggleOverflow}
        title="More options (theme, style, brightness, diagnostics)"
        aria-haspopup="menu"
        aria-expanded={overflowOpen}
        aria-label="More options">☰</button
      >
      <CommandRailOverflow
        open={overflowOpen}
        {activeTheme}
        activeStyle={activeCandleStyle}
        {brightness}
        onSelectTheme={handleThemeChange}
        onSelectStyle={handleCandleStyleChange}
        onBrightnessWheel={handleBrightnessWheel}
        onBrightnessStep={handleBrightnessStep}
        onOpenDiagnostics={openDiagnostics}
        onClose={closeOverflow}
        {smcPanelOpen}
        {displayMode}
        onToggleSmc={() => (smcPanelOpen = !smcPanelOpen)}
        onToggleDisplayMode={() =>
          (displayMode = displayMode === "focus" ? "research" : "focus")}
      />
    </div>
  </div>

  <!-- Overlay for critical states -->
  <StatusOverlay
    {statusInfo}
    wsUrl={WS_URL}
    onReconnect={handleReconnect}
    onReload={handleReload}
  />

  <!-- ADR-0068: Single InfoModal — defaultTab driven by openAbout/openDiagnostics. -->
  <InfoModal
    open={infoOpen}
    onClose={() => (infoOpen = false)}
    defaultTab={infoTab}
  />

  <!-- ADR-0066 PATCH 04b: Cold-load splash with Brand lockup, hides on first frame. -->
  <Splash visible={splashVisible} />
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
    color: var(--text-1);
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

  /* ADR-0068 Slice 3 / Part A: Brand watermark slot lives in BrandWatermark.svelte
     (locked CSS — desktop bottom:36/left:12, mobile bottom:30/left:6).
     DO NOT add .brand-slot rules here. */

  /* ADR-0065 rev 2 Tier 1: Top-right inline command rail.
     Layout: [NarrativePanel pill] [ATR · RV · M{tf}-cd · UTC] | ▶ replay | ☰ overflow
     ADR-0065 Phase 1: right:64px — clears LWC price scale (~54px) + 10px gap. */
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
  .tr-status-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tr-clock {
    font-size: var(--t3-size);
    font-family: "Roboto Mono", monospace, sans-serif;
    white-space: nowrap;
  }
  .tr-sep,
  .tr-sep-status {
    width: 1px;
    height: 12px;
    background: rgba(255, 255, 255, 0.08);
    flex-shrink: 0;
  }

  /* Tier 1 ▶ replay button (peripheral capsule, never primary) */
  .tr-replay-btn {
    all: unset;
    cursor: pointer;
    font-size: var(--t1-size);
    line-height: 1;
    padding: 2px 8px;
    border-radius: 4px;
    color: var(--text-3, #8b8f9a);
    opacity: 0.7;
    transition:
      opacity 0.15s ease,
      color 0.15s ease,
      background 0.15s ease;
  }
  .tr-replay-btn:hover {
    opacity: 1;
    color: var(--accent, #d4a017);
    background: color-mix(in srgb, var(--accent, #d4a017) 10%, transparent);
  }
  .tr-replay-btn:focus-visible {
    outline: 1px solid var(--accent, #d4a017);
    outline-offset: 2px;
  }
  .tr-replay-badge {
    font-size: var(--t3-size);
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 8px;
    border-radius: 4px;
    color: var(--accent, #d4a017);
    background: color-mix(in srgb, var(--accent, #d4a017) 8%, transparent);
    border: 1px solid
      color-mix(in srgb, var(--accent, #d4a017) 30%, transparent);
  }

  /* Tier 2: ☰ overflow button — anchor for CommandRailOverflow dropdown */
  .tr-overflow-wrap {
    position: relative;
  }
  .tr-overflow-btn {
    all: unset;
    cursor: pointer;
    font-size: var(--t2-size);
    line-height: 1;
    padding: 2px 6px;
    border-radius: 4px;
    color: var(--text-3, #8b8f9a);
    opacity: 0.7;
    transition:
      opacity 0.15s ease,
      color 0.15s ease,
      background 0.15s ease;
  }
  /* Hover wrapped у hover-capable media — на mobile тач не лишав sticky
     підсвітку (owner-flagged 2026-05-11 "слід від того квадрату").
     .open NEVER paints a background — menu itself is the active indicator.
     Тільки колір/opacity змінюємо щоб ☰ був видимий поверх chart. */
  @media (hover: hover) {
    .tr-overflow-btn:hover {
      opacity: 1;
      color: var(--text-1);
    }
  }
  .tr-overflow-btn.open {
    opacity: 1;
    color: var(--text-1);
  }
  .tr-overflow-btn:focus-visible {
    outline: 1px solid var(--accent, #d4a017);
    outline-offset: 2px;
  }

  /* Landscape phone reflow (orthogonal to portrait <640px below).
     Modern phones in landscape are 720-932px WIDE — well above 640px —
     so the portrait media query below does NOT fire. We catch them via
     height: any phone landscape is <500px tall, no tablet is.
     User-confirmed scope 2026-05-11: hide NP pill + replay button.
     KEEP CommandRail status row (ATR/RV/M15-cd/UTC) — landscape has
     horizontal real estate, peripheral context still useful. */
  @media (orientation: landscape) and (max-height: 500px) {
    .narrative-wrap,
    .tr-replay-btn,
    .tr-replay-badge {
      display: none;
    }
    /* ChartHud у landscape підняли в один row (top:0, padding:2px 4px),
       тож top-right-bar теж пригортаємо до краю — мінімум хром-плити,
       максимум чарту. tr-clock + CommandRail компактні (t4/t5 розміри).
       Owner-flagged 2026-05-11: "верхушка краде місце". */
    .top-right-bar {
      top: calc(0px + var(--safe-top, 0px));
      padding: 2px 8px;
      gap: 6px;
    }
    .tr-clock {
      font-size: var(--t4-size);
    }
    .tr-overflow-btn {
      font-size: var(--t3-size);
      padding: 1px 4px;
    }
  }

  /* ═══ ADR-0065 rev 2 Tier 3: Mobile reflow (640px breakpoint) ═══
     <640px: hide ATR/RV row + ▶ replay; KEEP ☰ overflow visible.
     The ☰ is the only chrome trigger on mobile — without it user has
     no access to theme/style/diagnostics. Was buggy hidden previously. */
  @media (max-width: 640px) {
    .top-right-bar {
      /* ☰ position — empirically locked 2026-05-11 via live mobile tuning.
         Geometry (measured on actual device, NOT calculated):
           [ chart canvas ............ ☰ ][ price scale ]
                                      ↑ │  ↑ "4712.00" etc
                                      │ 4px gap
                                      44px from viewport right edge
         Mobile price scale renders at ~40px wide on this device (LESS than
         engine.ts isMobile minimumWidth:44 — owner-verified, do NOT trust
         minimumWidth as actual width). right:44 = 40 scale + 4 gap.
         If price-scale width changes (engine.ts override or different
         device), re-measure: take screenshot, count pixels from right edge
         to where labels start, set right = that + desired gap.
         top:2px aligns vertically with ChartHud row 1 (XAU/USD · WAIT). */
      right: calc(44px + var(--safe-right, 0px));
      top: calc(2px + var(--safe-top, 0px));
      padding: 0;
      gap: 4px;
      z-index: 40;
    }
    /* Hidden: ATR/RV peripheral row (small screen — chrome economy),
       replay button + badge (replay UX is desktop-first MVP),
       NP narrative pill (Архі-surface deferred to mobile sheet ADR-0071). */
    .tr-status-row,
    .tr-sep-status,
    .tr-replay-btn,
    .tr-replay-badge,
    .narrative-wrap {
      display: none;
    }
    /* ☰ on mobile: fully transparent button + glyph anchored to top of
       hit-box so it visually sits ON THE SAME ROW as ChartHud row 1
       (XAU/USD · M15 · WAIT). 44×44px hit-area preserved via asymmetric
       padding (small top, large bottom). User explicit: "прозоре · в
       одному row з ціною · відступи від ціни". */
    .tr-overflow-btn {
      min-width: 44px;
      min-height: 44px;
      /* top: 2px → glyph appears at row 1 vertical center.
         bottom: 22px → invisible hit-extension downward (into row 2 area). */
      padding: 2px 12px 22px 12px;
      font-size: var(--t1-size, 18px);
      line-height: 1;
      display: inline-flex;
      align-items: flex-start;
      justify-content: center;
      background: transparent;
      border: 0;
      border-radius: 0;
      /* Legibility above any layer (price labels, candles, grid). */
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
    }
    /* Tap feedback only on :active — no idle plate. */
    .tr-overflow-btn:active {
      background: rgba(255, 255, 255, 0.08);
      border-radius: 6px;
    }
    /* Disable text selection on mobile chrome — accidental long-press on the
       ☰ glyph during a chart pan was selecting the character. Narrative text
       and chart-rendered content remain selectable (canvas isn't selectable
       anyway, narrative panels carry their own user-select rules if needed). */
    .top-right-bar,
    .tr-overflow-btn {
      user-select: none;
      -webkit-user-select: none;
    }
  }
</style>
