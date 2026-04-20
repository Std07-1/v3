<!--
    ChatTopbar — тонкий sticky topbar для нового Chat shell (ADR-0053 S1).

    Задача: на відміну від попереднього ChatHeader, тут нема Mode/Pulse/Pinned
    — лише ім'я, focus pill (symbol · bias · session) і live-dot свіжості
    shell snapshot. Dual Mode + Pulse Board переїхали у Mind (S2).

    Props:
      - focusSymbol?: string       — e.g. "XAUUSD"
      - biasLabel?: string         — "H4: bull" / "H4: bear" / ""
      - biasColor?: "bull" | "bear" | ""
      - session?: string           — market_session (London/NY/Asia/Closed)
      - liveFresh: boolean         — true → зелений live-dot, false → сірий
      - ttsSupported: boolean
      - ttsAuto: boolean
      - ontoggletts?: () => void
-->
<script lang="ts">
    let {
        focusSymbol = "",
        biasLabel = "",
        biasColor = "",
        session = "",
        liveFresh = false,
        ttsSupported = false,
        ttsAuto = false,
        ontoggletts = (): void => {},
    } = $props<{
        focusSymbol?: string;
        biasLabel?: string;
        biasColor?: "bull" | "bear" | "";
        session?: string;
        liveFresh?: boolean;
        ttsSupported?: boolean;
        ttsAuto?: boolean;
        ontoggletts?: () => void;
    }>();

    const hasPill = $derived(
        !!(focusSymbol || biasLabel || session),
    );
</script>

<div class="topbar">
    <div class="tb-left">
        <span class="tb-title">Арчі</span>
        {#if hasPill}
            <span class="tb-pill">
                {#if focusSymbol}<span class="tb-symbol">{focusSymbol}</span>{/if}
                {#if biasLabel}
                    <span class="tb-dot"></span>
                    <span class="tb-bias {biasColor}">{biasLabel}</span>
                {/if}
                {#if session}
                    <span class="tb-dot"></span>
                    <span class="tb-session">{session}</span>
                {/if}
                <span
                    class="tb-live"
                    class:fresh={liveFresh}
                    title={liveFresh ? "Свіжий snapshot" : "Stale snapshot"}
                ></span>
            </span>
        {/if}
    </div>
    <div class="tb-right">
        {#if ttsSupported}
            <button
                class="tb-tts"
                class:active={ttsAuto}
                onclick={ontoggletts}
                title={ttsAuto
                    ? "Вимкнути авто-озвучення"
                    : "Увімкнути авто-озвучення"}
                aria-label="Toggle TTS"
            >{ttsAuto ? "🔊" : "🔇"}</button>
        {/if}
    </div>
</div>

<style>
    .topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        height: 44px;
        padding: 0 14px;
        border-bottom: 1px solid var(--border);
        background: color-mix(in srgb, var(--surface) 94%, var(--bg));
        flex-shrink: 0;
        position: sticky;
        top: 0;
        z-index: 5;
    }
    .tb-left {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
        overflow: hidden;
    }
    .tb-title {
        font-size: 14px;
        font-weight: 650;
        color: var(--text);
        letter-spacing: 0.01em;
    }
    .tb-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 3px 10px;
        border-radius: 999px;
        background: var(--surface2);
        border: 1px solid var(--border);
        font-size: 11px;
        color: var(--text-muted);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
    }
    .tb-symbol {
        font-weight: 700;
        color: var(--text);
        letter-spacing: 0.04em;
    }
    .tb-dot {
        width: 3px;
        height: 3px;
        border-radius: 50%;
        background: currentColor;
        opacity: 0.4;
        flex-shrink: 0;
    }
    .tb-bias {
        font-weight: 600;
        text-transform: lowercase;
    }
    .tb-bias.bull { color: #28c864; }
    .tb-bias.bear { color: #e05555; }
    .tb-session {
        font-weight: 500;
    }
    .tb-live {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--text-muted);
        opacity: 0.35;
        margin-left: 2px;
        flex-shrink: 0;
        transition: background 0.25s, opacity 0.25s;
    }
    .tb-live.fresh {
        background: #28c864;
        opacity: 1;
        box-shadow: 0 0 6px rgba(40, 200, 100, 0.45);
        animation: tb-pulse 2.2s ease-in-out infinite;
    }
    @keyframes tb-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.55; transform: scale(0.85); }
    }
    .tb-right {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-shrink: 0;
    }
    .tb-tts {
        background: none;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 4px 8px;
        color: var(--text-muted);
        cursor: pointer;
        font-size: 13px;
        transition: color 0.15s, border-color 0.15s, background 0.15s;
    }
    .tb-tts:hover { color: var(--text); border-color: var(--border); }
    .tb-tts.active {
        color: var(--accent);
        border-color: color-mix(in srgb, var(--accent) 50%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, transparent);
    }

    @media (max-width: 520px) {
        .tb-session { display: none; }
    }
</style>
