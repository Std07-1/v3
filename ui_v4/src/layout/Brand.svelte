<!--
  src/layout/Brand.svelte — ADR-0066 Tier 1-3 + Tier 9
  Brand identity component: wordmark / mark / full lockup.
  Theme-aware via CSS variables (Tier 9.1: dot color shifts dark/black/light).
-->

<script lang="ts">
    interface Props {
        variant?: "wordmark" | "mark" | "lockup";
        size?: number; // px height for mark, px font-size for wordmark/lockup
        clickable?: boolean;
        onclick?: (e: MouseEvent) => void;
        title?: string;
    }

    const {
        variant = "wordmark",
        size = 32,
        clickable = false,
        onclick,
        title = "AI · ONE v3",
    }: Props = $props();

    // Wordmark font-size scales with `size` prop. Default 32px = canonical.
    const wmSize = $derived(size);
    const v3Size = $derived(Math.round(size * 0.4));   // ~13px @ canon 32
    const tagSize = $derived(Math.round(size * 0.34)); // ~11px @ canon 32

    function handleClick(e: MouseEvent) {
        if (onclick) onclick(e);
    }
</script>

{#if variant === "mark"}
    <!-- Mark only — geometric V3 per Tier 3 -->
    <span
        class="brand-mark"
        class:clickable
        role={clickable ? "button" : undefined}
        tabindex={clickable ? 0 : undefined}
        onclick={clickable ? handleClick : undefined}
        onkeydown={clickable ? (e) => e.key === "Enter" && handleClick(e as any) : undefined}
        style:--mark-size="{size}px"
        {title}
    >
        <svg viewBox="0 0 64 64" role="img" aria-label={title}>
            <defs>
                <linearGradient id="m3grad-{size}" x1="0" y1="64" x2="64" y2="0" gradientUnits="userSpaceOnUse">
                    <stop offset="0%" stop-color="var(--accent)" />
                    <stop offset="100%" stop-color="var(--bull, #22CC8F)" />
                </linearGradient>
            </defs>
            <path
                fill="url(#m3grad-{size})"
                fill-rule="evenodd"
                d="M 9 14 L 15 14 L 32 42 L 49 14 L 55 14 L 32 51 Z"
            />
            <path
                fill="none"
                stroke="url(#m3grad-{size})"
                stroke-width="2.6"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M 39 30 Q 49 30 49 35 Q 49 39 44 39 Q 49 39 49 43 Q 49 48 39 48"
            />
            <circle cx="32" cy="51" r="1.8" fill="var(--bull, #22CC8F)" />
        </svg>
    </span>
{:else if variant === "wordmark"}
    <!-- Wordmark only — AI · ONE per Tier 2 -->
    <span
        class="brand-wordmark"
        class:clickable
        role={clickable ? "button" : undefined}
        tabindex={clickable ? 0 : undefined}
        onclick={clickable ? handleClick : undefined}
        onkeydown={clickable ? (e) => e.key === "Enter" && handleClick(e as any) : undefined}
        style:font-size="{wmSize}px"
        {title}
    >
        <span class="wm-ai">AI</span>
        <span class="wm-dot">·</span>
        <span class="wm-one">ONE</span>
    </span>
{:else}
    <!-- Full lockup — wordmark + v3 + tagline -->
    <span
        class="brand-lockup"
        class:clickable
        role={clickable ? "button" : undefined}
        tabindex={clickable ? 0 : undefined}
        onclick={clickable ? handleClick : undefined}
        onkeydown={clickable ? (e) => e.key === "Enter" && handleClick(e as any) : undefined}
        {title}
    >
        <span class="lk-row">
            <span class="brand-wordmark" style:font-size="{wmSize}px">
                <span class="wm-ai">AI</span>
                <span class="wm-dot">·</span>
                <span class="wm-one">ONE</span>
            </span>
            <span class="lk-v3" style:font-size="{v3Size}px">v3</span>
        </span>
        <span class="lk-tagline" style:font-size="{tagSize}px">
            Smart Money Concepts · agent-led trading platform
        </span>
    </span>
{/if}

<style>
    /* ─── Mark variant ─── */
    .brand-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: var(--mark-size, 32px);
        height: var(--mark-size, 32px);
        flex-shrink: 0;
    }
    .brand-mark svg {
        width: 100%;
        height: 100%;
        display: block;
    }
    .brand-mark.clickable {
        cursor: pointer;
        transition: transform 0.15s ease;
    }
    .brand-mark.clickable:hover {
        transform: scale(1.05);
    }
    .brand-mark.clickable:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
        border-radius: 4px;
    }

    /* ─── Wordmark variant — Tier 2 spec ─── */
    .brand-wordmark {
        display: inline-flex;
        align-items: baseline;
        gap: 0;
        font-family: var(--font-sans, "Inter", -apple-system, BlinkMacSystemFont, sans-serif);
        font-weight: 800;
        letter-spacing: -0.015em;
        line-height: 1;
        white-space: nowrap;
        user-select: none;
    }
    .wm-ai {
        color: var(--text-1);
    }
    .wm-dot {
        /* Tier 9.1: gold on dark/black, accent-deep on light handled via theme overrides */
        color: var(--accent);
        padding: 0 0.22em;
        line-height: 1;
    }
    .wm-one {
        color: var(--text-2);
        opacity: 0.75;
    }
    .brand-wordmark.clickable {
        cursor: pointer;
        padding: 2px 6px;
        margin: -2px -6px;
        border-radius: 6px;
        transition: background 0.15s ease;
    }
    .brand-wordmark.clickable:hover {
        background: color-mix(in srgb, var(--accent) 8%, transparent);
    }
    .brand-wordmark.clickable:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }

    /* ─── Light theme: dot uses --accent-deep for AA contrast on white ─── */
    :global([data-theme="light"]) .wm-dot {
        color: var(--accent-deep);
    }

    /* ─── Lockup variant ─── */
    .brand-lockup {
        display: inline-flex;
        flex-direction: column;
        gap: 4px;
        font-family: var(--font-sans);
    }
    .lk-row {
        display: inline-flex;
        align-items: baseline;
        gap: 8px;
    }
    .lk-v3 {
        font-family: var(--font-mono);
        font-weight: 500;
        color: var(--text-3);
        letter-spacing: 0.05em;
    }
    .lk-tagline {
        font-family: var(--font-mono);
        font-weight: 400;
        color: var(--text-3);
        letter-spacing: 0.02em;
    }
    .brand-lockup.clickable {
        cursor: pointer;
        padding: 4px 8px;
        margin: -4px -8px;
        border-radius: 6px;
        transition: background 0.15s ease;
    }
    .brand-lockup.clickable:hover {
        background: color-mix(in srgb, var(--accent) 8%, transparent);
    }
    .brand-lockup.clickable:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }
</style>
