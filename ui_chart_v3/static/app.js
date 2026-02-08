// Мінімальний клієнт для Lightweight Charts.
// Працює з API: /api/symbols, /api/bars, /api/latest.

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
let loadAbort = null;
let currentTheme = 'light';
let uiDebugEnabled = true;
let lastHudSymbol = null;
let lastHudTf = null;
let updatesSeqCursor = null;
let bootId = null;
let lastApiSeenMs = null;
let lastSsotWriteMs = null;
let lastBarCloseMs = null;
let lastRedisSource = null;
let lastRedisPayloadMs = null;
let lastRedisTtlS = null;
let lastRedisSeq = null;
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
const cacheLru = new Map();
let updateStateLastCleanupMs = 0;
let favoritesState = null;

const RIGHT_OFFSET_PX = 48;
const THEME_KEY = 'ui_chart_theme';
const CANDLE_STYLE_KEY = 'ui_chart_candle_style';
const SYMBOL_KEY = 'ui_chart_symbol';
const TF_KEY = 'ui_chart_tf';
const LAYOUT_SAVE_KEY = 'ui_chart_layout_save';
const MAX_RENDER_BARS_ACTIVE = 60000;
const MAX_RENDER_BARS_WARM = 20000;
const WARM_LRU_LIMIT = 6;
const UPDATE_STATE_TTL_MS = 30 * 60 * 1000;
const UPDATE_STATE_CLEANUP_INTERVAL_MS = 60000;
const FAVORITES_KEY = 'ui_chart_favorites_v1';
const SCROLLBACK_TRIGGER_BARS = 80;
const SCROLLBACK_MIN_INTERVAL_MS = 1200;
const SCROLLBACK_CHUNK_MAX = 8000;
const SCROLLBACK_CHUNK_BY_TF = {
  300: 1000,
  900: 1000,
  1800: 1000,
  3600: 1000,
};

function readSelectedTf(defaultTf = 300) {
  const raw = parseInt(elTf?.value, 10);
  if (Number.isFinite(raw) && raw > 0) return raw;
  return defaultTf;
}
const diag = {
  loadAt: null,
  pollAt: null,
  barsTotal: 0,
  lastPollBars: 0,
  lastError: '',
};

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

function fmtPrice(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const s = n.toFixed(5).replace(/\.0+$/, '').replace(/\.(\d*?)0+$/, '.$1');
  return s.endsWith('.') ? s.slice(0, -1) : s;
}

function updateUtcNow() {
  if (!elDiagUtc) return;
  elDiagUtc.textContent = fmtUtc(Date.now());
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
  updateStreamingIndicator();
}

function updateHudPrice(price) {
  if (!elHudPrice) return;
  elHudPrice.textContent = fmtPrice(price);
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
  const r = await fetch(url, { cache: 'no-store', signal: opts.signal });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}

