<!--
    InputBar — композитор інпут-рядка: textarea + QuickActions + VoiceButton +
    EmojiPicker + Send button.

    Props:
      - value: bindable string (поточний текст чернетки)
      - sending: boolean (блокує textarea + send button)
      - error: string (показується у .input-error banner — приходить з chatStore)
      - contextActions: QuickAction[] (рендеряться коли !focused && !value.trim())

    Events:
      - onsend() — Ctrl/Cmd+Enter або клік по send
      - oninputchange(text) — кожна зміна value (для ondraftchange у parent)
      - onfocuschange(focused) — зміна фокусу textarea

    Exposed via bind:this:
      - primeDraft(text)  — append до value + hint "чернетка додана"
      - setDraft(text, hint) — replace value з custom hint (для handoff restore)
      - focus() / applyHeight() / resetHeight() — DOM-level operations

    Mobile:
      - Textarea wraps на свій ряд (flex-basis: 100%), actions wrap нижче.
      - Font-size 16px щоб iOS Safari не zoom-ив при focus.
-->
<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte";
    import QuickActions, { type QuickAction } from "./QuickActions.svelte";
    import VoiceButton from "./VoiceButton.svelte";
    import EmojiPicker from "./EmojiPicker.svelte";

    let {
        value = $bindable(""),
        sending = false,
        error = "",
        contextActions = [],
        onsend = (): void => {},
        oninputchange = (_text: string): void => {},
        onfocuschange = (_focused: boolean): void => {},
    } = $props<{
        value?: string;
        sending?: boolean;
        error?: string;
        contextActions?: QuickAction[];
        onsend?: () => void;
        oninputchange?: (text: string) => void;
        onfocuschange?: (focused: boolean) => void;
    }>();

    const DESKTOP_MAX = 140;
    const MOBILE_MIN = 52;
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
    function getComposerMaxHeight(): number {
        const vh = window.visualViewport?.height ?? window.innerHeight;
        return Math.round(Math.min(vh * 0.28, MOBILE_MAX));
    }

    export function applyHeight(): void {
        if (!textareaEl) return;
        const mobile = isMobileViewport();
        const min = mobile ? MOBILE_MIN : 44;
        const max = mobile ? getComposerMaxHeight() : DESKTOP_MAX;
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

    function setHint(text: string): void {
        hint = text;
        if (hintTimer) clearTimeout(hintTimer);
        hintTimer = setTimeout(() => {
            hint = "";
            hintTimer = null;
        }, 2600);
    }

    /** Replace draft entirely + custom hint. Used for handoff restore. */
    export function setDraft(text: string, hintText: string): void {
        value = text;
        oninputchange(text);
        setHint(hintText);
        tick().then(() => {
            applyHeight();
            textareaEl?.focus();
            const caret = value.length;
            textareaEl?.setSelectionRange?.(caret, caret);
        });
    }

    /** Append to draft OR replace if empty. Default hint depending on existing draft. */
    export function primeDraft(text: string): void {
        const existing = value.trim();
        const nextText = existing ? `${value}\n\n${text}` : text;
        setDraft(
            nextText,
            existing
                ? "Команду додано в чернетку. Відправка лишається ручною."
                : "Чернетка вставлена. Перевір і відправ вручну.",
        );
    }

    function onQuickActionSelect(text: string): void {
        primeDraft(text);
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

    // Re-apply height коли value змінюється ззовні (primeDraft/setDraft/clear).
    // applyHeight ідемпотентний — internal oninput шлях теж тригерить, це OK.
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

<div class="input-bar" class:focused>
    {#if !focused && !value.trim() && contextActions.length > 0}
        <QuickActions
            actions={contextActions}
            compact={true}
            onselect={onQuickActionSelect}
        />
    {/if}
    {#if error}
        <div class="input-error">{error}</div>
    {/if}
    <div class="input-hint" class:accent={!!hint}>
        {hint || "Enter — новий рядок · Ctrl/Cmd+Enter — відправити"}
    </div>
    <div class="input-row">
        <textarea
            class="chat-input"
            bind:this={textareaEl}
            bind:value
            oninput={handleInput}
            onkeydown={handleKeydown}
            onfocus={handleFocus}
            onblur={handleBlur}
            placeholder="Повідомлення…"
            rows={1}
            enterkeyhint="enter"
            spellcheck="true"
            disabled={sending}
        ></textarea>

        <div class="input-actions">
            <VoiceButton
                disabled={sending}
                ontranscript={onVoiceTranscript}
                bind:error={voiceError}
            />
            <EmojiPicker disabled={sending} oninsert={onEmojiInsert} />
            <button
                class="btn-send"
                onclick={onsend}
                disabled={sending || !value.trim()}
                title="Відправити (Ctrl/Cmd+Enter)"
                aria-label="Відправити повідомлення"
            >
                {sending ? "⏳" : "➤"}
            </button>
        </div>
    </div>
    {#if voiceError}
        <div class="voice-error">{voiceError}</div>
    {/if}
</div>

<style>
    .input-bar {
        padding: 8px 12px 12px;
        background: var(--surface);
        border-top: 1px solid var(--border);
        flex-shrink: 0;
        box-shadow: 0 -14px 32px rgba(0, 0, 0, 0.18);
    }
    .input-bar.focused {
        box-shadow: 0 -18px 36px rgba(0, 0, 0, 0.24);
    }

    .input-error {
        font-size: 12px;
        color: #e05555;
        margin-bottom: 4px;
        padding: 0 4px;
    }
    .input-hint {
        font-size: 11px;
        color: var(--text-muted);
        margin-bottom: 6px;
        padding: 0 4px;
    }
    .input-hint.accent { color: var(--accent); }
    .voice-error {
        font-size: 11px;
        color: #e05555;
        margin-top: 4px;
        padding: 0 4px;
    }

    .input-row {
        display: flex;
        align-items: flex-end;
        gap: 8px;
    }

    .chat-input {
        flex: 1;
        resize: none;
        border: 1px solid var(--border);
        border-radius: 22px;
        background: var(--bg);
        color: var(--text);
        font-family: inherit;
        font-size: 15px;
        line-height: 1.45;
        padding: 10px 16px;
        outline: none;
        transition: border-color 0.2s;
        min-height: 44px;
        max-height: 140px;
        overflow-y: auto;
    }
    .chat-input:focus { border-color: var(--accent); }
    .chat-input:disabled { opacity: 0.6; }
    .chat-input::placeholder { color: var(--text-muted); }

    .input-actions {
        display: flex;
        align-items: flex-end;
        gap: 4px;
        flex-shrink: 0;
    }

    .btn-send {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        border: none;
        background: var(--accent);
        color: #fff;
        cursor: pointer;
        font-size: 17px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition: opacity 0.15s, transform 0.1s;
    }
    .btn-send:disabled {
        opacity: 0.35;
        cursor: not-allowed;
    }
    .btn-send:not(:disabled):hover {
        opacity: 0.85;
        transform: scale(1.06);
    }

    @media (max-width: 768px) {
        .input-bar {
            padding: 8px 10px calc(10px + env(safe-area-inset-bottom)) 10px;
        }
        .input-row {
            flex-wrap: wrap;
            gap: 6px;
        }
        .chat-input {
            flex-basis: 100%;
            max-height: min(28vh, 196px);
            min-height: 52px;
            font-size: 16px; /* prevents iOS zoom on focus */
            border-radius: 18px;
            padding: 14px 16px;
        }
        .input-actions {
            width: 100%;
            justify-content: flex-end;
            gap: 8px;
        }
        .btn-send { width: 44px; height: 44px; }
    }
</style>
