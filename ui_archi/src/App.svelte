<script lang="ts">
    import "./lib/theme.css";
    import { getToken, setToken, api } from "./lib/api";
    import type { ChatHandoff } from "./lib/types";
    import { onMount } from "svelte";
    import {
        getDirectives,
        refreshDirectives,
        startPolling,
        stopPolling,
    } from "./lib/state.svelte";
    import Feed from "./views/Feed.svelte";
    import Thinking from "./views/Thinking.svelte";
    import Relationship from "./views/Relationship.svelte";
    import Chat from "./views/Chat.svelte";
    import Mind from "./views/Mind.svelte";
    import Logs from "./views/Logs.svelte";

    // ── routing (hash-based) ──
    let route = $state(window.location.hash.replace("#", "") || "/chat");
    let sectionSwitcherOpen = $state(false);
    window.addEventListener("hashchange", () => {
        route = window.location.hash.replace("#", "") || "/chat";
        sectionSwitcherOpen = false;
    });

    function nav(path: string) {
        sectionSwitcherOpen = false;
        window.location.hash = path;
    }

    // ── chat draft + handoff state ──
    let chatDraft = $state("");
    let chatHandoff = $state<ChatHandoff | null>(null);

    function openChatDraft(text: string) {
        chatHandoff = null;
        chatDraft = text;
        nav("/chat");
    }

    function openChatHandoff(handoff: ChatHandoff) {
        const currentDraft = chatDraft.trim();
        const nextPrompt = handoff.prompt.trim();
        chatHandoff = handoff;
        if (!currentDraft) {
            chatDraft = handoff.prompt;
        } else if (currentDraft === nextPrompt) {
            chatDraft = handoff.prompt;
        }
        nav("/chat");
    }

    function updateChatDraft(text: string) {
        chatDraft = text;
    }

    function dismissChatHandoff(handoffId: string) {
        if (chatHandoff?.id === handoffId) {
            chatHandoff = null;
        }
    }

    function getChatProps(): Record<string, unknown> {
        return {
            draft: chatDraft,
            handoff: chatHandoff,
            ondraftchange: updateChatDraft,
            ondismisshandoff: dismissChatHandoff,
        };
    }

    // ── auth ──
    let token = $state(getToken());
    let tokenInput = $state("");
    let authError = $state("");

    function submitToken() {
        if (!tokenInput.trim()) {
            authError = "Введи токен";
            return;
        }
        setToken(tokenInput);
        token = tokenInput.trim();
        authError = "";
        refreshDirectives();
    }

    // ── directives (shell-level, shared across views via state.svelte.ts) ──
    let directives = $derived(getDirectives());

    $effect(() => {
        if (token) {
            startPolling();
            return () => stopPolling();
        }
    });

    // ── P3: Mood → Accent color ──
    const MOOD_COLORS: Record<string, string> = {
        calm: "#5487FF",
        focused: "#22CC8F",
        alert: "#F5A623",
        stressed: "#ED4554",
        excited: "#9B59B6",
        cautious: "#fb923c",
        frustrated: "#ED4554",
    };
    const DEFAULT_ACCENT = "#7c6fff";

    $effect(() => {
        const mood = directives?.mood;
        const color = (mood && MOOD_COLORS[mood]) || DEFAULT_ACCENT;
        document.documentElement.style.setProperty("--accent", color);
    });

    // ── Mobile viewport + keyboard state ──
    const MOBILE_BREAKPOINT = "(max-width: 768px)";
    let keyboardOpen = $state(false);
    let viewportRaf: number | null = null;

    function hasTextEntryFocus(): boolean {
        const active = document.activeElement;
        return !!(
            active instanceof HTMLElement &&
            (active.tagName === "TEXTAREA" ||
                active.tagName === "INPUT" ||
                active.isContentEditable)
        );
    }

    function updateViewportLayout() {
        const vv = window.visualViewport;
        const vh = vv?.height ?? window.innerHeight;
        const isMobile = window.matchMedia(MOBILE_BREAKPOINT).matches;
        const textEntryFocused = hasTextEntryFocus();

        document.documentElement.style.setProperty("--app-vh", `${vh}px`);
        keyboardOpen = isMobile && textEntryFocused;
        document.body.classList.toggle("is-mobile", isMobile);
        document.body.classList.toggle("keyboard-open", keyboardOpen);
    }

    function scheduleViewportLayout() {
        if (viewportRaf !== null) return;
        viewportRaf = requestAnimationFrame(() => {
            viewportRaf = null;
            updateViewportLayout();
        });
    }

    $effect(() => {
        if (!(route === "/chat" || route === "") || !keyboardOpen) {
            sectionSwitcherOpen = false;
        }
    });

    onMount(() => {
        updateViewportLayout();

        const vv = window.visualViewport;
        vv?.addEventListener("resize", scheduleViewportLayout);
        vv?.addEventListener("scroll", scheduleViewportLayout);
        window.addEventListener("resize", scheduleViewportLayout);
        window.addEventListener("orientationchange", scheduleViewportLayout);

        return () => {
            vv?.removeEventListener("resize", scheduleViewportLayout);
            vv?.removeEventListener("scroll", scheduleViewportLayout);
            window.removeEventListener("resize", scheduleViewportLayout);
            window.removeEventListener(
                "orientationchange",
                scheduleViewportLayout,
            );
            if (viewportRaf !== null) {
                cancelAnimationFrame(viewportRaf);
                viewportRaf = null;
            }
        };
    });

    // ── Browser Push Notifications ──
    const NOTIF_TYPE_ICON: Record<string, string> = {
        analysis: "🧠",
        signal: "🎯",
        trade: "💰",
        alert: "⚠️",
        error: "❌",
        market: "📊",
    };
    let notifEnabled = $state(
        typeof Notification !== "undefined" &&
            Notification.permission === "granted" &&
            localStorage.getItem("archi_notif") === "1",
    );
    let notifSSE: EventSource | null = null;

    function toggleNotifications() {
        if (notifEnabled) {
            // turn off
            notifEnabled = false;
            localStorage.setItem("archi_notif", "0");
            notifSSE?.close();
            notifSSE = null;
            return;
        }
        if (!("Notification" in window)) return;
        if (Notification.permission === "granted") {
            notifEnabled = true;
            localStorage.setItem("archi_notif", "1");
            connectNotifSSE();
        } else if (Notification.permission !== "denied") {
            Notification.requestPermission().then((perm) => {
                if (perm === "granted") {
                    notifEnabled = true;
                    localStorage.setItem("archi_notif", "1");
                    connectNotifSSE();
                }
            });
        }
    }

    function connectNotifSSE() {
        if (notifSSE || !token || !notifEnabled) return;
        const url = `/api/archi/stream?token=${encodeURIComponent(token)}`;
        const es = new EventSource(url);
        notifSSE = es;

        es.onmessage = (evt: MessageEvent) => {
            if (!document.hidden) return; // only notify when tab not visible
            try {
                const msg = JSON.parse(evt.data);
                if (msg.type === "feed" && msg.data) {
                    const ev = msg.data;
                    const imp = ev.importance ?? 1;
                    if (imp < 3) return;
                    const type = ev.type ?? "system";
                    const body = (ev.body ?? "").slice(0, 140);
                    const icon = NOTIF_TYPE_ICON[type] ?? "📋";
                    new Notification(`${icon} Арчі — ${type}`, {
                        body,
                        tag: String(ev.id ?? ev.ts_ms),
                    });
                }
            } catch {
                /* ignore */
            }
        };

        es.onerror = () => {
            notifSSE?.close();
            notifSSE = null;
            // Reconnect after 15s
            if (notifEnabled) setTimeout(() => connectNotifSSE(), 15_000);
        };
    }

    // Auto-connect notification SSE if already enabled
    $effect(() => {
        if (token && notifEnabled) {
            connectNotifSSE();
        }
        return () => {
            notifSSE?.close();
            notifSSE = null;
        };
    });
