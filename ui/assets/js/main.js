import { api } from './api.js';
import { TemperatureChart } from './chart-panel.js';
import { LiveFeed } from './feed.js';
import {
  formatAge,
  formatCount,
  formatDateTime,
  formatDelta,
  formatDigits,
  formatHuman,
  formatTemp,
} from './format.js';

const CHANNELS = [
  { name: 'Furnace 1', fallback: '#38e0a5' },
  { name: 'Furnace 2', fallback: '#49bdf0' },
  { name: 'Furnace 3', fallback: '#f3b45a' },
  { name: 'Furnace 4', fallback: '#b491ff' },
];

const WINDOWS = [60, 600, 3600, 21600, 86400, 0];
const WINDOW_STORAGE_KEY = 'furnace.window';
const DEFAULT_WINDOW = 600;
const FAULT_BITS = [
  [1, 'Open thermocouple'],
  [2, 'Short to GND'],
  [4, 'Short to VCC'],
  [8, 'Read error'],
];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const el = {
  status: $('[data-role="status"]'),
  statusLabel: $('[data-role="status-label"]'),
  statusMeta: $('[data-role="status-meta"]'),
  newRun: $('[data-role="new-run"]'),
  cards: $$('.furnace-card').map((root) => ({
    root,
    value: $('[data-role="value"]', root),
    trend: $('[data-role="trend"]', root),
    delta: $('[data-role="delta"]', root),
    fault: $('[data-role="fault"]', root),
  })),
  runState: $('[data-role="run-state"]'),
  started: $('[data-role="started"]'),
  digits: {
    hours: $('[data-digit="hours"]'),
    minutes: $('[data-digit="minutes"]'),
    seconds: $('[data-digit="seconds"]'),
    tenths: $('[data-digit="tenths"]'),
  },
  human: $('[data-role="human"]'),
  samples: $('[data-role="samples"]'),
  rate: $('[data-role="rate"]'),
  age: $('[data-role="age"]'),
  legendButtons: $$('[data-legend]'),
  legendValues: [0, 1, 2, 3].map((index) => $(`[data-role="legend-value-${index}"]`)),
  windowButtons: $$('[data-window]'),
  empty: $('[data-role="empty"]'),
  canvas: $('#chart'),
  toast: $('[data-role="toast"]'),
};

const state = {
  run: null,
  latest: null,
  lastPayload: null,
  lastPayloadAt: 0,
  failures: 0,
  windowSeconds: DEFAULT_WINDOW,
};

const clock = { base: 0, at: 0, running: false };

let chart = null;
let historyRequest = 0;
let toastTimer = null;

const feed = new LiveFeed({
  onData: applyPayload,
  onError: (error) => {
    state.failures += 1;
    updateStatus();
    console.warn('[live]', error.message);

    if (state.failures === 5) {
      toast('Connection to the PHP API lost. Retrying…', 'error');
    }
  },
});

function setText(node, value) {
  if (node && node.textContent !== value) {
    node.textContent = value;
  }
}

function faultText(bits) {
  const labels = FAULT_BITS
    .filter(([bit]) => (bits & bit) === bit)
    .map(([, label]) => label);

  return labels.length ? labels.join(' · ') : 'Fault';
}

function trendFor(delta) {
  if (!Number.isFinite(delta)) {
    return { label: 'No data', tone: 'idle' };
  }

  if (delta > 1.5) {
    return { label: 'Heating', tone: 'heating' };
  }

  if (delta < -1.5) {
    return { label: 'Cooling', tone: 'cooling' };
  }

  return { label: 'Holding', tone: 'live' };
}

function syncClock(run) {
  clock.base = run?.elapsed_ms ?? 0;
  clock.at = performance.now();
  clock.running = Boolean(run?.running);
}

function elapsedNow() {
  return clock.running ? clock.base + (performance.now() - clock.at) : clock.base;
}

function renderCards(latest) {
  el.cards.forEach((card, index) => {
    const value = latest?.temps?.[index] ?? null;
    const fault = latest?.faults?.[index] ?? 0;
    let delta = Number.NaN;

    if (latest && value !== null) {
      const previous = chart?.sampleAt(index, latest.t_ms - 10000);
      delta = previous === null || previous === undefined ? Number.NaN : value - previous;
    }

    setText(card.value, formatTemp(value));
    card.root.classList.toggle('is-fault', Boolean(fault));
    card.fault.hidden = !fault;
    setText(card.fault, fault ? faultText(fault) : '');

    if (fault) {
      card.trend.className = 'chip chip--fault';
      setText(card.trend, 'Fault');
    } else if (value === null) {
      card.trend.className = 'chip chip--idle';
      setText(card.trend, 'No data');
    } else {
      const trend = trendFor(delta);
      card.trend.className = `chip chip--${trend.tone}`;
      setText(card.trend, trend.label);
    }

    card.delta.dataset.direction = Number.isFinite(delta)
      ? delta > 0.05
        ? 'up'
        : delta < -0.05
          ? 'down'
          : 'flat'
      : '';
    setText(card.delta, Number.isFinite(delta) ? `10 s ${formatDelta(delta)}` : '10 s change —');
  });
}

