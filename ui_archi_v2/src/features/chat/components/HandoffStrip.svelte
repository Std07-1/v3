<!--
    HandoffStrip — dumb банер передачі контексту з іншої view (Feed/Thinking/Mind/…).

    Props:
      - handoff: ChatHandoff (не-null — parent не рендерить якщо відсутнє)
      - draftActive: boolean   — підсвітити кнопку "Відновити чернетку" якщо вона вже у input
      - sourceLabel: string    — локалізована назва джерела ("Feed context", …)
      - formattedTs?: string   — форматований HH:MM або порожній рядок
      - onrestore?: () => void — клік "Відновити чернетку"
      - ondismiss?: () => void — клік "Сховати"
-->
<script lang="ts">
    import type { ChatHandoff } from "../../../lib/types";

    let {
        handoff,
        draftActive = false,
        sourceLabel,
        formattedTs = "",
        onrestore = (): void => {},
        ondismiss = (): void => {},
    } = $props<{
        handoff: ChatHandoff;
        draftActive?: boolean;
        sourceLabel: string;
        formattedTs?: string;
        onrestore?: () => void;
        ondismiss?: () => void;
    }>();
</script>

<div class="handoff-strip">
    <div class="handoff-meta">
        <span class="handoff-kicker">{sourceLabel}</span>
        {#if formattedTs}
            <span class="handoff-ts">{formattedTs}</span>
        {/if}
        {#if handoff.symbol}
            <span class="handoff-symbol">{handoff.symbol}</span>
        {/if}
    </div>

    <div class="handoff-main">
        <span class="handoff-icon">{handoff.icon}</span>
        <div class="handoff-copy">
            <div class="handoff-title-row">
                <div class="handoff-title">{handoff.title}</div>
                {#if draftActive}
                    <span class="handoff-badge">у чернетці</span>
                {/if}
            </div>
            <div class="handoff-summary">{handoff.summary}</div>
        </div>
    </div>

    <div class="handoff-actions">
        <button
            class="handoff-btn"
            class:active={draftActive}
            onclick={onrestore}
        >
            Відновити чернетку
        </button>
        <button class="handoff-btn subtle" onclick={ondismiss}>Сховати</button>
    </div>
</div>

<style>
    .handoff-strip {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 10px 16px;
        background: color-mix(in srgb, var(--surface) 94%, var(--bg));
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
    }
    .handoff-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        font-size: 10px;
        color: var(--text-muted);
    }
    .handoff-kicker {
        color: var(--accent);
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    .handoff-symbol {
        font-weight: 700;
        letter-spacing: 0.04em;
        color: var(--text);
    }
    .handoff-main {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        min-width: 0;
    }
    .handoff-icon {
        font-size: 18px;
        line-height: 1;
        margin-top: 1px;
        flex-shrink: 0;
    }
    .handoff-copy {
        min-width: 0;
        flex: 1;
    }
    .handoff-title-row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        margin-bottom: 4px;
    }
    .handoff-title {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .handoff-badge {
        flex-shrink: 0;
        padding: 2px 6px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
        color: var(--accent);
        font-size: 10px;
        font-weight: 700;
    }
    .handoff-summary {
        font-size: 12px;
        color: var(--text-muted);
        line-height: 1.45;
        display: -webkit-box;
        line-clamp: 2;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .handoff-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .handoff-btn {
        padding: 6px 11px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
        background: color-mix(in srgb, var(--accent) 10%, var(--surface2));
        color: var(--text);
        cursor: pointer;
        font-size: 11px;
        font-weight: 600;
        transition:
            border-color 0.15s,
            background 0.15s,
            color 0.15s;
    }
    .handoff-btn:hover {
        border-color: color-mix(in srgb, var(--accent) 42%, transparent);
        background: color-mix(in srgb, var(--accent) 16%, var(--surface2));
    }
    .handoff-btn.active { color: var(--accent); }
    .handoff-btn.subtle {
        border-color: color-mix(in srgb, var(--border) 92%, transparent);
        background: transparent;
        color: var(--text-muted);
    }
    .handoff-btn.subtle:hover {
        color: var(--text);
        border-color: color-mix(in srgb, var(--border) 72%, transparent);
        background: var(--surface2);
    }

    @media (max-width: 768px) {
        .handoff-strip {
            padding: 10px 12px 9px;
            gap: 7px;
        }
        .handoff-title { font-size: 12px; }
        .handoff-summary { font-size: 11px; }
    }
</style>
