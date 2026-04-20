<!--
    InputBar — тонкий premium композитор (ADR-0053 S4 rewrite).

    Структура:
        ┌──────────────────────────────────────────┐
        │ ╭──────────────────────────────────────╮ │
        │ │ Повідомлення…           🎤  😊  ➤   │ │   focus-ring ззовні pill
        │ ╰──────────────────────────────────────╯ │
        └──────────────────────────────────────────┘

    Прибрано (GPT-стиль, категорично відкинуто власником):
      - QuickActions контекст-чіпи ("🎯 Перевір тезис / 💼 VP статус / 🧠 Аналіз / 💭 Думки?")
      - Static hint "Enter — новий рядок · Ctrl/Cmd+Enter — відправити"

    Лишилось:
      - primeDraft/setDraft (handoff restore) з transient hint toast
      - Voice + Emoji + Send (icons інтегровані у pill-trailing)
      - applyHeight (auto-grow desktop 44..140, mobile 52..196)
      - error banner (з chatStore) як тонка лінія над pill
      - iOS Safari anti-zoom (font-size ≥16px на mobile)

    Shortcuts: Enter → newline, Ctrl/Cmd+Enter → send. Без легенди — це стандарт.
-->
<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import VoiceButton from "./VoiceButton.svelte";
    import EmojiPicker from "./EmojiPicker.svelte";

    let {
        value = $bindable(""),
        sending = false,
        error = "",
        onsend = (): void => {},
        oninputchange = (_text: string): void => {},
        onfocuschange = (_focused: boolean): void => {},
    } = $props<{
        value?: string;
        sending?: boolean;
        error?: string;
        onsend?: () => void;
        oninputchange?: (text: string) => void;
        onfocuschange?: (focused: boolean) => void;
    }>();

    const DESKTOP_MIN = 40;
    const DESKTOP_MAX = 144;
    const MOBILE_MIN = 48;
    const MOBILE_MAX = 196;

    let textareaEl: HTMLTextAreaElement;
    let focused = $state(false);
    let hint = $state("");
    let hintTimer: ReturnType<typeof setTimeout> | null = null;
    let voiceError = $state("");

    function isMobileViewport(): boolean {
        return (
            typeof window !== "undefined" &&
            window.matchMedia("(max-width: 768px)").matches
        );
    }
    function clamp(v: number, mn: number, mx: number): number {
        return Math.min(mx, Math.max(mn, v));
    }
    function getMobileMax(): number {
        const vh = window.visualViewport?.height ?? window.innerHeight;
        return Math.round(Math.min(vh * 0.28, MOBILE_MAX));
    }

    export function applyHeight(): void {
        if (!textareaEl) return;
        const mobile = isMobileViewport();
        const min = mobile ? MOBILE_MIN : DESKTOP_MIN;
        const max = mobile ? getMobileMax() : DESKTOP_MAX;
        textareaEl.style.height = "auto";
        const natural = textareaEl.scrollHeight;
        const target = clamp(natural, min, max);
        textareaEl.style.height = `${target}px`;
        textareaEl.style.overflowY = natural > target ? "auto" : "hidden";
    }

    export function focus(): void {
        textareaEl?.focus();
    }

    export function resetHeight(): void {
        if (textareaEl) textareaEl.style.height = "auto";
    }

    function showHint(text: string): void {
        hint = text;
        if (hintTimer) clearTimeout(hintTimer);
        hintTimer = setTimeout(() => {
            hint = "";
            hintTimer = null;
        }, 2600);
    }

    export function setDraft(text: string, hintText: string): void {
        value = text;
        oninputchange(text);
        showHint(hintText);
        tick().then(() => {
            applyHeight();
            textareaEl?.focus();
            const caret = value.length;
            textareaEl?.setSelectionRange?.(caret, caret);
        });
    }

    export function primeDraft(text: string): void {
        const existing = value.trim();
        const nextText = existing ? `${value}\n\n${text}` : text;
        setDraft(
            nextText,
            existing
                ? "Команду додано в чернетку."
                : "Чернетка вставлена — перевір і відправ.",
        );
    }

    function onVoiceTranscript(t: string): void {
        value = (value + " " + t).trim();
        oninputchange(value);
        tick().then(() => applyHeight());
    }

    function onEmojiInsert(e: string): void {
        value = value + e;
        oninputchange(value);
        tick().then(() => {
            applyHeight();
            textareaEl?.focus();
        });
    }

    function handleKeydown(e: KeyboardEvent): void {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            onsend();
        }
    }

    function handleInput(): void {
        applyHeight();
        oninputchange(value);
    }

    function handleFocus(): void {
        focused = true;
        onfocuschange(true);
        tick().then(() => applyHeight());
    }

    function handleBlur(): void {
        setTimeout(() => {
            focused = false;
            onfocuschange(false);
        }, 150);
    }

    const hasText = $derived(value.trim().length > 0);
    const canSend = $derived(hasText && !sending);

    $effect(() => {
        void value;
        tick().then(() => applyHeight());
    });

    onMount(() => {
        tick().then(() => applyHeight());
        const sync = () => requestAnimationFrame(() => applyHeight());
        window.visualViewport?.addEventListener("resize", sync);
        window.addEventListener("orientationchange", sync);
        return () => {
            window.visualViewport?.removeEventListener("resize", sync);
            window.removeEventListener("orientationchange", sync);
        };
    });

    onDestroy(() => {
        if (hintTimer) clearTimeout(hintTimer);
    });
