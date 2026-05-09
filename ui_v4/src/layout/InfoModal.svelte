<!--
  src/layout/InfoModal.svelte — ADR-0068 Slice 1 (rename from AboutModal.svelte)

  Modal with tabs: About / Credits / [Diagnostics — added in Slice 2].
  Header: Brand lockup. Esc + backdrop close. Focus trap on open.
  Theme-aware via tokens.

  Renamed from AboutModal.svelte (ADR-0066 PATCH 04 → ADR-0068 Slice 1).
  CSS-class names kept generic (.modal, .tabs) to allow future tabs without
  CSS rename. ARIA id updated info-modal-title.
-->

<script lang="ts">
    import { onMount, onDestroy } from "svelte";
    import Brand from "./Brand.svelte";
    import DiagnosticsView from "./DiagnosticsView.svelte";
    import { OSS_NOTICES } from "../data/oss-notices";

    // ADR-0068 Slice 2: "diagnostics" tab added (forward-only Ctrl+Shift+D redirect target).
    type InfoTab = "about" | "credits" | "diagnostics";

    interface Props {
        open: boolean;
        onClose: () => void;
        defaultTab?: InfoTab;
    }

    const { open, onClose, defaultTab = "about" }: Props = $props();

    // Build/version metadata. Vite injects via define() at build time;
    // fallback to dev placeholders if not provided.
    const VERSION = (
        typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "v3-dev"
    ) as string;
    const BUILD_DATE = (
        typeof __BUILD_DATE__ !== "undefined" ? __BUILD_DATE__ : "dev"
    ) as string;
    const COMMIT_HASH = (
        typeof __COMMIT_HASH__ !== "undefined" ? __COMMIT_HASH__ : "—"
    ) as string;

    // Initial value is intentional; $effect below resets on each `open` flip
    // using the current `defaultTab` prop, so the warning is benign.
    let activeTab = $state<InfoTab>("about");
    let modalEl = $state<HTMLDivElement | null>(null);
    let prevFocus = $state<HTMLElement | null>(null);

    function handleKeydown(e: KeyboardEvent) {
        if (!open) return;
        if (e.key === "Escape") {
            e.preventDefault();
            onClose();
            return;
        }
        // Focus trap: cycle Tab inside modal
        if (e.key === "Tab" && modalEl) {
            const focusables = modalEl.querySelectorAll<HTMLElement>(
                'button, [href], input, [tabindex]:not([tabindex="-1"])',
            );
            if (focusables.length === 0) return;
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    }

    function handleBackdrop(e: MouseEvent) {
        if (e.target === e.currentTarget) onClose();
    }

    $effect(() => {
        if (open) {
            // Reset to defaultTab on each open (callers may pass different default).
            activeTab = defaultTab;
            prevFocus = document.activeElement as HTMLElement;
            // Focus first focusable inside modal on next tick
            requestAnimationFrame(() => {
                const first = modalEl?.querySelector<HTMLElement>(
                    'button, [href], input, [tabindex]:not([tabindex="-1"])',
                );
                first?.focus();
            });
        } else if (prevFocus) {
            prevFocus.focus();
            prevFocus = null;
        }
    });

    onMount(() => {
        document.addEventListener("keydown", handleKeydown);
    });
    onDestroy(() => {
        document.removeEventListener("keydown", handleKeydown);
    });
</script>

{#if open}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div class="modal-backdrop" onclick={handleBackdrop} role="presentation">
        <div
            class="modal"
            bind:this={modalEl}
            role="dialog"
            aria-modal="true"
            aria-labelledby="info-modal-title"
        >
            <header class="modal-header">
                <div id="info-modal-title">
                    <Brand variant="lockup" size={28} />
                </div>
                <button
                    class="close-btn"
                    onclick={onClose}
                    aria-label="Close"
                    type="button">×</button
                >
            </header>

            <div class="tabs" role="tablist">
                <button
                    class="tab"
                    class:active={activeTab === "about"}
                    onclick={() => (activeTab = "about")}
                    role="tab"
                    aria-selected={activeTab === "about"}
                    type="button">About</button
                >
                <button
                    class="tab"
                    class:active={activeTab === "credits"}
                    onclick={() => (activeTab = "credits")}
                    role="tab"
                    aria-selected={activeTab === "credits"}
                    type="button">Credits</button
                >
                <button
                    class="tab"
                    class:active={activeTab === "diagnostics"}
                    onclick={() => (activeTab = "diagnostics")}
                    role="tab"
                    aria-selected={activeTab === "diagnostics"}
                    type="button">Diagnostics</button
                >
            </div>

            <div class="modal-body">
                {#if activeTab === "about"}
                    <div class="tab-pane">
                        <p class="version-line">
                            Version <strong>{VERSION}</strong>
                            · Build <code>{BUILD_DATE}</code>
                            · Commit <code>{COMMIT_HASH}</code>
                        </p>
                        <p class="about-blurb">
                            <strong>AI · ONE</strong> is an SMC-driven analytics
                            platform for institutional order-flow trading. Smart
                            Money Concepts overlay (zones, structure, liquidity,
                            P/D) with an autonomous AI agent (Archi) producing real-time
                            narrative and scenario synthesis.
                        </p>
                        <p class="disclaimer">
                            Not financial advice. Past performance does not
                            guarantee future results. Charts and signals are
                            analytical tools — final trading decisions remain
                            with the operator.
                        </p>
                    </div>
                {:else if activeTab === "credits"}
                    <div class="tab-pane">
                        <p class="credits-intro">
                            Built with these open-source projects. License
                            attributions are reproduced below per each project's
                            terms.
                        </p>
                        <ul class="oss-list">
                            {#each OSS_NOTICES as notice}
                                <li class="oss-item">
                                    <div class="oss-head">
                                        <a
                                            class="oss-name"
                                            href={notice.homepage}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            >{notice.name}</a
                                        >
                                        <span class="oss-license"
                                            >{notice.license}</span
                                        >
                                    </div>
                                    <div class="oss-copy">
                                        {notice.copyright}
                                    </div>
                                    <div class="oss-purpose">
                                        {notice.purpose}
                                    </div>
                                    {#if notice.noticeText}
                                        <pre
                                            class="oss-notice">{notice.noticeText}</pre>
                                    {/if}
                                </li>
                            {/each}
                        </ul>
                    </div>
                {:else}
                    <div class="tab-pane">
                        <DiagnosticsView />
                    </div>
                {/if}
            </div>
        </div>
    </div>
{/if}

<style>
    .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
        backdrop-filter: blur(4px);
        z-index: 200;
        display: flex;
        align-items: center;
        justify-content: center;
        animation: fade-in 180ms ease;
    }
    @keyframes fade-in {
        from {
            opacity: 0;
        }
        to {
            opacity: 1;
        }
    }

    .modal {
        max-width: 520px;
        width: calc(100% - 32px);
        max-height: 80vh;
        display: flex;
        flex-direction: column;
        background: var(--elev);
        border: 1px solid var(--border);
        border-radius: 10px;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        overflow: hidden;
        animation: slide-in 220ms cubic-bezier(0.22, 1, 0.36, 1);
    }
    @keyframes slide-in {
        from {
            opacity: 0;
            transform: translateY(-8px) scale(0.98);
        }
        to {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
    }

    .modal-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        padding: 16px 20px 12px;
        border-bottom: 1px solid var(--border);
    }
    .close-btn {
        all: unset;
        cursor: pointer;
        font-size: 24px;
        line-height: 1;
        color: var(--text-3);
        padding: 4px 8px;
        margin: -4px -8px;
        border-radius: 4px;
        transition:
            background 0.15s ease,
            color 0.15s ease;
    }
    .close-btn:hover {
        color: var(--text-1);
        background: color-mix(in srgb, var(--accent) 8%, transparent);
    }
    .close-btn:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }

    .tabs {
        display: flex;
        gap: 0;
        padding: 0 20px;
        border-bottom: 1px solid var(--border);
    }
    .tab {
        all: unset;
        cursor: pointer;
        padding: 10px 14px;
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        font-weight: var(--t3-weight);
        color: var(--text-2);
        border-bottom: 2px solid transparent;
        transition:
            color 0.15s ease,
            border-color 0.15s ease;
    }
    .tab:hover {
        color: var(--text-1);
    }
    .tab.active {
        color: var(--accent);
        border-bottom-color: var(--accent);
    }
    .tab:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: -1px;
    }

    .modal-body {
        padding: 16px 20px 20px;
        overflow-y: auto;
        flex: 1;
        font-family: var(--font-sans);
        color: var(--text-1);
    }

    .tab-pane {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .version-line {
        margin: 0;
        font-family: var(--font-mono);
        font-size: var(--t3-size);
        color: var(--text-2);
    }
    .version-line strong {
        color: var(--text-1);
    }
    .version-line code {
        font-family: var(--font-mono);
        color: var(--accent);
    }

    .about-blurb {
        margin: 4px 0 0;
        font-size: var(--t3a-size);
        line-height: 1.5;
        color: var(--text-1);
    }
    .about-blurb strong {
        color: var(--accent);
        font-weight: 700;
    }
    .disclaimer {
        margin: 0;
        font-size: var(--t6-size);
        color: var(--text-3);
        font-style: italic;
        line-height: 1.4;
        padding: 8px 10px;
        background: color-mix(in srgb, var(--warn) 8%, transparent);
        border-left: 2px solid var(--warn);
        border-radius: 4px;
    }

    .credits-intro {
        margin: 0;
        font-size: var(--t3a-size);
        color: var(--text-2);
        line-height: 1.5;
    }

    .oss-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 14px;
    }
    .oss-item {
        padding: 10px 12px;
        background: var(--card);
        border-radius: 6px;
        border: 1px solid var(--border-mute);
    }
    .oss-head {
        display: flex;
        align-items: baseline;
        gap: 10px;
        margin-bottom: 4px;
    }
    .oss-name {
        font-family: var(--font-mono);
        font-size: var(--t3-size);
        font-weight: 600;
        color: var(--accent);
        text-decoration: none;
    }
    .oss-name:hover {
        text-decoration: underline;
    }
    .oss-name:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }
    .oss-license {
        font-family: var(--font-mono);
        font-size: var(--t6-size);
        font-weight: 600;
        color: var(--text-3);
        padding: 1px 6px;
        background: color-mix(in srgb, var(--accent) 10%, transparent);
        border-radius: 3px;
    }
    .oss-copy {
        font-size: var(--t6-size);
        color: var(--text-2);
        margin-bottom: 4px;
    }
    .oss-purpose {
        font-size: var(--t6-size);
        color: var(--text-3);
        line-height: 1.4;
    }
    .oss-notice {
        font-family: var(--font-mono);
        font-size: var(--t7-size);
        color: var(--text-2);
        background: var(--bg);
        border: 1px solid var(--border-mute);
        padding: 6px 8px;
        margin: 6px 0 0;
        border-radius: 4px;
        white-space: pre-wrap;
        word-break: break-word;
        line-height: 1.5;
    }

    @media (max-width: 600px) {
        .modal {
            max-width: none;
            width: calc(100% - 16px);
            max-height: 90vh;
        }
        .modal-header {
            padding: 12px 14px 8px;
        }
        .modal-body {
            padding: 12px 14px 16px;
        }
        .tabs {
            padding: 0 14px;
        }
    }
</style>
