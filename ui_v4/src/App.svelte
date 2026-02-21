<script lang="ts">
  import { onMount } from 'svelte';
  import { WSConnection } from './ws/connection';
  import { createActions } from './ws/actions';

  import ChartPane from './layout/ChartPane.svelte';
  import DrawingToolbar from './layout/DrawingToolbar.svelte';

  import type { RenderFrame, ActiveTool, WsAction, T_MS, UiWarning } from './types';

  let ws: WSConnection | null = null;
  let actions: ReturnType<typeof createActions> | null = null;

  let currentFrame: RenderFrame | null = null;
  let activeTool: ActiveTool = null;

  let uiWarnings: UiWarning[] = [];
  let chartPaneRef: any = null;

  function sendRawAction(a: WsAction) {
    ws?.sendAction(a); // припускаємо, що WSConnection має sendAction(WsAction)
  }

  function scrollback(ms: T_MS) {
    actions?.scrollback(ms);
  }

  function addUiWarning(w: UiWarning) {
    uiWarnings = [w, ...uiWarnings].slice(0, 50);
  }

  onMount(() => {
    ws = new WSConnection('wss://api.example.com/ws', (frame) => { currentFrame = frame; });
    ws.connect();
    actions = createActions(ws);
    return () => ws?.close();
  });

  function handleKeydown(e: KeyboardEvent) {
    const t = (e.target as HTMLElement);
    const tag = t?.tagName?.toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;

    let handled = false;
    switch (e.key.toLowerCase()) {
      case 't': activeTool = activeTool === 'trend' ? null : 'trend'; handled = true; break;
      case 'h': activeTool = activeTool === 'hline' ? null : 'hline'; handled = true; break;
      case 'r': activeTool = activeTool === 'rect' ? null : 'rect'; handled = true; break;
      case 'e': activeTool = activeTool === 'eraser' ? null : 'eraser'; handled = true; break;
      case 'escape': activeTool = null; chartPaneRef?.cancelDraft?.(); handled = true; break;
      case 'z':
        if (e.ctrlKey || e.metaKey) { e.shiftKey ? chartPaneRef?.redo?.() : chartPaneRef?.undo?.(); handled = true; }
        break;
      case 'y':
        if (e.ctrlKey || e.metaKey) { chartPaneRef?.redo?.(); handled = true; }
        break;
    }
    if (handled) e.preventDefault();
  }
</script>

<svelte:window on:keydown={handleKeydown} />

<main class="app-layout">
  <div class="main-content">
    <DrawingToolbar {activeTool} onSelectTool={(t) => (activeTool = t)} />
    <div class="chart-wrapper">
      <ChartPane
        bind:this={chartPaneRef}
        {currentFrame}
        {activeTool}
        {sendRawAction}
        {scrollback}
        {addUiWarning}
      />
    </div>
  </div>
</main>