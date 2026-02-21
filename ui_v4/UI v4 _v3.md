Master Guide: Інтеграція UI v4 (Svelte 5

+ LWC)
Цей документ є єдиним і повним джерелом істини для побудови браузерного рендер-шару
UI v4. Він об'єднує архітектуру Svelte 5 (Thin Client) та високопродуктивний Canvas-рендер
(Game Engine logic).

1. Створення проекту та залежності
Виконайте ці команди у терміналі, щоб створити чистий проект:

# Створюємо проект Svelte (виберіть Skeleton project, TypeScript - ні)

npm create vite@latest ui_v4 -- --template svelte
cd ui_v4

# Встановлюємо ЄДИНУ зовнішню залежність

npm install lightweight-charts

# Встановлюємо стандартні пакети

npm install
Створіть наступну структуру в папці src/:
src/
├── ws/
│   ├── connection.js
│   └── actions.js
├── chart/
│   ├── lwc.js
│   ├── overlay.js
│   └── drawings.js
├── layout/
│   ├── TopBar.svelte
│   ├── ChartPane.svelte
│   ├── ReplayBar.svelte
│   └── StatusBar.svelte
├── App.svelte
└── main.js
2. Мережевий шар (WebSocket)
Цей шар відповідає виключно за транспорт. Жодної логіки обробки свічок.
src/ws/connection.js
export class WSConnection {
  constructor(url, onMessage) {
    this.url = url;
    this.onMessage = onMessage;
    this.ws = null;
    this.reconnectMs = 200;
    this.maxReconnectMs = 5000;
  }

  connect() {
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => { 
      console.log('[WS] Connected'); 
      this.reconnectMs = 200; // Reset backoff 
    }; 
 
    this.ws.onmessage = ({ data }) => { 
      // Сервер надсилає RenderFrame. Розпаковуємо і передаємо в 
Svelte.
      this.onMessage(JSON.parse(data));  
    };

    this.ws.onclose = () => { 
      console.warn(`[WS] Disconnected. Reconnecting in 
${this.reconnectMs}ms`);
      setTimeout(() => this.connect(), this.reconnectMs);
      this.reconnectMs = Math.min(this.reconnectMs * 1.5,
this.maxReconnectMs);
    };

    this.ws.onerror = (err) => console.error('[WS] Error:', err); 
  }

  send(payload) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }
}

src/ws/actions.js
export const createActions = (ws) => ({
  // Core
  switch:         (symbol, tf_s)   => ws.send({ action: 'switch',
symbol, tf_s }),
  scrollback:     (to_open_ms)     => ws.send({ action: 'scrollback',
to_open_ms }),
  overlayToggle:  (layer, visible) => ws.send({ action:
'overlay_toggle', layer, visible }),

  // Drawings
  drawingAdd:     (drawing)        => ws.send({ action: 'drawing_add',
drawing }),
  drawingUpdate:  (id, drawing)    => ws.send({ action:
'drawing_update', id, drawing }),
  drawingRemove:  (id)             => ws.send({ action:
'drawing_remove', id }),

  // Replay
  replayPlay:     ()               => ws.send({ action: 'replay_play'
}),
  replayPause:    ()               => ws.send({ action: 'replay_pause'
}),
  replaySeek:     (to_ms)          => ws.send({ action: 'replay_seek',
to_ms }),
  replaySpeed:    (mult)           => ws.send({ action:
'replay_speed', multiplier: mult }),
  replayExit:     ()               => ws.send({ action: 'replay_exit'
}),
});

1. Ядро Рендеру (Графіки та Canvas)
Тут знаходиться Vanilla JS логіка, яка взаємодіє з DOM. Вона відокремлена від Svelte для
максимальної швидкодії.
src/chart/lwc.js
Обгортка над Lightweight Charts v5. Керує тільки свічками.
import { createChart } from 'lightweight-charts';