async function loadUiConfig() {
  try {
    const data = await apiGet('/api/config');
    if (data && typeof data.ui_debug === 'boolean') {
      uiDebugEnabled = data.ui_debug;
    }
    // no live state
  } catch (e) {
    uiDebugEnabled = true;
    // no live state
  }
  applyUiDebug();
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
  const trimmedBars = trimBarsForLimit(barsStore, MAX_RENDER_BARS_ACTIVE);
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
  const key = makeCacheKey(symbol, tf);
  const cached = uiCacheByKey.get(key);
  if (!cached) return false;
  const bars = Array.isArray(cached.bars) ? cached.bars : [];
  if (!bars.length) return false;
  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(bars);
  }
  setBarsStore(bars);
  applyTheme(currentTheme);
  if (elCandleStyle) {
    applyCandleStyle(elCandleStyle.value || 'classic');
  }
  if (controller && typeof controller.setViewTimeframe === 'function') {
    controller.setViewTimeframe(tf);
  }
  lastOpenMs = Number.isFinite(cached.lastOpenMs) ? cached.lastOpenMs : bars[bars.length - 1].open_time_ms;
  lastBarCloseMs = Number.isFinite(cached.lastBarCloseMs) ? cached.lastBarCloseMs : null;
  updatesSeqCursor = Number.isFinite(cached.updatesSeqCursor) ? cached.updatesSeqCursor : null;
  scrollbackReachedStart = Boolean(cached.scrollbackReachedStart);
  scrollbackPending = false;
  scrollbackLatestRange = null;
  lastRedisSource = 'cache';
  lastRedisPayloadMs = null;
  lastRedisTtlS = null;
  lastRedisSeq = null;
  diag.barsTotal = bars.length;
  diag.lastPollBars = 0;
  diag.loadAt = Date.now();
  updateHudPrice(bars[bars.length - 1].close);
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
  barsStore = Array.isArray(bars) ? bars.slice() : [];
  if (barsStore.length > MAX_RENDER_BARS_ACTIVE) {
    barsStore = barsStore.slice(-MAX_RENDER_BARS_ACTIVE);
  }
  rebuildBarsIndex();
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
  if (barsStore.length > MAX_RENDER_BARS_ACTIVE) {
    barsStore = barsStore.slice(-MAX_RENDER_BARS_ACTIVE);
  }
  rebuildBarsIndex();
}

function getScrollbackChunk(tf) {
  return SCROLLBACK_CHUNK_BY_TF[tf] || 0;
}

function getAdaptiveScrollbackChunk(tf, range) {
  const base = getScrollbackChunk(tf);
  if (!base) return 0;
  const from = Number(range?.from);
  const to = Number(range?.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return base;
  const visibleBars = Math.max(0, Math.ceil(to - from));
  const boost = Math.min(visibleBars, base * 3);
  const chunk = base + boost;
  return Math.min(SCROLLBACK_CHUNK_MAX, chunk);
}

function mergeOlderBars(olderBars, prevRange) {
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
  const space = Math.max(0, MAX_RENDER_BARS_ACTIVE - barsStore.length);
  const take = space > 0 ? added.slice(-space) : [];
  if (take.length === 0) return;
  barsStore = take.concat(barsStore);
  rebuildBarsIndex();
  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(barsStore);
  }
  if (prevRange && controller && typeof controller.setVisibleLogicalRange === 'function') {
    controller.setVisibleLogicalRange({
      from: prevRange.from + take.length,
      to: prevRange.to + take.length,
    });
  }
  diag.barsTotal = barsStore.length;
  saveCacheCurrent();
}

