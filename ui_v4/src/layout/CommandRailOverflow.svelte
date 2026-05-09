<!--
  src/layout/CommandRailOverflow.svelte — ADR-0065 rev 2 Tier 2
  Dropdown menu anchored under the ☰ button in the top-right command rail.
  Houses low-frequency intra-session controls relocated out of inline rail:
    Theme ▸ submenu
    Style ▸ submenu
    Brightness scroll-row (wheel anywhere over the row)
  Closes on: outside click, ESC, item activation (after action), focus loss.
  Note (2026-05-09): Diagnostics item removed — доступ через
  brand watermark click → InfoModal[Diagnostics] або Ctrl+Shift+D.
-->

<script lang="ts">
    import type { ThemeName, CandleStyleName } from "../chart/lwc";
    import {
        THEMES,
        THEME_NAMES,
        CANDLE_STYLES,
        CANDLE_STYLE_NAMES,
        resolveCandleStyle,
    } from "../chart/lwc";

    interface Props {
        open: boolean;
        activeTheme: ThemeName;
        activeStyle: CandleStyleName;
        brightness: number;
        onSelectTheme: (t: ThemeName) => void;
        onSelectStyle: (s: CandleStyleName) => void;
        onBrightnessWheel: (e: WheelEvent) => void;
        onClose: () => void;
    }

    const {
        open,
        activeTheme,
        activeStyle,
        brightness,
        onSelectTheme,
        onSelectStyle,
        onBrightnessWheel,
        onClose,
    }: Props = $props();

    type SubmenuKey = "theme" | "style" | null;
    let openSubmenu = $state<SubmenuKey>(null);

    // Reset submenu state whenever menu closes.
    $effect(() => {
        if (!open) openSubmenu = null;
    });

    function handleThemePick(t: ThemeName) {
        onSelectTheme(t);
        onClose();
    }
    function handleStylePick(s: CandleStyleName) {
        onSelectStyle(s);
        onClose();
    }

    function handleKeydown(e: KeyboardEvent) {
        if (!open) return;
        if (e.key === "Escape") {
            e.preventDefault();
            onClose();
        }
    }

    // Brightness LED row: 5 dots, lit count = brightness scaled across [0.8, 1.2].
    const litCount = $derived.by(() => {
        const t = (brightness - 0.8) / (1.2 - 0.8);
        return Math.max(1, Math.min(5, Math.round(t * 5) + 1));
    });
    const brightnessPct = $derived(Math.round(brightness * 100));
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="overflow-menu"
        role="menu"
        aria-label="More options"
        onclick={(e) => e.stopPropagation()}
    >
        <!-- Theme submenu -->
        <div class="menu-item-row">
            <button
                class="menu-item"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={openSubmenu === "theme"}
                onclick={() =>
                    (openSubmenu = openSubmenu === "theme" ? null : "theme")}
            >
                <span class="mi-label">Theme</span>
                <span class="mi-current">{THEMES[activeTheme].label}</span>
                <span class="mi-chevron">▸</span>
            </button>
            {#if openSubmenu === "theme"}
                <div class="submenu" role="menu">
                    {#each THEME_NAMES as t}
                        <button
                            class="submenu-item"
                            class:active={t === activeTheme}
                            role="menuitemradio"
                            aria-checked={t === activeTheme}
                            onclick={() => handleThemePick(t)}
                        >
                            {THEMES[t].label}
                        </button>
                    {/each}
                </div>
            {/if}
        </div>

        <!-- Style submenu -->
        <div class="menu-item-row">
            <button
                class="menu-item"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={openSubmenu === "style"}
                onclick={() =>
                    (openSubmenu = openSubmenu === "style" ? null : "style")}
            >
                <span class="mi-label">Style</span>
                <span class="mi-current"
                    >{CANDLE_STYLES[activeStyle].label}</span
                >
                <span class="mi-chevron">▸</span>
            </button>
            {#if openSubmenu === "style"}
                <div class="submenu" role="menu">
                    {#each CANDLE_STYLE_NAMES as cs}
                        <button
                            class="submenu-item"
                            class:active={cs === activeStyle}
                            role="menuitemradio"
                            aria-checked={cs === activeStyle}
                            onclick={() => handleStylePick(cs)}
                        >
                            <span
                                class="swatch"
                                style:background={resolveCandleStyle(
                                    cs,
                                    activeTheme,
                                ).upColor}
                            ></span>
                            {CANDLE_STYLES[cs].label}
                        </button>
                    {/each}
                </div>
            {/if}
        </div>

        <!-- Brightness row: scroll anywhere over the row to adjust. -->
        <div
            class="menu-item brightness-row"
            role="menuitem"
            tabindex="0"
            onwheel={onBrightnessWheel}
            title="Brightness {brightnessPct}% — scroll to adjust"
        >
            <span class="mi-label">Brightness</span>
            <span
                class="brightness-leds"
                aria-label="Brightness {brightnessPct}%"
            >
                {#each [1, 2, 3, 4, 5] as i}
                    <span class="led" class:lit={i <= litCount}></span>
                {/each}
            </span>
        </div>
    </div>
{/if}

<style>
    .overflow-menu {
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        z-index: 100;
        min-width: 200px;
        max-width: 240px;
        max-height: min(360px, 60vh);
        overflow-y: auto;
        padding: 4px;
        background: var(--card, rgba(30, 34, 45, 0.96));
        border: 1px solid var(--border, rgba(255, 255, 255, 0.08));
        border-radius: 8px;
        box-shadow: var(--elev, 0 8px 24px rgba(0, 0, 0, 0.4));
        backdrop-filter: blur(12px);
        font-family: var(
            --font-sans,
            -apple-system,
            BlinkMacSystemFont,
            sans-serif
        );
    }
    .menu-item-row {
        position: relative;
    }
    .menu-item {
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
    .menu-item:hover,
    .menu-item:focus-visible {
        background: color-mix(in srgb, var(--accent) 8%, transparent);
        color: var(--text-1);
    }
    .menu-item:focus-visible {
        outline: 1px solid var(--accent);
        outline-offset: -1px;
    }
    .mi-label {
        flex: 1;
        font-weight: 500;
        letter-spacing: 0.01em;
    }
    .mi-current {
        font-size: var(--t4-size, 11px);
        color: var(--text-3, #6d7080);
        font-family: var(--font-mono);
    }
    .mi-chevron {
        font-size: var(--t4-size, 11px);
        color: var(--text-3, #6d7080);
        opacity: 0.7;
    }
    .mi-hint {
        font-size: var(--t4-size, 11px);
        color: var(--text-3, #6d7080);
        font-family: var(--font-mono);
        opacity: 0.7;
    }
    .submenu {
        margin: 2px 0 4px 8px;
        padding: 3px;
        background: color-mix(in srgb, var(--bg) 50%, transparent);
        border-radius: 6px;
        border-left: 2px solid var(--accent-soft, rgba(212, 160, 23, 0.4));
    }
    .submenu-item {
        all: unset;
        display: flex;
        align-items: center;
        gap: 6px;
        width: 100%;
        box-sizing: border-box;
        padding: 5px 8px;
        font-size: var(--t3-size, 12px);
        color: var(--text-2);
        cursor: pointer;
        border-radius: 4px;
    }
    .submenu-item:hover,
    .submenu-item:focus-visible {
        background: color-mix(in srgb, var(--accent) 10%, transparent);
        color: var(--text-1);
    }
    .submenu-item.active {
        background: color-mix(in srgb, var(--accent) 18%, transparent);
        color: var(--accent);
        font-weight: 600;
    }
    .swatch {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 2px;
        flex-shrink: 0;
    }
    .brightness-row {
        cursor: ns-resize;
    }
    .brightness-leds {
        display: inline-flex;
        align-items: center;
        gap: 3px;
    }
    .led {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--text-3, #6d7080);
        opacity: 0.35;
        transition:
            opacity 0.12s ease,
            background 0.12s ease;
    }
    .led.lit {
        background: var(--accent, #d4a017);
        opacity: 1;
    }
</style>