export class ChartEngine {
  constructor(container, onScrollback) {
    this.chart = createChart(container, {
      layout: { background: { color: 'transparent' }, textColor:
'#c8cdd6' },
      grid: { vertLines: { color: '#252930' }, horzLines: { color:
'#252930' } },
      crosshair: { mode: 0 },
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    this.series = this.chart.addCandlestickSeries({ 
      upColor: '#26a69a', downColor: '#ef5350', 
      borderVisible: false, wickUpColor: '#26a69a', wickDownColor: 
'#ef5350',
    });

    // Логіка Scrollback (Infinity Scroll вліво) 
    let debounceTimer; 
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((range) 
=> {
      if (range && range.from < 10) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          const barsInfo = this.series.barsInLogicalRange(range);
          if (barsInfo && barsInfo.barsBefore < 5) {
            // Отримуємо timestamp найстарішої свічки
            const oldestMs = this.series.dataByIndex(0)?.time * 1000;
            if (oldestMs) onScrollback(oldestMs);
          }
        }, 150);
      }
    });
  }

  setData(bars) { this.series.setData(bars); }
  update(bar) { this.series.update(bar); }
  prependData(bars) {  
    // Зливаємо нові історичні бари з поточними
    const currentData = this.series.data();
    this.series.setData([...bars, ...currentData]);  
  }
}

src/chart/overlay.js
Критичний компонент продуктивності. Малює SMC (зони, боси) поверх свічок.
Використовує Dirty Scheduler (RAF).
export class OverlayRenderer {
  constructor(canvas, chartEngine) {
    this.canvas = canvas;
    this.chartApi = chartEngine.chart;
    this.seriesApi = chartEngine.series;

    // alpha: true, бо канвас лежить ПОВЕРХ графіка LWC 
    this.ctx = canvas.getContext('2d');  
     
    this.rafId = null; 
    this._frame = null; // Поточні дані SMC 
    this.dpr = window.devicePixelRatio || 1; 
 
    // Синхронізація розміру канвасу з контейнером 
    this.resizeObserver = new ResizeObserver(entries => { 
      const { width, height } = entries[0].contentRect; 
      this.canvas.width = width * this.dpr; 
      this.canvas.height = height * this.dpr; 
      this.ctx.scale(this.dpr, this.dpr); 
      this.scheduleRender(); 
    }); 
    this.resizeObserver.observe(this.canvas.parentElement); 
 
    // Тригер перемальовування при скролі/зумі 
    this.chartApi.timeScale().subscribeVisibleTimeRangeChange(() => 
this.scheduleRender());
  }

  // Отримуємо нові дані з WebSocket
  patch(overlays) {
    if (!overlays) return;  
    this._frame = overlays;
    this.scheduleRender();
  }

  // Dirty Scheduler: уникаємо зайвих перемальовувань
  scheduleRender() {
    if (this.rafId) return;
    this.rafId = requestAnimationFrame(() => {
      this.forceRender();
      this.rafId = null;
    });
  }

