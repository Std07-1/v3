<!--
    ModeHearth — dumb presence layer (Dual Mode hearth).

    Показує режим Арчі: tone (home/work/bridge/quiet/degraded), title, detail,
    reason, meta pills, action buttons, optional warning.

    Props:
      - hearth: ModeHearth  — обчислений у Chat.svelte через buildModeHearth()
      - onaction?: (prompt: string) => void  — клік по action button → primeDraft
-->
<script lang="ts">
    import type { ModeHearth as Hearth } from "../lib/hearthHelpers";

    let {
        hearth,
        onaction = (_prompt: string): void => {},
    } = $props<{
        hearth: Hearth;
        onaction?: (prompt: string) => void;
    }>();
</script>

<div class="mode-hearth" data-tone={hearth.tone}>
    <div class="mode-hearth-head">
        <div>
            <div class="mode-hearth-kicker">Dual Mode</div>
            <div class="mode-hearth-title">{hearth.title}</div>
        </div>

        <div class="mode-hearth-badges">
            <span class="mode-hearth-badge emphasis">{hearth.modeLabel}</span>
            {#if hearth.modelLabel}
                <span class="mode-hearth-badge">{hearth.modelLabel}</span>
            {/if}
        </div>
    </div>

    <div class="mode-hearth-copy">{hearth.detail}</div>
    <div class="mode-hearth-reason">{hearth.reason}</div>

    {#if hearth.meta.length > 0}
        <div class="mode-hearth-meta">
            {#each hearth.meta as item}
                <span class="mode-hearth-pill">{item}</span>
            {/each}
        </div>
    {/if}

    {#if hearth.actions.length > 0}
        <div class="mode-hearth-actions">
            {#each hearth.actions as action}
                <button
                    class="mode-hearth-btn"
                    class:secondary={action.subtle}
                    onclick={() => onaction(action.prompt)}
                >
                    {action.label}
                </button>
            {/each}
        </div>
    {/if}

    {#if hearth.warning}
        <div class="mode-hearth-warning">{hearth.warning}</div>
    {/if}
</div>

<style>
    .mode-hearth {
        --hearth-accent: rgba(246, 168, 74, 0.28);
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 14px 16px 16px;
        border-bottom: 1px solid var(--border);
        background: radial-gradient(
                circle at top left,
                rgba(246, 168, 74, 0.16),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(92, 205, 180, 0.1),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
        flex-shrink: 0;
    }
    .mode-hearth[data-tone="work"] {
        --hearth-accent: rgba(120, 164, 255, 0.3);
        background: radial-gradient(
                circle at top left,
                rgba(120, 164, 255, 0.16),
                transparent 36%
            ),
            radial-gradient(
                circle at right center,
                rgba(92, 205, 180, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="bridge"] {
        --hearth-accent: rgba(150, 132, 255, 0.28);
        background: radial-gradient(
                circle at top left,
                rgba(150, 132, 255, 0.16),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(246, 168, 74, 0.1),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="quiet"] {
        --hearth-accent: rgba(92, 205, 180, 0.22);
        background: radial-gradient(
                circle at top left,
                rgba(92, 205, 180, 0.12),
                transparent 32%
            ),
            radial-gradient(
                circle at right center,
                rgba(120, 164, 255, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth[data-tone="degraded"] {
        --hearth-accent: rgba(239, 95, 71, 0.3);
        background: radial-gradient(
                circle at top left,
                rgba(239, 95, 71, 0.14),
                transparent 34%
            ),
            radial-gradient(
                circle at right center,
                rgba(246, 168, 74, 0.08),
                transparent 38%
            ),
            color-mix(in srgb, var(--surface) 94%, var(--bg));
    }
    .mode-hearth-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
    }
    .mode-hearth-kicker {
        font-size: 10px;
        color: #f6a84a;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .mode-hearth-title {
        font-size: 15px;
        font-weight: 650;
        color: var(--text);
        line-height: 1.35;
    }
    .mode-hearth-badges {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-badge {
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--border) 84%, transparent);
        background: color-mix(in srgb, var(--surface2) 90%, var(--bg));
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }
    .mode-hearth-badge.emphasis {
        border-color: var(--hearth-accent);
        color: var(--text);
    }
    .mode-hearth-copy {
        font-size: 13px;
        line-height: 1.6;
        color: var(--text);
        max-width: 760px;
    }
    .mode-hearth-reason {
        font-size: 12px;
        line-height: 1.55;
        color: var(--text-muted);
        max-width: 760px;
    }
    .mode-hearth-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-pill {
        padding: 5px 9px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--surface2) 90%, var(--bg));
        border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
        color: var(--text-muted);
        font-size: 10px;
        font-weight: 600;
    }
    .mode-hearth-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .mode-hearth-btn {
        padding: 7px 11px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
        background: color-mix(in srgb, var(--accent) 11%, var(--surface2));
        color: var(--text);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition:
            border-color 0.15s,
            background 0.15s,
            transform 0.15s;
    }
    .mode-hearth-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 44%, transparent);
        background: color-mix(in srgb, var(--accent) 17%, var(--surface2));
        transform: translateY(-1px);
    }
    .mode-hearth-btn.secondary {
        border-color: color-mix(in srgb, var(--border) 82%, transparent);
        background: transparent;
        color: var(--text-muted);
    }
    .mode-hearth-btn.secondary:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--border) 66%, transparent);
        background: color-mix(in srgb, var(--surface2) 92%, var(--bg));
    }
    .mode-hearth-warning {
        padding: 9px 11px;
        border-radius: 12px;
        border: 1px solid rgba(246, 168, 74, 0.2);
        background: rgba(246, 168, 74, 0.08);
        color: var(--text);
        font-size: 11px;
        line-height: 1.45;
    }
    @media (max-width: 720px) {
        .mode-hearth-head { flex-direction: column; }
        .mode-hearth-badges { justify-content: flex-start; }
    }
</style>
