UI v4 - Єдине доповнення v2 (консолідоване)
SMC Terminal UI (Svelte 5 + Lightweight Charts)
Дата: 2026-02-20
Цей документ є єдиним SSOT-доповненням до базового ui_v4_master_guide / UI v4_v3
і замінює попередні Addendum v1/v2 як окремі файли.
Фокус: Drawings + WS контракти; виправлення DPR і RAF; Snap-to-OHLC, hotkeys;
чесний Undo/Redo (без temp id). Hit-testing/Selection engine - не входить у v2.
0. Канонічні одиниці та інваріанти
0.1 Канонічний час (SSOT)
• На wire і в доменних payload (zones/drawings/replay): t_ms (Unix milliseconds).
• У Lightweight Charts: time_sec = t_ms / 1000 (UTCTimestamp seconds).
• Заборонено міксувати ms і sec в одному полі. Завжди явно називати: *_ms або
*_sec.
0.2 Інваріанти (рейки)
• NoMix: Overlay і Drawings - окремі canvas-шари; DOM не рендерить графіку.
• Pointer-events rail: drawings-canvas перехоплює події лише коли активний tool;
інакше pointer-events: none.
• RAF rail (latest wins): рендер overlay/drawings колапсується до <= 1 раз/кадр.
• DPR rail: у ResizeObserver заборонено ctx.scale(dpr,dpr); дозволено лише
ctx.setTransform(dpr,0,0,dpr,0,0).
• Degraded-but-loud: деградації відображаються через meta.warnings[] у StatusBar.
0.3 Non-goals для v2
• Selection handles, drag-and-drop редагування, повний hit-testing - Addendum v3.
• Оптимістичний Undo/Redo з temp_id - заборонено. Якщо немає стабільного id,
Undo/Redo disabled + warning.

1. Структура файлів і ролі
• src/App.svelte - SSOT станів UI (symbol/tf, activeTool, WS, frame routing, hotkeys).
• src/layout/DrawingToolbar.svelte - вибір інструментів.
• src/layout/ChartPane.svelte - ініціалізація ChartEngine + OverlayRenderer +
DrawingsRenderer.
• src/chart/overlay.js - canvas overlay (RAF).
• src/chart/drawings.js - інтерактивні drawings (draft/snap/commit/confirm/undo/redo).
• src/layout/StatusBar.svelte - live/latency/OHLC + warnings.
• src/layout/ReplayBar.svelte - replay seek/step/play/pause.
• src/ws/actions.js - WS екшени.
2. DrawingToolbar і state routing
Інструменти: hline, trend, rect, eraser. activeTool є SSOT у App.svelte і проброситься у
ChartPane, далі у DrawingsRenderer.setTool(activeTool).
// src/layout/ChartPane.svelte (фрагмент)
let { renderFrame, actions, activeTool } = $props();
$effect(() => {
  if (drawingsRenderer) drawingsRenderer.setTool(activeTool);
});
3. WebSocket контракти
3.1 RenderFrame (server -> UI)
Мінімальний SSOT контракт: усі доменні часи - у мілісекундах (t_ms / *_ms).
{
}
  "type": "render_frame",
  "frame_type": "full",
  "symbol": "XAUUSD",
  "tf": "15m",
  "candles": [
    { "t_ms": 1700000000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 123 }
  ],
  "zones": [
    {
      "start_ms": 1700000000000,
      "end_ms": 1700000900000,
      "high": 2.1,
      "low": 1.7,
      "kind": "fvg"
    }
  ],
  "drawings": [
    {
      "id": "d_123",
      "type": "trend",
      "points": [
        { "t_ms": 1700000000000, "price": 1.0 },
        { "t_ms": 1700000300000, "price": 1.2 }
      ]
    }
  ],
  "replay": { "mode": "off", "pos_ms": 1700000000000, "playing": false },
  "meta": { "status": "live", "latency_ms": 120, "warnings": [] }
