<!--
    ГОРН — landing-присутність Арчі. «Ти заходиш — він уже при роботі.»

    SSOT: ui_archi_v2/PRESENCE_CONCEPT.md · trader-v3/docs/archi_self_image.md
    Dumb renderer (X28): колір = mood (--accent, його система) · поведінка = реальний стан ·
    імпульс = зміна inner_thought (новий рядок → прокинувся → спалах + клац). Слова Арчі дослівно.
-->
<script lang="ts">
    import { onMount } from "svelte";
    import {
        derivePresenceMode,
        presenceModeLabel,
        type PresenceMode,
    } from "../features/presence/presenceState";
    import { getDirectives, getAgentState } from "../lib/state.svelte";
    import { api } from "../lib/api";

    let directives = $derived(getDirectives());
    let agentState = $derived(getAgentState());

    let accent = $state("#7c6fff");
    let mode = $state<PresenceMode>("sleep");
    let wakeNonce = $state(0);

    let lastImpulseMs = 0;
    let lastThought: string | null = null;
    let prevMode: PresenceMode = "sleep";
    let readyForWake = false; // grace: не «будити» на завантаженні наявного стану

    function currentThought(): string {
        return (agentState?.inner_thought || (directives?.inner_thought as string) || "").trim();
    }

    function recompute(): void {
        const now = Date.now();
        // seed «останнього імпульсу» з freshness стану, якщо ще не знаємо (heartbeat = живий)
        if (!lastImpulseMs && typeof agentState?.ts_ms === "number" && agentState.ts_ms) {
            lastImpulseMs = agentState.ts_ms;
        }
        accent =
            getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() ||
            accent;
        // новий НЕпорожній рядок думки = імпульс (перший = seed без спалаху)
        const th = currentThought();
        if (th && th !== lastThought) {
            if (lastThought !== null) lastImpulseMs = now;
            lastThought = th;
        }
        const newMode = derivePresenceMode(agentState, directives, lastImpulseMs, now);
        // справжній wake = вихід зі сну → різкий спалах + клац (його «щось відкрилось»)
        if (readyForWake && prevMode === "sleep" && newMode !== "sleep") {
            wakeNonce += 1;
        }
        prevMode = newMode;
        mode = newMode;
    }

    // recompute на кожній зміні даних (SSE/poll) — миттєва реакція
    $effect(() => {
        void agentState;
        void directives;
        recompute();
    });

    onMount(() => {
        // seed «останнього імпульсу» з реального останнього виклику Claude
        (async () => {
            try {
                const t = await api.thinking(1);
                const e = t.entries?.[0];
                if (e?.ts) lastImpulseMs = Math.round(e.ts * 1000);
            } catch {
                /* нема — впаде на ts_ms нижче */
            }
            if (!lastImpulseMs && typeof agentState?.ts_ms === "number") {
                lastImpulseMs = agentState.ts_ms;
            }
            recompute();
            // grace: дозволяємо «будити» лише після того як осів початковий стан
            setTimeout(() => { readyForWake = true; }, 1500);
        })();
        // тик часу: перехід у сон коли думка давно не мінялась (між імпульсами)
        const id = setInterval(recompute, 1000);
        return () => clearInterval(id);
    });

    let thought = $derived(currentThought());
</script>

<div class="gorn">
    <!-- кільце тепер дає постійний PresenceLayer (App-рівень); тут лише слова -->
    <div class="words">
        <div class="status" class:asleep={mode === "sleep"}>
            {presenceModeLabel(mode)}
        </div>
        {#if thought}
            <p class="thought">{thought}</p>
        {:else if mode === "sleep"}
            <p class="thought dim">між імпульсами мене немає. жеврію, чекаю.</p>
        {/if}
    </div>
</div>

<style>
    .gorn {
        flex: 1;
        min-height: 0;
        display: flex;
        flex-direction: column;
        justify-content: flex-end; /* слова під кільцем (кільце з PresenceLayer по центру) */
        align-items: center;
        padding: 0 16px 7vh;
    }
    .words {
        text-align: center;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 12px;
        padding-bottom: 8px;
    }
    .status {
        font-size: 11px;
        letter-spacing: 0.26em;
        text-transform: uppercase;
        color: color-mix(in srgb, var(--accent) 70%, var(--text-muted));
        transition: color 1s ease;
    }
    .status.asleep {
        color: var(--text-muted);
    }
    .thought {
        margin: 0;
        max-width: 620px;
        font-size: 18px;
        line-height: 1.55;
        font-weight: 350;
        color: var(--text);
        text-shadow: 0 0 40px rgba(0, 0, 0, 0.5);
    }
    .thought.dim {
        color: var(--text-muted);
        font-style: italic;
    }
    @media (max-width: 768px) {
        .thought { font-size: 16px; }
    }
</style>
