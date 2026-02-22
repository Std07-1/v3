// src/chart/interaction.ts
// P3.3-P3.5: Price axis interactions (V3 parity)
// SSOT: chart_adapter_lite.js:310-810 (V3 reference)
//
// Features:
//   - Wheel on price axis  → Y-zoom (anchor at cursor price)
//   - Shift+wheel in pane  → Y-pan (vertical scroll)
//   - Drag in pane (by Y)  → Y-pan (with activation threshold 6px)
//   - Double-click on price axis → resetViewAndFollow (auto-scale + scroll to latest)
//
// Integration: call setupPriceScaleInteractions(container, chart, series)
// Returns cleanup function.

import type { IChartApi, ISeriesApi } from 'lightweight-charts';

// ─── Constants (V3: chart_adapter_lite.js:153-156) ───
const DRAG_ACTIVATION_PX = 6;
const MIN_PRICE_SPAN = 0.0001;
const PRICE_AXIS_FALLBACK_WIDTH_PX = 60;
const WHEEL_OPTIONS: AddEventListenerOptions = { passive: false };

// ─── Price scale state ───
interface PriceRange {
    min: number;
    max: number;
}

interface PriceScaleState {
    manualRange: PriceRange | null;
    lastAutoRange: PriceRange | null;
}

interface VerticalPanState {
    active: boolean;
    pending: boolean;
    startY: number;
    startX: number;
    startRange: PriceRange | null;
    baseRange: PriceRange | null;
    pointerId: number | null;
}

// ─── Utility functions (V3: chart_adapter_lite.js:310-330, 477-530) ───

function normalizeRange(range: PriceRange | null): PriceRange | null {
    if (!range) return null;
    let min = Number(range.min);
    let max = Number(range.max);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (min === max) {
        min -= MIN_PRICE_SPAN / 2;
        max += MIN_PRICE_SPAN / 2;
    }
    if (max - min < MIN_PRICE_SPAN) {
        const mid = (max + min) / 2;
        min = mid - MIN_PRICE_SPAN / 2;
        max = mid + MIN_PRICE_SPAN / 2;
    }
    if (!(max > min)) return null;
    return { min, max };
}

function getRelativePointer(event: { clientX: number; clientY: number }, container: HTMLElement) {
    const rect = container.getBoundingClientRect();
    return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
        width: rect.width,
        height: rect.height,
    };
}

function getPaneMetrics(chart: IChartApi, container: HTMLElement) {
    const paneSize = (chart as any).paneSize?.() || {};
    let priceScaleWidth = 0;
    try {
        priceScaleWidth = chart.priceScale('right').width() || 0;
    } catch {
        priceScaleWidth = PRICE_AXIS_FALLBACK_WIDTH_PX;
    }
    const width = container.clientWidth || 0;
    const height = container.clientHeight || 0;
    const paneWidth = paneSize.width > 0 ? paneSize.width : Math.max(0, width - PRICE_AXIS_FALLBACK_WIDTH_PX);
    const paneHeight = paneSize.height > 0 ? paneSize.height : height;
    return { paneWidth, paneHeight, priceScaleWidth };
}

function isPointerInPriceAxis(
    event: { clientX: number; clientY: number },
    container: HTMLElement,
    chart: IChartApi,
): boolean {
    const pointer = getRelativePointer(event, container);
    const { paneWidth, paneHeight, priceScaleWidth } = getPaneMetrics(chart, container);
    if (!pointer.width || !pointer.height) return false;
    const effectivePaneHeight = paneHeight || pointer.height;
    const axisLeft = paneWidth > 0 ? paneWidth : Math.max(0, pointer.width - PRICE_AXIS_FALLBACK_WIDTH_PX);
    const axisRight = paneWidth > 0 && priceScaleWidth > 0 ? paneWidth + priceScaleWidth : pointer.width;
    return pointer.x >= axisLeft && pointer.x <= axisRight && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
}

function isPointerInsidePane(
    event: { clientX: number; clientY: number },
    container: HTMLElement,
    chart: IChartApi,
): boolean {
    const pointer = getRelativePointer(event, container);
    const { paneWidth, paneHeight } = getPaneMetrics(chart, container);
    if (!pointer.width || !pointer.height) return false;
    const effectivePaneHeight = paneHeight || pointer.height;
    const effectivePaneWidth = paneWidth > 0 ? paneWidth : Math.max(0, pointer.width - PRICE_AXIS_FALLBACK_WIDTH_PX);
    return pointer.x >= 0 && pointer.x <= effectivePaneWidth && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
}

