<!--
  src/layout/NoteEditor.svelte — ADR-0111 §3.7: поле вводу тексту нотатки (власник 19.10: «Так усе»).

  ПК: поле стає поверх нотатки (лівий нижній кут — якір чіпа); Enter або клік поза полем — зберегти, Esc — скасувати.
  Телефон (`request.touch`): поле там, де капсула малювання, — над клавіатурою (visualViewport), мовою капсули (скло,
  золота лінія по верхньому краю), з ✓ і ✕. Порожній текст — нотатки нема (нова) або текст не змінюється.
  Клавіші поля не доходять до клавіатури графіка (Ctrl+Z, Delete, літери-інструменти — stopPropagation).
  Монтування — noteEditorHost.ts (поза ChartPane/App — обидва біля межі X33).
-->
<script lang="ts">
  import { msg, t } from "../lib/i18n/i18n.svelte";
  import { untrack } from "svelte";
  import { NOTE_MAX_CHARS, normalizeNoteText, type NoteTextRequest } from "../chart/drawings/noteText";
  import { ICON_CLOSE } from "./toolIcons";

  const { request, onClose }: { request: NoteTextRequest; onClose: () => void } = $props();

  const PLACEHOLDER = msg("Текст нотатки");
  const SAVE_LABEL = msg("Зберегти нотатку");
  const CANCEL_LABEL = msg("Скасувати");
  const ICON_CHECK = '<path d="M20 6 9 17l-5-5"/>';
  /** Висота поля на ПК — як чіп нотатки (TextTool CHIP_H 20) з рамкою: поле накриває чіп. */
  const DESKTOP_FIELD_H = 24;
  /** Відступ поля телефона над клавіатурою. */
  const DOCK_GAP_PX = 8;
  /** Без клавіатури поле — рівно на місці капсули, накриває її (той самий низ, що `.mdraw` у MobileDrawBar). */
  const CAPSULE_BOTTOM = "calc(30px + env(safe-area-inset-bottom, 0px))";

  // Поле живе один запит (host монтує нове на кожен) — початковий текст береться раз
  let value = $state(untrack(() => request.initial));
  let done = false;
  /** Висота клавіатури телефона: низ видимої області (visualViewport) від низу вікна. */
  let keyboardInset = $state(0);

  function finish(save: boolean): void {
    if (done) return;
    done = true;
    const text = save ? normalizeNoteText(value) : null;
    if (text !== null && text !== request.initial) request.commit(text);
    onClose();
  }

  function onKeydown(e: KeyboardEvent): void {
    e.stopPropagation(); // клавіатура графіка (window) поля не чує
    if (e.key === "Enter") {
      e.preventDefault();
      finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  }

  $effect(() => {
    const vv = window.visualViewport;
    if (!request.touch || !vv) return;
    const update = () => (keyboardInset = Math.max(0, window.innerHeight - (vv.offsetTop + vv.height)));
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  });
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

{#if request.touch}
  <div class="note-editor docked" style:bottom="max({keyboardInset + DOCK_GAP_PX}px, {CAPSULE_BOTTOM})">
    <input
      type="text"
      bind:value
      maxlength={NOTE_MAX_CHARS}
      placeholder={t(PLACEHOLDER)}
      aria-label={t(PLACEHOLDER)}
      enterkeyhint="done"
      onkeydown={onKeydown}
    />
    <!-- pointerdown без зміни фокусу: інакше поле втратить фокус (і збереже) раніше, ніж ✕ скасує -->
    <button
      class="btn accent"
      type="button"
      aria-label={t(SAVE_LABEL)}
      onpointerdown={(e) => e.preventDefault()}
      onclick={() => finish(true)}>{@render icon(ICON_CHECK)}</button
    >
    <button
      class="btn"
      type="button"
      aria-label={t(CANCEL_LABEL)}
      onpointerdown={(e) => e.preventDefault()}
      onclick={() => finish(false)}>{@render icon(ICON_CLOSE)}</button
    >
  </div>
{:else}
  <div class="note-editor anchored" style:left="{request.clientX}px" style:top="{request.clientY - DESKTOP_FIELD_H}px">
    <input
      type="text"
      bind:value
      maxlength={NOTE_MAX_CHARS}
      placeholder={t(PLACEHOLDER)}
      aria-label={t(PLACEHOLDER)}
      onkeydown={onKeydown}
      onblur={() => finish(true)}
    />
  </div>
{/if}

<style>
  .note-editor {
    position: fixed;
    z-index: 1000;
    box-sizing: border-box;
    color: var(--text-1, #e6edf3);
    font-family: var(--font-sans, Inter, system-ui, sans-serif);
  }
  .note-editor input {
    all: unset;
    box-sizing: border-box;
    min-width: 0;
    font-size: 12px;
    line-height: 1;
    color: inherit;
  }
  .note-editor input::placeholder {
    color: var(--text-3, #8a93a0);
  }

  /* ПК: тонке поле поверх чіпа нотатки, тим самим шрифтом; рамка — акцент (йде ввід). */
  .anchored {
    height: 24px;
    width: 220px;
    border-radius: 4px;
    border: 1px solid var(--accent, #d4a017);
    background: var(--toolbar-tip-bg, rgba(22, 27, 36, 0.97));
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
  }
  .anchored input {
    width: 100%;
    height: 100%;
    padding: 0 6px;
  }

  /* Телефон: на місці капсули малювання, її мовою — скло, рамка теми, золота лінія по верхньому краю. */
  .docked {
    left: 8px;
    right: 8px;
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 4px 4px 4px 12px;
    border: 1px solid var(--toolbar-border, rgba(48, 54, 61, 0.8));
    border-radius: 10px;
    background: var(--toolbar-bg, rgba(13, 17, 23, 0.6));
    backdrop-filter: blur(14px) saturate(1.2);
    -webkit-backdrop-filter: blur(14px) saturate(1.2);
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.5);
  }
  .docked::before {
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
  .docked input {
    flex: 1 1 auto;
    height: 38px;
    font-size: 16px; /* iOS не збільшує сторінку на фокусі поля від 16 px */
  }
  .btn {
    all: unset;
    flex: 0 0 auto;
    width: 38px;
    height: 38px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    touch-action: manipulation;
  }
  .btn:active {
    background: rgba(128, 128, 128, 0.15);
  }
  .btn.accent {
    color: var(--accent, #d4a017);
  }
  .icon {
    width: 20px;
    height: 20px;
  }
</style>
