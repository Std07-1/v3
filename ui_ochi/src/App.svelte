<!--
    Очі Арчі — консоль спостереження за автономним агентом «його очима».

    Дві зони: PresenceHeader (стан зараз) зверху + WakeFilm (плівка пробуджень) під
    ним. Token-gate як ui_archi_v2: без токена — форма входу (той самий ключ
    archi_token → токен шериться між консолями). mood → --accent (directional
    formatting, X28). nowMs-тікер (1s) живить відносний час у обох зонах.
-->
<script lang="ts">
    import "./lib/theme.css";
    import { onMount } from "svelte";
    import { getToken, setToken } from "./lib/api";
    import {
        startNowPolling,
        stopNowPolling,
        getNow,
        isOffline,
    } from "./stores/nowStore.svelte";
    import PresenceHeader from "./features/presence/PresenceHeader.svelte";
    import WakeFilm from "./features/film/WakeFilm.svelte";

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
    }

    // ── presence snapshot (reactive via module-level $state) ──
    let nowSnap = $derived(getNow());
    let offline = $derived(isOffline());

    // ── живий відносний час: єдиний 1s-тікер на обидві зони ──
    let nowMs = $state(Date.now());

    // ── mood → --accent (колір орба = живий настрій Арчі; directional, не домен) ──
    const MOOD_COLORS: Record<string, string> = {
        focused: "#22CC8F",
        analytical: "#2DD4BF",
        alert: "#F5A623",
        cautious: "#FBBF24",
        determined: "#10B981",
        confident: "#3B82F6",
        uncertain: "#94A3B8",
        conflicted: "#A78BFA",
        calm: "#60A5FA",
        excited: "#C084FC",
        satisfied: "#4ADE80",
        hopeful: "#5EEAD4",
        curious: "#38BDF8",
        frustrated: "#F87171",
        tense: "#FB7185",
        weary: "#8B8BA7",
    };
    const DEFAULT_ACCENT = "#7c6fff";

    $effect(() => {
        const mood = nowSnap?.directives?.mood ?? nowSnap?.state?.mood ?? "";
        const color = (mood && MOOD_COLORS[mood]) || DEFAULT_ACCENT;
        document.documentElement.style.setProperty("--accent", color);
    });

    // ── polling lifecycle (лише коли є токен) ──
    $effect(() => {
        if (token) {
            startNowPolling();
            return () => stopNowPolling();
        }
    });

    onMount(() => {
        const tick = setInterval(() => {
            nowMs = Date.now();
        }, 1000);
        return () => clearInterval(tick);
    });
</script>

{#if !token}
    <!-- ── Token gate (як ui_archi_v2: living orb + mood-driven accent) ── -->
    <div class="auth-screen">
        <div class="aura"><span></span><span></span></div>
        <div class="auth-card">
            <div class="orb-wrap">
                <div class="ring r1"></div>
                <div class="ring r2"></div>
                <div class="ring r3"></div>
                <div class="orb"></div>
            </div>
            <h1 class="auth-title">Очі Арчі</h1>
            <p class="auth-sub">
                Дивись, як <b>живе його розум</b>.<br />Приватний доступ.
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
    <!-- ── App: дві зони ── -->
    <div class="page">
        <div class="col">
            <PresenceHeader now={nowSnap} {offline} {nowMs} />
            <WakeFilm {nowMs} />
        </div>
    </div>
{/if}

<style>
    /* ── page ── */
    .page {
        min-height: 100vh;
        background: var(--bg);
        background-image: var(--ambient);
    }
    .col {
        max-width: 720px;
        margin: 0 auto;
        padding: 24px 16px 64px;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    /* ── Auth Screen (orb presence + mood-driven glow) ── */
    .auth-screen {
        min-height: 100vh;
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
    @media (prefers-reduced-motion: reduce) {
        .orb, .ring, .aura span { animation: none; }
    }

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
</style>
