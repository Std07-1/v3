// Мінімальний клієнт для Lightweight Charts.
// Працює з API: /api/symbols, /api/bars, /api/latest.

// --- API_BASE: portable config ---
// Визначається з ui_config.json або query string ?api_base=...
// Порожній рядок = same-origin (дефолт).
let API_BASE = '';

const elSymbol = document.getElementById('symbol');
const elTf = document.getElementById('tf');
const elReload = document.getElementById('reload');
const elFollow = document.getElementById('follow');
const elTheme = document.getElementById('theme');
const elCandleStyle = document.getElementById('candle-style');
const elStatus = document.getElementById('status');
const chartEl = document.getElementById('chart');
const elDiagLoad = document.getElementById('diag-load');
const elDiagPoll = document.getElementById('diag-poll');
const elDiagLag = document.getElementById('diag-lag');
const elDiagLagSsot = document.getElementById('diag-lag-ssot');
const elDiagLast = document.getElementById('diag-last');
const elDiagBars = document.getElementById('diag-bars');
const elDiagError = document.getElementById('diag-error');
const elDiagUtc = document.getElementById('diag-utc');
const elDiagRedisSource = document.getElementById('diag-redis-source');
const elDiagRedisAge = document.getElementById('diag-redis-age');
const elDiagRedisTtl = document.getElementById('diag-redis-ttl');
const elDiagRedisSeq = document.getElementById('diag-redis-seq');
const elDiagIntervals = document.getElementById('diag-intervals');
const elDiagMeta = document.getElementById('diag-meta');
const elDiag = document.getElementById('diag');
const elDrawerHandle = document.getElementById('drawer-handle');
const elFloatingTools = document.getElementById('floating-tools');
const elHudMenuSymbol = document.getElementById('hud-menu-symbol');
const elHudMenuTf = document.getElementById('hud-menu-tf');
const elHud = document.getElementById('hud');
const elHudSymbol = document.getElementById('hud-symbol');
const elHudTf = document.getElementById('hud-tf');
const elHudStream = document.getElementById('hud-stream');
const elHudPrice = document.getElementById('hud-price');

let controller = null;
let lastOpenMs = null;
let pollTimer = null;
let loadReqId = 0;
let viewEpoch = 0;
let uiEpoch = 0;
let loadAbort = null;
let currentTheme = 'light';
let uiDebugEnabled = true;
let lastHudSymbol = null;
let lastHudTf = null;
let lastUpdatesPlane = null;
let lastUpdatesGap = null;
let lastUpdatesSeq = null;
let updatesSeqCursor = null;
let bootId = null;
let lastApiSeenMs = null;
let lastSsotWriteMs = null;
let lastBarCloseMs = null;
let lastRedisSource = null;
let lastRedisPayloadMs = null;
let lastRedisTtlS = null;
let lastRedisSeq = null;
let lastAlignMode = null;
const updateStateByKey = new Map();
const uiCacheByKey = new Map();
let currentCacheKey = null;
let barsStore = [];
let barsIndexByOpen = new Map();
let firstOpenMs = null;
let scrollbackInFlight = false;
let scrollbackReachedStart = false;
let scrollbackLastReqMs = 0;
let scrollbackAbort = null;
let scrollbackPending = false;
let scrollbackLatestRange = null;
let scrollbackPendingStep = 0;
const cacheLru = new Map();
let updateStateLastCleanupMs = 0;
let favoritesState = null;
let lastContinuityLogMs = 0;

// P2X.6-U1: overlay state
let overlayTimer = null;
const OVERLAY_POLL_INTERVAL_MS = 1000;
const OVERLAY_MIN_TF_S = 300;  // overlay лише для TF ≥ M5
const OVERLAY_MAX_TF_S = 3600;  // H4/D1 без overlay (тільки broker final)
const OVERLAY_PREVIEW_TF_SET = new Set([60, 180]);  // preview TF — overlay не потрібен
const OVERLAY_POLL_FAST_MS = 1000;
const OVERLAY_POLL_SLOW_MS = 2000;
let overlayInFlight = false;
let overlayHeldPrev = false;
let overlayLastBucketOpenMs = null;
let overlayNextDelayMs = null;
let uiPolicy = null;
let policyFallbackActive = false;

let updatesInFlight = false;
let updatesAbort = null;
let updatesEmptyStreak = 0;
let _cursorGapRecoveryMs = 0; // P3: debounce cold-reload
const CURSOR_GAP_RECOVERY_DEBOUNCE_MS = 5000;
const UPDATES_BASE_FINAL_MS = 3000;
const UPDATES_BASE_PREVIEW_MS = 1000;
const UPDATES_BACKOFF_PREVIEW_MS = [1000, 1000, 1000];
const UPDATES_BACKOFF_FINAL_MS = [3000, 5000, 8000];
let updatesNextDelayMs = null;

const RIGHT_OFFSET_PX = 48;
const THEME_KEY = 'ui_chart_theme';
const CANDLE_STYLE_KEY = 'ui_chart_candle_style';
const SYMBOL_KEY = 'ui_chart_symbol';
const TF_KEY = 'ui_chart_tf';
const LAYOUT_SAVE_KEY = 'ui_chart_layout_save';
// ── SSOT window policy (P1) ──────────────────────────────────
// Cold-start = перший load символу/TF.  Максимум = стеля scroll/zoom.
const COLD_START_BARS_BY_TF = {
  60: 10080, // 1m: 7d
  180: 3360, // 3m: 7d
  300: 2016, // 5m: 7d
  900: 672, // 15m: 7d
  1800: 336, // 30m: 7d
  3600: 168, // 1h: 7d
  14400: 1080, // 4h: 180d
  86400: 365, // 1d: 365d
};
const COLD_START_BARS_FALLBACK = 2000;
const MAX_BARS_CAP = 20000;
// Зберігає останній effective limit для кожного view-key (symbol|tf)
const _lastLimitByView = new Map();
const MAX_RENDER_BARS_WARM = 20000;
const WARM_LRU_LIMIT = 6;
const UPDATE_STATE_TTL_MS = 30 * 60 * 1000;
const UPDATE_STATE_CLEANUP_INTERVAL_MS = 60000;
const FAVORITES_KEY = 'ui_chart_favorites_v1';
// ── Scrollback policy (P2) ──────────────────────────────────
const SCROLLBACK_TRIGGER_BARS_BASE = 1000;
const SCROLLBACK_MIN_INTERVAL_MS = 1200;
const SCROLLBACK_CHUNK_MAX_BASE = 2000;
const SCROLLBACK_CHUNK_MIN_BASE = 500;
const SCROLLBACK_EXTRA_BARS = 0;
const SCROLLBACK_MAX_STEPS = 6;
const CONTINUITY_LOG_INTERVAL_MS = 15000;
const UPDATES_DROP_LOG_INTERVAL_MS = 15000;
const SCROLLBACK_CHUNK_BY_TF = {
  300: 1000,
  900: 1000,
  1800: 1000,
  3600: 1000,
};
const DEFAULT_SCROLLBACK_CHUNK = 1000;
const POLICY_FALLBACK_VERSION = 'fallback-local-v1';
// ── Switch debounce (P3) ─────────────────────────────────────
const SWITCH_DEBOUNCE_MS = 120;
let _switchDebounceTimer = null;
const SETDATA_DEBUG_MAX = 20;
let _setDataDebugCount = 0;

function readSelectedTf(defaultTf = 300) {
  const raw = parseInt(elTf?.value, 10);
  if (Number.isFinite(raw) && raw > 0) return raw;
  return defaultTf;
}

function isUiVisible() {
  return !document.hidden;
}

function startNewViewEpoch() {
  viewEpoch += 1;
  uiEpoch += 1;
  if (loadAbort) {
    loadAbort.abort();
    loadAbort = null;
  }
  if (scrollbackAbort) {
    scrollbackAbort.abort();
    scrollbackAbort = null;
  }
  // P4: abort in-flight updates fetch + reset flag
  if (updatesAbort) {
    updatesAbort.abort();
    updatesAbort = null;
  }
  updatesInFlight = false;
  scrollbackInFlight = false;
  scrollbackPending = false;
  scrollbackLatestRange = null;
  scrollbackPendingStep = 0;
  scrollbackReachedStart = false;
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (overlayTimer) {
    clearTimeout(overlayTimer);
    overlayTimer = null;
  }
  updatesSeqCursor = null;
  lastOpenMs = null;
  lastBarCloseMs = null;
  return viewEpoch;
}

function logSetDataDebug(source, symbol, tf, barsCount) {
  if (_setDataDebugCount >= SETDATA_DEBUG_MAX) return;
  _setDataDebugCount += 1;
  console.info('SETDATA', {
    source,
    symbol,
    tf,
    bars: barsCount,
    epoch: uiEpoch,
    n: _setDataDebugCount,
  });
}

const diag = {
  loadAt: null,
  pollAt: null,
  barsTotal: 0,
  lastPollBars: 0,
  lastError: '',
  droppedUpdates: 0,
};

let lastUpdatesDropLogMs = 0;

function warnUpdatesDrop(reason, details) {
  diag.droppedUpdates += 1;
  const now = Date.now();
  if (now - lastUpdatesDropLogMs >= UPDATES_DROP_LOG_INTERVAL_MS) {
    lastUpdatesDropLogMs = now;
    console.warn('UI_UPDATES_DROP', reason, details || '');
  }
  setStatus(`updates_drop:${reason}`);
  updateDiag(readSelectedTf());
}

