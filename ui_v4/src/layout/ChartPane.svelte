<!-- src/layout/ChartPane.svelte -->
<script lang="ts">
  import { onMount, onDestroy, untrack } from "svelte";
  import type {
    RenderFrame,
    WsAction,
    T_MS,
    UiWarning,
    SmcData,
    ActiveTool,
  } from "../types";

  import {
    ChartEngine,
    TF_TO_S,
    type CrosshairData,
    type ThemeName,
    type CandleStyleName,
  } from "../chart/lwc";
  import { setupPriceScaleInteractions } from "../chart/interaction";
  import { OverlayRenderer } from "../chart/overlay/OverlayRenderer";
  import type { DisplayMode } from "../chart/overlay/DisplayBudget";
  import { DrawingsRenderer } from "../chart/drawings/DrawingsRenderer";
  import OhlcvTooltip from "./OhlcvTooltip.svelte";
  // NarrativePanel moved to ChartHud inline (ADR-0033)
  // BiasBanner moved to ChartHud (ADR-0031: inline after star)
  import { saveViewSnapshot, loadViewSnapshot } from "../stores/viewCache";
  import {
    applySmcFull,
    applySmcDelta,
    EMPTY_SMC_DATA,
  } from "../stores/smcStore";
  import { replayStore } from "../stores/replayStore.svelte";

  const {
    currentFrame = null,
    sendRawAction,
    scrollback,
    addUiWarning,
    brightness = 1.0,
    activeTool = null,
    magnetEnabled = false,
  }: {
    currentFrame?: RenderFrame | null;
    sendRawAction: (a: WsAction) => void;
    scrollback: (ms: T_MS) => void;
    addUiWarning: (w: UiWarning) => void;
    brightness?: number;
    activeTool?: ActiveTool;
    magnetEnabled?: boolean;
  } = $props();

  // ADR-0024: локальний стан SMC overlay (оновлюється інкрементально)
  let smcData: SmcData = $state(EMPTY_SMC_DATA);

  // N3: SMC layer toggles (localStorage-backed)
  // ADR-0024c: toggles → OverlayRenderer per-layer flags.
  // Дані (smcData) завжди повні. Toggle одного шару не чіпає інші.
  let showOB = $state(true);
  let showFVG = $state(true);
  let showSW = $state(true);
  let showLVL = $state(true);
  let showBOS = $state(true);
  let showFR = $state(true);
  let showDIS = $state(true);
  let smcPanelOpen = $state(false);
  // ADR-0028 Φ0: Focus/Research display mode
  let displayMode: DisplayMode = $state("focus");
  // Crosshair data for tooltip (OHLCV + cursor position)
  let crosshairData: CrosshairData | null = $state(null);

  let wrapperRef: HTMLDivElement;
  let lwcHostRef: HTMLDivElement;
  let overlayCanvasRef: HTMLCanvasElement;
  let drawingsCanvasRef: HTMLCanvasElement;

  let chartEngine: ChartEngine;
  let overlayRenderer: OverlayRenderer;
  let drawingsRenderer: DrawingsRenderer;
  let interactionCleanup: (() => void) | null = null;

  let ro: ResizeObserver | null = null;

  // P3.6: Container dimensions for cursor-following tooltip
  let containerW = $state(0);
  let containerH = $state(0);

  // P3.15: Scrollback state indicator
  let scrollbackState: "idle" | "loading" | "wall" = $state("idle");

  // Entry 077: Left edge visible — for wall indicator
  let leftEdgeVisible = $state(false);

  // P1: "No data" state — якщо full frame повертає 0 candles
  let showNoData = $state(false);
  let noDataSymbol = $state("");
  let noDataTf = $state("");

  // P3.9: Track previous symbol/tf for viewCache save on switch
  let prevSymbol = "";
  let prevTf = "";

  // ADR-0027: Track current candles for replay enter + last rendered index
  let _currentCandles: import("../types").Candle[] = [];
  let _lastReplayIdx = 0;

  export function undo() {
    drawingsRenderer?.commandStack.undo();
  }
  export function redo() {
    drawingsRenderer?.commandStack.redo();
  }
  export function cancelDraft() {
    drawingsRenderer?.cancelDraft();
  }

  // P3.11/P3.12: Delegators for theme & candle style (expose to parent)
  export function applyTheme(name: ThemeName): void {
    chartEngine?.applyTheme(name);
    overlayRenderer?.setLightTheme(name === "light");
    // ADR-0007: CSS vars вже встановлені в App.svelte → кешуємо для canvas через rAF
    requestAnimationFrame(() => drawingsRenderer?.refreshThemeColors());
  }
  export function applyCandleStyle(name: CandleStyleName): void {
    chartEngine?.applyCandleStyle(name);
  }

  // ADR-0027: Enter/exit replay mode
  export function enterReplay(): void {
    if (_currentCandles.length === 0) return;
    replayStore.enter(_currentCandles, smcData);
    // НЕ ставимо _lastReplayIdx тут — cursor $effect зробить initial render
  }
  export function exitReplay(): void {
    replayStore.exit();
    _lastReplayIdx = 0;
    // Restore full chart data — trigger fresh full frame
    if (_currentCandles.length > 0 && chartEngine) {
      chartEngine.setData(_currentCandles);
    }
  }

  onMount(() => {
    // N3: restore SMC toggles from localStorage (R-03: safe in onMount)
    try {
      const raw = localStorage.getItem("smc_toggles");
      if (raw) {
        const t = JSON.parse(raw);
        if (typeof t.ob === "boolean") showOB = t.ob;
        if (typeof t.fvg === "boolean") showFVG = t.fvg;
        if (typeof t.sw === "boolean") showSW = t.sw;
        if (typeof t.lvl === "boolean") showLVL = t.lvl;
        if (typeof t.bos === "boolean") showBOS = t.bos;
        if (typeof t.fr === "boolean") showFR = t.fr;
        if (typeof t.dis === "boolean") showDIS = t.dis;
        if (typeof t.smcOpen === "boolean") smcPanelOpen = t.smcOpen;
        if (t.displayMode === "focus" || t.displayMode === "research")
          displayMode = t.displayMode;
      }
    } catch {
      /* corrupt → use defaults */
    }

    chartEngine = new ChartEngine(
      lwcHostRef,
      (data: CrosshairData) => {
        crosshairData = data;
      },
      (ms) => scrollback?.(ms),
    );

    // P3.15: Scrollback state feedback
    chartEngine.onScrollbackState((state) => {
      scrollbackState = state;
    });

    // Entry 077: Left edge visibility for "no more history" indicator
    chartEngine.onLeftEdgeChange((vis) => {
      leftEdgeVisible = vis;
    });

    overlayRenderer = new OverlayRenderer(
      overlayCanvasRef,
      chartEngine.chart,
      chartEngine.series,
      {
        onUiWarning: addUiWarning,
        warningThrottleMs: 1000,
      },
    );

    drawingsRenderer = new DrawingsRenderer(
      drawingsCanvasRef,
      wrapperRef,
      chartEngine.chart,
      chartEngine.series,
      () => {}, // noop: drawings client-only (ADR-0005)
      addUiWarning,
    );

    // Expose purge method for console debugging: window.__purgeDrawings()
    (window as any).__purgeDrawings = () =>
      drawingsRenderer?.purgeAllDrawings();

    // P3.3-P3.5: Price axis interactions (Y-zoom, Y-pan, dblclick reset)
    // ADR-0008: callback для sync-рендерингу малювань при Y-axis змінах
    interactionCleanup = setupPriceScaleInteractions(
      lwcHostRef,
      chartEngine.chart,
      chartEngine.series,
      () => {
        drawingsRenderer?.notifyPriceRangeChanged();
        overlayRenderer?.notifyPriceRangeChanged();
      },
    );

    ro = new ResizeObserver(() => {
      const r = wrapperRef.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      overlayRenderer.resize(Math.round(r.width), Math.round(r.height), dpr);
      // Entry 078 §6: no auto-scroll on resize (user controls scroll position)
      containerW = Math.round(r.width);
      containerH = Math.round(r.height);
    });
    ro.observe(wrapperRef);
  });

  $effect(() => {
    drawingsRenderer?.setTool(activeTool);
  });
  $effect(() => {
    drawingsRenderer?.setMagnetEnabled(magnetEnabled ?? false);
  });

  $effect(() => {
    if (currentFrame && chartEngine) {
      // ═══ ADR-0027: Replay mode intercept ═══
      // untrack: не створювати deps на replayStore.$state щоб уникнути
      // нескінченного циклу effect → write $state → re-trigger effect.
      if (untrack(() => replayStore.active)) {
        if (
          currentFrame.frame_type === "full" ||
          currentFrame.frame_type === "replay"
        ) {
          // TF switch during replay — update replay data with new candles
          const tfLabel = currentFrame.tf ?? "M5";
          const tfS = TF_TO_S[tfLabel] ?? 300;
          chartEngine.setTfS(tfS);
          overlayRenderer?.setViewerTfS(tfS);

          const candles = currentFrame.candles ?? [];
          _currentCandles = candles;
          const newSmc = applySmcFull(
            currentFrame.zones,
            currentFrame.swings,
            currentFrame.levels,
            currentFrame.trend_bias,
            currentFrame.zone_grades,
            currentFrame.bias_map,
            currentFrame.momentum_map,
          );
          // untrack: запис до replayStore без створення підписки
          untrack(() => replayStore.updateDataForNewTf(candles, newSmc));

          prevSymbol = currentFrame.symbol ?? "";
          prevTf = currentFrame.tf ?? "";

          // Drawings support during replay
          if (currentFrame.frame_type === "full") {
            const sym = currentFrame.symbol ?? "";
            const tf = currentFrame.tf ?? "";
            if (sym && tf) drawingsRenderer?.setStorageKey(sym, tf);
            drawingsRenderer?.setAll(currentFrame.drawings ?? []);
          }
          // Chart + SMC update буде зроблено cursor $effect (tracks cursorIndex)
        }
        // Skip delta/scrollback during replay (R1: heartbeat/config still pass through frameRouter)
        return;
      }

      // ═══ Normal mode (existing logic) ═══
      if (
        currentFrame.frame_type === "full" ||
        currentFrame.frame_type === "replay"
      ) {
        // Set TF for D1 offset (V3 parity: chart_adapter_lite.js:1005-1009)
        const tfLabel = currentFrame.tf ?? "M5";
        const tfS = TF_TO_S[tfLabel] ?? 300;
        chartEngine.setTfS(tfS);
        overlayRenderer?.setViewerTfS(tfS);

        // P3.3-P3.5: Reset manual price scale on full frame switch
        if ((lwcHostRef as any).__resetManualPriceScale) {
          (lwcHostRef as any).__resetManualPriceScale();
        }

        // ADR-0032 P4: Save center_ms + bars_visible before switching
        if (prevSymbol && prevTf && chartEngine && _currentCandles.length > 0) {
          const logRange = chartEngine.chart
            .timeScale()
            .getVisibleLogicalRange();
          if (logRange) {
            const centerIdx = Math.round(
              ((logRange.from as number) + (logRange.to as number)) / 2,
            );
            const clamped = Math.max(
              0,
              Math.min(_currentCandles.length - 1, centerIdx),
            );
            saveViewSnapshot(prevSymbol, prevTf, {
              center_ms: _currentCandles[clamped].t_ms,
              bars_visible: (logRange.to as number) - (logRange.from as number),
            });
          }
        }

        const candles = currentFrame.candles ?? [];
        _currentCandles = candles; // ADR-0027: track for replay enter
        if (candles.length === 0) {
          chartEngine.clearAll();
          showNoData = true;
          noDataSymbol = currentFrame.symbol ?? "";
          noDataTf = currentFrame.tf ?? "";
          console.warn(
            "[ChartPane] NO_DATA: full frame has 0 candles",
            currentFrame.symbol,
            currentFrame.tf,
          );
        } else {
          chartEngine.setData(candles);
          showNoData = false;

          // ADR-0032 P4: Restore center-based view for this symbol+tf
          const sym = currentFrame.symbol ?? "";
          const tf = currentFrame.tf ?? "";
          const snapshot = loadViewSnapshot(sym, tf);
          if (snapshot && candles.length > 0) {
            // Binary search for nearest candle to center_ms
            let lo = 0,
              hi = candles.length - 1,
              best = 0;
            while (lo <= hi) {
              const mid = (lo + hi) >>> 1;
              if (
                Math.abs(candles[mid].t_ms - snapshot.center_ms) <
                Math.abs(candles[best].t_ms - snapshot.center_ms)
              ) {
                best = mid;
              }
              if (candles[mid].t_ms < snapshot.center_ms) lo = mid + 1;
              else hi = mid - 1;
            }
            const half = Math.max(10, snapshot.bars_visible / 2);
            chartEngine.chart.timeScale().setVisibleLogicalRange({
              from: best - half,
              to: best + half,
            });
          } else {
            // No snapshot (first visit) — show latest bars
            chartEngine.chart.timeScale().scrollToRealTime();
          }
        }

        // P3.9: Update prev tracking
        prevSymbol = currentFrame.symbol ?? "";
        prevTf = currentFrame.tf ?? "";
      } else if (currentFrame.frame_type === "delta") {
        const bars = currentFrame.candles ?? [];
        for (const b of bars) chartEngine.update(b);
        if (bars.length > 0) showNoData = false;
      } else if (currentFrame.frame_type === "scrollback") {
        chartEngine.prependData(currentFrame.candles ?? []);
      }

      // ADR-0024 §6.2: оновлюємо smcData залежно від типу кадру
      if (
        currentFrame.frame_type === "full" ||
        currentFrame.frame_type === "replay"
      ) {
        smcData = applySmcFull(
          currentFrame.zones,
          currentFrame.swings,
          currentFrame.levels,
          currentFrame.trend_bias,
          currentFrame.zone_grades,
          currentFrame.bias_map,
          currentFrame.momentum_map,
        );
      } else if (currentFrame.frame_type === "delta") {
        if (currentFrame.smc_delta) {
          // FIX: untrack(smcData) — інакше ефект читає smcData (залежність)
          // І тут же пише smcData → Svelte перезапускає ефект → нескінченний цикл
          // → effect_update_depth_exceeded. untrack розриває цей цикл.
          smcData = applySmcDelta(
            untrack(() => smcData),
            currentFrame.smc_delta,
          );
        }
      }

      if (currentFrame.frame_type === "full") {
        // Switch drawing storage to this symbol+TF pair
        const sym = currentFrame.symbol ?? "";
        const tf = currentFrame.tf ?? "";
        if (sym && tf) drawingsRenderer?.setStorageKey(sym, tf);
        drawingsRenderer?.setAll(currentFrame.drawings ?? []);
      } else if (currentFrame.frame_type === "drawing_ack") {
        const d = currentFrame.drawings?.[0];
        if (d) drawingsRenderer?.confirm(d);
      }
    }
  });

  // ADR-0024c: data effect — тільки при зміні smcData (повне, без фільтрації)
  $effect(() => {
    overlayRenderer?.patch(smcData);
  });

  // ═══ ADR-0027: Replay cursor effect ═══
  // Deps: replayStore.active + replayStore.cursorIndex (тільки ці два).
  // Data access (allCandles, visibleCandles, visibleSmcData) в untrack
  // щоб зміна allCandles/allSmcData не перезапускала цей ефект.
  $effect(() => {
    if (!replayStore.active || !chartEngine) return;
    const idx = replayStore.cursorIndex;
    if (idx === _lastReplayIdx) return;

    untrack(() => {
      if (idx === _lastReplayIdx + 1 && idx > 0) {
        // Forward step by 1 — efficient incremental update
        const c = replayStore.allCandles[idx - 1];
        if (c) {
          chartEngine.update(c);
          showNoData = false;
        }
      } else {
        // Scrub / backward / large jump — full setData
        const visible = replayStore.visibleCandles;
        if (visible.length === 0) {
          chartEngine.clearAll();
          showNoData = true;
        } else {
          chartEngine.setData(visible);
          showNoData = false;
        }
      }

      _lastReplayIdx = idx;
      // Update SMC overlay for current cursor position
      smcData = replayStore.visibleSmcData;
    });
  });

  // ADR-0024c §layer isolation: toggle effects — кожен шар незалежний.
  // Toggle зон не чіпає рівні і навпаки. Zero frame rebuild.
  $effect(() => {
    overlayRenderer?.setZoneKindVisible("ob", showOB);
  });
  $effect(() => {
    overlayRenderer?.setZoneKindVisible("fvg", showFVG);
  });
  $effect(() => {
    overlayRenderer?.setLayerVisible("swings", showSW);
  });
  $effect(() => {
    overlayRenderer?.setLayerVisible("levels", showLVL);
  });
  $effect(() => {
    overlayRenderer?.setLayerVisible("structure", showBOS);
  });
  $effect(() => {
    overlayRenderer?.setLayerVisible("fractals", showFR);
  });
  $effect(() => {
    overlayRenderer?.setLayerVisible("displacement", showDIS);
  });
  // ADR-0028 Φ0: display mode effect
  $effect(() => {
    overlayRenderer?.setDisplayMode(displayMode);
  });

  // N3: persist toggles to localStorage
  $effect(() => {
    try {
      localStorage.setItem(
        "smc_toggles",
        JSON.stringify({
          ob: showOB,
          fvg: showFVG,
          sw: showSW,
          lvl: showLVL,
          bos: showBOS,
          fr: showFR,
          dis: showDIS,
          displayMode,
          smcOpen: smcPanelOpen,
        }),
      );
    } catch {
      /* quota / private mode */
    }
  });

  // ADR-0028 Φ0: keyboard shortcut ‘F’ toggles Focus/Research
  function handleDisplayModeKey(e: KeyboardEvent) {
    // Skip if focus inside input/textarea/contentEditable
    const t = e.target as HTMLElement | null;
    if (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
    )
      return;
    if (e.key === "f" || e.key === "F") {
      displayMode = displayMode === "focus" ? "research" : "focus";
    }
  }
  onMount(() => {
    window.addEventListener("keydown", handleDisplayModeKey);
  });
  onDestroy(() => {
    window.removeEventListener("keydown", handleDisplayModeKey);
  });

  onDestroy(() => {
    interactionCleanup?.();
    interactionCleanup = null;
    ro?.disconnect();
    ro = null;
    overlayRenderer?.destroy();
    drawingsRenderer?.destroy();
    chartEngine?.destroy?.();
  });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="chart-container"
  bind:this={wrapperRef}
  onclick={() => {
    if (smcPanelOpen) smcPanelOpen = false;
  }}
