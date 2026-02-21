<!-- src/chart/ChartPane.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';

  import type { SmcData, UiWarning } from '../types';
  import { OverlayRenderer } from './overlay/OverlayRenderer';
  import { metaStore } from '../stores/meta'; // має мати addUiWarning()

  export let smcData: SmcData | null = null;

  let rootEl: HTMLDivElement;
  let overlayCanvas: HTMLCanvasElement;

  let chart: IChartApi;
  let series: ISeriesApi<'Candlestick'>;
  let overlay: OverlayRenderer;

  let ro: ResizeObserver | null = null;

  function emitUiWarning(w: UiWarning): void {
    metaStore.addUiWarning(w);
  }

  onMount(() => {
    chart = createChart(rootEl, {
      width: 0,
      height: 0,
      // ваші опції…
    });

    series = chart.addCandlestickSeries();

    overlay = new OverlayRenderer(overlayCanvas, chart, series, {
      onUiWarning: emitUiWarning,
      warningThrottleMs: 1000,
    });

    // ResizeObserver: DPR rail через overlay.resize()
    ro = new ResizeObserver(() => {
      const r = rootEl.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      overlay.resize(Math.round(r.width), Math.round(r.height), dpr);
    });
    ro.observe(rootEl);

    // Crosshair safety: не припускаємо, що seriesData має close
    chart.subscribeCrosshairMove((param) => {
      // metaStore може відображати cursor price/time; тут тільки безпечне читання
      const v = param?.seriesData?.get(series as any) as any;
      const close = v?.close;
      if (typeof close === 'number') {
        metaStore.setCursorPrice(close);
      } else {
        metaStore.setCursorPrice(null);
      }
    });
  });

  $: overlay?.patch(smcData);

  onDestroy(() => {
    ro?.disconnect();
    ro = null;
    chart?.remove();
  });
</script>

<div class="chart-wrap" bind:this={rootEl}>
  <canvas class="overlay" bind:this={overlayCanvas}></canvas>
</div>

<style>
  .chart-wrap {
    position: relative;
    width: 100%;
    height: 100%;
  }

  /* Rail: overlay не перехоплює wheel/drag LWC */
  .overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
</style>