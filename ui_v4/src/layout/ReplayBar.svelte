<!-- src/layout/ReplayBar.svelte -->
<!-- ADR-0027: Client-side replay controls.
     Play/Pause, Speed, Scrubber, Step forward/back, Exit.
     Keyboard: Space=play/pause, ←/→=step, Shift+←/→=step×10, Esc=exit -->
<script lang="ts">
    import { replayStore, SPEED_OPTIONS } from "../stores/replayStore.svelte";

    const { onExit }: { onExit: () => void } = $props();

    // ── Scrubber dragging ──
    let scrubberRef: HTMLDivElement | undefined = $state(undefined);
    let isDragging = $state(false);

    function scrubFromEvent(e: MouseEvent | PointerEvent): void {
        if (!scrubberRef) return;
        const rect = scrubberRef.getBoundingClientRect();
        const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        const ratio = x / rect.width;
        const idx = Math.round(ratio * replayStore.totalBars);
        replayStore.seekIndex(idx);
    }

    function onScrubDown(e: PointerEvent): void {
        isDragging = true;
        scrubberRef?.setPointerCapture(e.pointerId);
        scrubFromEvent(e);
    }

    function onScrubMove(e: PointerEvent): void {
        if (!isDragging) return;
        scrubFromEvent(e);
    }

    function onScrubUp(e: PointerEvent): void {
        isDragging = false;
        scrubberRef?.releasePointerCapture(e.pointerId);
    }

    // ── Keyboard ──
    function handleKeydown(e: KeyboardEvent): void {
        // Don't capture keys when typing in inputs
        if (
            e.target instanceof HTMLInputElement ||
            e.target instanceof HTMLTextAreaElement
        )
            return;
        if (!replayStore.active) return;

        const step = e.shiftKey ? 10 : 1;

        switch (e.key) {
            case " ":
                e.preventDefault();
                replayStore.togglePlay();
                break;
            case "ArrowRight":
                e.preventDefault();
                replayStore.stepForward(step);
                break;
            case "ArrowLeft":
                e.preventDefault();
                replayStore.stepBack(step);
                break;
            case "Escape":
                e.preventDefault();
                onExit();
                break;
        }
    }

    // ── Helpers ──
    function formatBarInfo(): string {
        const idx = replayStore.cursorIndex;
        const total = replayStore.totalBars;
        if (idx === 0 || total === 0) return "—";
        const candle = replayStore.allCandles[idx - 1];
        if (!candle) return `${idx}/${total}`;
        const d = new Date(candle.t_ms);
        const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
        const dd = String(d.getUTCDate()).padStart(2, "0");
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mi = String(d.getUTCMinutes()).padStart(2, "0");
        return `${mm}-${dd} ${hh}:${mi}  (${idx}/${total})`;
    }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="replay-bar">
    <!-- Step back -->
    <button
        class="rb-btn"
        onclick={() => replayStore.stepBack(1)}
        title="Step back (←)">◀◀</button
    >

    <!-- Play / Pause -->
    <button
        class="rb-btn rb-play"
        onclick={() => replayStore.togglePlay()}
        title={replayStore.playing ? "Pause (Space)" : "Play (Space)"}
        >{replayStore.playing ? "⏸" : "▶"}</button
    >

    <!-- Step forward -->
    <button
        class="rb-btn"
        onclick={() => replayStore.stepForward(1)}
        title="Step forward (→)">▶▶</button
    >

    <!-- Scrubber -->
    <div
        class="rb-scrubber"
        bind:this={scrubberRef}
        onpointerdown={onScrubDown}
        onpointermove={onScrubMove}
        onpointerup={onScrubUp}
        role="slider"
        aria-valuemin={0}
        aria-valuemax={replayStore.totalBars}
        aria-valuenow={replayStore.cursorIndex}
        tabindex={0}
    >
        <div class="rb-scrubber-track">
            <div
                class="rb-scrubber-fill"
                style:width={`${replayStore.progress * 100}%`}
            ></div>
            <div
                class="rb-scrubber-thumb"
                style:left={`${replayStore.progress * 100}%`}
            ></div>
        </div>
    </div>

    <!-- Bar info -->
    <span class="rb-info">{formatBarInfo()}</span>

    <!-- Speed -->
    <button
        class="rb-btn rb-speed"
        onclick={() => replayStore.nextSpeed()}
        title="Speed (click to cycle)">{replayStore.speed}×</button
    >

    <!-- Exit -->
    <button class="rb-btn rb-exit" onclick={onExit} title="Exit replay (Esc)"
        >✕</button
    >
</div>

<style>
    .replay-bar {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 12px;
        background: rgba(19, 23, 34, 0.92);
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        backdrop-filter: blur(12px);
        height: 36px;
        user-select: none;
        flex-shrink: 0;
    }

    .rb-btn {
        all: unset;
        cursor: pointer;
        font-size: 12px;
        padding: 3px 8px;
        border-radius: 4px;
        color: #8b8f9a;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.06);
        transition: all 0.12s ease;
        white-space: nowrap;
        line-height: 1;
    }
    .rb-btn:hover {
        color: #d1d4dc;
        background: rgba(255, 255, 255, 0.08);
        border-color: rgba(255, 255, 255, 0.12);
    }

    .rb-play {
        font-size: 14px;
        padding: 3px 10px;
        color: #26a69a;
    }
    .rb-play:hover {
        color: #2bbd8e;
        background: rgba(38, 166, 154, 0.1);
        border-color: rgba(38, 166, 154, 0.25);
    }

    .rb-speed {
        font-family: "Roboto Mono", monospace, sans-serif;
        font-size: 11px;
        min-width: 36px;
        text-align: center;
        color: #f0b90b;
    }
    .rb-speed:hover {
        background: rgba(240, 185, 11, 0.1);
        border-color: rgba(240, 185, 11, 0.25);
    }

    .rb-exit {
        color: #ef5350;
        font-size: 13px;
        font-weight: bold;
        padding: 3px 6px;
    }
    .rb-exit:hover {
        background: rgba(239, 83, 80, 0.12);
        border-color: rgba(239, 83, 80, 0.3);
    }

    .rb-info {
        font-size: 10px;
        color: #5d6068;
        font-family: "Roboto Mono", monospace, sans-serif;
        white-space: nowrap;
        min-width: 130px;
        text-align: center;
    }

    /* Scrubber / progress bar */
    .rb-scrubber {
        flex: 1;
        min-width: 80px;
        height: 24px;
        display: flex;
        align-items: center;
        cursor: pointer;
        touch-action: none; /* prevent browser scroll on pointer drag */
    }

    .rb-scrubber-track {
        position: relative;
        width: 100%;
        height: 4px;
        background: rgba(255, 255, 255, 0.06);
        border-radius: 2px;
        overflow: visible;
    }

    .rb-scrubber-fill {
        position: absolute;
        top: 0;
        left: 0;
        height: 100%;
        background: rgba(38, 166, 154, 0.5);
        border-radius: 2px;
        transition: width 0.05s linear;
    }

    .rb-scrubber-thumb {
        position: absolute;
        top: 50%;
        width: 10px;
        height: 10px;
        background: #26a69a;
        border-radius: 50%;
        transform: translate(-50%, -50%);
        box-shadow: 0 0 4px rgba(38, 166, 154, 0.4);
        transition: left 0.05s linear;
    }

    .rb-scrubber:hover .rb-scrubber-track {
        height: 6px;
    }
    .rb-scrubber:hover .rb-scrubber-thumb {
        width: 12px;
        height: 12px;
    }
</style>
