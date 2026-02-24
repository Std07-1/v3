<script lang="ts">
  import type { ActiveTool } from "../types";

  const {
    activeTool,
    onSelectTool,
    magnetEnabled = false,
    onToggleMagnet,
  }: {
    activeTool: ActiveTool;
    onSelectTool: (tool: ActiveTool) => void;
    magnetEnabled?: boolean;
    onToggleMagnet?: () => void;
  } = $props();

  function loadCollapsed(): boolean {
    try {
      return localStorage.getItem("v4_toolbar_collapsed") === "1";
    } catch {
      return false;
    }
  }
  let collapsed = $state(loadCollapsed());
  function toggleCollapsed() {
    collapsed = !collapsed;
    try {
      localStorage.setItem("v4_toolbar_collapsed", collapsed ? "1" : "0");
    } catch {}
  }

  const tools: {
    id: ActiveTool;
    icon: string;
    title: string;
    hotkey: string;
  }[] = [
    { id: "hline", icon: "━", title: "Горизонтальна лінія", hotkey: "H" },
    { id: "trend", icon: "╱", title: "Трендова лінія", hotkey: "T" },
    { id: "rect", icon: "▭", title: "Прямокутник", hotkey: "R" },
    { id: "eraser", icon: "✕", title: "Видалити", hotkey: "E" },
  ];
</script>

<div class="drawing-toolbar" class:collapsed>
  <button
    class="collapse-btn"
    onclick={() => toggleCollapsed()}
    title={collapsed ? "Розгорнути" : "Згорнути"}
    type="button"
  >
    {collapsed ? "›" : "‹"}
  </button>

  {#if !collapsed}
    <div class="tools-group">
      {#each tools as tool}
        <button
          class="tool-btn"
          class:active={activeTool === tool.id}
          onclick={() => onSelectTool(activeTool === tool.id ? null : tool.id)}
          title={`${tool.title} [${tool.hotkey}]`}
          type="button"
        >
          {tool.icon}
        </button>
      {/each}
    </div>
  {/if}

  <!-- DEFERRED: magnet UI disabled — snap logic preserved, needs debugging (drawing_tools_v1)
  {#if !collapsed}
    <div class="tool-separator"></div>
    <button
      class="tool-btn magnet-btn"
      class:active={magnetEnabled}
      onclick={() => onToggleMagnet?.()}
      title={`Magnet: ${magnetEnabled ? "ON" : "OFF"} [G]`}
      type="button"
    >
      🧲
    </button>
  {/if}
  -->
</div>

<style>
  /* ADR-0007: Glass-like toolbar з CSS custom properties */
  .drawing-toolbar {
    position: absolute;
    left: 0;
    top: 80px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    padding: 4px 3px;
    background: var(--toolbar-bg, rgba(19, 23, 34, 0.6));
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--toolbar-border, rgba(255, 255, 255, 0.1));
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    width: 28px;
    z-index: 50;
    transition:
      width 0.2s ease,
      padding 0.2s ease;
    pointer-events: auto;
  }

  .drawing-toolbar.collapsed {
    width: 16px;
    padding: 4px 1px;
  }

  .collapse-btn {
    background: none;
    border: none;
    color: var(--toolbar-btn-color, #c8cdd6);
    opacity: 0.45;
    cursor: pointer;
    font-size: 10px;
    line-height: 1;
    padding: 2px 0;
    width: 100%;
    transition: opacity 0.15s;
  }

  .collapse-btn:hover {
    opacity: 0.85;
  }

  .tools-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 2px 0;
  }

  .tool-btn {
    background: none;
    border: none;
    color: var(--toolbar-btn-color, #c8cdd6);
    opacity: 0.6;
    cursor: pointer;
    width: 22px;
    height: 22px;
    border-radius: 4px;
    transition: all 0.15s ease;
    font-size: 11px;
    line-height: 22px;
    text-align: center;
    padding: 0;
  }

  .tool-btn:hover {
    opacity: 1;
    background: var(--toolbar-hover-bg, rgba(255, 255, 255, 0.08));
    box-shadow: 0 0 4px var(--toolbar-hover-bg, rgba(255, 255, 255, 0.08));
  }

  .tool-btn.active {
    color: var(--toolbar-active-color, #3d9aff);
    opacity: 1;
    background: rgba(61, 154, 255, 0.15);
    box-shadow: 0 0 8px rgba(61, 154, 255, 0.25);
  }

  .tool-separator {
    height: 1px;
    width: 14px;
    background: var(--toolbar-border, rgba(255, 255, 255, 0.08));
    margin: 2px auto;
  }

  .magnet-btn {
    font-size: 10px;
  }
</style>