  forceRender() {
    if (!this._frame) return;
    const width = this.canvas.width / this.dpr;
    const height = this.canvas.height / this.dpr;

    // Очищуємо попередній кадр 
    this.ctx.clearRect(0, 0, width, height); 
 
    // Функції-конвертери (Час/Ціна -> Пікселі) 
    const toX = (ms) => this.chartApi.timeScale().timeToCoordinate(ms 
/ 1000);
    const toY = (price) =>
this.seriesApi.priceScale().priceToCoordinate(price);

    // 1. Малюємо SMC Zones (FVG, OB) 
    this._frame.zones?.forEach(z => { 
      const x1 = toX(z.start_ms); 
      const x2 = toX(z.end_ms); 
      const y1 = toY(z.high); 
      const y2 = toY(z.low); 
       
      if (x1 === null || y1 === null) return; 
       
      this.ctx.fillStyle = z.type === 'ob' ? 'rgba(61, 154, 255, 0.1)' 
: 'rgba(245, 158, 11, 0.1)';
      const w = (x2 || width) - x1; // Якщо зона не завершена, малюємо
до краю екрану
      const h = Math.abs(y2 - y1);

      this.ctx.fillRect(x1, Math.min(y1, y2), w, h); 
    }); 
 
    // 2. Малюємо Market Structure (Swings: BOS, CHoCH) 
    this.ctx.font = '10px Inter, sans-serif'; 
    this.ctx.textAlign = 'center'; 
     
    this._frame.swings?.forEach(s => { 
      const x = toX(s.time_ms); 
      const y = toY(s.price); 
      if (x === null || y === null) return; 
 
      // Текст (HH, LL) 
      this.ctx.fillStyle = '#c8cdd6'; 
      this.ctx.fillText(s.label, x, s.is_high ? y - 8 : y + 16); 
       
      // Лінія пробою (BOS) 
      if (s.end_ms) { 
        const xEnd = toX(s.end_ms); 
        this.ctx.beginPath(); 
        this.ctx.setLineDash([4, 4]); 
        this.ctx.strokeStyle = '#5a6070'; 
        this.ctx.moveTo(x, y); 
        this.ctx.lineTo(xEnd || width, y); 
        this.ctx.stroke(); 
        this.ctx.setLineDash([]); 
      } 
    }); 
 
    // 3. Малюємо Levels (Ліквідність) 
    this._frame.levels?.forEach(l => { 
      const y = toY(l.price); 
      if (y === null) return; 
       
      this.ctx.strokeStyle = l.color || '#ef5350'; 
      this.ctx.lineWidth = 1; 
      this.ctx.beginPath(); 
      this.ctx.moveTo(0, y); 
      this.ctx.lineTo(width, y); 
      this.ctx.stroke(); 
    }); 
  }
}

src/chart/drawings.js
Керує малюнками трейдера. Знаходиться на найвищому шарі Canvas.
export class DrawingsRenderer {
  constructor(canvas, chartEngine, actions) {
    this.canvas = canvas;
    this.chartApi = chartEngine.chart;
    this.seriesApi = chartEngine.series;
    this.actions = actions; // Зв'язок з WS
    this.ctx = canvas.getContext('2d');
    this.drawings = [];

    // Тут у майбутньому реалізується mousedown, mousemove для 
малювання
    // Перетворення координат:
    // price = this.seriesApi.priceScale().coordinateToPrice(y)
    // time = this.chartApi.timeScale().coordinateToTime(x)
  }

  setAll(drawings) {
    this.drawings = drawings || [];
    this.render();
  }

  confirm(drawing) {
    const idx = this.drawings.findIndex(d => d.id === drawing.id);
    if (idx >= 0) this.drawings[idx] = drawing;
    else this.drawings.push(drawing);
    this.render();
  }

  render() {
    // Логіка рендеру H-Line, Trend Line з масиву this.drawings...
  }
}

1. UI Шар (Svelte 5 Компоненти)
src/layout/TopBar.svelte

<script>
  let { onSwitch } = $props();
  let symbols = ['XAU/USD', 'EUR/USD', 'BTC/USDT', 'EUSTX50'];
  let tfs = ['M1', 'M5', 'M15', 'H1', 'D1'];

  let activeSym = $state(symbols[0]);
  let activeTf = $state(tfs[0]);

  function handleSwitch(sym, tf) {
    activeSym = sym;
    activeTf = tf;
    if (onSwitch) onSwitch(sym, tf);
  }
</script>

