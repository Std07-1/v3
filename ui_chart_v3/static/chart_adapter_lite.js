/*
 * UI Chart v3: chart_adapter_lite
 *
 * Мінімальний адаптер взаємодій чарту, узгоджений з UX у нашому UI_v2:
 * - wheel по price-axis = zoom по Y
 * - Shift+wheel у pane = vertical-pan
 * - drag у pane (по Y) = vertical-pan
 * - built-in time-scale лишається активним
 */

(function () {
    const DEFAULT_CHART_OPTIONS = {
        layout: {
            background: { color: "#ffffff" },
            textColor: "#111111",
        },
        grid: {
            vertLines: { color: "rgba(42, 46, 57, 0.18)" },
            horzLines: { color: "rgba(42, 46, 57, 0.18)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: "rgba(17, 17, 17, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
            horzLine: {
                color: "rgba(17, 17, 17, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
        },
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            vertTouchDrag: true,
            horzTouchDrag: true,
        },
        handleScale: {
            axisPressedMouseMove: {
                time: true,
                price: true,
            },
            axisDoubleClickReset: true,
            mouseWheel: true,
            pinch: true,
        },
        rightPriceScale: {
            borderVisible: true,
            ticksVisible: true,
            autoScale: true,
            scaleMargins: {
                top: 0.12,
                bottom: 0.18,
            },
        },
        timeScale: {
            borderVisible: false,
            rightOffset: 0,
            barSpacing: 8,
            maxBarSpacing: 12,
            timeVisible: true,
            secondsVisible: false,
            fixLeftEdge: false,
            fixRightEdge: false,
            lockVisibleTimeRangeOnResize: false,
        },
    };

    const DARK_CHART_OPTIONS = {
        ...DEFAULT_CHART_OPTIONS,
        layout: {
            background: { color: "#0b0f14" },
            textColor: "#d5d5d5",
        },
        grid: {
            vertLines: { color: "rgba(43, 56, 70, 0.4)" },
            horzLines: { color: "rgba(43, 56, 70, 0.4)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: "rgba(213, 213, 213, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
            horzLine: {
                color: "rgba(213, 213, 213, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
        },
    };

    const DARK_GRAY_CHART_OPTIONS = {
        ...DEFAULT_CHART_OPTIONS,
        layout: {
            background: { color: "#2a3036" },
            textColor: "#d0d3d8",
        },
        grid: {
            vertLines: { color: "rgba(90, 96, 104, 0.35)" },
            horzLines: { color: "rgba(90, 96, 104, 0.35)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: "rgba(178, 206, 247, 0.45)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
            horzLine: {
                color: "rgba(42, 46, 52, 0.45)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
            },
        },
    };

    const MIN_PRICE_SPAN = 1e-4;
    const WHEEL_OPTIONS = { passive: false, capture: true };
    const PRICE_AXIS_FALLBACK_WIDTH_PX = 56;
    const DRAG_ACTIVATION_PX = 6;
    const VOLUME_UP_COLOR = "rgba(38, 166, 154, 0.32)";
    const VOLUME_DOWN_COLOR = "rgba(239, 83, 80, 0.32)";
    const CANDLE_STYLE_DEFAULT = "classic";
    const CANDLE_STYLES = {
        classic: {
            upColor: "#26a69a",
            downColor: "#ef5350",
            borderUpColor: "#26a69a",
            borderDownColor: "#ef5350",
            wickUpColor: "#26a69a",
            wickDownColor: "#ef5350",
        },
        gray: {
            upColor: "#9aa0a6",
            downColor: "rgba(255,255,255,0)",
            borderUpColor: "#9aa0a6",
            borderDownColor: "#5f6368",
            wickUpColor: "#9aa0a6",
            wickDownColor: "#5f6368",
        },
        dark: {
            upColor: "#3a3f44",
            downColor: "#1f2327",
            borderUpColor: "#3a3f44",
            borderDownColor: "#1f2327",
            wickUpColor: "#3a3f44",
            wickDownColor: "#1f2327",
        },
        white: {
            upColor: "#e2e5e9",
            downColor: "#2f3338",
            borderUpColor: "#e2e5e9",
            borderDownColor: "#2f3338",
            wickUpColor: "#e2e5e9",
            wickDownColor: "#2f3338",
        },
        hollow: {
            upColor: "rgba(255,255,255,0)",
            downColor: "#ef5350",
            borderUpColor: "#26a69a",
            borderDownColor: "#ef5350",
            wickUpColor: "#26a69a",
            wickDownColor: "#ef5350",
        },
    };

    function normalizeBar(bar) {
        if (!bar) return null;
        const time = Number(bar.time);
        const open = Number(bar.open);
        const high = Number(bar.high);
        const low = Number(bar.low);
        const close = Number(bar.close);
        const volumeRaw = bar.volume ?? bar.value ?? bar.v ?? 0;
        const volume = Number(volumeRaw);
        if (!Number.isFinite(time) || !Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) {
            return null;
        }
        return {
            time: Math.floor(time),
            open,
            high,
            low,
            close,
            volume: Number.isFinite(volume) ? volume : 0,
        };
    }

    function normalizeRange(range) {
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

    function createChartController(container, options = {}) {
        if (!container) {
            throw new Error("chart_adapter_lite: контейнер не передано");
        }
        if (typeof LightweightCharts === "undefined") {
            throw new Error("chart_adapter_lite: lightweight-charts не доступний");
        }

        const tooltipEl = options?.tooltipEl ?? null;

        const chart = LightweightCharts.createChart(container, DEFAULT_CHART_OPTIONS);
        const candles = chart.addCandlestickSeries({
            priceLineVisible: false,
            lastValueVisible: true,
        });
        const barsSeries = chart.addBarSeries({
            priceLineVisible: false,
            lastValueVisible: true,
        });
        barsSeries.applyOptions({ visible: false });
        const volumes = chart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "",
        });
        try {
            volumes.priceScale().applyOptions({
                scaleMargins: { top: 0.895, bottom: 0 },
            });
        } catch (_e) {
            // noop
        }

        const priceScaleState = {
            manualRange: null,
            lastAutoRange: null,
        };

        let lastBar = null;
        let activeSeries = candles;
        let currentCandleStyle = CANDLE_STYLE_DEFAULT;
        let lastBarsData = [];
        let currentVolumeColors = {
            up: VOLUME_UP_COLOR,
            down: VOLUME_DOWN_COLOR,
        };
        let barTimeSpanSeconds = 60;
        const interactionCleanup = [];
        const verticalPanState = {
            active: false,
            pending: false,
            startY: 0,
            startX: 0,
            startRange: null,
            baseRange: null,
            pointerId: null,
        };
        const visibleRangeHandlers = new Set();
        let visibleRangeRaf = null;

        const formatNumber = (value, digits = 6) => {
            if (!Number.isFinite(value)) return "—";
            return Number(value).toLocaleString("en-US", {
                minimumFractionDigits: 0,
                maximumFractionDigits: digits,
            });
        };

        const getTimeKey = (timeValue) => {
            if (timeValue == null) return "";
            if (typeof timeValue === "number") return String(timeValue);
            if (typeof timeValue === "object" && "year" in timeValue) {
                const y = timeValue.year || 1970;
                const m = String(timeValue.month || 1).padStart(2, "0");
                const d = String(timeValue.day || 1).padStart(2, "0");
                return `${y}-${m}-${d}`;
            }
            return String(timeValue);
        };

        const makePriceScaleAutoscaleInfoProvider = () => {
            return (baseImplementation) => {
                if (!priceScaleState.manualRange) {
                    const base = baseImplementation();
                    if (base?.priceRange) {
                        priceScaleState.lastAutoRange = {
                            min: base.priceRange.minValue,
                            max: base.priceRange.maxValue,
                        };
                    }
                    return base;
                }
                const range = priceScaleState.manualRange;
                const base = baseImplementation();
                return {
                    priceRange: {
                        minValue: range.min,
                        maxValue: range.max,
                    },
                    margins: base?.margins,
                };
            };
        };

        const autoscaleProvider = makePriceScaleAutoscaleInfoProvider();
        candles.applyOptions({ autoscaleInfoProvider: autoscaleProvider });
        barsSeries.applyOptions({ autoscaleInfoProvider: autoscaleProvider });
        const volumeByTime = new Map();

        const isPointInCandleRange = (bar, point) => {
            if (!bar || !point) return false;
            const highY = candles.priceToCoordinate(bar.high);
            const lowY = candles.priceToCoordinate(bar.low);
            if (!Number.isFinite(highY) || !Number.isFinite(lowY)) return false;
            const top = Math.min(highY, lowY);
            const bottom = Math.max(highY, lowY);
            return point.y >= top && point.y <= bottom;
        };

        if (tooltipEl && typeof chart.subscribeCrosshairMove === "function") {
            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.time || !param.seriesData) {
                    tooltipEl.hidden = true;
                    return;
                }
                const bar = param.seriesData.get(activeSeries);
                if (!bar) {
                    tooltipEl.hidden = true;
                    return;
                }
                const point = param.point || null;
                if (!point || !isPointInCandleRange(bar, point)) {
                    tooltipEl.hidden = true;
                    return;
                }
                const timeKey = getTimeKey(param.time);
                const open = formatNumber(bar.open);
                const high = formatNumber(bar.high);
                const low = formatNumber(bar.low);
                const close = formatNumber(bar.close);
                const volumeRaw = volumeByTime.get(timeKey) ?? bar.volume ?? 0;
                const volume = formatNumber(volumeRaw, 0);
                const change = Number.isFinite(bar.open) && Number.isFinite(bar.close)
                    ? formatNumber(bar.close - bar.open)
                    : "—";

                tooltipEl.innerHTML = `O: ${open}  H: ${high}  L: ${low}  C: ${close}<br>Δ: ${change}  V: ${volume}`;

                const safePoint = point || { x: 0, y: 0 };
                const rect = container.getBoundingClientRect();
                const margin = 12;
                let left = safePoint.x + margin;
                let top = safePoint.y + margin;
                if (left + tooltipEl.offsetWidth > rect.width) {
                    left = safePoint.x - tooltipEl.offsetWidth - margin;
                }
                if (top + tooltipEl.offsetHeight > rect.height) {
                    top = safePoint.y - tooltipEl.offsetHeight - margin;
                }
                let drawerOffset = 0;
                try {
                    const raw = getComputedStyle(document.documentElement).getPropertyValue("--drawer-offset");
                    drawerOffset = Number(String(raw).replace("px", "").trim()) || 0;
                } catch (_e) {
                    drawerOffset = 0;
                }
                tooltipEl.style.left = `${Math.max(0, left)}px`;
                tooltipEl.style.top = `${Math.max(0, top + drawerOffset)}px`;
                tooltipEl.hidden = false;
            });
        }

        function getRelativePointer(event) {
            const rect = container.getBoundingClientRect();
            return {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top,
                width: rect.width,
                height: rect.height,
                rect,
            };
        }

        function getPaneMetrics() {
            const paneSize = chart.paneSize() || {};
            const priceScaleWidth = chart.priceScale("right").width() || 0;
            const width = container.clientWidth || 0;
            const height = container.clientHeight || 0;
            const paneWidth = paneSize.width > 0 ? paneSize.width : Math.max(0, width - PRICE_AXIS_FALLBACK_WIDTH_PX);
            const paneHeight = paneSize.height > 0 ? paneSize.height : height;
            return { paneWidth, paneHeight, priceScaleWidth };
        }

        function isPointerInPriceAxis(event) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight, priceScaleWidth } = getPaneMetrics();
            if (!pointer.width || !pointer.height) return false;
            const effectivePaneHeight = paneHeight || pointer.height;
            const axisLeft = paneWidth > 0 ? paneWidth : Math.max(0, pointer.width - PRICE_AXIS_FALLBACK_WIDTH_PX);
            const axisRight = paneWidth > 0 && priceScaleWidth > 0 ? paneWidth + priceScaleWidth : pointer.width;
            return pointer.x >= axisLeft && pointer.x <= axisRight && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
        }

        function isPointerInsidePane(event) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight } = getPaneMetrics();
            if (!pointer.width || !pointer.height) return false;
            const effectivePaneHeight = paneHeight || pointer.height;
            const effectivePaneWidth = paneWidth > 0 ? paneWidth : Math.max(0, pointer.width - PRICE_AXIS_FALLBACK_WIDTH_PX);
            return pointer.x >= 0 && pointer.x <= effectivePaneWidth && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
        }

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

        function applyManualRange(range) {
            const normalized = normalizeRange(range);
            if (!normalized) return;
            priceScaleState.manualRange = normalized;
            requestPriceScaleSync();
        }

        function ensureManualRange(baseRange) {
            if (!priceScaleState.manualRange && baseRange) {
                priceScaleState.manualRange = { ...baseRange };
            }
        }

        function getEffectivePriceRange() {
            if (priceScaleState.manualRange) return { ...priceScaleState.manualRange };
            if (priceScaleState.lastAutoRange) return { ...priceScaleState.lastAutoRange };
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) return null;
            const series = activeSeries || candles;
            const top = series.coordinateToPrice(0);
            const bottom = series.coordinateToPrice(paneHeight);
            if (!Number.isFinite(top) || !Number.isFinite(bottom)) return null;
            const min = Math.min(top, bottom);
            const max = Math.max(top, bottom);
            if (!(max > min)) return null;
            priceScaleState.lastAutoRange = { min, max };
            return { min, max };
        }

        function resetManualPriceScale(options = {}) {
            priceScaleState.manualRange = null;
            if (!options.silent) {
                requestPriceScaleSync();
            }
        }

        function applyWheelPan(event) {
            const currentRange = getEffectivePriceRange();
            if (!currentRange) return;
            ensureManualRange(currentRange);
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) return;
            const span = priceScaleState.manualRange.max - priceScaleState.manualRange.min;
            if (!(span > 0)) return;
            const offset = (-event.deltaY / paneHeight) * span * 0.5;
            applyManualRange({
                min: priceScaleState.manualRange.min + offset,
                max: priceScaleState.manualRange.max + offset,
            });
        }

        function applyWheelZoom(event) {
            const currentRange = getEffectivePriceRange();
            if (!currentRange) return;
            const rect = container.getBoundingClientRect();
            const series = activeSeries || candles;
            const anchor = series.coordinateToPrice(event.clientY - rect.top);
            if (!Number.isFinite(anchor)) return;
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

        function setupPriceScaleInteractions() {
            let pendingWheelRaf = null;
            let pendingWheel = null;

            const flushPendingWheel = () => {
                pendingWheelRaf = null;
                const payload = pendingWheel;
                pendingWheel = null;
                if (!payload) return;
                const effectiveRange = getEffectivePriceRange();
                if (!effectiveRange) return;
                const pointerInAxis = isPointerInPriceAxis(payload);
                const pointerInPane = isPointerInsidePane(payload);
                if (payload.shiftKey && pointerInPane) {
                    applyWheelPan(payload);
                    return;
                }
                if (pointerInAxis) {
                    applyWheelZoom(payload);
                }
            };

            const schedulePendingWheel = (event) => {
                pendingWheel = {
                    clientX: event.clientX,
                    clientY: event.clientY,
                    deltaY: event.deltaY,
                    shiftKey: Boolean(event.shiftKey),
                };
                if (pendingWheelRaf !== null) return;
                pendingWheelRaf = window.requestAnimationFrame(flushPendingWheel);
            };

            const handleWheel = (event) => {
                const pointerInAxis = isPointerInPriceAxis(event);
                const pointerInPane = isPointerInsidePane(event);
                if (!pointerInAxis && !(event.shiftKey && pointerInPane)) {
                    return;
                }
                event.preventDefault();
                if (typeof event.stopImmediatePropagation === "function") {
                    event.stopImmediatePropagation();
                }
                event.stopPropagation();

                const effectiveRange = getEffectivePriceRange();
                if (!effectiveRange) {
                    schedulePendingWheel(event);
                    return;
                }
                if (event.shiftKey) {
                    applyWheelPan(event);
                    return;
                }
                if (pointerInAxis) {
                    applyWheelZoom(event);
                }
            };

            container.addEventListener("wheel", handleWheel, WHEEL_OPTIONS);
            interactionCleanup.push(() => container.removeEventListener("wheel", handleWheel, WHEEL_OPTIONS));
            interactionCleanup.push(() => {
                if (pendingWheelRaf !== null) {
                    try {
                        window.cancelAnimationFrame(pendingWheelRaf);
                    } catch (_e) {
                        // noop
                    }
                    pendingWheelRaf = null;
                }
                pendingWheel = null;
            });

            const setLibraryDragEnabled = (enabled) => {
                try {
                    chart.applyOptions({
                        handleScroll: {
                            pressedMouseMove: Boolean(enabled),
                        },
                    });
                } catch (_e) {
                    // noop
                }
            };

            const stopVerticalPan = () => {
                if (!verticalPanState.pending) return;
                verticalPanState.pending = false;
                verticalPanState.active = false;
                verticalPanState.startRange = null;
                verticalPanState.baseRange = null;
                verticalPanState.pointerId = null;
                setLibraryDragEnabled(true);
            };

            const beginPan = (clientX, clientY, pointerId = null) => {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) return;
                verticalPanState.pending = true;
                verticalPanState.active = false;
                verticalPanState.startY = clientY;
                verticalPanState.startX = clientX;
                verticalPanState.baseRange = currentRange;
                verticalPanState.startRange = null;
                verticalPanState.pointerId = pointerId;
            };

            const movePan = (event, clientX, clientY) => {
                if (!verticalPanState.pending) return;
                if (verticalPanState.pointerId !== null && event?.pointerId !== undefined) {
                    if (event.pointerId !== verticalPanState.pointerId) return;
                }

                const { paneHeight } = getPaneMetrics();
                if (!paneHeight) return;
                const deltaY = clientY - verticalPanState.startY;
                const deltaX = clientX - verticalPanState.startX;

                if (!verticalPanState.active) {
                    if (Math.abs(deltaY) < DRAG_ACTIVATION_PX || Math.abs(deltaY) <= Math.abs(deltaX)) {
                        return;
                    }
                    ensureManualRange(verticalPanState.baseRange);
                    verticalPanState.startRange = { ...priceScaleState.manualRange };
                    verticalPanState.active = true;
                    setLibraryDragEnabled(false);
                }

                event.preventDefault();
                event.stopPropagation();

                const span = verticalPanState.startRange.max - verticalPanState.startRange.min;
                if (!(span > 0)) return;
                const offset = (deltaY / paneHeight) * span;
                applyManualRange({
                    min: verticalPanState.startRange.min + offset,
                    max: verticalPanState.startRange.max + offset,
                });
            };

            const usePointerEvents = typeof window.PointerEvent !== "undefined";
            if (usePointerEvents) {
                const handlePointerDown = (event) => {
                    if (!event || event.button !== 0) return;
                    if (!isPointerInsidePane(event)) return;
                    beginPan(event.clientX, event.clientY, event.pointerId);
                };
                container.addEventListener("pointerdown", handlePointerDown, true);
                interactionCleanup.push(() => container.removeEventListener("pointerdown", handlePointerDown, true));

                const handlePointerMove = (event) => {
                    movePan(event, event.clientX, event.clientY);
                };
                window.addEventListener("pointermove", handlePointerMove, true);
                interactionCleanup.push(() => window.removeEventListener("pointermove", handlePointerMove, true));

                const handlePointerUp = () => stopVerticalPan();
                window.addEventListener("pointerup", handlePointerUp, true);
                window.addEventListener("pointercancel", handlePointerUp, true);
                window.addEventListener("blur", stopVerticalPan);
                interactionCleanup.push(() => window.removeEventListener("pointerup", handlePointerUp, true));
                interactionCleanup.push(() => window.removeEventListener("pointercancel", handlePointerUp, true));
                interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));
            } else {
                const handleMouseDown = (event) => {
                    if (event.button !== 0 || !isPointerInsidePane(event)) return;
                    beginPan(event.clientX, event.clientY, null);
                };
                container.addEventListener("mousedown", handleMouseDown, true);
                interactionCleanup.push(() => container.removeEventListener("mousedown", handleMouseDown, true));

                const handleMouseMove = (event) => movePan(event, event.clientX, event.clientY);
                window.addEventListener("mousemove", handleMouseMove, true);
                interactionCleanup.push(() => window.removeEventListener("mousemove", handleMouseMove, true));

                const handleMouseUp = () => stopVerticalPan();
                window.addEventListener("mouseup", handleMouseUp, true);
                window.addEventListener("blur", stopVerticalPan);
                interactionCleanup.push(() => window.removeEventListener("mouseup", handleMouseUp, true));
                interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));
            }

            const handleDblClick = (event) => {
                if (isPointerInPriceAxis(event)) {
                    resetViewAndFollow();
                }
            };
            container.addEventListener("dblclick", handleDblClick);
            interactionCleanup.push(() => container.removeEventListener("dblclick", handleDblClick));
        }

        function updateBarTimeSpanFromBars(bars) {
            if (!Array.isArray(bars) || bars.length < 2) return;
            let total = 0;
            let count = 0;
            for (let i = bars.length - 1; i > 0 && count < 32; i -= 1) {
                const diff = bars[i].time - bars[i - 1].time;
                if (Number.isFinite(diff) && diff > 0) {
                    total += diff;
                    count += 1;
                }
            }
            if (count) {
                barTimeSpanSeconds = Math.max(1, Math.round(total / count));
            }
        }

        function setBars(bars) {
            if (!Array.isArray(bars) || bars.length === 0) {
                resetManualPriceScale({ silent: true });
                priceScaleState.lastAutoRange = null;
                candles.setData([]);
                barsSeries.setData([]);
                volumes.setData([]);
                volumeByTime.clear();
                lastBar = null;
                lastBarsData = [];
                return;
            }

            const normalized = bars
                .map((bar) => normalizeBar(bar))
                .filter(Boolean)
                .sort((a, b) => a.time - b.time);
            const deduped = [];
            let lastTime = null;
            for (const bar of normalized) {
                if (lastTime === bar.time) {
                    deduped[deduped.length - 1] = bar;
                } else {
                    deduped.push(bar);
                    lastTime = bar.time;
                }
            }

            lastBarsData = deduped;
            candles.setData(deduped);
            barsSeries.setData(deduped);
            volumeByTime.clear();
            const volumeData = deduped.map((bar) => ({
                time: bar.time,
                value: bar.volume,
                color: bar.close >= bar.open ? currentVolumeColors.up : currentVolumeColors.down,
            }));
            volumes.setData(volumeData);
            for (const bar of deduped) {
                volumeByTime.set(String(bar.time), bar.volume);
            }
            lastBar = deduped.length ? deduped[deduped.length - 1] : null;
            updateBarTimeSpanFromBars(deduped);
            scheduleVisibleRangeNotify();
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) return;
            if (!lastBar || normalized.time >= lastBar.time) {
                if (lastBar && normalized.time > lastBar.time) {
                    const diff = normalized.time - lastBar.time;
                    if (Number.isFinite(diff) && diff > 0) {
                        barTimeSpanSeconds = Math.max(1, Math.round((barTimeSpanSeconds * 3 + diff) / 4));
                    }
                }
                if (lastBarsData.length && lastBarsData[lastBarsData.length - 1].time === normalized.time) {
                    lastBarsData[lastBarsData.length - 1] = normalized;
                } else {
                    lastBarsData.push(normalized);
                }
                candles.update(normalized);
                barsSeries.update(normalized);
                volumeByTime.set(String(normalized.time), normalized.volume);
                volumes.update({
                    time: normalized.time,
                    value: normalized.volume,
                    color: normalized.close >= normalized.open ? currentVolumeColors.up : currentVolumeColors.down,
                });
                lastBar = normalized;
            }
        }

        function _applyVolumeColors() {
            if (!lastBarsData.length) return;
            const volumeData = lastBarsData.map((bar) => ({
                time: bar.time,
                value: bar.volume,
                color: bar.close >= bar.open ? currentVolumeColors.up : currentVolumeColors.down,
            }));
            volumes.setData(volumeData);
        }

        function getCurrentBarSpacing() {
            try {
                const options = chart.timeScale().options?.();
                const spacing = options?.barSpacing;
                if (Number.isFinite(spacing) && spacing > 0) return spacing;
            } catch (_e) {
                // noop
            }
            return DEFAULT_CHART_OPTIONS.timeScale.barSpacing || 8;
        }

        function setRightOffsetPx(px) {
            const spacing = getCurrentBarSpacing();
            if (!(spacing > 0)) return;
            const bars = Math.max(0, px / spacing);
            try {
                chart.timeScale().applyOptions({ rightOffset: bars });
            } catch (_e) {
                // noop
            }
        }

        function scrollToRealTimeWithOffset(px = 48) {
            setRightOffsetPx(px);
            try {
                if (typeof chart.timeScale().scrollToRealTime === "function") {
                    chart.timeScale().scrollToRealTime();
                } else {
                    chart.timeScale().scrollToPosition(0, false);
                }
            } catch (_e) {
                // noop
            }
        }

        function resetView() {
            try {
                resetManualPriceScale({ silent: true });
                chart.timeScale().fitContent();
                requestPriceScaleSync();
            } catch (_e) {
                // noop
            }
        }

        function resetViewAndFollow(px = 48) {
            resetView();
            scrollToRealTimeWithOffset(px);
        }

        function isAtEnd(thresholdBars = 1) {
            const pos = chart.timeScale().scrollPosition();
            if (!Number.isFinite(pos)) return false;
            return Math.abs(pos) <= thresholdBars;
        }

        function scrollToRealTime() {
            scrollToRealTimeWithOffset(0);
        }

        function setFollowRightOffsetPx(px = 48) {
            setRightOffsetPx(px);
        }

        function setViewTimeframe(tfSec) {
            const sec = Number(tfSec);
            if (Number.isFinite(sec) && sec > 0) {
                barTimeSpanSeconds = Math.max(1, Math.floor(sec));
            }
        }

        function resizeToContainer() {
            const rect = container.getBoundingClientRect();
            const width = Math.floor(rect.width);
            const height = Math.floor(rect.height);
            if (width > 0 && height > 0) {
                chart.resize(width, height);
            }
        }

        function getVisibleLogicalRange() {
            try {
                const range = chart.timeScale().getVisibleLogicalRange();
                if (!range) return null;
                const from = Number(range.from);
                const to = Number(range.to);
                if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
                return { from, to };
            } catch (_e) {
                return null;
            }
        }

        function setVisibleLogicalRange(range) {
            if (!range) return;
            const from = Number(range.from);
            const to = Number(range.to);
            if (!Number.isFinite(from) || !Number.isFinite(to)) return;
            try {
                chart.timeScale().setVisibleLogicalRange({ from, to });
            } catch (_e) {
                // noop
            }
        }

        function notifyVisibleRange() {
            const range = getVisibleLogicalRange();
            if (!range) return;
            visibleRangeHandlers.forEach((handler) => {
                try {
                    handler(range);
                } catch (_e) {
                    // noop
                }
            });
        }

        function scheduleVisibleRangeNotify() {
            if (visibleRangeRaf !== null) return;
            visibleRangeRaf = window.requestAnimationFrame(() => {
                visibleRangeRaf = null;
                notifyVisibleRange();
            });
        }

        function onVisibleLogicalRangeChange(handler) {
            if (typeof handler !== "function") return () => { };
            visibleRangeHandlers.add(handler);
            return () => visibleRangeHandlers.delete(handler);
        }

        function installVisibleRangeObserver() {
            const timeScale = chart.timeScale();
            if (!timeScale) return;
            if (typeof timeScale.subscribeVisibleLogicalRangeChange === "function") {
                const cb = () => scheduleVisibleRangeNotify();
                timeScale.subscribeVisibleLogicalRangeChange(cb);
                interactionCleanup.push(() => timeScale.unsubscribeVisibleLogicalRangeChange(cb));
                return;
            }
            if (typeof timeScale.subscribeVisibleTimeRangeChange === "function") {
                const cb = () => scheduleVisibleRangeNotify();
                timeScale.subscribeVisibleTimeRangeChange(cb);
                interactionCleanup.push(() => timeScale.unsubscribeVisibleTimeRangeChange(cb));
            }
        }

        function clearAll() {
            resetManualPriceScale({ silent: true });
            priceScaleState.lastAutoRange = null;
            candles.setData([]);
            barsSeries.setData([]);
            volumes.setData([]);
            volumeByTime.clear();
            lastBar = null;
        }

        function setCandleStyle(style) {
            const name = (style || "").toLowerCase();
            if (name === "bars-dark") {
                currentVolumeColors = {
                    up: "rgba(90, 96, 102, 0.5)",
                    down: "rgba(50, 55, 60, 0.5)",
                };
            } else if (name === "dark") {
                currentVolumeColors = {
                    up: "rgba(80, 86, 92, 0.4)",
                    down: "rgba(40, 45, 50, 0.4)",
                };
            } else if (name === "gray") {
                currentVolumeColors = {
                    up: "rgba(154, 160, 166, 0.45)",
                    down: "rgba(95, 99, 104, 0.45)",
                };
            } else if (name === "white") {
                currentVolumeColors = {
                    up: "rgba(226, 229, 233, 0.45)",
                    down: "rgba(47, 51, 56, 0.45)",
                };
            } else if (name === "hollow") {
                currentVolumeColors = {
                    up: "rgba(38, 166, 154, 0.28)",
                    down: "rgba(239, 83, 80, 0.28)",
                };
            } else {
                currentVolumeColors = {
                    up: VOLUME_UP_COLOR,
                    down: VOLUME_DOWN_COLOR,
                };
            }
            if (name === "bars" || name === "bars-dark") {
                currentCandleStyle = "bars";
                activeSeries = barsSeries;
                if (typeof candles.applyOptions === "function") {
                    candles.applyOptions({
                        visible: true,
                        upColor: "rgba(0,0,0,0)",
                        downColor: "rgba(0,0,0,0)",
                        borderUpColor: "rgba(0,0,0,0)",
                        borderDownColor: "rgba(0,0,0,0)",
                        wickUpColor: "rgba(0,0,0,0)",
                        wickDownColor: "rgba(0,0,0,0)",
                    });
                }
                if (typeof barsSeries.applyOptions === "function") {
                    if (name === "bars-dark") {
                        barsSeries.applyOptions({
                            visible: true,
                            upColor: "#c9ced6",
                            downColor: "#5a6068",
                            thinBars: true,
                            openVisible: true,
                        });
                    } else {
                        barsSeries.applyOptions({
                            visible: true,
                            upColor: "#26a69a",
                            downColor: "#ef5350",
                            thinBars: true,
                            openVisible: true,
                        });
                    }
                }
                _applyVolumeColors();
                return;
            }
            const preset = CANDLE_STYLES[name] || CANDLE_STYLES[CANDLE_STYLE_DEFAULT];
            if (typeof candles.applyOptions === "function") {
                candles.applyOptions({ ...preset, visible: true });
            }
            if (typeof barsSeries.applyOptions === "function") {
                barsSeries.applyOptions({ visible: false });
            }
            currentCandleStyle = name in CANDLE_STYLES ? name : CANDLE_STYLE_DEFAULT;
            activeSeries = candles;
            _applyVolumeColors();
        }

        function setTheme(mode) {
            try {
                if (mode === true || mode === "dark") {
                    chart.applyOptions(DARK_CHART_OPTIONS);
                } else if (mode === "dark-gray" || mode === "dark_gray") {
                    chart.applyOptions(DEFAULT_CHART_OPTIONS);
                } else {
                    chart.applyOptions(DEFAULT_CHART_OPTIONS);
                }
            } catch (_e) {
                // noop
            }
        }

        setupPriceScaleInteractions();
        installVisibleRangeObserver();
        resizeToContainer();

        return {
            setBars,
            updateLastBar,
            resetViewAndFollow,
            resizeToContainer,
            clearAll,
            setViewTimeframe,
            setTheme,
            setCandleStyle,
            isAtEnd,
            scrollToRealTime,
            scrollToRealTimeWithOffset,
            setFollowRightOffsetPx,
            getVisibleLogicalRange,
            setVisibleLogicalRange,
            onVisibleLogicalRangeChange,
        };
    }

    window.createChartController = createChartController;
})();
