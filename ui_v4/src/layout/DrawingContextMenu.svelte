<!-- src/layout/DrawingContextMenu.svelte
     ADR-0078: контекстне міні-меню фігури малювання (right-click на фігурі).
     Frosted-glass premium surface на house-токенах (ADR-0066) — theme-aware
     (dark/black/light). Інлайн-свотчі: «Видалити» + ряд кольорів (1 клік = колір).
     Renderer = SSOT: меню лише кличе public deleteById/recolorById через колбеки.
     Dismiss: click/touch поза меню або Escape. Рендериться ПОЗА .chart-container
     (див. ChartPane) — інакше capture-pointerdown малювання ловив би клік по свотчу. -->
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

  // Палітра дзеркалить семантичні токени ADR-0066 (canvas ctx.strokeStyle не
  // читає CSS-vars → зберігаємо concrete hex, як themes.ts дзеркалить для LWC).
  // value=null → колір теми (нейтраль, рендер `?? --drawing-base-color`).
  const PALETTE: { label: string; value: string | null }[] = [
    { label: "Тема", value: null },
    { label: "Золото", value: "#D4A017" }, // --accent
    { label: "Червоний", value: "#ED4554" }, // --bear
    { label: "Зелений", value: "#22CC8F" }, // --bull
    { label: "Синій", value: "#5487FF" }, // --info
    { label: "Бурштин", value: "#FFB347" }, // --warn
  ];

  // Екранна позиція з clamp у viewport (щоб меню не вилазило за край).
  const MENU_W = 172;
  const MENU_H = 84;
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
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="ctx"
    style:left="{left}px"
    style:top="{top}px"
    onclick={(e) => e.stopPropagation()}
    use:dismissOnOutside={{ enabled: true, onDismiss: onClose }}
  >
    <button class="ctx-delete" onclick={() => onDelete(request.id)}>
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M3 6h18" />
        <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      </svg>
      <span>Видалити</span>
    </button>

    <div class="ctx-sep"></div>

    <div class="ctx-colors">
      {#each PALETTE as p (p.label)}
        <button
          class="sw"
          class:neutral={p.value === null}
          class:active={isActive(p.value)}
          style:--c={p.value ?? "var(--drawing-base-color, #c8cdd6)"}
          title={p.label}
          aria-label={p.label}
          onclick={() => onRecolor(request.id, p.value)}
        ></button>
      {/each}
    </div>
  </div>
{/if}

<style>
  .ctx {
    position: fixed;
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 5px;
    min-width: 158px;
    background: color-mix(in srgb, var(--card, #1c2128) 76%, transparent);
    -webkit-backdrop-filter: blur(18px) saturate(1.5);
    backdrop-filter: blur(18px) saturate(1.5);
    border: 1px solid color-mix(in srgb, var(--text-1, #e6edf3) 12%, transparent);
    border-radius: 12px;
    box-shadow:
      0 14px 34px -10px rgba(0, 0, 0, 0.62),
      0 2px 8px rgba(0, 0, 0, 0.34),
      inset 0 1px 0 color-mix(in srgb, var(--text-1, #fff) 8%, transparent);
    font-family: var(--font-sans, "Inter", sans-serif);
    user-select: none;
    transform-origin: top left;
    animation: ctx-in 130ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes ctx-in {
    from {
      opacity: 0;
      transform: scale(0.95) translateY(-3px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  .ctx-delete {
    display: flex;
    align-items: center;
    gap: 9px;
    width: 100%;
    padding: 7px 9px;
    background: transparent;
    border: none;
    border-radius: 8px;
    color: var(--text-2, #9b9bb0);
    font-family: inherit;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.1px;
    text-align: left;
    cursor: pointer;
    transition: background 0.12s ease, color 0.12s ease;
  }
  .ctx-delete svg {
    opacity: 0.8;
    transition: opacity 0.12s ease;
  }
  .ctx-delete:hover {
    background: color-mix(in srgb, var(--bear, #ed4554) 15%, transparent);
    color: var(--bear, #ed4554);
  }
  .ctx-delete:hover svg {
    opacity: 1;
  }

  .ctx-sep {
    height: 1px;
    margin: 2px 6px;
    background: color-mix(in srgb, var(--text-1, #fff) 9%, transparent);
  }

  .ctx-colors {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 8px 4px;
  }

  .sw {
    width: 19px;
    height: 19px;
    padding: 0;
    border-radius: 50%;
    border: 1.5px solid color-mix(in srgb, var(--text-1, #fff) 22%, transparent);
    background: var(--c);
    cursor: pointer;
    transition:
      transform 0.1s cubic-bezier(0.16, 1, 0.3, 1),
      box-shadow 0.1s ease;
  }
  .sw:hover {
    transform: scale(1.22);
    box-shadow: 0 0 9px color-mix(in srgb, var(--c) 55%, transparent);
  }
  /* нейтраль читається як «за замовчуванням / скинути до теми» */
  .sw.neutral {
    border-style: dashed;
  }
  /* active-кільце нейтральним світлом (не золотом) — щоб не збігалось зі
     золотим свотчем; card-gap відділяє кільце від крапки. */
  .sw.active {
    box-shadow:
      0 0 0 2px var(--card, #1c2128),
      0 0 0 3.5px var(--text-1, #e6edf3);
  }
</style>