async function loadScrollbackChunk(range) {
  const tf = readSelectedTf();
  const chunk = getAdaptiveScrollbackChunk(tf, range);
  if (!chunk) return;
  if (!Number.isFinite(firstOpenMs)) return;
  if (barsStore.length >= MAX_RENDER_BARS_ACTIVE) return;
  if (scrollbackInFlight || scrollbackReachedStart) return;
  const space = Math.max(0, MAX_RENDER_BARS_ACTIVE - barsStore.length);
  const limit = Math.min(chunk, space);
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
  try {
    const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`
      + `&to_open_ms=${beforeOpenMs}&force_disk=1`;
    const data = await apiGet(url, { signal: scrollbackAbort.signal });
    const olderBars = Array.isArray(data?.bars) ? data.bars : [];
    mergeOlderBars(olderBars, prevRange);
  } catch (e) {
    if (e && e.name === 'AbortError') return;
  } finally {
    scrollbackInFlight = false;
    if (scrollbackPending) {
      scrollbackPending = false;
      if (!scrollbackReachedStart && barsStore.length < MAX_RENDER_BARS_ACTIVE) {
        loadScrollbackChunk(scrollbackLatestRange);
      }
    }
  }
}

async function fetchNewerBarsFromDisk() {
  if (!Number.isFinite(lastOpenMs)) return false;
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  const limit = 2000;
  try {
    const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`
      + `&since_open_ms=${lastOpenMs}&force_disk=1`;
    const data = await apiGet(url);
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

function handleVisibleRangeChange(range) {
  if (!range || !Number.isFinite(range.from)) return;
  if (scrollbackReachedStart) return;
  if (!barsStore.length) return;
  if (barsStore.length >= MAX_RENDER_BARS_ACTIVE) return;
  scrollbackLatestRange = range;
  let barsBefore = null;
  if (controller && typeof controller.barsInLogicalRange === 'function') {
    const info = controller.barsInLogicalRange(range);
    if (info && Number.isFinite(info.barsBefore)) barsBefore = info.barsBefore;
  }
  if (barsBefore == null && Number.isFinite(range.from)) {
    barsBefore = Math.floor(range.from);
  }
  if (barsBefore == null || barsBefore > SCROLLBACK_TRIGGER_BARS) return;
  if (scrollbackInFlight) {
    scrollbackPending = true;
    return;
  }
  loadScrollbackChunk(range);
}

async function loadBarsFull(forceDisk = false) {
  const reqId = ++loadReqId;
  if (loadAbort) {
    loadAbort.abort();
  }
  loadAbort = new AbortController();
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  currentCacheKey = makeCacheKey(symbol, tf);
  const limit = MAX_RENDER_BARS_ACTIVE;

  setStatus('load…');
  diag.lastError = '';
  let url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`;
  if (forceDisk) {
    url += '&force_disk=1';
  } else {
    url += '&prefer_redis=1';
  }
  let data = null;
  try {
    data = await apiGet(url, { signal: loadAbort.signal });
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    throw e;
  }
  if (reqId !== loadReqId) return;
  if (data && data.boot_id) {
    bootId = data.boot_id;
  }
  if (data && data.meta) {
    const meta = data.meta || {};
    lastRedisSource = meta.source || (meta.redis_hit ? 'redis' : 'disk');
    lastRedisPayloadMs = Number.isFinite(meta.redis_payload_ts_ms) ? meta.redis_payload_ts_ms : null;
    lastRedisTtlS = Number.isFinite(meta.redis_ttl_s_left) ? meta.redis_ttl_s_left : null;
    lastRedisSeq = Number.isFinite(meta.redis_seq) ? meta.redis_seq : null;
  } else {
    lastRedisSource = null;
    lastRedisPayloadMs = null;
    lastRedisTtlS = null;
    lastRedisSeq = null;
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

  if (controller && typeof controller.setBars === 'function') {
    controller.setBars(bars);
  }
  setBarsStore(bars);
  scrollbackInFlight = false;
  if (scrollbackAbort) {
    scrollbackAbort.abort();
    scrollbackAbort = null;
  }
  scrollbackReachedStart = false;
  scrollbackPending = false;
  scrollbackLatestRange = null;
  saveCacheCurrent();
  applyTheme(currentTheme);
  if (elCandleStyle) {
    applyCandleStyle(elCandleStyle.value || 'classic');
  }
  if (controller && typeof controller.setViewTimeframe === 'function') {
    controller.setViewTimeframe(tf);
  }

  lastOpenMs = bars[bars.length - 1].open_time_ms;
  if (Number.isFinite(bars[bars.length - 1].close_time_ms)) {
    lastBarCloseMs = bars[bars.length - 1].close_time_ms;
  } else if (Number.isFinite(lastOpenMs) && Number.isFinite(tf)) {
    lastBarCloseMs = lastOpenMs + tf * 1000 - 1;
  }
  updatesSeqCursor = null;
  updateHudPrice(bars[bars.length - 1].close);
  diag.barsTotal = bars.length;
  diag.lastPollBars = 0;
  diag.loadAt = Date.now();
  updateDiag(tf);

  if (elFollow.checked && controller && typeof controller.resetViewAndFollow === 'function') {
    controller.resetViewAndFollow(RIGHT_OFFSET_PX);
  }

  setStatus(`ok · tf=${tf}s · bars=${bars.length}`);
}

function applyUpdates(events) {
  if (!Array.isArray(events) || events.length === 0) return 0;
  let applied = 0;
  let lastBar = null;
  let maxSeq = updatesSeqCursor;
  const sorted = events.slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  for (const ev of sorted) {
    if (!ev || !ev.bar) continue;
    const bar = ev.bar;
    if (!Number.isFinite(bar.open_time_ms)) continue;
    if (updatesSeqCursor != null && ev.seq != null && ev.seq <= updatesSeqCursor) {
      continue;
    }
    const keySymbol = ev.key && ev.key.symbol ? String(ev.key.symbol) : (bar.symbol || elSymbol.value);
    const keyTf = ev.key && Number.isFinite(ev.key.tf_s) ? ev.key.tf_s : (Number.isFinite(bar.tf_s) ? bar.tf_s : null);
    const keyOpen = ev.key && Number.isFinite(ev.key.open_ms) ? ev.key.open_ms : bar.open_time_ms;
    if (keyTf == null) continue;
    if (updatesCursor != null && keyOpen <= updatesCursor) {
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
      console.warn('NoMix violation', { key: stateKey, prev: prev.source, next: source });
      setStatus('nomix_violation');
      continue;
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
    updateHudPrice(lastBar.last_price != null ? lastBar.last_price : lastBar.close);
  }
  const allowedPairs = new Set(uiCacheByKey.keys());
  if (currentCacheKey) allowedPairs.add(currentCacheKey);
  cleanupUpdateStateCache(allowedPairs);
  if (applied > 0) {
    saveCacheCurrent();
  }
  return applied;
}

async function pollUpdates() {
  const symbol = elSymbol.value;
  const tf = readSelectedTf();
  if (lastOpenMs == null) return;
  const shouldFollow = Boolean(elFollow.checked && controller && typeof controller.isAtEnd === 'function'
    ? controller.isAtEnd()
    : false);

  try {
    let url = `/api/updates?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=500`;
    if (updatesSeqCursor != null) {
      url += `&since_seq=${updatesSeqCursor}`;
    }
    const data = await apiGet(url);
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
        return;
      }
      if (!bootId) {
        bootId = data.boot_id;
      }
    }
    diag.lastError = '';
    diag.pollAt = Date.now();
    updateDiag(tf);
    const events = data.events || [];
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
    if (applied === 0) return;
    if (elFollow.checked && shouldFollow && controller && typeof controller.scrollToRealTimeWithOffset === 'function') {
      controller.scrollToRealTimeWithOffset(RIGHT_OFFSET_PX);
    }
    diag.lastPollBars = applied;
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus(`ok · tf=${tf}s · +${applied}`);
  } catch (e) {
    diag.lastError = 'poll_error';
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus('poll_error');
  }
}

function resetPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollUpdates, 3000);
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
  await loadBarsFull();
  saveCacheCurrent();
  updateToolbarValue('tf');
  updateHudValues();
  updateStreamingIndicator();
  resetPolling();
  updateUtcNow();
  setInterval(updateUtcNow, 1000);

  window.addEventListener('resize', () => {
    syncHudMenuWidth();
  });

  if (elReload) {
    elReload.addEventListener('click', async () => {
      await loadBarsFull();
    });
  }

  elSymbol.addEventListener('change', async () => {
    saveLayoutValue(SYMBOL_KEY, elSymbol.value);
    saveCacheCurrent();
    const tf = readSelectedTf();
    const restored = restoreCacheFor(elSymbol.value, tf);
    if (!restored) {
      await loadBarsFull();
    }
    resetPolling();
    await pollUpdates();
  });

  elTf.addEventListener('change', async () => {
    saveLayoutValue(TF_KEY, elTf.value);
    saveCacheCurrent();
    const tf = readSelectedTf();
    const restored = restoreCacheFor(elSymbol.value, tf);
    if (!restored) {
      await loadBarsFull();
    }
    resetPolling();
    await pollUpdates();
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
}

init().catch(() => setStatus('init_error'));
