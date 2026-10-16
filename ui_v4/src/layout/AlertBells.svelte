<!-- src/layout/AlertBells.svelte
     ADR-0114 S8: дзвіночки ліній-сповіщень — DOM-шар над полотном малювань, на правому кінці лінії біля шкали ціни.
     Колір — колір самої лінії; спрацювало — тьмяніє; не доставлено в Telegram — крапка попередження. Без анімацій.
     pointer-events: none — шар нічого не перехоплює; на знімок графіка (ADR-0109, полотна) не потрапляє. -->
<script lang="ts">
  import type { Bell } from "../chart/alerts/alertBells";
  import { ICON_BELL } from "./toolIcons";

  interface Props {
    bells: readonly Bell[];
    /** Ширина шкали ціни праворуч, CSS-px: дзвіночок стоїть одразу перед нею. */
    priceScaleWidth: number;
  }
  let { bells, priceScaleWidth }: Props = $props();
</script>

<div class="alert-bells" style:right="{priceScaleWidth}px" aria-hidden="true">
  {#each bells as bell (bell.drawingId)}
    <span class="bell" class:dim={bell.state !== "armed"} style:top="{bell.y}px" style:color={bell.color}>
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1"
        stroke-linecap="round" stroke-linejoin="round">{@html ICON_BELL}</svg>
      {#if bell.state === "undelivered"}<span class="undelivered"></span>{/if}
    </span>
  {/each}
</div>

<style>
  .alert-bells {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 0;
    z-index: 21; /* одразу над полотном малювань (20), під панелями */
    pointer-events: none;
  }
  .bell {
    position: absolute;
    right: 5px;
    display: flex;
    transform: translateY(-50%);
    /* halo тлом графіка — читається поверх свічок, як × видалення фігури */
    filter: drop-shadow(0 0 1px var(--bg, #0d1117)) drop-shadow(0 0 2px var(--bg, #0d1117));
  }
  .bell.dim {
    opacity: 0.38;
  }
  .undelivered {
    position: absolute;
    top: -1px;
    right: -2px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--warn, #ffb347);
  }
</style>
