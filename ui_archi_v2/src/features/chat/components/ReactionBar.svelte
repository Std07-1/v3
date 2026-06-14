<!--
    ReactionBar — hover-visible reactions на chat bubble (ADR-0053 S1).

    Три дії: like / pin / star. Стан у reactionsStore (localStorage). S4
    (окремий slice) додасть backend — `POST /api/archi/chat/react` → Redis
    XADD `feedback:chat` stream. Поки цього немає — реакції лише у браузері.

    Props:
      - msgId: string       — ChatMessage.id, primary key для reactionsStore
      - side: "left" | "right" — leftside=bubble author archi (default), rightside=user
-->
<script lang="ts">
    import {
        reactionsStore,
        type ReactionType,
    } from "../stores/reactionsStore.svelte";

    let {
        msgId,
        side = "left",
    } = $props<{
        msgId: string;
        side?: "left" | "right";
    }>();

    const options: Array<{ type: ReactionType; label: string; title: string }> = [
        { type: "like", label: "👍", title: "Корисно" },
        { type: "pin", label: "📌", title: "Закріпити" },
        { type: "star", label: "⭐", title: "Обране" },
    ];

    function toggle(type: ReactionType): void {
        reactionsStore.toggle(msgId, type);
    }
</script>

<div class="rb" class:right={side === "right"}>
    {#each options as opt (opt.type)}
        <button
            type="button"
            class="rb-btn"
            class:active={reactionsStore.has(msgId, opt.type)}
            onclick={() => toggle(opt.type)}
            title={opt.title}
            aria-label={opt.title}
            aria-pressed={reactionsStore.has(msgId, opt.type)}
        >{opt.label}</button>
    {/each}
</div>

<style>
    .rb {
        display: flex;
        gap: 3px;
        align-self: flex-start;
        margin: -2px 0 2px;
        opacity: 0;
        transition: opacity 0.15s;
        pointer-events: none;
    }
    .rb.right { align-self: flex-end; }

    /* Parent hover surface (.msg-block) reveals the bar */
    :global(.msg-block:hover) .rb,
    :global(.msg-block:focus-within) .rb {
        opacity: 1;
        pointer-events: auto;
    }
    /* Always visible when any reaction is set — so user can see state w/o hover. */
    .rb:has(.rb-btn.active) {
        opacity: 1;
        pointer-events: auto;
    }

    .rb-btn {
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1px 7px;
        font-size: 12px;
        line-height: 1.4;
        cursor: pointer;
        color: var(--text-muted);
        transition: background 0.12s, border-color 0.12s, transform 0.1s;
    }
    .rb-btn:hover {
        background: color-mix(in srgb, var(--accent) 12%, var(--surface2));
        border-color: var(--accent);
    }
    .rb-btn:active { transform: scale(0.92); }
    .rb-btn.active {
        background: color-mix(in srgb, var(--accent) 18%, var(--surface2));
        border-color: var(--accent);
        color: var(--text);
    }
</style>