</script>

<div class="bar">
    {#if hint}
        <div class="toast" role="status" aria-live="polite">{hint}</div>
    {/if}
    {#if error}
        <div class="bar-err">{error}</div>
    {/if}
    {#if voiceError}
        <div class="bar-err voice">{voiceError}</div>
    {/if}

    <div class="pill" class:focused class:has-text={hasText}>
        <textarea
            class="ta"
            bind:this={textareaEl}
            bind:value
            oninput={handleInput}
            onkeydown={handleKeydown}
            onfocus={handleFocus}
            onblur={handleBlur}
            placeholder="Повідомлення Арчі…"
            rows={1}
            enterkeyhint="enter"
            spellcheck="true"
            disabled={sending}
        ></textarea>

        <div class="trail">
            <VoiceButton
                disabled={sending}
                ontranscript={onVoiceTranscript}
                bind:error={voiceError}
            />
            <EmojiPicker disabled={sending} oninsert={onEmojiInsert} />
            <button
                class="send"
                class:armed={canSend}
                onclick={onsend}
                disabled={!canSend}
                title="Відправити (Ctrl/Cmd+Enter)"
                aria-label="Відправити повідомлення"
            >
                {#if sending}
                    <span class="spin" aria-hidden="true"></span>
                {:else}
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M5 12h14" />
                        <path d="M13 6l6 6-6 6" />
                    </svg>
                {/if}
            </button>
        </div>
    </div>
</div>

<style>
    .bar {
        position: relative;
        padding: 8px 14px 12px;
        background: linear-gradient(
            180deg,
            color-mix(in srgb, var(--bg) 70%, transparent) 0%,
            var(--surface) 52%
        );
        border-top: 1px solid var(--border);
        flex-shrink: 0;
    }

    /* Transient handoff hint — floats above the pill, fades 2.6s. */
    .toast {
        position: absolute;
        left: 14px;
        right: 14px;
        top: -28px;
        font-size: 11px;
        color: var(--accent);
        background: color-mix(in srgb, var(--accent) 14%, var(--surface));
        border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border));
        padding: 5px 10px;
        border-radius: 10px;
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22);
        animation: toastIn 0.18s ease-out;
        pointer-events: none;
    }
    @keyframes toastIn {
        from { opacity: 0; transform: translateY(4px); }
        to   { opacity: 1; transform: translateY(0); }
    }

    .bar-err {
        font-size: 11px;
        color: #ff7373;
        margin: 0 4px 6px;
        padding: 4px 10px;
        border-radius: 8px;
        background: color-mix(in srgb, #ff4d4d 10%, transparent);
        border: 1px solid color-mix(in srgb, #ff4d4d 30%, var(--border));
    }

    /* ─── The pill ──────────────────────────────────────────────── */
    .pill {
        display: flex;
        align-items: flex-end;
        gap: 4px;
        padding: 4px 6px 4px 4px;
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: 22px;
        transition:
            border-color 0.18s,
            box-shadow 0.22s,
            background 0.18s;
        box-shadow:
            0 1px 0 rgba(255, 255, 255, 0.02) inset,
            0 10px 28px rgba(0, 0, 0, 0.18);
    }
    .pill.focused {
        border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
        box-shadow:
            0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent),
            0 14px 34px rgba(0, 0, 0, 0.22);
    }

    .ta {
        flex: 1;
        min-width: 0;
        resize: none;
        border: none;
        background: transparent;
        color: var(--text);
        font-family: inherit;
        font-size: 14.5px;
        line-height: 1.5;
        padding: 10px 4px 10px 14px;
        outline: none;
        min-height: 40px;
        max-height: 144px;
        overflow-y: auto;
        caret-color: var(--accent);
    }
    .ta::placeholder { color: var(--text-muted); }
    .ta:disabled { opacity: 0.55; }

    .trail {
        display: flex;
        align-items: center;
        gap: 2px;
        padding-bottom: 2px;
        flex-shrink: 0;
    }

    /* Send button: ghost while empty, solid accent when armed. */
    .send {
        width: 34px;
        height: 34px;
        margin-left: 2px;
        border-radius: 50%;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text-muted);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition:
            background 0.15s,
            border-color 0.15s,
            color 0.15s,
            transform 0.08s;
    }
    .send:disabled { cursor: not-allowed; }
    .send.armed {
        background: var(--accent);
        border-color: var(--accent);
        color: #fff;
        box-shadow: 0 4px 12px
            color-mix(in srgb, var(--accent) 45%, transparent);
    }
    .send.armed:hover { transform: translateY(-1px); }
    .send.armed:active { transform: scale(0.94); }
    .send svg { display: block; }

    .spin {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid color-mix(in srgb, var(--text-muted) 55%, transparent);
        border-top-color: transparent;
        animation: spin 0.75s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    @media (max-width: 768px) {
        .bar {
            padding: 8px 10px calc(10px + env(safe-area-inset-bottom)) 10px;
        }
        .ta {
            font-size: 16px; /* iOS anti-zoom */
            min-height: 48px;
            padding: 12px 4px 12px 14px;
            max-height: min(28vh, 196px);
        }
        .send { width: 38px; height: 38px; }
    }
</style>
