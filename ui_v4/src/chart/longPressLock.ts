// src/chart/longPressLock.ts
//
// ════════════════════════════════════════════════════════════════════════════
//  🔒 LOCKED MODULE — TradingView-mobile crosshair UX 🔒
// ════════════════════════════════════════════════════════════════════════════
//  Owner-confirmed working 2026-05-11 after 3 attempts (v1 vertTouchDrag-only,
//  v2 +pressedMouseMove, v3 Approach C). DO NOT replace with applyOptions
//  toggling — LWC v5.1.0 runtime НЕ honor-ить handleScroll прапори для
//  vertical pan (емпірично перевірено).
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
// Mobile UX: long-press на канвас чарту фіксує положення графіку, дозволяючи
// перетягувати crosshair (vertLine + horzLine) пальцем без supplementary
// chart pan. Цей патерн = TradingView mobile / Binance app — eSpec для
// інструменту трейдера, де precise hover-чтение OHLCV/levels критичне.
//
// ── Архітектурне рішення (Approach C, 2026-05-11) ────────────────────────
// Попередні спроби через `chart.applyOptions({handleScroll: {vertTouchDrag:
// false, horzTouchDrag:false, pressedMouseMove:false}})` НЕ дали результату
// для вертикального pan на mobile. Емпірично (LWC v5.1.0): horizontal лок
// працював, vertical продовжував pан-ити. Гіпотеза: applyOptions runtime
// не реактивує всі handleScroll прапори у LWC gesture handlers (handleScroll
// читається тільки при init або частково).
//
// Approach C: bypass LWC options entirely. У capture phase перехоплюємо
// touchmove events ДО того як LWC їх обробить, викликаємо preventDefault +
// stopImmediatePropagation → LWC pан-handler не fire-ить. Crosshair drive-имо
// вручну через `chart.setCrosshairPosition(price, time, series)` (LWC public
// API, typings.d.ts:1733). priceScale.autoScale тимчасово вимикаємо щоб
// нові ticks не recompute-ували Y range під час lock.
//
// ── UX flow ──────────────────────────────────────────────────────────────
//   1) touchstart  → arm 300ms timer (single-finger only)
//   2) touchmove < 8px у вікні 300ms → нічого, чекаємо
//   3) timer fires (300ms hold без значного руху) → ENTER lock:
//        - autoScale: false  (Y range freeze)
//        - capture-phase block активний на touchmove
//   4) touchmove під час lock → preventDefault + stopImmediatePropagation →
//      LWC не отримує event → НЕ pан-ить. Рахуємо price/time з touch coords,
//      викликаємо setCrosshairPosition вручну → crosshair lines рухаються
//      за пальцем, чарт стоїть на місці.
//   5) touchend / touchcancel → EXIT lock:
//        - autoScale: true (resume normal Y behavior)
//        - clearCrosshairPosition (LWC показує crosshair тільки під час hover)
//        - capture-phase блокування знімається через locked=false flag
//   6) touchmove >8px ДО 300ms → НЕ enter lock (це normal swipe-pan,
//      скасовуємо arm timer; LWC обробляє event звичайним шляхом)
//
// Pinch (e.touches.length === 2+) повністю ігноруємо — це zoom gesture,
// LWC сам обробляє через handleScale.pinch.
//
// Desktop (mouse) — handler attached але touch* events не fire-ять на mouse,
// тому no-op. Mouse pan через pressedMouseMove працює як завжди.
//
// API: setupLongPressCrosshairLock(container, chart, series) → cleanup.
// Викликається з ChartPane.svelte onMount, ремувається у onDestroy.

import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';

/** Hold duration перед активацією lock. 300ms = LWC's own internal long-press
 *  threshold (LWC показує crosshair при ~300ms hold). Менше → false positive
 *  від швидких тапів. Більше → user сприймає UX як "lagging". */
const LONG_PRESS_MS = 300;

/** Touch-jitter tolerance до activate. 8px = standard mobile UI threshold
 *  (Material Design touch-slop = 8dp). Нижче → false-positives від тремтіння
 *  пальця. Вище → пропускаємо швидкі-але-короткі hold gestures. */
const MOVE_THRESHOLD_PX = 8;

/**
 * Attach long-press crosshair-lock handlers to chart container.
 *
 * Single source of truth for mobile crosshair UX. Idempotent — multiple
 * setup calls would attach duplicate listeners (caller must invoke cleanup
 * before re-init). ChartPane.svelte wires/unwires у onMount/onDestroy.
 *
 * @param container - LWC host element (the div що містить canvas).
 *                    Listeners attach до нього, не до canvas (capture phase
 *                    fires before canvas-level LWC handlers).
 * @param chart     - LWC chart API. Used for priceScale freeze +
 *                    setCrosshairPosition + clearCrosshairPosition.
 * @param series    - Candlestick series API. Used для coordinateToPrice +
 *                    як series argument для setCrosshairPosition.
 * @returns cleanup function що знімає всі listeners та повертає chart до
 *          default стану (autoScale:true, crosshair cleared).
 */
