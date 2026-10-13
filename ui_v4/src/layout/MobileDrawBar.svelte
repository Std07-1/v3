<!--
  src/layout/MobileDrawBar.svelte — ADR-0112: малювання на телефоні (пробна версія під ?draw=1).

  Одна капсула мовою меню HUD (вибір символу/TF: матове скло, рамка, пункти з радіусом 4, активний — синюватий тон
  із золотом; кольори теми — ті самі menuBg/menuBorder, що в ChartHud), лише іконки, золота лінія по верхньому краю.
  Без інструмента: 6 інструментів реєстру · ↶ · видалити виділене · ✕; ↶ і «видалити» активні лише коли є що
  (canUndo / hasSelection), інакше — притушені й не натискаються.
  З інструментом капсула стає: ✕ · іконка · крок (крапки + «точка 1 з 2») · магніт · ✓. Точку ставить тап будь-де по
  графіку (touchDrawCursor веде кільце 1:1, тап без руху = точка в кільці) або ✓. Готова фігура — інструмент
  знімається сам (one-shot); ефемерна лінійка лишає результат, наступний тап починає новий вимір.
-->
<script lang="ts">
  import { fly } from "svelte/transition";
  import type { ActiveTool } from "../types";
  import { TOOL_REGISTRY } from "../chart/drawings/tools";
  import { ICON_CLOSE, ICON_MAGNET, ICON_TRASH, ICON_UNDO } from "./toolIcons";

  type PlaceResult = "draft" | "done" | "none";

  const {
    activeTool,
    onSelectTool,
    onPlace,
    magnetEnabled = false,
    onToggleMagnet,
    onUndo,
    onDeleteSelected,
    onClose,
    canUndo = false,
    hasSelection = false,
    menuBg = "rgba(30, 34, 45, 0.92)",
    menuBorder = "rgba(255, 255, 255, 0.08)",
  }: {
    activeTool: ActiveTool;
    onSelectTool: (tool: ActiveTool) => void;
    /** ✓ — поставити точку в кільці (те саме, що тап по графіку); null — курсора нема. */
    onPlace: () => PlaceResult | null;
    magnetEnabled?: boolean;
    onToggleMagnet?: () => void;
    onUndo: () => void;
    onDeleteSelected: () => void;
    onClose: () => void;
    /** Є що скасувати / виділено фігуру — ↶ і «видалити» активні лише тоді (ChartPane → App). */
    canUndo?: boolean;
    hasSelection?: boolean;
    /** Кольори меню теми — ті самі, що в меню символу/TF (ChartHud). */
    menuBg?: string;
    menuBorder?: string;
  } = $props();

  const ICON_CHECK = '<path d="M20 6 9 17l-5-5"/>';
  /** Короткий «клац» на вибір інструмента (Android; де нема — тиша). */
  const SELECT_VIBRATE_MS = 6;
  const tools = [...TOOL_REGISTRY.values()];
  const current = $derived(
    activeTool && activeTool !== "eraser" ? (TOOL_REGISTRY.get(activeTool) ?? null) : null,
  );

  // Хід побудови поточної фігури; належить інструменту, з яким його почали (зміна інструмента = з нуля). Точок
  // щонайбільше 2: «draft» завжди означає «поставлено першу» (не лічильник — не розходиться з renderer, рецензія D-2).
  let progress = $state<{ tool: ActiveTool; placed: number; measured: boolean }>({
    tool: null,
    placed: 0,
    measured: false,
  });
  const placed = $derived(progress.tool === activeTool ? progress.placed : 0);
  const measured = $derived(progress.tool === activeTool && progress.measured);

  const stepLabel = $derived.by(() => {
    if (!current) return "";
    if (measured) return "готово · тап — новий";
    if (current.pointsRequired === 1) return "тап — поставити";
    return `точка ${placed + 1} з ${current.pointsRequired}`;
  });
  /** Крапки кроку: скільки точок у фігури і скільки вже стоїть (одноточкова — без крапок). */
  const dots = $derived(
    current && current.pointsRequired > 1
      ? Array.from({ length: current.pointsRequired }, (_, i) => measured || i < placed)
      : [],
  );

  /** Результат точки — з тапу по графіку (ChartPane → App) або з ✓. */
  export function applyPlace(result: PlaceResult): void {
    const tool = current;
    if (!tool) return;
    if (result === "none") {
      progress = { tool: activeTool, placed: 0, measured: false }; // нічого не стало — інструмент лишається
      return;
    }
    if (result === "draft") {
      progress = { tool: activeTool, placed: 1, measured: false };
      return;
    }
    if (tool.ephemeral) {
      progress = { tool: activeTool, placed: 0, measured: true };
      return;
    }
    reset();
    onSelectTool(null); // one-shot: фігура готова — назад до звичайного графіка
  }

  function place(): void {
    const result = onPlace();
    if (result !== null) applyPlace(result);
  }

  function reset(): void {
    progress = { tool: null, placed: 0, measured: false };
  }

  function choose(tool: ActiveTool): void {
    reset();
    if (tool !== null) navigator.vibrate?.(SELECT_VIBRATE_MS);
    onSelectTool(tool);
  }
