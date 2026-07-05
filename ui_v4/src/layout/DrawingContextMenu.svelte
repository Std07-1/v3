<!-- src/layout/DrawingContextMenu.svelte
     ADR-0078: контекстне міні-меню фігури малювання (right-click на фігурі).
     Інлайн-свотчі: рядок «Видалити» + ряд кольорових крапок (1 клік = колір).
     Renderer лишається джерелом істини — меню лише викликає public deleteById/
     recolorById через колбеки. Dismiss: click/touch поза меню або Escape. -->
<script lang="ts">
  import type { DrawingContextRequest } from "../types";
  import { dismissOnOutside } from "../lib/actions/dismissOnOutside";

  interface Props {
    request: DrawingContextRequest | null;
    onDelete: (id: string) => void;
    onRecolor: (id: string, color: string | null) => void;
    onClose: () => void;
  }
  let { request, onDelete, onRecolor, onClose }: Props = $props();

  // ADR-0078: невелика трейдерська палітра. value=null → колір теми (нейтраль).
  const PALETTE: { label: string; value: string | null }[] = [
    { label: "Тема", value: null },
    { label: "Золото", value: "#D4A017" },
    { label: "Червоний", value: "#EF5350" },
    { label: "Зелений", value: "#26A69A" },
    { label: "Синій", value: "#42A5F5" },
    { label: "Оранж", value: "#FFA726" },
  ];

  // Екранна позиція з clamp у viewport (щоб меню не вилазило за край).
  const MENU_W = 176;
  const MENU_H = 78;
  const GAP = 8;
  let left = $derived(
    request ? Math.max(GAP, Math.min(request.screenX, window.innerWidth - MENU_W - GAP)) : 0,
  );
  let top = $derived(
    request ? Math.max(GAP, Math.min(request.screenY, window.innerHeight - MENU_H - GAP)) : 0,
  );

  // Активний swatch (кільце): null == колір теми.
  function isActive(v: string | null): boolean {
    return (request?.color ?? null) === v;
  }
</script>

{#if request}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="draw-ctx"
    style:left="{left}px"
    style:top="{top}px"
    style:width="{MENU_W}px"
    onclick={(e) => e.stopPropagation()}
    use:dismissOnOutside={{ enabled: true, onDismiss: onClose }}
  >
    <button class="ctx-delete" onclick={() => onDelete(request.id)}>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M3 6h18" />
        <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      </svg>
      <span>Видалити</span>
    </button>
    <div class="ctx-swatches">
      {#each PALETTE as p (p.label)}
        <button
          class="swatch"
          class:neutral={p.value === null}
          class:active={isActive(p.value)}
          style:--sw={p.value ?? "var(--drawing-base-color, #c8cdd6)"}
          title={p.label}
          aria-label={p.label}
          onclick={() => onRecolor(request.id, p.value)}
        ></button>
      {/each}
    </div>
  </div>
{/if}

<style>
  .draw-ctx {
    position: fixed;
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px;
    background: #0d1117;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
    user-select: none;
  }
  .ctx-delete {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 8px;
    background: transparent;
    border: none;
    border-radius: 5px;
    color: #e6edf3;
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .ctx-delete:hover {
    background: rgba(239, 83, 80, 0.16);
    color: #ff8a80;
  }
  .ctx-swatches {
    display: flex;
    gap: 6px;
    padding: 2px 4px 0;
  }
  .swatch {
    width: 20px;
    height: 20px;
    padding: 0;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.25);
    background: var(--sw);
    cursor: pointer;
    transition: transform 0.08s ease;
  }
  .swatch.neutral {
    /* нейтраль: контур яскравіший, щоб читалась як «скинути до теми» */
    border-style: dashed;
  }
  .swatch:hover {
    transform: scale(1.15);
  }
  .swatch.active {
    box-shadow: 0 0 0 2px #0d1117, 0 0 0 4px currentColor;
    color: #d4a017;
  }
</style>