function fmtAge(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function fmtUtc(ms) {
  if (ms == null) return '—';
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 19).replace('T', ' ')} UTC`;
}

function fmtTtl(v) {
  if (v == null) return '—';
  if (!Number.isFinite(v)) return '—';
  return `${v}s`;
}

function fmtIntervalMs(ms) {
  if (ms == null || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtPrice(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const s = n.toFixed(5).replace(/\.0+$/, '').replace(/\.(\d*?)0+$/, '.$1');
  return s.endsWith('.') ? s.slice(0, -1) : s;
}

function updateUtcNow() {
  const nowUtc = fmtUtc(Date.now());
  if (elDiagUtc) elDiagUtc.textContent = nowUtc;
}

function updateDiag(tfSeconds) {
  const now = Date.now();
  const apiSeenMs = lastApiSeenMs != null ? lastApiSeenMs : now;
  const lagCloseApiMs = (lastBarCloseMs != null)
    ? Math.max(0, apiSeenMs - lastBarCloseMs)
    : null;
  const lagSsotApiMs = (lastSsotWriteMs != null)
    ? Math.max(0, apiSeenMs - lastSsotWriteMs)
    : null;

  elDiagLoad.textContent = diag.loadAt ? fmtAge(now - diag.loadAt) : '—';
  elDiagPoll.textContent = diag.pollAt ? fmtAge(now - diag.pollAt) : '—';
  elDiagLag.textContent = lagCloseApiMs != null ? fmtAge(lagCloseApiMs) : '—';
  if (elDiagLagSsot) {
    elDiagLagSsot.textContent = lagSsotApiMs != null ? fmtAge(lagSsotApiMs) : '—';
  }
  elDiagLast.textContent = lastOpenMs != null ? fmtUtc(lastOpenMs) : '—';
  elDiagBars.textContent = diag.barsTotal ? `${diag.barsTotal} (+${diag.lastPollBars})` : '—';
  elDiagError.textContent = diag.lastError || '—';
  if (elDiagRedisSource) {
    elDiagRedisSource.textContent = lastRedisSource || '—';
  }
  if (elDiagRedisAge) {
    elDiagRedisAge.textContent = lastRedisPayloadMs != null ? fmtAge(now - lastRedisPayloadMs) : '—';
  }
  if (elDiagRedisTtl) {
    elDiagRedisTtl.textContent = fmtTtl(lastRedisTtlS);
  }
  if (elDiagRedisSeq) {
    elDiagRedisSeq.textContent = lastRedisSeq != null ? String(lastRedisSeq) : '—';
  }
  if (elDiagIntervals) {
    const up = fmtIntervalMs(updatesNextDelayMs);
    const ov = fmtIntervalMs(overlayNextDelayMs);
    elDiagIntervals.textContent = `upd=${up} ov=${ov}`;
  }
  if (elDiagMeta) {
    const tf = readSelectedTf(tfSeconds);
    const isPreview = OVERLAY_PREVIEW_TF_SET.has(tf);
    const overlayActive = !isPreview && tf >= OVERLAY_MIN_TF_S && tf <= OVERLAY_MAX_TF_S;
    const plane = isPreview ? 'preview' : (overlayActive ? 'overlay' : 'final');
    const seq = lastUpdatesSeq != null ? String(lastUpdatesSeq) : '—';
    const gap = _formatGap(lastUpdatesGap);
    const held = overlayHeldPrev ? 'yes' : 'no';
    const dropped = Number.isFinite(diag.droppedUpdates) ? diag.droppedUpdates : 0;
    const alignTag = lastAlignMode ? ` align=${lastAlignMode}` : '';
    elDiagMeta.textContent = `plane=${plane} seq=${seq} gap=${gap} held_prev=${held} drop=${dropped}${alignTag}`;
  }
  updateStreamingIndicator();
}

function updateHudPrice(price) {
  if (!elHudPrice) return;
  elHudPrice.textContent = fmtPrice(price);
}

// S4: HUD price policy — єдиний канал, без гонок між plane-ами
let hudInstrumentPrice = null;
let hudInstrumentPriceTsMs = 0;
const HUD_INSTRUMENT_STALE_MS = 60_000;

function updateHudInstrumentPrice(price) {
  if (!Number.isFinite(price)) return;
  hudInstrumentPrice = price;
  hudInstrumentPriceTsMs = Date.now();
  updateHudPrice(price);
}

function updateHudBarPrice(price) {
  if (!Number.isFinite(price)) return;
  if (hudInstrumentPrice != null && (Date.now() - hudInstrumentPriceTsMs) < HUD_INSTRUMENT_STALE_MS) return;
  updateHudPrice(price);
}

function _formatGap(gap) {
  if (!gap || typeof gap !== 'object') return 'no';
  const fromSeq = gap.from_seq ?? gap.fromSeq ?? gap.from;
  const toSeq = gap.to_seq ?? gap.toSeq ?? gap.to;
  if (Number.isFinite(fromSeq) && Number.isFinite(toSeq)) {
    return `${fromSeq}-${toSeq}`;
  }
  return 'yes';
}

function setStatus(txt) {
  if (elStatus) {
    elStatus.textContent = txt;
  }
}

function isLayoutSaveEnabled() {
  return true;
}

function saveLayoutValue(key, value) {
  if (!isLayoutSaveEnabled()) return;
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    // ignore storage errors
  }
}

function safeParseJson(raw, fallback) {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch (e) {
    // ignore parse errors
  }
  return fallback;
}

function loadFavoritesState() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    const parsed = safeParseJson(raw, { symbols: [], tfs: [] });
    const symbols = Array.isArray(parsed.symbols)
      ? parsed.symbols.map((v) => String(v)).filter((v) => v)
      : [];
    const tfs = Array.isArray(parsed.tfs)
      ? parsed.tfs.map((v) => String(v)).filter((v) => v)
      : [];
    return { symbols, tfs };
  } catch (e) {
    return { symbols: [], tfs: [] };
  }
}

function saveFavoritesState(nextState) {
  favoritesState = nextState;
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(nextState));
  } catch (e) {
    // ignore storage errors
  }
}

function getFavoriteSymbols() {
  return favoritesState && Array.isArray(favoritesState.symbols) ? favoritesState.symbols : [];
}

function getFavoriteTfs() {
  return favoritesState && Array.isArray(favoritesState.tfs) ? favoritesState.tfs : [];
}

function normalizeFavoritesSymbols(available) {
  if (!Array.isArray(available)) return;
  const allowed = new Set(available.map((v) => String(v)));
  const current = getFavoriteSymbols();
  const filtered = current.filter((v) => allowed.has(String(v)));
  if (filtered.length !== current.length) {
    saveFavoritesState({ symbols: filtered, tfs: getFavoriteTfs() });
  }
}

function normalizeFavoritesTfs(available) {
  if (!Array.isArray(available)) return;
  const allowed = new Set(available.map((v) => String(v)));
  const current = getFavoriteTfs();
  const filtered = current.filter((v) => allowed.has(String(v)));
  if (filtered.length !== current.length) {
    saveFavoritesState({ symbols: getFavoriteSymbols(), tfs: filtered });
  }
}

function toggleFavoriteSymbol(symbol) {
  const value = String(symbol || '');
  if (!value) return;
  const current = getFavoriteSymbols();
  const exists = current.includes(value);
  const next = exists ? current.filter((v) => v !== value) : current.concat(value);
  saveFavoritesState({ symbols: next, tfs: getFavoriteTfs() });
}

function toggleFavoriteTf(tfValue) {
  const value = String(tfValue || '');
  if (!value) return;
  const current = getFavoriteTfs();
  const exists = current.includes(value);
  const next = exists ? current.filter((v) => v !== value) : current.concat(value);
  saveFavoritesState({ symbols: getFavoriteSymbols(), tfs: next });
}

function updateStreamingIndicator() {
  if (!elHudStream) return;
  const now = Date.now();
  const recent = diag.pollAt && now - diag.pollAt < 12000;
  const paused = Boolean(diag.lastError) || !recent;
  elHudStream.classList.toggle('streaming', !paused);
  elHudStream.classList.toggle('paused', paused);
}

function updateHudValues() {
  if (elHudSymbol) {
    const opt = elSymbol && elSymbol.selectedIndex >= 0 ? elSymbol.options[elSymbol.selectedIndex] : null;
    const nextSymbol = opt ? opt.textContent : (elSymbol && elSymbol.value ? elSymbol.value : '—');
    if (lastHudSymbol !== null && nextSymbol !== lastHudSymbol) {
      elHudSymbol.classList.remove('pulse');
      void elHudSymbol.offsetWidth;
      elHudSymbol.classList.add('pulse');
    }
    elHudSymbol.textContent = nextSymbol;
    lastHudSymbol = nextSymbol;
  }
  if (elHudTf) {
    const opt = elTf && elTf.selectedIndex >= 0 ? elTf.options[elTf.selectedIndex] : null;
    const nextTf = opt ? opt.textContent : '—';
    if (lastHudTf !== null && nextTf !== lastHudTf) {
      elHudTf.classList.remove('pulse');
      void elHudTf.offsetWidth;
      elHudTf.classList.add('pulse');
    }
    elHudTf.textContent = nextTf;
    lastHudTf = nextTf;
  }
  syncHudMenuWidth();
}

function syncHudMenuWidth() {
  if (!elHud || !elHud.parentElement) return;
  const width = Math.round(elHud.getBoundingClientRect().width || 0);
  if (!width) return;
  elHud.parentElement.style.setProperty('--hud-width', `${width}px`);
}

async function apiGet(url, opts = {}) {
  const fullUrl = API_BASE ? API_BASE + url : url;
  const r = await fetch(fullUrl, { cache: 'no-store', signal: opts.signal });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}

async function loadUiConfig() {
  // 1. Query string override: ?api_base=http://host:port
  const params = new URLSearchParams(window.location.search);
  const qsBase = (params.get('api_base') || '').replace(/\/+$/, '');
  if (qsBase) {
    API_BASE = qsBase;
    console.log('[ui_config] api_base від query string:', API_BASE);
  }

  // 2. Portable ui_config.json (статичний файл поряд з app.js)
  if (!API_BASE) {
    try {
      const r = await fetch('ui_config.json', { cache: 'no-store' });
      if (r.ok) {
        const uiCfg = await r.json();
        if (uiCfg.api_base) {
          API_BASE = String(uiCfg.api_base).replace(/\/+$/, '');
          console.log('[ui_config] api_base від ui_config.json:', API_BASE);
        }
        if (typeof uiCfg.ui_debug === 'boolean') {
          uiDebugEnabled = uiCfg.ui_debug;
        }
      }
    } catch (_) { /* ui_config.json не обов'язковий */ }
  }

  // 3. Серверний /api/config (доповнює, не перезаписує api_base)
  try {
    const data = await apiGet('/api/config');
    if (data && typeof data.ui_debug === 'boolean') {
      uiDebugEnabled = data.ui_debug;
    }
    if (data && data.window_policy && typeof data.window_policy === 'object') {
      uiPolicy = {
        policyVersion: String(data.policy_version || ''),
        buildId: String(data.build_id || ''),
        windowPolicy: data.window_policy,
      };
      policyFallbackActive = false;
      console.info('UI_POLICY_LOADED', {
        policy_version: uiPolicy.policyVersion,
        build_id: uiPolicy.buildId,
        config_invalid: Boolean(data.config_invalid),
        warnings: Array.isArray(data.warnings) ? data.warnings : [],
      });
    } else {
      policyFallbackActive = true;
      console.warn('UI_POLICY_FALLBACK_ACTIVE reason=missing_window_policy policy_version=' + POLICY_FALLBACK_VERSION);
      setStatus('policy_fallback_active');
    }
  } catch (e) {
    uiDebugEnabled = true;
    policyFallbackActive = true;
    console.warn('UI_POLICY_FALLBACK_ACTIVE reason=api_config_unavailable policy_version=' + POLICY_FALLBACK_VERSION);
    setStatus('policy_fallback_active');
  }
  applyUiDebug();
}

function getPolicyMap(name, fallbackMap) {
  if (!uiPolicy || !uiPolicy.windowPolicy) {
    return fallbackMap;
  }
  const source = uiPolicy.windowPolicy[name];
  if (!source || typeof source !== 'object') {
    return fallbackMap;
  }
  const out = {};
  for (const [k, v] of Object.entries(source)) {
    const key = Number(k);
    const val = Number(v);
    if (Number.isFinite(key) && Number.isFinite(val) && val > 0) {
      out[key] = Math.floor(val);
    }
  }
  return Object.keys(out).length ? out : fallbackMap;
}

function getPolicyMaxBarsCap() {
  const cap = Number(uiPolicy?.windowPolicy?.max_bars_cap);
  if (Number.isFinite(cap) && cap > 0) return Math.floor(cap);
  return MAX_BARS_CAP;
}

function applyUiDebug() {
  const show = Boolean(uiDebugEnabled);
  document.body.classList.toggle('ui-debug-off', !show);
  if (elDiag) elDiag.style.display = show ? '' : 'none';
  if (elFollow) elFollow.closest('.follow-toggle')?.style.setProperty('display', show ? '' : 'none');
}

const REQUIRED_CONTROLLER_METHODS = [
  'setBars',
  'updateLastBar',
  'resetViewAndFollow',
  'resizeToContainer',
  'clearAll',
  'setViewTimeframe',
  'setTheme',
  'setCandleStyle',
  'isAtEnd',
  'scrollToRealTime',
  'scrollToRealTimeWithOffset',
  'setFollowRightOffsetPx',
  'getVisibleLogicalRange',
  'setVisibleLogicalRange',
  'onVisibleLogicalRangeChange',
  'barsInLogicalRange',
];

function ensureControllerContract(ctrl) {
  const missing = REQUIRED_CONTROLLER_METHODS.filter((name) => !ctrl || typeof ctrl[name] !== 'function');
  if (missing.length) {
    const msg = `chart_adapter: бракує методів: ${missing.join(', ')}`;
    setStatus('init_error');
    throw new Error(msg);
  }
}

function makeChart() {
  chartEl.innerHTML = '';
  if (typeof window.createChartController !== 'function') {
    throw new Error('chart_adapter: createChartController не доступний');
  }
  const tooltipEl = document.getElementById('chart-hover-tooltip');
  controller = window.createChartController(chartEl, { tooltipEl });
  ensureControllerContract(controller);
  if (controller && typeof controller.setFollowRightOffsetPx === 'function') {
    controller.setFollowRightOffsetPx(RIGHT_OFFSET_PX);
  }
  if (controller && typeof controller.onVisibleLogicalRangeChange === 'function') {
    controller.onVisibleLogicalRangeChange(handleVisibleRangeChange);
  }
  window.addEventListener('resize', () => {
    if (controller && typeof controller.resizeToContainer === 'function') {
      controller.resizeToContainer();
    }
  });
  if (controller && typeof controller.resizeToContainer === 'function') {
    controller.resizeToContainer();
  }
}

function applyTheme(theme) {
  const mode = theme || 'light';
  currentTheme = mode;
  document.body.classList.remove('dark', 'dark-gray');
  if (mode === 'dark') document.body.classList.add('dark');
  if (mode === 'dark-gray') document.body.classList.add('dark-gray');
  document.body.dataset.theme = mode;
  setStatus(`theme=${mode}`);
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch (e) {
    // ignore storage errors
  }
  if (controller && typeof controller.setTheme === 'function') {
    controller.setTheme(mode);
  }
}

function applyCandleStyle(style) {
  if (controller && typeof controller.setCandleStyle === 'function') {
    controller.setCandleStyle(style);
  }
}

const TOOLBAR_MENU_IDS = ['symbol', 'tf', 'candle-style'];
const TOOLBAR_ALL_IDS = ['symbol', 'tf', 'candle-style', 'theme'];

function getToolbarGroup(selectId) {
  return document.querySelector(`.toolbar-group[data-select="${selectId}"], .toolbar-group[data-toggle="${selectId}"]`);
}

function openToolbarMenu(selectId) {
  const group = getToolbarGroup(selectId);
  if (!group) return;
  if (elFloatingTools) {
    elFloatingTools.classList.remove('is-hidden');
  }
  closeAllToolMenus(selectId);
  buildToolbarMenu(selectId);
  group.classList.add('open');
}

function openHudMenu(selectId) {
  const select = document.getElementById(selectId);
  const menu = selectId === 'symbol' ? elHudMenuSymbol : elHudMenuTf;
  if (!select || !menu) return;
  if (menu.classList.contains('open')) {
    closeHudMenus();
    return;
  }
  closeAllToolMenus();
  buildHudMenu(select, menu);
  menu.classList.add('open');
}

function closeHudMenus() {
  if (elHudMenuSymbol) elHudMenuSymbol.classList.remove('open');
  if (elHudMenuTf) elHudMenuTf.classList.remove('open');
}

function closeAllToolMenus(exceptId = null) {
  for (const id of TOOLBAR_MENU_IDS) {
    if (id === exceptId) continue;
    const group = getToolbarGroup(id);
    if (group) group.classList.remove('open');
  }
  closeHudMenus();
}

function updateToolbarValue(selectId) {
  const select = document.getElementById(selectId);
  const group = getToolbarGroup(selectId);
  if (!select || !group) return;
  const valueEl = group.querySelector('[data-value]');
  const option = select.options && select.selectedIndex >= 0
    ? select.options[select.selectedIndex]
    : null;
  if (valueEl) {
    valueEl.textContent = option ? option.textContent : (select.value || '—');
  }
  const menu = group.querySelector('.tool-menu');
  if (!menu) {
    updateHudValues();
    return;
  }
  for (const btn of menu.querySelectorAll('.tool-option')) {
    btn.classList.toggle('active', btn.dataset.value === select.value);
  }
  updateHudValues();
}

function getSelectOptions(select) {
  return Array.from(select?.options || []);
}

function isFavoriteValue(selectId, value) {
  if (selectId === 'symbol') return getFavoriteSymbols().includes(String(value));
  if (selectId === 'tf') return getFavoriteTfs().includes(String(value));
  return false;
}

function splitOptionsByFavorites(selectId, options) {
  const fav = [];
  const rest = [];
  for (const opt of options) {
    if (isFavoriteValue(selectId, opt.value)) {
      fav.push(opt);
    } else {
      rest.push(opt);
    }
  }
  return { fav, rest };
}

function appendMenuSection(menu, title) {
  const section = document.createElement('div');
  section.className = 'tool-menu-section';
  section.textContent = title;
  menu.appendChild(section);
}

function createMenuOptionButton(selectId, select, opt, closeMenu) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'tool-option';
  btn.dataset.value = opt.value;
  const isFav = isFavoriteValue(selectId, opt.value);
  if (isFav) btn.classList.add('is-fav');
  if (opt.value === select.value) btn.classList.add('active');

  const label = document.createElement('span');
  label.className = 'tool-option-label';
  label.textContent = opt.textContent;
  btn.appendChild(label);

  const favBtn = document.createElement('span');
  favBtn.className = 'fav-btn';
  favBtn.textContent = 'fav';
  favBtn.title = isFav ? 'Зняти з улюблених' : 'Додати в улюблені';
  if (!isFav) favBtn.classList.add('is-off');
  favBtn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (selectId === 'symbol') {
      toggleFavoriteSymbol(opt.value);
    } else if (selectId === 'tf') {
      toggleFavoriteTf(opt.value);
    }
    buildToolbarMenu(selectId);
    if (selectId === 'symbol' && elHudMenuSymbol) buildHudMenu(elSymbol, elHudMenuSymbol);
    if (selectId === 'tf' && elHudMenuTf) buildHudMenu(elTf, elHudMenuTf);
  });
  btn.appendChild(favBtn);

  btn.addEventListener('click', () => {
    select.value = opt.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    if (typeof closeMenu === 'function') closeMenu();
  });

  return btn;
}

function buildToolbarMenu(selectId) {
  const select = document.getElementById(selectId);
  const group = getToolbarGroup(selectId);
  if (!select || !group) return;
  const menu = group.querySelector('.tool-menu');
  if (!menu) {
    updateToolbarValue(selectId);
    return;
  }
  menu.innerHTML = '';
  const options = getSelectOptions(select);
  if (options.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'tool-option';
    empty.textContent = '—';
    menu.appendChild(empty);
    updateToolbarValue(selectId);
    return;
  }
  const { fav, rest } = splitOptionsByFavorites(selectId, options);
  const closeMenu = () => {
    const groupLocal = getToolbarGroup(selectId);
    if (groupLocal) groupLocal.classList.remove('open');
  };
  if (fav.length > 0) {
    appendMenuSection(menu, 'Улюблені');
    for (const opt of fav) {
      menu.appendChild(createMenuOptionButton(selectId, select, opt, closeMenu));
    }
  }
  if (rest.length > 0) {
    if (fav.length > 0) appendMenuSection(menu, 'Усі');
    for (const opt of rest) {
      menu.appendChild(createMenuOptionButton(selectId, select, opt, closeMenu));
    }
  }
  updateToolbarValue(selectId);
}

function buildHudMenu(select, menu) {
  menu.innerHTML = '';
  const options = getSelectOptions(select);
  if (options.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'tool-option';
    empty.textContent = '—';
    menu.appendChild(empty);
    return;
  }
  const selectId = select.id;
  const { fav, rest } = splitOptionsByFavorites(selectId, options);
  if (fav.length > 0) {
    appendMenuSection(menu, 'Улюблені');
    for (const opt of fav) {
      menu.appendChild(createMenuOptionButton(selectId, select, opt, closeHudMenus));
    }
  }
  if (rest.length > 0) {
    if (fav.length > 0) appendMenuSection(menu, 'Усі');
    for (const opt of rest) {
      menu.appendChild(createMenuOptionButton(selectId, select, opt, closeHudMenus));
    }
  }
}

function setupToolbarSelect(selectId) {
  const select = document.getElementById(selectId);
  const group = getToolbarGroup(selectId);
  if (!select || !group) return;
  const button = group.querySelector('.tool-button');
  if (button) {
    if (selectId === 'theme') {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        const next = select.value === 'dark' ? 'light' : 'dark';
        select.value = next;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        updateToolbarValue(selectId);
      });
    } else if (selectId !== 'symbol' && selectId !== 'tf') {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        const isOpen = group.classList.contains('open');
        closeAllToolMenus(selectId);
        if (!isOpen) {
          buildToolbarMenu(selectId);
          group.classList.add('open');
        } else {
          group.classList.remove('open');
        }
      });
    }
  }
  select.addEventListener('change', () => updateToolbarValue(selectId));
  buildToolbarMenu(selectId);
}

function initToolbars() {
  for (const id of TOOLBAR_ALL_IDS) {
    setupToolbarSelect(id);
  }
  document.addEventListener('click', () => closeAllToolMenus());
  if (elDrawerHandle) {
    elDrawerHandle.addEventListener('click', (event) => {
      event.stopPropagation();
      if (elFloatingTools) {
        elFloatingTools.classList.toggle('is-hidden');
      }
    });
  }
  attachHudWheelControls();
  syncHudMenuWidth();
}

function cycleSelectValue(select, direction, preferredValues = []) {
  if (!select) return;
  const options = getSelectOptions(select);
  if (options.length === 0) return;
  const allowed = Array.isArray(preferredValues)
    ? preferredValues.filter((v) => options.some((opt) => opt.value === String(v)))
    : [];
  const values = allowed.length > 0 ? allowed : options.map((opt) => opt.value);
  if (values.length === 0) return;
  const currentValue = select.value;
  let index = values.indexOf(currentValue);
  if (index < 0) index = 0;
  else index = (index + direction + values.length) % values.length;
  select.value = values[index];
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

function getPreferredValuesForSelect(selectId) {
  if (selectId === 'symbol') return getFavoriteSymbols();
  if (selectId === 'tf') return getFavoriteTfs();
  return [];
}

function attachHudWheelControls() {
  if (elHudSymbol) {
    elHudSymbol.addEventListener('wheel', (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 1 : -1;
      cycleSelectValue(elSymbol, direction, getPreferredValuesForSelect('symbol'));
    }, { passive: false });
    elHudSymbol.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      openHudMenu('symbol');
    });
  }
  if (elHudTf) {
    elHudTf.addEventListener('wheel', (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 1 : -1;
      cycleSelectValue(elTf, direction, getPreferredValuesForSelect('tf'));
    }, { passive: false });
    elHudTf.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      openHudMenu('tf');
    });
  }
}


async function loadSymbols() {
  const data = await apiGet('/api/symbols');
  const syms = data.symbols || [];
  const preferred = elSymbol.value;
  elSymbol.innerHTML = '';
  for (const s of syms) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    elSymbol.appendChild(opt);
  }
  if (syms.length === 0) {
    const opt = document.createElement('option');
    opt.value = 'XAU/USD';
    opt.textContent = 'XAU/USD';
    elSymbol.appendChild(opt);
  }
  if (preferred && Array.from(elSymbol.options).some((opt) => opt.value === preferred)) {
    elSymbol.value = preferred;
  }
  normalizeFavoritesSymbols(syms.length ? syms : ['XAU/USD']);
  buildToolbarMenu('symbol');
  updateHudValues();
  return syms;
}

function rebuildBarsIndex() {
  if (barsStore.length > 1) {
    let sorted = true;
    for (let i = 1; i < barsStore.length; i += 1) {
      const prev = barsStore[i - 1]?.open_time_ms;
      const curr = barsStore[i]?.open_time_ms;
      if (!Number.isFinite(prev) || !Number.isFinite(curr)) continue;
      if (curr < prev) {
        sorted = false;
        break;
      }
    }
    if (!sorted) {
      barsStore.sort((a, b) => (a.open_time_ms || 0) - (b.open_time_ms || 0));
    }
  }
  barsIndexByOpen = new Map();
  for (let i = 0; i < barsStore.length; i += 1) {
    const openMs = barsStore[i]?.open_time_ms;
    if (Number.isFinite(openMs)) {
      barsIndexByOpen.set(openMs, i);
    }
  }
  firstOpenMs = barsStore.length ? barsStore[0].open_time_ms : null;
}

function getTfOptionsList() {
  const values = getSelectOptions(elTf).map((opt) => Number(opt.value));
  return values.filter((v) => Number.isFinite(v) && v > 0);
}

function isFavoriteSymbol(symbol) {
  if (!symbol) return false;
  return getFavoriteSymbols().includes(String(symbol));
}

function isFavoriteTf(tf) {
  if (!Number.isFinite(tf)) return false;
  return getFavoriteTfs().includes(String(tf));
}

function isFavoritePair(symbol, tf) {
  return isFavoriteSymbol(symbol) || isFavoriteTf(tf);
}

function getActiveMaxBars() {
  const tf = readSelectedTf();
  const maxBarsCap = getPolicyMaxBarsCap();
  const key = currentCacheKey || makeCacheKey(elSymbol?.value || '', tf);
  // Якщо view вже відвідували й user проскролив — використати last limit
  const last = _lastLimitByView.get(key);
  if (Number.isFinite(last) && last > 0) {
    return Math.min(last, maxBarsCap);
  }
  // Cold-start limit
  const coldMap = getPolicyMap('cold_start_bars_by_tf', COLD_START_BARS_BY_TF);
  const cold = coldMap[tf];
  if (!cold) {
    console.warn('LIMIT_POLICY_MISSING tf=' + tf + ', fallback=' + COLD_START_BARS_FALLBACK);
  }
  return Math.min(cold || COLD_START_BARS_FALLBACK, maxBarsCap);
}

function getScrollbackTriggerBars() {
  const symbol = elSymbol?.value;
  const tf = readSelectedTf();
  const isFav = isFavoritePair(symbol, tf);
  return isFav ? SCROLLBACK_TRIGGER_BARS_BASE * 2 : SCROLLBACK_TRIGGER_BARS_BASE;
}

function getScrollbackChunkMin() {
  const symbol = elSymbol?.value;
  const tf = readSelectedTf();
  const isFav = isFavoritePair(symbol, tf);
  return isFav ? SCROLLBACK_CHUNK_MIN_BASE * 2 : SCROLLBACK_CHUNK_MIN_BASE;
}

function getScrollbackChunkMax() {
  const symbol = elSymbol?.value;
  const tf = readSelectedTf();
  const isFav = isFavoritePair(symbol, tf);
  return isFav ? SCROLLBACK_CHUNK_MAX_BASE * 2 : SCROLLBACK_CHUNK_MAX_BASE;
}

function getNeighborTf(tf) {
  const list = getTfOptionsList();
  if (!Number.isFinite(tf) || list.length === 0) return null;
  const idx = list.findIndex((v) => v === tf);
  if (idx < 0) return null;
  if (idx + 1 < list.length) return list[idx + 1];
  if (idx - 1 >= 0) return list[idx - 1];
  return null;
}

function getPinnedCacheKeys() {
  const pinned = new Set();
  if (currentCacheKey) pinned.add(currentCacheKey);
  const symbol = elSymbol?.value;
  const tf = readSelectedTf();
  const neighborTf = getNeighborTf(tf);
  if (symbol && Number.isFinite(neighborTf)) {
    pinned.add(makeCacheKey(symbol, neighborTf));
  }
  return pinned;
}

function trimBarsForLimit(bars, limit) {
  if (!Array.isArray(bars)) return [];
  if (!Number.isFinite(limit) || limit <= 0) return bars.slice();
  return bars.length > limit ? bars.slice(-limit) : bars.slice();
}

function touchCacheKey(key) {
  if (!key) return;
  if (cacheLru.has(key)) cacheLru.delete(key);
  cacheLru.set(key, Date.now());
}

function dropUpdateStateForKey(cacheKey) {
  if (!cacheKey) return;
  const prefix = `${cacheKey}|`;
  for (const key of updateStateByKey.keys()) {
    if (key.startsWith(prefix)) updateStateByKey.delete(key);
  }
}

function normalizeWarmCaches(pinned) {
  for (const [key, entry] of uiCacheByKey.entries()) {
    if (pinned.has(key)) continue;
    const bars = Array.isArray(entry.bars) ? entry.bars : [];
    if (bars.length > MAX_RENDER_BARS_WARM) {
      entry.bars = bars.slice(-MAX_RENDER_BARS_WARM);
    }
  }
}

function evictWarmCaches(pinned) {
  const nonPinnedKeys = () => Array.from(cacheLru.keys()).filter((key) => !pinned.has(key));
  let warmKeys = nonPinnedKeys();
  while (warmKeys.length > WARM_LRU_LIMIT) {
    const key = warmKeys.shift();
    if (!key) break;
    uiCacheByKey.delete(key);
    cacheLru.delete(key);
    dropUpdateStateForKey(key);
    warmKeys = nonPinnedKeys();
  }
}

function cleanupUpdateStateCache(allowedPairs) {
  const now = Date.now();
  if (now - updateStateLastCleanupMs < UPDATE_STATE_CLEANUP_INTERVAL_MS) return;
  updateStateLastCleanupMs = now;
  const allowed = allowedPairs || new Set();
  for (const [key, value] of updateStateByKey.entries()) {
    const ts = Number(value?.ts || 0);
    const pairKey = value?.pairKey ? String(value.pairKey) : '';
    if (ts && now - ts > UPDATE_STATE_TTL_MS) {
      updateStateByKey.delete(key);
      continue;
    }
    if (pairKey && !allowed.has(pairKey)) {
      updateStateByKey.delete(key);
    }
  }
}

function makeCacheKey(symbol, tf) {
  return `${symbol}|${tf}`;
}

function saveCacheCurrent() {
  const fallbackSymbol = elSymbol?.value;
  const fallbackTf = Number(elTf?.value);
  const key = currentCacheKey || (fallbackSymbol && Number.isFinite(fallbackTf)
    ? makeCacheKey(fallbackSymbol, fallbackTf)
    : null);
  if (!key) return;
  if (!barsStore.length) return;
  const trimmedBars = trimBarsForLimit(barsStore, getActiveMaxBars());
  uiCacheByKey.set(key, {
    bars: trimmedBars,
    lastOpenMs,
    lastBarCloseMs,
    updatesSeqCursor,
    scrollbackReachedStart,
  });
  touchCacheKey(key);
  currentCacheKey = key;
  const pinned = getPinnedCacheKeys();
  normalizeWarmCaches(pinned);
  evictWarmCaches(pinned);
}

function storeCacheFor(symbol, tf, bars) {
  if (!symbol || !Number.isFinite(tf)) return;
  if (!Array.isArray(bars) || bars.length === 0) return;
  const key = makeCacheKey(symbol, tf);
  if (uiCacheByKey.has(key)) return;
  const trimmedBars = trimBarsForLimit(bars, MAX_RENDER_BARS_WARM);
  const last = trimmedBars[trimmedBars.length - 1];
  const lastOpen = Number.isFinite(last.open_time_ms) ? last.open_time_ms : null;
  let lastClose = Number.isFinite(last.close_time_ms) ? last.close_time_ms : null;
  if (lastClose == null && lastOpen != null) {
    lastClose = lastOpen + tf * 1000 - 1;
  }
  uiCacheByKey.set(key, {
    bars: trimmedBars,
    lastOpenMs: lastOpen,
    lastBarCloseMs: lastClose,
    updatesSeqCursor: null,
    scrollbackReachedStart: false,
  });
  touchCacheKey(key);
  const pinned = getPinnedCacheKeys();
  normalizeWarmCaches(pinned);
  evictWarmCaches(pinned);
}


function restoreCacheFor(symbol, tf) {
  startNewViewEpoch();
  const key = makeCacheKey(symbol, tf);
  const cached = uiCacheByKey.get(key);
  if (!cached) return false;
  const bars = Array.isArray(cached.bars) ? cached.bars : [];
  if (!bars.length) return false;
  // P4: perf mark for cache-restore render
  // D1 fix: встановити TF ДО setBars, щоб normalizeBar знав актуальний barTimeSpanSeconds
  if (controller && typeof controller.setViewTimeframe === 'function') {
    controller.setViewTimeframe(tf);
  }
  const _t0 = performance.now();
  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(bars);
    logSetDataDebug('cache_restore', symbol, tf, bars.length);
  }
  setBarsStore(bars);
  const _dt = performance.now() - _t0;
  if (_dt > 50) console.warn(`LONG_TASK_RENDER cache_restore tf=${tf} bars=${bars.length} ms=${_dt.toFixed(1)}`);
  applyTheme(currentTheme);
  if (elCandleStyle) {
    applyCandleStyle(elCandleStyle.value || 'classic');
  }
  lastOpenMs = Number.isFinite(cached.lastOpenMs) ? cached.lastOpenMs : bars[bars.length - 1].open_time_ms;
  lastBarCloseMs = Number.isFinite(cached.lastBarCloseMs) ? cached.lastBarCloseMs : null;
  updatesSeqCursor = Number.isFinite(cached.updatesSeqCursor) ? cached.updatesSeqCursor : null;
  scrollbackReachedStart = Boolean(cached.scrollbackReachedStart);
  scrollbackPending = false;
  scrollbackLatestRange = null;
  scrollbackPendingStep = 0;
  lastRedisSource = 'cache';
  lastRedisPayloadMs = null;
  lastRedisTtlS = null;
  lastRedisSeq = null;
  diag.barsTotal = bars.length;
  diag.lastPollBars = 0;
  diag.loadAt = Date.now();
  updateHudBarPrice(bars[bars.length - 1].close);
  updateDiag(Number(tf));
  if (elFollow.checked && controller && typeof controller.resetViewAndFollow === 'function') {
    controller.resetViewAndFollow(RIGHT_OFFSET_PX);
  }
  setStatus(`ok · cache · tf=${tf}s · bars=${bars.length}`);
  currentCacheKey = key;
  touchCacheKey(key);
  const pinned = getPinnedCacheKeys();
  normalizeWarmCaches(pinned);
  evictWarmCaches(pinned);
  return true;
}

function setBarsStore(bars) {
  const _t0 = performance.now();
  barsStore = Array.isArray(bars) ? bars.slice() : [];
  const maxBars = getActiveMaxBars();
  if (barsStore.length > maxBars) {
    barsStore = barsStore.slice(-maxBars);
  }
  rebuildBarsIndex();
  const _dt = performance.now() - _t0;
  if (_dt > 50) console.warn(`LONG_TASK_RENDER setBarsStore bars=${barsStore.length} ms=${_dt.toFixed(1)}`);
}

function upsertBarToStore(bar) {
  if (!bar || !Number.isFinite(bar.open_time_ms)) return;
  const openMs = bar.open_time_ms;
  const idx = barsIndexByOpen.get(openMs);
  if (idx != null) {
    barsStore[idx] = bar;
    return;
  }
  barsStore.push(bar);
  if (barsStore.length > 1 && Number.isFinite(lastOpenMs) && openMs < lastOpenMs) {
    barsStore.sort((a, b) => a.open_time_ms - b.open_time_ms);
  }
  const maxBars = getActiveMaxBars();
  if (barsStore.length > maxBars) {
    barsStore = barsStore.slice(-maxBars);
  }
  rebuildBarsIndex();
}

function getScrollbackChunk(tf) {
  const chunkMap = getPolicyMap('scrollback_chunk_by_tf', SCROLLBACK_CHUNK_BY_TF);
  return chunkMap[tf] || DEFAULT_SCROLLBACK_CHUNK;
}

function computeScrollbackNeed(range) {
  const from = Number(range?.from);
  const to = Number(range?.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  const targetLeftBuffer = getScrollbackTriggerBars();
  const deficitLeft = from < 0 ? Math.max(0, -Math.floor(from)) : 0;
  return { from, to, targetLeftBuffer, deficitLeft };
}

function computeScrollbackChunk(tf, range, barsBefore) {
  const base = getScrollbackChunk(tf);
  if (!base) return { chunk: 0, need: 0 };
  const needRange = computeScrollbackNeed(range);
  if (!needRange) return { chunk: base, need: base };
  const before = Number.isFinite(barsBefore) ? Math.max(0, barsBefore) : 0;
  const need = Math.max(0, needRange.targetLeftBuffer - before) + needRange.deficitLeft;
  const raw = Math.max(need + SCROLLBACK_EXTRA_BARS, getScrollbackChunkMin());
  const chunk = Math.min(getScrollbackChunkMax(), raw);
  return { chunk, need };
}

function computeBarsBefore(range) {
  if (controller && typeof controller.barsInLogicalRange === 'function') {
    const info = controller.barsInLogicalRange(range);
    if (info && Number.isFinite(info.barsBefore)) return info.barsBefore;
  }
  if (range && Number.isFinite(range.from)) return Math.floor(range.from);
  return null;
}

function logContinuityIfNeeded(tf) {
  const now = Date.now();
  if (now - lastContinuityLogMs < CONTINUITY_LOG_INTERVAL_MS) return;
  if (!Number.isFinite(tf) || tf <= 0) return;
  if (!Array.isArray(barsStore) || barsStore.length < 2) return;
  const expected = tf;
  // P2: HTF gap tolerance — D1 7d, H4 2d (вихідні/свята)
  const gapMultiplier = tf >= 86400 ? 8 : (tf >= 14400 ? 5 : 3);
  let maxDt = 0;
  let gaps = 0;
  for (let i = 1; i < barsStore.length; i += 1) {
    const prev = barsStore[i - 1]?.open_time_ms;
    const curr = barsStore[i]?.open_time_ms;
    if (!Number.isFinite(prev) || !Number.isFinite(curr)) continue;
    const dt = Math.max(0, Math.round((curr - prev) / 1000));
    if (dt > maxDt) maxDt = dt;
    if (dt > expected * gapMultiplier) gaps += 1;
  }
  lastContinuityLogMs = now;
  const htfNote = tf >= 14400 ? ' (htf:calendar_gaps_ok)' : '';
  console.info(`UI_CONTINUITY tf=${tf}s bars=${barsStore.length} max_dt_s=${maxDt} gaps_gt_${gapMultiplier}x=${gaps}${htfNote}`);
}

function mergeOlderBars(olderBars, prevRange, tf) {
  if (!Array.isArray(olderBars) || olderBars.length === 0) {
    scrollbackReachedStart = true;
    return;
  }
  if (!Number.isFinite(firstOpenMs)) return;
  const added = [];
  for (const bar of olderBars) {
    const openMs = bar?.open_time_ms;
    if (!Number.isFinite(openMs)) continue;
    if (openMs >= firstOpenMs) continue;
    if (barsIndexByOpen.has(openMs)) continue;
    added.push(bar);
  }
  if (added.length === 0) {
    scrollbackReachedStart = true;
    return;
  }
  added.sort((a, b) => a.open_time_ms - b.open_time_ms);
  const maxBars = getActiveMaxBars();
  const space = Math.max(0, maxBars - barsStore.length);
  const take = space > 0 ? added.slice(-space) : [];
  if (take.length === 0) return;
  barsStore = take.concat(barsStore);
  rebuildBarsIndex();
  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(barsStore);
    logSetDataDebug('scrollback', elSymbol.value, tf, barsStore.length);
  }
  if (prevRange && controller && typeof controller.setVisibleLogicalRange === 'function') {
    controller.setVisibleLogicalRange({
      from: prevRange.from + take.length,
      to: prevRange.to + take.length,
    });
  }
  diag.barsTotal = barsStore.length;
  // P1: оновити last limit після scrollback
  if (currentCacheKey) {
    _lastLimitByView.set(currentCacheKey, Math.min(barsStore.length, MAX_BARS_CAP));
  }
  logContinuityIfNeeded(tf);
  saveCacheCurrent();
}

function scheduleEnsureCoverage(range, step) {
  window.requestAnimationFrame(() => {
    ensureLeftCoverage(range, step, 'loop');
  });
}

async function loadScrollbackChunk(range, step) {
  const tf = readSelectedTf();
  const barsBefore = computeBarsBefore(range);
  const need = computeScrollbackNeed(range);
  if (!need) return;
  const calc = computeScrollbackChunk(tf, range, barsBefore);
  if (!calc.chunk) return;
  if (!Number.isFinite(firstOpenMs)) return;
  if (barsStore.length >= getActiveMaxBars()) return;
  if (scrollbackInFlight || scrollbackReachedStart) return;
  const space = Math.max(0, getActiveMaxBars() - barsStore.length);
  const limit = Math.min(calc.chunk, space);
  if (limit <= 0) return;
  const now = Date.now();
  if (now - scrollbackLastReqMs < SCROLLBACK_MIN_INTERVAL_MS) return;
  scrollbackLastReqMs = now;
  scrollbackInFlight = true;
  if (scrollbackAbort) {
    scrollbackAbort.abort();
  }
  scrollbackAbort = new AbortController();
  const symbol = elSymbol.value;
  const beforeOpenMs = firstOpenMs - 1;
  const prevRange = range || (controller && typeof controller.getVisibleLogicalRange === 'function'
    ? controller.getVisibleLogicalRange()
    : null);
  const epoch = viewEpoch;
  try {
    const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`
      + `&to_open_ms=${beforeOpenMs}&epoch=${viewEpoch}`;
    const data = await apiGet(url, { signal: scrollbackAbort.signal });
    if (epoch !== viewEpoch) return;
    const olderBars = Array.isArray(data?.bars) ? data.bars : [];
    mergeOlderBars(olderBars, prevRange, tf);
  } catch (e) {
    if (e && e.name === 'AbortError') return;
  } finally {
    scrollbackInFlight = false;
    if (scrollbackPending) {
      scrollbackPending = false;
      const nextRange = scrollbackLatestRange || range;
      const nextStep = Math.max(step + 1, scrollbackPendingStep);
      scrollbackPendingStep = 0;
      if (!scrollbackReachedStart && barsStore.length < getActiveMaxBars()) {
        scheduleEnsureCoverage(nextRange, nextStep);
      }
      return;
    }
    if (!scrollbackReachedStart && barsStore.length < getActiveMaxBars()) {
      scheduleEnsureCoverage(range, step + 1);
    }
  }
}

