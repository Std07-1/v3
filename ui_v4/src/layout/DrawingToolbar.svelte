<!--
  src/layout/DrawingToolbar.svelte — chart drawing tools.

  Redesign WIP (2026-07, supersedes ADR-0074 T3 fixed panel — new ADR pending):
    - Icon-only: no panel/background chrome, the icons float directly on the
      chart, snapped to the left wall in one-per-grid-cell (36px) rows.
    - Quiet "left-curtain": the strip is fully lit while the cursor is in the
      left band (≤ REACT_X) and fades to a click-through ghost past it, so it
      never occludes the chart when you are working elsewhere. An armed tool
      (or magnet) keeps it lit — you always see what will happen on next click.
    - Per-icon hover label: a tidy pill (name + hotkey) reveals to the right of
      each icon on hover — replaces the old always-on label row.
    - 6 icons: cursor (no-tool) · hline · trend · rect · eraser · magnet.
      Magnet is a modal toggle (magnetEnabled), not an ActiveTool.

  Mobile: still hidden (@media) pending the touch hit-test / draft-commit fixes
  the 2026-05-12 hide was created for — do NOT silently re-enable here.
-->
<script lang="ts">
  import type { ActiveTool } from "../types";
  import { TOOL_REGISTRY } from "../chart/drawings/tools";

  const {
    activeTool,
    onSelectTool,
    magnetEnabled = false,
    onToggleMagnet,
    alwaysShowHints = false,
  }: {
    activeTool: ActiveTool;
    onSelectTool: (tool: ActiveTool) => void;
    /** ADR-0074 T4: snap-to-OHLC magnet mode. Persisted у localStorage
     *  `v4_magnet_enabled` через App.svelte saveMagnet(). */
    magnetEnabled?: boolean;
    onToggleMagnet?: () => void;
    /** Menu toggle "показувати підказки": when true, hover labels always
     *  show (no polite suppression). Default false = polite timing. */
    alwaysShowHints?: boolean;
  } = $props();

  // Inline Lucide-style SVG icons. ~24×24 viewBox, stroke=currentColor.
  // Source: Lucide v0.460 (MIT) — mouse-pointer-2 / minus / trending-up /
  // square / eraser / magnet. Inlined (no dependency).
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

  // Order matters — cursor first ("default"), eraser last (destructive).
  // Hotkeys come from ToolModule.hotkey for registry tools; hardcoded for
  // cursor/eraser which are not Drawing entities.
  const buttons: ToolBtn[] = [
    { id: null, label: "Курсор", hotkey: "Esc", iconPath: ICON_CURSOR },
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
    { id: "eraser", label: "Гумка", hotkey: "E", iconPath: ICON_ERASER },
  ];

  function handleClick(id: ActiveTool) {
    // Toggle: re-click активного інструменту → деактивує (null).
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

  // ─────────────────────────────────────────────────────────────────
  // Quiet toolbar — left-curtain proximity. Cursor at/left of REACT_X →
  // fully lit; fades to a ghost across FADE_SPAN as it moves right into
  // the chart. Ghost = pointer-events:none, so clicks pass THROUGH to the
  // chart underneath. An armed tool / magnet keeps it lit. One rAF-throttled
  // capture-phase pointermove — no per-frame allocation.
  // ─────────────────────────────────────────────────────────────────
  let hostEl: HTMLDivElement | undefined = $state();
  let lastX = -99999;

  const REACT_X = 118; // px from the viewport left edge: fully lit at/left of
  const FADE_SPAN = 90; // px of falloff to the right of REACT_X
  const PROX_GHOST = 0.18; // resting opacity once past the band

  function applyDim(): void {
    const el = hostEl;
    if (!el) return;
    // Before the first pointer move, stay fully visible (discoverability).
    if (lastX < -9000) {
      el.style.setProperty("--dim", "1");
      el.style.pointerEvents = "auto";
      return;
    }
    const over = lastX - REACT_X;
    const t = over <= 0 ? 1 : over >= FADE_SPAN ? 0 : 1 - over / FADE_SPAN;
    const prox = PROX_GHOST + (1 - PROX_GHOST) * t;
    const lit = activeTool !== null || magnetEnabled ? 1 : prox;
    el.style.setProperty("--dim", lit.toFixed(3));
    el.style.pointerEvents = lit < 0.45 ? "none" : "auto";
  }

  // Attach the proximity listener once. Capture phase so it keeps firing
  // while the chart's own capture handlers are active over the canvas.
  $effect(() => {
    if (!hostEl) return;
    let raf = 0;
    function onMove(e: PointerEvent) {
      lastX = e.clientX;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        applyDim();
      });
    }
    window.addEventListener("pointermove", onMove, {
      capture: true,
      passive: true,
    });
    return () => {
      window.removeEventListener("pointermove", onMove, true);
      if (raf) cancelAnimationFrame(raf);
    };
  });

  // Re-light immediately when the armed tool / magnet changes.
  $effect(() => {
    applyDim();
  });

  // ─────────────────────────────────────────────────────────────────
  // Per-icon "polite" hover label timing.
  //  #1: hover → TIP_DELAY → show for TIP_SHOW, then hide.
  //  #2: hold the cursor still on the icon for TIP_DWELL → show for
  //      TIP_SHOW_LONG (the "I didn't catch it" re-read), then hide.
  //  #3+: silent — the label has done its job.
  //  Reset: leave the icon for TIP_RESET and its counter clears, so a
  //  later fresh hover starts the cycle again.
  //  alwaysShowHints (menu toggle) bypasses all this — plain show-on-hover
  //  that stays until you leave (learning mode).
  // ─────────────────────────────────────────────────────────────────
  const TIP_DELAY = 450;
  const TIP_SHOW = 1500;
  const TIP_SHOW_LONG = 3000;
  const TIP_DWELL = 600;
  const TIP_RESET = 4000;
  const TIP_MAX = 2;

  let shownKey = $state<string | null>(null);
  const revealCounts = new Map<string, number>();
  const resetTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let hoverKey: string | null = null;
  let delayTimer: ReturnType<typeof setTimeout> | 0 = 0;
  let hideTimer: ReturnType<typeof setTimeout> | 0 = 0;
  let dwellTimer: ReturnType<typeof setTimeout> | 0 = 0;

  function clearTipTimers(): void {
    if (delayTimer) clearTimeout(delayTimer);
    if (hideTimer) clearTimeout(hideTimer);
    if (dwellTimer) clearTimeout(dwellTimer);
    delayTimer = hideTimer = dwellTimer = 0;
  }

  function reveal(key: string, long: boolean): void {
    const c = revealCounts.get(key) ?? 0;
    if (!alwaysShowHints && c >= TIP_MAX) return; // label has done its job
    revealCounts.set(key, c + 1);
    shownKey = key;
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(
      () => hideTip(key),
      long ? TIP_SHOW_LONG : TIP_SHOW,
    );
  }

  function hideTip(key: string): void {
    if (shownKey === key) shownKey = null;
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = 0;
    // Re-arm the dwell re-summon while still hovering and under the cap.
    if (
      !alwaysShowHints &&
      hoverKey === key &&
      (revealCounts.get(key) ?? 0) < TIP_MAX
    ) {
      armDwell(key);
    }
  }

  function armDwell(key: string): void {
    if (dwellTimer) clearTimeout(dwellTimer);
    dwellTimer = setTimeout(() => {
      if (hoverKey === key) reveal(key, true);
    }, TIP_DWELL);
  }

  function tipEnter(key: string): void {
    clearTipTimers();
    hoverKey = key;
    const rt = resetTimers.get(key);
    if (rt) {
      clearTimeout(rt);
      resetTimers.delete(key);
    }
    if (alwaysShowHints) {
      delayTimer = setTimeout(() => {
        if (hoverKey === key) shownKey = key;
      }, TIP_DELAY);
      return;
    }
    if ((revealCounts.get(key) ?? 0) >= TIP_MAX) return; // silent — seen enough
    delayTimer = setTimeout(() => {
      if (hoverKey === key) reveal(key, false);
    }, TIP_DELAY);
  }

  function tipMove(key: string): void {
    // Movement resets the dwell "hold still" countdown.
    if (dwellTimer && hoverKey === key) armDwell(key);
  }

  function tipLeave(key: string): void {
    if (hoverKey === key) hoverKey = null;
    clearTipTimers();
    if (shownKey === key) shownKey = null;
    // After TIP_RESET away, forget this icon's count so it can teach again.
    const prev = resetTimers.get(key);
    if (prev) clearTimeout(prev);
    resetTimers.set(
      key,
      setTimeout(() => {
        revealCounts.delete(key);
        resetTimers.delete(key);
      }, TIP_RESET),
    );
  }
