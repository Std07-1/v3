<!--
  src/layout/SnapshotPreview.svelte — ADR-0109 rev (рішення власника 10.10): передогляд знімка на телефоні.
  Видно, що саме піде, перш ніж поділитися: «Широкий» (віртуальна рамка, за замовчуванням) або «Як на екрані».
  «Поділитися» — свіжий дотик, тож системне меню не відмовляє через затримку збирання картинки (iOS).
  Мова оформлення — InfoModal (backdrop, картка --elev, перемикач як вкладки). Esc і тап по тлу закривають.
-->
<script lang="ts">
    interface Props {
        open: boolean;
        imageUrl: string | null;
        wide: boolean;
        busy: boolean;
        status: string | null;
        onToggleWide: (wide: boolean) => void;
        onShare: () => void;
        onSave: () => void;
        onClose: () => void;
    }
    const { open, imageUrl, wide, busy, status, onToggleWide, onShare, onSave, onClose }: Props = $props();

    function handleKeydown(e: KeyboardEvent) {
        if (open && e.key === "Escape") {
            e.preventDefault();
            onClose();
        }
    }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="modal-backdrop" onclick={onClose}>
        <div
            class="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="snapshot-preview-title"
            tabindex="-1"
            onclick={(e) => e.stopPropagation()}
        >
            <div class="modal-header">
                <span id="snapshot-preview-title" class="title">Знімок графіка</span>
                <button class="close-btn" type="button" aria-label="Закрити" onclick={onClose}>×</button>
            </div>

            <div class="tabs" role="tablist" aria-label="Розмір знімка">
                <button
                    class="tab"
                    class:active={wide}
                    role="tab"
                    aria-selected={wide}
                    disabled={busy}
                    onclick={() => !wide && onToggleWide(true)}>Широкий</button
                >
                <button
                    class="tab"
                    class:active={!wide}
                    role="tab"
                    aria-selected={!wide}
                    disabled={busy}
                    onclick={() => wide && onToggleWide(false)}>Як на екрані</button
                >
            </div>

            <div class="preview" aria-busy={busy}>
                {#if imageUrl}
                    <img src={imageUrl} alt="Знімок графіка" class:dim={busy} />
                {:else}
                    <span class="placeholder">Готую знімок…</span>
                {/if}
            </div>

            <div class="actions">
                <span class="status" role="status">{status ?? ""}</span>
                <button class="btn" type="button" disabled={busy || !imageUrl} onclick={onSave}>Зберегти</button>
                <button class="btn primary" type="button" disabled={busy || !imageUrl} onclick={onShare}
                    >Поділитися</button
                >
            </div>
        </div>
    </div>
{/if}

<style>
    .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
        backdrop-filter: blur(4px);
        z-index: 200;
        display: flex;
        align-items: center;
        justify-content: center;
        animation: fade-in 180ms ease;
    }
    @keyframes fade-in {
        from {
            opacity: 0;
        }
        to {
            opacity: 1;
        }
    }
    .modal {
        width: calc(100% - 16px);
        max-width: 720px;
        max-height: calc(100% - 32px);
        display: flex;
        flex-direction: column;
        background: var(--elev);
        border: 1px solid var(--border);
        border-radius: 10px;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        overflow: hidden;
    }
    .modal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px 8px;
    }
    .title {
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        font-weight: 600;
        color: var(--text-1);
    }
    .close-btn {
        all: unset;
        cursor: pointer;
        font-size: 24px;
        line-height: 1;
        color: var(--text-3);
        padding: 4px 8px;
        margin: -4px -8px;
        border-radius: 4px;
    }
    .close-btn:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }
    .tabs {
        display: flex;
        padding: 0 16px;
        border-bottom: 1px solid var(--border);
    }
    .tab {
        all: unset;
        cursor: pointer;
        padding: 8px 12px;
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        color: var(--text-2);
        border-bottom: 2px solid transparent;
    }
    .tab.active {
        color: var(--accent);
        border-bottom-color: var(--accent);
    }
    .tab:disabled {
        cursor: default;
    }
    .tab:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: -1px;
    }
    .preview {
        flex: 1;
        min-height: 120px;
        padding: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        background: var(--bg);
    }
    .preview img {
        max-width: 100%;
        max-height: 60vh;
        object-fit: contain;
        border-radius: 4px;
        transition: opacity 0.15s ease;
    }
    .preview img.dim {
        opacity: 0.5;
    }
    .placeholder {
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        color: var(--text-3);
    }
    .actions {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 16px 14px;
        border-top: 1px solid var(--border);
    }
    .status {
        flex: 1;
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        color: var(--text-2);
    }
    .btn {
        all: unset;
        cursor: pointer;
        padding: 8px 14px;
        border-radius: 6px;
        font-family: var(--font-sans);
        font-size: var(--t3-size);
        color: var(--text-1);
        border: 1px solid var(--border);
    }
    .btn.primary {
        background: var(--accent);
        border-color: var(--accent);
        color: var(--bg);
        font-weight: 600;
    }
    .btn:disabled {
        opacity: 0.5;
        cursor: default;
    }
    .btn:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 1px;
    }
</style>