function updateLegend(latest) {
  el.legendValues.forEach((node, index) => {
    const value = latest?.temps?.[index] ?? null;
    setText(node, value === null ? '—' : `${formatTemp(value)}°`);
  });
}

function renderRun(run) {
  const running = Boolean(run?.running);

  el.runState.className = `chip ${run ? (running ? 'chip--live' : 'chip--idle') : 'chip--idle'}`;
  setText(el.runState, run ? (running ? 'Recording' : 'Stopped') : 'Waiting');
  setText(el.started, run ? `Started ${formatDateTime(run.started_at_ms)}` : 'No run started');
  setText(el.samples, formatCount(run?.sample_count ?? 0));

  const rate = run && run.elapsed_ms > 5000 ? run.sample_count / (run.elapsed_ms / 1000) : null;
  setText(el.rate, rate ? `${rate.toFixed(1)} /s` : '—');
}

function updateStatus() {
  const payload = state.lastPayload;

  if (!payload || !state.latest) {
    const failed = state.failures > 0;
    el.status.dataset.state = failed ? 'offline' : 'waiting';
    setText(el.statusLabel, failed ? 'Backend offline' : 'Connecting');
    setText(el.statusMeta, failed ? 'PHP API unreachable' : 'API not reached yet');
    setText(el.age, '—');
    return;
  }

  const age = Math.max(0, payload.server_time_ms - state.latest.received_at_ms)
    + (performance.now() - state.lastPayloadAt);
  const tone = state.failures > 0 || age > 15000
    ? 'offline'
    : age > 2500
      ? 'delayed'
      : 'live';

  el.status.dataset.state = tone;

  setText(
    el.statusLabel,
    tone === 'live' ? 'Live' : tone === 'delayed' ? 'Delayed' : 'No signal',
  );
  setText(el.statusMeta, `last sample ${formatAge(age)}`);
  setText(el.age, formatAge(age));
}

function updateEmptyState() {
  if (el.empty) {
    el.empty.hidden = Boolean(state.latest) && Boolean(chart);
  }
}

function applyPayload(payload) {
  state.failures = 0;
  state.lastPayload = payload;
  state.lastPayloadAt = performance.now();

  const activeId = payload.active_run_id ?? null;

  // Another tab or operator started a new run: follow it.
  if (activeId !== null && state.run && activeId !== state.run.id) {
    switchToRun(activeId);
    return;
  }

  const run = payload.run ?? null;
  const runChanged = (run?.id ?? null) !== (state.run?.id ?? null);

  if (runChanged) {
    state.run = run;
    state.latest = null;
    chart?.clear();
    syncClock(run);

    if (run) {
      loadHistory();
    }
  } else if (run) {
    state.run = run;
    syncClock(run);
  }

  if (payload.resync) {
    loadHistory();
  }

  if (payload.samples?.length) {
    if (state.run && state.windowSeconds === 0 && chart) {
      chart.setWindow(Math.max(state.run.elapsed_ms, 60000), { render: false });
    }

    chart?.appendSamples(payload.samples);
  }

  if (payload.latest) {
    state.latest = payload.latest;
  }

  renderCards(state.latest);
  updateLegend(state.latest);
  renderRun(state.run);
  updateStatus();
  updateEmptyState();
}

async function loadHistory({ runId = state.run?.id ?? null } = {}) {
  if (!chart) {
    return;
  }

  const token = ++historyRequest;

  try {
    const payload = await api.history({
      window: state.windowSeconds,
      maxPoints: 1400,
      run: runId,
    });

    if (token !== historyRequest) {
      return;
    }

    const run = payload.run ?? null;

    if (runId !== null && run !== null && run.id !== runId) {
      return;
    }

    if (run) {
      state.run = run;
      syncClock(run);
      renderRun(run);
    }

    const windowMs = state.windowSeconds === 0
      ? Math.max(payload.to_ms ?? 0, 60000)
      : state.windowSeconds * 1000;

    chart?.setWindow(windowMs, { render: false });
    chart?.replace(payload.points ?? []);
    updateEmptyState();
  } catch (error) {
    if (token === historyRequest) {
      console.warn('[history]', error.message);
    }
  }
}

