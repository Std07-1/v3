<!--
  src/layout/BrandWatermark.svelte — ADR-0068 Slice 3 / Part A

  ════════════════════════════════════════════════════════════════════════════
  🔒 LOCKED POSITION — DO NOT MOVE WITHOUT OWNER APPROVAL 🔒
  ════════════════════════════════════════════════════════════════════════════
  Slot: BOTTOM-LEFT, above LWC time axis (where TradingView attribution badge
  used to live before we replaced it з нашим брендом).

  Canonical values (verified 2026-05-09 with owner, не міняти):
    Desktop: position: fixed; bottom: 36px; left: 12px;
    Mobile (<640px): bottom: 30px; left: 6px;
    z-index: 36 (above HUD 35, below dropdowns 100, below modals 200)
    opacity: 0.62 idle, 1.0 hover

  Чому саме тут:
    - Top-left = ChartHud (.hud-stack absolute top: 1px) — overlap → ні
    - Bottom-right = LWC price axis ~50-60px → ні
    - Bottom-left = вільний слот після видалення TV attribution badge → так
    - bottom: 36px (не 12px, не 22px) — щоб НЕ налазити на time axis labels
      (12:00 / 15:00 / 18:00). 22px торкався цифр, 12px перекривав їх повністю.

  Дозволені правки:
    - Theme tokens (var(--text-N), opacity)
    - InfoModal target tab
  Заборонені правки без ADR + owner sign-off:
    - position values (bottom/left/right/top)
    - перенесення в інший куток
    - зміна z-index
  ════════════════════════════════════════════════════════════════════════════

  Click opens InfoModal[About].
-->

<script lang="ts">
    import Brand from "./Brand.svelte";

    interface Props {
        onclick: (e: MouseEvent) => void;
    }

    const { onclick }: Props = $props();
</script>

<div class="brand-watermark">
    <Brand
        variant="wordmark"
        size={13}
        clickable
        title="AI · ONE v3 — about, credits, diagnostics"
        {onclick}
    />
</div>

<style>
    .brand-watermark {
        position: fixed;
        bottom: 36px;
        left: 12px;
        z-index: 36;
        pointer-events: auto;
        opacity: 0.62;
        transition: opacity 0.18s ease;
    }
    .brand-watermark:hover {
        opacity: 1;
    }
    @media (max-width: 640px) {
        .brand-watermark {
            bottom: 30px;
            left: 6px;
        }
    }
</style>
