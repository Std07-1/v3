// src/chart/longPressLock.ts
//
// ════════════════════════════════════════════════════════════════════════════
//  🔒 LOCKED MODULE — TradingView-mobile crosshair UX 🔒
// ════════════════════════════════════════════════════════════════════════════
//  Owner-confirmed working 2026-05-11 after 3 attempts (v1 vertTouchDrag-only,
//  v2 +pressedMouseMove, v3 Approach C). DO NOT replace with applyOptions
//  toggling — LWC v5.1.0 runtime НЕ honor-ить handleScroll прапори для
//  vertical pan (емпірично перевірено).
//  2026-10-04 (ADR-0108, власник): перехрестя лишається після відпускання,
//  рух 1:1 як тачпад, вихід коротким тапом або щипком. Логіка жесту — чиста
//  машина станів crosshairPin.ts (з тестами); тут — лише DOM-оболонка.
//
//  Дозволені правки:
//    - LONG_PRESS_MS / MOVE_THRESHOLD_PX tuning (з повторним mobile testing)
//    - додаткові edge-cases (palm rejection, stylus, etc)
//    - bugfixes у coordinate conversion
//  Заборонені правки без owner approval:
//    - заміна capture-phase interception на applyOptions toggle
//    - видалення autoScale freeze (chart буде стрибати на ticks)
//    - видалення stopImmediatePropagation (LWC отримає event → pан-итиме)
//    - заміна setCrosshairPosition на subscribeCrosshairMove (буде throttled)
// ════════════════════════════════════════════════════════════════════════════
//
// ── Архітектурне рішення (Approach C, 2026-05-11) ────────────────────────
// Спроби через `chart.applyOptions({handleScroll: {...}})` НЕ зупиняли
// вертикальний pan на mobile (LWC v5.1.0). Approach C: у capture phase
// перехоплюємо touch-події ДО LWC (preventDefault + stopImmediatePropagation),
// перехрестя ставимо вручну `chart.setCrosshairPosition(price, time, series)`,
// priceScale.autoScale тимчасово вимикаємо, щоб нові ticks не зсували Y.
//
// ── UX (ADR-0108 S1, crosshairPin.ts) ───────────────────────────────────
//   1) дотик одним пальцем → таймер 300 мс; рух ≥ 8 px до нього = звичайна
//      прокрутка (події йдуть до LWC)
//   2) таймер спрацював → режим перехрестя: перехрестя під пальцем, autoScale
//      вимкнено, рух пальця блокується для LWC і веде перехрестя
//   3) відпустив палець → перехрестя ЛИШАЄТЬСЯ
//   4) новий дотик будь-де → тягнення рухає перехрестя 1:1 від точки, де воно
//      стояло (палець не закриває точку); дотики цього режиму до LWC не доходять
//   5) короткий тап (без руху, < 300 мс) або щипок → вихід: перехрестя знято,
//      autoScale повернуто, графік рухається як звичайно
//
// Desktop (mouse) — touch* events не fire-ять на mouse → no-op.
//
// API: setupLongPressCrosshairLock(container, chart, series, onPinnedChange?)
// → cleanup. Викликається з ChartPane.svelte onMount, знімається в onDestroy.

import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import {
    IDLE,
    LONG_PRESS_MS,
    pinArmFired,
    pinTouchEnd,
    pinTouchMove,
    pinTouchStart,
    type Bounds,
    type PinState,
    type PinStep,
    type Point,
} from './crosshairPin';

/**
 * Attach long-press crosshair handlers to chart container.
 *
 * Single source of truth for mobile crosshair UX. Idempotent — multiple
 * setup calls would attach duplicate listeners (caller must invoke cleanup
 * before re-init). ChartPane.svelte wires/unwires у onMount/onDestroy.
 *
 * @param container      - LWC host element (the div що містить canvas). Listeners attach до нього, не до canvas
 *                         (capture phase fires before canvas-level LWC handlers).
 * @param chart          - LWC chart API: priceScale freeze, setCrosshairPosition, clearCrosshairPosition.
 * @param series         - Candlestick series API: coordinateToPrice + series argument для setCrosshairPosition.
 * @param onPinnedChange - режим перехрестя увімкнено / вимкнено (ChartPane ховає на телефоні кнопки SMC, ADR-0108 S2).
 * @returns cleanup function що знімає всі listeners та повертає chart до default стану (autoScale:true, crosshair cleared).
 */
