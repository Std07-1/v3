<!--
  src/layout/Splash.svelte — ADR-0066 PATCH 04b (Tier 6 slot 4)
  Brand lockup overlay shown during initial WS warming (cold-load).
  Disappears on first render frame OR when stale > 3s (stale fallback so
  splash never blocks chart visibility on slow WS).
-->

<script lang="ts">
    import Brand from "./Brand.svelte";

    interface Props {
        /** Whether to show splash. App.svelte computes this from
         *  initial state (no first frame yet) AND non-fatal status. */
        visible: boolean;
    }

    const { visible }: Props = $props();

    // Splash UX:
    //   - Appears immediately when `visible` first becomes true.
    //   - STAYS visible for at least MIN_SHOW_MS so user perceives brand
    //     identity even if WS connects in <50ms (which it usually does
    //     locally — without this guarantee splash flashes invisibly).
    //   - Dismisses MAX_SHOW_MS after first appear regardless, so a
    //     never-arriving WS doesn't stick splash forever.
    //   - Once dismissed, never re-shows in this session (reconnect UX
    //     belongs to StatusOverlay, not splash).
    const MIN_SHOW_MS = 800;
    const MAX_SHOW_MS = 5000;

    let appeared = $state(false);
    let dismissed = $state(false);

    // Single-shot effect: arms a one-way timer the first time `visible` becomes
    // true. We DO NOT return a cleanup — Svelte 5 re-runs effects on any state
    // change inside (including our own `appeared = true`), and cleanup would
    // clear the timer immediately, leaving the splash stuck on screen.
    // The timer is harmless if component unmounts (state is gone with it).
    $effect(() => {
        if (visible && !appeared) {
            appeared = true;
            setTimeout(() => {
                dismissed = true;
            }, MIN_SHOW_MS);
            setTimeout(() => {
                dismissed = true;
            }, MAX_SHOW_MS);
        }
    });

    let renderSplash = $derived(appeared && !dismissed);
</script>

{#if renderSplash}
    <div class="splash" role="status" aria-live="polite" aria-busy="true">
        <div class="splash-content">
            <!-- ADR-0066 PATCH 03 slot 2: V3 mark-v4 (gold→teal gradient).
                 Stacked above lockup для full brand identity на cold-load. -->
            <Brand variant="mark" size={72} />
            <Brand variant="lockup" size={32} />
            <div class="spinner" aria-hidden="true">
                <span class="spinner-dot"></span>
                <span class="spinner-dot"></span>
                <span class="spinner-dot"></span>
            </div>
            <p class="status-text">Connecting…</p>
        </div>
    </div>
{/if}

<style>
    .splash {
        position: fixed;
        inset: 0;
        /* z-index 9999: covers HUD (35), SMC overlay tooltips (~100),
           InfoModal backdrop (200), DiagPanel — splash MUST be top-most
           or partial chart/overlay bleeds through. */
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
        /* Fully opaque solid bg — never expose chart canvas behind. */
        background: var(--bg);
        opacity: 1;
        animation: splash-fade-in 200ms ease;
        pointer-events: auto;
    }

    @keyframes splash-fade-in {
        from {
            opacity: 0;
        }
        to {
            opacity: 1;
        }
    }

    .splash-content {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 18px;
        animation: splash-content-rise 500ms cubic-bezier(0.22, 1, 0.36, 1);
    }

    /* Landscape phone: tighter stack — height <500px не вмістить великий
       72px mark + lockup + spinner + status з 24px gaps. Зменшуємо mark,
       gap, ховаємо status text як другорядний (spinner = enough feedback). */
    @media (orientation: landscape) and (max-height: 500px) {
        .splash-content {
            gap: 10px;
        }
    }
    @keyframes splash-content-rise {
        from {
            opacity: 0;
            transform: translateY(8px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    .spinner {
        display: flex;
        gap: 6px;
        align-items: center;
        justify-content: center;
    }
    .spinner-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--accent);
        animation: spinner-pulse 1.2s infinite ease-in-out;
        opacity: 0.4;
    }
    .spinner-dot:nth-child(2) {
        animation-delay: 0.2s;
    }
    .spinner-dot:nth-child(3) {
        animation-delay: 0.4s;
    }
    @keyframes spinner-pulse {
        0%,
        100% {
            opacity: 0.4;
            transform: scale(1);
        }
        50% {
            opacity: 1;
            transform: scale(1.3);
        }
    }

    .status-text {
        margin: 0;
        font-family: var(--font-mono);
        font-size: var(--t3-size);
        color: var(--text-3);
        letter-spacing: 0.05em;
    }
</style>
