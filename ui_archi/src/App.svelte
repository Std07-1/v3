<script lang="ts">
    import "./lib/theme.css";
    import { getToken, setToken, api } from "./lib/api";
    import type { Directives } from "./lib/types";
    import Feed from "./views/Feed.svelte";
    import Thinking from "./views/Thinking.svelte";
    import Relationship from "./views/Relationship.svelte";
    import Chat from "./views/Chat.svelte";
    import Mind from "./views/Mind.svelte";

    // ── routing (hash-based) ──
    let route = $state(window.location.hash.replace("#", "") || "/chat");
    window.addEventListener("hashchange", () => {
        route = window.location.hash.replace("#", "") || "/chat";
    });

    function nav(path: string) {
        window.location.hash = path;
    }

    // ── chat prefill (from Thinking quick reply) ──
    let chatPrefill = $state("");

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
        fetchDirectives();
    }

    // ── directives (shell-level, shared across views) ──
    let directives = $state<Directives | null>(null);

    async function fetchDirectives() {
        try {
            directives = await api.directives(true);
        } catch {
            // silent — directives are non-critical
        }
    }

    $effect(() => {
        if (token) {
            fetchDirectives();
            const id = setInterval(fetchDirectives, 30_000);
            return () => clearInterval(id);
        }
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
            </ul>

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
                            <span class="dir-val">{directives.mood}</span>
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
                        <div class="dir-row">
                            <span class="dir-label">сценарій</span>
                            <span class="dir-val scenario"
                                >{directives.active_scenario}</span
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

        <div class="right-panel">
            <!-- ── Status HUD ── -->
            {#if directives}
                <div class="status-hud">
                    {#if directives.kill_switch_active}
                        <span class="hud-chip danger">⛔ KILL</span>
                    {/if}
                    {#if directives.economy_mode_active}
                        <span class="hud-chip warning">⚡ ECO</span>
                    {/if}
                    {#if directives.mode}
                        <span class="hud-chip mode">{directives.mode}</span>
                    {/if}
                    {#if directives.focus_symbol}
                        <span class="hud-chip gold"
                            >{directives.focus_symbol}</span
                        >
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
                        <span class="hud-chip muted"
                            >~{isNaN(_total)
                                ? "?"
                                : Math.round(_total / 1000) + "k"} токенів</span
                        >
                    {/if}
                </div>
            {/if}

            <main
                class="content"
                class:chat-layout={route === "/chat" || route === ""}
            >
                {#if route === "/chat" || route === ""}
                    <Chat prefill={chatPrefill} />
                {:else if route === "/feed"}
                    <Feed />
                {:else if route === "/thinking"}
                    <Thinking
                        onchat={(text: string) => {
                            chatPrefill = text;
                            nav("/chat");
                        }}
                    />
                {:else if route === "/relationship"}
                    <Relationship />
                {:else if route === "/mind"}
                    <Mind />
                {:else}
                    <div class="empty-state">404 — не знайдено</div>
                {/if}
            </main>

            <!-- ── Bottom Nav (mobile only) ── -->
            <nav class="bottom-nav">
                <button
                    class:active={route === "/chat" || route === ""}
                    onclick={() => nav("/chat")}
                >
                    <span class="bn-icon">💬</span>
                    <span class="bn-label">Chat</span>
                </button>
                <button
                    class:active={route === "/feed"}
                    onclick={() => nav("/feed")}
                >
                    <span class="bn-icon">⚡</span>
                    <span class="bn-label">Feed</span>
                </button>
                <button
                    class:active={route === "/thinking"}
                    onclick={() => nav("/thinking")}
                >
                    <span class="bn-icon">🧠</span>
                    <span class="bn-label">Thinking</span>
                </button>
                <button
                    class:active={route === "/relationship"}
                    onclick={() => nav("/relationship")}
                >
                    <span class="bn-icon">💙</span>
                    <span class="bn-label">Relation</span>
                </button>
                <button
                    class:active={route === "/mind"}
                    onclick={() => nav("/mind")}
                >
                    <span class="bn-icon">🧩</span>
                    <span class="bn-label">Mind</span>
                </button>
            </nav>
        </div>
    </div>
{/if}

<style>
    /* ── Auth Screen ── */
    .auth-screen {
        min-height: 100vh;
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
        height: 100vh;
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

    /* ── Status HUD ── */
    .status-hud {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 16px;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        flex-wrap: wrap;
    }
    .hud-chip {
        font-size: 11px;
        font-weight: 500;
        padding: 2px 8px;
        border-radius: 10px;
        background: var(--surface2);
        color: var(--text-muted);
        border: 1px solid var(--border);
        white-space: nowrap;
    }
    .hud-chip.danger {
        background: rgba(239, 68, 68, 0.15);
        color: #f87171;
        border-color: rgba(239, 68, 68, 0.3);
    }
    .hud-chip.warning {
        background: rgba(245, 158, 11, 0.15);
        color: #fbbf24;
        border-color: rgba(245, 158, 11, 0.3);
    }
    .hud-chip.gold {
        background: rgba(251, 191, 36, 0.12);
        color: #fbbf24;
        border-color: rgba(251, 191, 36, 0.3);
    }
    .hud-chip.mode {
        background: var(--accent-dim);
        color: var(--accent);
        border-color: transparent;
    }
    .hud-chip.muted {
        color: var(--text-muted);
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

    /* ── Mobile ── */
    @media (max-width: 768px) {
        .sidebar {
            display: none;
        }
        .content {
            padding-bottom: 60px; /* reserve space for bottom nav */
        }
        .bottom-nav {
            display: flex;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 56px;
            background: var(--surface);
            border-top: 1px solid var(--border);
            z-index: 100;
            safe-area-inset-bottom: env(safe-area-inset-bottom);
        }
        .bottom-nav button {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 2px;
            background: none;
            border: none;
            cursor: pointer;
            color: var(--text-muted);
            transition: color 0.15s;
        }
        .bottom-nav button.active {
            color: var(--accent);
        }
        .bn-icon {
            font-size: 20px;
            line-height: 1;
        }
        .bn-label {
            font-size: 10px;
            font-weight: 500;
        }
        .status-hud {
            padding: 4px 12px;
        }
    }
</style>
