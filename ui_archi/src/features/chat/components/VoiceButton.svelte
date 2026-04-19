<!--
    VoiceButton — голосовий ввід через Web Speech API (uk-UA).

    Інкапсулює:
      - feature detection (SpeechRecognition / webkitSpeechRecognition)
      - lifecycle (onMount init, onDestroy stop)
      - state (listening, error)

    Props:
      - disabled: boolean (блокує toggle, наприклад під час sending)

    Events:
      - ontranscript(text: string) — розпізнаний фрагмент

    Bindable:
      - error: string — останнє повідомлення про помилку ("" якщо чисто)
                        Parent може рендерити error під інпутом у бажаному місці.

    Degraded-but-loud (I7):
      - Браузер без SR API → компонент НЕ рендериться (supported=false)
      - not-allowed / network errors → error встановлюється, listening=false
-->
<script lang="ts">
    import { onMount, onDestroy } from "svelte";

    let {
        disabled = false,
        ontranscript = (_text: string): void => {},
        error = $bindable(""),
    } = $props<{
        disabled?: boolean;
        ontranscript?: (text: string) => void;
        error?: string;
    }>();

    let supported = $state(false);
    let listening = $state(false);
    let recognition: any = null;

    onMount(() => {
        const SR =
            (window as any).SpeechRecognition ||
            (window as any).webkitSpeechRecognition;
        if (!SR) {
            supported = false;
            return;
        }
        supported = true;
        recognition = new SR();
        recognition.lang = "uk-UA";
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onresult = (e: any) => {
            const transcript = e.results[0]?.[0]?.transcript ?? "";
            if (transcript) ontranscript(transcript);
        };
        recognition.onerror = (e: any) => {
            error =
                e.error === "not-allowed" ? "Мікрофон заблоковано" : e.error;
            listening = false;
        };
        recognition.onend = () => {
            listening = false;
        };
    });

    onDestroy(() => {
        if (recognition && listening) {
            try {
                recognition.stop();
            } catch {
                // degraded-but-loud not required — browser stop() ідемпотентний
            }
        }
    });

    function toggle(): void {
        if (!supported || !recognition || disabled) return;
        if (listening) {
            recognition.stop();
            listening = false;
        } else {
            error = "";
            try {
                recognition.start();
                listening = true;
            } catch (err: any) {
                error = err?.message ?? "Не вдалося запустити мікрофон";
                listening = false;
            }
        }
    }
</script>

{#if supported}
    <button
        class="ia-btn"
        class:recording={listening}
        onclick={toggle}
        {disabled}
        title={listening ? "Зупинити" : "Голос"}
        aria-label={listening ? "Зупинити запис" : "Розпочати голосовий ввід"}
    >
        {listening ? "🔴" : "🎤"}
    </button>
{/if}

<style>
    .ia-btn {
        width: 38px;
        height: 38px;
        border-radius: 50%;
        border: none;
        background: none;
        cursor: pointer;
        font-size: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--text-muted);
        transition: background 0.15s, color 0.15s;
    }
    .ia-btn:hover {
        background: var(--surface2);
        color: var(--text);
    }
    .ia-btn.recording {
        color: #e05555;
        animation: rec-pulse 1s ease infinite;
    }
    @keyframes rec-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(224, 85, 85, 0.35); }
        50% { box-shadow: 0 0 0 6px rgba(224, 85, 85, 0); }
    }
    @media (max-width: 768px) {
        .ia-btn { width: 42px; height: 42px; }
    }
</style>
