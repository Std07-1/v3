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
const elDiagLast = document.getElementById('diag-last');
const elDiagBars = document.getElementById('diag-bars');
const elDiagError = document.getElementById('diag-error');
const elDiagUtc = document.getElementById('diag-utc');

let controller = null;
let lastOpenMs = null;
let pollTimer = null;
let currentTheme = 'light';

const RIGHT_OFFSET_PX = 48;
const THEME_KEY = 'ui_chart_theme';
const CANDLE_STYLE_KEY = 'ui_chart_candle_style';
const SYMBOL_KEY = 'ui_chart_symbol';
const TF_KEY = 'ui_chart_tf';
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

function updateUtcNow() {
  if (!elDiagUtc) return;
  elDiagUtc.textContent = fmtUtc(Date.now());
}

function updateDiag(tfSeconds) {
  const now = Date.now();
  const lagMs = (lastOpenMs != null && tfSeconds)
    ? Math.max(0, now - (lastOpenMs + tfSeconds * 1000))
    : null;

  elDiagLoad.textContent = diag.loadAt ? fmtAge(now - diag.loadAt) : '—';
  elDiagPoll.textContent = diag.pollAt ? fmtAge(now - diag.pollAt) : '—';
  elDiagLag.textContent = lagMs != null ? fmtAge(lagMs) : '—';
  elDiagLast.textContent = lastOpenMs != null ? fmtUtc(lastOpenMs) : '—';
  elDiagBars.textContent = diag.barsTotal ? `${diag.barsTotal} (+${diag.lastPollBars})` : '—';
  elDiagError.textContent = diag.lastError || '—';
}

function setStatus(txt) {
  elStatus.textContent = txt;
}

async function apiGet(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
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

function closeAllToolMenus(exceptId = null) {
  for (const id of TOOLBAR_MENU_IDS) {
    if (id === exceptId) continue;
    const group = getToolbarGroup(id);
    if (group) group.classList.remove('open');
  }
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
  if (!menu) return;
  for (const btn of menu.querySelectorAll('.tool-option')) {
    btn.classList.toggle('active', btn.dataset.value === select.value);
  }
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
    } else {
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
}

async function loadBarsFull() {
  const symbol = elSymbol.value;
  const tf = parseInt(elTf.value, 10);
  const limit = 20000;

  setStatus('load…');
  diag.lastError = '';
  const data = await apiGet(`/api/bars?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=${limit}`);
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
  diag.barsTotal = bars.length;
  diag.lastPollBars = 0;
  diag.loadAt = Date.now();
  updateDiag(tf);

  if (elFollow.checked && controller && typeof controller.resetViewAndFollow === 'function') {
    controller.resetViewAndFollow(RIGHT_OFFSET_PX);
  }

  setStatus(`ok · tf=${tf}s · bars=${bars.length}`);
}

async function pollLatest() {
  const symbol = elSymbol.value;
  const tf = parseInt(elTf.value, 10);
  if (lastOpenMs == null) return;
  const shouldFollow = Boolean(elFollow.checked && controller && typeof controller.isAtEnd === 'function'
    ? controller.isAtEnd()
    : false);

  try {
    const data = await apiGet(`/api/latest?symbol=${encodeURIComponent(symbol)}&tf_s=${tf}&limit=500&after_open_ms=${lastOpenMs}`);
    const bars = data.bars || [];
    if (bars.length === 0) return;

    for (const b of bars) {
      if (controller && typeof controller.updateLastBar === 'function') {
        controller.updateLastBar(b);
      }
      lastOpenMs = b.open_time_ms;
    }
    if (elFollow.checked && shouldFollow && controller && typeof controller.scrollToRealTimeWithOffset === 'function') {
      controller.scrollToRealTimeWithOffset(RIGHT_OFFSET_PX);
    }
    diag.lastPollBars = bars.length;
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus(`ok · tf=${tf}s · +${bars.length}`);
  } catch (e) {
    diag.lastError = 'poll_error';
    diag.pollAt = Date.now();
    updateDiag(tf);
    setStatus('poll_error');
  }
}

function resetPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollLatest, 3000);
}

async function init() {
  makeChart();
  initToolbars();
  let theme = 'light';
  let candleStyle = 'classic';
  let savedSymbol = null;
  let savedTf = null;
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light' || saved === 'dark-gray') {
      theme = saved;
    } else if (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme = 'dark';
    }
    const savedStyle = localStorage.getItem(CANDLE_STYLE_KEY);
    if (savedStyle) candleStyle = savedStyle;
    savedSymbol = localStorage.getItem(SYMBOL_KEY);
    savedTf = localStorage.getItem(TF_KEY);
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
  const symbolPreferred = savedSymbol || 'XAU/USD';
  if (Array.from(elSymbol.options).some((opt) => opt.value === symbolPreferred)) {
    elSymbol.value = symbolPreferred;
  }
  updateToolbarValue('symbol');
  await loadBarsFull();
  updateToolbarValue('tf');
  resetPolling();
  updateUtcNow();
  setInterval(updateUtcNow, 1000);

  if (elReload) {
    elReload.addEventListener('click', async () => {
      await loadBarsFull();
    });
  }

  elSymbol.addEventListener('change', async () => {
    try {
      localStorage.setItem(SYMBOL_KEY, elSymbol.value);
    } catch (e) {
      // ignore storage errors
    }
    await loadBarsFull();
  });

  elTf.addEventListener('change', async () => {
    try {
      localStorage.setItem(TF_KEY, elTf.value);
    } catch (e) {
      // ignore storage errors
    }
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
    });
    elTheme.addEventListener('input', () => {
      const mode = readThemeValue();
      applyTheme(mode);
    });
  }

  if (elCandleStyle) {
    elCandleStyle.addEventListener('change', () => {
      const style = elCandleStyle.value;
      try {
        localStorage.setItem(CANDLE_STYLE_KEY, style);
      } catch (e) {
        // ignore storage errors
      }
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
