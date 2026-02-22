<!-- src/layout/SymbolTfPicker.svelte -->
<!-- P2: SSOT — symbols/tfs приходять із сервера через props. Без хардкоду. -->
<script lang="ts">
    const {
        symbols,
        tfs,
        onSwitch,
    }: {
        symbols: string[];
        tfs: string[];
        onSwitch: (symbol: string, tf: string) => void;
    } = $props();

    let selectedSymbol = $state("");
    let selectedTf = $state("");

    // Ініціалізувати/синхронізувати вибір при зміні серверного allowlist
    $effect(() => {
        if (symbols.length > 0 && !symbols.includes(selectedSymbol)) {
            selectedSymbol = symbols[0];
        }
    });

    $effect(() => {
        if (tfs.length > 0 && !tfs.includes(selectedTf)) {
            // Default = M5 (match ws_server default_tf_s=300); інакше перший
            selectedTf = tfs.includes("M5") ? "M5" : tfs[0];
        }
    });

    function handleChange() {
        if (selectedSymbol && selectedTf) {
            onSwitch(selectedSymbol, selectedTf);
        }
    }

    const ready = $derived(symbols.length > 0 && tfs.length > 0);
</script>

<div class="picker">
    <select
        bind:value={selectedSymbol}
        onchange={handleChange}
        class="picker-select"
        disabled={!ready}
    >
        {#if !ready}
            <option value="">…</option>
        {/if}
        {#each symbols as sym}
            <option value={sym}>{sym}</option>
        {/each}
    </select>

    <select
        bind:value={selectedTf}
        onchange={handleChange}
        class="picker-select"
        disabled={!ready}
    >
        {#if !ready}
            <option value="">…</option>
        {/if}
        {#each tfs as tf}
            <option value={tf}>{tf}</option>
        {/each}
    </select>
</div>

<style>
    .picker {
        display: flex;
        gap: 8px;
        align-items: center;
    }

    .picker-select {
        background: #2a2e39;
        color: #d1d4dc;
        border: 1px solid #363a45;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 13px;
        cursor: pointer;
        outline: none;
    }

    .picker-select:hover {
        border-color: #4a90d9;
    }

    .picker-select:focus {
        border-color: #4a90d9;
        box-shadow: 0 0 0 1px #4a90d950;
    }
</style>
