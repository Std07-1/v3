(function () {
    const ChartAdapterLogic =
        typeof globalThis !== "undefined" && globalThis.ChartAdapterLogic
            ? globalThis.ChartAdapterLogic
            : null;

    const CANDLE_COLORS = {
        up: "#26a69a",
        down: "#ef5350",
        live: "#f6c343",
    };

    const VOLUME_WINDOW_SIZE = 200;
    const OPACITY_MIN = 0.25;
    const OPACITY_MAX = 1.0;
    const VOLUME_BAR_ALPHA = 0.55;
    const VOLUME_SCALE_QUANTILE = 0.98;

    // Mobile UX: робимо праву цінову шкалу компактнішою.
    // Зауваження: lightweight-charts має внутрішні відступи для axis labels, тому "впритул" =
    // мінімум по ширині контейнера (minimumWidth) + менший шрифт (якщо колись знадобиться).
    const RIGHT_PRICE_SCALE_MIN_WIDTH_DESKTOP_PX = 56;
    const RIGHT_PRICE_SCALE_MIN_WIDTH_MOBILE_V2_PX = 44;

    const DEFAULT_CHART_OPTIONS = {
        layout: {
            background: { color: "#131722" },
            textColor: "#d1d4dc",
        },
        grid: {
            vertLines: { color: "rgba(42, 46, 57, 0.7)" },
            horzLines: { color: "rgba(42, 46, 57, 0.7)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: "rgba(209, 212, 220, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: "#2a2e39",
            },
            horzLine: {
                color: "rgba(209, 212, 220, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: "#2a2e39",
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
            borderColor: "rgba(54, 58, 69, 0.9)",
            borderVisible: true,
            ticksVisible: true,
            autoScale: true,
            scaleMargins: {
                top: 0.12,
                bottom: 0.18,
            },
        },
        timeScale: {
            borderColor: "rgba(54, 58, 69, 0.9)",
            borderVisible: true,
            rightOffset: 0,
            barSpacing: 8,
            timeVisible: true,
            secondsVisible: false,
            fixLeftEdge: false,
            fixRightEdge: false,
            lockVisibleTimeRangeOnResize: false,
        },
    };

    const STRUCTURE_TRIANGLE = {
        widthBars: 6,
        minWidthSec: 180,
        heightRatio: 0.35,
        minHeight: 0.01,
        minHeightPct: 0.0006,
        colors: {
            bos: "#4ade80",
            choch: "#facc15",
        },
        maxEvents: 12,
        edgeWidth: 3,
        baseWidth: 2,
    };

    const OTE_STYLES = {
        LONG: {
            border: "rgba(34, 197, 94, 0.45)",
            arrow: "rgba(34, 197, 94, 0.65)",
            axisLabel: "rgba(34, 197, 94, 0.65)",
        },
        SHORT: {
            border: "rgba(248, 113, 113, 0.45)",
            arrow: "rgba(248, 113, 113, 0.65)",
            axisLabel: "rgba(248, 113, 113, 0.65)",
        },
    };

    function normalizeOteDirection(raw) {
        const s = String(raw || "").trim().toUpperCase();
        if (!s) {
            return "";
        }
        // Бекенд/контракти можуть віддавати як LONG/SHORT, так і BUY/SELL.
        // Для UI канонізуємо, щоб кольори були детерміновані.
        if (s === "SHORT" || s === "SELL" || s === "S" || s === "BEAR" || s === "BEARISH") {
            return "SHORT";
        }
        if (s === "LONG" || s === "BUY" || s === "B" || s === "BULL" || s === "BULLISH") {
            return "LONG";
        }
        if (s.includes("SHORT") || s.includes("SELL")) {
            return "SHORT";
        }
        if (s.includes("LONG") || s.includes("BUY")) {
            return "LONG";
        }
        return s;
    }

    function normalizeBar(bar) {
        if (!bar) {
            return null;
        }

        const toUnixSeconds = (value) => {
            if (value === null || value === undefined) {
                return null;
            }
            const direct = Number(value);
            if (!Number.isFinite(direct)) {
                return null;
            }

            const abs = Math.abs(direct);
            // Евристика: FX/крипто час може приходити як sec/ms/us.
            // sec ~ 1e9, ms ~ 1e12, us ~ 1e15.
            if (abs > 1e14) {
                return direct / 1e6;
            }
            if (abs > 1e12) {
                return direct / 1e3;
            }
            return direct;
        };

        const timeRaw =
            bar.time ??
            bar.open_time ??
            bar.openTime ??
            bar.close_time ??
            bar.closeTime ??
            bar.start_ts ??
            bar.start_time ??
            bar.ts ??
            bar.timestamp ??
            bar.end_ts ??
            bar.end_time;
        const timeSec = toUnixSeconds(timeRaw);
        if (!Number.isFinite(timeSec)) {
            return null;
        }

        const open = Number(bar.open);
        const high = Number(bar.high);
        const low = Number(bar.low);
        const close = Number(bar.close);
        if (!Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) {
            return null;
        }

        return {
            time: Math.floor(timeSec),
            open,
            high,
            low,
            close,
        };
    }

    function normalizeVolume(bar) {
        if (!bar) {
            return 0;
        }
        const value = Number(bar.volume);
        if (!Number.isFinite(value) || value <= 0) {
            return 0;
        }
        return value;
    }

    function clamp(value, min, max) {
        if (ChartAdapterLogic && typeof ChartAdapterLogic.clamp === "function") {
            return ChartAdapterLogic.clamp(value, min, max);
        }
        if (!Number.isFinite(value)) {
            return min;
        }
        return Math.min(max, Math.max(min, value));
    }

    function hexToRgba(hex, alpha) {
        if (typeof hex !== "string" || !hex.startsWith("#") || hex.length !== 7) {
            return hex;
        }
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        if (![r, g, b].every(Number.isFinite)) {
            return hex;
        }
        const a = clamp(alpha, 0, 1);
        return `rgba(${r}, ${g}, ${b}, ${a})`;
    }

    function computeRecentMaxVolume(volumes) {
        if (!Array.isArray(volumes) || volumes.length === 0) {
            return 0;
        }
        const tail = volumes.slice(Math.max(0, volumes.length - VOLUME_WINDOW_SIZE));
        let maxValue = 0;
        for (const v of tail) {
            const num = Number(v);
            if (Number.isFinite(num) && num > maxValue) {
                maxValue = num;
            }
        }
        return maxValue;
    }

    function computeVolumeScaleMax(volumes, quantile = VOLUME_SCALE_QUANTILE) {
        if (!Array.isArray(volumes) || volumes.length === 0) {
            return 1;
        }
        const cleaned = volumes
            .map((v) => Number(v))
            .filter((v) => Number.isFinite(v) && v > 0)
            .sort((a, b) => a - b);
        if (!cleaned.length) {
            return 1;
        }

        // Кеп по квантилю, щоб один спайк не "вбивав" масштаб для всіх інших брусків.
        const q = clamp(Number(quantile), 0.5, 1.0);
        const idx = Math.min(cleaned.length - 1, Math.floor((cleaned.length - 1) * q));
        const qValue = cleaned[idx] ?? 1;
        const maxAll = cleaned[cleaned.length - 1] ?? qValue;

        // Якщо даних мало — краще показати повний max, ніж різко обрізати.
        const useMaxAll = cleaned.length < 40;
        const chosen = useMaxAll ? maxAll : qValue;
        return Math.max(1, chosen);
    }

    function volumeToOpacity(volume, recentMax) {
        if (!Number.isFinite(recentMax) || recentMax <= 0) {
            return OPACITY_MAX;
        }
        const norm = clamp(Number(volume) / recentMax, 0, 1);
        return OPACITY_MIN + norm * (OPACITY_MAX - OPACITY_MIN);
    }

    function createChartController(container) {
        if (!container) {
            throw new Error("chart_adapter: контейнер не передано");
        }
        if (typeof LightweightCharts === "undefined") {
            throw new Error("chart_adapter: lightweight-charts не доступний");
        }

        const chart = LightweightCharts.createChart(container, DEFAULT_CHART_OPTIONS);
        const tooltipEl =
            typeof document !== "undefined"
                ? container
                    ?.closest(".chart-overlay-shell")
                    ?.querySelector("#chart-hover-tooltip")
                : null;

        const poiLabelLayer = (() => {
            if (typeof document === "undefined") return null;
            const shell = container?.closest(".chart-overlay-shell");
            if (!shell) return null;
            const anchor = shell.querySelector("#chart-hover-tooltip") || null;
            const existing = shell.querySelector("#chart-poi-label-layer");
            if (existing) {
                // Важливо: шар має бути НАД графіком, але ПІД tooltip.
                if (anchor) {
                    shell.insertBefore(existing, anchor);
                }
                return existing;
            }
            const created = document.createElement("div");
            created.id = "chart-poi-label-layer";
            created.className = "chart-poi-label-layer";
            created.setAttribute("aria-hidden", "true");
            if (anchor) {
                shell.insertBefore(created, anchor);
            } else {
                shell.appendChild(created);
            }
            return created;
        })();

        const poolsLabelLayer = (() => {
            if (typeof document === "undefined") return null;
            const shell = container?.closest(".chart-overlay-shell");
            if (!shell) return null;
            const anchor = shell.querySelector("#chart-hover-tooltip") || null;
            const existing = shell.querySelector("#chart-pools-label-layer");
            if (existing) {
                // Важливо: шар має бути НАД графіком, але ПІД tooltip.
                if (anchor) {
                    shell.insertBefore(existing, anchor);
                }
                return existing;
            }
            const created = document.createElement("div");
            created.id = "chart-pools-label-layer";
            // Використовуємо той самий клас, що й POI labels (без нових CSS токенів).
            created.className = "chart-poi-label-layer";
            created.setAttribute("aria-hidden", "true");
            if (anchor) {
                shell.insertBefore(created, anchor);
            } else {
                shell.appendChild(created);
            }
            return created;
        })();

        function readBoolParam(name) {
            try {
                const truthy = new Set(["1", "true", "yes", "on"]);
                const falsy = new Set(["0", "false", "no", "off"]);

                const readFrom = (raw) => {
                    const s = String(raw || "").trim().toLowerCase();
                    if (!s) return null;
                    if (truthy.has(s)) return true;
                    if (falsy.has(s)) return false;
                    return null;
                };

                const searchParams = new URLSearchParams(window.location.search || "");
                const fromSearch = readFrom(searchParams.get(name));
                if (fromSearch !== null) return fromSearch;

                // Дозволяємо параметри також у hash-роутингу: /#/view?zone_labels=1
                const hash = String(window.location.hash || "");
                const idx = hash.indexOf("?");
                if (idx >= 0) {
                    const hashQuery = hash.slice(idx + 1);
                    const hashParams = new URLSearchParams(hashQuery);
                    const fromHash = readFrom(hashParams.get(name));
                    if (fromHash !== null) return fromHash;
                }

                return null;
            } catch (_e) {
                return null;
            }
        }

        // UX: трейдер має бачити POI підпис у місці формування.
        // Дефолт: увімкнено. Override: ?zone_labels=0
        const zoneLabelsParam = readBoolParam("zone_labels");
        const ZONE_LABELS_ENABLED = zoneLabelsParam !== false;

        // Тестові хуки (E2E): дефолт вимкнено. Override: ?test_hooks=1
        // Важливо: не впливає на UX/логіку, лише додає read-only debug API.
        const testHooksParam = readBoolParam("test_hooks");
        const TEST_HOOKS_ENABLED = testHooksParam === true;

        // Діагностика autoscale (self-check): дефолт вимкнено. Override: ?autoscale_selfcheck=1
        // Важливо: це лише логування/перевірки, без зміни семантики рендера.
        const autoscaleSelfcheckParam = readBoolParam("autoscale_selfcheck");
        const AUTOSCALE_SELFCHECK_ENABLED = autoscaleSelfcheckParam === true;

        // Debug/fallback: старі marker-кружки з підписом (не трейдерський режим).
        // Дефолт: вимкнено. Override: ?zone_label_markers=1
        const zoneLabelMarkersParam = readBoolParam("zone_label_markers");
        const ZONE_LABEL_MARKERS_ENABLED = zoneLabelMarkersParam === true;

        const stateRank = (state) => {
            const s = String(state || "").toUpperCase();
            if (s === "FRESH" || s === "NEW") return 3;
            if (s === "TOUCHED" || s === "TAPPED") return 2;
            if (s === "MITIGATED" || s === "FILLED") return 1;
            return 0;
        };

        const normalizePoiType = (raw) => {
            const s = String(raw || "").toUpperCase();
            if (!s) return "ZONE";
            if (s.includes("FVG") || s.includes("IMBALANCE")) return "FVG";
            if (s.includes("BREAKER")) return "BREAKER";
            if ((s.includes("ORDER") && s.includes("BLOCK")) || s === "OB" || s.includes(" OB")) return "OB";
            return s.replace(/\s+/g, " ").trim();
        };

        const roleShort = (z) => {
            const r = String(z?.role || "").toUpperCase();
            if (r === "PRIMARY" || r === "P") return "P";
            if (r === "COUNTERTREND" || r === "C") return "C";
            if (r === "NEUTRAL" || r === "N") return "N";
            return "?";
        };

        const zoneLabelText = (z) => {
            const tf = z?.timeframe ? String(z.timeframe) : "";
            const t = normalizePoiType(z?.poi_type || z?.type || z?.label || "ZONE");
            const r = roleShort(z);
            return `${tf ? tf + " " : ""}${t} ${r}`.trim();
        };

        let poiLabelsRaf = null;
        let poiDomLabelsByKey = new Map();
        let poiDomLabelsModel = [];

        let poolsLabelsRaf = null;
        let poolsDomLabelsById = new Map();
        let poolsDomLabelsModel = [];

        const clearPoiDomLabels = () => {
            if (!poiLabelLayer) return;
            poiDomLabelsByKey = new Map();
            poiDomLabelsModel = [];
            poiLabelLayer.textContent = "";
        };

        const clearPoolsDomLabels = () => {
            if (!poolsLabelLayer) return;
            poolsDomLabelsById = new Map();
            poolsDomLabelsModel = [];
            poolsLabelLayer.textContent = "";
        };

        const poolLabelText = (p) => {
            // P4.9: UI render-only — текст чіпа приходить як SSOT (PoolChipV1.label).
            const text = String(p?.label || "").trim();
            return text || "POOL";
        };

        const syncPoolsDomLabelsModel = () => {
            if (!poolsLabelLayer) return;

            const rows = Array.isArray(poolsDomLabelsModel) ? poolsDomLabelsModel : [];
            const nextKeys = new Set();
            const nextModel = [];

            for (const item of rows) {
                const id = String(item?.id || "").trim();
                if (!id) continue;
                nextKeys.add(id);
                nextModel.push(item);
            }

            // Видаляємо зайві
            for (const [id, el] of poolsDomLabelsById.entries()) {
                if (nextKeys.has(id)) continue;
                try {
                    el.remove();
                } catch (_e) {
                    // noop
                }
                poolsDomLabelsById.delete(id);
            }

            // Додаємо відсутні
            for (const item of nextModel) {
                const id = String(item?.id || "").trim();
                if (!id) continue;
                if (poolsDomLabelsById.has(id)) continue;
                const el = document.createElement("div");
                // Використовуємо існуючий style (poi-label) як “FVG-подібний” бейдж.
                el.className = "poi-label";
                el.textContent = poolLabelText(item);
                const chip = item?.chipStyle;
                if (chip && typeof chip === "object") {
                    if (typeof chip.bgRgba === "string" && chip.bgRgba) {
                        el.style.backgroundColor = chip.bgRgba;
                    }
                    if (typeof chip.borderRgba === "string" && chip.borderRgba) {
                        el.style.borderColor = chip.borderRgba;
                    }
                    if (typeof chip.textColor === "string" && chip.textColor) {
                        el.style.color = chip.textColor;
                    }
                }
                poolsLabelLayer.appendChild(el);
                poolsDomLabelsById.set(id, el);
            }

            // Оновлюємо текст/стилі (якщо змінились tf/kind/статус).
            for (const item of nextModel) {
                const id = String(item?.id || "").trim();
                if (!id) continue;
                const el = poolsDomLabelsById.get(id);
                if (!el) continue;
                const nextText = poolLabelText(item);
                if (el.textContent !== nextText) {
                    el.textContent = nextText;
                }
                const chip = item?.chipStyle;
                if (chip && typeof chip === "object") {
                    if (typeof chip.bgRgba === "string" && chip.bgRgba) {
                        el.style.backgroundColor = chip.bgRgba;
                    }
                    if (typeof chip.borderRgba === "string" && chip.borderRgba) {
                        el.style.borderColor = chip.borderRgba;
                    }
                    if (typeof chip.textColor === "string" && chip.textColor) {
                        el.style.color = chip.textColor;
                    }
                }
            }
        };

        const layoutPoolsDomLabels = () => {
            if (!poolsLabelLayer) return;

            const height = container?.clientHeight || 0;
            if (!height) return;

            const shell = container?.closest(".chart-overlay-shell") || poolsLabelLayer.parentElement;
            let offsetX = 0;
            let offsetY = 0;
            try {
                if (shell && typeof shell.getBoundingClientRect === "function") {
                    const shellRect = shell.getBoundingClientRect();
                    const containerRect = container.getBoundingClientRect();
                    offsetX = containerRect.left - shellRect.left;
                    offsetY = containerRect.top - shellRect.top;
                }
            } catch (_e) {
                // noop
            }

            const tfRank = (tfRaw) => {
                const tf = String(tfRaw || "").trim().toLowerCase();
                // Для лейблів (читабельність): HTF вище, 5m нижче (шум).
                if (tf === "4h") return 30;
                if (tf === "1h") return 20;
                if (tf === "5m") return 10;
                return 0;
            };

            // P4.4: детермінований packer. НЕ міняємо набір лейблів, лише їх позицію.
            // Порядок: TF-priority -> price(desc) -> id(asc).
            // P4.5: viewport gate - ховаємо лише OFFSCREEN (поза price viewport), а не через колізії.
            const VIEWPORT_MARGIN_PX = 12;
            const LABEL_Y_NUDGE_PX = 6;
            const items = [];
            for (const item of poolsDomLabelsModel) {
                if (!item || typeof item !== "object") continue;
                const id = String(item.id || "").trim();
                if (!id) continue;
                // P4.9: SSOT-чіпи дають anchor_price напряму.
                const anchorPrice = Number(item.anchor_price);
                if (!Number.isFinite(anchorPrice)) continue;
                const price = anchorPrice;
                const y = candles.priceToCoordinate(anchorPrice);
                if (!Number.isFinite(y)) continue;
                const yLocal = Number(y) - LABEL_Y_NUDGE_PX;

                // Viewport gate: якщо поза екраном - не показуємо і не "притискаємо" до краю.
                if (yLocal < -VIEWPORT_MARGIN_PX || yLocal > height + VIEWPORT_MARGIN_PX) {
                    continue;
                }

                items.push({
                    id,
                    y: yLocal,
                    price,
                    // Для детермінізму пакера: пріоритет приходить як SSOT.
                    priority: Number(item.priority) || 0,
                    tf: String(item.tf_mask || ""),
                });
            }

            items.sort((a, b) => {
                const pr = Number(b.priority) - Number(a.priority);
                if (pr) return pr;
                const r = tfRank(b.tf) - tfRank(a.tf);
                if (r) return r;
                const p = Number(b.price) - Number(a.price);
                if (p) return p;
                return String(a.id).localeCompare(String(b.id));
            });

            const MIN_GAP_PX = 16;
            // Спочатку ховаємо все; далі вмикаємо лише видимі (P4.5).
            for (const el of poolsDomLabelsById.values()) {
                if (!el) continue;
                el.style.display = "none";
            }

            let lastPlacedY = null;
            for (const it of items) {
                const el = poolsDomLabelsById.get(it.id);
                if (!el) continue;
                // Працюємо в локальних координатах контейнера, без "edge glue".
                let y = clamp(Number(it.y), 0, Math.max(0, height));

                if (lastPlacedY !== null && y < lastPlacedY + MIN_GAP_PX) {
                    y = lastPlacedY + MIN_GAP_PX;
                }

                // Якщо packer виштовхнув за межу - ховаємо (це layout, не selection).
                if (y > height) {
                    el.style.display = "none";
                    continue;
                }

                el.style.display = "";
                el.style.top = `${offsetY + y}px`;
                el.style.left = `${offsetX}px`;
                lastPlacedY = y;
            }
        };

        const schedulePoolsDomLabels = () => {
            if (!poolsLabelLayer) return;
            if (poolsLabelsRaf) return;
            poolsLabelsRaf = requestAnimationFrame(() => {
                poolsLabelsRaf = null;
                syncPoolsDomLabelsModel();
                layoutPoolsDomLabels();
            });
        };

        const stablePoiClusterKey = (cluster, rep) => {
            const zid = rep?.zone_id;
            if (zid !== null && zid !== undefined && String(zid).trim() !== "") {
                return `id:${String(zid)}`;
            }
            const tf = rep?.timeframe ? String(rep.timeframe) : "";
            const t = normalizePoiType(rep?.poi_type || rep?.type || rep?.label || "ZONE");
            const r = roleShort(rep);
            const fromRaw = Number(cluster?.start_time ?? rep?.origin_time);
            const from = Number.isFinite(fromRaw) ? Math.floor(fromRaw) : 0;
            const min = Number(rep?.min ?? rep?.price_min);
            const max = Number(rep?.max ?? rep?.price_max);
            const minS = Number.isFinite(min) ? min.toFixed(6) : "-";
            const maxS = Number.isFinite(max) ? max.toFixed(6) : "-";
            return `anon:${tf}|${t}|${r}|${from}|${minS}|${maxS}`;
        };

        const syncPoiDomLabelsModel = () => {
            if (!poiLabelLayer || !ZONE_LABELS_ENABLED) return;

            const clusters = Array.isArray(lastZoneClusters) ? lastZoneClusters : [];
            const nextModel = [];
            const nextKeys = new Set();

            for (const cluster of clusters) {
                const rep = cluster?.rep;
                if (!rep) continue;
                const key = stablePoiClusterKey(cluster, rep);
                nextKeys.add(key);
                nextModel.push({ key, cluster, rep });
            }

            // Видаляємо зайві.
            for (const [key, el] of poiDomLabelsByKey.entries()) {
                if (nextKeys.has(key)) continue;
                try {
                    el.remove();
                } catch (_e) {
                    // noop
                }
                poiDomLabelsByKey.delete(key);
            }

            // Додаємо нові.
            for (const m of nextModel) {
                if (poiDomLabelsByKey.has(m.key)) continue;
                const label = document.createElement("div");
                label.className = "poi-label";
                label.textContent = zoneLabelText(m.rep);
                poiLabelLayer.appendChild(label);
                poiDomLabelsByKey.set(m.key, label);
            }

            // Оновлюємо текст (якщо змінився type/tf/role).
            for (const m of nextModel) {
                const el = poiDomLabelsByKey.get(m.key);
                if (!el) continue;
                const nextText = zoneLabelText(m.rep);
                if (el.textContent !== nextText) {
                    el.textContent = nextText;
                }
            }

            poiDomLabelsModel = nextModel;
        };

        const updatePoiDomLabelPositions = () => {
            if (!poiLabelLayer || !ZONE_LABELS_ENABLED) return;
            const shell = container?.closest(".chart-overlay-shell") || poiLabelLayer.parentElement;
            if (!shell || typeof shell.getBoundingClientRect !== "function") return;

            const shellRect = shell.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            const offsetX = containerRect.left - shellRect.left;
            const offsetY = containerRect.top - shellRect.top;

            const ts = chart?.timeScale?.();
            if (!ts || typeof ts.timeToCoordinate !== "function") return;

            for (const m of poiDomLabelsModel) {
                const rep = m?.rep;
                const cluster = m?.cluster;
                const el = poiDomLabelsByKey.get(m.key);
                if (!rep || !el) continue;

                const minPrice = Number(rep?.min ?? rep?.price_min);
                const maxPrice = Number(rep?.max ?? rep?.price_max);
                if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) {
                    el.style.display = "none";
                    continue;
                }
                const midPrice = (Math.min(minPrice, maxPrice) + Math.max(minPrice, maxPrice)) * 0.5;

                const fromRaw = Number(cluster?.start_time ?? rep?.origin_time);
                const from = Number.isFinite(fromRaw) ? Math.floor(fromRaw) : null;
                if (!Number.isFinite(from)) {
                    el.style.display = "none";
                    continue;
                }

                const x = ts.timeToCoordinate(from);
                const y = candles.priceToCoordinate(midPrice);
                if (!Number.isFinite(x) || !Number.isFinite(y)) {
                    el.style.display = "none";
                    continue;
                }

                el.style.display = "";
                el.style.left = `${offsetX + x}px`;
                el.style.top = `${offsetY + y}px`;
            }
        };

        const renderPoiDomLabels = () => {
            if (!poiLabelLayer || !ZONE_LABELS_ENABLED) return;
            // ВАЖЛИВО: не rebuild на кожен move (це викликало лаги/«розмазування»),
            // а лише sync по даним + позиціонування.
            syncPoiDomLabelsModel();
            updatePoiDomLabelPositions();
        };

        const schedulePoiDomLabels = () => {
            if (!poiLabelLayer || !ZONE_LABELS_ENABLED) return;
            if (poiLabelsRaf !== null) return;
            poiLabelsRaf = window.requestAnimationFrame(() => {
                poiLabelsRaf = null;
                renderPoiDomLabels();
            });
        };

        // Оновлюємо DOM-лейбли при скролі/зумі таймскейлу, якщо API доступний.
        try {
            const ts = chart.timeScale();
            if (ts && typeof ts.subscribeVisibleLogicalRangeChange === "function") {
                const onRange = () => {
                    schedulePoiDomLabels();
                    schedulePoolsDomLabels();
                };
                ts.subscribeVisibleLogicalRangeChange(onRange);
                interactionCleanup.push(() => {
                    try {
                        ts.unsubscribeVisibleLogicalRangeChange(onRange);
                    } catch (_e) {
                        // noop
                    }
                });
            }
            if (ts && typeof ts.subscribeVisibleTimeRangeChange === "function") {
                const onTime = () => {
                    schedulePoiDomLabels();
                    schedulePoolsDomLabels();
                };
                ts.subscribeVisibleTimeRangeChange(onTime);
                interactionCleanup.push(() => {
                    try {
                        ts.unsubscribeVisibleTimeRangeChange(onTime);
                    } catch (_e) {
                        // noop
                    }
                });
            }
        } catch (_e) {
            // noop
        }

        // Fallback: навіть якщо в lightweight-charts немає subscribeVisible*,
        // оновлюємо позиції DOM-лейблів під час drag/wheel.
        // ВАЖЛИВО: викликаємо лише schedule (RAF), без перебудови DOM на кожен event.
        try {
            let pointerDown = false;
            const hasPointer = typeof window !== "undefined" && typeof window.PointerEvent !== "undefined";

            const onDown = () => {
                pointerDown = true;
                schedulePoiDomLabels();
                schedulePoolsDomLabels();
            };
            const onUp = () => {
                if (!pointerDown) return;
                pointerDown = false;
                schedulePoiDomLabels();
                schedulePoolsDomLabels();
            };
            const onMove = () => {
                if (!pointerDown) return;
                schedulePoiDomLabels();
                schedulePoolsDomLabels();
            };
            const onWheel = () => {
                schedulePoiDomLabels();
                schedulePoolsDomLabels();
            };

            if (hasPointer) {
                container.addEventListener("pointerdown", onDown, { passive: true, capture: true });
                container.addEventListener("pointermove", onMove, { passive: true, capture: true });
                window.addEventListener("pointerup", onUp, { passive: true, capture: true });
                window.addEventListener("pointercancel", onUp, { passive: true, capture: true });
            } else {
                container.addEventListener("mousedown", onDown, { passive: true, capture: true });
                container.addEventListener("mousemove", onMove, { passive: true, capture: true });
                window.addEventListener("mouseup", onUp, { passive: true, capture: true });
            }

            // wheel у capture-режимі вже обробляється price-axis. Тут — best-effort.
            container.addEventListener("wheel", onWheel, { passive: true });

            interactionCleanup.push(() => {
                try {
                    if (hasPointer) {
                        container.removeEventListener("pointerdown", onDown, { passive: true, capture: true });
                        container.removeEventListener("pointermove", onMove, { passive: true, capture: true });
                        window.removeEventListener("pointerup", onUp, { passive: true, capture: true });
                        window.removeEventListener("pointercancel", onUp, { passive: true, capture: true });
                    } else {
                        container.removeEventListener("mousedown", onDown, { passive: true, capture: true });
                        container.removeEventListener("mousemove", onMove, { passive: true, capture: true });
                        window.removeEventListener("mouseup", onUp, { passive: true, capture: true });
                    }
                    container.removeEventListener("wheel", onWheel, { passive: true });
                } catch (_e) {
                    // noop
                }
            });
        } catch (_e) {
            // noop
        }

        const sessionsScaleId = "sessions";
        // UX: користувачі часто сприймають full-height session bands як “заливку на весь чарт”.
        // Залишаємо SSOT session range boxes + ribbon, а background bands вимикаємо.
        const ENABLE_SESSION_BACKGROUND_BANDS = false;
        const sessionSeries = {
            enabled: true,
            // Сесії малюємо як «суцільні блоки» у власній шкалі 0..1.
            // Критично: кожен блок — окрема BaselineSeries з 2 точками (start/end),
            // щоб не було «підкошених» країв (діагоналей) і щоб не з'єднувались різні дні.
            bands: {
                asia: [],
                london: [],
                newYork: [],
            },
        };

        // Sessions SSOT:
        // - єдине джерело: epoch-вікна з viewer_state.meta.session_windows_utc (list).
        // - НІЯКИХ fallback-розкладів/дефолт-мап у UI.
        let sessionWindowsUtcEpoch = null; // {asia/london/newYork: [{from,to}, ...]}
        let sessionsRibbonOverlay = null;

        const SESSION_BAND_POOL_SIZE = 24;
        const SESSION_BAND_VALUE = 1;

        // Останній список зон (active/poi), щоб тултіп міг показувати семантику.
        // Важливо: має бути в спільному scope з setZones() і setupHoverTooltip().
        let lastZones = [];
        let lastZoneClusters = [];
        let zoneLabelById = new Map();
        let zoneGeometryById = new Map();
        // Pools-V1 (P4): legacy pools вимкнені. SSOT — pools_selected_v1.
        let lastOteZones = [];

        // User-control: ліміт зон для micro-view (ультра-дрібного view), щоб не засмічувати екран.
        // - near2: як зараз (top-2 вище + top-2 нижче + зони, в яких ціна всередині)
        // - near1: 1 вище + 1 нижче + inside
        // - all: без ліміту (лише інші ворота/кластеризація)
        let zoneLimitMode = "near2";

        // View-control: обраний таймфрейм (із UI). Використовуємо як "джерело правди"
        // для Gate3 (антишум для micro-view), бо у реплеї/дірках датасету barTimeSpanSeconds може
        // з'їхати і вимкнути micro-view логіку.
        let viewTimeframeSecOverride = null;

        // Pools-V1 (P4): SSOT render-only. Ключ = stable id.
        // value = { topSeg, botSeg, labelLine, last: { id, tf, top, bot, status, label, kind, side } }
        let poolsV1ById = new Map();
        let lastPoolsV1WarnMs = 0;
        let lastPoolsV1AutoscaleWarn = { key: "", ms: 0 };
        let poolsV1AutoscaleSelfcheckPending = false;
        let lastLegacyPoolsWarnMs = 0;
        let lastLevelsSelectedV1RenderedLog = { key: "", ms: 0 };
        let lastLevelsSelectedV1CapWarn = { key: "", ms: 0 };

        function debugLevelsSelectedV1Rendered(lines, bands, tfRaw) {
            const tf = String(tfRaw || "").toLowerCase();
            if (!tf) return;

            const l = Math.max(0, Number(lines) || 0);
            const b = Math.max(0, Number(bands) || 0);
            if (l === 0 && b === 0) {
                return;
            }

            const now = Date.now();
            const key = `${tf}|${l}|${b}`;
            if (key === lastLevelsSelectedV1RenderedLog.key && now - lastLevelsSelectedV1RenderedLog.ms < 2000) {
                return;
            }

            lastLevelsSelectedV1RenderedLog = { key, ms: now };
            try {
                if (typeof window !== "undefined" && typeof window.uiDebugLogRateLimited === "function") {
                    window.uiDebugLogRateLimited(
                        `levels_selected_v1_rendered:${tf}`,
                        `levels_selected_v1_rendered: lines=${l} bands=${b} tf=${tf}`,
                    );
                }
            } catch (_e) {
                // ignore
            }
        }

        function poolsV1TfRank(tfRaw) {
            const tf = String(tfRaw || "").toLowerCase();
            // Пріоритет у tooltip (не selection): 5m > 1h > 4h.
            if (tf === "5m") return 30;
            if (tf === "1h") return 20;
            if (tf === "4h") return 10;
            return 0;
        }

        function poolsV1Style(tfRaw, statusRaw) {
            const tf = String(tfRaw || "").toLowerCase();
            const status = String(statusRaw || "").toLowerCase();

            // V1 граматика: колір = тип "POOL" (єдиний), TF = opacity/width.
            // EQH/EQL кодуємо позицією чіпа (top/bot) і символом, НЕ кольором.
            const baseColorHex = "#3b82f6";

            const baseEdgeAlpha = tf === "5m" ? 0.55 : tf === "1h" ? 0.4 : 0.28;
            const edgeAlpha = status === "swept" ? baseEdgeAlpha * 0.65 : baseEdgeAlpha;
            const edgeWidth = tf === "5m" ? 2 : tf === "1h" ? 2 : 1;
            const edgeStyle = LightweightCharts.LineStyle.Dotted;

            const fillAlphaBase = tf === "5m" ? 0.05 : tf === "1h" ? 0.065 : 0.08;
            const fillAlpha = status === "swept" ? fillAlphaBase * 0.7 : fillAlphaBase;
            const fillRgba = hexToRgba(baseColorHex, fillAlpha);

            const edgeColor = hexToRgba(baseColorHex, edgeAlpha);
            return {
                edge: {
                    color: edgeColor,
                    lineWidth: edgeWidth,
                    lineStyle: edgeStyle,
                },
                fill: {
                    fillRgba,
                },
                chip: {
                    textColor: "",
                    bgRgba: hexToRgba(baseColorHex, 0.16),
                    borderRgba: hexToRgba(baseColorHex, Math.min(0.75, edgeAlpha + 0.15)),
                },
            };
        }

        function pickPoolsV1SegmentRange(chartTimeRangeIn, lastBarIn, lastLiveBarIn) {
            const span = Math.max(1, Number(barTimeSpanSeconds) || 60);
            const toRaw = Number(chartTimeRangeIn?.max ?? lastBarIn?.time ?? lastLiveBarIn?.time);
            if (!Number.isFinite(toRaw)) return null;
            const to = Math.floor(toRaw);
            const from = Math.floor(to - span * 140);
            if (!(to > from)) return null;
            return { from, to };
        }

        function warnPoolsV1Input(issue, count) {
            const now = Date.now();
            if (now - lastPoolsV1WarnMs > 5000) {
                lastPoolsV1WarnMs = now;
                console.warn(`chart_adapter: pools_selected_v1: ${issue} (n=${count})`);
            }
        }

        function warnPoolsV1AutoscaleSelfcheck(payload) {
            const now = Date.now();
            const key = String(payload?.key || "");
            if (key && key === lastPoolsV1AutoscaleWarn.key && now - lastPoolsV1AutoscaleWarn.ms < 8000) {
                return;
            }
            if (now - lastPoolsV1AutoscaleWarn.ms < 8000) {
                return;
            }
            lastPoolsV1AutoscaleWarn = { key, ms: now };
            console.warn("chart_adapter: autoscale self-check: pools_v1 вплинув на lastAutoRange (неочікувано)", payload);
        }

        // Підтримує TF-рядки формату N{sec|min|h|d}, зокрема 15m та 1d.
        const parseTfSecondsSimple = (tfRaw) => {
            const s = String(tfRaw || "").trim().toLowerCase();
            if (!s) return null;
            const m = s.match(/^(\d+)(s|sec|m|min|h|d)$/i);
            if (!m) return null;
            const n = Number(m[1]);
            if (!Number.isFinite(n) || n <= 0) return null;
            const unit = String(m[2]).toLowerCase();
            if (unit === "s" || unit === "sec") return Math.floor(n);
            if (unit === "m" || unit === "min") return Math.floor(n * 60);
            if (unit === "h") return Math.floor(n * 3600);
            if (unit === "d") return Math.floor(n * 86400);
            return null;
        };

        // Останні execution-події (Stage5), індексовані по часу бару.
        // Тримамо в загальному scope, щоб тултіп міг показувати деталі при hover по стрілці.
        let lastExecutionEvents = [];
        let executionEventsByTime = new Map();
        const MAX_EXECUTION_MARKERS = 120;

        function createSessionBand(fillRgba) {
            const band = chart.addBaselineSeries({
                priceScaleId: sessionsScaleId,
                baseValue: { type: "price", price: 0 },
                autoscaleInfoProvider: () => ({
                    priceRange: {
                        minValue: 0,
                        maxValue: 1,
                    },
                }),
                baseLineVisible: false,
                baseLineWidth: 0,
                topFillColor1: fillRgba,
                topFillColor2: fillRgba,
                bottomFillColor1: fillRgba,
                bottomFillColor2: fillRgba,
                lineWidth: 0,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            band.applyOptions({ visible: false });
            band.setData([]);
            return band;
        }

        for (let i = 0; i < SESSION_BAND_POOL_SIZE; i += 1) {
            sessionSeries.bands.asia.push(createSessionBand("rgba(38, 166, 154, 0.06)"));
            sessionSeries.bands.london.push(createSessionBand("rgba(246, 195, 67, 0.055)"));
            sessionSeries.bands.newYork.push(createSessionBand("rgba(239, 83, 80, 0.055)"));
        }

        // “A по даних”: session range boxes (high/low) на price-scale.
        // Без ліній/лейблів — лише заливка між low↔high.
        const SESSION_RANGE_BOX_POOL_SIZE = 6;
        const sessionRangeBoxes = [];
        const sessionRangeBoxRanges = [];

        function createSessionRangeBoxSeries() {
            const series = chart.addBaselineSeries({
                baseValue: { type: "price", price: 0 },
                baseLineVisible: false,
                baseLineWidth: 0,
                lineVisible: false,
                lineColor: "rgba(0, 0, 0, 0)",
                topLineColor: "rgba(0, 0, 0, 0)",
                bottomLineColor: "rgba(0, 0, 0, 0)",
                topFillColor1: "rgba(209, 212, 220, 0.08)",
                topFillColor2: "rgba(209, 212, 220, 0.04)",
                bottomFillColor1: "rgba(209, 212, 220, 0.08)",
                bottomFillColor2: "rgba(209, 212, 220, 0.04)",
                lineWidth: 0,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            series.applyOptions({ visible: false });
            series.setData([]);
            return series;
        }

        for (let i = 0; i < SESSION_RANGE_BOX_POOL_SIZE; i += 1) {
            sessionRangeBoxes.push(createSessionRangeBoxSeries());
            sessionRangeBoxRanges.push(null);
        }

        let lastSessionRangeRequest = null;
        let lastSessionRangeBoxesRequest = null;
        chart.priceScale(sessionsScaleId).applyOptions({
            scaleMargins: {
                top: 0.0,
                bottom: 0.0,
            },
            borderVisible: false,
            ticksVisible: false,
        });

        const candles = chart.addCandlestickSeries({
            upColor: CANDLE_COLORS.up,
            wickUpColor: CANDLE_COLORS.up,
            downColor: CANDLE_COLORS.down,
            wickDownColor: CANDLE_COLORS.down,
            borderVisible: false,
            // Вимикаємо дефолтний «лейбл поточної ціни» серії,
            // щоб керувати ним вручну (менший текст + динамічний колір up/down).
            priceLineVisible: false,
            lastValueVisible: false,
        });
        const liveCandles = chart.addCandlestickSeries({
            upColor: "rgba(246, 195, 67, 0.18)",
            wickUpColor: CANDLE_COLORS.live,
            downColor: "rgba(246, 195, 67, 0.18)",
            wickDownColor: CANDLE_COLORS.live,
            borderVisible: true,
            borderUpColor: CANDLE_COLORS.live,
            borderDownColor: CANDLE_COLORS.live,
            // Важливо для UX: «жива» ціна має оновлюватися разом зі свічкою,
            // а не лише по закритій свічці (історичний candles-series).
            priceLineVisible: false,
            lastValueVisible: false,
        });

        const volume = chart.addHistogramSeries({
            priceScaleId: "volume",
            priceFormat: {
                type: "volume",
            },
            base: 0,
        });
        volume.applyOptions({
            lastValueVisible: false,
            priceLineVisible: false,
        });
        const liveVolume = chart.addHistogramSeries({
            priceScaleId: "volume",
            priceFormat: {
                type: "volume",
            },
            base: 0,
        });
        liveVolume.applyOptions({
            lastValueVisible: false,
            priceLineVisible: false,
        });
        chart.priceScale("volume").applyOptions({
            scaleMargins: {
                top: 0.76,
                bottom: 0.0,
            },
            borderVisible: false,
            ticksVisible: false,
        });

        let lastBar = null;
        let lastLiveBar = null;
        let lastTickPrice = null;
        let lastTickPriceAtMs = 0;
        let lastLiveVolume = 0;
        let liveVolumeVisible = false;
        let liveVolumeTime = null;
        let lastCandleDataset = [];
        let lastCandleTimes = [];
        let currentPriceLine = null;
        let currentPriceLineOwner = null;
        let currentPriceLineState = { price: null, color: null, owner: null };
        // Глобальний max обсягу для фіксованого autoscale.
        // Якщо автоскейл рахувати лише по видимому діапазону, при скролі/зумі volume «стрибає».
        let volumeScaleMax = 1;
        let recentVolumeMax = 0;
        let recentVolumes = [];
        let eventMarkers = [];
        let executionMarkers = [];
        let zoneMarkers = [];
        let zoneLabelsLogged = false;
        let poolLines = [];
        let poolSegments = [];
        let selectedLevelLines = [];
        let selectedLevelSegments = [];
        let selectedLevelBands = [];
        let rangeAreas = [];
        let zoneLines = [];
        let zoneAreas = [];
        let zoneBorders = [];
        let structureTriangles = [];
        let structureTriangleLabels = [];
        let oteOverlays = [];
        // Життєвий цикл OTE у фронтенді: бекенд віддає лише поточні (active) зони,
        // тому start/end відстежуємо по появі/зникненню в стрімі.
        // key -> { zone, start_time, end_time }
        let oteLifecycle = new Map();
        let barTimeSpanSeconds = 60;
        let chartTimeRange = { min: null, max: null };
        let lastBarsSignature = null;
        let autoFitDone = false;

        // Levels-V1 (4.1): legacy selection/рендер pools-рівнів у фронтенді вимкнено.
        // Єдине джерело істини для рівнів — `levels_selected_v1` з бекенду.
        const LEGACY_POOL_LEVELS_ENABLED = false;

        // Формат нижньої часової шкали: вирівнюємо підпис по кроку барів.
        // Це прибирає артефакти на кшталт 23:04 для TF=5m, якщо в даних трапляються
        // неідеально вирівняні timestamps.
        try {
            const pad2 = (n) => String(n).padStart(2, "0");
            const toTsSec = (t) => {
                if (t === null || t === undefined) return null;
                if (typeof t === "number") {
                    return Number.isFinite(t) ? Math.floor(t) : null;
                }
                if (typeof t === "object" && t && "year" in t && "month" in t && "day" in t) {
                    const y = Number(t.year);
                    const m = Number(t.month);
                    const d = Number(t.day);
                    if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
                    return Math.floor(Date.UTC(y, m - 1, d) / 1000);
                }
                return null;
            };

            chart.applyOptions({
                timeScale: {
                    tickMarkFormatter: (time) => {
                        const ts = toTsSec(time);
                        if (ts === null) return "";
                        const span = Math.max(1, Number(barTimeSpanSeconds) || 60);
                        const snapped = Math.floor(ts / span) * span;
                        const date = new Date(snapped * 1000);
                        const hh = pad2(date.getHours());
                        const mm = pad2(date.getMinutes());
                        return `${hh}:${mm}`;
                    },
                },
            });
        } catch (_e) {
            // noop
        }
        const priceScaleState = {
            manualRange: null,
            lastAutoRange: null,
        };
        let lastContainerSize = { width: 0, height: 0 };
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
        const DRAG_ACTIVATION_PX = 6;
        // Важливо: wheel по price-axis перехоплюємо у capture-фазі.
        // Інакше lightweight-charts може встигнути застосувати власний scale,
        // а наш manualRange-zoom/pan додасться зверху -> «бам, розтягнуло по Y».
        const WHEEL_OPTIONS = { passive: false, capture: true };
        const MIN_PRICE_SPAN = 1e-4;

        function applyCombinedMarkers() {
            const combined = []
                .concat(Array.isArray(eventMarkers) ? eventMarkers : [])
                .concat(Array.isArray(executionMarkers) ? executionMarkers : [])
                .concat(Array.isArray(zoneMarkers) ? zoneMarkers : []);
            if (!combined.length) {
                candles.setMarkers([]);
                return;
            }
            combined.sort((a, b) => Number(a.time) - Number(b.time));
            candles.setMarkers(combined);
        }

        function makePriceScaleAutoscaleInfoProvider(trackAutoRange) {
            return (baseImplementation) => {
            if (!priceScaleState.manualRange) {
                const base = baseImplementation();
                if (base?.priceRange) {
                    if (trackAutoRange) {
                        priceScaleState.lastAutoRange = {
                            min: base.priceRange.minValue,
                            max: base.priceRange.maxValue,
                        };
                    }
                }
                return base;
            }

            // Коли активний manualRange (наш vertical-pan), всі серії на правій шкалі
            // мають повертати ОДНАКОВИЙ priceRange, інакше lightweight-charts буде
            // “склеювати” діапазони і виходить ефект «стеля/підлога».
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
        }

        const priceScaleAutoscaleInfoProvider = makePriceScaleAutoscaleInfoProvider(true);
        const livePriceScaleAutoscaleInfoProvider = makePriceScaleAutoscaleInfoProvider(false);

        // Для оверлеїв (зони/рамки):
        // - у normal режимі НЕ впливають на autoscale (і не чіпають lastAutoRange);
        // - у manualRange режимі повертають той самий діапазон, щоб не було «стелі/підлоги».
        function overlayAutoscaleInfoProvider(baseImplementation) {
            if (!priceScaleState.manualRange) {
                return null;
            }
            const range = priceScaleState.manualRange;
            const base = typeof baseImplementation === "function" ? baseImplementation() : null;
            return {
                priceRange: {
                    minValue: range.min,
                    maxValue: range.max,
                },
                margins: base?.margins,
            };
        }

        function makeSessionRangeBoxAutoscaleInfoProvider(getRange) {
            return (baseImplementation) => {
                // Під час manualRange — поводимось ідентично до інших серій (щоб не було «стеля/підлога»).
                if (priceScaleState.manualRange) {
                    return priceScaleAutoscaleInfoProvider(baseImplementation);
                }

                // У normal режимі session boxes — лише візуальний шар.
                // Не впливаємо на autoscale (і не “дрібнимо” price-scale при скролі/зумі).
                return null;
            };
        }

        candles.applyOptions({ autoscaleInfoProvider: priceScaleAutoscaleInfoProvider });
        // Важливо: live-серія не має “перетирати” lastAutoRange — інакше перший vertical-pan
        // може стартувати з діапазону 1-ї live-свічки і дати різкий Y-стрибок.
        liveCandles.applyOptions({ autoscaleInfoProvider: livePriceScaleAutoscaleInfoProvider });
        for (let i = 0; i < sessionRangeBoxes.length; i += 1) {
            const idx = i;
            sessionRangeBoxes[idx].applyOptions({
                autoscaleInfoProvider: makeSessionRangeBoxAutoscaleInfoProvider(() => sessionRangeBoxRanges[idx]),
            });
        }

        setupPriceScaleInteractions();
        setupResizeHandling();
        setupHoverTooltip();

        function setupHoverTooltip() {
            if (!tooltipEl || typeof chart.subscribeCrosshairMove !== "function") {
                return;
            }

            let hoverTimer = null;
            let hideTimer = null;
            let lastPayload = null;
            let lastPointKey = null;

            // UX: на live-графіку crosshair callback може викликатись під час update/setData.
            // Не ховаємо tooltip миттєво і не скидаємо show-timer, якщо курсор не рухався.
            const SHOW_DELAY_MS = 200;
            const HIDE_GRACE_MS = 250;

            const clearHoverTimer = () => {
                if (hoverTimer) {
                    clearTimeout(hoverTimer);
                    hoverTimer = null;
                }
            };

            const clearHideTimer = () => {
                if (hideTimer) {
                    clearTimeout(hideTimer);
                    hideTimer = null;
                }
            };

            const hideTooltip = () => {
                clearHoverTimer();
                clearHideTimer();
                tooltipEl.hidden = true;
                tooltipEl.textContent = "";
            };

            const scheduleHideTooltip = () => {
                clearHoverTimer();
                clearHideTimer();
                hideTimer = setTimeout(() => {
                    hideTooltip();
                }, HIDE_GRACE_MS);
            };

            const formatCompact = (value) => {
                if (value === null || value === undefined) return "-";
                const num = Number(value);
                if (!Number.isFinite(num)) return "-";
                if (Math.abs(num) >= 1000) return String(Math.round(num));
                if (Math.abs(num) >= 1) return num.toFixed(2);
                return num.toPrecision(4);
            };

            const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.time || !param.point) {
                    scheduleHideTooltip();
                    return;
                }

                const seriesData = param.seriesData;
                const candle = seriesData?.get?.(candles) || seriesData?.get?.(liveCandles) || null;
                const volRow =
                    seriesData?.get?.(liveVolume) ||
                    seriesData?.get?.(volume) ||
                    null;

                if (!candle) {
                    scheduleHideTooltip();
                    return;
                }

                lastPayload = {
                    point: param.point,
                    time: param.time,
                    candle,
                    volume: volRow?.value ?? null,
                };

                clearHideTimer();

                // Якщо курсор не рухався (а прийшла подія від live-оновлення) —
                // не скидаємо таймер показу, інакше tooltip може "ніколи" не з'являтись.
                const pointKey = `${param.point.x}|${param.point.y}|${String(param.time)}`;
                if (hoverTimer && lastPointKey === pointKey) {
                    return;
                }
                lastPointKey = pointKey;

                clearHoverTimer();
                tooltipEl.hidden = true;

                hoverTimer = setTimeout(() => {
                    const payload = lastPayload;
                    if (!payload) {
                        hideTooltip();
                        return;
                    }

                    const price = payload.candle?.close;
                    const vol = payload.volume;

                    const lines = [`Close: ${formatCompact(price)}`, `Vol: ${formatCompact(vol)}`];

                    // ВАЖЛИВО: hit-test робимо по cursor_price (y-координата), а не по close свічки.
                    let cursorPrice = null;
                    try {
                        const y = payload.point?.y;
                        const fromCandles = candles?.coordinateToPrice?.(y);
                        const fromLive = liveCandles?.coordinateToPrice?.(y);
                        const v = fromCandles ?? fromLive;
                        const n = Number(v);
                        cursorPrice = Number.isFinite(n) ? n : null;
                    } catch (_e) {
                        cursorPrice = null;
                    }

                    const getMaybeNumber = (value) => {
                        if (value === null || value === undefined) return null;
                        const n = Number(value);
                        return Number.isFinite(n) ? n : null;
                    };

                    const getZoneDistanceAtr = (z) => {
                        return getMaybeNumber(z?.distance_atr ?? z?.meta?.distance_atr);
                    };

                    const formatZoneLines = (z, labelPrefix) => {
                        const toFiniteNumber = (value) => {
                            if (value === null || value === undefined) return null;
                            const n = Number(value);
                            return Number.isFinite(n) ? n : null;
                        };

                        const dir = String(z?.direction || "").toUpperCase();
                        const side = dir === "SHORT" ? "Bearish" : dir === "LONG" ? "Bullish" : "";
                        const rawType = String(z?.poi_type || z?.type || z?.label || "ZONE").toUpperCase();
                        const type =
                            rawType.startsWith("COMPOSITE") ? rawType :
                                rawType.includes("ORDER") && rawType.includes("BLOCK") ? "OB" :
                                    rawType.includes("OB") ? "OB" :
                                        rawType.includes("BREAKER") ? "BREAKER" :
                                            rawType.includes("FVG") || rawType.includes("IMBALANCE") ? "FVG" :
                                                rawType.replace(/\s+/g, " ").trim();
                        const tf = z?.timeframe ? String(z.timeframe) : "";
                        const score = toFiniteNumber(z?.score);
                        const uiRank = toFiniteNumber(z?._score);
                        const filled = toFiniteNumber(z?.filled_pct);
                        const dist = getZoneDistanceAtr(z);
                        const stateRaw = String(z?.state || "").trim().toUpperCase();
                        const invalidatedTime = toFiniteNumber(z?.invalidated_time);
                        const head = `${labelPrefix}: ${tf ? tf + " " : ""}${side ? side + " " : ""}${type}`.trim();

                        const deriveStatus = () => {
                            if (Number.isFinite(invalidatedTime)) {
                                return "INVALIDATED";
                            }

                            // Якщо бекенд уже прислав state — беремо як пріоритет.
                            if (stateRaw) {
                                if (stateRaw === "INVALIDATED") return "INVALIDATED";
                                if (stateRaw === "MITIGATED") return "MITIGATED";
                                if (stateRaw === "TOUCHED" || stateRaw === "TAPPED") return "TAPPED";
                                if (stateRaw === "FRESH") return "FRESH";
                                if (stateRaw === "FILLED") return "MITIGATED";
                            }

                            if (Number.isFinite(filled)) {
                                if (filled >= 80) return "MITIGATED";
                                if (filled > 0) return "TOUCHED";
                                return "FRESH";
                            }

                            return "UNKNOWN";
                        };

                        const linesOut = [head];

                        // Score: 0–100 шкала з бекенду (якщо є). UI-rank (якщо є) показуємо окремо.
                        if (Number.isFinite(score) && score > 0) {
                            linesOut.push(`score=${score.toFixed(2)}`);
                        }
                        if (Number.isFinite(uiRank) && uiRank > 0) {
                            linesOut.push(`rank_ui=${uiRank.toFixed(2)}`);
                        }

                        const isFvgLike = type.includes("FVG") || rawType.includes("FVG");

                        // filled/dist: показуємо лише якщо реально є значення (без n/a).
                        if (isFvgLike) {
                            if (Number.isFinite(filled)) {
                                linesOut.push(`filled=${Math.round(filled)}%`);
                            }
                        }
                        if (Number.isFinite(dist)) {
                            linesOut.push(`dist_atr=${dist.toFixed(2)}`);
                        } else {
                            linesOut.push("dist_atr=N/A");
                        }

                        const status = deriveStatus();
                        if (status === "MITIGATED" && Number.isFinite(filled)) {
                            linesOut.push(`status=MITIGATED (filled ${Math.round(filled)}%)`);
                        } else if (status === "TOUCHED") {
                            linesOut.push("status=TAPPED");
                        } else {
                            linesOut.push(`status=${status}`);
                        }

                        const whyRaw = Array.isArray(z?.why) ? z.why : [];
                        const why = whyRaw.map((v) => String(v)).filter((v) => v);
                        if (why.length) {
                            linesOut.push(`why: ${why.slice(0, 3).join("; ")}`);
                        }

                        return linesOut;
                    };

                    const cursorTs = (() => {
                        const t = payload.time;
                        const n = Number(t);
                        return Number.isFinite(n) ? n : null;
                    })();

                    const isTimeCompatible = (z) => {
                        if (cursorTs === null) return true;
                        const start = Number(z?.origin_time);
                        const end = Number(z?.invalidated_time);
                        if (Number.isFinite(start) && cursorTs < start) return false;
                        if (Number.isFinite(end) && cursorTs > end) return false;
                        return true;
                    };

                    const roleRank = (role) => {
                        const r = String(role || "").toUpperCase();
                        if (r === "PRIMARY" || r === "P") return 3;
                        if (r === "COUNTERTREND" || r === "C") return 2;
                        if (r === "NEUTRAL" || r === "N") return 1;
                        return 0;
                    };

                    const zonePriorityScore = (z) => {
                        const rr = roleRank(z?.role);
                        const strength = Number(z?.strength);
                        const confidence = Number(z?.confidence);
                        const score = Number(z?.score ?? z?._score);
                        const sOk = Number.isFinite(strength) ? strength : 0;
                        const cOk = Number.isFinite(confidence) ? confidence : 0;
                        const scOk = Number.isFinite(score) ? score : 0;
                        return rr * 1_000_000 + sOk * 10_000 + cOk * 1_000 + scOk;
                    };

                    const sortZonesByPriority = (a, b) => {
                        const ap = zonePriorityScore(a);
                        const bp = zonePriorityScore(b);
                        if (ap !== bp) return bp - ap;

                        const aw = Math.abs(Number(a?.max) - Number(a?.min));
                        const bw = Math.abs(Number(b?.max) - Number(b?.min));
                        const awOk = Number.isFinite(aw) ? aw : Number.POSITIVE_INFINITY;
                        const bwOk = Number.isFinite(bw) ? bw : Number.POSITIVE_INFINITY;
                        return awOk - bwOk;
                    };

                    const pickZoneStack = () => {
                        if (cursorPrice === null) return { hits: [], top: null, clusterSize: 0 };
                        const p = Number(cursorPrice);
                        if (!Number.isFinite(p)) return { hits: [], top: null, clusterSize: 0 };
                        const tolAbs = (() => {
                            const ref = pickRefPrice();
                            const r = Number(ref);
                            if (Number.isFinite(r) && r > 0) {
                                return Math.max(1e-9, r * 0.00005);
                            }
                            return 0;
                        })();

                        const clusters = Array.isArray(lastZoneClusters) ? lastZoneClusters : [];
                        const hits = clusters
                            .filter((c) => {
                                const z = c?.rep || null;
                                if (!z) return false;
                                const min = Number(z?.min ?? z?.price_min);
                                const max = Number(z?.max ?? z?.price_max);
                                if (!Number.isFinite(min) || !Number.isFinite(max)) return false;
                                const lo = Math.min(min, max);
                                const hi = Math.max(min, max);
                                if (p < lo - tolAbs || p > hi + tolAbs) return false;

                                // Для кластера: курсорний час має бути не раніше початку (мін. origin_time).
                                const start = Number(c?.start_time);
                                if (cursorTs !== null && Number.isFinite(start) && cursorTs < start) return false;
                                return true;
                            });

                        if (!hits.length) return { hits: [], top: null, clusterSize: 0 };

                        // Якщо декілька кластерів попали (рідко) — вибираємо найпріоритетніший реп.
                        hits.sort((a, b) => sortZonesByPriority(a?.rep, b?.rep));
                        const topCluster = hits[0];
                        const members = Array.isArray(topCluster?.members) ? topCluster.members.slice(0) : [];
                        members.sort(sortZonesByPriority);
                        const top = members[0] || topCluster?.rep || null;
                        return { hits, top, clusterSize: members.length || 1, members };
                    };

                    const pickTopPoi = () => {
                        const zs = Array.isArray(lastZones) ? lastZones.slice(0) : [];
                        if (!zs.length) return null;
                        zs.sort((a, b) => {
                            const as = Number(a?.score);
                            const bs = Number(b?.score);
                            const asOk = Number.isFinite(as) ? as : -1;
                            const bsOk = Number.isFinite(bs) ? bs : -1;
                            if (asOk !== bsOk) return bsOk - asOk;

                            const ad = getZoneDistanceAtr(a);
                            const bd = getZoneDistanceAtr(b);
                            const adOk = ad === null ? Number.POSITIVE_INFINITY : ad;
                            const bdOk = bd === null ? Number.POSITIVE_INFINITY : bd;
                            if (adOk !== bdOk) return adOk - bdOk;

                            const aw = Math.abs(Number(a?.max) - Number(a?.min));
                            const bw = Math.abs(Number(b?.max) - Number(b?.min));
                            const awOk = Number.isFinite(aw) ? aw : Number.POSITIVE_INFINITY;
                            const bwOk = Number.isFinite(bw) ? bw : Number.POSITIVE_INFINITY;
                            return awOk - bwOk;
                        });
                        return zs[0];
                    };

                    const zoneStack = pickZoneStack();
                    const hoverZone = zoneStack?.top || null;
                    const topPoi = null;

                    const pickAuxHit = () => {
                        // Пріоритет: POI зона завжди перемагає.
                        if (hoverZone) return { pool: null, ote: null };
                        if (cursorPrice === null) return { pool: null, ote: null };
                        const p = Number(cursorPrice);
                        if (!Number.isFinite(p)) return { pool: null, ote: null };

                        const ref = pickRefPrice();
                        const refOk = Number.isFinite(Number(ref)) ? Number(ref) : p;
                        const windowAbs = estimatePriceWindowAbs(refOk);
                        const tolAbs = Math.max(1e-9, windowAbs * 0.045, Math.abs(refOk) * 0.00006, 0.2);

                        // 1) Pools-V1: SSOT hit-test по band-геометрії.
                        // Пріоритет шарів: 5m > 1h > 4h. Без distance/strength.
                        let bestPool = null;
                        let bestRank = -Infinity;
                        let bestId = "";
                        for (const node of poolsV1ById.values()) {
                            const row = node?.last;
                            if (!row) continue;
                            const top = Number(row?.top);
                            const bot = Number(row?.bot);
                            if (!Number.isFinite(top) || !Number.isFinite(bot)) continue;
                            const lo = Math.min(top, bot);
                            const hi = Math.max(top, bot);
                            if (!(p >= lo && p <= hi)) continue;
                            const rank = poolsV1TfRank(row?.tf);
                            const id = String(row?.id || "");
                            if (rank > bestRank || (rank === bestRank && id && id < bestId)) {
                                bestPool = row;
                                bestRank = rank;
                                bestId = id;
                            }
                        }

                        // 2) OTE: якщо ціна всередині або поруч з діапазоном — хіт.
                        const otes = Array.isArray(lastOteZones) ? lastOteZones : [];
                        let bestOte = null;
                        let bestOteDist = Number.POSITIVE_INFINITY;
                        for (const z of otes) {
                            // Якщо зона має часовий діапазон — перевіряємо, що курсор у ньому.
                            if (cursorTs !== null) {
                                const zStart = Number(z?.start_time ?? z?.start ?? z?.from);
                                const zEndRaw = z?.end_time ?? z?.end ?? z?.to;
                                const zEnd = zEndRaw === null || zEndRaw === undefined ? null : Number(zEndRaw);
                                if (Number.isFinite(zStart)) {
                                    const ct = Number(cursorTs);
                                    if (!Number.isFinite(ct)) continue;
                                    if (ct < zStart) continue;
                                    if (zEnd !== null && Number.isFinite(zEnd) && ct > zEnd) continue;
                                }
                            }
                            const minRaw = Number(z?.min ?? z?.price_min ?? z?.ote_min);
                            const maxRaw = Number(z?.max ?? z?.price_max ?? z?.ote_max);
                            if (!Number.isFinite(minRaw) || !Number.isFinite(maxRaw)) continue;
                            const lo = Math.min(minRaw, maxRaw);
                            const hi = Math.max(minRaw, maxRaw);
                            const d = p < lo ? lo - p : p > hi ? p - hi : 0;
                            if (d <= tolAbs && d < bestOteDist) {
                                bestOte = z;
                                bestOteDist = d;
                            }
                        }

                        return { pool: bestPool, ote: bestOte };
                    };

                    const aux = pickAuxHit();
                    const poolHit = aux.pool;
                    const oteHit = aux.ote;

                    const execEvents = (() => {
                        if (cursorTs === null) return [];
                        const key = Math.floor(cursorTs);
                        const row = executionEventsByTime?.get?.(key);
                        return Array.isArray(row) ? row : [];
                    })();

                    const shortExecType = (t) => {
                        const u = String(t || "").toUpperCase();
                        if (u === "RETEST_OK") return "RETEST";
                        if (u === "MICRO_BOS") return "μBOS";
                        if (u === "MICRO_CHOCH") return "μCHOCH";
                        return u || "?";
                    };

                    const formatExecLine = (e) => {
                        if (!e || typeof e !== "object") return "?";
                        const t = shortExecType(e.event_type || e.type);
                        const d = String(e.direction || "").toUpperCase();
                        const arrow = d === "LONG" ? "↑" : d === "SHORT" ? "↓" : "";
                        const lvlNum = Number(e.level);
                        const lvl = Number.isFinite(lvlNum) ? ` @${formatCompact(lvlNum)}` : "";
                        const ref = String(e.ref || "").toUpperCase();
                        const refPart = ref ? ` (${ref}${e.poi_zone_id ? `:#${String(e.poi_zone_id)}` : ""})` : "";
                        return `${t}${arrow}${lvl}${refPart}`.trim();
                    };

                    const hasZoneHit = Boolean(hoverZone);

                    // Порожній блок для читабельності: додатковий роздільник ставимо лише
                    // коли є деталі POI/Execution.

                    if (execEvents.length || hoverZone || topPoi || poolHit || oteHit) {
                        lines.push("—");
                    }

                    // Execution (Stage5): показуємо лише коли є стрілка на цьому барі.
                    if (execEvents.length) {
                        lines.push("EXEC:");
                        execEvents.slice(0, 4).forEach((e) => lines.push(`- ${formatExecLine(e)}`));
                        if (execEvents.length > 4) {
                            lines.push(`- … +${execEvents.length - 4}`);
                        }
                    }

                    if (poolHit) {
                        // Pools-V1: показуємо band-діапазон + мінімальну семантику.
                        const kind = String(poolHit?.kind || poolHit?.type || "POOL").toUpperCase();
                        const side = String(poolHit?.side || "").toUpperCase();
                        const tfHint = String(poolHit?.tf || "").toLowerCase();
                        const status = String(poolHit?.status ?? poolHit?.state ?? "").toUpperCase();

                        const top = Number(poolHit?.top ?? poolHit?.hi);
                        const bot = Number(poolHit?.bot ?? poolHit?.lo);
                        const lo = Number.isFinite(top) && Number.isFinite(bot) ? Math.min(top, bot) : null;
                        const hi = Number.isFinite(top) && Number.isFinite(bot) ? Math.max(top, bot) : null;

                        const name = `${kind}${side ? ` ${side}` : ""}${tfHint ? ` ${tfHint}` : ""}${status ? ` — ${status}` : ""}`.trim();
                        lines.push(name);
                        if (lo !== null && hi !== null) {
                            const width = Math.max(0, hi - lo);
                            if (Math.abs(width) < 1e-9) {
                                lines.push(formatCompact(lo));
                            } else {
                                lines.push(`${formatCompact(lo)} – ${formatCompact(hi)}`);
                            }
                        } else {
                            const price = Number(poolHit?.price);
                            if (Number.isFinite(price)) lines.push(formatCompact(price));
                        }

                        // P4.x: Anchor+Suppress meta (без union-box). Показуємо коротко, що поруч були близькі пули.
                        const merged = Array.isArray(poolHit?.merged_from) ? poolHit.merged_from : [];
                        if (merged.length) {
                            const tfOrder = (tfRaw) => {
                                const tf = String(tfRaw || "").toLowerCase();
                                if (tf === "4h") return 30;
                                if (tf === "1h") return 20;
                                if (tf === "5m") return 10;
                                return 0;
                            };
                            const counts = new Map();
                            for (const m of merged) {
                                const tf = String(m?.tf || "").toLowerCase();
                                if (!tf) continue;
                                const rawCount = Number(m?.count);
                                const c = Number.isFinite(rawCount) && rawCount >= 1 ? Math.floor(rawCount) : 1;
                                counts.set(tf, (counts.get(tf) || 0) + c);
                            }
                            const parts = Array.from(counts.entries())
                                .sort((a, b) => tfOrder(b[0]) - tfOrder(a[0]) || String(a[0]).localeCompare(String(b[0])))
                                .map(([tf, c]) => `${tf}(${c})`);
                            if (parts.length) {
                                lines.push(`merged: ${parts.join(" + ")}`);
                            }
                        }
                    }

                    if (oteHit) {
                        const dir = String(oteHit?.direction || "").toUpperCase();
                        const side = dir === "SHORT" ? "SELL" : dir === "LONG" ? "BUY" : "";
                        const roleRaw = String(oteHit?.role ?? oteHit?.ote_role ?? "").toUpperCase();
                        const roleLabel = roleRaw === "PRIMARY" || roleRaw === "P" ? "Primary" :
                            roleRaw === "COUNTER" || roleRaw === "C" ? "Counter" :
                                roleRaw === "TARGET" ? "Target" :
                                    roleRaw === "ENTRY" ? "Entry" :
                                        roleRaw === "STOP" || roleRaw === "SL" ? "SL" :
                                            roleRaw === "TAKE_PROFIT" || roleRaw === "TP" ? "TP" :
                                                roleRaw ? roleRaw : "";
                        const minRaw = Number(oteHit?.min ?? oteHit?.price_min ?? oteHit?.ote_min);
                        const maxRaw = Number(oteHit?.max ?? oteHit?.price_max ?? oteHit?.ote_max);
                        if (Number.isFinite(minRaw) && Number.isFinite(maxRaw)) {
                            const lo = Math.min(minRaw, maxRaw);
                            const hi = Math.max(minRaw, maxRaw);
                            const name = `${side ? side + " " : ""}OTE${roleLabel ? ` (${roleLabel})` : ""}`.trim();
                            lines.push(name);
                            const width = Math.max(0, hi - lo);

                            // Діапазон — нижче під назвою.
                            if (Math.abs(width) < 1e-9) {
                                lines.push(formatCompact(lo));
                            } else {
                                lines.push(`${formatCompact(lo)} – ${formatCompact(hi)}`);
                            }

                            // Додаткова інфа для трейдера: розмір діапазону + позиція відносно Close.
                            if (Number.isFinite(width) && width > 0) {
                                lines.push(`Range: ${formatCompact(width)}`);
                            }

                            const closeNum = Number(payload?.candle?.close);
                            if (Number.isFinite(closeNum)) {
                                const dClose = closeNum < lo ? lo - closeNum : closeNum > hi ? closeNum - hi : 0;
                                const pos = dClose === 0 ? "в зоні" : closeNum < lo ? "нижче зони" : "вище зони";
                                lines.push(`Close: ${pos}`);
                                if (dClose > 0) {
                                    lines.push(`ΔClose: ${formatCompact(dClose)}`);
                                }
                            }
                        }
                    }
                    if (hoverZone) {
                        const tf = hoverZone?.timeframe ? String(hoverZone.timeframe) : "";
                        const type = normalizePoiType(hoverZone?.poi_type || hoverZone?.type || hoverZone?.label || "ZONE");
                        const roleRaw = String(hoverZone?.role || "").toUpperCase();
                        const roleLabel = roleRaw === "PRIMARY" || roleRaw === "P" ? "Primary" :
                            roleRaw === "COUNTERTREND" || roleRaw === "COUNTER" || roleRaw === "C" ? "Countertrend" :
                                roleRaw === "NEUTRAL" || roleRaw === "N" ? "Neutral" :
                                    roleRaw ? roleRaw : "-";
                        const stateRaw = String(hoverZone?.state || "").toUpperCase();
                        const stateLabel = stateRaw === "FRESH" ? "Fresh" :
                            stateRaw === "TOUCHED" ? "Touched" :
                                stateRaw === "MITIGATED" ? "Mitigated" :
                                    stateRaw ? stateRaw : "-";

                        lines.push(`POI: ${tf ? tf + " " : ""}${type} (${roleLabel}) — ${stateLabel}`.trim());

                        const pickTarget = (z) => {
                            const dir = String(z?.direction || "").toUpperCase();

                            const zMin = Math.min(Number(z?.min), Number(z?.max));
                            const zMax = Math.max(Number(z?.min), Number(z?.max));
                            if (!Number.isFinite(zMin) || !Number.isFinite(zMax)) return null;

                            const tfRankTarget = (tfRaw) => {
                                const tf = String(tfRaw || "").toLowerCase();
                                // Target-підказка: HTF важливіше.
                                if (tf === "4h") return 30;
                                if (tf === "1h") return 20;
                                if (tf === "5m") return 10;
                                return 0;
                            };

                            const all = [];
                            for (const node of poolsV1ById.values()) {
                                const r = node?.last;
                                if (!r) continue;
                                const top = Number(r?.top);
                                const bot = Number(r?.bot);
                                if (!Number.isFinite(top) || !Number.isFinite(bot)) continue;
                                all.push({
                                    id: String(r?.id || ""),
                                    type: String(r?.kind || r?.type || "POOL").toUpperCase(),
                                    tf: String(r?.tf || "").toLowerCase(),
                                    top: Math.max(top, bot),
                                    bot: Math.min(top, bot),
                                });
                            }

                            const wantType = dir === "LONG" ? "EQH" : dir === "SHORT" ? "EQL" : null;
                            const preferred = wantType ? all.filter((p) => p.type === wantType) : all;
                            const src = preferred.length ? preferred : all;
                            if (!src.length) return null;

                            const pickAbove = (rows) =>
                                rows
                                    .slice()
                                    .sort((a, b) => {
                                        const aAbove = a.bot >= zMax;
                                        const bAbove = b.bot >= zMax;
                                        if (aAbove !== bAbove) return aAbove ? -1 : 1;
                                        const da = aAbove ? a.bot - zMax : Math.abs(((a.top + a.bot) * 0.5) - zMax);
                                        const db = bAbove ? b.bot - zMax : Math.abs(((b.top + b.bot) * 0.5) - zMax);
                                        if (da !== db) return da - db;
                                        const ra = tfRankTarget(a.tf);
                                        const rb = tfRankTarget(b.tf);
                                        if (ra !== rb) return rb - ra;
                                        return String(a.id).localeCompare(String(b.id));
                                    })[0] || null;

                            const pickBelow = (rows) =>
                                rows
                                    .slice()
                                    .sort((a, b) => {
                                        const aBelow = a.top <= zMin;
                                        const bBelow = b.top <= zMin;
                                        if (aBelow !== bBelow) return aBelow ? -1 : 1;
                                        const da = aBelow ? zMin - a.top : Math.abs(((a.top + a.bot) * 0.5) - zMin);
                                        const db = bBelow ? zMin - b.top : Math.abs(((b.top + b.bot) * 0.5) - zMin);
                                        if (da !== db) return da - db;
                                        const ra = tfRankTarget(a.tf);
                                        const rb = tfRankTarget(b.tf);
                                        if (ra !== rb) return rb - ra;
                                        return String(a.id).localeCompare(String(b.id));
                                    })[0] || null;

                            if (dir === "LONG") return pickAbove(src);
                            if (dir === "SHORT") return pickBelow(src);

                            return src
                                .slice()
                                .sort((a, b) => {
                                    const ca = (a.top + a.bot) * 0.5;
                                    const cb = (b.top + b.bot) * 0.5;
                                    const da = Math.abs(ca - ((zMin + zMax) * 0.5));
                                    const db = Math.abs(cb - ((zMin + zMax) * 0.5));
                                    if (da !== db) return da - db;
                                    const ra = tfRankTarget(a.tf);
                                    const rb = tfRankTarget(b.tf);
                                    if (ra !== rb) return rb - ra;
                                    return String(a.id).localeCompare(String(b.id));
                                })[0] || null;
                        };

                        const target = pickTarget(hoverZone);
                        const dir = String(hoverZone?.direction || "").toUpperCase();
                        if (dir === "LONG") {
                            lines.push("Draw (delivery): ↑ до EQH");
                        } else if (dir === "SHORT") {
                            lines.push("Draw (delivery): ↓ до EQL");
                        }

                        if (target) {
                            const tType = String(target.type || "POOL").toUpperCase();
                            const tfHint = String(target.tf || "").toLowerCase();
                            const lo = Number.isFinite(Number(target.bot)) && Number.isFinite(Number(target.top))
                                ? Math.min(Number(target.bot), Number(target.top))
                                : null;
                            const hi = Number.isFinite(Number(target.bot)) && Number.isFinite(Number(target.top))
                                ? Math.max(Number(target.bot), Number(target.top))
                                : null;

                            if (lo !== null && hi !== null && Math.abs(hi - lo) > 1e-9) {
                                lines.push(`Target: ${tType} ${formatCompact(lo)} – ${formatCompact(hi)}${tfHint ? ` (HTF ${tfHint})` : ""}`);
                            } else {
                                const price = lo !== null ? lo : hi !== null ? hi : Number(target.price);
                                if (Number.isFinite(price)) {
                                    lines.push(`Target: ${tType} @${formatCompact(price)}${tfHint ? ` (HTF ${tfHint})` : ""}`);
                                }
                            }
                        }

                        const score = getMaybeNumber( hoverZone?.score );
                        const filled = getMaybeNumber( hoverZone?.filled_pct );
                        const distAtr = getZoneDistanceAtr(hoverZone);

                        lines.push(`Mitigation: ${filled === null ? "N/A" : `${Math.round(filled)}%`}`);
                        lines.push(`dist_atr: ${distAtr === null ? "N/A" : distAtr.toFixed(2)}`);
                        lines.push(`score: ${score === null ? "N/A" : score.toFixed(2)}`);

                        const whyRaw = Array.isArray(hoverZone?.why) ? hoverZone.why : [];
                        const why = whyRaw.map((v) => String(v)).filter((v) => v);
                        if (why.length) {
                            const head = why.slice(0, 3).join("; ");
                            const tailCount = Math.max(0, why.length - 3);
                            lines.push(`Why: ${head}${tailCount ? ` (+${tailCount})` : ""}`);
                        }

                        // Якщо зона — кластер, показуємо стек.
                        const members = Array.isArray(zoneStack?.members) ? zoneStack.members : [];
                        if (members.length > 1) {
                            lines.push(`Кластер зон: ${members.length} (об'єднано)`);
                            lines.push(`Dominant: ${zoneLabelText(hoverZone)} (${stateLabel})`);

                            const agg = new Map();
                            members.forEach((z) => {
                                const key = `${normalizePoiType(z?.poi_type || z?.type || z?.label || "ZONE")}(${roleShort(z)})`;
                                agg.set(key, (agg.get(key) || 0) + 1);
                            });
                            const parts = Array.from(agg.entries())
                                .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
                                .slice(0, 6)
                                .map(([k, c]) => `${k}×${c}`);
                            if (parts.length) {
                                lines.push(`Склад: ${parts.join(", ")}`);
                            }
                        }
                    }

                    tooltipEl.textContent = lines.join("\n");

                    // Перетворимо \n на реальні рядки без innerHTML.
                    tooltipEl.style.whiteSpace = "pre-wrap";
                    tooltipEl.hidden = false;

                    const shell = tooltipEl.offsetParent || tooltipEl.parentElement;
                    if (!shell || typeof shell.getBoundingClientRect !== "function") {
                        return;
                    }
                    const shellRect = shell.getBoundingClientRect();
                    const containerRect = container.getBoundingClientRect();
                    const tipRect = tooltipEl.getBoundingClientRect();

                    // Координати param.point — відносно області графіка (container).
                    // Перетворимо їх у координати shell (offsetParent), щоб робити clamp.
                    const pointX = (containerRect.left - shellRect.left) + payload.point.x;
                    const pointY = (containerRect.top - shellRect.top) + payload.point.y;

                    const padding = 8;
                    let left = pointX + padding;
                    let top = pointY + padding;

                    // Якщо не влазить справа — ставимо ліворуч.
                    if (left + tipRect.width > shellRect.width - padding) {
                        left = pointX - tipRect.width - padding;
                    }
                    // Якщо не влазить знизу — піднімаємо вгору.
                    if (top + tipRect.height > shellRect.height - padding) {
                        top = pointY - tipRect.height - padding;
                    }

                    // Завжди clamp в межах контейнера.
                    const maxLeft = shellRect.width - tipRect.width - padding;
                    const maxTop = shellRect.height - tipRect.height - padding;
                    left = clamp(left, padding, Math.max(padding, maxLeft));
                    top = clamp(top, padding, Math.max(padding, maxTop));

                    tooltipEl.style.left = `${left}px`;
                    tooltipEl.style.top = `${top}px`;
                }, SHOW_DELAY_MS);
            });

            container.addEventListener("mouseleave", hideTooltip);
            interactionCleanup.push(() => container.removeEventListener("mouseleave", hideTooltip));
            interactionCleanup.push(() => {
                clearHoverTimer();
                clearHideTimer();
                if (tooltipEl) {
                    tooltipEl.hidden = true;
                }
            });
        }

        function setBars(bars) {
            // Якщо користувач «відмотав» графік вліво, не маємо права зсувати viewport
            // під час періодичного оновлення датасету (polling/rehydrate шарів).
            const prevLogicalRange = chart.timeScale().getVisibleLogicalRange();
            const prevScrollPos = chart.timeScale().scrollPosition();
            const prevLen = Array.isArray(lastCandleDataset) ? lastCandleDataset.length : 0;
            const wasFollowingRightEdge =
                prevLogicalRange && prevLen
                    ? Number(prevLogicalRange.to) >= prevLen - 2
                    : true;

            if (!Array.isArray(bars) || bars.length === 0) {
                resetManualPriceScale({ silent: true });
                priceScaleState.lastAutoRange = null;
                candles.setData([]);
                liveCandles.setData([]);
                volume.setData([]);
                liveVolume.setData([]);
                setSessionsData([]);
                lastBar = null;
                lastLiveBar = null;
                lastLiveVolume = 0;
                clearCurrentPriceLine();
                recentVolumeMax = 0;
                recentVolumes = [];
                chartTimeRange = { min: null, max: null };
                lastBarsSignature = null;
                autoFitDone = false;
                lastCandleDataset = [];
                lastCandleTimes = [];
                lastZones = [];
                lastZoneClusters = [];
                zoneLabelById = new Map();
                zoneGeometryById = new Map();
                return;
            }

            const normalized = bars
                .map((bar) => {
                    const candle = normalizeBar(bar);
                    if (!candle) {
                        return null;
                    }
                    return {
                        candle,
                        volume: normalizeVolume(bar),
                    };
                })
                .filter(Boolean)
                .sort((a, b) => a.candle.time - b.candle.time);

            const candleData = normalized.map((row) => row.candle);
            const volumeValues = normalized.map((row) => row.volume);
            lastCandleDataset = candleData.slice();
            lastCandleTimes = candleData.map((bar) => bar.time);

            // Фіксуємо шкалу volume по всьому датасету (а не по видимому фрагменту).
            // Це прибирає "провалювання" обсягів при горизонтальному скролі.
            volumeScaleMax = computeVolumeScaleMax(volumeValues);

            const signature = {
                firstTime: candleData[0]?.time ?? null,
                lastTime: candleData[candleData.length - 1]?.time ?? null,
                length: candleData.length,
            };
            const looksLikeNewDataset =
                !lastBarsSignature ||
                signature.firstTime !== lastBarsSignature.firstTime ||
                signature.length < lastBarsSignature.length ||
                signature.lastTime < lastBarsSignature.lastTime;
            if (looksLikeNewDataset) {
                autoFitDone = false;
                // Якщо датасет реально «перезапустився» (символ/TF/бекфіл/ресет) —
                // логічно скинути ручний price range.
                resetManualPriceScale({ silent: true });
                priceScaleState.lastAutoRange = null;

                // Також скидаємо "липкі" execution-стрілочки, щоб не змішувати різні серії/TF.
                lastExecutionEvents = [];
                executionEventsByTime = new Map();
                if (executionMarkers.length) {
                    executionMarkers = [];
                    applyCombinedMarkers();
                }
            }

            recentVolumes = volumeValues.slice(Math.max(0, volumeValues.length - VOLUME_WINDOW_SIZE));
            recentVolumeMax = computeRecentMaxVolume(volumeValues);

            const styledCandles = candleData.map((bar, index) => {
                const vol = volumeValues[index] ?? 0;
                if (!(recentVolumeMax > 0)) {
                    return bar;
                }
                const isUp = Number(bar.close) >= Number(bar.open);
                const alpha = volumeToOpacity(vol, recentVolumeMax);
                const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                const rgba = hexToRgba(base, alpha);
                return {
                    ...bar,
                    color: rgba,
                    wickColor: rgba,
                    borderColor: rgba,
                };
            });

            candles.setData(styledCandles);
            setSessionsData(candleData);
            if (recentVolumeMax > 0) {
                const volumeData = candleData.map((bar, index) => {
                    const vol = volumeValues[index] ?? 0;
                    const isUp = Number(bar.close) >= Number(bar.open);
                    // Важливо для UX: при великих піках volume відносна прозорість робить
                    // більшість брусків майже невидимими (особливо при зумі/скролі).
                    // Тому для гістограми тримаємо сталу альфу.
                    const alpha = clamp(VOLUME_BAR_ALPHA, 0.18, 0.85);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    return {
                        time: bar.time,
                        value: vol,
                        color: hexToRgba(base, alpha),
                    };
                });
                volume.setData(volumeData);
            } else {
                volume.setData([]);
            }

            lastBar = candleData.length ? candleData[candleData.length - 1] : null;
            // Якщо live-бар більше не відповідає історії — скинемо.
            if (lastLiveBar && lastBar && lastLiveBar.time < lastBar.time) {
                clearLiveBar();
            }
            updateCurrentPriceLine();
            updateBarTimeSpanFromBars(candleData);
            updateTimeRangeFromBars(candleData);

            if (!autoFitDone) {
                chart.timeScale().fitContent();
                autoFitDone = true;
            } else if (prevLogicalRange && !wasFollowingRightEdge) {
                chart.timeScale().setVisibleLogicalRange({
                    from: prevLogicalRange.from,
                    to: prevLogicalRange.to,
                });
            } else if (!prevLogicalRange && Number.isFinite(prevScrollPos) && !wasFollowingRightEdge) {
                chart.timeScale().scrollToPosition(prevScrollPos, false);
            }
            lastBarsSignature = signature;
        }

        function utcDayStartSec(timeSec) {
            const d = new Date(Number(timeSec) * 1000);
            return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000);
        }

        function normalizeSessionEpochWindows(windows) {
            if (!Array.isArray(windows)) {
                return null;
            }

            const out = { asia: [], london: [], newYork: [] };

            for (const w of windows) {
                const kind = String(w?.kind || "");
                if (kind && kind !== "session") {
                    continue;
                }
                const tag = String(w?.tag || "").trim().toUpperCase();
                const from = Number(w?.start_ts);
                const to = Number(w?.end_ts);
                if (!tag || !Number.isFinite(from) || !Number.isFinite(to) || !(to > from)) {
                    continue;
                }

                if (tag === "TOKYO" || tag === "ASIA") {
                    out.asia.push({ from: Math.floor(from), to: Math.floor(to) });
                } else if (tag === "LONDON" || tag === "LDN") {
                    out.london.push({ from: Math.floor(from), to: Math.floor(to) });
                } else if (tag === "NY" || tag === "NEW_YORK" || tag === "NEWYORK") {
                    out.newYork.push({ from: Math.floor(from), to: Math.floor(to) });
                }
            }

            out.asia.sort((a, b) => a.from - b.from);
            out.london.sort((a, b) => a.from - b.from);
            out.newYork.sort((a, b) => a.from - b.from);
            return out;
        }

        function setSessionWindowsUtc(windows) {
            // Новий формат: epoch-вікна.
            const epoch = normalizeSessionEpochWindows(windows);
            if (epoch) {
                sessionWindowsUtcEpoch = epoch;
                setSessionsData(lastCandleDataset);
                return;
            }

            // Якщо epoch-вікон немає — UI НЕ придумує розклад, а просто ховає шари.
            sessionWindowsUtcEpoch = null;
            setSessionsData(lastCandleDataset);
        }

        function setSessionsRibbonData(payload) {
            // P1: AiOne Sessions ribbon (DOM overlay, не серія графіка).
            // Джерело правди: viewer_state.meta.{session_windows_utc, active_session_utc} + payload_ts.
            try {
                if (!sessionsRibbonOverlay) {
                    const shell = container?.closest?.(".chart-overlay-shell") || container?.parentElement || null;
                    const Ctor = window.SessionsRibbonOverlay;
                    if (shell && typeof Ctor === "function") {
                        sessionsRibbonOverlay = new Ctor({
                            chart,
                            shell,
                            container,
                        });
                    }
                }
                if (sessionsRibbonOverlay && typeof sessionsRibbonOverlay.update === "function") {
                    sessionsRibbonOverlay.update(payload || null);
                }
            } catch (_e) {
                // noop
            }
        }

        function setSessionsData(candleData) {
            const applyBlock = (series, visible, from, to) => {
                if (!series || typeof series.setData !== "function") {
                    return;
                }
                if (!visible) {
                    series.setData([]);
                    if (typeof series.applyOptions === "function") {
                        series.applyOptions({ visible: false });
                    }
                    return;
                }
                const start = Math.floor(from);
                const end = Math.floor(to);
                series.setData([
                    { time: start, value: SESSION_BAND_VALUE },
                    { time: end, value: SESSION_BAND_VALUE },
                ]);
                if (typeof series.applyOptions === "function") {
                    series.applyOptions({ visible: true });
                }
            };

            const clearAllBands = () => {
                for (const band of sessionSeries.bands.asia) {
                    applyBlock(band, false);
                }
                for (const band of sessionSeries.bands.london) {
                    applyBlock(band, false);
                }
                for (const band of sessionSeries.bands.newYork) {
                    applyBlock(band, false);
                }
            };

            if (!sessionSeries.enabled) {
                clearAllBands();
                return;
            }

            if (!ENABLE_SESSION_BACKGROUND_BANDS) {
                clearAllBands();
                return;
            }

            if (!Array.isArray(candleData) || candleData.length === 0) {
                clearAllBands();
                return;
            }

            const firstTime = Number(candleData[0]?.time);
            const lastTime = Number(candleData[candleData.length - 1]?.time);
            if (!Number.isFinite(firstTime) || !Number.isFinite(lastTime) || !(lastTime > firstTime)) {
                clearAllBands();
                return;
            }

            // «Підкладка» сесій від чарту (за UTC): Tokyo, London, New York.
            // ВАЖЛИВО: розклад — SSOT (viewer_state.meta.session_windows_utc).
            // Кожен день/сесія — окремий блок (окрема серія з 2 точками),
            // щоб не було «діагоналей» і щоб різні дні не з'єднувались.
            // Також ми обмежуємо кількість блоків розміром пулу, щоб не плодити серії.

            if (!sessionWindowsUtcEpoch) {
                // Нема SSOT epoch-вікон — не рендеримо нічого.
                clearAllBands();
                return;
            }

            const blocks = { asia: [], london: [], newYork: [] };
            const clipList = (arr) =>
                (Array.isArray(arr) ? arr : [])
                    .map((seg) => {
                        const from = Math.max(Number(seg?.from), firstTime);
                        const to = Math.min(Number(seg?.to), lastTime);
                        return { from, to };
                    })
                    .filter((seg) => Number.isFinite(seg.from) && Number.isFinite(seg.to) && seg.to > seg.from);

            blocks.asia = clipList(sessionWindowsUtcEpoch.asia);
            blocks.london = clipList(sessionWindowsUtcEpoch.london);
            blocks.newYork = clipList(sessionWindowsUtcEpoch.newYork);

            // Заповнюємо пул серій: 1 блок = 1 BaselineSeries.
            // Якщо блоків менше за пул — решту ховаємо.
            const applyPool = (pool, list) => {
                const items = Array.isArray(list) ? list.slice() : [];
                // Для стабільності — відсортуємо по часу початку.
                items.sort((a, b) => Number(a.from) - Number(b.from));
                // Страховка: якщо чомусь блоків більше пулу — беремо хвіст (найновіші).
                const tail = items.length > SESSION_BAND_POOL_SIZE ? items.slice(items.length - SESSION_BAND_POOL_SIZE) : items;
                for (let i = 0; i < pool.length; i += 1) {
                    const seg = tail[i];
                    if (seg && Number.isFinite(seg.from) && Number.isFinite(seg.to) && seg.to > seg.from) {
                        applyBlock(pool[i], true, seg.from, seg.to);
                    } else {
                        applyBlock(pool[i], false);
                    }
                }
            };

            applyPool(sessionSeries.bands.asia, blocks.asia);
            applyPool(sessionSeries.bands.london, blocks.london);
            applyPool(sessionSeries.bands.newYork, blocks.newYork);
        }

        function setSessionsEnabled(enabled) {
            const next = Boolean(enabled);
            if (sessionSeries.enabled === next) {
                return;
            }
            sessionSeries.enabled = next;

            const applyVisible = (series, value) => {
                if (!series || typeof series.applyOptions !== "function") {
                    return;
                }
                series.applyOptions({ visible: value });
            };
            for (const band of sessionSeries.bands.asia) {
                applyVisible(band, next);
            }
            for (const band of sessionSeries.bands.london) {
                applyVisible(band, next);
            }
            for (const band of sessionSeries.bands.newYork) {
                applyVisible(band, next);
            }

            setSessionsData(lastCandleDataset);

            // Синхронізуємо також session range boxes.
            setSessionRangeBoxes(lastSessionRangeBoxesRequest);
        }

        function setSessionRangeBoxes(ranges) {
            lastSessionRangeBoxesRequest = Array.isArray(ranges) ? ranges : null;

            if (!sessionSeries.enabled) {
                for (let i = 0; i < sessionRangeBoxes.length; i += 1) {
                    sessionRangeBoxes[i].setData([]);
                    sessionRangeBoxes[i].applyOptions({ visible: false });
                    sessionRangeBoxRanges[i] = null;
                }
                return;
            }

            const pickSessionFill = (tag) => {
                const key = String(tag || "").trim().toLowerCase();
                // Більш видимі «зони» сесій: New York — зелений, Tokyo — синій, London — оранжевий.
                // Лінії лишаємо вимкненими (працює лише заливка).
                if (key === "new_york" || key === "newyork" || key === "ny") {
                    return {
                        a1: "rgba(34, 197, 94, 0.10)",
                        a2: "rgba(34, 197, 94, 0.04)",
                    };
                }
                if (key === "tokyo" || key === "asia") {
                    return {
                        a1: "rgba(59, 130, 246, 0.10)",
                        a2: "rgba(59, 130, 246, 0.04)",
                    };
                }
                if (key === "london") {
                    return {
                        a1: "rgba(249, 115, 22, 0.10)",
                        a2: "rgba(249, 115, 22, 0.04)",
                    };
                }
                return {
                    a1: "rgba(209, 212, 220, 0.08)",
                    a2: "rgba(209, 212, 220, 0.03)",
                };
            };

            const toTsSec = (t) => {
                if (t === null || t === undefined) return null;
                if (typeof t === "number") return Number.isFinite(t) ? Math.floor(t) : null;
                if (typeof t === "object" && t && "year" in t && "month" in t && "day" in t) {
                    const y = Number(t.year);
                    const m = Number(t.month);
                    const d = Number(t.day);
                    if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
                    return Math.floor(Date.UTC(y, m - 1, d) / 1000);
                }
                return null;
            };

            const visible = chart?.timeScale?.()?.getVisibleRange?.() || null;
            const vFrom = toTsSec(visible?.from);
            const vTo = toTsSec(visible?.to);
            const span = Math.max(1, Number(barTimeSpanSeconds) || 60);
            const bufferSec = Math.max(60 * 15, span * 20); // ±15m або ±20 барів

            const cleaned = (Array.isArray(ranges) ? ranges : [])
                .filter((r) => r && typeof r === "object")
                .map((r) => {
                    const from = Number(r?.from ?? r?.start_ts);
                    const to = Number(r?.to ?? r?.end_ts);
                    const low = Number(r?.low);
                    const high = Number(r?.high);
                    const rawSession = (r?.session ?? r?.tag) || "";
                    const session = String(rawSession).trim().toLowerCase();
                    return { from, to, low, high, session };
                })
                .filter(
                    (r) =>
                        Number.isFinite(r.from) &&
                        Number.isFinite(r.to) &&
                        Number.isFinite(r.low) &&
                        Number.isFinite(r.high) &&
                        r.to > r.from &&
                        r.high >= r.low,
                )
                .sort((a, b) => Number(a.from) - Number(b.from))
                .filter((r) => {
                    // Якщо немає валідного visible-range — не фільтруємо (safe default).
                    if (!Number.isFinite(vFrom) || !Number.isFinite(vTo) || !(vTo > vFrom)) {
                        return true;
                    }
                    const from = Math.floor(Number(r.from));
                    const to = Math.floor(Number(r.to));
                    const minT = Math.floor(vFrom) - bufferSec;
                    const maxT = Math.floor(vTo) + bufferSec;
                    return !(to < minT || from > maxT);
                });

            // Якщо боксів більше, ніж пул — беремо найновіші (щоб не було “вибуху” на wide range).
            const items = cleaned.length > SESSION_RANGE_BOX_POOL_SIZE
                ? cleaned.slice(cleaned.length - SESSION_RANGE_BOX_POOL_SIZE)
                : cleaned;

            for (let i = 0; i < sessionRangeBoxes.length; i += 1) {
                const item = items[i] || null;
                if (!item) {
                    sessionRangeBoxes[i].setData([]);
                    sessionRangeBoxes[i].applyOptions({ visible: false });
                    sessionRangeBoxRanges[i] = null;
                    continue;
                }

                const fill = pickSessionFill(item.session);
                sessionRangeBoxes[i].applyOptions({
                    visible: true,
                    baseValue: { type: "price", price: item.low },
                    baseLineVisible: false,
                    baseLineWidth: 0,
                    lineVisible: false,
                    lineColor: "rgba(0, 0, 0, 0)",
                    topLineColor: "rgba(0, 0, 0, 0)",
                    bottomLineColor: "rgba(0, 0, 0, 0)",
                    topFillColor1: fill.a1,
                    topFillColor2: fill.a2,
                    bottomFillColor1: fill.a1,
                    bottomFillColor2: fill.a2,
                });
                sessionRangeBoxes[i].setData([
                    { time: Math.floor(item.from), value: item.high },
                    { time: Math.floor(item.to), value: item.high },
                ]);
                sessionRangeBoxRanges[i] = item;
            }
        }

        function setSessionRangeBox(range) {
            lastSessionRangeRequest = range || null;
            if (!range) {
                setSessionRangeBoxes(null);
                return;
            }
            setSessionRangeBoxes([
                {
                    from: Number(range?.from),
                    to: Number(range?.to),
                    low: Number(range?.low),
                    high: Number(range?.high),
                    session: String(range?.session || ""),
                },
            ]);
        }

        function setLiveBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }

            // Критично для UX (TradingView-like): коли ринок на паузі (ніч/вихідні/свята),
            // у датасеті немає нових барів, але тиковий час може «йти по годиннику».
            // Якщо пустити такий live-бар з часом далеко праворуч, lightweight-charts
            // розтягне time-scale і покаже порожнє полотно.
            // Тому: якщо live-час суттєво попереду останнього історичного бару —
            // вважаємо це паузою і НЕ оновлюємо live-бар (очищаємо його, якщо був).
            const lastClosedTime = Number(lastBar?.time);
            const spanSec = Math.max(1, Number(barTimeSpanSeconds) || 60);
            if (Number.isFinite(lastClosedTime) && Number.isFinite(normalized.time)) {
                // Дозволяємо максимум 2 бари «вперед» (на випадок лагу/неідеальних таймінгів).
                const maxAllowed = lastClosedTime + spanSec * 2;
                if (normalized.time > maxAllowed) {
                    clearLiveBar();
                    return;
                }
            }

            let vol = normalizeVolume(bar);
            // Якщо live volume вже накопичене у межах свічки — не даємо йому миготіти в 0.
            if (vol <= 0 && lastLiveBar && normalized.time === lastLiveBar.time && lastLiveVolume > 0) {
                vol = lastLiveVolume;
            } else {
                lastLiveVolume = vol;
            }

            if (vol > volumeScaleMax) {
                volumeScaleMax = vol;
            }
            // Тримаємо рівно одну "живу" свічку.
            if (!lastLiveBar || normalized.time !== lastLiveBar.time) {
                liveCandles.setData([normalized]);
            } else {
                liveCandles.update(normalized);
            }
            lastLiveBar = normalized;

            if (vol > 0) {
                const point = {
                    time: normalized.time,
                    value: vol,
                    color: "rgba(250, 204, 21, 0.35)",
                };
                // Важливо: не робимо setData на кожен тик.
                // Якщо бар той самий — update; якщо новий — setData (щоб тримати рівно 1 точку).
                if (!liveVolumeVisible || liveVolumeTime === null || normalized.time !== liveVolumeTime) {
                    liveVolume.setData([point]);
                } else {
                    liveVolume.update(point);
                }
                liveVolumeVisible = true;
                liveVolumeTime = normalized.time;
            } else {
                // Якщо live volume вже порожній — не чіпаємо (щоб не смикати графік).
                if (liveVolumeVisible) {
                    liveVolume.setData([]);
                    liveVolumeVisible = false;
                    liveVolumeTime = null;
                }
            }

            updateCurrentPriceLine();
        }

        function clearLiveBar() {
            liveCandles.setData([]);
            liveVolume.setData([]);
            lastLiveBar = null;
            lastLiveVolume = 0;
            liveVolumeVisible = false;
            liveVolumeTime = null;
            updateCurrentPriceLine();
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            const vol = normalizeVolume(bar);
            if (vol > volumeScaleMax) {
                volumeScaleMax = vol;
            }
            if (!lastBar || normalized.time >= lastBar.time) {
                if (lastBar && normalized.time > lastBar.time) {
                    const diff = normalized.time - lastBar.time;
                    if (Number.isFinite(diff) && diff > 0) {
                        barTimeSpanSeconds = Math.max(
                            1,
                            Math.round((barTimeSpanSeconds * 3 + diff) / 4)
                        );
                    }
                }

                if (lastBar && normalized.time === lastBar.time && recentVolumes.length) {
                    recentVolumes[recentVolumes.length - 1] = vol;
                } else if (lastBar && normalized.time > lastBar.time) {
                    recentVolumes.push(vol);
                    if (recentVolumes.length > VOLUME_WINDOW_SIZE) {
                        recentVolumes.shift();
                    }
                }
                recentVolumeMax = computeRecentMaxVolume(recentVolumes);

                let candleToWrite = normalized;
                if (recentVolumeMax > 0) {
                    const isUp = Number(normalized.close) >= Number(normalized.open);
                    const alpha = volumeToOpacity(vol, recentVolumeMax);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    const rgba = hexToRgba(base, alpha);
                    candleToWrite = {
                        ...normalized,
                        color: rgba,
                        wickColor: rgba,
                        borderColor: rgba,
                    };
                }
                candles.update(candleToWrite);

                if (recentVolumeMax > 0) {
                    const isUp = Number(normalized.close) >= Number(normalized.open);
                    const alpha = clamp(VOLUME_BAR_ALPHA, 0.18, 0.85);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    volume.update({
                        time: normalized.time,
                        value: vol,
                        color: hexToRgba(base, alpha),
                    });
                }
                lastBar = normalized;
                if (Array.isArray(lastCandleDataset) && lastCandleDataset.length) {
                    const lastIdx = lastCandleDataset.length - 1;
                    const prev = lastCandleDataset[lastIdx];
                    const prevTime = Number(prev?.time);
                    if (Number.isFinite(prevTime) && normalized.time === prevTime) {
                        lastCandleDataset[lastIdx] = normalized;
                        if (Array.isArray(lastCandleTimes) && lastCandleTimes.length) {
                            lastCandleTimes[lastCandleTimes.length - 1] = normalized.time;
                        }
                    } else if (!Number.isFinite(prevTime) || normalized.time > prevTime) {
                        lastCandleDataset.push(normalized);
                        if (Array.isArray(lastCandleTimes)) {
                            lastCandleTimes.push(normalized.time);
                        } else {
                            lastCandleTimes = [normalized.time];
                        }
                    }
                } else {
                    lastCandleDataset = [normalized];
                    lastCandleTimes = [normalized.time];
                }
                if (chartTimeRange.min == null) {
                    chartTimeRange.min = normalized.time;
                }
                chartTimeRange.max = Math.max(chartTimeRange.max ?? normalized.time, normalized.time);
                updateCurrentPriceLine();
                setSessionsData(lastCandleDataset);
            }
        }

        function clearCurrentPriceLine() {
            if (!currentPriceLine) {
                return;
            }
            try {
                if (currentPriceLineOwner === "live") {
                    liveCandles.removePriceLine(currentPriceLine);
                } else {
                    candles.removePriceLine(currentPriceLine);
                }
            } catch (err) {
                console.warn("chart_adapter: не вдалося прибрати current price line", err);
            }
            currentPriceLine = null;
            currentPriceLineOwner = null;
            currentPriceLineState = { price: null, color: null, owner: null };
        }

        function updateCurrentPriceLine() {
            const tickPrice = Number(lastTickPrice);
            const tickFresh =
                Number.isFinite(tickPrice) &&
                lastTickPriceAtMs > 0 &&
                Date.now() - lastTickPriceAtMs <= 10_000;

            const source = tickFresh ? null : lastLiveBar || lastBar;
            if (!source && !tickFresh) {
                clearCurrentPriceLine();
                return;
            }

            // Time SSOT: tick не рухає свічки/час, але може оновити price badge.
            // Для бейджа використовуємо основну серію (candles), щоб не створювати live-candle побічні ефекти.
            const owner = tickFresh ? "candles" : lastLiveBar ? "live" : "candles";
            const price = tickFresh ? tickPrice : Number(source.close);
            if (!Number.isFinite(price)) {
                clearCurrentPriceLine();
                return;
            }

            // Колір бейджа: якщо є попередня закрита свічка — порівнюємо з нею;
            // інакше — по open/close поточного бару.
            let ref = null;
            if (lastBar && lastLiveBar) {
                const refPrice = Number(lastBar.close);
                if (Number.isFinite(refPrice)) {
                    ref = refPrice;
                }
            }
            if (ref == null) {
                const open = Number(source?.open);
                if (Number.isFinite(open)) {
                    ref = open;
                }
            }
            const isUp = ref == null ? true : price >= ref;
            // Менш яскравий бейдж на шкалі (приглушуємо колір).
            const colorBase = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
            const color = hexToRgba(colorBase, 0.6);

            const stateUnchanged =
                currentPriceLineState.price === price &&
                currentPriceLineState.color === color &&
                currentPriceLineState.owner === owner;
            if (stateUnchanged) {
                return;
            }

            // Важливо для live: не пересоздаємо price line на кожен тик.
            // Якщо власник не змінився, намагаємось оновити існуючий через applyOptions().
            if (currentPriceLine && currentPriceLineOwner === owner && typeof currentPriceLine.applyOptions === "function") {
                try {
                    currentPriceLine.applyOptions({
                        price,
                        color,
                    });
                    currentPriceLineState = { price, color, owner };
                    return;
                } catch (_e) {
                    // fallback нижче
                }
            }

            // Якщо власник змінився або applyOptions недоступний — пересоздаємо.
            clearCurrentPriceLine();
            const series = owner === "live" ? liveCandles : candles;
            currentPriceLine = series.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                // Щоб не перевантажувати графік: лишаємо компактний маркер на шкалі,
                // без додаткової горизонтальної лінії на полі.
                lineVisible: false,
                // Без title -> компактніший бейдж на шкалі.
            });
            currentPriceLineOwner = owner;
            currentPriceLineState = { price, color, owner };
        }

        function setLastPriceFromTick(price) {
            const p = Number(price);
            if (!Number.isFinite(p)) {
                return;
            }
            lastTickPrice = p;
            lastTickPriceAtMs = Date.now();
            updateCurrentPriceLine();
        }

        function clearEvents() {
            if (eventMarkers.length) {
                eventMarkers = [];
                applyCombinedMarkers();
            }
            if (executionMarkers.length) {
                executionMarkers = [];
                applyCombinedMarkers();
            }
            lastExecutionEvents = [];
            executionEventsByTime = new Map();
            clearStructureTriangles();
        }

        function clearPools() {
            poolLines.forEach((line) => candles.removePriceLine(line));
            poolLines = [];

            poolSegments.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool segment", err);
                }
            });
            poolSegments = [];
        }

        function clearPoolsSelectedV1() {
            for (const node of poolsV1ById.values()) {
                try {
                    if (node?.topSeg) chart.removeSeries(node.topSeg);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 top segment", err);
                }
                try {
                    if (node?.botSeg) chart.removeSeries(node.botSeg);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 bot segment", err);
                }
                try {
                    if (node?.fillBand) chart.removeSeries(node.fillBand);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 fill band", err);
                }
            }
            poolsV1ById = new Map();
        }

        function setPoolsSelectedV1(pools) {
            const desired = new Map();

            const rows = Array.isArray(pools) ? pools : [];
            let invalidId = 0;
            let invalidRange = 0;
            let duplicateId = 0;

            for (const raw of rows) {
                if (!raw || typeof raw !== "object") continue;
                const idRaw = raw.id ?? raw.stable_id;
                const id = String(idRaw || "").trim();
                if (!id) {
                    invalidId += 1;
                    continue;
                }
                if (desired.has(id)) {
                    duplicateId += 1;
                    continue;
                }

                const tf = String(raw.tf || raw.timeframe || "").toLowerCase();
                const status = String(raw.status ?? raw.state ?? "");
                const label = String(raw.label ?? "");
                const kind = String(raw.kind ?? raw.type ?? "");
                const side = String(raw.side ?? raw.direction ?? "");

                const a = Number(raw.top ?? raw.hi ?? raw.high ?? raw.max);
                const b = Number(raw.bot ?? raw.lo ?? raw.low ?? raw.min);
                if (!Number.isFinite(a) || !Number.isFinite(b)) {
                    invalidRange += 1;
                    continue;
                }
                const top = Math.max(a, b);
                const bot = Math.min(a, b);

                desired.set(id, {
                    id,
                    tf,
                    top,
                    bot,
                    status,
                    label,
                    kind,
                    side,
                });
            }

            if (invalidId) warnPoolsV1Input("порожній/невалідний id", invalidId);
            if (duplicateId) warnPoolsV1Input("дубльований id", duplicateId);
            if (invalidRange) warnPoolsV1Input("невалідний діапазон top/bot", invalidRange);

            const selfcheckEligible =
                AUTOSCALE_SELFCHECK_ENABLED && !priceScaleState.manualRange && Boolean(lastBarsSignature);
            const selfcheckSnapshot = selfcheckEligible
                ? {
                      barsSig: lastBarsSignature,
                      poolsCount: desired.size,
                      beforeAuto: priceScaleState.lastAutoRange ? { ...priceScaleState.lastAutoRange } : null,
                  }
                : null;

            // Remove
            for (const [id, node] of poolsV1ById.entries()) {
                if (desired.has(id)) continue;
                try {
                    if (node?.topSeg) chart.removeSeries(node.topSeg);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 top segment", err);
                }
                try {
                    if (node?.botSeg) chart.removeSeries(node.botSeg);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 bot segment", err);
                }
                try {
                    if (node?.fillBand) chart.removeSeries(node.fillBand);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 fill band", err);
                }
                try {
                    if (node?.labelLine) candles.removePriceLine(node.labelLine);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати pool_v1 label", err);
                }
                poolsV1ById.delete(id);
            }

            const range = pickPoolsV1SegmentRange(chartTimeRange, lastBar, lastLiveBar);

            // ВАЖЛИВО (layering): створення серій має бути детермінованим.
            // lightweight-charts малює останні створені серії "вище", тому створюємо 4h→1h→5m.
            const upsertOrder = Array.from(desired.values()).sort((a, b) => {
                const ar = poolsV1TfRank(a.tf);
                const br = poolsV1TfRank(b.tf);
                if (ar !== br) return ar - br; // 4h(10) first, 5m(30) last
                return String(a.id).localeCompare(String(b.id));
            });

            // Upsert
            for (const next of upsertOrder) {
                const id = next.id;
                const prevNode = poolsV1ById.get(id) || null;
                const prev = prevNode?.last || null;

                const unchanged =
                    prev &&
                    prev.tf === next.tf &&
                    prev.top === next.top &&
                    prev.bot === next.bot &&
                    prev.status === next.status &&
                    prev.label === next.label &&
                    prev.kind === next.kind &&
                    prev.side === next.side;

                const node =
                    prevNode ||
                    {
                        topSeg: null,
                        botSeg: null,
                        fillBand: null,
                        fillBot: null,
                        labelLine: null,
                        last: null,
                    };
                const style = poolsV1Style(next.tf, next.status);

                // Якщо додаємо fill-band до старого node — пересоздаємо edges,
                // щоб edges гарантовано були "вище" за fill (order-sensitive у lightweight-charts).
                if (prevNode && !node.fillBand && (node.topSeg || node.botSeg)) {
                    try {
                        if (node.topSeg) chart.removeSeries(node.topSeg);
                    } catch (_e) {
                        // noop
                    }
                    try {
                        if (node.botSeg) chart.removeSeries(node.botSeg);
                    } catch (_e) {
                        // noop
                    }
                    node.topSeg = null;
                    node.botSeg = null;
                }

                const ensureFillBand = (existing, botPrice, fillRgba) => {
                    const bot = Number(botPrice);
                    if (!Number.isFinite(bot)) return null;
                    const sameBase = existing && Number.isFinite(node.fillBot) && node.fillBot === bot;
                    if (sameBase) {
                        try {
                            existing.applyOptions({
                                topFillColor1: fillRgba,
                                topFillColor2: fillRgba,
                            });
                        } catch (_e) {
                            // noop
                        }
                        return existing;
                    }

                    if (existing) {
                        try {
                            chart.removeSeries(existing);
                        } catch (_e) {
                            // noop
                        }
                    }

                    const s = chart.addBaselineSeries({
                        baseValue: { type: "price", price: bot },
                        autoscaleInfoProvider: () => null,
                        baseLineVisible: false,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                        lineWidth: 0,
                        topFillColor1: fillRgba,
                        topFillColor2: fillRgba,
                        bottomFillColor1: "rgba(0,0,0,0)",
                        bottomFillColor2: "rgba(0,0,0,0)",
                        topLineColor: "rgba(0,0,0,0)",
                        bottomLineColor: "rgba(0,0,0,0)",
                    });
                    s.setData([]);
                    node.fillBot = bot;
                    return s;
                };

                node.fillBand = ensureFillBand(node.fillBand, next.bot, style.fill.fillRgba);

                const ensureSeg = (existing) => {
                    if (existing) return existing;
                    const s = chart.addLineSeries({
                        color: style.edge.color,
                        lineWidth: style.edge.lineWidth,
                        lineStyle: style.edge.lineStyle,
                        lastValueVisible: false,
                        priceLineVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null,
                    });
                    s.setData([]);
                    return s;
                };

                node.topSeg = ensureSeg(node.topSeg);
                node.botSeg = ensureSeg(node.botSeg);
                try {
                    node.topSeg.applyOptions(style.edge);
                    node.botSeg.applyOptions(style.edge);
                } catch (_e) {
                    // noop
                }

                if (range) {
                    if (node.fillBand) {
                        node.fillBand.setData([
                            { time: range.from, value: next.top },
                            { time: range.to, value: next.top },
                        ]);
                    }
                    node.topSeg.setData([
                        { time: range.from, value: next.top },
                        { time: range.to, value: next.top },
                    ]);
                    node.botSeg.setData([
                        { time: range.from, value: next.bot },
                        { time: range.to, value: next.bot },
                    ]);
                } else {
                    if (node.fillBand) node.fillBand.setData([]);
                    node.topSeg.setData([]);
                    node.botSeg.setData([]);
                }

                // ВАЖЛИВО (P4.2): пули не створюють правих axis-label бейджів.
                // Підписи пулів рендеримо лише зліва через DOM overlay (setPoolsLabelsLeftV1).
                node.labelLine = null;

                node.last = next;
                poolsV1ById.set(id, node);
            }

            // Autoscale self-check (тільки діагностика): pools_v1 не має впливати на autoscale.
            // Перевіряємо зміни lastAutoRange після upsert, лише якщо свічки не мінялись.
            if (selfcheckSnapshot && !poolsV1AutoscaleSelfcheckPending) {
                const beforeAuto = selfcheckSnapshot.beforeAuto;
                if (beforeAuto && Number.isFinite(beforeAuto.min) && Number.isFinite(beforeAuto.max)) {
                    const key = `bars:${selfcheckSnapshot.barsSig}|pools:${selfcheckSnapshot.poolsCount}`;
                    poolsV1AutoscaleSelfcheckPending = true;
                    setTimeout(() => {
                        poolsV1AutoscaleSelfcheckPending = false;
                        if (!AUTOSCALE_SELFCHECK_ENABLED) return;
                        if (priceScaleState.manualRange) return;
                        if (selfcheckSnapshot.barsSig !== lastBarsSignature) return;

                        const afterAuto = priceScaleState.lastAutoRange ? { ...priceScaleState.lastAutoRange } : null;
                        if (!afterAuto || !Number.isFinite(afterAuto.min) || !Number.isFinite(afterAuto.max)) {
                            return;
                        }

                        const span = Math.max(1e-9, Math.abs(beforeAuto.max - beforeAuto.min));
                        const tolAbs = Math.max(1e-6, span * 1e-6);
                        const dMin = Math.abs(afterAuto.min - beforeAuto.min);
                        const dMax = Math.abs(afterAuto.max - beforeAuto.max);
                        if (dMin > tolAbs || dMax > tolAbs) {
                            warnPoolsV1AutoscaleSelfcheck({
                                key,
                                barsSig: selfcheckSnapshot.barsSig,
                                poolsCount: selfcheckSnapshot.poolsCount,
                                tolAbs,
                                before: beforeAuto,
                                after: afterAuto,
                                dMin,
                                dMax,
                            });
                        }
                    }, 0);
                }
            }
        }

        function clearLevelsSelectedV1() {
            selectedLevelLines.forEach((line) => candles.removePriceLine(line));
            selectedLevelLines = [];

            selectedLevelSegments.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати level segment", err);
                }
            });
            selectedLevelSegments = [];

            selectedLevelBands.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати level band", err);
                }
            });
            selectedLevelBands = [];
        }

        function clearRanges() {
            rangeAreas.forEach((series) => chart.removeSeries(series));
            rangeAreas = [];
        }

        function clearZones() {
            zoneLines.forEach((line) => candles.removePriceLine(line));
            zoneLines = [];
            zoneAreas.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати zone box", err);
                }
            });
            zoneAreas = [];

            zoneBorders.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати рамку зони", err);
                }
            });
            zoneBorders = [];
        }

        function clearStructureTriangles() {
            if (structureTriangles.length) {
                structureTriangles.forEach((series) => {
                    try {
                        chart.removeSeries(series);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати трикутник", err);
                    }
                });
                structureTriangles = [];
            }
            if (structureTriangleLabels.length) {
                structureTriangleLabels.forEach((line) => {
                    try {
                        candles.removePriceLine(line);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати структуральний label", err);
                    }
                });
                structureTriangleLabels = [];
            }
        }

        function clearOteOverlays() {
            if (!oteOverlays.length) {
                return;
            }
            oteOverlays.forEach((overlay) => {
                overlay.series?.forEach((series) => {
                    try {
                        chart.removeSeries(series);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати OTE серію", err);
                    }
                });
                if (overlay.priceLine) {
                    try {
                        candles.removePriceLine(overlay.priceLine);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати OTE label", err);
                    }
                }
            });
            oteOverlays = [];
        }

        function clamp01(value) {
            const num = Number(value);
            if (!Number.isFinite(num)) return 0;
            return Math.max(0, Math.min(1, num));
        }

        function pickRefPrice() {
            const liveClose = Number(lastLiveBar?.close);
            if (Number.isFinite(liveClose)) return liveClose;
            const close = Number(lastBar?.close);
            if (Number.isFinite(close)) return close;
            const open = Number(lastBar?.open);
            if (Number.isFinite(open)) return open;
            return null;
        }

        function estimatePriceWindowAbs(refPrice) {
            const ref = Number(refPrice);
            const refComponent = Number.isFinite(ref) ? Math.abs(ref) * 0.0015 : 0;

            const bars = Array.isArray(lastCandleDataset) ? lastCandleDataset : [];
            const tail = bars.slice(Math.max(0, bars.length - 80));
            let maxHigh = null;
            let minLow = null;
            for (const bar of tail) {
                const h = Number(bar?.high);
                const l = Number(bar?.low);
                if (!Number.isFinite(h) || !Number.isFinite(l)) continue;
                maxHigh = maxHigh == null ? h : Math.max(maxHigh, h);
                minLow = minLow == null ? l : Math.min(minLow, l);
            }
            const n = Math.max(1, tail.length);
            const span = maxHigh != null && minLow != null ? Math.max(0, maxHigh - minLow) : 0;
            const atrLike = (span / n) * 14;
            const volComponent = atrLike * 0.6;
            return Math.max(refComponent, volComponent, 0.5);
        }

        function estimateMergeTolAbs(refPrice, priceWindowAbs) {
            const ref = Number(refPrice);
            const refComponent = Number.isFinite(ref) ? Math.abs(ref) * 0.00025 : 0;
            const windowComponent = Number(priceWindowAbs) * 0.08;
            return Math.max(refComponent, windowComponent, 0.2);
        }

        function roleWeight(role) {
            const r = String(role || "").toUpperCase();
            if (r === "PRIMARY") return 1.0;
            if (r === "COUNTER") return 0.6;
            return 0.5;
        }



        function selectZonesForRender(zones) {
            const refPrice = pickRefPrice();
            if (!Number.isFinite(Number(refPrice))) {
                return { zones: [], mergeTolAbs: 0.2 };
            }
            const priceWindowAbs = estimatePriceWindowAbs(refPrice);
            const mergeTolAbs = estimateMergeTolAbs(refPrice, priceWindowAbs);
            const ref = Number(refPrice);
            const focusMin = ref - priceWindowAbs * 1.2;
            const focusMax = ref + priceWindowAbs * 1.2;

            const candidates = (Array.isArray(zones) ? zones : [])
                .map((z) => {
                    const min = Number(z?.min ?? z?.price_min ?? z?.ote_min);
                    const max = Number(z?.max ?? z?.price_max ?? z?.ote_max);
                    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
                    const zMin = Math.min(min, max);
                    const zMax = Math.max(min, max);
                    if (zMax < focusMin || zMin > focusMax) return null;
                    const center = (zMin + zMax) / 2;
                    const role = String(z?.role || "").toUpperCase();
                    const w = roleWeight(role);
                    const distNorm = Math.abs(center - ref) / Math.max(1e-9, priceWindowAbs);

                    // Пріоритет POI: якщо бекенд позначив зону як poi_type/POI — піднімаємо.
                    const poiType = String(z?.poi_type || "").toUpperCase();
                    const label = String(z?.label || z?.type || z?.role || "").toUpperCase();
                    const isPoi = Boolean(poiType) || label.includes("POI");
                    const poiBoost = isPoi ? 1.75 : 1.0;
                    const score = (w * poiBoost) / (1 + Math.min(6, distNorm));
                    return {
                        ...z,
                        min: zMin,
                        max: zMax,
                        _center: center,
                        _score: score,
                        _isPoi: isPoi,
                    };
                })
                .filter(Boolean)
                .sort((a, b) => b._score - a._score);

            const picked = [];
            for (const z of candidates) {
                if (picked.length >= 3) break;
                // Для POI менш агресивно мерджимо центри — краще показати POI, ніж приховати.
                const tol = z._isPoi ? mergeTolAbs * 0.65 : mergeTolAbs;
                if (picked.some((p) => Math.abs(Number(p._center) - Number(z._center)) <= tol)) {
                    continue;
                }
                picked.push(z);
            }

            const normalized = picked.map((z) => {
                const thin = Math.abs(Number(z.max) - Number(z.min)) < mergeTolAbs;
                if (!thin) return z;
                const center = Number(z._center);
                return {
                    ...z,
                    min: center,
                    max: center,
                };
            });

            return { zones: normalized, mergeTolAbs };
        }

        function setEvents(events) {
            clearEvents();
            if (!Array.isArray(events) || !events.length) {
                return;
            }

            const getViewTfSeconds = () => {
                if (Number.isFinite(viewTimeframeSecOverride) && viewTimeframeSecOverride > 0) {
                    return Math.floor(viewTimeframeSecOverride);
                }
                return Math.max(1, Math.floor(barTimeSpanSeconds) || 60);
            };

            const isHtfView = () => {
                const tf = getViewTfSeconds();
                return Number.isFinite(tf) && tf >= 3600;
            };

            const toUnixSeconds = (value) => {
                const num = Number(value);
                if (!Number.isFinite(num)) return null;
                return Math.floor(num / (num > 1e12 ? 1000 : 1));
            };

            const snapToNearestBarTime = (timeSec) => {
                if (!Number.isFinite(timeSec)) return null;
                const times = lastCandleTimes;
                if (!Array.isArray(times) || times.length === 0) {
                    return Math.floor(timeSec);
                }

                const target = Math.floor(timeSec);
                let lo = 0;
                let hi = times.length;
                while (lo < hi) {
                    const mid = (lo + hi) >> 1;
                    const v = times[mid];
                    if (v < target) lo = mid + 1;
                    else hi = mid;
                }

                const rightIdx = Math.min(times.length - 1, lo);
                const leftIdx = Math.max(0, rightIdx - 1);
                const left = Number(times[leftIdx]);
                const right = Number(times[rightIdx]);
                const pick =
                    !Number.isFinite(left) ? right :
                        !Number.isFinite(right) ? left :
                            Math.abs(target - left) <= Math.abs(right - target) ? left : right;

                if (!Number.isFinite(pick)) {
                    return null;
                }

                const maxDiff = Math.max(1, Number(barTimeSpanSeconds) || 60) * 1.5;
                if (Math.abs(pick - target) > maxDiff) {
                    return null;
                }
                return Math.floor(pick);
            };

            withViewportPreserved(() => {
                const structureEvents = events.filter(isStructureEvent);
                if (!structureEvents.length) {
                    return;
                }
                const getEventTime = (evt) => {
                    const raw = evt.time ?? evt.ts ?? evt.timestamp ?? 0;
                    const sec = toUnixSeconds(raw);
                    return sec ?? 0;
                };
                const sortedEvents = structureEvents
                    .slice()
                    .sort((a, b) => getEventTime(a) - getEventTime(b));
                eventMarkers = sortedEvents
                    .map((evt) => {
                        const timeRaw = evt.time ?? evt.ts ?? evt.timestamp;
                        const time = toUnixSeconds(timeRaw);
                        if (!Number.isFinite(time)) return null;

                        const snapped = snapToNearestBarTime(time);
                        if (!Number.isFinite(snapped)) return null;

                        // HTF UX: на 1h/4h не підтягуємо 5m BOS/CHOCH як «горизонталі/шум».
                        // Тримаємо лише події, що близькі до реальної HTF-свічки (по часу).
                        if (isHtfView()) {
                            const viewSec = getViewTfSeconds();
                            // Допускаємо невелику похибку (секунди/десятки секунд), але не хвилини.
                            const tol = Math.min(180, Math.max(10, Math.round(viewSec * 0.02)));
                            if (Math.abs(snapped - time) > tol) {
                                return null;
                            }
                        }

                        const direction = (evt.direction || evt.dir || "").toUpperCase();
                        const kind = (evt.type || evt.event_type || "").toUpperCase();
                        const isChoch = kind.includes("CHOCH");
                        const isBos = !isChoch && kind.includes("BOS");

                        const isShort = direction === "SHORT";
                        const isLong = direction === "LONG";
                        // BOS: окремий (стабільний) стиль, щоб було читабельно.
                        // CHOCH лишаємо залежним від direction.
                        const color = isBos ? "#3b82f6" : isShort ? "#ef476f" : "#1ed760";

                        const arrowShape = isShort ? "arrowDown" : "arrowUp";
                        const shape = isChoch ? arrowShape : isBos ? "square" : arrowShape;
                        const text = isChoch ? "CHOCH" : isBos ? "BOS" : kind;
                        return {
                            time: snapped,
                            // На вимогу UX: лишаємо лише напис НАД свічкою.
                            position: "aboveBar",
                            color,
                            shape,
                            text,
                        };
                    })
                    .filter(Boolean);
                applyCombinedMarkers();
            });
        }

        function setExecutionEvents(events) {
            // ВАЖЛИВО (UX): execution-стрілочки мають бути "липкими".
            // Тобто якщо подія з’явилась на барі — стрілка лишається на цьому барі,
            // навіть якщо в наступному snapshot execution_events уже порожній.
            if (!Array.isArray(events) || !events.length) {
                return;
            }

            lastExecutionEvents = Array.isArray(events) ? events.slice(0) : [];

            const toUnixSeconds = (value) => {
                const num = Number(value);
                if (!Number.isFinite(num)) return null;
                return Math.floor(num / (num > 1e12 ? 1000 : 1));
            };

            const snapToNearestBarTime = (timeSec) => {
                if (!Number.isFinite(timeSec)) return null;
                const times = lastCandleTimes;
                if (!Array.isArray(times) || times.length === 0) {
                    return Math.floor(timeSec);
                }

                const target = Math.floor(timeSec);
                let lo = 0;
                let hi = times.length;
                while (lo < hi) {
                    const mid = (lo + hi) >> 1;
                    const v = times[mid];
                    if (v < target) lo = mid + 1;
                    else hi = mid;
                }

                const rightIdx = Math.min(times.length - 1, lo);
                const leftIdx = Math.max(0, rightIdx - 1);
                const left = Number(times[leftIdx]);
                const right = Number(times[rightIdx]);
                const pick =
                    !Number.isFinite(left) ? right :
                        !Number.isFinite(right) ? left :
                            Math.abs(target - left) <= Math.abs(right - target) ? left : right;

                if (!Number.isFinite(pick)) {
                    return null;
                }

                const maxDiff = Math.max(1, Number(barTimeSpanSeconds) || 60) * 1.5;
                if (Math.abs(pick - target) > maxDiff) {
                    return null;
                }
                return Math.floor(pick);
            };

            withViewportPreserved(() => {
                for (const evt of Array.isArray(events) ? events : []) {
                        const timeRaw = evt.time ?? evt.ts ?? evt.timestamp;
                        const time = toUnixSeconds(timeRaw);
                        if (!Number.isFinite(time)) continue;

                        const snapped = snapToNearestBarTime(time);
                        if (!Number.isFinite(snapped)) continue;

                        const direction = String(evt.direction || evt.dir || "").toUpperCase();
                        const isShort = direction === "SHORT";
                        const shape = isShort ? "arrowDown" : "arrowUp";
                        const position = isShort ? "aboveBar" : "belowBar";
                        const color = isShort ? CANDLE_COLORS.down : CANDLE_COLORS.up;

                        const rows = executionEventsByTime.get(snapped) || [];
                        rows.push(evt);
                        executionEventsByTime.set(snapped, rows);

                        // Якщо маркер на цей бар ще не створено — додаємо.
                        const exists = executionMarkers.some((m) => Number(m?.time) === snapped);
                        if (!exists) {
                            executionMarkers.push({
                                time: snapped,
                                position,
                                color,
                                shape,
                                // Вимога UX: на графіку лише стрілка (без лейблу).
                                text: "",
                            });
                        }
                }

                // Кеп по кількості маркерів: лишаємо найновіші за часом.
                if (executionMarkers.length > MAX_EXECUTION_MARKERS) {
                    executionMarkers.sort((a, b) => Number(a.time) - Number(b.time));
                    executionMarkers = executionMarkers.slice(-MAX_EXECUTION_MARKERS);

                    // Підчистимо map у відповідності до маркерів.
                    const keep = new Set(executionMarkers.map((m) => Number(m.time)));
                    for (const key of Array.from(executionEventsByTime.keys())) {
                        if (!keep.has(Number(key))) {
                            executionEventsByTime.delete(key);
                        }
                    }
                }

                applyCombinedMarkers();
            });
        }

        function setLiquidityPools(pools) {
            // Legacy path: вимкнено (4.1). Залишається лише як safe no-op,
            // щоб інші частини UI могли викликати setLiquidityPools([]) для очистки.
            clearPools();
            const now = Date.now();
            if (now - lastLegacyPoolsWarnMs > 5000) {
                lastLegacyPoolsWarnMs = now;
                console.warn("chart_adapter: legacy setLiquidityPools() викликано — ігнорую (P4: pools SSOT = pools_selected_v1)");
            }
            if (!LEGACY_POOL_LEVELS_ENABLED) {
                return;
            }
        }

        function setLevelsSelectedV1(levels, renderTf, tickSize) {
            clearLevelsSelectedV1();
            const tf = String(renderTf || "").toLowerCase();
            if (!Array.isArray(levels) || !levels.length || !tf) {
                return;
            }

            const toFiniteOrNull = (value) => {
                if (value === null || value === undefined) return null;
                const num = typeof value === "number" ? value : Number(value);
                return Number.isFinite(num) ? num : null;
            };

            const pickTickSize = () => {
                const ts = toFiniteOrNull(tickSize);
                if (ts !== null && ts > 0) {
                    return ts;
                }

                // Евристика (fallback): достатня для кластеризації бейджів на шкалі.
                const prices = [];
                for (const lvl of Array.isArray(levels) ? levels : []) {
                    const kind = String(lvl?.kind || "").toLowerCase();
                    if (kind === "band") {
                        const a = toFiniteOrNull(lvl?.top);
                        const b = toFiniteOrNull(lvl?.bot);
                        if (a !== null) prices.push(a);
                        if (b !== null) prices.push(b);
                    } else {
                        const p = toFiniteOrNull(lvl?.price);
                        if (p !== null) prices.push(p);
                    }
                }

                const abs = prices.length ? Math.max(...prices.map((p) => Math.abs(Number(p)) || 0)) : 0;
                if (abs >= 1000) return 0.1;
                if (abs >= 100) return 0.01;
                if (abs >= 10) return 0.001;
                return 0.0001;
            };

            const tick = pickTickSize();

            const groupRankForToken = (token) => {
                const t = String(token || "");
                // Стабільний порядок токенів у рядку: SESSION → DAILY → RANGE → EQ.
                if (t.startsWith("A") || t.startsWith("L") || t.startsWith("N")) return 10;
                if (t.startsWith("PD") || t.startsWith("ED")) return 20;
                if (t.startsWith("R")) return 30;
                if (t.startsWith("EQ")) return 40;
                return 90;
            };

            const tokenForLabel = (label) => {
                const v = String(label || "").toUpperCase();
                const map = {
                    // SESSION
                    ASH: "A↑",
                    ASL: "A↓",
                    ASM: "A~",
                    LSH: "L↑",
                    LSL: "L↓",
                    LSM: "L~",
                    NYH: "N↑",
                    NYL: "N↓",
                    NYM: "N~",
                    // DAILY
                    PDH: "PD↑",
                    PDL: "PD↓",
                    EDH: "ED↑",
                    EDL: "ED↓",
                    // RANGE
                    RANGE_H: "R↑",
                    RANGE_L: "R↓",
                };
                return map[v] || v;
            };

            const tokenForBandBoundary = (label, side) => {
                const v = String(label || "").toUpperCase();
                if (v === "EQH" || v === "EQL") {
                    return `${v}${side === "top" ? "↑" : "↓"}`;
                }
                return `${v || "BAND"}${side === "top" ? "↑" : "↓"}`;
            };

            const pickSegmentRange = () => {
                const span = Math.max(1, Number(barTimeSpanSeconds) || 60);
                const to = Number(chartTimeRange?.max ?? lastBar?.time);
                const fromMin = Number(chartTimeRange?.min);
                if (!Number.isFinite(to)) {
                    return null;
                }
                const segmentSpan = span * 140;
                const fromRaw = to - segmentSpan;
                const from = Number.isFinite(fromMin) ? Math.max(fromMin, fromRaw) : fromRaw;
                if (!(to > from)) {
                    return null;
                }
                return { from: Math.floor(from), to: Math.floor(to) };
            };

            const segmentRange = pickSegmentRange();

            // --- SSOT: стиль рівнів (колір/прозорість) ---
            // Принцип: колір = семантика (який це рівень), ↑/↓ = напрям (не колір).
            // Для кластерів (кілька токенів) беремо найпріоритетніший.
            const LEVEL_STYLE_V1 = {
                // DAILY (найважливіше)
                PD: { color: "rgba(112, 84, 17, 0.78)", priority: 100 }, // PDH/PDL або PD↑/PD↓
                ED: { color: "rgba(23, 89, 69, 0.78)", priority: 90 }, // EDH/EDL або ED↑/ED↓

                // SESSION
                N: { color: "rgba(244, 210, 97, 0.75)", priority: 80 }, // NYH/NYL або N↑/N↓
                L: { color: "rgba(129, 178, 154, 0.75)", priority: 70 }, // LSH/LSL або L↑/L↓
                A: { color: "rgba(77, 139, 194, 0.75)", priority: 60 }, // ASH/ASL або A↑/A↓

                // RANGE / EQ
                R: { color: "rgba(232, 139, 139, 0.65)", priority: 30 }, // RANGE_H/L або R↑/R↓

                DEFAULT: { color: "#577590", priority: 0 },
            };

            const tokensFromTitle = (titleRaw) => {
                const raw = String(titleRaw || "").toUpperCase().trim();
                if (!raw) return [];

                // Кластер: "N↓ ED↓ +2" → беремо токени, ігноруємо "+2".
                // Визначаємо кластер через наявність стрілок (↑/↓).
                if (raw.includes("↑") || raw.includes("↓")) {
                    return raw
                        .split(/\s+/)
                        .map((x) => x.trim())
                        .filter((x) => x && !x.startsWith("+"));
                }

                // Межі band інколи прокидаються як "... L" / "... H" (суфікс-сторона).
                // Це не "London" і не має впливати на семантику стилю.
                if (raw.endsWith(" L") || raw.endsWith(" H")) {
                    return [raw.slice(0, -2).trim()];
                }

                return [raw];
            };

            const styleKeyForToken = (tokRaw) => {
                const t0 = String(tokRaw || "").toUpperCase();
                const t = t0.replace(/[↑↓]/g, "").replace(/[^A-Z0-9_]/g, "");

                // Кластери (скорочені токени)
                if (t.startsWith("PD")) return "PD";
                if (t.startsWith("ED")) return "ED";
                if (t.startsWith("N")) return "N";
                if (t.startsWith("L")) return "L";
                if (t.startsWith("A")) return "A";
                if (t.startsWith("R")) return "R";

                // Сирі лейбли
                if (t === "PDH" || t === "PDL") return "PD";
                if (t === "EDH" || t === "EDL") return "ED";
                if (t === "NYH" || t === "NYL") return "N";
                if (t === "LSH" || t === "LSL") return "L";
                if (t === "ASH" || t === "ASL") return "A";
                if (t === "RANGE_H" || t === "RANGE_L") return "R";

                return "DEFAULT";
            };

            const lineColorForTitle = (titleRaw) => {
                const toks = tokensFromTitle(titleRaw);
                let best = LEVEL_STYLE_V1.DEFAULT;
                for (const tok of toks) {
                    const key = styleKeyForToken(tok);
                    const st = LEVEL_STYLE_V1[key] || LEVEL_STYLE_V1.DEFAULT;
                    if (st.priority >= best.priority) best = st;
                }
                return best.color;
            };

            const renderLine = (price, label) => {
                const p = Number(price);
                if (!Number.isFinite(p)) return;

                const title = String(label || "LVL").toUpperCase();
                const color = lineColorForTitle(title);

                const badge = candles.createPriceLine({
                    price: p,
                    color,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    lineVisible: false,
                    title,
                });
                selectedLevelLines.push(badge);

                if (segmentRange) {
                    const seg = chart.addLineSeries({
                        color,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        lastValueVisible: false,
                        priceLineVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null,
                    });
                    seg.setData([
                        { time: segmentRange.from, value: p },
                        { time: segmentRange.to, value: p },
                    ]);
                    selectedLevelSegments.push(seg);
                }
            };

            const renderBand = (top, bot, label) => {
                const toFiniteOrNull = (value) => {
                    if (value === null || value === undefined) return null;
                    const num = typeof value === "number" ? value : Number(value);
                    return Number.isFinite(num) ? num : null;
                };

                const a = toFiniteOrNull(top);
                const b = toFiniteOrNull(bot);
                if (a === null || b === null) return;

                const zMin = Math.min(a, b);
                const zMax = Math.max(a, b);
                const title = String(label || "BAND").toUpperCase();

                // UX: band як "коридор" показуємо без заливки (щоб не перекривати графік).
                // Малюємо 2 лінії (нижня/верхня межа) з тим самим label.
                renderLine(zMin, `${title} L`);
                renderLine(zMax, `${title} H`);
            };

            const buildSelectedLineClustersV1 = (rows, ownerTf) => {
                const tfLocal = String(ownerTf || "").toLowerCase();
                const tickLocal = toFiniteOrNull(tick) || 0.01;

                const normalizeStyleHint = (value) => {
                    const v = String(value || "").toLowerCase().trim();
                    if (v === "solid") return "solid";
                    if (v === "dashed") return "dashed";
                    return null;
                };

                const priceKey = (price) => {
                    const p = toFiniteOrNull(price);
                    if (p === null) return null;
                    const snapped = Math.round(p / tickLocal) * tickLocal;
                    return Number(snapped.toFixed(12));
                };

                const flat = [];
                for (const lvl of Array.isArray(rows) ? rows : []) {
                    const kind = String(lvl?.kind || "").toLowerCase();
                    const label = String(lvl?.label || "");
                    const styleHint = normalizeStyleHint(lvl?.style_hint);
                    if (kind === "band") {
                        const top = toFiniteOrNull(lvl?.top);
                        const bot = toFiniteOrNull(lvl?.bot);
                        if (top !== null) {
                            const tok = tokenForBandBoundary(label, "top");
                            flat.push({ price: top, token: tok, group: groupRankForToken(tok), styleHint });
                        }
                        if (bot !== null) {
                            const tok = tokenForBandBoundary(label, "bot");
                            flat.push({ price: bot, token: tok, group: groupRankForToken(tok), styleHint });
                        }
                        continue;
                    }

                    const p = toFiniteOrNull(lvl?.price);
                    if (p === null) continue;
                    const tok = tokenForLabel(label);
                    flat.push({ price: p, token: tok, group: groupRankForToken(tok), styleHint });
                }

                const clustersByPrice = new Map();
                for (const row of flat) {
                    const key = priceKey(row.price);
                    if (key === null) continue;
                    const cur = clustersByPrice.get(key) || { price: key, tokens: [], styleHints: [] };
                    cur.tokens.push({ token: row.token, group: row.group });
                    if (row.styleHint) {
                        cur.styleHints.push(String(row.styleHint));
                    }
                    clustersByPrice.set(key, cur);
                }

                const tokensMax = 3;
                const finalizeTitle = (tokens) => {
                    const uniq = new Map();
                    for (const t of tokens) {
                        const k = String(t.token);
                        if (!uniq.has(k)) {
                            uniq.set(k, t.group);
                        }
                    }
                    const ordered = Array.from(uniq.entries())
                        .map(([token, group]) => ({ token, group }))
                        .sort((a, b) => (a.group - b.group) || a.token.localeCompare(b.token));
                    const list = ordered.map((x) => x.token);
                    if (list.length <= tokensMax) {
                        return { title: list.join(" "), tokensFinal: list, tokensMax };
                    }
                    const head = list.slice(0, tokensMax);
                    const rest = list.length - tokensMax;
                    return { title: `${head.join(" ")} +${rest}`, tokensFinal: list, tokensMax };
                };

                const clusters = Array.from(clustersByPrice.values())
                    .map((c) => {
                        const { title, tokensFinal, tokensMax: max } = finalizeTitle(c.tokens);
                        const hints = Array.isArray(c.styleHints) ? c.styleHints : [];
                        // Backward compatible default: якщо style_hint відсутній — SOLID.
                        // Dashed використовуємо лише коли бекенд явно це просить.
                        const styleHint = hints.includes("dashed") ? "dashed" : "solid";
                        return {
                            price: c.price,
                            title,
                            tokensOriginal: c.tokens.map((t) => t.token),
                            tokensFinal,
                            tokensMax: max,
                            styleHint,
                        };
                    })
                    // Стабільний порядок (не "стрибає"): по ціні згори вниз.
                    .sort((a, b) => Number(b.price) - Number(a.price));

                return clusters;
            };

            const byTf = levels.filter((lvl) => String(lvl?.owner_tf || "").toLowerCase() === tf);

            // Safety net: dedup по id (якщо раптом в payload є дублікати).
            const seenIds = new Set();
            const deduped = [];
            for (const lvl of byTf) {
                const id = String(lvl?.id || "");
                if (!id) continue;
                if (seenIds.has(id)) continue;
                seenIds.add(id);
                deduped.push(lvl);
            }

            const rawLines = [];
            const rawBands = [];
            for (const lvl of deduped) {
                const kind = String(lvl?.kind || "").toLowerCase();
                if (kind === "band") rawBands.push(lvl);
                else rawLines.push(lvl);
            }

            // Safety-net caps (4.1): бекенд уже капить selected; UI лише попереджає.
            if (tf === "5m" && (rawLines.length > 3 || rawBands.length > 2)) {
                console.warn(
                    `levels_selected_v1_render_cap: tf=5m lines=${rawLines.length} bands=${rawBands.length} -> lines=3 bands=2`,
                );
            }

            // Варіант A: кластеризація “1 ціна → 1 бейдж” + короткі токени.
            let clusters = buildSelectedLineClustersV1(deduped, tf);

            // Кеп застосовуємо ПІСЛЯ кластеризації (зменшує кількість лейблів).
            if (tf === "5m") {
                const cap = 3;
                if (clusters.length > cap) {
                    const now = Date.now();
                    const key = `${tf}|${clusters.length}|${cap}`;
                    if (key !== lastLevelsSelectedV1CapWarn.key || now - lastLevelsSelectedV1CapWarn.ms > 30000) {
                        lastLevelsSelectedV1CapWarn = { key, ms: now };
                        console.debug(
                            `levels_selected_v1_render_cap: tf=5m clusters=${clusters.length} -> clusters=${cap}`,
                        );
                    }
                    clusters = clusters.slice(0, cap);
                }
            }

            for (const c of clusters) {
                const title = String(c.title || "LVL");
                const baseColor = lineColorForTitle(title);
                const p = Number(c.price);
                if (!Number.isFinite(p)) continue;

                const styleHint = String(c.styleHint || "").toLowerCase();
                const lineStyle = styleHint === "dashed" ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid;

                // Preview (dashed) — робимо “тихішим” без зміни семантики кольору.
                const softenRgbaAlpha = (color, maxAlpha) => {
                    const s = String(color || "").trim();
                    const m = s.match(/^rgba\((\s*\d+\s*),(\s*\d+\s*),(\s*\d+\s*),(\s*\d*\.?\d+\s*)\)$/i);
                    if (!m) return s;
                    const r = Number(m[1]);
                    const g = Number(m[2]);
                    const b = Number(m[3]);
                    const a = Number(m[4]);
                    if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b) || !Number.isFinite(a)) return s;
                    const nextA = Math.min(a, maxAlpha);
                    return `rgba(${r}, ${g}, ${b}, ${nextA})`;
                };

                const color = styleHint === "dashed" ? softenRgbaAlpha(baseColor, 0.42) : baseColor;

                const badge = candles.createPriceLine({
                    price: p,
                    color,
                    lineWidth: 1,
                    lineStyle,
                    axisLabelVisible: true,
                    lineVisible: false,
                    title,
                });
                selectedLevelLines.push(badge);

                if (segmentRange) {
                    const seg = chart.addLineSeries({
                        color,
                        lineWidth: 1,
                        lineStyle,
                        lastValueVisible: false,
                        priceLineVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null,
                    });
                    seg.setData([
                        { time: segmentRange.from, value: p },
                        { time: segmentRange.to, value: p },
                    ]);
                    selectedLevelSegments.push(seg);
                }
            }

            debugLevelsSelectedV1Rendered(clusters.length, 0, tf);
        }

        function setRanges(ranges) {
            clearRanges();
            if (!Array.isArray(ranges) || !ranges.length) {
                return;
            }
            ranges.forEach((range) => {
                const minPrice = Number(range.min || range.price_min);
                const maxPrice = Number(range.max || range.price_max);
                const from = Number(range.start_time || range.from || range.time_start);
                const to = Number(range.end_time || range.to || range.time_end);
                if (
                    !Number.isFinite(minPrice) ||
                    !Number.isFinite(maxPrice) ||
                    !Number.isFinite(from) ||
                    !Number.isFinite(to)
                ) {
                    return;
                }
                // AreaSeries заливає до baseline=0, тож для «box» між min↔max використовуємо BaselineSeries.
                const band = chart.addBaselineSeries({
                    baseValue: { type: "price", price: minPrice },
                    topFillColor1: "rgba(59, 130, 246, 0.18)",
                    topFillColor2: "rgba(59, 130, 246, 0.06)",
                    bottomFillColor1: "rgba(59, 130, 246, 0.18)",
                    bottomFillColor2: "rgba(59, 130, 246, 0.06)",
                    lineWidth: 0,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                band.setData([
                    { time: Math.floor(from), value: maxPrice },
                    { time: Math.floor(to), value: maxPrice },
                ]);
                rangeAreas.push(band);
            });
        }

        function setBandZones(zones, colors) {
            clearZones();
            if (!Array.isArray(zones) || !zones.length) {
                return;
            }
            zones.forEach((zone) => {
                const minPrice = Number(zone.min || zone.price_min || zone.ote_min);
                const maxPrice = Number(zone.max || zone.price_max || zone.ote_max);
                if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) {
                    return;
                }
                const label = zone.label || zone.type || zone.role || "ZONE";

                // Якщо зона надто тонка — малюємо як один рівень (центр), а не 2 лінії.
                if (Math.abs(maxPrice - minPrice) < 1e-9) {
                    const line = candles.createPriceLine({
                        price: minPrice,
                        color: colors.max,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: false,
                        title: `${label}`,
                    });
                    zoneLines.push(line);
                    return;
                }

                const lineMin = candles.createPriceLine({
                    price: minPrice,
                    color: colors.min,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: false,
                    title: `${label} min`,
                });
                const lineMax = candles.createPriceLine({
                    price: maxPrice,
                    color: colors.max,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: false,
                    title: `${label} max`,
                });
                zoneLines.push(lineMin, lineMax);
            });
        }

        function setZoneBoxes(zones) {
            // UX-правило: box від origin_time до "зараз" або до неактуальності.
            // "Зараз" = останній відомий бар на графіку (історичний або live).
            if (!Array.isArray(zones) || !zones.length) {
                return;
            }

            // Підтримуємо 2 формати:
            // 1) старий: zones[]
            // 2) новий: clusters[] = { rep, members, start_time }
            const items = (() => {
                const first = zones[0];
                if (first && typeof first === "object" && first.rep) {
                    return zones;
                }
                return zones.map((z) => ({ rep: z, members: [z], start_time: Number(z?.origin_time) }));
            })();

            const nowRaw = Number(lastLiveBar?.time);
            const nowAlt = Number(lastBar?.time);
            const now = Number.isFinite(nowRaw) ? Math.floor(nowRaw) : Number.isFinite(nowAlt) ? Math.floor(nowAlt) : null;
            if (!Number.isFinite(now)) {
                return;
            }

            const firstTime = Array.isArray(lastCandleTimes) && lastCandleTimes.length ? Number(lastCandleTimes[0]) : null;
            const fallbackFrom = Number.isFinite(firstTime) ? Math.floor(firstTime) : Math.max(0, now - Math.max(1, Number(barTimeSpanSeconds) || 60) * 200);

            items.forEach((cluster, zoneIndex) => {
                const zone = cluster?.rep;
                const minPrice = Number(zone.min || zone.price_min || zone.ote_min);
                const maxPrice = Number(zone.max || zone.price_max || zone.ote_max);
                if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) {
                    return;
                }

                const zMin = Math.min(minPrice, maxPrice);
                const zMax = Math.max(minPrice, maxPrice);

                const label = String(zone.label || zone.type || zone.role || "ZONE");
                const dir = String(zone.direction || "").toUpperCase();
                const poiType = String(zone.poi_type || "").toUpperCase();
                const zTypeRaw = String(zone.type || "").toUpperCase();
                const kind = poiType || zTypeRaw || label.toUpperCase();
                const isFvg = kind.includes("FVG") || kind.includes("IMBALANCE");
                const isBreaker = kind.includes("BREAKER");
                const isOb =
                    (kind.includes("ORDER") && kind.includes("BLOCK")) ||
                    kind === "OB" ||
                    kind.includes("OB");
                const isPoi = Boolean(poiType) || label.toUpperCase().includes("POI");

                // origin_time очікуємо як unix seconds (з app.js safeUnixSeconds).
                const origin = Number(zone.origin_time);
                const clusterStart = Number(cluster?.start_time);
                const invalidated = Number(zone.invalidated_time);
                const fromRaw = Number.isFinite(clusterStart) ? clusterStart : origin;
                const from = Number.isFinite(fromRaw) ? Math.floor(fromRaw) : fallbackFrom;
                const to = Number.isFinite(invalidated) ? Math.floor(invalidated) : Math.floor(now);
                if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) {
                    return;
                }

                // Візуальна семантика (без введення нових кольорів):
                // - OB: LONG/SHORT (зелений/червоний) і більш щільний
                // - Breaker: синій
                // - FVG: жовтий і більш прозорий
                // Також #1 (найвищий пріоритет) робимо трохи більш контрастним.
                let baseAlpha = isFvg ? 0.07 : isBreaker ? 0.11 : isOb ? 0.14 : 0.09;
                if (zoneIndex === 0) {
                    baseAlpha = Math.min(0.18, baseAlpha * 1.35);
                }
                const baseAlpha2 = Math.max(0.03, baseAlpha * 0.45);

                const palette = (() => {
                    if (isBreaker) {
                        return { r: 59, g: 130, b: 246 }; // blue
                    }
                    if (isFvg) {
                        return { r: 255, g: 209, b: 102 }; // yellow
                    }
                    // OB/інші: напрям
                    if (dir === "SHORT") {
                        return { r: 248, g: 113, b: 113 }; // red
                    }
                    return { r: 34, g: 197, b: 94 }; // green
                })();

                let fillTop1 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${baseAlpha})`;
                let fillTop2 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${baseAlpha2})`;
                let fillBottom1 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${baseAlpha})`;
                let fillBottom2 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${baseAlpha2})`;

                // Якщо це не POI — робимо ще менш помітним, щоб не маскувало POI.
                if (!isPoi) {
                    fillTop1 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${Math.max(0.03, baseAlpha * 0.7)})`;
                    fillTop2 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${Math.max(0.02, baseAlpha2 * 0.7)})`;
                    fillBottom1 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${Math.max(0.03, baseAlpha * 0.7)})`;
                    fillBottom2 = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${Math.max(0.02, baseAlpha2 * 0.7)})`;
                }

                const band = chart.addBaselineSeries({
                    baseValue: { type: "price", price: zMin },
                    topFillColor1: fillTop1,
                    topFillColor2: fillTop2,
                    bottomFillColor1: fillBottom1,
                    bottomFillColor2: fillBottom2,
                    lineWidth: 0,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: overlayAutoscaleInfoProvider,
                });

                band.setData([
                    { time: from, value: zMax },
                    { time: to, value: zMax },
                ]);
                zoneAreas.push(band);

                // Рамка (без ліній по всьому графіку): малюємо 2 короткі горизонтальні відрізки.
                // Для Breaker — dashed, для FVG — dotted, для OB — solid (трохи товще).
                const borderStyle = isBreaker
                    ? LightweightCharts.LineStyle.Dashed
                    : isFvg
                        ? LightweightCharts.LineStyle.Dotted
                        : LightweightCharts.LineStyle.Solid;
                const borderWidth = isOb ? 2 : 1;
                const borderAlpha = isFvg ? 0.30 : isBreaker ? 0.40 : 0.45;
                const borderColor = `rgba(${palette.r}, ${palette.g}, ${palette.b}, ${borderAlpha})`;

                const createBorder = (priceValue) => {
                    const s = chart.addLineSeries({
                        color: borderColor,
                        lineWidth: borderWidth,
                        lineStyle: borderStyle,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: overlayAutoscaleInfoProvider,
                    });
                    s.setData([
                        { time: from, value: priceValue },
                        { time: to, value: priceValue },
                    ]);
                    zoneBorders.push(s);
                };

                createBorder(zMax);
                createBorder(zMin);
            });
        }

        function setOteZones(zones) {
            withViewportPreserved(() => {
                clearOteOverlays();
                const domain = getChartTimeDomain();
                if (!domain) return;

                const nowSec = (() => {
                    const t = Number(lastBar?.time);
                    if (Number.isFinite(t)) return Math.floor(t);
                    const mx = Number(domain?.max);
                    if (Number.isFinite(mx)) return Math.floor(mx);
                    return Math.floor(Date.now() / 1000);
                })();

                const mkKey = (z) => {
                    const dir = normalizeOteDirection(z?.direction) || "LONG";
                    const role = String(z?.role || "").toUpperCase();
                    const a = Number(z?.min ?? z?.price_min ?? z?.ote_min);
                    const b = Number(z?.max ?? z?.price_max ?? z?.ote_max);
                    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
                    const lo = Math.min(a, b);
                    const hi = Math.max(a, b);
                    // Ключ з квантуванням, щоб уникати "флуктуацій" через float-шум.
                    const loK = lo.toFixed(3);
                    const hiK = hi.toFixed(3);
                    return `${dir}|${role}|${loK}|${hiK}`;
                };

                const incoming = Array.isArray(zones) ? zones : [];
                const activeKeys = new Set();

                for (const z of incoming) {
                    const key = mkKey(z);
                    if (!key) continue;
                    activeKeys.add(key);
                    const canonicalDir = normalizeOteDirection(z?.direction) || "LONG";
                    const existing = oteLifecycle.get(key);
                    if (!existing) {
                        oteLifecycle.set(key, {
                            zone: { ...z, direction: canonicalDir },
                            start_time: nowSec,
                            end_time: null,
                        });
                    } else {
                        existing.zone = { ...existing.zone, ...z, direction: canonicalDir };
                        existing.end_time = null;
                    }
                }

                // Закриваємо ті, що зникли зі стріму.
                for (const [key, entry] of oteLifecycle.entries()) {
                    if (!activeKeys.has(key) && entry && entry.end_time == null) {
                        entry.end_time = nowSec;
                    }
                }

                // Підрізаємо історію, щоб не накопичувати сотні прямокутників у довгій сесії.
                // Тримаймо останні 24 OTE за часом завершення/початку.
                try {
                    const all = Array.from(oteLifecycle.entries()).map(([k, v]) => ({
                        key: k,
                        start: Number(v?.start_time) || 0,
                        end: v?.end_time == null ? Number.POSITIVE_INFINITY : Number(v?.end_time) || 0,
                    }));
                    all.sort((a, b) => (b.end === a.end ? b.start - a.start : b.end - a.end));
                    const keep = new Set(all.slice(0, 24).map((row) => row.key));
                    for (const [k] of oteLifecycle.entries()) {
                        if (!keep.has(k)) oteLifecycle.delete(k);
                    }
                } catch (_e) {
                    // ignore
                }

                // Формуємо список для tooltip/hit-test: показуємо лише те, що перетинає поточний домен.
                const visibleOtes = [];
                for (const entry of oteLifecycle.values()) {
                    if (!entry || !entry.zone) continue;
                    const start = Number(entry.start_time);
                    const end = entry.end_time == null ? nowSec : Number(entry.end_time);
                    if (!Number.isFinite(start)) continue;
                    const endOk = Number.isFinite(end) ? end : nowSec;
                    if (endOk < domain.min || start > domain.max) continue;
                    visibleOtes.push({
                        ...entry.zone,
                        start_time: start,
                        end_time: entry.end_time == null ? null : endOk,
                        _active: entry.end_time == null,
                    });
                }
                lastOteZones = visibleOtes;

                // Рендеримо як прямокутники в часі (від start до end/now).
                if (!visibleOtes.length) return;
                visibleOtes.forEach((z, index) => {
                    const from = Math.max(domain.min, Number(z.start_time) || domain.min);
                    const toRaw = z.end_time == null ? nowSec : Number(z.end_time);
                    const to = Math.min(domain.max, Number.isFinite(toRaw) ? toRaw : nowSec);
                    renderOteZone(z, index, from, Math.max(from + 1, to));
                });
            });
        }

        function setZones(zones) {
            withViewportPreserved(() => {
                clearZones();
                // Зони вже відібрані бекендом як active_zones — у UI не перефільтровуємо.
                // Малюємо як box (область), без додаткових ліній.
                // Також зберігаємо список для hover-tooltip.
                const stableZoneKey = (z) => {
                    const zid = z?.zone_id;
                    if (zid !== null && zid !== undefined && String(zid).trim() !== "") {
                        return `id:${String(zid)}`;
                    }
                    const tf = z?.timeframe ? String(z.timeframe) : "";
                    const t = String(z?.poi_type || z?.type || z?.label || "ZONE");
                    const r = String(z?.role || "");
                    const o = Number(z?.origin_time);
                    const min = Number(z?.min ?? z?.price_min);
                    const max = Number(z?.max ?? z?.price_max);
                    return `anon:${tf}|${t}|${r}|${Number.isFinite(o) ? Math.floor(o) : "-"}|${Number.isFinite(min) ? min.toFixed(6) : "-"}|${Number.isFinite(max) ? max.toFixed(6) : "-"}`;
                };

                const parseTfSeconds = (tfRaw) => {
                    const s = String(tfRaw || "").trim().toLowerCase();
                    if (!s) return null;
                    const m = s.match(/^(\d+)(s|sec|m|min|h|d)$/i);
                    if (!m) return null;
                    const n = Number(m[1]);
                    if (!Number.isFinite(n) || n <= 0) return null;
                    const unit = String(m[2]).toLowerCase();
                    if (unit === "s" || unit === "sec") return Math.floor(n);
                    if (unit === "m" || unit === "min") return Math.floor(n * 60);
                    if (unit === "h") return Math.floor(n * 3600);
                    if (unit === "d") return Math.floor(n * 86400);
                    return null;
                };

                const getViewTfSeconds = () => {
                    if (Number.isFinite(viewTimeframeSecOverride) && viewTimeframeSecOverride > 0) {
                        return Math.floor(viewTimeframeSecOverride);
                    }
                    return Math.max(1, Math.floor(barTimeSpanSeconds) || 60);
                };

                const isHtfView = () => {
                    const tf = getViewTfSeconds();
                    return Number.isFinite(tf) && tf >= 3600;
                };

                const bounds = (z) => {
                    const lo = Number(z?.min ?? z?.price_min);
                    const hi = Number(z?.max ?? z?.price_max);
                    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
                    return { lo: Math.min(lo, hi), hi: Math.max(lo, hi) };
                };

                const refine5mZonesForHtfView = (zs) => {
                    const list = Array.isArray(zs) ? zs : [];
                    if (!list.length) return [];

                    const ref = Number(pickRefPrice());
                    const refOk = Number.isFinite(ref) ? ref : null;
                    const tolAbs = refOk && refOk > 0 ? Math.max(1e-9, refOk * 0.00005) : 0;

                    // HTF POI: усе, що >= 1h.
                    const htf = [];
                    const m5 = [];
                    const other = [];
                    for (const z of list) {
                        const tfSec = parseTfSeconds(z?.timeframe);
                        if (Number.isFinite(tfSec) && tfSec >= 3600) {
                            htf.push(z);
                        } else if (String(z?.timeframe || "").trim().toLowerCase() === "5m") {
                            m5.push(z);
                        } else {
                            other.push(z);
                        }
                    }

                    if (!m5.length) {
                        return list;
                    }

                    // 1) Refinement: 5m лише якщо повністю всередині будь-якої HTF зони.
                    const inside = [];
                    if (htf.length) {
                        for (const z of m5) {
                            const b = bounds(z);
                            if (!b) continue;
                            const ok = htf.some((hz) => {
                                const hb = bounds(hz);
                                if (!hb) return false;
                                return b.lo >= hb.lo - tolAbs && b.hi <= hb.hi + tolAbs;
                            });
                            if (ok) inside.push(z);
                        }
                    }

                    if (inside.length) {
                        return htf.concat(inside, other);
                    }

                    // 2) Якщо HTF-контейнера немає/нічого не влізло — показуємо лише top-1 5m вище і top-1 5m нижче від ціни.
                    if (!refOk) {
                        return htf.concat(other);
                    }

                    const scored = m5
                        .map((z) => {
                            const b = bounds(z);
                            if (!b) return null;
                            const inZone = refOk >= b.lo - tolAbs && refOk <= b.hi + tolAbs;
                            const above = b.lo > refOk + tolAbs;
                            const below = b.hi < refOk - tolAbs;
                            let side = "";
                            let dist = 0;
                            if (inZone) {
                                side = "in";
                                dist = 0;
                            } else if (above) {
                                side = "above";
                                dist = b.lo - refOk;
                            } else if (below) {
                                side = "below";
                                dist = refOk - b.hi;
                            } else {
                                // overlap edge-case: часткове перекриття
                                side = "in";
                                dist = 0;
                            }
                            return { z, side, dist };
                        })
                        .filter(Boolean);

                    const inZone = scored.filter((x) => x.side === "in");
                    if (inZone.length) {
                        // Якщо ціна всередині 5m зони — показуємо тільки її (як refinement).
                        inZone.sort((a, b) => a.dist - b.dist);
                        return htf.concat([inZone[0].z], other);
                    }

                    const above = scored
                        .filter((x) => x.side === "above" && Number.isFinite(x.dist) && x.dist >= 0)
                        .sort((a, b) => a.dist - b.dist);
                    const below = scored
                        .filter((x) => x.side === "below" && Number.isFinite(x.dist) && x.dist >= 0)
                        .sort((a, b) => a.dist - b.dist);
                    const picked = [];
                    if (above[0]?.z) picked.push(above[0].z);
                    if (below[0]?.z) picked.push(below[0].z);
                    return htf.concat(picked, other);
                };

                // Gate 2 (Truth по TF): приймаємо лише зони, що зібрані з complete барів.
                // Евристика: origin_time має бути кратним TF і не пізніше за останній complete close-time.
                const lastCompleteCloseTime = (() => {
                    const t = Number(lastBar?.time);
                    return Number.isFinite(t) ? Math.floor(t) : null;
                })();

                const isZoneFromCompleteTf = (z) => {
                    const tf = z?.timeframe;
                    const tfSec = parseTfSeconds(tf);
                    if (!Number.isFinite(lastCompleteCloseTime)) {
                        // Якщо ще немає complete історії — безпечніше нічого не малювати.
                        return false;
                    }
                    if (!Number.isFinite(tfSec) || tfSec <= 0) {
                        // Невідомий TF: не можемо довести "truth" => не малюємо (жорстко).
                        return false;
                    }
                    const origin = Number(z?.origin_time);
                    if (!Number.isFinite(origin)) return false;
                    const originSec = Math.floor(origin);
                    // Обов’язково: origin_time вирівняний по TF.
                    if (originSec % tfSec !== 0) return false;
                    // Обов’язково: не з майбутнього відносно останнього complete бару цього TF.
                    const lastTfClose = Math.floor(lastCompleteCloseTime / tfSec) * tfSec;
                    return originSec <= lastTfClose;
                };

                const isPoiZone = (z) => {
                    const poiType = String(z?.poi_type || "").trim();
                    if (poiType) return true;
                    const label = String(z?.label || z?.type || "").toUpperCase();
                    return label.includes("POI");
                };

                const distanceToRangeAbs = (price, zMin, zMax) => {
                    if (!Number.isFinite(price) || !Number.isFinite(zMin) || !Number.isFinite(zMax)) return Number.POSITIVE_INFINITY;
                    if (price < zMin) return zMin - price;
                    if (price > zMax) return price - zMax;
                    return 0;
                };

                // Gate 3 (антишум для micro-view): показуємо лише найближчі 5m POI (top-2 зверху/знизу).
                // У Z3.S1 micro-view не доступний через меню TF, але лишаємо як safe-no-op.
                const applyNearest5mFilterForMicroView = (zs) => {
                    const span = Number.isFinite(viewTimeframeSecOverride)
                        ? Number(viewTimeframeSecOverride)
                        : Math.max(1, Number(barTimeSpanSeconds) || 60);
                    const isMicroView = Math.max(1, Number(span) || 60) <= 70;
                    if (!isMicroView) return zs;

                    const mode = String(zoneLimitMode || "near2").toLowerCase();
                    if (mode === "all") {
                        return zs;
                    }
                    const sideCount = mode === "near1" ? 1 : 2;

                    const ref = Number(pickRefPrice());
                    if (!Number.isFinite(ref)) return [];
                    const priceWindowAbs = estimatePriceWindowAbs(ref);
                    const maxDistAbs = Math.max(1e-9, priceWindowAbs) * 1.2;

                    const fiveMin = 5 * 60;
                    const candidates = (Array.isArray(zs) ? zs : [])
                        .filter((z) => parseTfSeconds(z?.timeframe) === fiveMin)
                        .filter((z) => isPoiZone(z));

                    const scored = candidates
                        .map((z) => {
                            const min = Number(z?.min ?? z?.price_min);
                            const max = Number(z?.max ?? z?.price_max);
                            if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
                            const zMin = Math.min(min, max);
                            const zMax = Math.max(min, max);
                            const dist = distanceToRangeAbs(ref, zMin, zMax);
                            const side = ref < zMin ? "above" : ref > zMax ? "below" : "inside";
                            return { z, zMin, zMax, dist, side };
                        })
                        .filter(Boolean)
                        .filter((x) => x.dist <= maxDistAbs || x.side === "inside")
                        .sort((a, b) => a.dist - b.dist);

                    const inside = scored.filter((x) => x.side === "inside").map((x) => x.z);
                    const above = scored.filter((x) => x.side === "above").slice(0, sideCount).map((x) => x.z);
                    const below = scored.filter((x) => x.side === "below").slice(0, sideCount).map((x) => x.z);

                    // Дедуп по stable key.
                    const out = [];
                    const seen = new Set();
                    for (const z of inside.concat(above).concat(below)) {
                        const k = stableZoneKey(z);
                        if (seen.has(k)) continue;
                        seen.add(k);
                        out.push(z);
                    }
                    return out;
                };

                const freezeGeometry = (input) => {
                    const min = Number(input?.min ?? input?.price_min);
                    const max = Number(input?.max ?? input?.price_max);
                    if (!Number.isFinite(min) || !Number.isFinite(max)) {
                        return input;
                    }
                    const key = stableZoneKey(input);
                    const existing = zoneGeometryById.get(key);
                    if (existing && Number.isFinite(existing.min) && Number.isFinite(existing.max)) {
                        return {
                            ...input,
                            min: existing.min,
                            max: existing.max,
                            // Gate 1 (No-repaint): origin_time після створення не змінюється.
                            ...(Number.isFinite(existing.origin_time)
                                ? { origin_time: existing.origin_time }
                                : {}),
                        };
                    }

                    const origin = Number(input?.origin_time);
                    const originSec = Number.isFinite(origin) ? Math.floor(origin) : null;
                    zoneGeometryById.set(key, {
                        min: Math.min(min, max),
                        max: Math.max(min, max),
                        origin_time: Number.isFinite(originSec) ? originSec : undefined,
                    });
                    return {
                        ...input,
                        min: Math.min(min, max),
                        max: Math.max(min, max),
                        ...(Number.isFinite(originSec) ? { origin_time: originSec } : {}),
                    };
                };

                const clusterZones = (zs) => {
                    const list = (Array.isArray(zs) ? zs : []).filter((z) => z && typeof z === "object");
                    if (!list.length) return [];

                    const ref = Number(pickRefPrice());
                    const tolAbs = Number.isFinite(ref) && ref > 0 ? Math.max(1e-9, ref * 0.00005) : 0;

                    const bounds = (z) => {
                        const lo = Number(z?.min ?? z?.price_min);
                        const hi = Number(z?.max ?? z?.price_max);
                        if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
                        return { lo: Math.min(lo, hi), hi: Math.max(lo, hi) };
                    };

                    const overlapSpanRatio = (a, b) => {
                        const aa = bounds(a);
                        const bb = bounds(b);
                        if (!aa || !bb) return 0;
                        const inter = Math.max(0, Math.min(aa.hi, bb.hi) - Math.max(aa.lo, bb.lo));
                        const spanA = Math.max(0, aa.hi - aa.lo);
                        const spanB = Math.max(0, bb.hi - bb.lo);
                        const spanSmall = Math.max(1e-9, Math.min(spanA, spanB));
                        return inter / spanSmall;
                    };

                    const gapAbs = (a, b) => {
                        const aa = bounds(a);
                        const bb = bounds(b);
                        if (!aa || !bb) return Number.POSITIVE_INFINITY;
                        if (aa.hi < bb.lo) return bb.lo - aa.hi;
                        if (bb.hi < aa.lo) return aa.lo - bb.hi;
                        return 0;
                    };

                    const shouldCluster = (prev, next) => {
                        const o = overlapSpanRatio(prev, next);
                        if (o >= 0.6) return true;
                        const g = gapAbs(prev, next);
                        return Number.isFinite(g) && g <= tolAbs;
                    };

                    const roleRank = (role) => {
                        const r = String(role || "").toUpperCase();
                        if (r === "PRIMARY" || r === "P") return 3;
                        if (r === "COUNTERTREND" || r === "C") return 2;
                        if (r === "NEUTRAL" || r === "N") return 1;
                        return 0;
                    };

                    const zonePickScore = (z) => {
                        const sr = stateRank(z?.state);
                        const rr = roleRank(z?.role);
                        const strength = Number(z?.strength);
                        const confidence = Number(z?.confidence);
                        const score = Number(z?.score ?? z?._score);
                        const sOk = Number.isFinite(strength) ? strength : 0;
                        const cOk = Number.isFinite(confidence) ? confidence : 0;
                        const scOk = Number.isFinite(score) ? score : 0;
                        return sr * 10_000_000 + rr * 1_000_000 + sOk * 10_000 + cOk * 1_000 + scOk;
                    };

                    const pickRep = (members) => {
                        const sorted = members.slice().sort((a, b) => {
                            const ap = zonePickScore(a);
                            const bp = zonePickScore(b);
                            if (ap !== bp) return bp - ap;
                            const aw = Math.abs(Number(a?.max) - Number(a?.min));
                            const bw = Math.abs(Number(b?.max) - Number(b?.min));
                            const awOk = Number.isFinite(aw) ? aw : Number.POSITIVE_INFINITY;
                            const bwOk = Number.isFinite(bw) ? bw : Number.POSITIVE_INFINITY;
                            return awOk - bwOk;
                        });
                        return sorted[0] || members[0] || null;
                    };

                    const sorted = list
                        .slice()
                        .map((z) => ({ z, b: bounds(z) }))
                        .filter((x) => x.b)
                        .sort((a, b) => a.b.lo - b.b.lo)
                        .map((x) => x.z);

                    const out = [];
                    let bucket = [];
                    for (const z of sorted) {
                        if (!bucket.length) {
                            bucket = [z];
                            continue;
                        }
                        const last = bucket[bucket.length - 1];
                        if (shouldCluster(last, z)) {
                            bucket.push(z);
                        } else {
                            const rep = pickRep(bucket);
                            const start = bucket
                                .map((m) => Number(m?.origin_time))
                                .filter((v) => Number.isFinite(v))
                                .sort((a, b) => a - b)[0];
                            out.push({ rep, members: bucket.slice(0), start_time: start });
                            bucket = [z];
                        }
                    }
                    if (bucket.length) {
                        const rep = pickRep(bucket);
                        const start = bucket
                            .map((m) => Number(m?.origin_time))
                            .filter((v) => Number.isFinite(v))
                            .sort((a, b) => a - b)[0];
                        out.push({ rep, members: bucket.slice(0), start_time: start });
                    }

                    return out.filter((c) => c && c.rep);
                };

                try {
                    const base = Array.isArray(zones) ? zones.slice(0) : [];
                    const truth = base.filter(isZoneFromCompleteTf);
                    const filteredForView = isHtfView()
                        ? refine5mZonesForHtfView(truth)
                        : applyNearest5mFilterForMicroView(truth);

                    lastZones = filteredForView.map(freezeGeometry);
                    lastZoneClusters = clusterZones(lastZones);
                } catch (_e) {
                    lastZones = [];
                    lastZoneClusters = [];
                }

                // Підписи зон як markers: 1 маркер на origin_time кожної active POI зони.
                // Важливо: дедуп по zone_id, щоб не мигали між апдейтами.
                if (ZONE_LABELS_ENABLED) {
                    const toUnixSeconds = (value) => {
                        const num = Number(value);
                        if (!Number.isFinite(num)) return null;
                        return Math.floor(num / (num > 1e12 ? 1000 : 1));
                    };

                    const snapToNearestBarTime = (timeSec) => {
                        if (!Number.isFinite(timeSec)) return null;
                        const times = lastCandleTimes;
                        if (!Array.isArray(times) || times.length === 0) {
                            return Math.floor(timeSec);
                        }

                        const target = Math.floor(timeSec);
                        let lo = 0;
                        let hi = times.length;
                        while (lo < hi) {
                            const mid = (lo + hi) >> 1;
                            const v = times[mid];
                            if (v < target) lo = mid + 1;
                            else hi = mid;
                        }

                        const rightIdx = Math.min(times.length - 1, lo);
                        const leftIdx = Math.max(0, rightIdx - 1);
                        const left = Number(times[leftIdx]);
                        const right = Number(times[rightIdx]);
                        const pick =
                            !Number.isFinite(left) ? right :
                                !Number.isFinite(right) ? left :
                                    Math.abs(target - left) <= Math.abs(right - target) ? left : right;

                        if (!Number.isFinite(pick)) {
                            return null;
                        }

                        const maxDiff = Math.max(1, Number(barTimeSpanSeconds) || 60) * 1.5;
                        if (Math.abs(pick - target) > maxDiff) {
                            return null;
                        }
                        return Math.floor(pick);
                    };

                    const zoneText = (z, suffix = "") => {
                        const tf = z?.timeframe ? String(z.timeframe) : "";
                        const t = normalizePoiType(z?.poi_type || z?.type || z?.label || "ZONE");
                        const r = roleShort(z);
                        return `${tf ? tf + " " : ""}${t} ${r}${suffix}`.trim();
                    };

                    const firstBarTime = (() => {
                        const times = lastCandleTimes;
                        if (!Array.isArray(times) || times.length === 0) return null;
                        const first = Number(times[0]);
                        return Number.isFinite(first) ? Math.floor(first) : null;
                    })();

                    const fallbackLastBarTime = (() => {
                        const times = lastCandleTimes;
                        if (!Array.isArray(times) || times.length === 0) return null;
                        const last = Number(times[times.length - 1]);
                        return Number.isFinite(last) ? Math.floor(last) : null;
                    })();

                    zoneMarkers = [];
                    if (ZONE_LABEL_MARKERS_ENABLED) {
                        for (const z of Array.isArray(lastZones) ? lastZones : []) {
                            const zid = z?.zone_id;
                            const id = zid !== null && zid !== undefined ? String(zid) : null;
                            if (!id) continue;

                            const originRaw = z?.origin_time ?? z?.origin_ts ?? z?.ts ?? z?.timestamp;
                            const origin = toUnixSeconds(originRaw);
                            if (!Number.isFinite(origin)) continue;

                            // Якщо origin поза історією (раніше першого видимого бару) — ставимо на перший бар.
                            let time = origin;
                            let suffix = "";
                            if (Number.isFinite(firstBarTime) && origin < firstBarTime) {
                                time = firstBarTime;
                                suffix = " (earlier)";
                            }

                            const cached = zoneLabelById.get(id);
                            if (cached && Number.isFinite(cached.time) && typeof cached.text === "string") {
                                zoneMarkers.push({
                                    time: cached.time,
                                    position: "belowBar",
                                    color: "rgba(209, 212, 220, 0.85)",
                                    shape: "circle",
                                    text: cached.text,
                                });
                                continue;
                            }

                            const snapped = snapToNearestBarTime(time);
                            if (!Number.isFinite(snapped)) continue;

                            const text = zoneText(z, suffix);
                            zoneLabelById.set(id, { time: snapped, text });
                            zoneMarkers.push({
                                time: snapped,
                                position: "belowBar",
                                color: "rgba(209, 212, 220, 0.85)",
                                shape: "circle",
                                text,
                            });
                        }
                    }

                    if (!zoneLabelsLogged) {
                        zoneLabelsLogged = true;
                        console.info(
                            `ui: zone_labels=1, dom=1, markers=${ZONE_LABEL_MARKERS_ENABLED ? 1 : 0}, zones=${Array.isArray(lastZones) ? lastZones.length : 0}, marker_count=${zoneMarkers.length}`,
                        );
                    }
                } else {
                    zoneMarkers = [];
                    clearPoiDomLabels();
                }

                schedulePoiDomLabels();
                applyCombinedMarkers();
                // Антишум: малюємо 1 box на кластер, а у tooltip показуємо stack.
                setZoneBoxes(lastZoneClusters.length ? lastZoneClusters : zones);
            });
        }

        function clearAll() {
            candles.setData([]);
            liveCandles.setData([]);
            volume.setData([]);
            liveVolume.setData([]);
            setSessionsData([]);
            setSessionRangeBoxes(null);
            lastBar = null;
            lastLiveBar = null;
            lastTickPrice = null;
            lastTickPriceAtMs = 0;
            lastLiveVolume = 0;
            recentVolumeMax = 0;
            recentVolumes = [];
            lastBarsSignature = null;
            autoFitDone = false;
            zoneMarkers = [];
            executionMarkers = [];
            lastZones = [];
            lastZoneClusters = [];
            zoneLabelById = new Map();
            zoneGeometryById = new Map();
            clearPoiDomLabels();
            clearPoolsDomLabels();
            clearEvents();
            clearPools();
            clearPoolsSelectedV1();
            clearLevelsSelectedV1();
            clearRanges();
            clearZones();
            clearOteOverlays();
            resetManualPriceScale({ silent: true });
            priceScaleState.lastAutoRange = null;
            structureTriangles = [];
            chartTimeRange = { min: null, max: null };
        }

        function syncPriceScaleCssVar() {
            // SSOT для UI оверлеїв: ширина правої цінової шкали.
            // Використовується для safe-area, щоб кнопки/шапка не наїжджали на price scale.
            try {
                if (typeof document === "undefined") {
                    return;
                }
                const w = Number(chart.priceScale("right").width() || 0);
                const fallback = RIGHT_PRICE_SCALE_MIN_WIDTH_DESKTOP_PX;
                const px = Number.isFinite(w) && w > 0 ? Math.round(w) : fallback;
                document.documentElement.style.setProperty("--price-scale-w", `${px}px`);
            } catch (_e) {
                // noop
            }
        }

        const api = {
            setBars,
            updateLastBar,
            setLiveBar,
            setLastPriceFromTick,
            clearLiveBar,
            resetView() {
                // Mobile UX: одна кнопка "Reset" має повертати графік у базовий стан,
                // без скидання даних/шарів.
                try {
                    resetManualPriceScale({ silent: true });
                    chart.timeScale().fitContent();
                    requestPriceScaleSync();
                } catch (_e) {
                    // noop
                }
            },
            setMobileV2PriceScaleCompact(enabled) {
                // Ізольовано від desktop: застосовується лише коли app.js явно викликає цю функцію.
                // Використовуємо minimumWidth, щоб прибрати "порожній" простір навколо ціни.
                const on = Boolean(enabled);
                const minWidth = on ? RIGHT_PRICE_SCALE_MIN_WIDTH_MOBILE_V2_PX : RIGHT_PRICE_SCALE_MIN_WIDTH_DESKTOP_PX;
                try {
                    chart.applyOptions({
                        rightPriceScale: {
                            minimumWidth: minWidth,
                        },
                    });
                    syncPriceScaleCssVar();
                } catch (_e) {
                    // noop
                }
            },
            setEvents,
            setExecutionEvents,
            setOteZones,
            setLiquidityPools,
            setPoolsSelectedV1,
            clearPoolsSelectedV1,
            setLevelsSelectedV1,
            setRanges,
            setZones,
            setViewTimeframe: (tf) => {
                const sec = parseTfSecondsSimple(tf);
                viewTimeframeSecOverride = Number.isFinite(sec) ? sec : null;
            },
            setZoneLimitMode(mode) {
                const m = String(mode || "").trim().toLowerCase();
                zoneLimitMode = m === "near1" || m === "near2" || m === "all" ? m : "near2";
            },
            setSessionsEnabled,
            setSessionWindowsUtc,
            setSessionsRibbonData,
            setSessionRangeBoxes,
            setSessionRangeBox,
            resizeToContainer,
            clearAll,
            setPoolsLabelsLeftV1(pools) {
                // Рішення: прибираємо бокові маркери/чіпи для Pools-V1 повністю.
                // Залишається лише box-band (edges + fill) + tooltip.
                // Важливо: чистимо вже намальоване, навіть якщо прийшов непорожній список.
                void pools;
                poolsDomLabelsModel = [];
                clearPoolsDomLabels();
            },
            clearPoolsLabelsLeftV1() {
                poolsDomLabelsModel = [];
                clearPoolsDomLabels();
            },
            dispose() {
                clearLiveBar();
                clearStructureTriangles();
                clearOteOverlays();
                interactionCleanup.splice(0).forEach((cleanup) => {
                    try {
                        cleanup();
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося очистити обробник", err);
                    }
                });
                container.classList.remove("vertical-pan-active");
                chart.remove();
            },
        };

        if (TEST_HOOKS_ENABLED) {
            api.__debugGetPriceScaleState = () => ({
                manualRange: priceScaleState.manualRange ? { ...priceScaleState.manualRange } : null,
                lastAutoRange: priceScaleState.lastAutoRange ? { ...priceScaleState.lastAutoRange } : null,
            });
            api.__debugGetEffectivePriceRange = () => getEffectivePriceRange();
            api.__debugIsVerticalPanActive = () => ({
                pending: Boolean(verticalPanState.pending),
                active: Boolean(verticalPanState.active),
            });

            api.__debugGetTimeAnchors = () => ({
                lastBarTime: Number.isFinite(Number(lastBar?.time)) ? Number(lastBar.time) : null,
                lastLiveBarTime: Number.isFinite(Number(lastLiveBar?.time)) ? Number(lastLiveBar.time) : null,
                chartTimeRangeMax: Number.isFinite(Number(chartTimeRange?.max)) ? Number(chartTimeRange.max) : null,
                barTimeSpanSeconds: Number.isFinite(Number(barTimeSpanSeconds)) ? Number(barTimeSpanSeconds) : null,
            });
        }

        return api;

        function resizeToContainer() {
            if (!container || typeof container.getBoundingClientRect !== "function") {
                return;
            }
            const rect = container.getBoundingClientRect();
            const width = Math.floor(rect.width);
            const height = Math.floor(rect.height);
            if (
                !Number.isFinite(width) ||
                !Number.isFinite(height) ||
                width <= 0 ||
                height <= 0
            ) {
                return;
            }
            if (lastContainerSize.width === width && lastContainerSize.height === height) {
                return;
            }
            lastContainerSize = { width, height };
            chart.applyOptions({ width, height });
            syncPriceScaleCssVar();
            schedulePoiDomLabels();
        }

        function renderStructureTriangle(evt) {
            if (!evt) {
                return;
            }
            const price = Number(evt.price ?? evt.level);
            const time = Number(evt.time ?? evt.ts ?? evt.timestamp);
            if (!Number.isFinite(price) || !Number.isFinite(time)) {
                return;
            }
            const normalizedTime = Math.floor(time);
            const direction = (evt.direction || evt.dir || "").toUpperCase();
            const type = (evt.type || evt.event_type || "").toUpperCase();
            const color = type.includes("CHOCH")
                ? STRUCTURE_TRIANGLE.colors.choch
                : STRUCTURE_TRIANGLE.colors.bos;
            const priceRange = getEffectivePriceRange();
            const fallbackSpan = Math.max(Math.abs(price) * 0.02, 1);
            const rangeSpan = priceRange
                ? priceRange.max - priceRange.min
                : fallbackSpan;
            const widthSeconds = Math.max(
                STRUCTURE_TRIANGLE.minWidthSec,
                Math.round(barTimeSpanSeconds * STRUCTURE_TRIANGLE.widthBars)
            );
            const halfWidth = Math.max(1, Math.round(widthSeconds / 2));
            const leftTime = Math.max(0, normalizedTime - halfWidth);
            const rightTime = normalizedTime + halfWidth;
            const minHeightFromPrice = Math.max(
                STRUCTURE_TRIANGLE.minHeight,
                Math.abs(price) * (STRUCTURE_TRIANGLE.minHeightPct || 0)
            );
            const height = Math.max(
                minHeightFromPrice,
                rangeSpan * STRUCTURE_TRIANGLE.heightRatio
            );
            const isShort = direction === "SHORT";
            const basePrice = isShort ? price + height : price - height;
            const edgesSeries = createOverlaySeries(color, STRUCTURE_TRIANGLE.edgeWidth);
            edgesSeries.setData([
                { time: leftTime, value: basePrice },
                { time: normalizedTime, value: price },
                { time: rightTime, value: basePrice },
            ]);
            const baseSeries = createOverlaySeries(color, STRUCTURE_TRIANGLE.baseWidth);
            baseSeries.setData([
                { time: leftTime, value: basePrice },
                { time: rightTime, value: basePrice },
            ]);
            structureTriangles.push(edgesSeries, baseSeries);
            const priceLineTitle = [type || "STRUCT", direction || ""]
                .map((part) => part.trim())
                .filter(Boolean)
                .join(" ");
            const priceLine = candles.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                lineVisible: false,
                title: priceLineTitle || "STRUCT",
            });
            structureTriangleLabels.push(priceLine);
        }

        function renderOteZone(zone, index, left, right) {
            if (!zone) {
                return;
            }
            const minPrice = Number(zone.min ?? zone.price_min ?? zone.ote_min);
            const maxPrice = Number(zone.max ?? zone.price_max ?? zone.ote_max);
            if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || minPrice >= maxPrice) {
                return;
            }
            const direction = normalizeOteDirection(zone.direction) || "LONG";
            const palette = direction === "SHORT" ? OTE_STYLES.SHORT : OTE_STYLES.LONG;
            const safeLeft = Math.floor(left);
            const safeRight = Math.max(safeLeft + 1, Math.floor(right));

            // Легка заливка (прямокутник) + dotted рамка.
            const softenRgba = (rgba, alphaMul, alphaMin = 0.03, alphaMax = 0.35) => {
                const s = String(rgba || "").trim();
                const m = s.match(/^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9]*\.?[0-9]+)\s*\)$/i);
                if (!m) return rgba;
                const r = Number(m[1]);
                const g = Number(m[2]);
                const b = Number(m[3]);
                const a = Number(m[4]);
                if (![r, g, b, a].every(Number.isFinite)) return rgba;
                const outA = clamp(a * Number(alphaMul || 1), alphaMin, alphaMax);
                return `rgba(${r}, ${g}, ${b}, ${outA})`;
            };

            const fill1 = softenRgba(palette.border, 0.38, 0.03, 0.22);
            const fill2 = softenRgba(palette.border, 0.18, 0.02, 0.14);

            const band = chart.addBaselineSeries({
                baseValue: { type: "price", price: minPrice },
                topFillColor1: fill1,
                topFillColor2: fill2,
                bottomFillColor1: fill1,
                bottomFillColor2: fill2,
                // BaselineSeries має власну "line" (за замовчуванням може бути зеленуватою).
                // Ми малюємо рамку окремими line-series, тому тут лінію повністю глушимо.
                baseLineVisible: false,
                baseLineColor: "rgba(0, 0, 0, 0)",
                topLineColor: "rgba(0, 0, 0, 0)",
                bottomLineColor: "rgba(0, 0, 0, 0)",
                lineWidth: 0,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: overlayAutoscaleInfoProvider,
            });
            band.setData([
                { time: safeLeft, value: maxPrice },
                { time: safeRight, value: maxPrice },
            ]);

            // Для OTE робимо лінії «тоншими» візуально: dotted стиль + приглушений колір.
            const createOteBorderSeries = () =>
                chart.addLineSeries({
                    color: palette.border,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    priceScaleId: "right",
                    lastValueVisible: false,
                    priceLineVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: overlayAutoscaleInfoProvider,
                });

            const topSeries = createOteBorderSeries();
            topSeries.setData([
                { time: safeLeft, value: maxPrice },
                { time: safeRight, value: maxPrice },
            ]);
            const bottomSeries = createOteBorderSeries();
            bottomSeries.setData([
                { time: safeLeft, value: minPrice },
                { time: safeRight, value: minPrice },
            ]);
            const overlaySeries = [topSeries, bottomSeries];
            overlaySeries.unshift(band);
            const isActive = zone?._active === true;
            const priceLine = isActive
                ? candles.createPriceLine({
                    price: (minPrice + maxPrice) / 2,
                    color: palette.axisLabel,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: true,
                    lineVisible: false,
                    // Короткий title: менше «шуму» на шкалі.
                    title: direction === "SHORT" ? "↓" : "↑",
                })
                : null;
            oteOverlays.push({
                series: overlaySeries,
                priceLine,
            });
        }

        function isStructureEvent(evt) {
            if (!evt) {
                return false;
            }
            const kind = (evt.type || evt.event_type || "").toUpperCase();
            return kind.includes("BOS") || kind.includes("CHOCH");
        }

        function getChartTimeDomain() {
            if (
                chartTimeRange.min != null &&
                chartTimeRange.max != null &&
                chartTimeRange.max > chartTimeRange.min
            ) {
                return {
                    min: chartTimeRange.min,
                    max: chartTimeRange.max,
                };
            }
            if (lastBar?.time) {
                const fallbackMin = lastBar.time - barTimeSpanSeconds * 200;
                return {
                    min: Math.max(0, fallbackMin),
                    max: lastBar.time,
                };
            }
            return null;
        }

        function updateBarTimeSpanFromBars(bars) {
            if (!Array.isArray(bars) || bars.length < 2) {
                return;
            }
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

        function updateTimeRangeFromBars(bars) {
            if (!Array.isArray(bars) || !bars.length) {
                chartTimeRange = { min: null, max: null };
                return;
            }
            chartTimeRange = {
                min: bars[0].time,
                max: bars[bars.length - 1].time,
            };
        }

        function clampTime(value, min, max) {
            if (!Number.isFinite(value)) {
                return min;
            }
            return Math.max(min, Math.min(max, value));
        }

        function createOverlaySeries(color, lineWidth) {
            return chart.addLineSeries({
                color,
                lineWidth,
                priceScaleId: "right",
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null,
            });
        }

        function withViewportPreserved(action) {
            const logicalRange = chart.timeScale().getVisibleLogicalRange();
            const scrollPos = chart.timeScale().scrollPosition();
            action();
            if (logicalRange) {
                chart.timeScale().setVisibleLogicalRange({
                    from: logicalRange.from,
                    to: logicalRange.to,
                });
            } else if (Number.isFinite(scrollPos)) {
                chart.timeScale().scrollToPosition(scrollPos, false);
            }
        }

        function setupPriceScaleInteractions() {
            if (!container || typeof window === "undefined") {
                return;
            }

            // Fallback-ширина правої цінової шкали для hit-test у моменти, коли
            // lightweight-charts ще не віддав валідні paneSize/priceScale.width().
            // Вирівняно з UI резервом під праву шкалу: styles.css -> .chart-overlay-actions { padding-right: 56px; }
            const PRICE_AXIS_FALLBACK_WIDTH_PX = 56;

            // Під час нашого vertical-pan тимчасово блокуємо drag-скрол бібліотеки,
            // щоб не було «упирання» і переходу в масштабування.
            const setLibraryDragEnabled = (enabled) => {
                try {
                    chart.applyOptions({
                        handleScroll: {
                            pressedMouseMove: Boolean(enabled),
                        },
                    });
                } catch (_e) {
                    // ignore
                }
            };

            let pendingWheelRaf = null;
            let pendingWheel = null;

            const flushPendingWheel = () => {
                pendingWheelRaf = null;
                const payload = pendingWheel;
                pendingWheel = null;
                if (!payload) {
                    return;
                }

                const effectiveRange = getEffectivePriceRange();
                if (!effectiveRange) {
                    return;
                }

                const ev = {
                    clientX: payload.clientX,
                    clientY: payload.clientY,
                    deltaY: payload.deltaY,
                    shiftKey: payload.shiftKey,
                };
                const pointerInAxis = isPointerInPriceAxis(ev, PRICE_AXIS_FALLBACK_WIDTH_PX);
                const pointerInPane = isPointerInsidePane(ev, PRICE_AXIS_FALLBACK_WIDTH_PX);

                if (ev.shiftKey && pointerInPane) {
                    applyWheelPan(ev);
                    return;
                }
                if (pointerInAxis) {
                    applyWheelZoom(ev);
                }
            };

            const schedulePendingWheel = (event) => {
                pendingWheel = {
                    clientX: event.clientX,
                    clientY: event.clientY,
                    deltaY: event.deltaY,
                    shiftKey: Boolean(event.shiftKey),
                };
                if (pendingWheelRaf !== null) {
                    return;
                }
                pendingWheelRaf = window.requestAnimationFrame(flushPendingWheel);
            };

            const handleWheel = (event) => {
                const pointerInAxis = isPointerInPriceAxis(event, PRICE_AXIS_FALLBACK_WIDTH_PX);
                const pointerInPane = isPointerInsidePane(event, PRICE_AXIS_FALLBACK_WIDTH_PX);
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
                    // Ще не готові метрики/autoRange: не даємо built-in scale “проскакувати”,
                    // але пробуємо повторити дію в наступному кадрі.
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

            const stopVerticalPan = () => {
                if (!verticalPanState.pending) {
                    return;
                }
                verticalPanState.pending = false;
                verticalPanState.active = false;
                verticalPanState.startRange = null;
                verticalPanState.baseRange = null;
                verticalPanState.pointerId = null;
                container.classList.remove("vertical-pan-active");
                setLibraryDragEnabled(true);
            };

            const beginPan = (clientX, clientY, pointerId = null) => {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                verticalPanState.pending = true;
                verticalPanState.active = false;
                verticalPanState.startY = clientY;
                verticalPanState.startX = clientX;
                verticalPanState.baseRange = currentRange;
                verticalPanState.startRange = null;
                verticalPanState.pointerId = pointerId;
            };

            const movePan = (event, clientX, clientY) => {
                if (!verticalPanState.pending) {
                    return;
                }
                if (verticalPanState.pointerId !== null && event?.pointerId !== undefined) {
                    if (event.pointerId !== verticalPanState.pointerId) {
                        return;
                    }
                }

                const paneHeight = getPaneMetrics().paneHeight;
                if (!paneHeight) {
                    return;
                }
                const deltaY = clientY - verticalPanState.startY;
                const deltaX = clientX - verticalPanState.startX;

                if (!verticalPanState.active) {
                    if (Math.abs(deltaY) < DRAG_ACTIVATION_PX || Math.abs(deltaY) <= Math.abs(deltaX)) {
                        return;
                    }
                    ensureManualRange(verticalPanState.baseRange);
                    verticalPanState.startRange = { ...priceScaleState.manualRange };
                    verticalPanState.active = true;
                    container.classList.add("vertical-pan-active");

                    // Блокуємо drag бібліотеки тільки коли точно почали vertical-pan.
                    setLibraryDragEnabled(false);
                }

                event.preventDefault();
                event.stopPropagation();

                const span = verticalPanState.startRange.max - verticalPanState.startRange.min;
                if (!(span > 0)) {
                    return;
                }
                const offset = (deltaY / paneHeight) * span;
                applyManualRange({
                    min: verticalPanState.startRange.min + offset,
                    max: verticalPanState.startRange.max + offset,
                });
                schedulePoiDomLabels();
            };

            const usePointerEvents = typeof window.PointerEvent !== "undefined";

            if (usePointerEvents) {
                const handlePointerDown = (event) => {
                    if (!event || event.button !== 0) {
                        return;
                    }
                    if (!isPointerInsidePane(event)) {
                        return;
                    }
                    beginPan(event.clientX, event.clientY, event.pointerId);
                };
                container.addEventListener("pointerdown", handlePointerDown, true);
                interactionCleanup.push(() => container.removeEventListener("pointerdown", handlePointerDown, true));

                const handlePointerMove = (event) => {
                    movePan(event, event.clientX, event.clientY);
                };
                window.addEventListener("pointermove", handlePointerMove, true);
                interactionCleanup.push(() => window.removeEventListener("pointermove", handlePointerMove, true));

                const handlePointerUp = () => {
                    stopVerticalPan();
                };
                window.addEventListener("pointerup", handlePointerUp, true);
                interactionCleanup.push(() => window.removeEventListener("pointerup", handlePointerUp, true));
                window.addEventListener("pointercancel", handlePointerUp, true);
                interactionCleanup.push(() => window.removeEventListener("pointercancel", handlePointerUp, true));
                window.addEventListener("blur", stopVerticalPan);
                interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));
            } else {
                const handleMouseDown = (event) => {
                    if (event.button !== 0 || !isPointerInsidePane(event)) {
                        return;
                    }
                    beginPan(event.clientX, event.clientY, null);
                };
                container.addEventListener("mousedown", handleMouseDown, true);
                interactionCleanup.push(() => container.removeEventListener("mousedown", handleMouseDown, true));

                const handleMouseMove = (event) => {
                    movePan(event, event.clientX, event.clientY);
                };
                window.addEventListener("mousemove", handleMouseMove, true);
                interactionCleanup.push(() => window.removeEventListener("mousemove", handleMouseMove, true));

                const handleMouseUp = () => {
                    stopVerticalPan();
                };
                window.addEventListener("mouseup", handleMouseUp);
                interactionCleanup.push(() => window.removeEventListener("mouseup", handleMouseUp));
                window.addEventListener("blur", stopVerticalPan);
                interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));

                const handleLeave = () => {
                    stopVerticalPan();
                };
                container.addEventListener("mouseleave", handleLeave);
                interactionCleanup.push(() => container.removeEventListener("mouseleave", handleLeave));
            }

            const handleDblClick = (event) => {
                if (isPointerInPriceAxis(event)) {
                    resetManualPriceScale();
                }
            };
            container.addEventListener("dblclick", handleDblClick);
            interactionCleanup.push(() => container.removeEventListener("dblclick", handleDblClick));

            function applyWheelPan(event) {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                ensureManualRange(currentRange);
                const paneHeight = getPaneMetrics().paneHeight;
                if (!paneHeight) {
                    return;
                }
                if (ChartAdapterLogic && typeof ChartAdapterLogic.computeWheelPanRange === "function") {
                    const next = ChartAdapterLogic.computeWheelPanRange({
                        range: priceScaleState.manualRange,
                        paneHeight,
                        deltaY: event.deltaY,
                        panFactor: 0.5,
                        minPriceSpan: MIN_PRICE_SPAN,
                    });
                    if (next) {
                        applyManualRange(next);
                        schedulePoiDomLabels();
                    }
                    return;
                }

                const span = priceScaleState.manualRange.max - priceScaleState.manualRange.min;
                if (!(span > 0)) {
                    return;
                }
                const offset = (-event.deltaY / paneHeight) * span * 0.5;
                applyManualRange({
                    min: priceScaleState.manualRange.min + offset,
                    max: priceScaleState.manualRange.max + offset,
                });
                schedulePoiDomLabels();
            }

            function applyWheelZoom(event) {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                const anchor = getAnchorPriceFromEvent(event);
                if (!Number.isFinite(anchor)) {
                    return;
                }
                if (ChartAdapterLogic && typeof ChartAdapterLogic.computeWheelZoomRange === "function") {
                    const next = ChartAdapterLogic.computeWheelZoomRange({
                        range: currentRange,
                        anchor,
                        deltaY: event.deltaY,
                        intensity: 0.002,
                        maxDelta: 600,
                        minPriceSpan: MIN_PRICE_SPAN,
                    });
                    if (next) {
                        applyManualRange(next);
                        schedulePoiDomLabels();
                    }
                    return;
                }

                const span = currentRange.max - currentRange.min;
                if (!(span > 0)) {
                    return;
                }
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
                    schedulePoiDomLabels();
                }
            }
        }


        function setupResizeHandling() {
            if (!container || typeof window === "undefined") {
                return;
            }
            const schedule = () => {
                const raf = window.requestAnimationFrame || window.setTimeout;
                raf(() => resizeToContainer());
            };
            if (typeof ResizeObserver !== "undefined") {
                const resizeObserver = new ResizeObserver(() => {
                    schedule();
                });
                resizeObserver.observe(container);
                interactionCleanup.push(() => {
                    try {
                        resizeObserver.disconnect();
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося відписатися від ResizeObserver", err);
                    }
                });
            } else {
                const handleResize = () => {
                    schedule();
                };
                window.addEventListener("resize", handleResize);
                interactionCleanup.push(() => window.removeEventListener("resize", handleResize));
            }
            schedule();
        }

        function getRelativePointer(event) {
            const rect = container.getBoundingClientRect();
            return {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top,
                width: rect.width,
                height: rect.height,
            };
        }

        function getPaneMetrics() {
            const paneSize = chart.paneSize() || {};
            const priceScaleWidth = chart.priceScale("right").width() || 0;
            return {
                paneWidth: paneSize.width || 0,
                paneHeight: paneSize.height || 0,
                priceScaleWidth,
            };
        }

        function isPointerInPriceAxis(event, priceAxisFallbackWidthPx = 56) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight, priceScaleWidth } = getPaneMetrics();
            if (ChartAdapterLogic && typeof ChartAdapterLogic.isPointerInPriceAxis === "function") {
                return ChartAdapterLogic.isPointerInPriceAxis(
                    {
                        x: pointer.x,
                        y: pointer.y,
                        width: pointer.width,
                        height: pointer.height,
                        paneWidth,
                        paneHeight,
                        priceScaleWidth,
                    },
                    priceAxisFallbackWidthPx
                );
            }
            if (!pointer.width || !pointer.height) {
                return false;
            }

            const effectivePaneHeight = paneHeight || pointer.height;

            // Якщо paneWidth ще 0 (після init/resize), робимо hit-test по “правому краю”.
            const axisLeft = paneWidth > 0
                ? paneWidth
                : Math.max(0, pointer.width - Math.max(0, Number(priceAxisFallbackWidthPx) || 56));

            // Інколи `priceScale("right").width()` може тимчасово бути 0 (під час resize/перемальовки).
            // У такому випадку не ламаємо UX: вважаємо price-axis як «усе правіше paneWidth».
            const axisRight = paneWidth > 0 && priceScaleWidth > 0 ? paneWidth + priceScaleWidth : pointer.width;
            return pointer.x >= axisLeft && pointer.x <= axisRight && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
        }

        function isPointerInsidePane(event, priceAxisFallbackWidthPx = 56) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight } = getPaneMetrics();
            if (ChartAdapterLogic && typeof ChartAdapterLogic.isPointerInsidePane === "function") {
                return ChartAdapterLogic.isPointerInsidePane(
                    {
                        x: pointer.x,
                        y: pointer.y,
                        width: pointer.width,
                        height: pointer.height,
                        paneWidth,
                        paneHeight,
                    },
                    priceAxisFallbackWidthPx
                );
            }
            if (!pointer.width || !pointer.height) {
                return false;
            }

            const effectivePaneHeight = paneHeight || pointer.height;
            const effectivePaneWidth = paneWidth > 0
                ? paneWidth
                : Math.max(0, pointer.width - Math.max(0, Number(priceAxisFallbackWidthPx) || 56));
            return pointer.x >= 0 && pointer.x <= effectivePaneWidth && pointer.y >= 0 && pointer.y <= effectivePaneHeight;
        }

        function getAnchorPriceFromEvent(event) {
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) {
                return null;
            }
            const pointer = getRelativePointer(event);
            const clampedY = Math.max(0, Math.min(pointer.y, paneHeight));
            return candles.coordinateToPrice(clampedY);
        }

        function normalizeRange(range) {
            if (ChartAdapterLogic && typeof ChartAdapterLogic.normalizeRange === "function") {
                return ChartAdapterLogic.normalizeRange(range, MIN_PRICE_SPAN);
            }
            if (!range) {
                return null;
            }
            let { min, max } = range;
            if (!Number.isFinite(min) || !Number.isFinite(max)) {
                return null;
            }
            if (min === max) {
                min -= MIN_PRICE_SPAN / 2;
                max += MIN_PRICE_SPAN / 2;
            }
            if (max - min < MIN_PRICE_SPAN) {
                const mid = (max + min) / 2;
                min = mid - MIN_PRICE_SPAN / 2;
                max = mid + MIN_PRICE_SPAN / 2;
            }
            if (max <= min) {
                return null;
            }
            return { min, max };
        }

        function applyManualRange(range) {
            const normalized = normalizeRange(range);
            if (!normalized) {
                return;
            }
            priceScaleState.manualRange = normalized;
            requestPriceScaleSync();
        }

        function ensureManualRange(baseRange) {
            if (!priceScaleState.manualRange && baseRange) {
                priceScaleState.manualRange = { ...baseRange };
            }
        }

        function getEffectivePriceRange() {
            if (ChartAdapterLogic && typeof ChartAdapterLogic.computeEffectivePriceRange === "function") {
                const { paneHeight } = getPaneMetrics();
                const top = Number.isFinite(paneHeight) && paneHeight > 0 ? candles.coordinateToPrice(0) : null;
                const bottom = Number.isFinite(paneHeight) && paneHeight > 0 ? candles.coordinateToPrice(paneHeight) : null;
                const res = ChartAdapterLogic.computeEffectivePriceRange({
                    manualRange: priceScaleState.manualRange,
                    lastAutoRange: priceScaleState.lastAutoRange,
                    paneHeight,
                    topPrice: top,
                    bottomPrice: bottom,
                });
                if (res && res.nextLastAutoRange) {
                    priceScaleState.lastAutoRange = { ...res.nextLastAutoRange };
                }
                return res ? res.range : null;
            }
            if (priceScaleState.manualRange) {
                return { ...priceScaleState.manualRange };
            }
            if (priceScaleState.lastAutoRange) {
                return { ...priceScaleState.lastAutoRange };
            }
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) {
                return null;
            }
            const top = candles.coordinateToPrice(0);
            const bottom = candles.coordinateToPrice(paneHeight);
            if (!Number.isFinite(top) || !Number.isFinite(bottom)) {
                return null;
            }
            const min = Math.min(top, bottom);
            const max = Math.max(top, bottom);
            if (!(max > min)) {
                return null;
            }
            priceScaleState.lastAutoRange = { min, max };
            return { min, max };
        }

        function requestPriceScaleSync() {
            const logicalRange = chart.timeScale().getVisibleLogicalRange();
            if (logicalRange) {
                chart.timeScale().setVisibleLogicalRange({
                    from: logicalRange.from,
                    to: logicalRange.to,
                });
                return;
            }
            const position = chart.timeScale().scrollPosition();
            if (Number.isFinite(position)) {
                chart.timeScale().scrollToPosition(position, false);
            }
        }

        function resetManualPriceScale(options = {}) {
            priceScaleState.manualRange = null;
            if (!options.silent) {
                requestPriceScaleSync();
            }
        }
    }

    window.createChartController = createChartController;
})();