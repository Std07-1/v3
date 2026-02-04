// Мінімальний клієнт для Lightweight Charts.
// Працює з API: /api/symbols, /api/bars, /api/latest.

const elSymbol = document.getElementById('symbol');
const elTf = document.getElementById('tf');
const elReload = document.getElementById('reload');
const elFollow = document.getElementById('follow');
const elTheme = document.getElementById('theme');
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
const RIGHT_OFFSET_PX = 48;
const THEME_KEY = 'ui_chart_theme';
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

function applyTheme(isDark) {
  document.body.classList.toggle('dark', isDark);
  try {
    localStorage.setItem(THEME_KEY, isDark ? 'dark' : 'light');
  } catch (e) {
    // ignore storage errors
  }
  if (controller && typeof controller.setTheme === 'function') {
    controller.setTheme(isDark);
  }
}

async function loadSymbols() {
  const data = await apiGet('/api/symbols');
  const syms = data.symbols || [];
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
  let isDark = false;
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'dark') isDark = true;
    if (saved === 'light') isDark = false;
    if (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      isDark = true;
    }
  } catch (e) {
    // ignore storage errors
  }
  elTheme.checked = isDark;
  applyTheme(isDark);
  await loadSymbols();
  await loadBarsFull();
  resetPolling();
  updateUtcNow();
  setInterval(updateUtcNow, 1000);

  elReload.addEventListener('click', async () => {
    await loadBarsFull();
  });

  elSymbol.addEventListener('change', async () => {
    await loadBarsFull();
  });

  elTf.addEventListener('change', async () => {
    await loadBarsFull();
  });

  elTheme.addEventListener('change', () => {
    applyTheme(elTheme.checked);
  });

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
