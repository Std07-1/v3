<!-- src/layout/DrawingStyleFlyout.svelte
     ADR-0080 (surface-2): ліва панель налаштувань інструмента малювання.
     Тригер: right-click на іконці в DrawingToolbar → frosted-glass flyout збоку.
     House-токени ADR-0066 (theme-aware dark/black/light), мова стилю = ADR-0078
     DrawingContextMenu (той самий frosted язик — консистентність поверхонь).

     Заголовок = СМИСЛ поточного кольору (нейтраль/акцент/бик/…), тонований у той
     колір, + жива лінія-зразок праворуч (правдива до фігури). Палітра = 6 барів-
     ліній (не кружки — правдиво до об'єкта), кожен = семантична роль (SSOT
     colorRoles.ts). Товщина/стиль — наступні кроки (порядок owner-а).

     P-A: оболонка + тригер. onPickColor оновлює дефолт інструмента (localStorage);
     застосування до полотна (canvas resolveColor) + live-до-вибраного = наступний
     slice. Dismiss: click/touch поза / Escape (переюз dismissOnOutside). -->
<script lang="ts">
  import type { DrawingType } from "../types";
  import {
    DRAWING_COLOR_ROLES,
    roleSpec,
    type DrawingColorRole,
  } from "../chart/drawings/colorRoles";
  import { dismissOnOutside } from "../lib/actions/dismissOnOutside";

  interface Props {
    /** null → закрито. tool = який інструмент; anchor = екранна позиція іконки
     *  (права грань + верх), від якої flyout відкривається праворуч. */
    request: { tool: DrawingType; anchorX: number; anchorY: number } | null;
    /** Поточна роль-колір дефолту цього інструмента (для зразка + active-бару). */
    colorRole: DrawingColorRole;
    onPickColor: (role: DrawingColorRole) => void;
    onClose: () => void;
  }
  let { request, colorRole, onPickColor, onClose }: Props = $props();

  // Смисл поточного кольору (заголовок) + його токен для тонування.
  let current = $derived(roleSpec(colorRole) ?? DRAWING_COLOR_ROLES[0]);
  let sampleColor = $derived(`var(${current.cssVar}, ${current.fallback})`);

  // Позиція: праворуч від іконки, clamp у viewport (щоб не вилазив за край).
  const FLYOUT_W = 188;
  const FLYOUT_H = 82;
  const GAP = 8;
  let left = $derived(
    request
      ? Math.min(request.anchorX + GAP, window.innerWidth - FLYOUT_W - GAP)
      : 0,
  );
  let top = $derived(
    request ? Math.max(GAP, Math.min(request.anchorY, window.innerHeight - FLYOUT_H - GAP)) : 0,
  );
</script>

{#if request}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="flyout"
    style:left="{left}px"
    style:top="{top}px"
    onclick={(e) => e.stopPropagation()}
    use:dismissOnOutside={{ enabled: true, onDismiss: onClose }}
  >
    <!-- Заголовок = смисл кольору (тонований) + жива лінія-зразок праворуч. -->
    <div class="head">
      <span class="meaning" style:color={sampleColor}>{current.label}</span>
      <svg class="sample" viewBox="0 0 48 12" aria-hidden="true">
        <line
          x1="2"
          y1="6"
          x2="46"
          y2="6"
          stroke={sampleColor}
          stroke-width="1.4"
          stroke-linecap="round"
          opacity="0.9"
        />
      </svg>
    </div>

    <div class="sep"></div>

    <!-- Палітра ролей — лінії-бари (правдиво до об'єкта, не кружки). -->
    <div class="roles">
      {#each DRAWING_COLOR_ROLES as r (r.role)}
        <button
          class="role"
          class:active={r.role === colorRole}
          style:--rc="var({r.cssVar}, {r.fallback})"
          title={r.label}
          aria-label={r.label}
          aria-pressed={r.role === colorRole}
          onclick={() => onPickColor(r.role)}
        >
          <span class="bar"></span>
        </button>
      {/each}
    </div>
  </div>
{/if}

<style>
  /* Frosted-glass — house-токени ADR-0066. Легка, напівпрозора присутність:
     скло тримається на blur, не на щільному фоні; тінь тонка. Панель не має
     «важити» над чартом — вона тимчасова, тиха. */
  .flyout {
    position: fixed;
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 7px 9px 8px;
    width: 188px;
    background: color-mix(in srgb, var(--card, #1c2128) 46%, transparent);
    -webkit-backdrop-filter: blur(22px) saturate(1.4);
    backdrop-filter: blur(22px) saturate(1.4);
    border: 1px solid color-mix(in srgb, var(--text-1, #e6edf3) 8%, transparent);
    border-radius: 11px;
    box-shadow:
      0 6px 18px -10px rgba(0, 0, 0, 0.45),
      0 1px 2px rgba(0, 0, 0, 0.2);
    font-family: var(--font-sans, "Inter", sans-serif);
    user-select: none;
    transform-origin: top left;
    animation: flyout-in 130ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes flyout-in {
    from {
      opacity: 0;
      transform: scale(0.97) translateX(-3px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 1px 3px 2px;
  }
  .meaning {
    font-size: 11.5px;
    font-weight: 450;
    letter-spacing: 0.3px;
    opacity: 0.88;
    /* колір задається inline (тон ролі) */
  }
  .sample {
    width: 44px;
    height: 10px;
    flex-shrink: 0;
  }

  .sep {
    height: 1px;
    margin: 1px 2px;
    background: color-mix(in srgb, var(--text-1, #fff) 6%, transparent);
  }

  .roles {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    padding: 2px 1px 0;
  }

  /* Кожна роль = коротка тонка лінія-бар свого кольору (правдиво до фігури). */
  .role {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 22px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    cursor: pointer;
    transition:
      background 0.12s ease,
      border-color 0.12s ease;
  }
  .role .bar {
    width: 18px;
    height: 2px;
    border-radius: 1.5px;
    background: var(--rc);
    opacity: 0.82;
    transition:
      height 0.1s ease,
      opacity 0.12s ease;
  }
  .role:hover {
    background: color-mix(in srgb, var(--rc) 10%, transparent);
  }
  .role:hover .bar {
    opacity: 1;
    height: 2.5px;
  }
  .role:focus-visible {
    outline: none;
    border-color: color-mix(in srgb, var(--rc) 45%, transparent);
  }
  /* active = поточний дефолт: тонка рамка кольором ролі, бар повної яскравості. */
  .role.active {
    background: color-mix(in srgb, var(--rc) 9%, transparent);
    border-color: color-mix(in srgb, var(--rc) 42%, transparent);
  }
  .role.active .bar {
    opacity: 1;
    height: 2.5px;
  }
</style>