async function fetchNewerBarsFromDisk() {
  if (!Number.isFinite(lastOpenMs)) return false;
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  const limit = 2000;
  const epoch = viewEpoch;
  try {
    const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`
      + `&since_open_ms=${lastOpenMs}&epoch=${viewEpoch}`;
    const data = await apiGet(url);
    if (epoch !== viewEpoch) return false;
    const newerBars = Array.isArray(data?.bars) ? data.bars : [];
    if (!newerBars.length) return false;
    newerBars.sort((a, b) => a.open_time_ms - b.open_time_ms);
    for (const bar of newerBars) {
      if (!Number.isFinite(bar.open_time_ms)) continue;
      if (bar.open_time_ms <= lastOpenMs) continue;
      if (controller && typeof controller.updateLastBar === 'function') {
        controller.updateLastBar(bar);
      }
      upsertBarToStore(bar);
      lastOpenMs = bar.open_time_ms;
      if (Number.isFinite(bar.close_time_ms)) {
        lastBarCloseMs = bar.close_time_ms;
      }
    }
    diag.barsTotal = barsStore.length;
    diag.lastPollBars = newerBars.length;
    diag.pollAt = Date.now();
    updateDiag(tf);
    saveCacheCurrent();
    return true;
  } catch (e) {
    return false;
  }
}

function ensureLeftCoverage(range, step, reason) {
  const currentStep = Number.isFinite(step) ? step : 0;
  if (!range || !Number.isFinite(range.from)) return;
  if (scrollbackReachedStart) return;
  if (!barsStore.length) return;
  if (barsStore.length >= getActiveMaxBars()) return;
  if (currentStep >= SCROLLBACK_MAX_STEPS) return;
  if (!Number.isFinite(firstOpenMs)) return;
  scrollbackLatestRange = range;

  const barsBefore = computeBarsBefore(range);
  const need = computeScrollbackNeed(range);
  if (!need) return;
  const targetLeftBuffer = need.targetLeftBuffer;
  const deficitLeft = need.deficitLeft;
  const before = Number.isFinite(barsBefore) ? Math.max(0, barsBefore) : 0;
  const missing = Math.max(0, targetLeftBuffer - before) + deficitLeft;
  if (missing <= 0) return;

  if (scrollbackInFlight) {
    scrollbackPending = true;
    scrollbackPendingStep = currentStep;
    return;
  }
  if (reason !== 'loop') {
    scrollbackPendingStep = 0;
  }
  loadScrollbackChunk(range, currentStep);
}

function handleVisibleRangeChange(range) {
  ensureLeftCoverage(range, 0, 'rangeChange');
}

async function loadBarsFull() {
  const reqId = ++loadReqId;
  if (loadAbort) {
    loadAbort.abort();
  }
  loadAbort = new AbortController();
  const uiEpochAtStart = uiEpoch;
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  currentCacheKey = makeCacheKey(symbol, tf);
  const limit = getActiveMaxBars();
  const epoch = viewEpoch;

  setStatus('load…');
  diag.lastError = '';
  let url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`;
  // ADR-0002 Phase 3: H4 = first-class UDS TF, align=tv більше не потрібен
  url += `&epoch=${epoch}`;
  let data = null;
  try {
    data = await apiGet(url, { signal: loadAbort.signal });
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    throw e;
  }
  if (uiEpochAtStart !== uiEpoch) return;
  if (epoch !== viewEpoch) return;
  if (reqId !== loadReqId) return;
  if (data && data.boot_id) {
    bootId = data.boot_id;
  }
  if (data && data.meta) {
    const meta = data.meta || {};
    const source = String(meta.source || '');
    lastRedisSource = meta.source || (meta.redis_hit ? 'redis' : 'disk');
    lastRedisPayloadMs = Number.isFinite(meta.redis_payload_ts_ms) ? meta.redis_payload_ts_ms : null;
    lastRedisTtlS = Number.isFinite(meta.redis_ttl_s_left) ? meta.redis_ttl_s_left : null;
    lastRedisSeq = Number.isFinite(meta.redis_seq) ? meta.redis_seq : null;
    lastAlignMode = (meta.extensions && meta.extensions.align) ? meta.extensions.align : null;
    data.meta._source_norm = source;
  } else {
    lastRedisSource = null;
    lastRedisPayloadMs = null;
    lastRedisTtlS = null;
    lastRedisSeq = null;
    lastAlignMode = null;
  }
  const bars = data.bars || [];
  if (bars.length === 0) {
    setStatus('no_data');
    if (controller && typeof controller.clearAll === 'function') {
      controller.clearAll();
    }
    setBarsStore([]);
    scrollbackReachedStart = true;
    lastOpenMs = null;
    diag.barsTotal = 0;
    diag.lastPollBars = 0;
    diag.loadAt = Date.now();
    updateDiag(tf);
    return;
  }

  // P4: perf marks for loadBarsFull render
  // D1 fix: встановити TF ДО setBars, щоб normalizeBar знав актуальний barTimeSpanSeconds
  if (controller && typeof controller.setViewTimeframe === 'function') {
    controller.setViewTimeframe(tf);
  }
  const _t0Render = performance.now();
  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(bars);
    logSetDataDebug('loadBarsFull', symbol, tf, bars.length);
  }
  setBarsStore(bars);
  const _dtRender = performance.now() - _t0Render;
  if (_dtRender > 50) console.warn(`LONG_TASK_RENDER loadBarsFull tf=${tf} bars=${bars.length} ms=${_dtRender.toFixed(1)}`);
  // P1: зберегти effective limit для повторного відкриття view
  if (currentCacheKey) {
    _lastLimitByView.set(currentCacheKey, Math.min(Math.max(bars.length, limit), MAX_BARS_CAP));
  }
  scrollbackInFlight = false;
  if (scrollbackAbort) {
    scrollbackAbort.abort();
    scrollbackAbort = null;
  }
  scrollbackReachedStart = false;
  scrollbackPending = false;
  scrollbackLatestRange = null;
  scrollbackPendingStep = 0;
  saveCacheCurrent();
  applyTheme(currentTheme);
  if (elCandleStyle) {
    applyCandleStyle(elCandleStyle.value || 'classic');
  }

  lastOpenMs = bars[bars.length - 1].open_time_ms;
  if (Number.isFinite(bars[bars.length - 1].close_time_ms)) {
    lastBarCloseMs = bars[bars.length - 1].close_time_ms;
  } else if (Number.isFinite(lastOpenMs) && Number.isFinite(tf)) {
    lastBarCloseMs = lastOpenMs + tf * 1000 - 1;
  }
  updatesSeqCursor = null;
  updateHudBarPrice(bars[bars.length - 1].close);
  diag.barsTotal = bars.length;
  diag.lastPollBars = 0;
  diag.loadAt = Date.now();
  updateDiag(tf);

  if (elFollow.checked && controller && typeof controller.resetViewAndFollow === 'function') {
    controller.resetViewAndFollow(RIGHT_OFFSET_PX);
  }

  setStatus(`ok · tf=${tf}s · bars=${bars.length}`);
}