export function setupLongPressCrosshairLock(
    container: HTMLElement,
    chart: IChartApi,
    series: ISeriesApi<'Candlestick'>,
    onPinnedChange?: (pinned: boolean) => void,
): () => void {
    let state: PinState = IDLE;
    let armTimer: number | null = null;

    function clearArmTimer(): void {
        if (armTimer != null) {
            window.clearTimeout(armTimer);
            armTimer = null;
        }
    }

    function setAutoScale(on: boolean): void {
        try {
            chart.priceScale('right').applyOptions({ autoScale: on });
        } catch {
            /* no-op: rightPriceScale always present in our setup */
        }
    }

    function setCrosshairAt(p: Point): void {
        const time = chart.timeScale().coordinateToTime(p.x);
        const price = series.coordinateToPrice(p.y);
        if (time == null || price == null) return;
        try {
            chart.setCrosshairPosition(price as number, time as Time, series);
        } catch {
            /* setCrosshairPosition кидає, якщо series detached — ignore */
        }
    }

    /** Область графіка без шкал — межі руху перехрестя. */
    function paneBounds(): Bounds {
        try {
            return {
                width: chart.timeScale().width(),
                height: container.clientHeight - chart.timeScale().height(),
            };
        } catch {
            return { width: container.clientWidth, height: container.clientHeight };
        }
    }

    function fingerPoint(t: Touch): Point {
        const rect = container.getBoundingClientRect();
        return { x: t.clientX - rect.left, y: t.clientY - rect.top };
    }

    function apply(step: PinStep, e?: TouchEvent): void {
        state = step.state;
        const fx = step.effects;
        if (fx.disarm || fx.arm) clearArmTimer();
        if (fx.arm) {
            armTimer = window.setTimeout(() => {
                armTimer = null;
                apply(pinArmFired(state));
            }, LONG_PRESS_MS);
        }
        if (fx.enter) {
            // Freeze Y axis — autoScale recompute на нових ticks (delta_loop 2s) зсував би chart вертикально
            setAutoScale(false);
            onPinnedChange?.(true);
        }
        if (fx.crosshair) setCrosshairAt(fx.crosshair);
        if (fx.reassert) {
            // Кінець дотику довгого тапу обробляє і LWC — ставимо перехрестя ще раз після нього
            const at = fx.reassert;
            window.requestAnimationFrame(() => {
                if (state.mode === 'pinned') setCrosshairAt(at);
            });
        }
        if (fx.exit) {
            // Спершу повідомляємо про вихід: подія LWC «перехрестя нема» від clearCrosshairPosition — уже справжня
            onPinnedChange?.(false);
            setAutoScale(true);
            try {
                chart.clearCrosshairPosition();
            } catch {
                /* clearCrosshairPosition доступний у LWC v5 — fallback no-op */
            }
        }
        if (fx.block && e) {
            // Capture phase: LWC (слухає touch* на canvas) подію не отримає → не прокручує, не тапає.
            // preventDefault і на touchstart: поглинутий дотик браузер віддає сторінці з кожним рухом — інакше Chrome
            // мовчки ковтає touchmove у межах свого touch slop (~15 px), і точна наводка не доходить (стенд 04.10)
            if (e.cancelable) e.preventDefault();
            e.stopImmediatePropagation();
        }
    }

    function onTouchStart(e: TouchEvent): void {
        const t = e.touches[0];
        apply(pinTouchStart(state, e.touches.length, t ? fingerPoint(t) : { x: 0, y: 0 }, e.timeStamp), e);
    }

    function onTouchMove(e: TouchEvent): void {
        const t = e.touches[0];
        if (!t) return;
        apply(pinTouchMove(state, e.touches.length, fingerPoint(t), paneBounds()), e);
    }

    function onTouchEnd(e: TouchEvent): void {
        apply(pinTouchEnd(state, e.timeStamp), e);
    }

    function onTouchCancel(e: TouchEvent): void {
        apply(pinTouchEnd(state, e.timeStamp, true), e);
    }

    // capture:true → наш handler fire-ить ПЕРЕД LWC's listener (LWC слухає touch* на canvas, ми на container).
    // passive:false → preventDefault працює; поза режимом перехрестя ми його не викликаємо (прокрутка й зум як завжди).
    const captureOpts: AddEventListenerOptions = { capture: true, passive: false };

    container.addEventListener('touchstart', onTouchStart, captureOpts);
    container.addEventListener('touchmove', onTouchMove, captureOpts);
    container.addEventListener('touchend', onTouchEnd, captureOpts);
    container.addEventListener('touchcancel', onTouchCancel, captureOpts);

    return () => {
        clearArmTimer();
        // Якщо disposed у режимі перехрестя — повертаємо chart до default стану.
        if (state.mode === 'pinned') apply({ state: IDLE, effects: { exit: true } });
        state = IDLE;
        container.removeEventListener('touchstart', onTouchStart, captureOpts);
        container.removeEventListener('touchmove', onTouchMove, captureOpts);
        container.removeEventListener('touchend', onTouchEnd, captureOpts);
        container.removeEventListener('touchcancel', onTouchCancel, captureOpts);
    };
}
