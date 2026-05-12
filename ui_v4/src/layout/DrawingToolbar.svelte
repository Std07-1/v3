<!--
  src/layout/DrawingToolbar.svelte — ADR-0074 T3 (Drawing Tools V1 redesign).

  Зміни проти попередньої версії (164 LOC, unicode glyphs 22×22):
    - Inline SVG icons (Lucide-style stroke geometry, no new dependency)
    - Always-visible labels (Ukrainian), hotkey badge `<kbd>`
    - Button sizes CSS-var-driven (--drawing-toolbar-btn-size):
        desktop 32×32, mobile (pointer:coarse) 44×44 — WCAG 2.1 AA по факту
    - 5 buttons: cursor (no-tool mode) + hline + trend + rect + eraser
      Cursor — OQ2 resolution rev 1.1: explicit "no tool" state replaces
      implicit "click outside to deselect".
    - Layout:
        Desktop:        left-edge vertical column; expanded 140px з labels
                        OR collapsed 36px icon-only (user toggle persisted)
        Mobile portrait: bottom-LEFT vertical stack 44×44 icon-only;
                        position bottom: env(safe-area-inset-bottom) per
                        ADR-0071 PWA + ADR-0074 OQ3 resolution.
        Mobile landscape: top-left vertical icon-only 32×32 (compact).

  Hotkey display: registry hotkey як `<kbd>`. trend показує `\` per
  ADR-0074 §6, але keyboard binding ще `t` до Slice T5 (paralleled).
  Slice T5 уніфікує keyboard store → display й binding match.

  Magnet — DEFERRED до Slice T4 (окремий button + onToggleMagnet prop).
-->
<script lang="ts">
  import type { ActiveTool } from "../types";
  import { TOOL_REGISTRY } from "../chart/drawings/tools";

  const {
    activeTool,
    onSelectTool,
    magnetEnabled = false,
    onToggleMagnet,
  }: {
    activeTool: ActiveTool;
    onSelectTool: (tool: ActiveTool) => void;
    /** ADR-0074 T4: snap-to-OHLC magnet mode. Persisted у localStorage
     *  `v4_magnet_enabled` через App.svelte saveMagnet(). Active state visual
     *  matches drawing-tool buttons (gold accent + inset border). */
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

  // Inline Lucide-style SVG icons. ~24×24 viewBox, stroke=currentColor.
  // ~200-400 bytes each, ~1.5KB total raw — менше за lucide-svelte tree-shake.
  // Source: Lucide v0.460 (MIT) — mouse-pointer-2 / minus / trending-up / square / x.
  type ToolBtn = {
    id: ActiveTool;
    label: string;
    hotkey: string;
    iconPath: string; // inner SVG markup (paths/lines/etc)
  };

  const ICON_CURSOR =
    '<path d="M4 4l16.4 6.8a.5.5 0 0 1 .05.9L13.2 15.5 9.5 22.45a.5.5 0 0 1-.9-.05L4 4z"/>';
  const ICON_HLINE = '<path d="M3 12h18"/>';
  const ICON_TREND =
    '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>';
  const ICON_RECT = '<rect x="3" y="3" width="18" height="18" rx="2"/>';
  const ICON_ERASER =
    '<path d="M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21"/><path d="m5.082 11.09 8.828 8.828"/>';
  // Lucide "magnet" — U-shaped horseshoe magnet, intuitive snap metaphor.
  const ICON_MAGNET =
    '<path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/>';

  // Toolbar buttons — order matters (cursor first як "default", eraser last
  // як destructive). Hotkeys беруться з ToolModule.hotkey для tools що у
  // registry, hardcoded для cursor/eraser що НЕ Drawing entities.
  const buttons: ToolBtn[] = [
    {
      id: null,
      label: "Курсор",
      hotkey: "Esc",
      iconPath: ICON_CURSOR,
    },
    {
      id: "hline",
      label: "Горизонталь",
      hotkey: TOOL_REGISTRY.get("hline")?.hotkey?.toUpperCase() ?? "H",
      iconPath: ICON_HLINE,
    },
    {
      id: "trend",
      label: "Трендова",
      hotkey: TOOL_REGISTRY.get("trend")?.hotkey ?? "T",
      iconPath: ICON_TREND,
    },
    {
      id: "rect",
      label: "Прямокутник",
      hotkey: TOOL_REGISTRY.get("rect")?.hotkey?.toUpperCase() ?? "R",
      iconPath: ICON_RECT,
    },
    {
      id: "eraser",
      label: "Гумка",
      hotkey: "E",
      iconPath: ICON_ERASER,
    },
  ];

  function handleClick(id: ActiveTool) {
    // Toggle: re-click активного інструменту → деактивує (null).
    // Cursor button: id вже null → onSelectTool(null) у будь-якому випадку.
    if (id === null) {
      onSelectTool(null);
    } else {
      onSelectTool(activeTool === id ? null : id);
    }
  }

  // Cursor button "active" коли немає інструменту вибрано — explicit indicator.
  function isActive(id: ActiveTool): boolean {
    if (id === null) return activeTool === null;
    return activeTool === id;
  }
</script>

<div class="drawing-toolbar" class:collapsed>
  <button
    class="collapse-btn"
    onclick={toggleCollapsed}
    title={collapsed ? "Розгорнути" : "Згорнути"}
    type="button"
    aria-label={collapsed ? "Expand toolbar" : "Collapse toolbar"}
    aria-expanded={!collapsed}
  >
    {collapsed ? "›" : "‹"}
  </button>

  <div class="tools-group" role="toolbar" aria-label="Drawing tools">
    {#each buttons as btn (btn.id ?? "_cursor")}
      <button
        class="tool-btn"
        class:active={isActive(btn.id)}
        onclick={() => handleClick(btn.id)}
        title={`${btn.label} [${btn.hotkey}]`}
        type="button"
        aria-label={btn.label}
        aria-pressed={isActive(btn.id)}
      >
        <svg
          class="tool-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          {@html btn.iconPath}
        </svg>
        {#if !collapsed}
          <span class="tool-label">{btn.label}</span>
          <kbd class="tool-hotkey">{btn.hotkey}</kbd>
        {/if}
      </button>
    {/each}

    <!-- ADR-0074 T4: Magnet toggle. Modal toggle (НЕ drawing tool) —
         не входить у buttons[] iteration бо логіка active state інша
         (magnetEnabled bool, не activeTool match). Visual divider above
         виділяє modal-vs-drawing-tool boundary. -->
    {#if onToggleMagnet}
      <div class="tool-divider" aria-hidden="true"></div>
      <button
        class="tool-btn magnet-btn"
        class:active={magnetEnabled}
        onclick={onToggleMagnet}
        title={`Magnet (snap-to-OHLC): ${magnetEnabled ? "ON" : "OFF"} [G]`}
        type="button"
        aria-label="Magnet snap-to-OHLC"
        aria-pressed={magnetEnabled}
      >
        <svg
          class="tool-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          {@html ICON_MAGNET}
        </svg>
        {#if !collapsed}
          <span class="tool-label">Магніт</span>
          <kbd class="tool-hotkey">G</kbd>
        {/if}
      </button>
    {/if}
  </div>
</div>

<style>
  /* ADR-0074 T3: redesigned toolbar.
     CSS-var driven sizes (tokens.css --drawing-toolbar-*).
     Backdrop-filter blur для glass effect (preserved з ADR-0008). */
  .drawing-toolbar {
    position: absolute;
    left: 8px;
    top: 80px;
    z-index: 50;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: var(--drawing-toolbar-gap, 4px);
    padding: 6px 4px;
    background: var(--toolbar-bg, rgba(19, 23, 34, 0.6));
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--toolbar-border, rgba(255, 255, 255, 0.1));
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    pointer-events: auto;
    transition: width 0.2s ease;
  }
  /* Default desktop expanded width: icon + label + kbd fit. */
  .drawing-toolbar:not(.collapsed) {
    width: 156px;
  }
  .drawing-toolbar.collapsed {
    width: calc(var(--drawing-toolbar-btn-size, 32px) + 12px);
    padding: 6px 4px;
  }

  .collapse-btn {
    background: none;
    border: none;
    color: var(--toolbar-btn-color, #c8cdd6);
    opacity: 0.45;
    cursor: pointer;
    font-size: var(--t4-size);
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
    gap: var(--drawing-toolbar-gap, 4px);
  }

  /* ADR-0074 T4: visual separator між drawing tools (cursor..eraser) і
     modal toggles (magnet). 1px hairline matching toolbar border style. */
  .tool-divider {
    height: 1px;
    margin: 4px 4px;
    background: var(--toolbar-border, rgba(255, 255, 255, 0.1));
  }

  .tool-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--drawing-toolbar-label-offset, 8px);
    background: none;
    border: none;
    color: var(--toolbar-btn-color, #c8cdd6);
    opacity: 0.72;
    cursor: pointer;
    min-height: var(--drawing-toolbar-btn-size, 32px);
    padding: 4px 6px;
    border-radius: 6px;
    transition:
      background 0.12s ease,
      opacity 0.12s ease,
      color 0.12s ease;
    text-align: left;
    font-family: var(
      --font-sans,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      sans-serif
    );
  }
  .drawing-toolbar.collapsed .tool-btn {
    justify-content: center;
    padding: 0;
    min-width: var(--drawing-toolbar-btn-size, 32px);
    width: var(--drawing-toolbar-btn-size, 32px);
  }
  .tool-btn:hover {
    opacity: 1;
    background: var(--toolbar-hover-bg, rgba(255, 255, 255, 0.08));
  }
  .tool-btn:focus-visible {
    outline: 1px solid var(--accent, #d4a017);
    outline-offset: 1px;
  }
  .tool-btn.active {
    color: var(--toolbar-active-color, #d4a017);
    opacity: 1;
    background: color-mix(
      in srgb,
      var(--toolbar-active-color, #d4a017) 14%,
      transparent
    );
    box-shadow: inset 2px 0 0
      color-mix(in srgb, var(--toolbar-active-color, #d4a017) 70%, transparent);
  }

  .tool-icon {
    width: var(--drawing-toolbar-icon-size, 16px);
    height: var(--drawing-toolbar-icon-size, 16px);
    flex-shrink: 0;
    display: block;
  }
  .tool-label {
    flex: 1 1 auto;
    font-size: var(--t3-size, 12px);
    font-weight: 500;
    line-height: 1.2;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tool-hotkey {
    flex-shrink: 0;
    font-family: var(--font-mono, "SF Mono", "Consolas", monospace);
    font-size: var(--t6-size, 10px);
    font-weight: 500;
    line-height: 1;
    padding: 2px 5px;
    border-radius: 3px;
    background: rgba(255, 255, 255, 0.06);
    color: var(--text-3, #6b6b80);
    opacity: 0.8;
  }
  .tool-btn.active .tool-hotkey {
    background: color-mix(
      in srgb,
      var(--toolbar-active-color, #d4a017) 18%,
      transparent
    );
    color: var(--toolbar-active-color, #d4a017);
    opacity: 1;
  }

  /* ═══ Mobile landscape phone (max-height:500px landscape) ═══
     Compact icon-only column, top-left. Не залежить від collapse state —
     малий екран = forced collapse для chart real estate. */
  @media (orientation: landscape) and (max-height: 500px) {
    .drawing-toolbar {
      width: calc(var(--drawing-toolbar-btn-size, 32px) + 12px) !important;
      top: 4px;
      left: 4px;
      padding: 4px 4px;
    }
    .drawing-toolbar .tool-label,
    .drawing-toolbar .tool-hotkey,
    .collapse-btn {
      display: none;
    }
    .tool-btn {
      justify-content: center;
      padding: 0;
      min-width: var(--drawing-toolbar-btn-size, 32px);
      width: var(--drawing-toolbar-btn-size, 32px);
    }
  }

  /* ═══ Mobile portrait (≤640px portrait) ═══
     ADR-0074 §3 + OQ3 resolution: bottom-LEFT з safe-area inset.
     Залишає bottom-CENTER/RIGHT під future NarrativeSheet (ADR-0075+).
     Forced icon-only (44×44 — WCAG 2.1 AA target по факту з --drawing-*-px). */
  @media (max-width: 640px) and (orientation: portrait) {
    .drawing-toolbar {
      top: auto !important;
      bottom: calc(env(safe-area-inset-bottom, 0px) + 8px);
      left: calc(env(safe-area-inset-left, 0px) + 8px);
      width: calc(var(--drawing-toolbar-btn-size, 44px) + 12px) !important;
      padding: 6px 4px;
    }
    .drawing-toolbar .tool-label,
    .drawing-toolbar .tool-hotkey,
    .collapse-btn {
      display: none;
    }
    .tool-btn {
      justify-content: center;
      padding: 0;
      min-width: var(--drawing-toolbar-btn-size, 44px);
      width: var(--drawing-toolbar-btn-size, 44px);
    }
  }
</style>
