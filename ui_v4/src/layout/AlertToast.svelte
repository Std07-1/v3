<!-- src/layout/AlertToast.svelte
     ADR-0114 S8: коротке повідомлення на відкритій сторінці — спрацювання лінії (з тихим сигналом) або відмова дії
     (причина людською мовою). Зверху по центру під рядками HUD, скло мовою меню; сам зникає. Нічого не перехоплює.
     Rev 2: поки браузер підписується на пуш (перша реєстрація у FCM — до хвилини) — «Вмикаю сповіщення…». -->
<script lang="ts">
  import { onMount } from "svelte";
  import { fly } from "svelte/transition";
  import { get } from "svelte/store";
  import { serverConfig } from "../app/frameRouter";
  import { configuredPriceDigits } from "../lib/priceDigits";
  import { alerts } from "../lib/alerts/alertsStore";
  import { alertErrorText, firedToastText } from "../lib/alerts/alertUi";
  import { armAlertSound, playAlertChime } from "../lib/alerts/alertSound";
  import { ICON_BELL } from "./toolIcons";

  type ToastKind = "fired" | "error" | "info";
  const SHOW_MS: Record<ToastKind, number> = { fired: 8000, error: 5000, info: 60_000 }; // info — поки триває дія

  let toast = $state<{ id: number; text: string; kind: ToastKind } | null>(null);
  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function show(text: string, kind: ToastKind): void {
    toast = { id: ++seq, text, kind };
    clearTimeout(timer);
    timer = setTimeout(() => (toast = null), SHOW_MS[kind]);
  }

  onMount(() => {
    const disarm = armAlertSound();
    const stopFired = alerts.onFired((alert) => {
      const digits = configuredPriceDigits(get(serverConfig).priceDigits, alert.symbol);
      show(firedToastText(alert, digits), "fired");
      playAlertChime();
    });
    let lastError: string | null = null;
    let enabling = false;
    const stopView = alerts.view.subscribe((view) => {
      if (view.pushEnabling && !enabling) show("Вмикаю сповіщення…", "info");
      if (!view.pushEnabling && enabling && toast?.kind === "info") toast = null;
      enabling = view.pushEnabling;
      if (view.lastError !== null && view.lastError !== lastError) show(alertErrorText(view.lastError), "error");
      lastError = view.lastError;
    });
    return () => {
      disarm();
      stopFired();
      stopView();
      clearTimeout(timer);
    };
  });
</script>

{#if toast}
  {#key toast.id}
    <div class="alert-toast" class:error={toast.kind === "error"} role="status" aria-live="polite"
      transition:fly={{ y: -6, duration: 160 }}>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{@html ICON_BELL}</svg>
      <span>{toast.text}</span>
    </div>
  {/key}
{/if}

<style>
  .alert-toast {
    position: absolute;
    top: 44px;
    left: 0;
    right: 0;
    margin: 0 auto;
    width: max-content;
    max-width: calc(100% - 32px);
    z-index: 40;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    box-sizing: border-box;
    background: color-mix(in srgb, var(--card, #1c2128) 72%, transparent);
    -webkit-backdrop-filter: blur(18px) saturate(1.4);
    backdrop-filter: blur(18px) saturate(1.4);
    border: 1px solid color-mix(in srgb, var(--text-1, #e6edf3) 9%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--accent, #d4a017) 55%, transparent);
    border-radius: 9px;
    box-shadow: 0 6px 18px -10px rgba(0, 0, 0, 0.5);
    color: var(--text-1, #e6edf3);
    font-family: var(--font-sans, "Inter", sans-serif);
    font-size: 12px;
    font-weight: 450;
    letter-spacing: 0.2px;
    pointer-events: none;
  }
  .alert-toast svg {
    flex: none;
    color: var(--accent, #d4a017);
  }
  .alert-toast.error {
    border-top-color: color-mix(in srgb, var(--warn, #ffb347) 55%, transparent);
  }
  .alert-toast.error svg {
    color: var(--warn, #ffb347);
  }
</style>
