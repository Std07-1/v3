<!--
    ГОРН — публічна поверхня. Живе кільце Арчі як головна.

    «Ти не відкриваєш застосунок — ти заходиш у ГОРН, де Арчі вже при роботі»
    (PRESENCE_CONCEPT.md). Кільце — герой: дихає, колір = його mood, спалахує коли
    приходить нова думка (реальна wake-подія). Слова під ним = його жива думка.

    Нічого не заповнюється руками (пункт Стаса 2026-07-11): усе тече з того, що Арчі
    вже емітить — mood / inner_thought / active_scenario / wake_conditions. Канал —
    де він говорить; ця сторінка — де його видно живим. Ніяких внутрішніх екранів,
    ніякого входу: один публічний трейдерський/інвесторський застосунок.

    X28: dumb renderer. Стан приходить з /api/agent/state + /api/archi/directives,
    фронт лише деривує колір/темп/яскравість (directional formatting), не домен.
-->
<script lang="ts">
    import "./lib/theme.css";
    import { onMount } from "svelte";
    import PresenceLayer from "./features/presence/PresenceLayer.svelte";
    import {
        derivePresenceMode,
        type PresenceMode,
    } from "./features/presence/presenceState";
    import {
        getSnapshot,
        startSnapshotPolling,
        stopSnapshotPolling,
    } from "./lib/publicSnapshot.svelte";
    import type { AgentState, Directives } from "./lib/types";
    import Home from "./views/Home.svelte";

    let snapshot = $derived(getSnapshot());
    let presence = $derived(snapshot?.presence ?? null);

    // ── mood → --accent (колір кільця = живий настрій Арчі) ──
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
        const mood = presence?.mood;
        const color = (mood && MOOD_COLORS[mood]) || DEFAULT_ACCENT;
        document.documentElement.style.setProperty("--accent", color);
    });

    // ── присутність: mode + wake-спалах на нову думку ──
    let presenceMode = $state<PresenceMode>("sleep");
    let ringAccent = $state(DEFAULT_ACCENT);
    let ringWake = $state(0);
    let idle = $state(false);
    let _lastThought: string | null = null;
    let _lastImpulseMs = 0;
    let _readyForWake = false; // grace: не «будити» на першому осіданні стану

    function publicContentKey(): string {
        // імпульс = зміна ПУБЛІЧНОГО контенту (теза/напрям/mood/рівні) — сирі думки
        // зі знімка прибрано (owner 12.07: не для інвесторів, ADR-0086 rev1)
        const snap = getSnapshot();
        if (!snap) return "";
        const focus = snap.focus;
        return [
            focus?.thesis ?? "",
            focus?.direction ?? "",
            snap.presence.mood,
            snap.watching.map((w) => w.level).join(","),
        ].join("|");
    }

    function presenceTick(): void {
        const now = Date.now();
        const snap = getSnapshot();
        const pres = snap?.presence ?? null;
        if (!_lastImpulseMs && typeof pres?.ts_ms === "number" && pres.ts_ms)
            _lastImpulseMs = pres.ts_ms;
        ringAccent =
            getComputedStyle(document.documentElement)
                .getPropertyValue("--accent")
                .trim() || ringAccent;
        // Арчі оновив сторінку (нова теза/рівні/настрій) → кільце спалахує
        const contentKey = publicContentKey();
        if (contentKey && contentKey !== _lastThought) {
            if (_lastThought !== null) {
                _lastImpulseMs = now;
                if (_readyForWake) ringWake += 1;
            }
            _lastThought = contentKey;
        }
        // derivePresenceMode приймає (state, directives); знімок несе ті самі поля
        // у presence + focus → адаптуємо без домен-перерахунку (X28).
        const stateArg = pres as unknown as AgentState | null;
        const dirArg = pres
            ? ({
                  kill_switch_active: pres.kill_switch_active,
                  active_scenario: pres.has_active_scenario ? (snap?.focus ?? {}) : undefined,
              } as unknown as Directives)
            : null;
        presenceMode = derivePresenceMode(stateArg, dirArg, _lastImpulseMs, now);
    }

    $effect(() => {
        void snapshot;
        presenceTick();
    });

    // ── idle-dim: сцена/кільце тьмяніє коли довго без уваги (90с) ──
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    function bumpIdle(): void {
        if (idle) idle = false;
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
            idle = true;
        }, 90_000);
    }

    // ── мобільний viewport height → --app-vh ──
    let viewportRaf: number | null = null;
    function applyViewportHeight(): void {
        const vv = window.visualViewport;
        const vh = vv?.height ?? window.innerHeight;
        document.documentElement.style.setProperty("--app-vh", `${vh}px`);
        document.body.classList.toggle(
            "is-mobile",
            window.matchMedia("(max-width: 768px)").matches,
        );
    }
    function scheduleViewport(): void {
        if (viewportRaf !== null) return;
        viewportRaf = requestAnimationFrame(() => {
            viewportRaf = null;
            applyViewportHeight();
        });
    }

    onMount(() => {
        startSnapshotPolling(); // публічний санітизований знімок (token-free)
        const tick = setInterval(presenceTick, 1000);
        const graceId = setTimeout(() => {
            _readyForWake = true;
        }, 1500);

        applyViewportHeight();
        const vv = window.visualViewport;
        vv?.addEventListener("resize", scheduleViewport);
        window.addEventListener("resize", scheduleViewport);
        window.addEventListener("orientationchange", scheduleViewport);

        const idleEvents = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart"];
        for (const e of idleEvents)
            window.addEventListener(e, bumpIdle, { passive: true });
        bumpIdle();

        return () => {
            stopSnapshotPolling();
            clearInterval(tick);
            clearTimeout(graceId);
            vv?.removeEventListener("resize", scheduleViewport);
            window.removeEventListener("resize", scheduleViewport);
            window.removeEventListener("orientationchange", scheduleViewport);
            for (const e of idleEvents) window.removeEventListener(e, bumpIdle);
            if (idleTimer) clearTimeout(idleTimer);
            if (viewportRaf !== null) cancelAnimationFrame(viewportRaf);
        };
    });
</script>

<div class="stage" class:idle>
    <PresenceLayer
        mode={presenceMode}
        accent={ringAccent}
        focused={false}
        wakeNonce={ringWake}
        {idle}
        onArchiClick={() => {}}
    />
    <div class="home-layer">
        <Home mode={presenceMode} />
    </div>
</div>

<style>
    .stage {
        position: relative;
        height: var(--app-vh, 100vh);
        overflow: hidden;
    }
    /* ── ДІМ-шар: кільце по центру дає PresenceLayer (фіксований), тут слова Арчі ── */
    .home-layer {
        position: fixed;
        inset: 0;
        z-index: 2; /* над кільцем (z:1) */
        display: flex;
        flex-direction: column;
        pointer-events: none; /* слова не інтерактивні; кліки йдуть до кільця */
    }
</style>
