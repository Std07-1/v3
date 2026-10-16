<!-- src/layout/AlertsMenuSection.svelte
     ADR-0114 S8: ☰ → «Сповіщення». Не під'єднано — «Під'єднати Telegram» (відкриває бота) і поле для 6 цифр, які
     бот надішле («код назад», §4.5: прив'язується лише цей браузер). Під'єднано — список сповіщень з діями. Сервіс
     вимкнений чи недоступний — пункту нема. Стан — із сервера (X28); тут лише показ. -->
<script lang="ts">
  import { serverConfig } from "../app/frameRouter";
  import { configuredPriceDigits } from "../lib/priceDigits";
  import { alerts } from "../lib/alerts/alertsStore";
  import type { AlertWire, ConfirmResult } from "../lib/alerts/alertClient";
  import { AlertsApiError } from "../lib/alerts/alertClient";
  import { alertErrorText, alertStateText, alertTitle } from "../lib/alerts/alertUi";

  const view = alerts.view;
  const CODE_DIGITS = 6;

  let expanded = $state(false);
  let pairingLink = $state<string | null>(null);
  let code = $state("");
  let message = $state<string | null>(null);
  let busy = $state(false);

  const digits = $derived(code.replace(/\D/g, ""));
  const sorted = $derived(
    [...$view.alerts].sort(
      (a, b) =>
        Number(a.state !== "armed") - Number(b.state !== "armed") || b.created_at_ms - a.created_at_ms,
    ),
  );
  const armedCount = $derived($view.alerts.filter((a) => a.state === "armed").length);

  function confirmMessage(result: ConfirmResult): string | null {
    if (result.status === "wrong_code") return `Невірні цифри — лишилось спроб: ${result.attempts_left ?? 0}`;
    if (result.status === "awaiting_bot") return "Спершу відкрийте бота за посиланням і натисніть Start";
    if (result.status === "locked") return "Спроби вичерпано — почніть під'єднання знову";
    if (result.status === "no_pairing") return "Час минув — почніть під'єднання знову";
    return "Telegram під'єднано";
  }

  async function run(job: () => Promise<void>): Promise<void> {
    busy = true;
    message = null;
    try {
      await job();
    } catch (err) {
      message = alertErrorText(err instanceof AlertsApiError ? err.code : String(err));
    } finally {
      busy = false;
    }
  }

  const startPairing = () =>
    run(async () => {
      const { link } = await alerts.pair();
      pairingLink = link;
      window.open(link, "_blank", "noopener");
    });

  const confirm = () =>
    run(async () => {
      const result = await alerts.confirm(digits);
      message = confirmMessage(result);
      if (result.status === "bound" || result.status === "locked" || result.status === "no_pairing") {
        pairingLink = null;
        code = "";
      }
    });

  const digitsOf = (alert: AlertWire) => configuredPriceDigits($serverConfig.priceDigits, alert.symbol);
</script>

{#if $view.status === "unpaired" || $view.status === "ready"}
  <button class="row" role="menuitem" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
    <span class="label">Сповіщення</span>
    <span class="state">{$view.status === "unpaired" ? "не під'єднано" : armedCount} {expanded ? "▾" : "▸"}</span>
  </button>

  {#if expanded}
    <div class="section">
      {#if $view.status === "unpaired"}
        {#if pairingLink === null}
          <p class="hint">Повідомлення про перетин ліній приходять у Telegram.</p>
          <button class="action" disabled={busy} onclick={startPairing}>Під'єднати Telegram</button>
        {:else}
          <p class="hint">
            Бот надішле {CODE_DIGITS} цифр — введіть їх тут.
            <a href={pairingLink} target="_blank" rel="noopener">Відкрити бота</a>
          </p>
          <div class="code-row">
            <input
              class="code"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="8"
              placeholder="000000"
              aria-label="Цифри з Telegram"
              bind:value={code}
            />
            <button class="action" disabled={busy || digits.length !== CODE_DIGITS} onclick={confirm}>
              Підтвердити
            </button>
          </div>
        {/if}
      {:else}
        {#if sorted.length === 0}
          <p class="hint">Правий клік на горизонталі → «Сповіщати».</p>
        {/if}
        {#each sorted as alert (alert.alert_id)}
          <div class="item" class:fired={alert.state !== "armed"}>
            <div class="text">
              <span class="title">{alertTitle(alert, digitsOf(alert))}</span>
              <span class="sub">{alertStateText(alert, $view.degraded)}</span>
            </div>
            <div class="actions">
              {#if alert.state !== "armed"}
                <button class="mini" disabled={busy} onclick={() => run(async () => void (await alerts.rearmAlert(alert)))}>
                  знову
                </button>
              {/if}
              <button
                class="mini"
                disabled={busy}
                onclick={() => run(() => alerts.disable(alert.drawing_id, alert.symbol))}
              >
                {alert.state === "armed" ? "вимкнути" : "прибрати"}
              </button>
            </div>
          </div>
        {/each}
      {/if}
      {#if message}
        <p class="hint message">{message}</p>
      {/if}
    </div>
  {/if}
{/if}

<style>
  /* Мова пунктів CommandRailOverflow (стилі там scoped — повторюємо ті самі токени). */
  .row {
    all: unset;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 7px 10px;
    font-size: var(--t3-size, 12px);
    color: var(--text-2, #9b9bb0);
    cursor: pointer;
    border-radius: 5px;
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }
  .row:hover,
  .row:focus-visible {
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    color: var(--text-1);
  }
  .label {
    flex: 1;
    font-weight: 500;
    letter-spacing: 0.01em;
  }
  .state {
    font-size: var(--t4-size, 11px);
    color: var(--text-3, #6d7080);
    font-family: var(--font-mono);
  }
  .section {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0 4px 6px;
    padding: 8px;
    border-radius: 7px;
    background: color-mix(in srgb, var(--text-1, #e6edf3) 4%, transparent);
  }
  .hint {
    margin: 0;
    font-size: var(--t4-size, 11px);
    line-height: 1.4;
    color: var(--text-3, #6d7080);
  }
  .hint a {
    color: var(--accent, #d4a017);
  }
  .message {
    color: var(--text-2, #9b9bb0);
  }
  .action,
  .mini {
    all: unset;
    box-sizing: border-box;
    cursor: pointer;
    border-radius: 5px;
    font-size: var(--t4-size, 11px);
    font-weight: 500;
    text-align: center;
    color: var(--accent, #d4a017);
    border: 1px solid color-mix(in srgb, var(--accent, #d4a017) 35%, transparent);
  }
  .action {
    padding: 6px 10px;
  }
  .mini {
    flex: none;
    padding: 2px 6px;
  }
  .action:hover:not(:disabled),
  .mini:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent, #d4a017) 12%, transparent);
  }
  .action:disabled,
  .mini:disabled {
    cursor: default;
    opacity: 0.45;
  }
  .code-row {
    display: flex;
    gap: 6px;
  }
  .code {
    flex: 1;
    min-width: 0;
    box-sizing: border-box;
    padding: 5px 8px;
    border-radius: 5px;
    border: 1px solid color-mix(in srgb, var(--text-1, #e6edf3) 14%, transparent);
    background: transparent;
    color: var(--text-1, #e6edf3);
    font-family: var(--font-mono);
    font-size: 13px;
    letter-spacing: 0.18em;
  }
  .item {
    display: flex;
    flex-wrap: wrap; /* дві кнопки не стискають текст — переносяться під нього праворуч */
    align-items: center;
    gap: 4px 6px;
  }
  .actions {
    display: flex;
    gap: 4px;
    margin-left: auto;
  }
  .item.fired .title {
    opacity: 0.6;
  }
  .text {
    flex: 1 1 128px;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .title {
    font-family: var(--font-mono);
    font-size: var(--t4-size, 11px);
    color: var(--text-1, #e6edf3);
  }
  .sub {
    font-size: 10.5px;
    color: var(--text-3, #6d7080);
  }
</style>