>
  <div
    class="layer lwc-layer"
    bind:this={lwcHostRef}
    style:filter={brightness !== 1 ? `brightness(${brightness})` : undefined}
  ></div>
  <OhlcvTooltip data={crosshairData} />
  <canvas class="layer overlay-layer" bind:this={overlayCanvasRef}></canvas>
  <canvas
    class="layer drawings-layer"
    bind:this={drawingsCanvasRef}
    style:filter={brightness !== 1 ? `brightness(${brightness})` : undefined}
  ></canvas>
  {#if showNoData}
    <div class="no-data-overlay">
      <div class="no-data-content">
        <span class="no-data-icon">📊</span>
        <span class="no-data-text">No data</span>
        <span class="no-data-detail">{noDataSymbol} · {noDataTf}</span>
      </div>
    </div>
  {/if}
  <!-- P3.15: Scrollback state indicator -->
  {#if scrollbackState === "loading"}
    <div class="scrollback-indicator loading">
      <span class="scrollback-spinner"></span> Loading history…
    </div>
  {:else if scrollbackState === "wall" && leftEdgeVisible}
    <div class="scrollback-indicator wall">No more history available</div>
  {/if}
  <!-- N3: SMC layer toggles — per-kind colour coding -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="smc-panel" onclick={(e) => e.stopPropagation()}>
    {#if smcPanelOpen}
      <div class="smc-grid">
        <button
          class="smc-toggle smc-t-ob"
          class:active={showOB}
          onclick={() => (showOB = !showOB)}
          title="Order Blocks">OB</button
        >
        <button
          class="smc-toggle smc-t-fvg"
          class:active={showFVG}
          onclick={() => (showFVG = !showFVG)}
          title="Fair Value Gaps">FVG</button
        >
        <button
          class="smc-toggle smc-t-sw"
          class:active={showSW}
          onclick={() => (showSW = !showSW)}
          title="Swings">SW</button
        >
        <button
          class="smc-toggle smc-t-lvl"
          class:active={showLVL}
          onclick={() => (showLVL = !showLVL)}
          title="Levels">LVL</button
        >
        <button
          class="smc-toggle smc-t-bos"
          class:active={showBOS}
          onclick={() => (showBOS = !showBOS)}
          title="BOS / CHoCH">BOS</button
        >
        <button
          class="smc-toggle smc-t-fr"
          class:active={showFR}
          onclick={() => (showFR = !showFR)}
          title="Williams Fractals">FR</button
        >
        <button
          class="smc-toggle smc-t-dis"
          class:active={showDIS}
          onclick={() => (showDIS = !showDIS)}
          title="Displacement">DIS</button
        >
      </div>
    {/if}
    <!-- ADR-0028 Φ0: Focus / Research display mode toggle (key: F) -->
    <button
      class="smc-trigger"
      class:open={smcPanelOpen}
      onclick={() => (smcPanelOpen = !smcPanelOpen)}
      onwheel={(e) => {
        e.preventDefault();
        const allOn =
          showOB &&
          showFVG &&
          showSW &&
          showLVL &&
          showBOS &&
          showFR &&
          showDIS;
        const next = !allOn;
        showOB = next;
        showFVG = next;
        showSW = next;
        showLVL = next;
        showBOS = next;
        showFR = next;
        showDIS = next;
      }}
      title="Toggle SMC controls (scroll: all on/off)"
      >{smcPanelOpen ? "✕" : "SMC"}</button
    >
    <button
      class="smc-toggle smc-t-mode"
      class:research={displayMode === "research"}
      onclick={() =>
        (displayMode = displayMode === "focus" ? "research" : "focus")}
      title={displayMode === "focus"
        ? "Focus mode (F to toggle)"
        : "Research mode (F to toggle)"}
      >{displayMode === "focus" ? "F" : "R"}</button
    >
  </div>
</div>

<style>
  .chart-container {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }
  .layer {
    position: absolute;
    inset: 0;
  }
  .lwc-layer {
    z-index: 1;
  }
  .overlay-layer {
    pointer-events: none;
    z-index: 10;
  }
  .drawings-layer {
    pointer-events: none;
    z-index: 20;
  } /* SSOT: завжди none */
  .no-data-overlay {
    position: absolute;
    inset: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(19, 23, 34, 0.75);
    pointer-events: none;
  }
  .no-data-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }
  .no-data-icon {
    font-size: 32px;
    opacity: 0.6;
  }
  .no-data-text {
    font-size: 18px;
    font-weight: 600;
    color: #8b8f9a;
  }
  .no-data-detail {
    font-size: 12px;
    color: #5d6068;
  }

  /* P3.15: Scrollback state indicators */
  .scrollback-indicator {
    position: absolute;
    top: 50%;
    left: 12px;
    transform: translateY(-50%);
    z-index: 25;
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 6px;
    pointer-events: none;
    backdrop-filter: blur(8px);
  }
  .scrollback-indicator.loading {
    background: rgba(74, 144, 217, 0.18);
    color: #4a90d9;
    border: 1px solid rgba(74, 144, 217, 0.25);
  }
  .scrollback-indicator.wall {
    background: rgba(120, 123, 134, 0.15);
    color: #787b86;
    border: 1px solid rgba(120, 123, 134, 0.2);
  }
  .scrollback-spinner {
    display: inline-block;
    width: 10px;
    height: 10px;
    border: 2px solid rgba(74, 144, 217, 0.3);
    border-top-color: #4a90d9;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 4px;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* N3: SMC layer toggles — refined collapsible panel */
  .smc-panel {
    position: absolute;
    top: 36px;
    right: 64px;
    z-index: 36;
    display: flex;
    align-items: center;
    gap: 3px;
    pointer-events: auto;
  }
  .smc-trigger {
    font-size: 9px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid transparent;
    background: none;
    color: #7b8ba8;
    cursor: pointer;
    transition: all 0.18s ease;
    line-height: 1.4;
    letter-spacing: 0.5px;
    white-space: nowrap;
    min-width: 28px;
    text-align: center;
  }
  .smc-trigger:hover,
  .smc-trigger.open {
    color: #c0d0e4;
    border-color: rgba(74, 144, 217, 0.45);
    background: rgba(40, 48, 65, 0.75);
  }
  .smc-trigger.open {
    color: #ef5350;
    border-color: rgba(239, 83, 80, 0.3);
    background: rgba(239, 83, 80, 0.08);
  }
  .smc-grid {
    display: flex;
    gap: 2px;
    animation: fadeSlideRight 0.15s ease-out;
  }
  @keyframes fadeSlideRight {
    from {
      opacity: 0;
      transform: translateX(6px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }
  .smc-toggle {
    font-size: 9px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid transparent;
    background: none;
    color: #5d6068;
    cursor: pointer;
    transition: all 0.15s ease;
    line-height: 1.4;
    letter-spacing: 0.3px;
  }
  .smc-toggle:hover {
    background: rgba(50, 55, 70, 0.65);
    color: #a0a4b0;
    border-color: rgba(120, 123, 134, 0.2);
  }
  /* Per-kind active colours (matches overlay rendering palette) */
  .smc-toggle.active {
    color: #4a90d9;
    border-color: rgba(74, 144, 217, 0.3);
    background: rgba(74, 144, 217, 0.1);
  }
  .smc-toggle.active.smc-t-ob {
    color: #e67e22;
    border-color: rgba(230, 126, 34, 0.35);
    background: rgba(230, 126, 34, 0.1);
  }
  .smc-toggle.active.smc-t-fvg {
    color: #2ecc71;
    border-color: rgba(46, 204, 113, 0.35);
    background: rgba(46, 204, 113, 0.1);
  }
  .smc-toggle.active.smc-t-sw {
    color: #ef5350;
    border-color: rgba(239, 83, 80, 0.35);
    background: rgba(239, 83, 80, 0.1);
  }
  .smc-toggle.active.smc-t-lvl {
    color: #ff9800;
    border-color: rgba(255, 152, 0, 0.35);
    background: rgba(255, 152, 0, 0.1);
  }
  .smc-toggle.active.smc-t-bos {
    color: #ffa726;
    border-color: rgba(255, 167, 38, 0.35);
    background: rgba(255, 167, 38, 0.1);
  }
  .smc-toggle.active.smc-t-fr {
    color: #ab47bc;
    border-color: rgba(171, 71, 188, 0.35);
    background: rgba(171, 71, 188, 0.1);
  }
  .smc-toggle.active.smc-t-dis {
    color: #00e676;
    border-color: rgba(0, 230, 118, 0.35);
    background: rgba(0, 230, 118, 0.1);
  }
  /* ADR-0028 Φ0: Focus/Research mode toggle */
  .smc-toggle.smc-t-mode {
    color: #4a90d9;
    border-color: rgba(74, 144, 217, 0.3);
    background: none;
    min-width: 18px;
    text-align: center;
  }
  .smc-toggle.smc-t-mode.research {
    color: #ff9800;
    border-color: rgba(255, 152, 0, 0.35);
    background: none;
  }
</style>