// Максимальний дозволений forward-gap (у барах) перш ніж тригерити reload
const FORWARD_GAP_MAX_BARS = 3;
let _forwardGapReloadPending = false;

// P5: дозволені upgrade-переходи source для preview TF (tick_promoted→history тощо)
const _ALLOWED_SOURCE_UPGRADES = {
  'preview_tick\u2192tick_promoted': true,
  'tick_promoted\u2192history': true,
  'preview_tick\u2192history': true,
  'stream\u2192tick_promoted': true,
  'stream\u2192history': true,
};
function _isAllowedSourceUpgrade(prevSrc, nextSrc) {
  return _ALLOWED_SOURCE_UPGRADES[prevSrc + '\u2192' + nextSrc] === true;
}

function applyUpdates(events) {
  if (!Array.isArray(events) || events.length === 0) return 0;
  const _t0 = performance.now();
  let applied = 0;
  let lastBar = null;
  let maxSeq = updatesSeqCursor;
  const tf = readSelectedTf();
  const expectedSymbol = elSymbol.value;
  const tfMs = tf * 1000;
  const sorted = events.slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  for (const ev of sorted) {
    if (!ev || !ev.bar) continue;
    const bar = ev.bar;
    if (!Number.isFinite(bar.open_time_ms)) continue;
    if (updatesSeqCursor != null && ev.seq != null && ev.seq <= updatesSeqCursor) {
      continue;
    }
    // Drop stale bars — заборона мутації минулого
    if (lastOpenMs != null && bar.open_time_ms < lastOpenMs - tfMs) {
      continue;
    }
    // Forward-gap guard: live не має права стартувати далеко попереду history
    if (lastOpenMs != null && bar.open_time_ms > lastOpenMs + tfMs * FORWARD_GAP_MAX_BARS) {
      if (!_forwardGapReloadPending) {
        _forwardGapReloadPending = true;
        const gapBars = Math.round((bar.open_time_ms - lastOpenMs) / tfMs);
        console.warn('[FORWARD_GAP] reload', { barOpen: bar.open_time_ms, last: lastOpenMs, gapBars });
        setStatus('forward_gap_reload');
        loadBarsFull().then(() => { _forwardGapReloadPending = false; });
      }
      return applied;
    }
    const keySymbol = ev.key && ev.key.symbol ? String(ev.key.symbol) : (bar.symbol || elSymbol.value);
    const keyTf = ev.key && Number.isFinite(ev.key.tf_s) ? ev.key.tf_s : (Number.isFinite(bar.tf_s) ? bar.tf_s : null);
    const keyOpen = ev.key && Number.isFinite(ev.key.open_ms) ? ev.key.open_ms : bar.open_time_ms;
    if (keyTf == null) continue;
    if (keySymbol !== expectedSymbol || keyTf !== tf) {
      warnUpdatesDrop('event_mismatch', { keySymbol, keyTf, expectedSymbol, expectedTf: tf });
      continue;
    }
    const stateKey = `${keySymbol}|${keyTf}|${keyOpen}`;
    const complete = ev.complete === true || bar.complete === true;
    const source = ev.source || bar.src || '';
    const pairKey = `${keySymbol}|${keyTf}`;
    const prev = updateStateByKey.get(stateKey);
    if (prev && prev.complete === true && !complete) {
      continue;
    }
    if (prev && prev.complete === true && complete && prev.source && source && prev.source !== source) {
      // P5: дозволений upgrade для preview TF (tick_promoted→history etc.)
      const _isPreviewTf = OVERLAY_PREVIEW_TF_SET.has(keyTf);
      const _upgradeOk = _isPreviewTf && _isAllowedSourceUpgrade(prev.source, source);
      if (!_upgradeOk) {
        console.warn('NoMix violation', { key: stateKey, prev: prev.source, next: source });
        setStatus('nomix_violation');
        continue;
      }
    }
    updateStateByKey.set(stateKey, { complete, source, pairKey, ts: Date.now() });
    if (controller && typeof controller.updateLastBar === 'function') {
      controller.updateLastBar(bar);
    }
    upsertBarToStore(bar);
    if (complete) {
      if (lastOpenMs == null || bar.open_time_ms > lastOpenMs) {
        lastOpenMs = bar.open_time_ms;
      }
    }
    if (ev.seq != null && (maxSeq == null || ev.seq > maxSeq)) {
      maxSeq = ev.seq;
    }
    lastBar = bar;
    applied += 1;
  }
  if (maxSeq != null) {
    updatesSeqCursor = maxSeq;
  }
  if (lastBar) {
    updateHudInstrumentPrice(lastBar.last_price ?? lastBar.close ?? lastBar.c);
  }
  const allowedPairs = new Set(uiCacheByKey.keys());
  if (currentCacheKey) allowedPairs.add(currentCacheKey);
  cleanupUpdateStateCache(allowedPairs);
  if (applied > 0) {
    saveCacheCurrent();
  }
  // P4: perf measure
  const _dt = performance.now() - _t0;
  if (_dt > 50) console.warn(`LONG_TASK_RENDER applyUpdates events=${events.length} applied=${applied} ms=${_dt.toFixed(1)}`);
  return applied;
}

