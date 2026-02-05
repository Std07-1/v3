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
let currentTheme = 'light';
let uiDebugEnabled = true;
let lastHudSymbol = null;
let lastHudTf = null;
let liveEnabled = false;
let liveSymbol = null;
let liveTimer = null;
let lastLiveOpenMs = null;
let lastLiveTickTs = null;
let updatesSeqCursor = null;
let bootId = null;
let lastApiSeenMs = null;
let lastSsotWriteMs = null;
let lastBarCloseMs = null;
const updateStateByKey = new Map();

const RIGHT_OFFSET_PX = 48;
const THEME_KEY = 'ui_chart_theme';
const CANDLE_STYLE_KEY = 'ui_chart_candle_style';
const SYMBOL_KEY = 'ui_chart_symbol';
const TF_KEY = 'ui_chart_tf';
const LAYOUT_SAVE_KEY = 'ui_chart_layout_save';
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

async function apiGet(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}

async function loadUiConfig() {
  try {
    const data = await apiGet('/api/config');
    if (data && typeof data.ui_debug === 'boolean') {
      uiDebugEnabled = data.ui_debug;
    }
    if (data && typeof data.live_candle_enabled === 'boolean') {
      liveEnabled = Boolean(data.live_candle_enabled);
    }
    if (data && typeof data.live_symbol === 'string') {
      const raw = data.live_symbol.trim();
      liveSymbol = raw ? raw : null;
    }
  } catch (e) {
    uiDebugEnabled = true;
    liveEnabled = false;
    liveSymbol = null;
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
  const options = Array.from(select.options || []);
  if (options.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'tool-option';
    empty.textContent = '—';
    menu.appendChild(empty);
    updateToolbarValue(selectId);
    return;
  }
  for (const opt of options) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tool-option';
    btn.dataset.value = opt.value;
    btn.textContent = opt.textContent;
    if (opt.value === select.value) btn.classList.add('active');
    btn.addEventListener('click', () => {
      select.value = opt.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      updateToolbarValue(selectId);
      const groupLocal = getToolbarGroup(selectId);
      if (groupLocal) groupLocal.classList.remove('open');
    });
    menu.appendChild(btn);
  }
  updateToolbarValue(selectId);
}

function buildHudMenu(select, menu) {
  menu.innerHTML = '';
  const options = Array.from(select.options || []);
  if (options.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'tool-option';
    empty.textContent = '—';
    menu.appendChild(empty);
    return;
  }
  for (const opt of options) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tool-option';
    btn.dataset.value = opt.value;
    btn.textContent = opt.textContent;
    if (opt.value === select.value) btn.classList.add('active');
    btn.addEventListener('click', () => {
      select.value = opt.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      closeHudMenus();
    });
    menu.appendChild(btn);
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

function cycleSelectValue(select, direction) {
  if (!select || !select.options || select.options.length === 0) return;
  const total = select.options.length;
  const current = Math.max(0, select.selectedIndex);
  const next = (current + direction + total) % total;
  select.selectedIndex = next;
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

function attachHudWheelControls() {
  if (elHudSymbol) {
    elHudSymbol.addEventListener('wheel', (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 1 : -1;
      cycleSelectValue(elSymbol, direction);
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
      cycleSelectValue(elTf, direction);
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
  buildToolbarMenu('symbol');
  updateHudValues();
}

async function loadBarsFull(forceDisk = false) {
  const symbol = elSymbol.value;
  const tf = parseInt(elTf.value, 10);
  const limit = 20000;

  setStatus('load…');
  diag.lastError = '';
  let url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`;
  if (forceDisk) {
    url += '&force_disk=1';
  }
  const data = await apiGet(url);
  if (data && data.boot_id) {
    bootId = data.boot_id;
  }
  const bars = data.bars || [];
  if (bars.length === 0) {
    setStatus('no_data');
    if (controller && typeof controller.clearAll === 'function') {
      controller.clearAll();
    }
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
    const prev = updateStateByKey.get(stateKey);
    if (prev && prev.complete === true && !complete) {
      continue;
    }
    if (prev && prev.complete === true && complete && prev.source && source && prev.source !== source) {
      console.warn('NoMix violation', { key: stateKey, prev: prev.source, next: source });
      setStatus('nomix_violation');
      continue;
    }
    updateStateByKey.set(stateKey, { complete, source });
    if (controller && typeof controller.updateLastBar === 'function') {
      controller.updateLastBar(bar);
    }
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
  return applied;
}

async function pollUpdates() {
  const symbol = elSymbol.value;
  const tf = parseInt(elTf.value, 10);
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
      await loadBarsFull(true);
      resetPolling();
      return;
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

async function pollLive() {
  if (!liveEnabled) return;
  const symbol = elSymbol.value;
  const tf = parseInt(elTf.value, 10);
  const shouldFollow = Boolean(elFollow.checked && controller && typeof controller.isAtEnd === 'function'
    ? controller.isAtEnd()
    : false);

  try {
    const data = await apiGet(`/api/live?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}`);
    if (data && data.last_tick_ts != null) {
      if (lastLiveTickTs !== null && data.last_tick_ts === lastLiveTickTs) return;
      lastLiveTickTs = data.last_tick_ts;
    }
    const bar = data && data.bar ? data.bar : null;
    if (!bar) return;
    const openMs = bar.open_time_ms;
    if (Number.isFinite(openMs) && lastLiveOpenMs != null && openMs < lastLiveOpenMs) return;
    if (Number.isFinite(openMs)) {
      lastLiveOpenMs = openMs;
    }
    if (controller && typeof controller.updateLastBar === 'function') {
      controller.updateLastBar(bar);
    }
    updateHudPrice(bar.last_price != null ? bar.last_price : bar.close);
    if (elFollow.checked && shouldFollow && controller && typeof controller.scrollToRealTimeWithOffset === 'function') {
      controller.scrollToRealTimeWithOffset(RIGHT_OFFSET_PX);
    }
  } catch (e) {
    // ignore live errors
  }
}

function resetPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (liveTimer) clearInterval(liveTimer);
  lastLiveOpenMs = null;
  lastLiveTickTs = null;
  pollTimer = setInterval(pollUpdates, 3000);
}

async function init() {
  makeChart();
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
    const tfValue = savedTf || '60';
    if (Array.from(elTf.options).some((opt) => opt.value === tfValue)) {
      elTf.value = tfValue;
    }
  }
  await loadSymbols();
  const symbolPreferred = savedSymbol || (liveEnabled && liveSymbol ? liveSymbol : 'XAU/USD');
  if (Array.from(elSymbol.options).some((opt) => opt.value === symbolPreferred)) {
    elSymbol.value = symbolPreferred;
  }
  updateToolbarValue('symbol');
  await loadBarsFull();
  updateToolbarValue('tf');
  updateHudValues();
  updateStreamingIndicator();
  resetPolling();
  if (liveTimer) {
    clearInterval(liveTimer);
  }
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
    lastLiveOpenMs = null;
    await loadBarsFull();
  });

  elTf.addEventListener('change', async () => {
    saveLayoutValue(TF_KEY, elTf.value);
    lastLiveOpenMs = null;
    await loadBarsFull();
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
