<!-- src/layout/ChartPane.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from "svelte";
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
  import { DrawingsRenderer } from "../chart/drawings/DrawingsRenderer";
  import OhlcvTooltip from "./OhlcvTooltip.svelte";
  import { saveVisibleRange, loadVisibleRange } from "../stores/viewCache";

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
    // ADR-0007: CSS vars вже встановлені в App.svelte → кешуємо для canvas через rAF
    requestAnimationFrame(() => drawingsRenderer?.refreshThemeColors());
  }
  export function applyCandleStyle(name: CandleStyleName): void {
    chartEngine?.applyCandleStyle(name);
  }

  function buildSmc(frame: RenderFrame): SmcData {
    return {
      zones: frame.zones ?? [],
      swings: frame.swings ?? [],
      levels: frame.levels ?? [],
    };
  }

  onMount(() => {
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

    // P3.3-P3.5: Price axis interactions (Y-zoom, Y-pan, dblclick reset)
    // ADR-0008: callback для sync-рендерингу малювань при Y-axis змінах
    interactionCleanup = setupPriceScaleInteractions(
      lwcHostRef,
      chartEngine.chart,
      chartEngine.series,
      () => drawingsRenderer?.notifyPriceRangeChanged(),
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
      if (
        currentFrame.frame_type === "full" ||
        currentFrame.frame_type === "replay"
      ) {
        // Set TF for D1 offset (V3 parity: chart_adapter_lite.js:1005-1009)
        const tfLabel = currentFrame.tf ?? "M5";
        const tfS = TF_TO_S[tfLabel] ?? 300;
        chartEngine.setTfS(tfS);

        // P3.3-P3.5: Reset manual price scale on full frame switch
        if ((lwcHostRef as any).__resetManualPriceScale) {
          (lwcHostRef as any).__resetManualPriceScale();
        }

        // P3.9: Save current visible range before switching
        if (prevSymbol && prevTf && chartEngine) {
          const range = chartEngine.chart.timeScale().getVisibleLogicalRange();
          saveVisibleRange(prevSymbol, prevTf, range);
        }

        const candles = currentFrame.candles ?? [];
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

          // P3.9: Restore cached visible range for this symbol+tf
          const sym = currentFrame.symbol ?? "";
          const tf = currentFrame.tf ?? "";
          const cached = loadVisibleRange(sym, tf);
          if (cached) {
            chartEngine.chart.timeScale().setVisibleLogicalRange(cached);
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

      overlayRenderer?.patch(buildSmc(currentFrame));

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

  onDestroy(() => {
    interactionCleanup?.();
    interactionCleanup = null;
    ro?.disconnect();
    ro = null;
    drawingsRenderer?.destroy();
    chartEngine?.destroy?.();
  });
</script>

<div class="chart-container" bind:this={wrapperRef}>
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
</style>