</script>

{#snippet icon(markup: string)}
  <svg
    class="icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true">{@html markup}</svg
  >
{/snippet}

<div
  class="mdraw"
  role="toolbar"
  aria-label="Малювання"
  style:background={menuBg}
  style:border-color={menuBorder}
>
  <!-- Зміна режиму (інструменти ↔ інструмент) — вміст підпливає, капсула не блимає -->
  {#key current?.id ?? "browse"}
    <div class="row" in:fly={{ y: 6, duration: 180 }}>
      {#if current}
        <button class="btn dim" type="button" aria-label="Скасувати фігуру" onclick={() => choose(null)}>
          {@render icon(ICON_CLOSE)}
        </button>
        <span class="tool-now">
          <span class="btn active as-label" aria-hidden="true">{@render icon(current.icon)}</span>
          <span class="step">
            {#if dots.length}
              <span class="dots" aria-hidden="true">
                {#each dots as filled, i (i)}
                  <span class="dot" class:filled></span>
                {/each}
              </span>
            {/if}
            <span class="step-text">{stepLabel}</span>
          </span>
        </span>
        {#if onToggleMagnet}
          <button
            class="btn"
            class:active={magnetEnabled}
            type="button"
            aria-label="Магніт"
            aria-pressed={magnetEnabled}
            onclick={onToggleMagnet}
          >
            {@render icon(ICON_MAGNET)}
          </button>
        {/if}
        <button class="btn accent" type="button" aria-label="Поставити точку" onclick={place}>
          {@render icon(ICON_CHECK)}
        </button>
      {:else}
        {#each tools as tool (tool.id)}
          <button class="btn" type="button" aria-label={tool.label} onclick={() => choose(tool.id)}>
            {@render icon(tool.icon)}
          </button>
        {/each}
        <span class="sep" style:background={menuBorder}></span>
        <button class="btn" type="button" aria-label="Скасувати дію" disabled={!canUndo} onclick={onUndo}>
          {@render icon(ICON_UNDO)}
        </button>
        <button
          class="btn danger"
          type="button"
          aria-label="Видалити виділене"
          disabled={!hasSelection}
          onclick={onDeleteSelected}
        >
          {@render icon(ICON_TRASH)}
        </button>
        <button class="btn dim" type="button" aria-label="Закрити малювання" onclick={onClose}>
          {@render icon(ICON_CLOSE)}
        </button>
      {/if}
    </div>
  {/key}
</div>

<style>
  /* Мова меню HUD (ChartHud .hud-menu): матове скло, рамка, радіус 8, тінь. Низ графіка — на обсягах, одразу над
     віссю часу (на телефоні 26 px) і правіше знака v3 (BrandWatermark: закріплене місце, на телефоні 6–26 px зліва,
     30–54 px від низу), з безпечною зоною. */
  .mdraw {
    position: absolute;
    left: 32px;
    right: 4px;
    bottom: calc(30px + env(safe-area-inset-bottom, 0px));
    z-index: 30;
    /* По центру через margin, не left:50% — інакше ширину стискає половина контейнера, і кнопки мліють до мінімуму */
    width: max-content;
    max-width: calc(100% - 36px);
    margin: 0 auto;
    box-sizing: border-box;
    padding: 4px;
    border: 1px solid;
    border-radius: 10px;
    backdrop-filter: blur(14px) saturate(1.2);
    -webkit-backdrop-filter: blur(14px) saturate(1.2);
    box-shadow:
      0 8px 28px rgba(0, 0, 0, 0.5),
      inset 0 1px 0 rgba(255, 255, 255, 0.04);
    color: var(--text-1);
    font-family: var(--font-sans);
    animation: mdraw-in 280ms cubic-bezier(0.2, 0.9, 0.25, 1.15);
  }
  /* Золота лінія по верхньому краю — підпис v3 (як золото активного TF), гасне до країв */
  .mdraw::before {
    content: "";
    position: absolute;
    left: 14px;
    right: 14px;
    top: -1px;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent, #d4a017) 50%, transparent);
    opacity: 0.6;
    pointer-events: none;
  }
  @keyframes mdraw-in {
    from {
      opacity: 0;
      transform: translateY(14px) scale(0.96);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }
  .row {
    display: flex;
    align-items: center;
    min-width: 0;
  }
  /* Пункт меню HUD: радіус 4, активний — синюватий тон + золото (як вибраний TF / символ). 9 кнопок по 36 вміщаються
     в телефон 375 px; на вужчому стискаються до 32. */
  .btn {
    all: unset;
    flex: 0 1 auto;
    width: 36px;
    min-width: 32px;
    height: 38px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    color: inherit;
    opacity: 0.88;
    transition:
      background 0.12s,
      opacity 0.2s,
      color 0.2s,
      transform 0.12s;
    touch-action: manipulation;
  }
  .btn:active:not(:disabled) {
    background: rgba(128, 128, 128, 0.15);
    transform: scale(0.92);
  }
  .btn.active {
    background: rgba(74, 144, 217, 0.2);
    color: var(--accent);
    opacity: 1;
  }
  .btn.dim {
    opacity: 0.6;
  }
  .btn.accent {
    color: var(--accent);
    opacity: 1;
  }
  /* «Видалити» — руйнівна дія: коли є що, червонуватий тон (як × на фігурі) */
  .btn.danger:not(:disabled) {
    color: #ef6b6b;
    opacity: 1;
  }
  /* Нема що скасувати / нічого не виділено — кнопка є (місце не стрибає), але притушена й не натискається */
  .btn:disabled {
    opacity: 0.22;
    cursor: default;
  }
  .as-label {
    pointer-events: none;
  }
  .tool-now {
    flex: 1 1 auto;
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 0 6px 0 2px;
  }
  .step {
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 13px;
    color: var(--text-1);
    white-space: nowrap;
  }
  .step-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .dots {
    display: inline-flex;
    gap: 4px;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    border: 1px solid var(--accent, #d4a017);
    opacity: 0.55;
    transition:
      background 0.2s,
      opacity 0.2s,
      transform 0.2s;
  }
  .dot.filled {
    background: var(--accent, #d4a017);
    opacity: 1;
    transform: scale(1.15);
    box-shadow: 0 0 6px rgba(212, 160, 23, 0.6);
  }
  .sep {
    flex: 0 0 auto;
    width: 1px;
    height: 22px;
    margin: 0 3px;
  }
  .icon {
    width: 20px;
    height: 20px;
  }
</style>