// ─── Main setup function ───
// V3: chart_adapter_lite.js:538-804
export function setupPriceScaleInteractions(
    container: HTMLElement,
    chart: IChartApi,
    series: ISeriesApi<'Candlestick'>,
): () => void {
    const cleanups: Array<() => void> = [];

    const state: PriceScaleState = {
        manualRange: null,
        lastAutoRange: null,
    };

    const panState: VerticalPanState = {
        active: false,
        pending: false,
        startY: 0,
        startX: 0,
        startRange: null,
        baseRange: null,
        pointerId: null,
    };

    // ─── autoscaleInfoProvider (V3: chart_adapter_lite.js:360-380) ───
    // Зберігає manualRange між перемалюваннями LWC
    const autoscaleProvider = (baseImplementation: () => any) => {
        if (!state.manualRange) {
            const base = baseImplementation();
            if (base?.priceRange) {
                state.lastAutoRange = {
                    min: base.priceRange.minValue,
                    max: base.priceRange.maxValue,
                };
            }
            return base;
        }
        const range = state.manualRange;
        const base = baseImplementation();
        return {
            priceRange: {
                minValue: range.min,
                maxValue: range.max,
            },
            margins: base?.margins,
        };
    };

    series.applyOptions({ autoscaleInfoProvider: autoscaleProvider });

    // ─── Sync helpers ───

    function requestPriceScaleSync() {
        const logicalRange = chart.timeScale().getVisibleLogicalRange();
        if (logicalRange && Number.isFinite(logicalRange.from) && Number.isFinite(logicalRange.to)) {
            chart.timeScale().setVisibleLogicalRange({ from: logicalRange.from, to: logicalRange.to });
            return;
        }
        const position = chart.timeScale().scrollPosition();
        if (Number.isFinite(position)) {
            chart.timeScale().scrollToPosition(position, false);
        }
    }

    function applyManualRange(range: PriceRange) {
        const normalized = normalizeRange(range);
        if (!normalized) return;
        state.manualRange = normalized;
        requestPriceScaleSync();
    }

    function ensureManualRange(baseRange: PriceRange | null) {
        if (!state.manualRange && baseRange) {
            state.manualRange = { ...baseRange };
        }
    }

    function getEffectivePriceRange(): PriceRange | null {
        if (state.manualRange) return { ...state.manualRange };
        if (state.lastAutoRange) return { ...state.lastAutoRange };
        const { paneHeight } = getPaneMetrics(chart, container);
        if (!paneHeight) return null;
        const top = series.coordinateToPrice(0);
        const bottom = series.coordinateToPrice(paneHeight);
        if (top === null || bottom === null || !Number.isFinite(top) || !Number.isFinite(bottom)) return null;
        const min = Math.min(top, bottom);
        const max = Math.max(top, bottom);
        if (!(max > min)) return null;
        state.lastAutoRange = { min, max };
        return { min, max };
    }

    // ─── Wheel: pan (Shift) / zoom (price axis) ───
    // V3: chart_adapter_lite.js:540-650

    function applyWheelPan(deltaY: number) {
        const currentRange = getEffectivePriceRange();
        if (!currentRange) return;
        ensureManualRange(currentRange);
        const { paneHeight } = getPaneMetrics(chart, container);
        if (!paneHeight || !state.manualRange) return;
        const span = state.manualRange.max - state.manualRange.min;
        if (!(span > 0)) return;
        const offset = (-deltaY / paneHeight) * span * 0.5;
        applyManualRange({
            min: state.manualRange.min + offset,
            max: state.manualRange.max + offset,
        });
    }

    function applyWheelZoom(event: { clientY: number; deltaY: number }) {
        const currentRange = getEffectivePriceRange();
        if (!currentRange) return;
        const rect = container.getBoundingClientRect();
        const anchor = series.coordinateToPrice(event.clientY - rect.top);
        if (anchor === null || !Number.isFinite(anchor)) return;
        const span = currentRange.max - currentRange.min;
        if (!(span > 0)) return;
        const intensity = 0.002;
        const scale = Math.exp(Math.min(Math.abs(event.deltaY), 600) * intensity);
        const factor = event.deltaY < 0 ? 1 / scale : scale;
        const distanceMin = anchor - currentRange.min;
        const distanceMax = currentRange.max - anchor;
        const nextRange = normalizeRange({
            min: anchor - distanceMin * factor,
            max: anchor + distanceMax * factor,
        });
        if (nextRange) {
            applyManualRange(nextRange);
        }
    }

    // ─── Wheel handler with rAF throttle ───

    let pendingWheelRaf: number | null = null;
    let pendingWheel: { clientX: number; clientY: number; deltaY: number; shiftKey: boolean } | null = null;

    const flushPendingWheel = () => {
        pendingWheelRaf = null;
        const payload = pendingWheel;
        pendingWheel = null;
        if (!payload) return;
        if (payload.shiftKey) {
            applyWheelPan(payload.deltaY);
        } else {
            applyWheelZoom(payload);
        }
    };

    const handleWheel = (event: WheelEvent) => {
        const inAxis = isPointerInPriceAxis(event, container, chart);
        const inPane = isPointerInsidePane(event, container, chart);

        if (!inAxis && !(event.shiftKey && inPane)) {
            return; // Не наша зона — дозволити LWC обробити X-zoom/scroll
        }

        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }

        const effectiveRange = getEffectivePriceRange();
        if (!effectiveRange) {
            // Немає даних ще — schedule deferred
            pendingWheel = {
                clientX: event.clientX,
                clientY: event.clientY,
                deltaY: event.deltaY,
                shiftKey: Boolean(event.shiftKey),
            };
            if (pendingWheelRaf === null) {
                pendingWheelRaf = requestAnimationFrame(flushPendingWheel);
            }
            return;
        }

        if (event.shiftKey && inPane) {
            applyWheelPan(event.deltaY);
            return;
        }
        if (inAxis) {
            applyWheelZoom(event);
        }
    };

    container.addEventListener('wheel', handleWheel, WHEEL_OPTIONS);
    cleanups.push(() => container.removeEventListener('wheel', handleWheel, WHEEL_OPTIONS));
    cleanups.push(() => {
        if (pendingWheelRaf !== null) {
            cancelAnimationFrame(pendingWheelRaf);
            pendingWheelRaf = null;
        }
        pendingWheel = null;
    });

    // ─── Vertical pan: pointer drag ───
    // V3: chart_adapter_lite.js:660-800

    const setLibraryDragEnabled = (enabled: boolean) => {
        try {
            chart.applyOptions({
                handleScroll: {
                    pressedMouseMove: enabled,
                },
            });
        } catch {
            // noop
        }
    };

    const stopVerticalPan = () => {
        if (!panState.pending) return;
        panState.pending = false;
        panState.active = false;
        panState.startRange = null;
        panState.baseRange = null;
        panState.pointerId = null;
        setLibraryDragEnabled(true);
    };

    const beginPan = (clientX: number, clientY: number, pointerId: number | null = null) => {
        const currentRange = getEffectivePriceRange();
        if (!currentRange) return;
        panState.pending = true;
        panState.active = false;
        panState.startY = clientY;
        panState.startX = clientX;
        panState.baseRange = currentRange;
        panState.startRange = null;
        panState.pointerId = pointerId;
    };

    const movePan = (event: PointerEvent | MouseEvent, clientX: number, clientY: number) => {
        if (!panState.pending) return;
        if (panState.pointerId !== null && 'pointerId' in event) {
            if (event.pointerId !== panState.pointerId) return;
        }

        const { paneHeight } = getPaneMetrics(chart, container);
        if (!paneHeight) return;
        const deltaY = clientY - panState.startY;
        const deltaX = clientX - panState.startX;

        if (!panState.active) {
            if (Math.abs(deltaY) < DRAG_ACTIVATION_PX || Math.abs(deltaY) <= Math.abs(deltaX)) {
                return; // Ще не активований, або X-домінантний drag → LWC обробить
            }
            ensureManualRange(panState.baseRange);
            panState.startRange = state.manualRange ? { ...state.manualRange } : null;
            panState.active = true;
            setLibraryDragEnabled(false);
        }

        event.preventDefault();
        event.stopPropagation();

        if (!panState.startRange) return;
        const span = panState.startRange.max - panState.startRange.min;
        if (!(span > 0)) return;
        const offset = (deltaY / paneHeight) * span;
        applyManualRange({
            min: panState.startRange.min + offset,
            max: panState.startRange.max + offset,
        });
    };

    // Pointer events (V3: chart_adapter_lite.js:730-790)
    const handlePointerDown = (event: PointerEvent) => {
        if (!event || event.button !== 0) return;
        if (!isPointerInsidePane(event, container, chart)) return;
        beginPan(event.clientX, event.clientY, event.pointerId);
    };
    container.addEventListener('pointerdown', handlePointerDown, true);
    cleanups.push(() => container.removeEventListener('pointerdown', handlePointerDown, true));

    const handlePointerMove = (event: PointerEvent) => {
        movePan(event, event.clientX, event.clientY);
    };
    window.addEventListener('pointermove', handlePointerMove, true);
    cleanups.push(() => window.removeEventListener('pointermove', handlePointerMove, true));

    const handlePointerUp = () => stopVerticalPan();
    window.addEventListener('pointerup', handlePointerUp, true);
    window.addEventListener('pointercancel', handlePointerUp, true);
    window.addEventListener('blur', stopVerticalPan);
    cleanups.push(() => window.removeEventListener('pointerup', handlePointerUp, true));
    cleanups.push(() => window.removeEventListener('pointercancel', handlePointerUp, true));
    cleanups.push(() => window.removeEventListener('blur', stopVerticalPan));

    // ─── Double-click on price axis → resetViewAndFollow ───
    // V3: chart_adapter_lite.js:798-803

    const handleDblClick = (event: MouseEvent) => {
        if (isPointerInPriceAxis(event, container, chart)) {
            state.manualRange = null;
            state.lastAutoRange = null;
            requestPriceScaleSync();
            chart.timeScale().scrollToRealTime();
        }
    };
    container.addEventListener('dblclick', handleDblClick);
    cleanups.push(() => container.removeEventListener('dblclick', handleDblClick));

    // ─── Public: resetManualRange (callable from outside, e.g. on full frame switch) ───
    (container as any).__resetManualPriceScale = () => {
        state.manualRange = null;
        state.lastAutoRange = null;
    };

    // ─── Return cleanup function ───
    return () => {
        for (const fn of cleanups) {
            try { fn(); } catch { /* noop */ }
        }
        cleanups.length = 0;
        delete (container as any).__resetManualPriceScale;
    };
}
