<script lang="ts">
  import type { ActiveTool } from '../types';

  export let activeTool: ActiveTool;
  export let onSelectTool: (tool: ActiveTool) => void;

  const tools: { id: ActiveTool; icon: string; title: string; hotkey: string }[] = [
    { id: 'hline', icon: '—', title: 'Horizontal Line', hotkey: 'H' },
    { id: 'trend', icon: '╱', title: 'Trend Line', hotkey: 'T' },
    { id: 'rect', icon: '▭', title: 'Rectangle', hotkey: 'R' },
    // Eraser в v2 може бути “заглушка”; повноцінно — у v3.
    { id: 'eraser', icon: '⌫', title: 'Eraser (v3)', hotkey: 'E' },
  ];
</script>

<div class="drawing-toolbar">
  {#each tools as tool}
    <button
      class="tool-btn"
      class:active={activeTool === tool.id}
      on:click={() => onSelectTool(activeTool === tool.id ? null : tool.id)}
      title={`${tool.title} [${tool.hotkey}]`}
      type="button"
    >
      {tool.icon}
    </button>
  {/each}
</div>

<style>
  .drawing-toolbar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    background: var(--bg-panel);
    border-right: 1px solid var(--border);
    width: 40px;
    z-index: 50;
  }

  .tool-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    height: 24px;
    border-radius: 4px;
    transition: 0.2s;
    font-weight: bold;
  }

  .tool-btn:hover {
    color: var(--text);
    background: var(--bg-hover);
  }

  .tool-btn.active {
    color: var(--accent);
    background: rgba(61, 154, 255, 0.1);
  }
</style>