</script>

<div class="drawing-toolbar" bind:this={hostEl}>
  <div class="tools-group" role="toolbar" aria-label="Drawing tools">
    {#each buttons as btn (btn.id ?? "_cursor")}
      <button
        class="tool-btn"
        class:active={isActive(btn.id)}
        onclick={() => handleClick(btn.id)}
        onpointerenter={() => tipEnter(btn.id ?? "_cursor")}
        onpointerleave={() => tipLeave(btn.id ?? "_cursor")}
        onpointermove={() => tipMove(btn.id ?? "_cursor")}
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
        <span class="tool-tip" class:show={shownKey === (btn.id ?? "_cursor")}>
          {btn.label}<kbd>{btn.hotkey}</kbd>
        </span>
      </button>
    {/each}

    <!-- Magnet — modal toggle (magnetEnabled), not an ActiveTool. -->
    {#if onToggleMagnet}
      <button
        class="tool-btn magnet-btn"
        class:active={magnetEnabled}
        onclick={onToggleMagnet}
        onpointerenter={() => tipEnter("_magnet")}
        onpointerleave={() => tipLeave("_magnet")}
        onpointermove={() => tipMove("_magnet")}
        type="button"
        aria-label="Магніт (snap-to-OHLC)"
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
        <span class="tool-tip" class:show={shownKey === "_magnet"}>
          Магніт<kbd>G</kbd>
        </span>
      </button>
    {/if}
  </div>
</div>

<style>
  /* Icon-only toolbar snapped to the left wall, one icon per 36px grid cell.
     No panel chrome — the icons float on the chart. Opacity is driven by the
     left-curtain proximity (--dim, set in script); hover/focus force it lit. */
  .drawing-toolbar {
    position: absolute;
    left: 2px;
    top: 72px;
    z-index: 50;
    width: var(--drawing-cell-size, 36px);
    display: flex;
    flex-direction: column;
    pointer-events: auto;
    opacity: var(--dim, 1);
    transition: opacity 0.28s ease;
  }
  .drawing-toolbar:hover,
  .drawing-toolbar:focus-within {
    opacity: 1;
  }

  .tools-group {
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  .tool-btn {
    position: relative; /* anchor for the hover label */
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--drawing-cell-size, 36px);
    height: var(--drawing-cell-size, 36px);
    padding: 0;
    background: none;
    border: none;
    color: var(--toolbar-btn-color, #c8cdd6);
    opacity: 0.72;
    cursor: pointer;
    transition:
      opacity 0.12s ease,
      color 0.12s ease;
  }

  /* Highlight the icon itself — no background box. Hover = brighter stroke
     + a soft light glow so it pops without any panel behind it. */
  .tool-btn:hover {
    opacity: 1;
    color: var(--toolbar-btn-hover-color, #f2f5fa);
  }
  .tool-btn:hover .tool-icon {
    filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.6))
      drop-shadow(0 0 5px rgba(150, 180, 230, 0.35));
  }
  .tool-btn:focus-visible {
    outline: 1px solid var(--accent, #d4a017);
    outline-offset: -2px;
    border-radius: 6px;
  }
  /* Active = gold icon + gold glow, no background box. */
  .tool-btn.active {
    color: var(--toolbar-active-color, #d4a017);
    opacity: 1;
  }
  .tool-btn.active .tool-icon {
    filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.5))
      drop-shadow(
        0 0 6px
          color-mix(in srgb, var(--toolbar-active-color, #d4a017) 65%, transparent)
      );
  }

  .tool-icon {
    width: var(--drawing-toolbar-icon-size, 16px);
    height: var(--drawing-toolbar-icon-size, 16px);
    flex-shrink: 0;
    display: block;
    /* Soft symmetric dark halo keeps the stroke legible on any candle
       colour without a panel (symmetric = no smear/ghost). */
    filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.65));
  }

  /* Per-icon hover label — a tidy pill to the right of the icon (name +
     hotkey). Reveals on hover / keyboard-focus, slides in, never blocks. */
  .tool-tip {
    position: absolute;
    left: calc(100% + 8px);
    top: 50%;
    transform: translateY(-50%) translateX(-4px);
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 9px;
    border-radius: 7px;
    background: var(--toolbar-tip-bg, rgba(22, 27, 36, 0.97));
    border: 0.5px solid rgba(255, 255, 255, 0.09);
    color: var(--text-1, #e6edf3);
    font-family: var(
      --font-sans,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      sans-serif
    );
    font-size: 12px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition:
      opacity 0.14s ease,
      transform 0.14s ease;
    z-index: 60;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
  }
  .tool-tip.show,
  .tool-btn:focus-visible .tool-tip {
    opacity: 1;
    transform: translateY(-50%) translateX(0);
  }
  .tool-tip kbd {
    font-family: var(--font-mono, "SF Mono", "Consolas", monospace);
    font-size: 10px;
    font-weight: 500;
    line-height: 1;
    padding: 2px 5px;
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.08);
    color: var(--text-3, #8a93a0);
  }

  /* ═══ Mobile (landscape OR portrait) — TEMP HIDE 2026-05-12 ═══
     Drawing tools hidden on touch pending hit-test / draft-commit fixes.
     Do NOT silently re-enable — see header comment. */
  @media (orientation: landscape) and (max-height: 500px) {
    .drawing-toolbar {
      display: none;
    }
  }
  @media (max-width: 640px) and (orientation: portrait) {
    .drawing-toolbar {
      display: none;
    }
  }
</style>
