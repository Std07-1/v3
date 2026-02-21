<script lang="ts"> 
  import { onMount } from 'svelte'; 
  import { WSConnection } from './ws/connection'; 
  import { createActions } from './ws/actions'; 
  import type { RenderFrame } from './types'; 
 
  // --- STATE --- 
  let ws: WSConnection | null = $state(null); 
  let actions: ReturnType<typeof createActions> | null = $state(null); 
   
  let currentFrame: RenderFrame | null = $state(null); 
  let lastSeq = -1; 
   
  // RAIL: Явне розділення власників warnings 
  let serverWarnings: string[] = $state([]); 
  let uiWarnings: string[] = $state([]); 
  let allWarnings = $derived([...serverWarnings, ...uiWarnings]); 
   
  let metaStatus = $state({ status: 'connecting', latency_ms: 0, 
ready_pct: 0 }); 
 
  // --- FRAME ROUTER --- 
  function handleWSFrame(frame: RenderFrame) { 
    // 1. Seq Монотонність 
    if (frame.meta.seq <= lastSeq) { 
      const msg = `Stale frame dropped (seq: ${frame.meta.seq} <= 
${lastSeq})`; 
      console.warn(`[Router] ${msg}`); 
      uiWarnings = [...uiWarnings, msg]; 
      return; 
    } 
    lastSeq = frame.meta.seq; 
 
    // 2. Очищення при boot/switch 
    if (frame.frame_type === 'full') { 
      uiWarnings = []; 
    } 
 
    // 3. Збереження серверних warnings (SSOT) 
    serverWarnings = frame.meta.warnings || []; 
 
    // 4. Оновлення статусу 
    metaStatus = { 
      status: frame.meta.status || metaStatus.status, 
      latency_ms: frame.meta.latency_ms || metaStatus.latency_ms, 
      ready_pct: frame.meta.ready_pct || metaStatus.ready_pct, 
    }; 
 
// 5. Диспетчеризація кадру 
if (['full', 'delta', 'scrollback', 'drawing_ack', 
'replay'].includes(frame.frame_type)) { 
currentFrame = frame; 
} 
} 
onMount(() => { 
ws = new WSConnection('wss://api.example.com/ws', handleWSFrame); 
ws.connect(); 
actions = createActions(ws); 
return () => ws?.close(); 
}); 
</script> 
<main class="app-layout"> 
<!-- В наступному Slice тут з'явиться <StatusBar 
warnings={allWarnings} meta={metaStatus} /> --> 
</main> 