<!--
  ADR-0071 P6 — InstallButton.svelte
  Captures `beforeinstallprompt` event (Chrome/Edge/Android Chrome).
  iOS Safari: no event API → fallback hint "Share → Add to Home Screen".
  Hidden when:
    - App already running у standalone mode (matchMedia '(display-mode: standalone)')
    - User dismissed (localStorage flag, persists across sessions)
  Lives у InfoModal "About" tab (low-frequency surface).
-->
<script lang="ts">
    let deferredPrompt: BeforeInstallPromptEvent | null = $state(null);
    let installed = $state(false);
    let dismissed = $state(false);
    let isIosSafari = $state(false);

    interface BeforeInstallPromptEvent extends Event {
        prompt: () => Promise<void>;
        userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
    }

    // Hydration on mount.
    $effect(() => {
        // Standalone detection — already installed.
        if (typeof window !== "undefined") {
            try {
                installed = window.matchMedia("(display-mode: standalone)").matches;
            } catch {
                installed = false;
            }
            try {
                dismissed = window.localStorage.getItem("pwa-install-dismissed") === "1";
            } catch {
                /* localStorage unavailable */
            }
            // iOS Safari detection (no beforeinstallprompt support).
            const ua = window.navigator.userAgent;
            const isIos = /iPad|iPhone|iPod/.test(ua) && !(window as any).MSStream;
            const isSafari = /^((?!chrome|android).)*safari/i.test(ua);
            isIosSafari = isIos && isSafari;
        }
    });

    // Capture beforeinstallprompt for non-iOS browsers.
    $effect(() => {
        function onBeforeInstall(e: Event) {
            e.preventDefault();
            deferredPrompt = e as BeforeInstallPromptEvent;
        }
        function onAppInstalled() {
            installed = true;
            deferredPrompt = null;
        }
        window.addEventListener("beforeinstallprompt", onBeforeInstall);
        window.addEventListener("appinstalled", onAppInstalled);
        return () => {
            window.removeEventListener("beforeinstallprompt", onBeforeInstall);
            window.removeEventListener("appinstalled", onAppInstalled);
        };
    });

    async function handleInstall() {
        if (!deferredPrompt) return;
        await deferredPrompt.prompt();
        const choice = await deferredPrompt.userChoice;
        if (choice.outcome === "accepted") {
            installed = true;
        }
        deferredPrompt = null;
    }

    function handleDismiss() {
        dismissed = true;
        try {
            window.localStorage.setItem("pwa-install-dismissed", "1");
        } catch {
            /* OK */
        }
    }
</script>

{#if !installed && !dismissed}
    <div class="install-card">
        <div class="install-header">
            <span class="install-icon">📲</span>
            <div class="install-text">
                <div class="install-title">Встановити на пристрій</div>
                <div class="install-sub">
                    Запускати як native app, без browser bar
                </div>
            </div>
        </div>
        {#if deferredPrompt}
            <div class="install-actions">
                <button class="install-btn" onclick={handleInstall}>
                    Встановити
                </button>
                <button class="install-dismiss" onclick={handleDismiss}>
                    Не треба
                </button>
            </div>
        {:else if isIosSafari}
            <div class="ios-hint">
                iPhone / iPad: натисни <b>⎋ Поділитися</b> у Safari →
                <b>Додати на головний екран</b>.
            </div>
            <button class="install-dismiss" onclick={handleDismiss}>
                Зрозуміло
            </button>
        {:else}
            <div class="ios-hint">
                Install prompt недоступний у цьому browser. Підтримується:
                Chrome, Edge (desktop + Android), Safari (iOS via Share menu).
            </div>
            <button class="install-dismiss" onclick={handleDismiss}>
                Закрити
            </button>
        {/if}
    </div>
{/if}

<style>
    .install-card {
        margin-top: 16px;
        padding: 14px 16px;
        background: rgba(212, 160, 23, 0.08);
        border: 1px solid rgba(212, 160, 23, 0.25);
        border-radius: 8px;
        font-family: var(--font-sans, system-ui, sans-serif);
    }
    .install-header {
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }
    .install-icon {
        font-size: 24px;
        line-height: 1;
        flex-shrink: 0;
    }
    .install-text { flex: 1; }
    .install-title {
        font-size: 14px;
        font-weight: 600;
        color: var(--text-1, #e6edf3);
        margin-bottom: 2px;
    }
    .install-sub {
        font-size: 12px;
        color: var(--text-2, #9b9bb0);
        line-height: 1.4;
    }
    .install-actions {
        display: flex;
        gap: 8px;
        margin-top: 12px;
    }
    .install-btn {
        flex: 1;
        background: linear-gradient(135deg, #d4a017 0%, #22cc8f 100%);
        border: 0;
        color: #0d1117;
        font-family: inherit;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        border-radius: 6px;
        cursor: pointer;
        transition: opacity 0.15s ease;
    }
    .install-btn:hover { opacity: 0.92; }
    .install-dismiss {
        background: transparent;
        border: 1px solid rgba(155, 155, 176, 0.25);
        color: var(--text-3, #6b6b80);
        font-family: inherit;
        font-size: 12px;
        padding: 8px 14px;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s ease;
    }
    .install-dismiss:hover {
        color: var(--text-2, #9b9bb0);
        border-color: rgba(155, 155, 176, 0.4);
    }
    .ios-hint {
        font-size: 12px;
        color: var(--text-2, #9b9bb0);
        line-height: 1.5;
        margin-top: 12px;
        padding: 10px;
        background: rgba(255, 255, 255, 0.03);
        border-radius: 6px;
    }
    .ios-hint b {
        color: var(--text-1, #e6edf3);
        font-weight: 600;
    }
</style>