export function setupLongPressCrosshairLock(
    container: HTMLElement,
    chart: IChartApi,
    series: ISeriesApi<'Candlestick'>,
): () => void {
    let armTimer: number | null = null;
    let locked = false;
    let startX = 0;
    let startY = 0;

    function clearArmTimer(): void {
        if (armTimer != null) {
            window.clearTimeout(armTimer);
            armTimer = null;
        }
    }

    function enterLock(): void {
        if (locked) return;
        locked = true;
        // Freeze Y axis — autoScale recompute на нових ticks (delta_loop 2s)
        // інакше зсував би chart вертикально під час hold.
        try {
            chart.priceScale('right').applyOptions({ autoScale: false });
        } catch {
            /* no-op: rightPriceScale always present in our setup */
        }
    }

    function exitLock(): void {
        if (!locked) return;
        locked = false;
        try {
            chart.priceScale('right').applyOptions({ autoScale: true });
        } catch {
            /* no-op */
        }
        // Hide crosshair after release — LWC default behavior coли finger up.
        // Без цього last crosshair position лишається намальованою.
        try {
            chart.clearCrosshairPosition();
        } catch {
            /* clearCrosshairPosition доступний у LWC v5 — fallback no-op */
        }
    }

    function setCrosshairFromTouch(t: Touch): void {
        const rect = container.getBoundingClientRect();
        const x = t.clientX - rect.left;
        const y = t.clientY - rect.top;
        const time = chart.timeScale().coordinateToTime(x);
        const price = series.coordinateToPrice(y);
        if (time == null || price == null) return;
        try {
            chart.setCrosshairPosition(price as number, time as Time, series);
        } catch {
            /* setCrosshairPosition kідає якщо series detached — ignore */
        }
    }

    function onTouchStart(e: TouchEvent): void {
        // Pinch або multi-touch — let LWC handle pinch-zoom natively.
        if (e.touches.length !== 1) {
            clearArmTimer();
            // Якщо вже у lock-режимі і другий палець торкнувся — виходимо з
            // lock, щоб user міг pinch-zoom як завжди.
            if (locked) exitLock();
            return;
        }
        const t = e.touches[0];
        startX = t.clientX;
        startY = t.clientY;
        clearArmTimer();
        armTimer = window.setTimeout(() => {
            armTimer = null;
            enterLock();
            // Одразу malюємо crosshair у позиції пальця — щоб користувач
            // побачив візуальний feedback що lock активовано.
            setCrosshairFromTouch(t);
        }, LONG_PRESS_MS);
    }

    function onTouchMove(e: TouchEvent): void {
        if (e.touches.length !== 1) {
            // Multi-touch (pinch почався) — exit lock + cancel arm.
            clearArmTimer();
            if (locked) exitLock();
            return;
        }
        const t = e.touches[0];

        // Locked → BLOCK LWC pan-handler + drive crosshair manually.
        if (locked) {
            // Capture phase + preventDefault + stopImmediatePropagation:
            // LWC's touchmove listener (added у target/bubble phase) НЕ fire-ить.
            e.preventDefault();
            e.stopImmediatePropagation();
            setCrosshairFromTouch(t);
            return;
        }

        // Ще чекаємо на long-press — перевіряємо чи палець не "поплив".
        if (armTimer == null) return;
        const dx = Math.abs(t.clientX - startX);
        const dy = Math.abs(t.clientY - startY);
        if (dx > MOVE_THRESHOLD_PX || dy > MOVE_THRESHOLD_PX) {
            // Звичайний swipe-pan, не long-press. Скасовуємо arm,
            // LWC отримає це touchmove та pан-ить як завжди.
            clearArmTimer();
        }
    }

    function onTouchEnd(): void {
        clearArmTimer();
        exitLock();
    }

    // capture:true → наш handler fire-ить ПЕРЕД LWC's listener (LWC слухає
    // touch* на canvas, ми на container — capture phase обходить deep target).
    // passive:false → preventDefault працює (passive:true ігнорував би його).
    const captureOpts: AddEventListenerOptions = { capture: true, passive: false };
    const passiveOpts: AddEventListenerOptions = { capture: true, passive: true };

    container.addEventListener('touchstart', onTouchStart, passiveOpts);
    container.addEventListener('touchmove', onTouchMove, captureOpts);
    container.addEventListener('touchend', onTouchEnd, passiveOpts);
    container.addEventListener('touchcancel', onTouchEnd, passiveOpts);

    return () => {
        clearArmTimer();
        // Якщо disposed mid-lock — повертаємо chart до default стану.
        exitLock();
        container.removeEventListener('touchstart', onTouchStart, passiveOpts);
        container.removeEventListener('touchmove', onTouchMove, captureOpts);
        container.removeEventListener('touchend', onTouchEnd, passiveOpts);
        container.removeEventListener('touchcancel', onTouchEnd, passiveOpts);
    };
}
