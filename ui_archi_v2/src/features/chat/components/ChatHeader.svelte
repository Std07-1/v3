<!--
    ChatHeader — dumb header для Chat view.

    Відповідає за: mood orb, title, focus symbol badge, bias badge, TTS pill.

    Props:
      - mood?: string         — directives.mood (calm/focused/alert/stressed/cautious/excited)
      - focusSymbol?: string  — directives.focus_symbol (XAUUSD, BTCUSDT, etc.)
      - biasLabel?: string    — e.g. "H4: bull"
      - biasColor?: string    — "bull" | "bear" | ""
      - ttsSupported: boolean — якщо false → TTS pill не рендериться
      - ttsAuto: boolean      — active state pill
      - ontoggletts?: () => void  — клік по TTS pill
-->
<script lang="ts">
    let {
        mood = "",
        focusSymbol = "",
        biasLabel = "",
        biasColor = "",
        ttsSupported = false,
        ttsAuto = false,
        ontoggletts = (): void => {},
    } = $props<{
        mood?: string;
        focusSymbol?: string;
        biasLabel?: string;
        biasColor?: string;
        ttsSupported?: boolean;
        ttsAuto?: boolean;
        ontoggletts?: () => void;
    }>();
</script>

<div class="chat-header">
    <div class="ch-left">
        {#if mood}
            <span class="ch-mood-orb" data-mood={mood}></span>
        {/if}
        <h2 class="ch-title">Чат</h2>
        {#if focusSymbol}
            <span class="ch-symbol">{focusSymbol}</span>
        {/if}
        {#if biasLabel}
            <span class="ch-bias {biasColor}">{biasLabel}</span>
        {/if}
    </div>
    <div class="ch-right">
        {#if ttsSupported}
            <button
                class="tts-pill"
                class:active={ttsAuto}
                onclick={ontoggletts}
                title={ttsAuto
                    ? "Вимкнути авто-озвучення (браузерне, лише у відкритій вкладці)"
                    : "Увімкнути авто-озвучення (браузерне, лише у відкритій вкладці)"}
            >
                {ttsAuto ? "🔊 Авто" : "🔇"}
            </button>
        {/if}
    </div>
</div>

<style>
    .chat-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 16px 12px;
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        background: var(--surface);
    }
    .ch-left {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
    }
    .ch-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--text);
        margin: 0;
    }
    .ch-mood-orb {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--accent);
        animation: ch-pulse 2.5s ease-in-out infinite;
        flex-shrink: 0;
    }
    .ch-mood-orb[data-mood="calm"] {
        background: #60a5fa;
        animation-duration: 3s;
    }
    .ch-mood-orb[data-mood="focused"] { background: #34d399; }
    .ch-mood-orb[data-mood="alert"] {
        background: #fbbf24;
        animation-duration: 1.5s;
    }
    .ch-mood-orb[data-mood="stressed"] {
        background: #f87171;
        animation-duration: 1s;
    }
    .ch-mood-orb[data-mood="cautious"] { background: #fb923c; }
    .ch-mood-orb[data-mood="excited"] {
        background: #c084fc;
        animation-duration: 1.2s;
    }
    @keyframes ch-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.5; transform: scale(0.7); }
    }
    .ch-symbol {
        font-size: 11px;
        font-weight: 700;
        color: var(--text-muted);
        letter-spacing: 0.04em;
        padding: 2px 7px;
        background: var(--surface2);
        border-radius: 6px;
    }
    .ch-bias {
        font-size: 10px;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 10px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    .ch-bias.bull {
        background: rgba(40, 200, 100, 0.12);
        color: #28c864;
    }
    .ch-bias.bear {
        background: rgba(220, 60, 60, 0.12);
        color: #e05555;
    }
    .ch-right {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 0;
    }

    .tts-pill {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 12px;
        border-radius: 16px;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text-muted);
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s ease;
        white-space: nowrap;
    }
    .tts-pill:hover {
        border-color: var(--accent);
        color: var(--text);
    }
    .tts-pill.active {
        background: var(--accent);
        color: #fff;
        border-color: var(--accent);
        box-shadow: 0 0 12px rgba(124, 111, 255, 0.3);
    }
</style>