UI-конвертація для LWC candles: time = floor(t_ms/1000), open/high/low/close = o/h/l/c.
3.2 Actions (UI -> server)
// src/ws/actions.js (фрагмент)
export const createActions = (ws) => ({
  drawingAdd: (drawing) => ws.send({ action: 'drawing_add', drawing }),
  drawingUpdate: (drawing) => ws.send({ action: 'drawing_update', drawing }),
  drawingRemove: (id) => ws.send({ action: 'drawing_remove', id }),
  // Replay: ms + bars
  replaySeek: (to_ms) => ws.send({ action: 'replay_seek', to_ms }),
  replayStep: (delta_bars) => ws.send({ action: 'replay_step', delta_bars }),
});
4. OverlayRenderer: DPR + RAF + crosshair + range triggers
4.1 DPR sync (ResizeObserver)
// src/chart/overlay.js (фрагмент)
const dpr = window.devicePixelRatio || 1;
this.resizeObserver = new ResizeObserver((entries) => {
  const { width, height } = entries[0].contentRect;
  this.canvas.width = Math.floor(width *dpr);
  this.canvas.height = Math.floor(height* dpr);
  this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  this.scheduleRender();
});
4.2 RAF scheduler (latest wins) + triggers
scheduleRender() {
  if (this.rafId) return;
  this.rafId = requestAnimationFrame(() => {
    this.forceRender(); // використовує lastCrosshairParam і lastVisibleRange
    this.rafId = null;
  });
}
constructor(...) {
  this.chartApi.subscribeCrosshairMove((param) => {
    this.lastCrosshairParam = param;
    this.scheduleRender();
  });
  this.chartApi.timeScale().subscribeVisibleTimeRangeChange((range) => {
    this.lastVisibleRange = range;
    this.scheduleRender();
  });
}
4.3 Time mapping
// domain-time = ms, LWC-time = sec
const toX = (ms) => this.chartApi.timeScale().timeToCoordinate(ms / 1000);
5. DrawingsRenderer: інтерактивність + Snap-to-OHLC + rails
5.1 Pointer-events rail
setTool(tool) {
  this.activeTool = tool;
  this.canvas.style.pointerEvents = tool ? 'auto' : 'none';
}
5.2 Pixel <-> Time (ms)
// LWC coordinateToTime -> seconds (може бути null)
const timeSec = this.chartApi.timeScale().coordinateToTime(x);
if (timeSec === null) return;
// domain time -> ms
const t_ms = timeSec *1000;
// ms -> x
const x2 = this.chartApi.timeScale().timeToCoordinate(t_ms / 1000);
5.3 Snap-to-OHLC (магніт)
Rails: logical може бути 0 (валідно) -> перевірка тільки logical === null. Без sort (GC)
тільки min-scan.
_getSnappedPrice(x, y, rawPrice) {
  const logical = this.chartApi.timeScale().coordinateToLogical(x);
  if (logical === null) return rawPrice;
  const index = Math.floor(logical);
  const bar = this.seriesApi.dataByIndex(index);
  if (!bar) return rawPrice;
  const ps = this.seriesApi.priceScale();
  const yH = ps.priceToCoordinate(bar.high);
  const yL = ps.priceToCoordinate(bar.low);
  const yO = ps.priceToCoordinate(bar.open);
  const yC = ps.priceToCoordinate(bar.close);
  if (yH === null || yL === null || yO === null || yC === null) return rawPrice;
  const thresholdPx = this.magnetThresholdPx ?? 10;
  let bestPrice = rawPrice;
  let bestDist = Infinity;
  const dH = Math.abs(y - yH); if (dH < bestDist) { bestDist = dH; bestPrice = bar.high; }
  const dL = Math.abs(y - yL); if (dL < bestDist) { bestDist = dL; bestPrice = bar.low; }
  const dO = Math.abs(y - yO); if (dO < bestDist) { bestDist = dO; bestPrice = bar.open; }
}
  const dC = Math.abs(y - yC); if (dC < bestDist) { bestDist = dC; bestPrice = bar.close; }
  return bestDist <= thresholdPx ? bestPrice : rawPrice;
5.4 Draft/commit (скорочено, з ms)
// На mousedown
const timeSec = this.chartApi.timeScale().coordinateToTime(x);
const rawPrice = this.seriesApi.priceScale().coordinateToPrice(y);
if (timeSec === null || rawPrice === null) return;
const t_ms = timeSec* 1000;
const price = this._getSnappedPrice(x, y, rawPrice);
this.draft = {
  id: this._newId(),
  type: 'trend',
  points: [
    { t_ms, price },
    { t_ms, price },
  ],
};
// На mousemove: оновити points[1] (t_ms, price), scheduleRender (RAF).
// На mouseup:_commitDrawing(this.draft); draft=null; setTool(null).
5.5 Eraser (v2 coarse-hit)
Eraser у v2 дозволяє тільки coarse-hit для delete. Drag/selection handles - лише у v3.
6. Undo/Redo: чесна архітектура (без temp id)
6.1 Рішення v2: client-generated id (uuid)
UI генерує id при commit. Сервер приймає цей id як канонічний і повертає його в
frames. Якщо сервер не підтримує - Undo/Redo disabled + meta.warnings.
6.2 Мінімальний CommandStack
AddCommand(d)    -> undo: drawing_remove(d.id)
DeleteCommand(s) -> undo: drawing_add(s)
UpdateCommand(p) -> undo: drawing_update(p)
7. Keyboard shortcuts
T/H/R/E, Esc, Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z або Ctrl+Y. Ігнор:
input/textarea/select/contenteditable.
8. Warnings у StatusBar
badge WARN: N + tooltip зі списком. Це частина degraded-but-loud.
9. ReplayBar: чіткий протокол
Seek - абсолютний to_ms. Step - відносний delta_bars. Play/Pause - окремі екшени
(опційно).
10. Reconnect semantics (серверне правило)
На WS onopen клієнт нічого не запитує. Сервер сам пушить frame_type=full для
останнього (symbol, tf) цієї сесії.
11. Exit gates (DoD для v2)
• DPR: 10 ресайзів без зміни товщини ліній/координат.
• RAF: overlay/drawings рендеряться <= 1 раз/кадр на crosshair move і на range
change.
• Snap: точне прилипання до OHLC; при невідомих координатах - snap off + warning.
• Hotkeys: не ламають введення/браузерні дії.
• Undo/Redo: або real (client-id SSOT), або disabled + warning (без фейку)
