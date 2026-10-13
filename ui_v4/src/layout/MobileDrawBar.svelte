<!--
  src/layout/MobileDrawBar.svelte — ADR-0112: малювання на телефоні (пробна версія під ?draw=1).

  Без інструмента: ряд інструментів реєстру (ADR-0111 §3.3) + магніт + ✕; під ним ↶ ↷ «Видалити» (виділену фігуру).
  З інструментом: ✕ (скасувати) · «Трендова · точка 1 з 2» · «Поставити». Курсор-перехрестя веде touchDrawCursor
  (ChartPane): палець будь-де рухає його 1:1, «Поставити» = клік мишею в точці курсора. Готова фігура — інструмент
  знімається сам (one-shot); ефемерна лінійка лишає результат, наступне натискання починає новий вимір.
-->
<script lang="ts">
  import type { ActiveTool } from "../types";
  import { TOOL_REGISTRY } from "../chart/drawings/tools";
  import { ICON_CLOSE, ICON_MAGNET, ICON_REDO, ICON_TRASH, ICON_UNDO } from "./toolIcons";

  const {
    activeTool,
    onSelectTool,
    onPlace,
    magnetEnabled = false,
    onToggleMagnet,
    onUndo,
    onRedo,
    onDeleteSelected,
    onClose,
  }: {
    activeTool: ActiveTool;
    onSelectTool: (tool: ActiveTool) => void;
    /** «Поставити» в точці курсора: 'draft' — фігура ще в роботі, 'done' — готова; null — курсора нема. */
    onPlace: () => "draft" | "done" | null;
    magnetEnabled?: boolean;
    onToggleMagnet?: () => void;
    onUndo: () => void;
    onRedo: () => void;
    onDeleteSelected: () => void;
    onClose: () => void;
  } = $props();

  const tools = [...TOOL_REGISTRY.values()];
  const current = $derived(
    activeTool && activeTool !== "eraser" ? (TOOL_REGISTRY.get(activeTool) ?? null) : null,
  );

  // Хід побудови поточної фігури; належить інструменту, з яким його почали (зміна інструмента = з нуля).
  let progress = $state<{ tool: ActiveTool; placed: number; measured: boolean }>({
    tool: null,
    placed: 0,
    measured: false,
  });
  const placed = $derived(progress.tool === activeTool ? progress.placed : 0);
  const measured = $derived(progress.tool === activeTool && progress.measured);

  const stepLabel = $derived.by(() => {
    if (!current) return "";
    if (measured) return `${current.label} · готово`;
    if (current.pointsRequired === 1) return current.label;
    return `${current.label} · точка ${placed + 1} з ${current.pointsRequired}`;
  });

  function place(): void {
    const tool = current;
    if (!tool) return;
    const result = onPlace();
    if (result === null) return;
    if (result === "draft") {
      progress = { tool: activeTool, placed: placed + 1, measured: false };
      return;
    }
    if (tool.ephemeral) {
      progress = { tool: activeTool, placed: 0, measured: true };
      return;
    }
    progress = { tool: null, placed: 0, measured: false };
    onSelectTool(null); // one-shot: фігура готова — назад до звичайного графіка
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

<div class="mdraw" role="toolbar" aria-label="Малювання">
  {#if current}
    <div class="row">
      <button class="btn" type="button" aria-label="Скасувати фігуру" onclick={() => onSelectTool(null)}>
        {@render icon(ICON_CLOSE)}
      </button>
      <span class="step">{stepLabel}</span>
      <button class="btn primary" type="button" onclick={place}>{measured ? "Новий вимір" : "Поставити"}</button>
    </div>
  {:else}
    <div class="row">
      {#each tools as tool (tool.id)}
        <button class="btn" type="button" aria-label={tool.label} onclick={() => onSelectTool(tool.id)}>
          {@render icon(tool.icon)}
        </button>
      {/each}
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
      <button class="btn close" type="button" aria-label="Закрити малювання" onclick={onClose}>
        {@render icon(ICON_CLOSE)}
      </button>
    </div>
    <div class="row actions">
      <button class="act" type="button" onclick={onUndo}>{@render icon(ICON_UNDO)}<span>Скасувати</span></button>
      <button class="act" type="button" onclick={onRedo}>{@render icon(ICON_REDO)}<span>Повторити</span></button>
      <button class="act" type="button" onclick={onDeleteSelected}>{@render icon(ICON_TRASH)}<span>Видалити</span></button>
    </div>
  {/if}
</div>

<style>
  /* Над віссю часу (≈28 px) і безпечною зоною телефона; поверх шарів графіка, нижче модалок. */
  .mdraw {
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: calc(36px + env(safe-area-inset-bottom, 0px));
    z-index: 30;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px;
    background: var(--elev);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
    font-family: var(--font-sans);
    color: var(--text-1);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2px;
  }
  .btn {
    flex: 0 0 auto;
    width: 40px;
    height: 40px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: var(--text-1);
    touch-action: manipulation;
  }
  .btn.active {
    color: var(--accent);
  }
  .btn.close {
    color: var(--text-3);
  }
  .btn.primary {
    width: auto;
    padding: 0 18px;
    background: var(--accent);
    color: #111;
    font-size: 15px;
    font-weight: 600;
  }
  .step {
    flex: 1 1 auto;
    min-width: 0;
    padding: 0 8px;
    font-size: 14px;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .actions {
    justify-content: space-around;
    border-top: 1px solid var(--border);
    padding-top: 4px;
  }
  .act {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 32px;
    padding: 0 10px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: var(--text-1);
    font-size: 13px;
    touch-action: manipulation;
  }
  .icon {
    width: 22px;
    height: 22px;
  }
  .act .icon {
    width: 18px;
    height: 18px;
  }
  .btn:active,
  .act:active {
    background: color-mix(in srgb, var(--text-1) 12%, transparent);
  }
</style>
