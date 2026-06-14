<!--
    PinnedThought — inner_thought pin на верхньому shell stack-у.

    Fades після 5s без взаємодії (керується railStore.contextVisible).

    Props:
      - text: string       — directives.inner_thought (parent не рендерить якщо порожній)
      - faded?: boolean    — true → opacity 0.3 + max-height 24px
-->
<script lang="ts">
    let {
        text,
        faded = false,
    } = $props<{
        text: string;
        faded?: boolean;
    }>();
</script>

<div class="pinned-thought" class:faded>
    <span class="pt-icon">💭</span>
    <span class="pt-text">{text}</span>
</div>

<style>
    .pinned-thought {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 8px 16px;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        transition:
            opacity 0.6s ease,
            max-height 0.6s ease;
        max-height: 60px;
        overflow: hidden;
    }
    .pinned-thought.faded {
        opacity: 0.3;
        max-height: 24px;
    }
    .pt-icon {
        font-size: 14px;
        flex-shrink: 0;
        line-height: 1.5;
    }
    .pt-text {
        font-size: 12px;
        color: var(--text-muted);
        font-style: italic;
        line-height: 1.5;
        overflow: hidden;
        text-overflow: ellipsis;
        display: -webkit-box;
        line-clamp: 2;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
    }
    @media (max-width: 768px) {
        .pinned-thought {
            padding: 6px 12px;
            max-height: 34px;
        }
        .pt-text {
            font-size: 11px;
            line-clamp: 1;
            -webkit-line-clamp: 1;
        }
    }
</style>