function switchToRun(runId) {
  historyRequest += 1;
  feed.selectRun(runId);
  state.run = null;
  state.latest = null;
  chart?.clear();
  renderCards(null);
  updateLegend(null);
  renderRun(null);
  updateEmptyState();
  loadHistory({ runId });
}

function selectWindow(seconds, { persist = true } = {}) {
  state.windowSeconds = seconds;

  for (const button of el.windowButtons) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.window) === seconds));
  }

  if (persist) {
    try {
      localStorage.setItem(WINDOW_STORAGE_KEY, String(seconds));
    } catch {
      // Private mode: the selection simply does not persist.
    }
  }

  const windowMs = seconds === 0
    ? Math.max(state.run?.elapsed_ms ?? 0, 60000)
    : seconds * 1000;

  chart?.setWindow(windowMs);
  loadHistory();
}

function toast(message, tone = 'info') {
  if (!el.toast) {
    return;
  }

  el.toast.textContent = message;
  el.toast.dataset.tone = tone;
  el.toast.dataset.visible = 'true';

  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.dataset.visible = 'false';
  }, 3200);
}

function startCounter() {
  let digitsKey = '';
  let humanText = '';

  const frame = () => {
    const elapsed = elapsedNow();
    const digits = formatDigits(elapsed);

    if (digits.key !== digitsKey) {
      setText(el.digits.hours, digits.hours);
      setText(el.digits.minutes, digits.minutes);
      setText(el.digits.seconds, digits.seconds);
      setText(el.digits.tenths, digits.tenths);
      digitsKey = digits.key;
    }

    const human = formatHuman(elapsed);

    if (human !== humanText) {
      setText(el.human, human);
      humanText = human;
    }

    requestAnimationFrame(frame);
  };

  requestAnimationFrame(frame);
}

function bindEvents() {
  el.newRun?.addEventListener('click', async () => {
    if (!window.confirm('Start a new run? The timer will reset to 00:00:00:00.')) {
      return;
    }

    el.newRun.disabled = true;

    try {
      const payload = await api.reset();
      historyRequest += 1;
      state.run = payload.run;
      state.latest = null;
      state.lastPayload = null;
      chart?.clear();
      feed.selectRun(payload.run.id);
      syncClock(payload.run);
      renderRun(payload.run);
      renderCards(null);
      updateLegend(null);
      updateEmptyState();
      toast('New run started');
    } catch (error) {
      toast(`Could not start a run: ${error.message}`, 'error');
    } finally {
      el.newRun.disabled = false;
    }
  });

  for (const button of el.windowButtons) {
    button.addEventListener('click', () => selectWindow(Number(button.dataset.window)));
  }

  for (const button of el.legendButtons) {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.legend);
      const visible = button.getAttribute('aria-pressed') === 'true';
      button.setAttribute('aria-pressed', String(!visible));
      chart?.setVisible(index, !visible);
    });
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      feed.wake();

      if (state.run) {
        loadHistory();
      }
    }
  });

  window.addEventListener('online', () => feed.wake());

  window.addEventListener('offline', () => {
    state.failures = Math.max(1, state.failures);
    updateStatus();
  });
}

function bootstrap() {
  try {
    chart = new TemperatureChart(el.canvas, CHANNELS);
  } catch (error) {
    console.error(error);
    el.empty.textContent = `Chart could not start: ${error.message}`;
    el.empty.hidden = false;
  }

  let storedWindow = null;

  try {
    const raw = localStorage.getItem(WINDOW_STORAGE_KEY);
    storedWindow = raw === null ? null : Number(raw);
  } catch {
    storedWindow = null;
  }

  state.windowSeconds = WINDOWS.includes(storedWindow) ? storedWindow : DEFAULT_WINDOW;

  for (const button of el.windowButtons) {
    button.setAttribute(
      'aria-pressed',
      String(Number(button.dataset.window) === state.windowSeconds),
    );
  }

  bindEvents();
  renderRun(null);
  renderCards(null);
  updateStatus();
  startCounter();

  feed.start();
  loadHistory();

  // Keep long windows crisp without streaming thousands of raw points.
  setInterval(() => {
    if (!document.hidden && state.run && (state.windowSeconds === 0 || state.windowSeconds > 120)) {
      loadHistory();
    }
  }, 20000);
}

bootstrap();