<header class="topbar">
  <div class="symbol-selector">
    {#each symbols as sym}
      <button class:active={activeSym === sym} onclick={() =>
handleSwitch(sym, activeTf)}>{sym}</button>
    {/each}
  </div>
  <div class="tf-selector">
    {#each tfs as tf}
      <button class:active={activeTf === tf} onclick={() =>
handleSwitch(activeSym, tf)}>{tf}</button>
    {/each}
  </div>
</header>

<style>
  .topbar { height: 40px; display: flex; align-items: center; padding:
0 16px; background: var(--bg-panel); gap: 24px; border-bottom: 1px
solid var(--border); }
  .symbol-selector, .tf-selector { display: flex; gap: 8px; }
  button { background: none; border: none; color: var(--text-dim);
cursor: pointer; font-size: 12px; font-weight: bold; padding: 4px 8px;
border-radius: 4px; transition: 0.2s; }
  button:hover { color: var(--text); background: var(--bg-hover); }
  button.active { color: var(--accent); background: rgba(61, 154, 255,
0.1); }
</style>

src/layout/ChartPane.svelte
Головний контейнер-сендвіч. Монтує всі три шари (DOM + Canvas + Canvas) і розкидає їм
дані.
<script>
  import { onMount, onDestroy } from 'svelte';
  import { ChartEngine } from '../chart/lwc.js';
  import { OverlayRenderer } from '../chart/overlay.js';
  import { DrawingsRenderer } from '../chart/drawings.js';

  let { renderFrame, actions, onCrosshairPrice } = $props();

  let containerRef;
  let overlayCanvasRef;
  let drawingsCanvasRef;

  let chartEngine;
  let overlayRenderer;
  let drawingsRenderer;

  onMount(() => {
    // 1. Ініціалізуємо рушії
    chartEngine = new ChartEngine(containerRef, (ms) =>
actions?.scrollback(ms));
    overlayRenderer = new OverlayRenderer(overlayCanvasRef,
chartEngine);
    drawingsRenderer = new DrawingsRenderer(drawingsCanvasRef,
chartEngine, actions);

    // 2. Слухаємо кросхейр для виводу ціни в StatusBar
    chartEngine.chart.subscribeCrosshairMove(p => {
      if (p.seriesData.size > 0 && onCrosshairPrice) {
        onCrosshairPrice(p.seriesData.values().next().value?.close);
      }
    });
  });

  // $effect викликається щоразу, коли змінюється renderFrame
  $effect(() => {
    if (!renderFrame || !chartEngine) return;

    // Роутинг даних згідно з Frame Type
    switch (renderFrame.frame_type) {
      case 'full':
        chartEngine.setData(renderFrame.bars);
        overlayRenderer.patch(renderFrame.overlays);
        drawingsRenderer.setAll(renderFrame.drawings);
        break;
      case 'delta':
      case 'replay':
        chartEngine.update(renderFrame.bars[0]);
        overlayRenderer.patch(renderFrame.overlays);
        break;
      case 'scrollback':
        chartEngine.prependData(renderFrame.bars);
        overlayRenderer.patch(renderFrame.overlays);
        break;
      case 'drawing_ack':
        drawingsRenderer.confirm(renderFrame.drawings[0]);
        break;
    }
  });

  onDestroy(() => {
    if (chartEngine) chartEngine.chart.remove();
  });
</script>

<div class="chart-container">
  <!-- Шар 1: Lightweight Charts (Свічки) -->
  <div bind:this={containerRef} class="layer lwc-layer"></div>

  <!-- Шар 2: Overlay (SMC). Прозорий для миші -->
  <canvas bind:this={overlayCanvasRef} class="layer
overlay-layer"></canvas>

  <!-- Шар 3: Drawings (Малюнки трейдера). Змінює pointer-events 
динамічно -->
  <canvas bind:this={drawingsCanvasRef} class="layer
drawings-layer"></canvas>
</div>

<style>
  .chart-container { position: relative; width: 100%; height: 100%;
overflow: hidden; background: var(--bg); }
  .layer { position: absolute; inset: 0; }
  .overlay-layer { pointer-events: none; z-index: 10; }
  .drawings-layer { pointer-events: none; z-index: 20; } /* Стає
'auto' через JS, коли вибрано інструмент малювання */
</style>

src/layout/ReplayBar.svelte
<script>
  let { replayState, actions } = $props();
</script>

{#if replayState?.active}
  <div class="replay-bar">
    <div class="badge">▶ REPLAY</div>

    <div class="controls"> 
      <button onclick={() => actions?.replaySeek(-50)}>◀◀</button> 
      <button class="play-btn" onclick={() => replayState.playing ? 
actions?.replayPause() : actions?.replayPlay()}>
        {replayState.playing ? '
⏸
' : '▶'}
      </button>
    </div>

    <div class="scrubber" onclick={(e) => { 
      // Тут логіка конвертації кліку в timestamp 
      // actions?.replaySeek(calculated_ms); 
    }}> 
      <div class="line"></div> 
      <div class="thumb" style="left: 50%;">●</div> 
    </div> 
 
    <button onclick={() => actions?.replaySpeed(2)}>2x</button> 
    <div class="time-label">14:32 D1</div> 
     
    <button class="exit-btn" onclick={() => actions?.replayExit()}>✕ 
Exit</button>
  </div>
{/if}

<style>
  .replay-bar { height: 36px; background: var(--bg-panel); border-top:
1px solid var(--border); display: flex; align-items: center; padding:
0 16px; gap: 16px; }
  .badge { color: var(--replay); font-weight: bold; font-size: 12px; }
  .controls button, .exit-btn { background: none; border: none;
cursor: pointer; font-size: 12px; font-weight: bold; }
  .controls button { color: var(--text); }
  .play-btn { color: var(--accent) !important; font-size: 14px
!important; }
  .exit-btn { color: var(--bear); }
  .scrubber { flex: 1; position: relative; display: flex; align-items:
center; cursor: pointer; height: 100%; }
  .scrubber .line { width: 100%; height: 2px; background:
var(--border); }
  .scrubber .thumb { position: absolute; color: var(--accent);
font-size: 16px; transform: translateX(-50%); }
  .time-label { font-family: monospace; font-size: 12px; color:
var(--text-dim); }
</style>

src/layout/StatusBar.svelte
<script>
  let { meta, liveCandle, hoverPrice } = $props();
</script>

<footer class="statusbar">
  <div class="left-section">
    <div class="status">
      <span class="indicator" class:live={meta.status ===
'live'}></span>
      {meta.status.toUpperCase()}
    </div>

    {#if liveCandle} 
      <div class="ohlc"> 
        O: <span class="val">{liveCandle.open.toFixed(2)}</span> 
        H: <span class="val">{liveCandle.high.toFixed(2)}</span> 
        L: <span class="val">{liveCandle.low.toFixed(2)}</span> 
        C: <span class="val">{liveCandle.close.toFixed(2)}</span> 
      </div> 
    {/if} 
  </div>

  <div class="right-section">
    {#if hoverPrice}
      <div class="crosshair-price">CURSOR:
{hoverPrice.toFixed(2)}</div>
    {/if}
    <div class="latency">{meta.latency_ms || 0}ms WS</div>
  </div>
</footer>

<style>
  .statusbar { height: 28px; display: flex; align-items: center;
justify-content: space-between; padding: 0 16px; background:
var(--bg-panel); font-size: 11px; font-family: monospace; color:
var(--text-dim); border-top: 1px solid var(--border); }
  .left-section, .right-section { display: flex; align-items: center;
gap: 16px; }
  .status { display: flex; align-items: center; gap: 6px; font-weight:
bold; }
  .indicator { width: 6px; height: 6px; border-radius: 50%;
background: var(--bear); }
  .indicator.live { background: var(--bull); box-shadow: 0 0 5px
var(--bull); }
  .ohlc { display: flex; gap: 8px; }
  .ohlc .val { color: var(--text); }
  .crosshair-price { color: var(--accent); }
</style>

src/App.svelte
Кореневий компонент. Встановлює WS з'єднання, містить глобальний стан та CSS змінні.
<script>
  import { onMount } from 'svelte';
  import { WSConnection } from './ws/connection.js';
  import { createActions } from './ws/actions.js';
  import TopBar from './layout/TopBar.svelte';
  import ChartPane from './layout/ChartPane.svelte';
  import ReplayBar from './layout/ReplayBar.svelte';
  import StatusBar from './layout/StatusBar.svelte';

  // State (Svelte 5 Runes)
  let ws = $state(null);
  let actions = $state(null);

  let currentFrame = $state(null);
  let metaInfo = $state({ latency_ms: 0, status: 'connecting',
warnings: [] });
  let replayInfo = $state({ active: false });
  let liveCandle = $state(null);
  let crosshairPrice = $state(null);

  onMount(() => {
    // Підключення до серверу (замініть URL на ваш з ui_config.json)
    const wsUrl =
'wss://[api.yoursite.com/ws?token=SESSION_TOKEN](https://api.yoursite.
com/ws?token=SESSION_TOKEN)';

    ws = new WSConnection(wsUrl, handleWSFrame);
    ws.connect();
    actions = createActions(ws);
  });

  // Головний роутер для RenderFrame
  function handleWSFrame(frame) {
    // 1. Оновлюємо графіки
    if (['full', 'delta', 'scrollback', 'drawing_ack',
'replay'].includes(frame.frame_type)) {
      currentFrame = frame;
    }

    // 2. Оновлюємо UI Метадані
    switch (frame.frame_type) {
      case 'full':
        metaInfo = { ...metaInfo, status: 'live', ...frame.meta };
        if (frame.replay) replayInfo = frame.replay;
        if (frame.bars?.length) liveCandle =
frame.bars[frame.bars.length - 1];
        break;

      case 'delta':
      case 'replay':
        if (frame.bars?.length) liveCandle =
frame.bars[frame.bars.length - 1];
        if (frame.frame_type === 'replay' && frame.meta?.replay) {
           replayInfo = frame.meta.replay;
        }
        break;

      case 'heartbeat':
        metaInfo.latency_ms = frame.meta?.latency_ms ||
metaInfo.latency_ms;
        break;

      case 'warming':
        metaInfo.status = `warming (${frame.meta?.ready_pct || 0}%)`;
        break;
    }
  }
</script>

<main class="app-layout">
  <TopBar onSwitch={(sym, tf) => actions?.switch(sym, tf)} />

  <div class="chart-wrapper">
    <ChartPane  
      renderFrame={currentFrame}
      {actions}
      onCrosshairPrice={(price) => crosshairPrice = price}
    />
  </div>

  <ReplayBar  
    replayState={replayInfo}  
    {actions}  
  />

  <StatusBar  
    meta={metaInfo}  
    {liveCandle}
    hoverPrice={crosshairPrice}
  />
</main>

<!-- Глобальні стилі додатку -->
<style>
  :global(:root) {
    --bg: #0d0f12;
    --bg-panel: #13161b;
    --bg-hover: #1c2028;
    --border: #252930;
    --text: #c8cdd6;
    --text-dim: #5a6070;
    --accent: #3d9aff;
    --bull: #26a69a;
    --bear: #ef5350;
    --warn: #f59e0b;
    --replay: #a855f7;
  }

  :global(*) { box-sizing: border-box; }

  :global(body) {  
    margin: 0; padding: 0;  
    font-family: 'Inter', system-ui, -apple-system, sans-serif;  
    background: var(--bg);  
    color: var(--text);  
    overflow: hidden;  
  }

  .app-layout { display: flex; flex-direction: column; height: 100vh;
}
  .chart-wrapper { flex: 1; position: relative; }
</style>

1. Запуск та тестування
Коли всі файли збережені у папці src, виконайте:
npm run dev
Оскільки бекенд зараз не підключений, графік буде чорним, а статус — "CONNECTING".
Щоб побачити графік в дії без бекенду, ви можете викликати функцію імітації (mock)
всередині onMount в App.svelte, відправивши об'єкт full з тестовими барами у функцію
handleWSFrame(mockFrame).
Головні переваги цієї архітектури:
1. Нуль Business Logic в UI: Svelte тільки розбирає RenderFrame і передає його у
Vanilla-класи.
1. Нуль Garbage Collection фрізів: У циклах рендеру Canvas (overlay.js) немає
функцій map, filter, slice.
1. Бандл: Якщо ви запустите npm run build, ви побачите, що весь JS важить менше 70
KB.
