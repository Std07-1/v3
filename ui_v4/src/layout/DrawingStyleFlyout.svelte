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
  import {
    DRAWING_COLOR_ROLES,
    roleSpec,
    type DrawingColorRole,
  } from "../chart/drawings/colorRoles";
  import {
    DRAWING_LINE_STYLES,
    type DrawingLineStyle,
  } from "../chart/drawings/lineStyles";
  import { dismissOnOutside } from "../lib/actions/dismissOnOutside";

  interface Props {
    /** null → закрито. anchor = екранна позиція, від якої flyout відкривається
     *  праворуч (іконки в tool-режимі / курсора в object-режимі).
     *  showDelete → object-режим (right-click на фігурі): рядок «Видалити». */
    request: { anchorX: number; anchorY: number; showDelete?: boolean } | null;
    /** Поточна роль-колір: дефолт інструмента (tool) або фігури (object). */
    colorRole: DrawingColorRole;
    /** Поточна товщина лінії (px). Прев'ю chips тоновані поточним кольором. */
    lineWidth: number;
    /** Поточний стиль лінії (solid/dashed/dotted). */
    lineStyle: DrawingLineStyle;
    onPickColor: (role: DrawingColorRole) => void;
    onPickWidth: (width: number) => void;
    onPickStyle: (style: DrawingLineStyle) => void;
    /** object-режим: live-preview на фігурі. Наведення → значення; вихід з ряду
     *  → null (відкат). Undefined у tool-режимі (нема об'єкта). */
    onPreviewColor?: (role: DrawingColorRole | null) => void;
    onPreviewWidth?: (width: number | null) => void;
    onPreviewStyle?: (style: DrawingLineStyle | null) => void;
    /** object-режим: видалити фігуру (undoable). Undefined у tool-режимі. */
    onDelete?: () => void;
    onClose: () => void;
  }
  let {
    request,
    colorRole,
    lineWidth,
    lineStyle,
    onPickColor,
    onPickWidth,
    onPickStyle,
    onPreviewColor,
    onPreviewWidth,
    onPreviewStyle,
    onDelete,
    onClose,
  }: Props = $props();

  // SVG dash-візерунок для прев'ю chips (dotted → round-cap крапки: нульовий
  // штрих = круг діаметром stroke-width, як canvas dashPattern).
  function svgDash(style: DrawingLineStyle): string {
    return style === "dashed" ? "7 4" : style === "dotted" ? "0.01 4" : "none";
  }

  // Смисл поточного кольору (заголовок) + його токен для тонування.
  let current = $derived(roleSpec(colorRole) ?? DRAWING_COLOR_ROLES[0]);
  let sampleColor = $derived(`var(${current.cssVar}, ${current.fallback})`);

  // Товщини лінії (px) — прев'ю у поточному кольорі (правдиве комбо).
  const WIDTHS = [1, 2, 3, 4];

  // Позиція: праворуч від іконки, clamp у viewport (щоб не вилазив за край).
  const FLYOUT_W = 188;
  const GAP = 8;
  let flyoutH = $derived(request?.showDelete ? 190 : 154);
  let left = $derived(
    request
      ? Math.min(request.anchorX + GAP, window.innerWidth - FLYOUT_W - GAP)
      : 0,
  );
  let top = $derived(
    request ? Math.max(GAP, Math.min(request.anchorY, window.innerHeight - flyoutH - GAP)) : 0,
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

    <!-- Палітра ролей — лінії-бари (правдиво до об'єкта, не кружки).
         object-режим: наведення → live-preview на фігурі, вихід з палітри → відкат. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="roles" onmouseleave={() => onPreviewColor?.(null)}>
      {#each DRAWING_COLOR_ROLES as r (r.role)}
        <button
          class="role"
          class:active={r.role === colorRole}
          style:--rc="var({r.cssVar}, {r.fallback})"
          title={r.label}
          aria-label={r.label}
          aria-pressed={r.role === colorRole}
          onmouseenter={() => onPreviewColor?.(r.role)}
          onclick={() => onPickColor(r.role)}
        >
          <span class="bar"></span>
        </button>
      {/each}
    </div>

    <div class="sep"></div>

    <!-- Товщина — chips з прев'ю лінії у ПОТОЧНОМУ кольорі (правдиве комбо).
         object-режим: наведення → live-preview на фігурі, вихід → відкат. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="widths" onmouseleave={() => onPreviewWidth?.(null)}>
      {#each WIDTHS as w (w)}
        <button
          class="width"
          class:active={w === lineWidth}
          title={`${w}px`}
          aria-label={`Товщина ${w}px`}
          aria-pressed={w === lineWidth}
          onmouseenter={() => onPreviewWidth?.(w)}
          onclick={() => onPickWidth(w)}
        >
          <svg viewBox="0 0 40 14" aria-hidden="true">
            <line
              x1="4"
              y1="7"
              x2="36"
              y2="7"
              stroke={sampleColor}
              stroke-width={w}
              stroke-linecap="round"
              opacity="0.9"
            />
          </svg>
        </button>
      {/each}
    </div>

    <div class="sep"></div>

    <!-- Стиль лінії — chips solid/dashed/dotted (прев'ю в поточному кольорі).
         object-режим: наведення → live-preview на фігурі, вихід → відкат. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="styles" onmouseleave={() => onPreviewStyle?.(null)}>
      {#each DRAWING_LINE_STYLES as s (s.style)}
        <button
          class="lstyle"
          class:active={s.style === lineStyle}
          title={s.label}
          aria-label={s.label}
          aria-pressed={s.style === lineStyle}
          onmouseenter={() => onPreviewStyle?.(s.style)}
          onclick={() => onPickStyle(s.style)}
        >
          <svg viewBox="0 0 44 12" aria-hidden="true">
            <line
              x1="3"
              y1="6"
              x2="41"
              y2="6"
              stroke={sampleColor}
              stroke-width="2"
              stroke-linecap={s.style === "dotted" ? "round" : "butt"}
              stroke-dasharray={svgDash(s.style)}
              opacity="0.9"
            />
          </svg>
        </button>
      {/each}
    </div>

    {#if request.showDelete && onDelete}
      <div class="sep"></div>
      <button class="delete" onclick={() => onDelete?.()}>
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M3 6h18" />
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
        </svg>
        <span>Видалити</span>
      </button>
    {/if}
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

  /* Товщина — chips з прев'ю лінії (у поточному кольорі). */
  .widths {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    padding: 2px 1px 0;
  }
  .width {
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
  .width svg {
    width: 30px;
    height: 12px;
    display: block;
  }
  .width:hover {
    background: color-mix(in srgb, var(--text-1, #fff) 8%, transparent);
  }
  .width:focus-visible {
    outline: none;
    border-color: color-mix(in srgb, var(--text-1, #fff) 24%, transparent);
  }
  .width.active {
    background: color-mix(in srgb, var(--text-1, #fff) 7%, transparent);
    border-color: color-mix(in srgb, var(--text-1, #fff) 20%, transparent);
  }

  /* Стиль лінії — chips solid/dashed/dotted (прев'ю в поточному кольорі). */
  .styles {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    padding: 2px 1px 0;
  }
  .lstyle {
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
  .lstyle svg {
    width: 34px;
    height: 10px;
    display: block;
  }
  .lstyle:hover {
    background: color-mix(in srgb, var(--text-1, #fff) 8%, transparent);
  }
  .lstyle:focus-visible {
    outline: none;
    border-color: color-mix(in srgb, var(--text-1, #fff) 24%, transparent);
  }
  .lstyle.active {
    background: color-mix(in srgb, var(--text-1, #fff) 7%, transparent);
    border-color: color-mix(in srgb, var(--text-1, #fff) 20%, transparent);
  }

  /* object-режим: рядок «Видалити» під палітрою (мова DrawingContextMenu). */
  .delete {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 5px;
    background: transparent;
    border: none;
    border-radius: 6px;
    color: var(--text-2, #9b9bb0);
    font-family: inherit;
    font-size: 11.5px;
    font-weight: 450;
    letter-spacing: 0.2px;
    text-align: left;
    cursor: pointer;
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }
  .delete svg {
    opacity: 0.75;
    transition: opacity 0.12s ease;
  }
  .delete:hover {
    background: color-mix(in srgb, var(--bear, #ed4554) 13%, transparent);
    color: var(--bear, #ed4554);
  }
  .delete:hover svg {
    opacity: 1;
  }
</style>
