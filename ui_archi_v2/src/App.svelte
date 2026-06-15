<script lang="ts">
    import "./lib/theme.css";
    import { getToken, setToken, api } from "./lib/api";
    import type { ChatHandoff } from "./lib/types";
    import { onMount } from "svelte";
    import {
        getDirectives,
        getAgentState,
        refreshDirectives,
        applyDirectives,
        startPolling,
        stopPolling,
    } from "./lib/state.svelte";
    import type { Directives } from "./lib/types";
    import PresenceLayer from "./features/presence/PresenceLayer.svelte";
    import { derivePresenceMode, type PresenceMode } from "./features/presence/presenceState";
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
        if (path !== "/gorn" && path !== "" && path !== route) ringWake += 1; // перехід у view → іскри
        window.location.hash = path;
    }

    // ── Постійна присутність (Slice C): кільце на рівні застосунку ──
    let agentStateP = $derived(getAgentState());
    let presenceMode = $state<PresenceMode>("sleep");
    let ringAccent = $state("#7c6fff");
    let ringWake = $state(0);
    let presenceFocused = $derived(!(route === "/gorn" || route === ""));
    let _lastThought: string | null = null;
    let _lastImpulseMs = 0;
    function presenceTick(): void {
        const now = Date.now();
        const st = getAgentState();
        const dir = getDirectives();
        if (!_lastImpulseMs && typeof st?.ts_ms === "number" && st.ts_ms) _lastImpulseMs = st.ts_ms;
        ringAccent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || ringAccent;
        const th = ((st?.inner_thought as string) || (dir?.inner_thought as string) || "").trim();
        if (th && th !== _lastThought) { if (_lastThought !== null) _lastImpulseMs = now; _lastThought = th; }
        presenceMode = derivePresenceMode(st, dir, _lastImpulseMs, now);
    }
    $effect(() => { void agentStateP; void getDirectives(); presenceTick(); });
    onMount(() => { const id = setInterval(presenceTick, 1000); return () => clearInterval(id); });

    // ── C2: ГОРН = дім (кільце в центрі), будь-який view = центр-вікно ──
    // Арчі тягне вікно з глибини у фокус (іскри вже б'ють із nav), сам відступає вбік.
    // ✕ або клік-по-Арчі забирають увагу → вікно тоне, кільце вертається в центр.
    let panelRoute = $state<string | null>(null); // який view у вікні (null = дім)
    let windowShown = $state(false);               // драйвить матеріалізацію з глибини
    let winTimer: ReturnType<typeof setTimeout> | null = null;
    function clearWinTimer() { if (winTimer) { clearTimeout(winTimer); winTimer = null; } }
    function syncWindow(r: string): void {
        clearWinTimer(); // race-fix: один перехід за раз (як clearTimers у study)
        if (r === "/gorn" || r === "") {
            windowShown = false; // вікно тоне в глибину
            winTimer = setTimeout(() => {
                if (route === "/gorn" || route === "") panelRoute = null;
            }, 620);
        } else {
            panelRoute = r;      // контент вантажиться за blur (у глибині)
            windowShown = false; // гарантуємо from-стан, щоб перехід відіграв
            winTimer = setTimeout(() => { windowShown = true; }, 140);
        }
    }
    $effect(() => { syncWindow(route); });

    // ── ГОРН focus-room: сайдбар геть, жаринки-нав reveal на hover/edge-touch ──
    const NAV = [
        { path: "/gorn", icon: "flame", label: "ГОРН" },
        { path: "/chat", icon: "chat", label: "Chat" },
        { path: "/feed", icon: "feed", label: "Feed" },
        { path: "/thinking", icon: "thinking", label: "Thinking" },
        { path: "/relationship", icon: "relationship", label: "Relationship" },
        { path: "/mind", icon: "mind", label: "Mind" },
        { path: "/workspace", icon: "workspace", label: "Workspace" },
        { path: "/logs", icon: "logs", label: "Logs" },
    ];
    let navRevealed = $state(false);
    let navHideTimer: ReturnType<typeof setTimeout> | null = null;
    function scheduleNavHide(delay = 2600) {
        if (navHideTimer) clearTimeout(navHideTimer);
        navHideTimer = setTimeout(() => { navRevealed = false; }, delay);
    }
    function revealNav() {
        navRevealed = true;
        scheduleNavHide();
    }
    function holdNav() {
        if (navHideTimer) { clearTimeout(navHideTimer); navHideTimer = null; }
    }
    function isNavActive(path: string): boolean {
        return route === path || (path === "/gorn" && route === "");
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
    <div class="shell" class:viewing={presenceFocused}>
        <PresenceLayer
            mode={presenceMode}
            accent={ringAccent}
            focused={presenceFocused}
            wakeNonce={ringWake}
            onArchiClick={() => nav("/gorn")}
        />

        <!-- Спільне поле (концепт 6): кільце+вікно в одній аурі світла, що м'яко дихає.
             Несе «вони разом»; вікно саме стоїть спокійно. З'являється лише у фокусі. -->
        <div class="presence-field" class:on={presenceFocused}></div>

        <!-- жаринки-нав: hover (десктоп) кличе стрічку. На ВСІХ маршрутах (focus-everywhere). -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            class="edge-trigger"
            onmouseenter={revealNav}
            ontouchstart={revealNav}
        ></div>
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <nav
            class="ember-nav"
            class:revealed={navRevealed}
            onmouseenter={holdNav}
            onmouseleave={() => scheduleNavHide()}
            ontouchstart={holdNav}
        >
            {#each NAV as item}
                <button
                    class="ember"
                    class:active={isNavActive(item.path)}
                    title={item.label}
                    aria-label={item.label}
                    onclick={() => nav(item.path)}
                >
                    <Icon name={item.icon} size={20} />
                </button>
            {/each}
        </nav>

        <!-- тихі універсальні контроли (були в сайдбарі): сповіщення + вихід -->
        <div class="app-controls">
            <button
                class="ctl"
                class:active={notifEnabled}
                onclick={toggleNotifications}
                title={notifEnabled ? "Сповіщення увімкнено" : "Увімкнути сповіщення"}
                aria-label="Сповіщення"
            >
                <Icon name={notifEnabled ? "bell" : "belloff"} size={16} />
            </button>
            <button
                class="ctl"
                onclick={() => { setToken(""); token = ""; }}
                title="Вийти"
                aria-label="Вийти"
            >
                <Icon name="logout" size={16} />
            </button>
        </div>
        <!-- ДІМ: ГОРН — кільце по центру (PresenceLayer) + слова Арчі. Тане, коли є фокус. -->
        <div class="home-layer" class:dim={presenceFocused}>
            <Gorn />
        </div>

        <!-- ВІКНО: будь-який view = центр-вікно, що Арчі тягне з глибини у фокус.
             ✕ або клік-по-Арчі (PresenceLayer) забирають увагу → вікно тоне. -->
        <div class="window-overlay">
            {#if panelRoute}
                <div class="window-dock">
                    <section class="center-window" class:shown={windowShown}>
                        <div class="window-body">
                            {#if panelRoute === "/chat"}
                                <Chat {...getChatProps()} />
                            {:else if panelRoute === "/feed"}
                                <Feed onchat={openChatHandoff} />
                            {:else if panelRoute === "/thinking"}
                                <Thinking
                                    onchat={(text: string) => {
                                        openChatDraft(text);
                                    }}
                                />
                            {:else if panelRoute === "/relationship"}
                                <Relationship onchat={openChatHandoff} />
                            {:else if panelRoute === "/mind"}
                                <Mind onchat={openChatHandoff} />
                            {:else if panelRoute === "/workspace"}
                                <Workspace onchat={openChatHandoff} />
                            {:else if panelRoute === "/logs"}
                                <Logs onchat={openChatHandoff} />
                            {/if}
                        </div>
                    </section>
                    <!-- ✕ за кутом вікна (window-dock шринк-врап) → не б'ється з хедерами views -->
                    <button
                        class="window-close"
                        class:visible={windowShown}
                        onclick={() => nav("/gorn")}
                        aria-label="Закрити"
                        title="Закрити (або клік по Арчі)">✕</button>
                </div>
            {/if}
        </div>

        <!-- Bottom Nav (mobile only) -->
        <nav class="bottom-nav">
            {#each NAV as item}
                <button
                    class:active={isNavActive(item.path)}
                    onclick={() => nav(item.path)}
                    aria-label={item.label}
                >
                    <span class="bn-pill"><span class="bn-icon"><Icon name={item.icon} size={22} /></span></span>
                </button>
            {/each}
        </nav>
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

    /* ── Shell (C2: фіксовані шари — кільце, дім, вікно — один поверх одного) ── */
    .shell {
        position: relative;
        height: var(--app-vh, 100vh);
        overflow: hidden;
    }

    /* ── Тихі універсальні контроли (top-right): сповіщення + вихід ── */
    .app-controls {
        position: fixed;
        top: 14px;
        right: 16px;
        z-index: 65;
        display: flex;
        gap: 6px;
        opacity: 0.42;
        transition: opacity 0.2s ease;
    }
    .app-controls:hover {
        opacity: 1;
    }
    .ctl {
        width: 34px;
        height: 34px;
        display: grid;
        place-items: center;
        background: color-mix(in srgb, var(--surface) 55%, transparent);
        backdrop-filter: blur(12px) saturate(140%);
        -webkit-backdrop-filter: blur(12px) saturate(140%);
        border: 1px solid color-mix(in srgb, var(--accent) 12%, var(--border));
        border-radius: 10px;
        color: var(--text-muted);
        cursor: pointer;
        transition: color 0.15s, border-color 0.15s, background 0.15s;
    }
    .ctl:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
    }
    .ctl.active {
        color: var(--accent);
        border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
    }
    .edge-trigger {
        position: fixed;
        left: 0;
        top: 0;
        bottom: 0;
        width: 24px;
        z-index: 50;
    }
    .ember-nav {
        position: fixed;
        left: 0;
        top: 50%;
        transform: translate(-118%, -50%);
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 10px 8px;
        z-index: 60;
        background: color-mix(in srgb, var(--surface) 52%, transparent);
        backdrop-filter: blur(16px) saturate(140%);
        -webkit-backdrop-filter: blur(16px) saturate(140%);
        border: 1px solid color-mix(in srgb, var(--accent) 14%, var(--border));
        border-left: none;
        border-radius: 0 16px 16px 0;
        box-shadow: 0 24px 60px -26px rgba(0, 0, 0, 0.6);
        opacity: 0;
        transition:
            transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1),
            opacity 0.3s ease;
    }
    .ember-nav.revealed {
        transform: translate(0, -50%);
        opacity: 1;
    }
    .ember {
        width: 40px;
        height: 40px;
        display: grid;
        place-items: center;
        background: none;
        border: none;
        border-radius: 12px;
        color: color-mix(in srgb, var(--accent) 50%, var(--text-muted));
        opacity: 0.62;
        cursor: pointer;
        transition:
            opacity 0.15s,
            background 0.15s,
            color 0.15s,
            transform 0.15s;
    }
    .ember:hover {
        opacity: 1;
        background: color-mix(in srgb, var(--accent) 12%, transparent);
        color: var(--text);
        transform: translateX(2px);
    }
    .ember.active {
        opacity: 1;
        color: var(--accent);
        background: color-mix(in srgb, var(--accent) 14%, transparent);
    }
    /* ── Спільне поле (концепт 6): аура світла, що огортає кільце+вікно й дихає ── */
    .presence-field {
        position: fixed;
        inset: 0;
        z-index: 2; /* над кільцем (z:1), під вікном (z:3) */
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.7s ease;
        /* радіус між кільцем (зліва) і вікном (центр) → обидва всередині одного поля */
        background: radial-gradient(
            58% 52% at 40% 50%,
            color-mix(in srgb, var(--accent) 15%, transparent),
            transparent 70%
        );
    }
    .presence-field.on {
        opacity: 1;
        /* дихання поля = масштаб м'якого гало (без opacity-блимання) */
        animation: field-breathe 6.5s ease-in-out infinite;
    }
    @keyframes field-breathe {
        0%, 100% { transform: scale(1); filter: brightness(0.82); }
        50% { transform: scale(1.07); filter: brightness(1.3); }
    }

    /* ── ДІМ-шар: ГОРН (кільце по центру дає PresenceLayer, тут слова) ── */
    .home-layer {
        position: fixed;
        inset: 0;
        z-index: 2; /* над кільцем (z:1), під вікном (z:3) */
        display: flex;
        flex-direction: column;
        pointer-events: none; /* слова не інтерактивні; кліки йдуть до кільця */
        transition: opacity 0.5s ease, filter 0.5s ease;
    }
    .home-layer.dim {
        opacity: 0;
        filter: blur(6px);
    }

    /* ── ВІКНО-шар: view виринає з глибини у центр (Арчі тягне у фокус) ── */
    .window-overlay {
        position: fixed;
        inset: 0;
        z-index: 3;
        display: flex;
        align-items: center;
        justify-content: center;
        pointer-events: none; /* поза вікном кліки проходять до кільця-хотспота */
    }
    .window-dock {
        position: relative; /* шринк-врап вікна → ✕ виноситься за його кут */
        pointer-events: auto;
        transform-origin: center center;
        /* вікно стоїть майже спокійно (Стас: не дихає / ледь) — 0.25%, повільно.
           «Дихають разом» несе поле-аура (.presence-field), не саме вікно. */
        animation: dock-breathe 7s ease-in-out infinite;
    }
    @keyframes dock-breathe {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.0025); }
    }
    .center-window {
        position: relative;
        /* C3 «більша присутність»: домінує на широких екранах, кільце дихає зліва */
        width: min(70vw, 1240px);
        height: min(90vh, 920px);
        /* Преміум-картка «вийнята з глибини»: скло + глибока тінь + mood-halo + forge-тепло */
        background: color-mix(in srgb, var(--surface) 88%, transparent);
        backdrop-filter: blur(24px) saturate(150%);
        -webkit-backdrop-filter: blur(24px) saturate(150%);
        border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--border));
        border-radius: 22px;
        box-shadow:
            0 60px 150px -50px rgba(0, 0, 0, 0.82),
            0 0 120px -36px color-mix(in srgb, var(--accent) 30%, transparent),
            0 -3px 40px -14px color-mix(in srgb, #ff9a4d 26%, transparent),
            inset 0 1px 0 color-mix(in srgb, #ffcaa0 34%, transparent);
        overflow: hidden;
        opacity: 0;
        visibility: hidden;
        transform: scale(0.66);
        filter: blur(22px);
        transition:
            opacity 0.5s ease,
            filter 0.5s ease,
            transform 0.6s cubic-bezier(0.2, 0.85, 0.25, 1),
            visibility 0.5s;
    }
    .center-window.shown {
        opacity: 1;
        visibility: visible;
        transform: scale(1);
        filter: blur(0);
        /* вхід = depth (V1, обрано Стасом на всі views): перекриває transition на відкритті;
           закриття = recede через transition (коли .shown знято) */
        animation: win-enter 0.74s cubic-bezier(0.16, 0.84, 0.24, 1.06) both;
    }
    /* depth — справжня 3D-глибина: летить у Z з нахилом, тьмяне+блюр → наперед із догоном */
    @keyframes win-enter {
        from { opacity: 0; visibility: visible; transform: perspective(1200px) translateZ(-360px) rotateX(7deg); filter: blur(20px) brightness(0.62); }
        58% { opacity: 1; }
        to { opacity: 1; visibility: visible; transform: perspective(1200px) translateZ(0) rotateX(0deg); filter: blur(0) brightness(1); }
    }
    .window-body {
        width: 100%;
        height: 100%;
        display: flex;
        flex-direction: column;
        min-height: 0; /* щоб view (height:100%) скролив усередині */
    }
    .window-close {
        position: absolute;
        top: -14px;
        right: -14px;
        z-index: 6;
        width: 34px;
        height: 34px;
        display: grid;
        place-items: center;
        border-radius: 50%;
        background: color-mix(in srgb, var(--surface2) 92%, var(--bg));
        border: 1px solid color-mix(in srgb, var(--accent) 24%, var(--border));
        box-shadow: 0 8px 22px -8px rgba(0, 0, 0, 0.6);
        color: var(--text-muted);
        font-size: 15px;
        line-height: 1;
        cursor: pointer;
        opacity: 0;
        transform: scale(0.8);
        transition:
            color 0.15s,
            border-color 0.15s,
            background 0.15s,
            opacity 0.3s ease 0.12s,
            transform 0.3s cubic-bezier(0.2, 0.85, 0.25, 1) 0.12s;
    }
    .window-close.visible {
        opacity: 1;
        transform: scale(1);
    }
    .window-close:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
        background: var(--surface2);
    }

    /* ── Bottom Nav (hidden on desktop) ── */
    .bottom-nav {
        display: none;
    }

    /* ── Mobile ── */
    @media (max-width: 768px) {
        /* мобілка: нав = bottom-nav; жаринки-стрічка десктопна — геть */
        .edge-trigger,
        .ember-nav {
            display: none;
        }
        .app-controls {
            top: 10px;
            right: 10px;
        }
        /* у фокусі вікно-аркуш має власну ✕ — контроли ховаємо, щоб не накладались */
        .shell.viewing .app-controls {
            opacity: 0;
            pointer-events: none;
        }

        /* вікно = аркуш знизу; кільце-якір угорі лишається видимим */
        .window-overlay {
            align-items: flex-end;
            padding-bottom: calc(56px + env(safe-area-inset-bottom));
            transition: padding-bottom 0.22s ease;
        }
        .center-window {
            width: 96vw;
            height: calc(var(--app-vh, 100vh) * 0.72);
            border-radius: 20px 20px 0 0;
            transform-origin: bottom center;
        }
        /* мобілка: desktop-depth-вхід (перспектива) не пасує аркушу — лишаємо transition (sheet) */
        .center-window.shown {
            animation: none;
        }
        /* мобілка: ✕ всередину кута (за кутом — зрізалось би краєм екрана на аркуші 96vw) */
        .window-close {
            top: 8px;
            right: 8px;
        }
        /* мобілка: поле зміщене вгору — кільце-якір угорі, аркуш знизу */
        .presence-field {
            background: radial-gradient(
                70% 46% at 50% 40%,
                color-mix(in srgb, var(--accent) 15%, transparent),
                transparent 70%
            );
        }
        /* клавіатура: вікно на весь, bottom-nav та відступ геть */
        :global(body.keyboard-open) .window-overlay {
            padding-bottom: 0;
        }
        :global(body.keyboard-open) .center-window {
            height: calc(var(--app-vh, 100vh) - 56px);
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
        :global(body.keyboard-open) .bottom-nav {
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