function _calcUpdatesDelayMs(tf) {
  const isPreview = OVERLAY_PREVIEW_TF_SET.has(tf);
  if (updatesEmptyStreak < 3) {
    return isPreview ? UPDATES_BASE_PREVIEW_MS : UPDATES_BASE_FINAL_MS;
  }
  const idx = Math.min(updatesEmptyStreak - 3, UPDATES_BACKOFF_PREVIEW_MS.length - 1);
  return isPreview ? UPDATES_BACKOFF_PREVIEW_MS[idx] : UPDATES_BACKOFF_FINAL_MS[idx];
}

function _schedulePollUpdates(delayMs) {
  if (pollTimer) clearTimeout(pollTimer);
  updatesNextDelayMs = Math.max(0, delayMs);
  pollTimer = setTimeout(pollUpdates, updatesNextDelayMs);
}

async function pollUpdates() {
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  if (lastOpenMs == null) return;
  if (!isUiVisible()) return;
  if (updatesInFlight) return;
  updatesInFlight = true;
  // P4: AbortController для updates fetch
  updatesAbort = new AbortController();
  // Таймаут 15с на fetch — захист від зависання
  const _pollFetchTimeout = setTimeout(() => {
    if (updatesAbort) updatesAbort.abort();
  }, 15000);
  const uiEpochAtStart = uiEpoch;
  const shouldFollow = Boolean(elFollow.checked && controller && typeof controller.isAtEnd === 'function'
    ? controller.isAtEnd()
    : false);
  const epoch = viewEpoch;
  let nextDelayMs = UPDATES_BASE_FINAL_MS;
  try {
    // P6: менший limit для preview TF
    const isPreviewTf = OVERLAY_PREVIEW_TF_SET.has(tf);
    const pollLimit = isPreviewTf ? 50 : 500;
    let url = `/api/updates?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${pollLimit}`;
    if (updatesSeqCursor != null) {
      url += `&since_seq=${updatesSeqCursor}`;
    }
    url += `&epoch=${viewEpoch}`;
    const data = await apiGet(url, { signal: updatesAbort.signal });
    if (uiEpochAtStart !== uiEpoch) {
      warnUpdatesDrop('epoch_mismatch', { batch: 'updates', start: uiEpochAtStart, now: uiEpoch });
      return;
    }
    if (epoch !== viewEpoch) return;
    const expectedSymbol = elSymbol.value;
    const expectedTf = readSelectedTf();
    const respSymbol = data && data.symbol != null ? String(data.symbol) : '';
    const respTf = data && Number.isFinite(data.tf_s) ? data.tf_s : null;
    if (respSymbol !== expectedSymbol || respTf !== expectedTf) {
      warnUpdatesDrop('batch_mismatch', { respSymbol, respTf, expectedSymbol, expectedTf });
      return;
    }
    if (Number.isFinite(data.api_seen_ts_ms)) {
      lastApiSeenMs = data.api_seen_ts_ms;
    }
    if (Number.isFinite(data.ssot_write_ts_ms)) {
      lastSsotWriteMs = data.ssot_write_ts_ms;
    }
    if (Number.isFinite(data.bar_close_ms)) {
      lastBarCloseMs = data.bar_close_ms;
    }
    if (data && data.boot_id) {
      if (bootId && data.boot_id !== bootId) {
        bootId = data.boot_id;
        updatesSeqCursor = null;
        await loadBarsFull();
        resetPolling();
        resetOverlayPolling();
        return;
      }
      if (!bootId) {
        bootId = data.boot_id;
      }
    }
    if (data && data.meta && data.meta.extensions) {
      lastUpdatesPlane = data.meta.extensions.plane || lastUpdatesPlane;
      lastUpdatesGap = data.meta.extensions.gap || null;
    } else if (OVERLAY_PREVIEW_TF_SET.has(tf)) {
      lastUpdatesPlane = 'preview';
      lastUpdatesGap = null;
    } else {
      lastUpdatesPlane = 'final';
      lastUpdatesGap = null;
    }
    diag.lastError = '';
    diag.pollAt = Date.now();
    updateDiag(tf);

    // P3/PATCH: cursor_gap informational-only (без reload/clear)
    const hasCursorGap = Array.isArray(data.warnings) && data.warnings.includes('cursor_gap');
    if (hasCursorGap) {
      if (Number.isFinite(data.cursor_seq)) {
        updatesSeqCursor = data.cursor_seq;
        lastUpdatesSeq = updatesSeqCursor;
      }
      const now = Date.now();
      if (now - _cursorGapRecoveryMs >= CURSOR_GAP_RECOVERY_DEBOUNCE_MS) {
        _cursorGapRecoveryMs = now;
        console.warn('CURSOR_GAP informational: fast-forward only (no reload)', { cursor_seq: updatesSeqCursor });
        setStatus('cursor_gap_info');
      }
      nextDelayMs = 2000;
      return;
    }

    const events = data.events || [];
    if (events.length === 0) {
      updatesEmptyStreak += 1;
    } else {
      updatesEmptyStreak = 0;
    }
    const applied = applyUpdates(events);
    if (events.length === 0
      && Number.isFinite(data.disk_last_open_ms)
      && lastOpenMs != null
      && data.disk_last_open_ms > lastOpenMs) {
      await fetchNewerBarsFromDisk();
    }
    if (Number.isFinite(data.cursor_seq) && (updatesSeqCursor == null || data.cursor_seq > updatesSeqCursor)) {
      updatesSeqCursor = data.cursor_seq;
    }
    if (updatesSeqCursor != null) {
      lastUpdatesSeq = updatesSeqCursor;
    }
    nextDelayMs = _calcUpdatesDelayMs(tf);
    if (applied === 0) return;
    if (elFollow.checked && shouldFollow && controller && typeof controller.scrollToRealTimeWithOffset === 'function') {
      controller.scrollToRealTimeWithOffset(RIGHT_OFFSET_PX);
    }
    diag.lastPollBars = applied;
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus(`ok · tf=${tf}s · +${applied}`);
    updateHudValues();
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    updatesEmptyStreak += 1;
    diag.lastError = 'poll_error';
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus('poll_error');
    updateHudValues();
    nextDelayMs = _calcUpdatesDelayMs(tf);
  } finally {
    clearTimeout(_pollFetchTimeout);
    updatesInFlight = false;
    updatesAbort = null;
    if (epoch === viewEpoch && isUiVisible()) {
      _schedulePollUpdates(nextDelayMs);
    }
  }
}

