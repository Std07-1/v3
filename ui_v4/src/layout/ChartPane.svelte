<!-- src/layout/ChartPane.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { RenderFrame, ActiveTool, WsAction, T_MS, UiWarning, SmcData } from '../types';

  import { ChartEngine } from '../chart/lwc';
  import { OverlayRenderer } from '../chart/overlay/OverlayRenderer';
  import { DrawingsRenderer } from '../chart/drawings/DrawingsRenderer';

  export let currentFrame: RenderFrame | null = null;
  export let activeTool: ActiveTool = null;

  export let sendRawAction: (a: WsAction) => void;
  export let scrollback: (ms: T_MS) => void;
  export let addUiWarning: (w: UiWarning) => void;

  let wrapperRef: HTMLDivElement;          // interactionEl (capture)
  let lwcHostRef: HTMLDivElement;          // LWC mount
  let overlayCanvasRef: HTMLCanvasElement;
  let drawingsCanvasRef: HTMLCanvasElement;

  let chartEngine: ChartEngine;
  let overlayRenderer: OverlayRenderer;
  let drawingsRenderer: DrawingsRenderer;

  let ro: ResizeObserver | null = null;

  export function undo() { drawingsRenderer?.commandStack.undo(); }
  export function redo() { drawingsRenderer?.commandStack.redo(); }
  export function cancelDraft() { drawingsRenderer?.cancelDraft(); }

  function buildSmc(frame: RenderFrame): SmcData {
    return {
      zones: frame.zones ?? [],
      swings: frame.swings ?? [],
      levels: frame.levels ?? [],
    };
  }

  onMount(() => {
    chartEngine = new ChartEngine(lwcHostRef, () => {}, (ms) => scrollback?.(ms));
    overlayRenderer = new OverlayRenderer(overlayCanvasRef, chartEngine.chart, chartEngine.series, {
      onUiWarning: addUiWarning,
      warningThrottleMs: 1000,
    });

    drawingsRenderer = new DrawingsRenderer(
      drawingsCanvasRef,
      wrapperRef,
      chartEngine.chart,
      chartEngine.series,
      sendRawAction,
      addUiWarning,
    );

    // ResizeObserver для OverlayRenderer (DrawingsRenderer має власний)
    ro = new ResizeObserver(() => {
      const r = wrapperRef.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      overlayRenderer.resize(Math.round(r.width), Math.round(r.height), dpr);
    });
    ro.observe(wrapperRef);
  });

  $: drawingsRenderer?.setTool(activeTool);

  $: if (currentFrame && chartEngine) {
    // Candles
    if (currentFrame.frame_type === 'full' || currentFrame.frame_type === 'replay') {
      chartEngine.setData(currentFrame.candles ?? []);
    } else if (currentFrame.frame_type === 'delta') {
      const bars = currentFrame.candles ?? [];
      for (const b of bars) chartEngine.update(b);
    } else if (currentFrame.frame_type === 'scrollback') {
      chartEngine.prependData(currentFrame.candles ?? []);
    }

    // Overlays
    overlayRenderer?.patch(buildSmc(currentFrame));

    // Drawings
    if (currentFrame.frame_type === 'full') {
      drawingsRenderer?.setAll(currentFrame.drawings ?? []);
    } else if (currentFrame.frame_type === 'drawing_ack') {
      const d = currentFrame.drawings?.[0];
      if (d) drawingsRenderer?.confirm(d);
    }
  }

  onDestroy(() => {
    ro?.disconnect();
    ro = null;

    drawingsRenderer?.destroy();
    chartEngine?.destroy?.();
  });
</script>

<div class="chart-container" bind:this={wrapperRef}>
  <div class="layer lwc-layer" bind:this={lwcHostRef}></div>
  <canvas class="layer overlay-layer" bind:this={overlayCanvasRef}></canvas>
  <canvas class="layer drawings-layer" bind:this={drawingsCanvasRef}></canvas>
</div>

<style>
  .chart-container { position: relative; width: 100%; height: 100%; overflow: hidden; }
  .layer { position: absolute; inset: 0; }
  .lwc-layer { z-index: 1; }
  .overlay-layer { pointer-events: none; z-index: 10; }
  .drawings-layer { pointer-events: none; z-index: 20; } /* SSOT: завжди none */
</style>
