<!--
  src/layout/Brand.svelte — ADR-0066 Tier 1-3 + Tier 9
  Brand identity component: mark / lockup.
  Бренд — «v3» (ADR-0066 rev 6, рішення власника 10.10: «AI · ONE» прибрано зі згадок і назв). Знак V3 і є назвою,
  тож lockup = знак + tagline, без окремого надпису «v3» (рішення власника 11.10: «нижня v3 не потрібна — має
  виглядати гарно й консистентно»): заставка і шапка «Про» показують один і той самий блок.
-->

<script lang="ts">
    interface Props {
        variant?: "mark" | "lockup";
        size?: number; // px — висота знака (в lockup теж)
        clickable?: boolean;
        onclick?: (e: MouseEvent) => void;
        title?: string;
    }

    const {
        variant = "mark",
        size = 32,
        clickable = false,
        onclick,
        title = "v3",
    }: Props = $props();

    function handleClick(e: MouseEvent) {
        if (onclick) onclick(e);
    }
</script>

<!-- V3 mark-v4.svg (owner redesign 2026-05-12): precise contour trace, gold→teal gradient + dark canvas background
     built into SVG. Single source of truth — favicon + Brand component identical pixel-for-pixel. -->
{#snippet markImage()}
    <img src="/brand/mark-v4.svg" alt={title} width={size} height={size} />
{/snippet}

{#if variant === "mark"}
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
        {@render markImage()}
    </span>
{:else}
    <!-- Lockup — знак V3 + tagline -->
    <span
        class="brand-lockup"
        class:clickable
        role={clickable ? "button" : undefined}
        tabindex={clickable ? 0 : undefined}
        onclick={clickable ? handleClick : undefined}
        onkeydown={clickable ? (e) => e.key === "Enter" && handleClick(e as any) : undefined}
        style:--mark-size="{size}px"
        {title}
    >
        <span class="brand-mark">{@render markImage()}</span>
        <span class="lk-tagline">Smart Money Concepts · agent-led trading platform</span>
    </span>
{/if}

<style>
    /* ─── Mark ─── */
    .brand-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: var(--mark-size, 32px);
        height: var(--mark-size, 32px);
        flex-shrink: 0;
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

    /* ─── Lockup ─── */
    .brand-lockup {
        display: inline-flex;
        flex-direction: column;
        /* align-items:center — знак і tagline різної ширини; без center блок виглядав би off-center
           (owner-flagged 2026-05-11). */
        align-items: center;
        /* Відступ росте зі знаком: заставка (72) — 18px, як було між знаком і блоком; шапка «Про» — менше. */
        gap: calc(var(--mark-size, 32px) * 0.25);
        font-family: var(--font-sans);
    }
    .lk-tagline {
        font-family: var(--font-mono);
        font-size: var(--t3-size);
        font-weight: 400;
        color: var(--text-3);
        letter-spacing: 0.02em;
        /* text-align:center страхує коли tagline wrap-иться на 2 рядки —
           кожен рядок остається центрованим, не left-aligned. */
        text-align: center;
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