function _shouldDropUpdatesBatch(respSymbol, respTf, expectedSymbol, expectedTf, epochAtStart, epochNow) {
  if (epochAtStart !== epochNow) return true;
  if (respSymbol !== expectedSymbol) return true;
  if (respTf !== expectedTf) return true;
  return false;
}

function runUpdateGateSelfTest() {
  const ok1 = !_shouldDropUpdatesBatch('XAU/USD', 14400, 'XAU/USD', 14400, 1, 1);
  const ok2 = _shouldDropUpdatesBatch('XAU/USD', 3600, 'XAU/USD', 14400, 1, 1);
  const ok3 = _shouldDropUpdatesBatch('XAU/USD', 14400, 'XAU/USD', 14400, 1, 2);
  const ok = ok1 && ok2 && ok3;
  console.log('ui_update_gate_selftest', ok ? 'ok' : 'fail', { ok1, ok2, ok3 });
  return ok;
}

function resetPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  updatesEmptyStreak = 0;
  updatesInFlight = false;
  updatesNextDelayMs = null;
  if (!isUiVisible()) return;
  _schedulePollUpdates(0);
}

// P2X.6-U1: overlay polling — окремий від applyUpdates
function _calcOverlayDelayMs(tf, meta, bars) {
  const heldPrev = Array.isArray(bars) && bars.length > 1;
  if (heldPrev) return OVERLAY_POLL_FAST_MS;
  const tfMs = tf * 1000;
  let bucketOpenMs = null;
  if (meta && meta.extensions && Number.isFinite(meta.extensions.bucket_open_ms)) {
    bucketOpenMs = meta.extensions.bucket_open_ms;
  } else if (Number.isFinite(overlayLastBucketOpenMs)) {
    bucketOpenMs = overlayLastBucketOpenMs;
  }
  if (!Number.isFinite(bucketOpenMs)) {
    const now = Date.now();
    bucketOpenMs = Math.floor(now / tfMs) * tfMs;
  }
  const msToNext = (bucketOpenMs + tfMs) - Date.now();
  if (msToNext > 60_000) return OVERLAY_POLL_SLOW_MS;
  return OVERLAY_POLL_FAST_MS;
}