</script>

{#if !token}
    <!-- ── Auth Screen ── -->
    <div class="auth-screen">
        <div class="auth-card">
            <div class="auth-icon">🤖</div>
            <h1 class="auth-title">Archi Console</h1>
            <p class="auth-sub">Приватний доступ. Введи Bearer токен.</p>
            <form
                onsubmit={(e) => {
                    e.preventDefault();
                    submitToken();
                }}
                class="auth-form"
            >
                <input
                    class="token-input"
                    type="password"
                    bind:value={tokenInput}
                    placeholder="Bearer token..."
                    autocomplete="off"
                    spellcheck="false"
                />
                <button class="btn-primary" type="submit">Увійти</button>
            </form>
            {#if authError}<p class="auth-error">{authError}</p>{/if}
        </div>
    </div>
{:else}
    <!-- ── App Shell ── -->
    <div class="shell">
        <nav class="sidebar">
            <div class="logo">⬡ Archi</div>
            <ul class="nav-links">
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/chat" || route === ""}
                        onclick={() => nav("/chat")}
                    >
                        <span class="nav-icon">💬</span> Chat
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/feed"}
                        onclick={() => nav("/feed")}
                    >
                        <span class="nav-icon">⚡</span> Feed
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/thinking"}
                        onclick={() => nav("/thinking")}
                    >
                        <span class="nav-icon">🧠</span> Thinking
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/relationship"}
                        onclick={() => nav("/relationship")}
                    >
                        <span class="nav-icon">💙</span> Relationship
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/mind"}
                        onclick={() => nav("/mind")}
                    >
                        <span class="nav-icon">🧩</span> Mind
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/logs"}
                        onclick={() => nav("/logs")}
                    >
                        <span class="nav-icon">📋</span> Logs
                    </button>
                </li>
            </ul>

            <!-- ── Notification toggle ── -->
            <button
                class="notif-toggle"
                class:active={notifEnabled}
                onclick={toggleNotifications}
                title={notifEnabled
                    ? "Сповіщення увімкнено"
                    : "Увімкнути сповіщення"}
            >
                <span class="notif-bell">{notifEnabled ? "🔔" : "🔕"}</span>
                <span class="notif-label"
                    >{notifEnabled ? "Сповіщення" : "Увімкнути"}</span
                >
            </button>

            <!-- ── Directives panel ── -->
            {#if directives}
                <div class="directives-panel">
                    <div class="dir-title">Стан Арчі</div>
                    {#if directives.kill_switch_active}
                        <div class="dir-row danger">⛔ KILL SWITCH</div>
                    {/if}
                    {#if directives.economy_mode_active}
                        <div class="dir-row warning">⚡ eco mode</div>
                    {/if}
                    {#if directives.mode}
                        <div class="dir-row">
                            <span class="dir-label">режим</span>
                            <span class="dir-val badge-sm mode"
                                >{directives.mode}</span
                            >
                        </div>
                    {/if}
                    {#if directives.mood}
                        <div class="dir-row">
                            <span class="dir-label">настрій</span>
                            <span class="dir-val mood-val">
                                <span
                                    class="mood-dot"
                                    data-mood={directives.mood}
                                ></span>
                                {directives.mood}
                            </span>
                        </div>
                    {/if}
                    {#if directives.focus_symbol}
                        <div class="dir-row">
                            <span class="dir-label">символ</span>
                            <span class="dir-val badge-sm gold"
                                >{directives.focus_symbol}</span
                            >
                        </div>
                    {/if}
                    {#if directives.active_scenario}
                        {@const _sc = directives.active_scenario}
                        {@const _scText = typeof _sc === 'object' && _sc !== null
                            ? ((_sc as Record<string, unknown>).thesis
                                ? String((_sc as Record<string, unknown>).thesis)
                                : ((_sc as Record<string, unknown>).direction
                                    ? String((_sc as Record<string, unknown>).direction)
                                    : '—'))
                            : String(_sc)}
                        <div class="dir-row">
                            <span class="dir-label">сценарій</span>
                            <span class="dir-val scenario" title={_scText}
                                >{_scText}</span
                            >
                        </div>
                    {/if}
                    {#if directives.token_usage_today != null}
                        {@const _tok = directives.token_usage_today as
                            | Record<string, number>
                            | number}
                        {@const _total =
                            typeof _tok === "object" && _tok !== null
                                ? (_tok.input_tokens ?? 0) +
                                  (_tok.output_tokens ?? 0)
                                : Number(_tok)}
                        <div class="dir-row">
                            <span class="dir-label">токени</span>
                            <span class="dir-val"
                                >{isNaN(_total)
                                    ? "?"
                                    : _total.toLocaleString()}</span
                            >
                        </div>
                    {/if}
                    {#if directives.inner_thought}
                        <div class="dir-thought">
                            "{directives.inner_thought}"
                        </div>
                    {/if}
                </div>
            {/if}

            <div class="sidebar-footer">
                <button
                    class="btn-ghost small"
                    onclick={() => {
                        setToken("");
                        token = "";
                    }}
                >
                    Вийти
                </button>
            </div>
        </nav>

        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            class="right-panel"
            class:chat-route={route === "/chat" || route === ""}
            class:keyboard-open={keyboardOpen}
        >
            <main
                class="content"
                class:chat-layout={route === "/chat" || route === ""}
                class:logs-layout={route === "/logs"}
            >
                {#if route === "/chat" || route === ""}
                    <Chat {...getChatProps()} />
                {:else if route === "/feed"}
                    <Feed onchat={openChatHandoff} />
                {:else if route === "/thinking"}
                    <Thinking
                        onchat={(text: string) => {
                            openChatDraft(text);
                        }}
                    />
                {:else if route === "/relationship"}
                    <Relationship onchat={openChatHandoff} />
                {:else if route === "/mind"}
                    <Mind onchat={openChatHandoff} />
                {:else if route === "/logs"}
                    <Logs onchat={openChatHandoff} />
                {:else}
                    <div class="empty-state">404 — не знайдено</div>
                {/if}
            </main>

            {#if (route === "/chat" || route === "") && keyboardOpen}
                <div class="section-switcher">
                    <button
                        class="section-switcher-toggle"
                        class:active={sectionSwitcherOpen}
                        onclick={() => {
                            sectionSwitcherOpen = !sectionSwitcherOpen;
                        }}
                        aria-expanded={sectionSwitcherOpen}
                        aria-label="Перемкнути розділ"
                    >
                        <span class="section-switcher-icon">☰</span>
                        <span class="section-switcher-label">Розділи</span>
                    </button>

                    {#if sectionSwitcherOpen}
                        <div class="section-switcher-menu">
                            <button
                                class:active={route === "/chat" || route === ""}
                                onclick={() => nav("/chat")}
                            >
                                <span class="switcher-emoji">💬</span>
                                <span>Chat</span>
                            </button>
                            <button onclick={() => nav("/feed")}>
                                <span class="switcher-emoji">⚡</span>
                                <span>Feed</span>
                            </button>
                            <button onclick={() => nav("/thinking")}>
                                <span class="switcher-emoji">🧠</span>
                                <span>Thinking</span>
                            </button>
                            <button onclick={() => nav("/relationship")}>
                                <span class="switcher-emoji">💙</span>
                                <span>Relationship</span>
                            </button>
                            <button onclick={() => nav("/mind")}>
                                <span class="switcher-emoji">🧩</span>
                                <span>Mind</span>
                            </button>
                            <button onclick={() => nav("/logs")}>
                                <span class="switcher-emoji">📋</span>
                                <span>Logs</span>
                            </button>
                        </div>
                    {/if}
                </div>
            {/if}

            <!-- ── Bottom Nav (mobile only — icon-only with underline) ── -->
            <nav class="bottom-nav">
                <button
                    class:active={route === "/chat" || route === ""}
                    onclick={() => nav("/chat")}
                    aria-label="Chat"
                >
                    <span class="bn-pill"><span class="bn-icon">💬</span></span>
                </button>
                <button
                    class:active={route === "/feed"}
                    onclick={() => nav("/feed")}
                    aria-label="Feed"
                >
                    <span class="bn-pill"><span class="bn-icon">⚡</span></span>
                </button>
                <button
                    class:active={route === "/thinking"}
                    onclick={() => nav("/thinking")}
                    aria-label="Thinking"
                >
                    <span class="bn-pill"><span class="bn-icon">🧠</span></span>
                </button>
                <button
                    class:active={route === "/relationship"}
                    onclick={() => nav("/relationship")}
                    aria-label="Relationship"
                >
                    <span class="bn-pill"><span class="bn-icon">💙</span></span>
                </button>
                <button
                    class:active={route === "/mind"}
                    onclick={() => nav("/mind")}
                    aria-label="Mind"
                >
                    <span class="bn-pill"><span class="bn-icon">🧩</span></span>
                </button>
                <button
                    class:active={route === "/logs"}
                    onclick={() => nav("/logs")}
                    aria-label="Logs"
                >
                    <span class="bn-pill"><span class="bn-icon">📋</span></span>
                </button>
            </nav>
        </div>
    </div>
{/if}

<style>
    /* ── Auth Screen ── */
    .auth-screen {
        min-height: var(--app-vh, 100vh);
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--bg);
    }
    .auth-card {
        width: 360px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 40px 32px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 16px;
    }
    .auth-icon {
        font-size: 40px;
    }
    .auth-title {
        font-size: 20px;
        font-weight: 600;
        color: var(--text);
    }
    .auth-sub {
        font-size: 13px;
        color: var(--text-muted);
        text-align: center;
    }
    .auth-form {
        width: 100%;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .token-input {
        width: 100%;
        padding: 10px 12px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        color: var(--text);
        font-family: var(--font-mono);
        font-size: 13px;
        outline: none;
    }
    .token-input:focus {
        border-color: var(--accent);
    }
    .btn-primary {
        padding: 10px;
        background: var(--accent);
        color: white;
        border: none;
        border-radius: var(--radius);
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
    }
    .btn-primary:hover {
        filter: brightness(1.1);
    }
    .auth-error {
        font-size: 12px;
        color: var(--danger);
    }

    /* ── Shell ── */
    .shell {
        display: flex;
        height: var(--app-vh, 100vh);
        overflow: hidden;
    }

    /* ── Sidebar ── */
    .sidebar {
        width: 180px;
        flex-shrink: 0;
        background: var(--surface);
        border-right: 1px solid var(--border);
        display: flex;
        flex-direction: column;
        padding: 16px 12px;
        gap: 8px;
    }
    .logo {
        font-size: 15px;
        font-weight: 700;
        color: var(--accent);
        padding: 4px 8px 12px;
        letter-spacing: 0.02em;
    }
    .nav-links {
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: 2px;
        flex: 1;
    }
    .nav-item {
        width: 100%;
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        background: none;
        border: none;
        border-radius: 6px;
        color: var(--text-muted);
        font-size: 13px;
        cursor: pointer;
        text-align: left;
        transition:
            background 0.1s,
            color 0.1s;
    }
    .nav-item:hover {
        background: var(--surface2);
        color: var(--text);
    }
    .nav-item.active {
        background: var(--accent-dim);
        color: var(--text);
    }
    .nav-icon {
        font-size: 15px;
    }
    .sidebar-footer {
        padding-top: 8px;
        border-top: 1px solid var(--border);
    }
    .btn-ghost {
        background: none;
        border: 1px solid var(--border);
        border-radius: var(--radius);
        color: var(--text-muted);
        cursor: pointer;
        padding: 6px 12px;
    }
    .btn-ghost:hover {
        color: var(--text);
        border-color: var(--text-muted);
    }
    .btn-ghost.small {
        font-size: 12px;
        padding: 4px 10px;
    }

    /* ── Notification toggle ── */
    .notif-toggle {
        display: flex;
        align-items: center;
        gap: 6px;
        width: 100%;
        padding: 6px 10px;
        background: none;
        border: 1px solid var(--border);
        border-radius: 6px;
        color: var(--text-muted);
        font-size: 12px;
        cursor: pointer;
        transition:
            border-color 0.15s,
            color 0.15s;
    }
    .notif-toggle:hover {
        border-color: var(--accent);
        color: var(--text);
    }
    .notif-toggle.active {
        border-color: var(--accent);
        color: var(--accent);
    }
    .notif-bell {
        font-size: 14px;
    }
    .notif-label {
        font-weight: 500;
    }

    /* ── Content ── */
    .right-panel {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
        background: var(--bg);
    }
    .content {
        flex: 1;
        overflow-y: auto;
        background: var(--bg);
    }
    /* Chat needs its own internal scroll — disable outer scroll */
    .content.chat-layout {
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }
    .content.logs-layout {
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }

    /* ── Directives panel ── */
    .directives-panel {
        margin: 8px 4px;
        padding: 10px 10px;
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .dir-title {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--text-muted);
        margin-bottom: 4px;
    }
    .dir-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 4px;
        font-size: 11px;
        color: var(--text);
    }
    .dir-row.danger {
        color: #f87171;
        font-weight: 600;
    }
    .dir-row.warning {
        color: #fbbf24;
        font-weight: 500;
    }
    .dir-label {
        color: var(--text-muted);
        flex-shrink: 0;
    }
    .dir-val {
        text-align: right;
        max-width: 90px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .dir-val.scenario {
        color: var(--accent);
        font-size: 10px;
    }
    .badge-sm {
        font-size: 10px;
        padding: 1px 5px;
        border-radius: 4px;
        white-space: nowrap;
    }
    .badge-sm.gold {
        background: rgba(251, 191, 36, 0.12);
        color: #fbbf24;
    }
    .badge-sm.mode {
        background: var(--accent-dim);
        color: var(--accent);
    }
    .dir-thought {
        font-size: 10px;
        color: var(--text-muted);
        font-style: italic;
        line-height: 1.4;
        margin-top: 4px;
        padding-top: 4px;
        border-top: 1px solid var(--border);
        overflow: hidden;
        display: -webkit-box;
        line-clamp: 3;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
    }

    .empty-state {
        padding: 60px;
        text-align: center;
        color: var(--text-muted);
    }

    /* ── Bottom Nav (hidden on desktop) ── */
    .bottom-nav {
        display: none;
    }
    .section-switcher {
        display: none;
    }

    /* ── Mood dot (pulsing) ── */
    .mood-dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--accent);
        animation: mood-pulse 2s ease-in-out infinite;
        vertical-align: middle;
        margin-right: 4px;
    }
    .mood-val {
        display: flex;
        align-items: center;
        gap: 4px;
    }

    /* Mood colors */
    .mood-dot[data-mood="calm"] {
        background: #60a5fa;
        animation-duration: 3s;
    }
    .mood-dot[data-mood="focused"] {
        background: #34d399;
        animation-duration: 2s;
    }
    .mood-dot[data-mood="alert"] {
        background: #fbbf24;
        animation-duration: 1.5s;
    }
    .mood-dot[data-mood="stressed"] {
        background: #f87171;
        animation-duration: 1s;
    }
    .mood-dot[data-mood="cautious"] {
        background: #fb923c;
        animation-duration: 1.8s;
    }
    .mood-dot[data-mood="frustrated"] {
        background: #f87171;
        animation-duration: 0.8s;
    }
    .mood-dot[data-mood="excited"] {
        background: #c084fc;
        animation-duration: 1.2s;
    }

    @keyframes mood-pulse {
        0%,
        100% {
            opacity: 1;
            transform: scale(1);
        }
        50% {
            opacity: 0.5;
            transform: scale(0.7);
        }
    }

    /* ── Mobile ── */
    @media (max-width: 768px) {
        .sidebar {
            display: none;
        }
        .right-panel {
            --mobile-nav-space: calc(60px + env(safe-area-inset-bottom));
        }
        .right-panel.chat-route.keyboard-open {
            --mobile-nav-space: 0px;
        }
        .content {
            padding-bottom: var(--mobile-nav-space);
            transition: padding-bottom 0.22s ease;
        }
        .section-switcher {
            position: fixed;
            top: max(12px, calc(env(safe-area-inset-top) + 4px));
            right: 12px;
            z-index: 130;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 8px;
        }
        .section-switcher-toggle {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 9px 12px;
            border-radius: 999px;
            border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
            background: color-mix(in srgb, var(--surface) 96%, var(--bg));
            color: var(--text);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
            cursor: pointer;
            transition:
                border-color 0.15s,
                transform 0.15s;
        }
        .section-switcher-toggle.active {
            border-color: color-mix(in srgb, var(--accent) 45%, transparent);
        }
        .section-switcher-toggle:active {
            transform: scale(0.98);
        }
        .section-switcher-icon {
            font-size: 13px;
            line-height: 1;
        }
        .section-switcher-label {
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .section-switcher-menu {
            width: min(228px, calc(100vw - 24px));
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
            padding: 10px;
            border-radius: 18px;
            border: 1px solid var(--border);
            background: color-mix(in srgb, var(--surface) 97%, var(--bg));
            box-shadow: 0 18px 40px rgba(0, 0, 0, 0.28);
        }
        .section-switcher-menu button {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 8px;
            min-height: 42px;
            padding: 10px 12px;
            border-radius: 12px;
            border: 1px solid color-mix(in srgb, var(--border) 92%, transparent);
            background: var(--surface2);
            color: var(--text-muted);
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
        }
        .section-switcher-menu button.active {
            color: var(--text);
            border-color: color-mix(in srgb, var(--accent) 35%, transparent);
            background: color-mix(in srgb, var(--accent) 14%, var(--surface2));
        }
        .switcher-emoji {
            font-size: 15px;
            line-height: 1;
        }
        .bottom-nav {
            display: flex;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            align-items: center;
            justify-content: space-between;
            min-height: 56px;
            padding: 6px 10px calc(8px + env(safe-area-inset-bottom));
            background: color-mix(in srgb, var(--surface) 96%, var(--bg));
            border-top: 1px solid var(--border);
            z-index: 100;
            transition:
                transform 0.22s ease,
                opacity 0.22s ease;
        }
        .right-panel.chat-route.keyboard-open .bottom-nav {
            transform: translateY(120%);
            opacity: 0;
            pointer-events: none;
        }
        .bottom-nav button {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 48px;
            min-height: 48px;
            background: none;
            border: none;
            cursor: pointer;
            color: var(--text-muted);
            transition: color 0.15s;
        }
        .bottom-nav button.active {
            color: var(--text);
        }
        .bn-pill {
            width: 42px;
            height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 17px;
            transition:
                background 0.15s,
                box-shadow 0.15s,
                transform 0.15s;
        }
        .bottom-nav button.active .bn-pill {
            background: color-mix(in srgb, var(--accent) 18%, var(--surface2));
            box-shadow:
                inset 0 0 0 1px
                    color-mix(in srgb, var(--accent) 35%, transparent),
                0 6px 18px rgba(0, 0, 0, 0.18);
            transform: translateY(-1px);
        }
        .bn-icon {
            font-size: 22px;
            line-height: 1;
        }
    }
</style>
