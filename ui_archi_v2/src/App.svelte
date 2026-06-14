<script lang="ts">
    import "./lib/theme.css";
    import { getToken, setToken, api } from "./lib/api";
    import type { ChatHandoff } from "./lib/types";
    import { onMount } from "svelte";
    import {
        getDirectives,
        refreshDirectives,
        applyDirectives,
        startPolling,
        stopPolling,
    } from "./lib/state.svelte";
    import type { Directives } from "./lib/types";
    import Feed from "./views/Feed.svelte";
    import Thinking from "./views/Thinking.svelte";
    import Relationship from "./views/Relationship.svelte";
    import Chat from "./views/Chat.svelte";
    import Gorn from "./views/Gorn.svelte";
    import Mind from "./views/Mind.svelte";
    import Workspace from "./views/Workspace.svelte";
    import Logs from "./views/Logs.svelte";
    import Icon from "./lib/Icon.svelte";

    // ── routing (hash-based) ──
    let route = $state(window.location.hash.replace("#", "") || "/gorn");
    window.addEventListener("hashchange", () => {
        route = window.location.hash.replace("#", "") || "/gorn";
    });

    function nav(path: string) {
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
            startPolling(); // fallback / cold-start; SSE drives live updates
            connectLiveSSE(); // Фаза 3a: real-time directives + notif
            return () => {
                stopPolling();
                notifSSE?.close();
                notifSSE = null;
            };
        }
    });

    // ── P3: Mood → Accent color ──
    const MOOD_COLORS: Record<string, string> = {
        // ⚙ робочий стан
        focused: "#22CC8F",
        analytical: "#2DD4BF",
        alert: "#F5A623",
        cautious: "#FBBF24",
        determined: "#10B981",
        // ◈ впевненість
        confident: "#3B82F6",
        uncertain: "#94A3B8",
        conflicted: "#A78BFA",
        // ✦ позитив
        calm: "#60A5FA",
        excited: "#C084FC",
        satisfied: "#4ADE80",
        hopeful: "#5EEAD4",
        curious: "#38BDF8",
        // ⚠ напруга
        frustrated: "#F87171",
        tense: "#FB7185",
        weary: "#8B8BA7",
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

    function updateViewportLayout() {
        const vv = window.visualViewport;
        const vh = vv?.height ?? window.innerHeight;
        const isMobile = window.matchMedia(MOBILE_BREAKPOINT).matches;
        // Клавіатура РЕАЛЬНО займає екран (visualViewport коротший за layout),
        // а не просто фокус інпута. Інакше баг: сховав клавіатуру кнопкою —
        // інпут лишився focused → keyboardOpen=true → нав не повертався.
        const keyboardUp = !!vv && window.innerHeight - vv.height > 150;

        document.documentElement.style.setProperty("--app-vh", `${vh}px`);
        keyboardOpen = isMobile && keyboardUp;
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
            // turn off browser notifications ONLY — the live SSE stays connected
            // (Фаза 3a: SSE now drives real-time state, not just notifications).
            notifEnabled = false;
            localStorage.setItem("archi_notif", "0");
            return;
        }
        if (!("Notification" in window)) return;
        if (Notification.permission === "granted") {
            notifEnabled = true;
            localStorage.setItem("archi_notif", "1");
            connectLiveSSE();
        } else if (Notification.permission !== "denied") {
            Notification.requestPermission().then((perm) => {
                if (perm === "granted") {
                    notifEnabled = true;
                    localStorage.setItem("archi_notif", "1");
                    connectLiveSSE();
                }
            });
        }
    }

    // Фаза 3a (2026-06-13): live SSE — connects ALWAYS (not gated on notifEnabled),
    // applies directives changes to shared state in real time so mood/думка/scenario
    // no longer lag 30s while watching. Browser notifications stay opt-in + tab-hidden.
    function connectLiveSSE() {
        if (notifSSE || !token) return;
        const url = `/api/archi/stream?token=${encodeURIComponent(token)}`;
        const es = new EventSource(url);
        notifSSE = es;

        es.onmessage = (evt: MessageEvent) => {
            try {
                const msg = JSON.parse(evt.data);
                // LIVE state update — always, regardless of tab visibility
                if (msg.type === "directives" && msg.data) {
                    applyDirectives(msg.data as Directives);
                    return;
                }
                // Feed → browser notification only when tab hidden + opted in + important
                if (msg.type === "feed" && msg.data && document.hidden && notifEnabled) {
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
            if (token) setTimeout(() => connectLiveSSE(), 15_000); // reconnect
        };
    }

    // Auto-connect notification SSE if already enabled
    $effect(() => {
        if (token && notifEnabled) {
            connectLiveSSE();
        }
        return () => {
            notifSSE?.close();
            notifSSE = null;
        };
    });
</script>

{#if !token}
    <!-- ── Auth Screen (premium redesign — living orb, mood-driven accent) ── -->
    <div class="auth-screen">
        <div class="aura"><span></span><span></span></div>
        <div class="auth-card">
            <div class="orb-wrap">
                <div class="ring r1"></div>
                <div class="ring r2"></div>
                <div class="ring r3"></div>
                <div class="orb"></div>
            </div>
            <h1 class="auth-title">Archi</h1>
            <p class="auth-sub">
                Торговий розум, що <b>живе ринком</b>.<br />Приватний доступ.
            </p>
            <form
                onsubmit={(e) => {
                    e.preventDefault();
                    submitToken();
                }}
                class="auth-form"
            >
                <div class="field">
                    <input
                        class="token-input"
                        type="password"
                        bind:value={tokenInput}
                        placeholder="Bearer token"
                        autocomplete="off"
                        spellcheck="false"
                    />
                </div>
                <button class="btn-primary" type="submit">Увійти</button>
            </form>
            {#if authError}<p class="auth-error">{authError}</p>{/if}
        </div>
    </div>
{:else}
    <!-- ── App Shell ── -->
    <div class="shell">
        <nav class="sidebar">
            <div class="brand">
                <div class="brand-orb"></div>
                <span class="brand-name">Archi</span>
            </div>
            <ul class="nav-links">
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/gorn" || route === ""}
                        onclick={() => nav("/gorn")}
                    >
                        <span class="nav-icon"><Icon name="flame" /></span> ГОРН
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/chat"}
                        onclick={() => nav("/chat")}
                    >
                        <span class="nav-icon"><Icon name="chat" /></span> Chat
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/feed"}
                        onclick={() => nav("/feed")}
                    >
                        <span class="nav-icon"><Icon name="feed" /></span> Feed
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/thinking"}
                        onclick={() => nav("/thinking")}
                    >
                        <span class="nav-icon"><Icon name="thinking" /></span> Thinking
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/relationship"}
                        onclick={() => nav("/relationship")}
                    >
                        <span class="nav-icon"><Icon name="relationship" /></span> Relationship
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/mind"}
                        onclick={() => nav("/mind")}
                    >
                        <span class="nav-icon"><Icon name="mind" /></span> Mind
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/workspace"}
                        onclick={() => nav("/workspace")}
                    >
                        <span class="nav-icon"><Icon name="workspace" /></span> Workspace
                    </button>
                </li>
                <li>
                    <button
                        class="nav-item"
                        class:active={route === "/logs"}
                        onclick={() => nav("/logs")}
                    >
                        <span class="nav-icon"><Icon name="logs" /></span> Logs
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
                <span class="notif-bell"><Icon name={notifEnabled ? "bell" : "belloff"} size={14} /></span>
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
            class:chat-route={route === "/chat"}
            class:keyboard-open={keyboardOpen}
        >
            <main
                class="content"
                class:gorn-layout={route === "/gorn" || route === ""}
                class:chat-layout={route === "/chat"}
                class:logs-layout={route === "/logs"}
            >
                {#if route === "/gorn" || route === ""}
                    <Gorn />
                {:else if route === "/chat"}
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
                {:else if route === "/workspace"}
                    <Workspace onchat={openChatHandoff} />
                {:else if route === "/logs"}
                    <Logs onchat={openChatHandoff} />
                {:else}
                    <div class="empty-state">404 — не знайдено</div>
                {/if}
            </main>

            <!-- ── Bottom Nav (mobile only — icon-only with underline) ── -->
            <nav class="bottom-nav">
                <button
                    class:active={route === "/gorn" || route === ""}
                    onclick={() => nav("/gorn")}
                    aria-label="ГОРН"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="flame" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/chat"}
                    onclick={() => nav("/chat")}
                    aria-label="Chat"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="chat" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/feed"}
                    onclick={() => nav("/feed")}
                    aria-label="Feed"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="feed" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/thinking"}
                    onclick={() => nav("/thinking")}
                    aria-label="Thinking"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="thinking" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/relationship"}
                    onclick={() => nav("/relationship")}
                    aria-label="Relationship"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="relationship" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/mind"}
                    onclick={() => nav("/mind")}
                    aria-label="Mind"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="mind" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/workspace"}
                    onclick={() => nav("/workspace")}
                    aria-label="Workspace"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="workspace" size={22} /></span></span>
                </button>
                <button
                    class:active={route === "/logs"}
                    onclick={() => nav("/logs")}
                    aria-label="Logs"
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name="logs" size={22} /></span></span>
                </button>
            </nav>
        </div>
    </div>
{/if}

<style>
    /* ── Auth Screen (premium redesign — orb presence + mood-driven glow) ── */
    .auth-screen {
        min-height: var(--app-vh, 100vh);
        display: flex;
        align-items: center;
        justify-content: center;
        position: relative;
        overflow: hidden;
        background:
            radial-gradient(1100px 760px at 50% -12%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 60%),
            radial-gradient(820px 640px at 86% 112%, color-mix(in srgb, var(--accent) 10%, transparent), transparent 55%),
            var(--bg);
    }
    .aura { position: absolute; inset: 0; pointer-events: none; z-index: 0; }
    .aura span { position: absolute; border-radius: 50%; filter: blur(60px); opacity: 0.5; }
    .aura span:nth-child(1) {
        width: 500px; height: 500px; left: 8%; top: 12%;
        background: radial-gradient(circle, color-mix(in srgb, var(--accent) 35%, transparent), transparent 70%);
        animation: aura-drift1 18s ease-in-out infinite;
    }
    .aura span:nth-child(2) {
        width: 440px; height: 440px; right: 6%; bottom: 8%;
        background: radial-gradient(circle, color-mix(in srgb, var(--accent) 26%, transparent), transparent 70%);
        animation: aura-drift2 22s ease-in-out infinite;
    }
    @keyframes aura-drift1 { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(40px,30px) scale(1.1); } }
    @keyframes aura-drift2 { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(-30px,-40px) scale(1.08); } }

    .auth-card {
        position: relative;
        z-index: 2;
        width: 380px;
        padding: 48px 40px 36px;
        background: color-mix(in srgb, var(--surface) 70%, transparent);
        backdrop-filter: blur(24px) saturate(140%);
        -webkit-backdrop-filter: blur(24px) saturate(140%);
        border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
        border-radius: 28px;
        box-shadow:
            0 30px 80px rgba(0, 0, 0, 0.55),
            0 1px 0 rgba(255, 255, 255, 0.06) inset;
        display: flex;
        flex-direction: column;
        align-items: center;
        animation: auth-rise 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) both;
    }
    @keyframes auth-rise { from { opacity: 0; transform: translateY(24px) scale(0.98); } to { opacity: 1; transform: none; } }

    /* living Archi presence */
    .orb-wrap { position: relative; width: 132px; height: 132px; margin-bottom: 24px; display: grid; place-items: center; }
    .ring { position: absolute; inset: 0; border-radius: 50%; border: 1px solid var(--accent); opacity: 0; }
    .ring.r1 { animation: orb-pulse 3.6s ease-out infinite; }
    .ring.r2 { animation: orb-pulse 3.6s ease-out infinite 1.2s; }
    .ring.r3 { animation: orb-pulse 3.6s ease-out infinite 2.4s; }
    @keyframes orb-pulse { 0% { transform: scale(0.55); opacity: 0.7; } 100% { transform: scale(1.15); opacity: 0; } }
    .orb {
        width: 84px; height: 84px; border-radius: 50%;
        background: radial-gradient(circle at 35% 30%, #fff 0%, color-mix(in srgb, var(--accent) 55%, #fff) 22%, var(--accent) 55%, color-mix(in srgb, var(--accent) 72%, #000) 100%);
        box-shadow:
            0 0 50px 6px color-mix(in srgb, var(--accent) 55%, transparent),
            0 0 100px 18px color-mix(in srgb, var(--accent) 25%, transparent),
            0 8px 30px rgba(0, 0, 0, 0.4);
        animation: orb-breathe 4.2s ease-in-out infinite;
        position: relative;
    }
    .orb::after {
        content: ''; position: absolute; inset: 0; border-radius: 50%;
        background: radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.7), transparent 38%);
        mix-blend-mode: screen;
    }
    @keyframes orb-breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.06); } }

    .auth-title {
        font-size: 30px; font-weight: 700; letter-spacing: -0.02em;
        background: linear-gradient(180deg, #fff, color-mix(in srgb, var(--accent) 30%, #fff));
        -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .auth-sub {
        margin-top: 8px; font-size: 13.5px; color: var(--text-muted);
        text-align: center; line-height: 1.5;
    }
    .auth-sub b { color: color-mix(in srgb, var(--accent) 55%, #fff); font-weight: 500; }
    .auth-form { width: 100%; margin-top: 28px; display: flex; flex-direction: column; gap: 12px; }
    .field { position: relative; }
    .token-input {
        width: 100%; padding: 13px 15px;
        background: color-mix(in srgb, var(--bg) 60%, transparent);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 13px; color: var(--text);
        font-family: var(--font-mono); font-size: 13px; outline: none;
        transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
    }
    .token-input::placeholder { color: var(--text-muted); }
    .token-input:focus {
        border-color: var(--accent);
        background: color-mix(in srgb, var(--bg) 85%, transparent);
        box-shadow:
            0 0 0 4px color-mix(in srgb, var(--accent) 14%, transparent),
            0 0 30px color-mix(in srgb, var(--accent) 18%, transparent);
    }
    .btn-primary {
        margin-top: 4px; padding: 13px; border: none; border-radius: 13px;
        color: #fff; font-size: 14.5px; font-weight: 600; cursor: pointer;
        background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 70%, #fff), var(--accent) 55%, color-mix(in srgb, var(--accent) 80%, #000));
        box-shadow:
            0 8px 24px color-mix(in srgb, var(--accent) 35%, transparent),
            0 0 0 1px rgba(255, 255, 255, 0.08) inset;
        transition: transform 0.12s, box-shadow 0.2s, filter 0.2s;
    }
    .btn-primary:hover {
        transform: translateY(-1px); filter: brightness(1.08);
        box-shadow:
            0 12px 34px color-mix(in srgb, var(--accent) 50%, transparent),
            0 0 0 1px rgba(255, 255, 255, 0.12) inset;
    }
    .btn-primary:active { transform: translateY(0); }
    .auth-error { font-size: 12px; color: var(--danger); margin-top: 4px; }

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
    .brand {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 6px 8px 14px;
    }
    .brand-orb {
        width: 26px;
        height: 26px;
        border-radius: 50%;
        flex-shrink: 0;
        position: relative;
        /* Приглушена сфера настрою — без білого ядра й halo, сидить у темі.
           Login-orb лишається ярким героєм; цей — тиха периферійна присутність. */
        background: radial-gradient(circle at 36% 32%,
            color-mix(in srgb, var(--accent) 50%, #fff) 0%,
            var(--accent) 48%,
            color-mix(in srgb, var(--accent) 72%, #000) 100%);
        /* Тільки тонкий rim для об'єму — нуль світіння в кутку */
        box-shadow:
            0 0 0 1px color-mix(in srgb, var(--accent) 20%, transparent),
            0 2px 8px -3px color-mix(in srgb, var(--accent) 30%, transparent);
        animation: brand-breathe 6s ease-in-out infinite;
    }
    .brand-orb::after {
        content: '';
        position: absolute;
        inset: 0;
        border-radius: 50%;
        /* Тонкий sheen для об'єму, без screen-перепалу */
        background: radial-gradient(circle at 34% 30%, rgba(255, 255, 255, 0.22), transparent 46%);
    }
    /* Тихе дихання: лише opacity + мікро-scale, БЕЗ пульсації світіння */
    @keyframes brand-breathe {
        0%, 100% { transform: scale(1); opacity: 0.78; }
        50% { transform: scale(1.03); opacity: 0.92; }
    }
    .brand-name {
        font-size: 16px;
        font-weight: 700;
        letter-spacing: -0.01em;
        color: var(--text);
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
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
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
        /* Ambient «світло зсередини» живе тут (стабільно, не скролиться),
           контент прозорий → аура просвічує крізь скляні картки */
        background: var(--ambient), var(--bg);
    }
    .content {
        flex: 1;
        overflow-y: auto;
        background: transparent;
    }
    /* Chat needs its own internal scroll — disable outer scroll */
    .content.chat-layout {
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }
    /* ГОРН — присутність на весь екран, без зовнішнього скролу */
    .content.gorn-layout {
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

    /* Mood colors — 16 станів, 4 набори (синхронізовано з MOOD_COLORS + бот whitelist) */
    /* ⚙ робочий стан */
    .mood-dot[data-mood="focused"] { background: #22cc8f; animation-duration: 2s; }
    .mood-dot[data-mood="analytical"] { background: #2dd4bf; animation-duration: 2.4s; }
    .mood-dot[data-mood="alert"] { background: #f5a623; animation-duration: 1.5s; }
    .mood-dot[data-mood="cautious"] { background: #fbbf24; animation-duration: 1.8s; }
    .mood-dot[data-mood="determined"] { background: #10b981; animation-duration: 1.8s; }
    /* ◈ впевненість */
    .mood-dot[data-mood="confident"] { background: #3b82f6; animation-duration: 2.2s; }
    .mood-dot[data-mood="uncertain"] { background: #94a3b8; animation-duration: 2.6s; }
    .mood-dot[data-mood="conflicted"] { background: #a78bfa; animation-duration: 1.4s; }
    /* ✦ позитив */
    .mood-dot[data-mood="calm"] { background: #60a5fa; animation-duration: 3s; }
    .mood-dot[data-mood="excited"] { background: #c084fc; animation-duration: 1.2s; }
    .mood-dot[data-mood="satisfied"] { background: #4ade80; animation-duration: 2.8s; }
    .mood-dot[data-mood="hopeful"] { background: #5eead4; animation-duration: 2.5s; }
    .mood-dot[data-mood="curious"] { background: #38bdf8; animation-duration: 1.6s; }
    /* ⚠ напруга */
    .mood-dot[data-mood="frustrated"] { background: #f87171; animation-duration: 0.8s; }
    .mood-dot[data-mood="tense"] { background: #fb7185; animation-duration: 1s; }
    .mood-dot[data-mood="weary"] { background: #8b8ba7; animation-duration: 3.2s; }

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
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
    }
</style>