function _schedulePollOverlay(delayMs) {
  if (overlayTimer) clearTimeout(overlayTimer);
  overlayNextDelayMs = Math.max(0, delayMs);
  overlayTimer = setTimeout(pollOverlay, overlayNextDelayMs);
}

async function pollOverlay() {
  const symbol = elSymbol.value;
  const tf = readSelectedTf();

  // Overlay не потрібен для preview TF
  if (OVERLAY_PREVIEW_TF_SET.has(tf)) return;
  if (tf < OVERLAY_MIN_TF_S || tf > OVERLAY_MAX_TF_S) return;
  if (!isUiVisible()) return;
  if (overlayInFlight) return;
  overlayInFlight = true;
  const epoch = viewEpoch;
  let nextDelayMs = OVERLAY_POLL_SLOW_MS;
  const _overlayAbort = new AbortController();
  const _overlayFetchTimeout = setTimeout(() => _overlayAbort.abort(), 15000);

  try {
    const baseTf = tf >= 14400 ? 180 : 60;  // HTF (H4+D1) аграгує з M3, решта з M1
    const url = `/api/overlay?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&base_tf_s=${baseTf}`;
    const data = await apiGet(url);
    if (!data || !data.ok) return;
    if (epoch !== viewEpoch) return;

    // boot_id перевірка
    if (data.meta && data.meta.boot_id) {
      if (bootId && data.meta.boot_id !== bootId) {
        // Рестарт сервера — основний pollUpdates зробить reload
        return;
      }
    }

    overlayHeldPrev = Array.isArray(data.bars) && data.bars.length > 1;
    if (data.meta && data.meta.extensions && Number.isFinite(data.meta.extensions.bucket_open_ms)) {
      overlayLastBucketOpenMs = data.meta.extensions.bucket_open_ms;
    }
    if (controller && typeof controller.updateOverlayBar === 'function') {
      controller.updateOverlayBar(data.bar, data.bars);  // P2X.6-U3: bars=[prev?,curr?]
    }
    if (Array.isArray(data.bars) && data.bars.length > 0) {
      const lastOverlay = data.bars[data.bars.length - 1];
      if (lastOverlay && (lastOverlay.last_price != null || lastOverlay.close != null)) {
        const price = lastOverlay.last_price != null ? lastOverlay.last_price : lastOverlay.close;
        if (Number.isFinite(price)) updateHudInstrumentPrice(price);
      }
    } else if (data.bar && (data.bar.last_price != null || data.bar.close != null)) {
      const price = data.bar.last_price != null ? data.bar.last_price : data.bar.close;
      if (Number.isFinite(price)) updateHudInstrumentPrice(price);
    }
    updateHudValues();
    nextDelayMs = _calcOverlayDelayMs(tf, data.meta, data.bars);
  } catch (_e) {
    // Overlay помилки не фатальні — тихо ігноруємо
    nextDelayMs = OVERLAY_POLL_SLOW_MS;
  } finally {
    clearTimeout(_overlayFetchTimeout);
    overlayInFlight = false;
    if (epoch === viewEpoch && isUiVisible()) {
      _schedulePollOverlay(nextDelayMs);
    }
  }
}

function resetOverlayPolling() {
  if (overlayTimer) clearTimeout(overlayTimer);
  overlayTimer = null;
  overlayInFlight = false;
  overlayHeldPrev = false;
  overlayLastBucketOpenMs = null;
  overlayNextDelayMs = null;

  // S3-2: безумовний clear overlay перед будь-яким switch
  if (controller && typeof controller.clearOverlay === 'function') {
    controller.clearOverlay();
  }

  const tf = readSelectedTf();
  // Запускаємо overlay polling тільки для TF ≥ M5 і не preview
  const overlayActive = !OVERLAY_PREVIEW_TF_SET.has(tf) && tf >= OVERLAY_MIN_TF_S && tf <= OVERLAY_MAX_TF_S;

  // S5: перемикаємо lastValueVisible на candles: off коли overlay показує live ціну
  if (controller && typeof controller.setOverlayActive === 'function') {
    controller.setOverlayActive(overlayActive);
  }

  if (overlayActive) {
    if (!isUiVisible()) return;
    _schedulePollOverlay(0);
  }
}

async function init() {
  makeChart();
  favoritesState = loadFavoritesState();
  initToolbars();
  await loadUiConfig();
  let theme = 'light';
  let candleStyle = 'classic';
  let savedSymbol = null;
  let savedTf = null;
  let layoutEnabled = true;
  try {
    const saveFlag = localStorage.getItem(LAYOUT_SAVE_KEY);
    if (saveFlag === '0') {
      layoutEnabled = false;
    }
    const saved = localStorage.getItem(THEME_KEY);
    if (layoutEnabled) {
      if (saved === 'dark' || saved === 'light' || saved === 'dark-gray') {
        theme = saved;
      } else if (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        theme = 'dark';
      }
      const savedStyle = localStorage.getItem(CANDLE_STYLE_KEY);
      if (savedStyle) candleStyle = savedStyle;
      savedSymbol = localStorage.getItem(SYMBOL_KEY);
      savedTf = localStorage.getItem(TF_KEY);
    }
  } catch (e) {
    // ignore storage errors
  }
  if (elTheme) {
    elTheme.value = theme;
  }
  applyTheme(theme);
  updateToolbarValue('theme');
  if (elCandleStyle) {
    elCandleStyle.value = candleStyle;
  }
  applyCandleStyle(candleStyle);
  updateToolbarValue('candle-style');
  if (elTf) {
    const tfValue = savedTf || '300';
    if (Array.from(elTf.options).some((opt) => opt.value === tfValue)) {
      elTf.value = tfValue;
    }
  }
  if (elTf) {
    const tfOptions = getSelectOptions(elTf).map((opt) => opt.value);
    normalizeFavoritesTfs(tfOptions);
  }
  const syms = await loadSymbols();
  const symbolPreferred = savedSymbol || 'XAU/USD';
  if (Array.from(elSymbol.options).some((opt) => opt.value === symbolPreferred)) {
    elSymbol.value = symbolPreferred;
  }
  updateToolbarValue('symbol');
  startNewViewEpoch();
  await loadBarsFull();
  saveCacheCurrent();
  updateToolbarValue('tf');
  updateHudValues();
  updateStreamingIndicator();
  resetPolling();
  resetOverlayPolling();
  updateUtcNow();
  setInterval(updateUtcNow, 1000);

  window.addEventListener('resize', () => {
    syncHudMenuWidth();
  });

  if (elReload) {
    elReload.addEventListener('click', async () => {
      startNewViewEpoch();
      await loadBarsFull();
    });
  }

  elSymbol.addEventListener('change', () => {
    saveLayoutValue(SYMBOL_KEY, elSymbol.value);
    // P1: abort одразу при зміні, не чекаючи debounce
    startNewViewEpoch();
    if (_switchDebounceTimer) clearTimeout(_switchDebounceTimer);
    _switchDebounceTimer = setTimeout(async () => {
      _switchDebounceTimer = null;
      saveCacheCurrent();
      const tf = readSelectedTf();
      const restored = restoreCacheFor(elSymbol.value, tf);
      // P1: cache-first → loadBarsFull у background, не блокує UI
      resetPolling();
      resetOverlayPolling();
      if (!restored) {
        setStatus('load…');
        loadBarsFull().catch(() => { });
      } else {
        // P1: background refresh навіть при cache hit
        loadBarsFull().catch(() => { });
      }
    }, SWITCH_DEBOUNCE_MS);
  });

  elTf.addEventListener('change', () => {
    saveLayoutValue(TF_KEY, elTf.value);
    // P1: abort одразу при зміні, не чекаючи debounce
    startNewViewEpoch();
    if (_switchDebounceTimer) clearTimeout(_switchDebounceTimer);
    _switchDebounceTimer = setTimeout(async () => {
      _switchDebounceTimer = null;
      saveCacheCurrent();
      const tf = readSelectedTf();
      const restored = restoreCacheFor(elSymbol.value, tf);
      // P1: cache-first → loadBarsFull у background, не блокує UI
      resetPolling();
      resetOverlayPolling();
      if (!restored) {
        setStatus('load…');
        loadBarsFull().catch(() => { });
      } else {
        // P1: background refresh навіть при cache hit
        loadBarsFull().catch(() => { });
      }
    }, SWITCH_DEBOUNCE_MS);
  });

  if (elTheme) {
    const readThemeValue = () => {
      const idx = elTheme.selectedIndex;
      if (idx >= 0 && elTheme.options && elTheme.options[idx]) {
        return elTheme.options[idx].value || 'light';
      }
      return elTheme.value || 'light';
    };
    elTheme.addEventListener('change', () => {
      const mode = readThemeValue();
      applyTheme(mode);
      setStatus(`theme_change=${mode}`);
      saveLayoutValue(THEME_KEY, mode);
    });
    elTheme.addEventListener('input', () => {
      const mode = readThemeValue();
      applyTheme(mode);
      saveLayoutValue(THEME_KEY, mode);
    });
  }

  if (elCandleStyle) {
    elCandleStyle.addEventListener('change', () => {
      const style = elCandleStyle.value;
      saveLayoutValue(CANDLE_STYLE_KEY, style);
      applyCandleStyle(style);
    });
  }

  window.addEventListener('keydown', (event) => {
    if (event.defaultPrevented) return;
    const target = event.target;
    const tag = target && target.tagName ? target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (!controller) return;

    if (event.key === 'r' || event.key === 'R') {
      event.preventDefault();
      elFollow.checked = true;
      if (typeof controller.resetViewAndFollow === 'function') {
        controller.resetViewAndFollow(RIGHT_OFFSET_PX);
      }
    }

    if (event.key === 'f' || event.key === 'F') {
      event.preventDefault();
      elFollow.checked = true;
      if (typeof controller.scrollToRealTimeWithOffset === 'function') {
        controller.scrollToRealTimeWithOffset(RIGHT_OFFSET_PX);
      }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
      if (overlayTimer) clearTimeout(overlayTimer);
      overlayTimer = null;
      updatesNextDelayMs = null;
      overlayNextDelayMs = null;
      return;
    }
    resetPolling();
    resetOverlayPolling();
  });

  // Watchdog: якщо polling не працює >30с — примусовий reset
  setInterval(() => {
    if (lastOpenMs == null || !isUiVisible()) return;
    const sinceLastPoll = Date.now() - (diag.pollAt || 0);
    if (sinceLastPoll > 30000 && !updatesInFlight) {
      console.warn('POLL_WATCHDOG: no poll for', sinceLastPoll, 'ms, resetting');
      resetPolling();
    }
    if (sinceLastPoll > 45000) {
      console.warn('POLL_WATCHDOG: force reset (inFlight stuck)', sinceLastPoll, 'ms');
      updatesInFlight = false;
      resetPolling();
      resetOverlayPolling();
    }
  }, 15000);

  window.runUpdateGateSelfTest = runUpdateGateSelfTest;
}

init().catch(() => setStatus('init_error'));
