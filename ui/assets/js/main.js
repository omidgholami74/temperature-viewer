import { API_LABEL, api, probeApi } from './api.js';
import { TemperatureChart } from './chart-panel.js';
import { LiveFeed } from './feed.js';
import { TemperatureDropFilter } from './drop-filter.js';
import {
  formatAge,
  formatCount,
  formatDateTime,
  formatDelta,
  formatDigits,
  formatHuman,
  formatShortDuration,
  formatStamp,
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
const THEME_STORAGE_KEY = 'furnace.theme';
const DEFAULT_WINDOW = 600;
const DEFAULT_RANGE = 86400;
// One firmware frame is 100 ms, so ten samples are one recorded second. The
// counter is derived from the sample count instead of the upload clock: the
// collector uploads in batches, and a clock-based counter would freeze between
// batches and then jump.
const SAMPLE_MS = 100;
const MIN_THRESHOLD = 1;
const MAX_THRESHOLD = 95;
const PAUSED_AFTER_MS = 2500;
const FAULT_BITS = [
  [1, 'Open thermocouple'],
  [2, 'Short to GND'],
  [4, 'Short to VCC'],
  [8, 'Read error'],
];
const RANGE_PRESETS = {
  3600: 'last hour',
  21600: 'last 6 hours',
  86400: 'last 24 hours',
  604800: 'last 7 days',
  2592000: 'last 30 days',
  0: 'all recorded data',
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const el = {
  status: $('[data-role="status"]'),
  statusLabel: $('[data-role="status-label"]'),
  statusMeta: $('[data-role="status-meta"]'),
  settingsButton: $('[data-role="settings"]'),
  dialog: $('[data-role="settings-dialog"]'),
  dialogClose: $('[data-role="settings-close"]'),
  tabs: $$('[data-tab]'),
  panels: $$('[data-panel]'),
  themeButtons: $$('[data-theme-choice]'),
  themeColor: $('[data-role="theme-color"]'),
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
  idle: $('[data-role="idle"]'),
  age: $('[data-role="age"]'),
  legendButtons: $$('[data-legend]'),
  legendValues: [0, 1, 2, 3].map((index) => $(`[data-role="legend-value-${index}"]`)),
  windowButtons: $$('[data-window]'),
  empty: $('[data-role="empty"]'),
  canvas: $('#chart'),
  toast: $('[data-role="toast"]'),
  rangePresets: $$('[data-range]'),
  rangeCustom: $('[data-role="range-custom"]'),
  rangeFrom: $('[data-role="range-from"]'),
  rangeTo: $('[data-role="range-to"]'),
  rangeApply: $('[data-role="range-apply"]'),
  rangeNote: $('[data-role="range-note"]'),
  analysisCold: $('[data-role="analysis-cold"]'),
  analysisColdMeta: $('[data-role="analysis-cold-meta"]'),
  analysisColdTime: $('[data-role="analysis-cold-time"]'),
  analysisHot: $('[data-role="analysis-hot"]'),
  analysisHotMeta: $('[data-role="analysis-hot-meta"]'),
  analysisHotTime: $('[data-role="analysis-hot-time"]'),
  analysisAvg: $('[data-role="analysis-avg"]'),
  analysisAvgSpan: $('[data-role="analysis-avg-span"]'),
  analysisStatus: $('[data-role="analysis-status"]'),
  analysisRows: [0, 1, 2, 3].map((index) => ({
    root: $(`[data-channel-row="${index}"]`),
    min: $(`[data-role="row-min-${index}"]`),
    minTime: $(`[data-role="row-min-time-${index}"]`),
    max: $(`[data-role="row-max-${index}"]`),
    maxTime: $(`[data-role="row-max-time-${index}"]`),
    avg: $(`[data-role="row-avg-${index}"]`),
    points: $(`[data-role="row-points-${index}"]`),
  })),
  exportButton: $('[data-role="export"]'),
  exportResolution: $('[data-role="export-resolution"]'),
  exportRange: $('[data-role="export-range"]'),
  exportNote: $('[data-role="export-note"]'),
  dataStatus: $('[data-role="data-status"]'),
  noiseEnabled: $('[data-role="noise-enabled"]'),
  noiseThreshold: $('[data-role="noise-threshold"]'),
  noiseStatus: $('[data-role="noise-status"]'),
};

const state = {
  run: null,
  latest: null,
  rawLatest: null,
  filteredLatest: null,
  lastPayload: null,
  lastPayloadAt: 0,
  failures: 0,
  reason: null,
  windowSeconds: DEFAULT_WINDOW,
  theme: document.documentElement.dataset.themePreference ?? 'system',
  bounds: null,
  range: { preset: DEFAULT_RANGE, fromMs: null, toMs: null },
  analysis: null,
  analysisToken: 0,
  analysisLoading: false,
};

/**
 * Sample clock: one stored sample is exactly 0.1 s of recorded time. The count
 * comes from the server, so a retried upload cannot count an interval twice,
 * and the display freezes when no new samples arrive.
 */
const clock = { samples: 0, shown: 0 };
const noiseFilter = new TemperatureDropFilter();
const NOISE_STORAGE_KEY = 'furnace.noise-filter';

let chart = null;
let historyRequest = 0;
let toastTimer = null;

const feed = new LiveFeed({
  onData: applyPayload,
  onError: (error) => {
    state.failures += 1;
    updateStatus();
    console.warn('[live]', error.message);

    // One probe per outage: it costs a request and only changes the hint.
    if (state.failures === 1 || state.failures % 15 === 0) {
      diagnose();
    }

    if (state.failures === 5) {
      toast(`No answer from ${API_LABEL}. Retrying…`, 'error');
    }
  },
});

let diagnosing = false;

/** Ask the browser why the API did not answer, then repaint the status line. */
async function diagnose() {
  if (diagnosing) {
    return;
  }

  diagnosing = true;

  try {
    state.reason = await probeApi();
  } catch {
    state.reason = 'unreachable';
  } finally {
    diagnosing = false;
    updateStatus();
  }
}

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

/* --- recorded-time clock ------------------------------------------------ */

/** Recorded milliseconds for a run: ten stored samples are one second. */
function sampleTime(run) {
  return Math.max(0, Math.round(Number(run?.sample_count) || 0)) * SAMPLE_MS;
}

function syncClock(run) {
  clock.samples = Math.max(0, Math.round(Number(run?.sample_count) || 0));
  // A late or retried answer must never rewind the display.
  clock.shown = Math.max(clock.shown, clock.samples * SAMPLE_MS);
}

function resetClock() {
  clock.samples = 0;
  clock.shown = 0;
}

function elapsedNow() {
  return clock.shown;
}

/* --- rendering ---------------------------------------------------------- */

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

/** Age of the newest sample, using the server clock like the status line. */
function sampleAge() {
  const payload = state.lastPayload;

  if (!payload || !state.latest) {
    return Number.POSITIVE_INFINITY;
  }

  return Math.max(0, payload.server_time_ms - state.latest.received_at_ms)
    + (performance.now() - state.lastPayloadAt);
}

function updateRunChip() {
  const run = state.run;

  if (!run) {
    el.runState.className = 'chip chip--idle';
    setText(el.runState, 'Waiting');
    return;
  }

  if (!run.running) {
    el.runState.className = 'chip chip--idle';
    setText(el.runState, 'Stopped');
    return;
  }

  if (sampleAge() > PAUSED_AFTER_MS) {
    el.runState.className = 'chip chip--idle';
    setText(el.runState, 'Paused');
  } else {
    el.runState.className = 'chip chip--live';
    setText(el.runState, 'Recording');
  }
}

function renderRun(run) {
  const recorded = sampleTime(run);
  const elapsed = Math.max(0, Number(run?.elapsed_ms) || 0);
  const idle = Math.max(0, elapsed - recorded);
  const rate = run && recorded > 5000 ? (Number(run.sample_count) || 0) / (recorded / 1000) : null;

  setText(el.started, run ? `Started ${formatDateTime(run.started_at_ms)}` : 'No run started');
  setText(el.samples, formatCount(run?.sample_count ?? 0));
  setText(el.rate, rate ? `${rate.toFixed(1)} /s` : '—');
  setText(el.idle, run ? formatShortDuration(idle) : '—');
  updateRunChip();
}

function failureText() {
  // The page scheme is part of the message: over https an http API is blocked
  // before the request leaves the tab, and that is invisible from the URL alone.
  const scheme = window.location.protocol.replace(':', '');

  switch (state.reason) {
    case 'mixed-content':
      return 'http API blocked on an https page';
    case 'cors':
      return `${API_LABEL} blocked by CORS`;
    case 'timeout':
      return `${API_LABEL} timed out · ${scheme} page`;
    default:
      return `${API_LABEL} unreachable · ${scheme} page`;
  }
}

function updateStatus() {
  const payload = state.lastPayload;

  if (!payload || !state.latest) {
    const failed = state.failures > 0;
    el.status.dataset.state = failed ? 'offline' : 'waiting';
    setText(el.statusLabel, failed ? 'Backend offline' : 'Connecting');
    // Name the origin the browser tried and, when the probe worked it out, why
    // it failed: wrong scheme, missing CORS header or no answer at all.
    setText(el.statusMeta, failed ? failureText() : 'API not reached yet');
    setText(el.age, '—');
  } else {
    const age = sampleAge();
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

  // On a narrow phone the status collapses to its dot, so the full sentence
  // lives in the accessible name.
  el.status.title = `${el.statusLabel?.textContent ?? ''} · ${el.statusMeta?.textContent ?? ''}`;
  el.status.setAttribute('aria-label', el.status.title);
}

function updateEmptyState() {
  if (el.empty) {
    el.empty.hidden = Boolean(state.latest) && Boolean(chart);
  }
}

function applyPayload(payload) {
  state.failures = 0;
  state.reason = null;
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
    state.rawLatest = null;
    state.filteredLatest = null;
    resetClock();
    chart?.clear();
    noiseFilter.reset();
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

    const filteredSamples = payload.samples.map((sample) => ({
      ...sample,
      temps: noiseFilter.process(sample.temps),
    }));
    chart?.appendSamples(filteredSamples);
    state.filteredLatest = filteredSamples.at(-1) ?? state.filteredLatest;
  }

  if (payload.latest) {
    // Live samples were processed above; reuse that result instead of feeding
    // the same physical frame through the filter a second time. Repeated polls
    // of an unchanged latest frame also reuse its result and don't age the
    // one-frame spike confirmation window.
    const frameWasProcessed = payload.samples?.some((sample) => sample.id === payload.latest.id);
    const isNewLatest = payload.latest.id !== state.rawLatest?.id;
    const filtered = frameWasProcessed && state.filteredLatest?.id === payload.latest.id
      ? state.filteredLatest.temps
      : isNewLatest
        ? noiseFilter.process(payload.latest.temps)
        : (state.latest?.temps ?? noiseFilter.process(payload.latest.temps));
    state.rawLatest = payload.latest;
    state.latest = { ...payload.latest, temps: filtered };
  } else if (state.latest && payload.samples?.length) {
    // An unchanged latest can still have caught up from an earlier id; keep the
    // accepted displayed value aligned with the newest filtered sample.
    state.latest = { ...state.latest, temps: state.filteredLatest?.temps ?? state.latest.temps };
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
    const points = payload.points ?? [];
    const filteredPoints = points.map((point) => ({
      ...point,
      v: noiseFilter.process(point.v ?? []),
    }));
    chart?.replace(filteredPoints);
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
  state.rawLatest = null;
  state.filteredLatest = null;
  noiseFilter.reset();
  resetClock();
  chart?.clear();
  renderCards(null);
  updateLegend(null);
  renderRun(null);
  updateEmptyState();

  // A different run means different bounds and different analysis.
  state.bounds = null;
  state.analysis = null;
  state.analysisToken += 1;
  state.analysisLoading = false;
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

    const human = elapsed > 0 ? formatHuman(elapsed) : '—';

    if (human !== humanText) {
      setText(el.human, human);
      humanText = human;
    }

    requestAnimationFrame(frame);
  };

  requestAnimationFrame(frame);
}

/* --- theme -------------------------------------------------------------- */

const lightQuery = window.matchMedia('(prefers-color-scheme: light)');

function resolveTheme(preference) {
  return preference === 'system' ? (lightQuery.matches ? 'light' : 'dark') : preference;
}

function applyTheme(preference, { persist = true } = {}) {
  const safe = ['dark', 'light', 'system'].includes(preference) ? preference : 'system';
  const resolved = resolveTheme(safe);

  state.theme = safe;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = safe;

  if (el.themeColor) {
    el.themeColor.content = resolved === 'light' ? '#eef3f9' : '#04080d';
  }

  for (const button of el.themeButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === safe));
  }

  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, safe);
    } catch {
      // Private mode: the theme applies to this visit only.
    }
  }

  chart?.setTheme();
}

/** Keep the typed drop threshold a whole percentage inside the allowed range. */
function normalizeThreshold(value, fallback = 60) {
  const numeric = Number(value);

  return Number.isFinite(numeric)
    ? Math.min(MAX_THRESHOLD, Math.max(MIN_THRESHOLD, Math.round(numeric)))
    : fallback;
}

function noiseStatusText(enabled, threshold) {
  return `${enabled ? 'Enabled' : 'Disabled'} · ${enabled ? `ignores isolated drops of ${threshold}% or more` : 'all readings shown'}; raw samples and timer are unchanged.`;
}

function loadNoiseSettings() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(NOISE_STORAGE_KEY) ?? 'null');
  } catch {
    saved = null;
  }
  const enabled = saved?.enabled !== false;
  const threshold = normalizeThreshold(saved?.threshold ?? 60);
  noiseFilter.configure({ enabled, threshold });
  el.noiseEnabled.checked = enabled;
  el.noiseThreshold.value = String(threshold);
  setText(el.noiseStatus, noiseStatusText(enabled, threshold));
  return { enabled, threshold };
}

function saveNoiseSettings() {
  const enabled = el.noiseEnabled.checked;
  const threshold = normalizeThreshold(el.noiseThreshold.value);
  el.noiseThreshold.value = String(threshold);
  noiseFilter.configure({ enabled, threshold });

  try {
    localStorage.setItem(NOISE_STORAGE_KEY, JSON.stringify({ enabled, threshold }));
  } catch {
    // Keep applying settings for this page even if storage is unavailable.
  }

  // Retain the displayed value as the new baseline. Changing settings during
  // a pending spike won't suddenly reveal it; future frames use the new rule.
  noiseFilter.reset(state.latest?.temps ?? []);
  setText(el.noiseStatus, noiseStatusText(enabled, threshold));
  toast(`Noise filter ${enabled ? 'enabled' : 'disabled'}`);
}

lightQuery.addEventListener('change', () => {
  if (state.theme === 'system') {
    applyTheme('system', { persist: false });
  }
});

/* --- settings dialog ---------------------------------------------------- */

function selectTab(name) {
  for (const tab of el.tabs) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }

  for (const panel of el.panels) {
    panel.hidden = panel.dataset.panel !== name;
  }

  if (name === 'data') {
    ensureRange();
  }

  // Analysis covers everything stored, so it is useful from the first sample
  // instead of waiting for a 24 hour window to fill.
  if (name === 'analysis') {
    loadAnalysis();
  }
}

function openSettings(tab = 'appearance') {
  selectTab(tab);

  if (!el.dialog.open) {
    el.dialog.showModal();
  }
}

function closeSettings() {
  if (el.dialog.open) {
    el.dialog.close();
  }
}

/* --- date range --------------------------------------------------------- */

function toLocalInput(milliseconds) {
  const date = new Date(milliseconds);
  const pad = (value) => String(value).padStart(2, '0');

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value) {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);

  return Number.isFinite(parsed.getTime()) ? parsed.getTime() : null;
}

function rangeDuration() {
  return state.range.toMs !== null && state.range.fromMs !== null
    ? state.range.toMs - state.range.fromMs
    : 0;
}

function updateRangeUI() {
  const { fromMs, toMs, preset } = state.range;
  const ready = fromMs !== null && toMs !== null;

  for (const button of el.rangePresets) {
    const value = button.dataset.range;
    button.setAttribute('aria-pressed', String(
      value === 'custom' ? preset === 'custom' : Number(value) === preset,
    ));
  }

  el.rangeCustom.hidden = preset !== 'custom';

  if (ready) {
    const label = `${formatStamp(fromMs)} → ${formatStamp(toMs)} · ${formatShortDuration(rangeDuration())}`;
    setText(el.rangeNote, label);
    setText(el.exportRange, label);
    setText(el.dataStatus, 'Ready to download the selected range.');
  } else {
    setText(el.rangeNote, 'Pick a range to download.');
    setText(el.exportRange, 'No range selected.');
  }

  // A single-sample range is still downloadable; an empty one is not.
  el.exportButton.disabled = !(ready && toMs >= fromMs);

  if (state.bounds) {
    el.rangeFrom.min = toLocalInput(state.bounds.first_ms);
    el.rangeFrom.max = toLocalInput(state.bounds.last_ms);
    el.rangeTo.min = toLocalInput(state.bounds.first_ms);
    el.rangeTo.max = toLocalInput(state.bounds.last_ms);
  }
}

function setRangePreset(seconds) {
  const bounds = state.bounds;

  if (!bounds || !bounds.last_ms) {
    state.range = { preset: seconds, fromMs: null, toMs: null };
    updateRangeUI();
    return;
  }

  state.range.preset = seconds;

  if (seconds === 'custom') {
    const fallbackTo = state.range.toMs ?? bounds.last_ms;
    const fallbackFrom = state.range.fromMs ?? Math.max(bounds.first_ms, fallbackTo - DEFAULT_RANGE * 1000);

    state.range.fromMs = fallbackFrom;
    state.range.toMs = fallbackTo;
    el.rangeFrom.value = toLocalInput(fallbackFrom);
    el.rangeTo.value = toLocalInput(fallbackTo);
    updateRangeUI();
    return;
  }

  const end = bounds.last_ms;
  const start = seconds === 0
    ? bounds.first_ms
    : Math.max(bounds.first_ms, end - seconds * 1000);

  state.range.fromMs = start;
  state.range.toMs = end;
  updateRangeUI();
}

function applyCustomRange() {
  const from = fromLocalInput(el.rangeFrom.value);
  const to = fromLocalInput(el.rangeTo.value);

  if (from === null || to === null) {
    setText(el.dataStatus, 'Choose both the first and the last date.');
    return;
  }

  if (to <= from) {
    setText(el.dataStatus, 'The last date must be after the first date.');
    return;
  }

  state.range.preset = 'custom';
  state.range.fromMs = from;
  state.range.toMs = to;
  updateRangeUI();
}

async function ensureRange() {
  if (!state.bounds) {
    try {
      state.bounds = await api.bounds();
    } catch (error) {
      setText(el.dataStatus, `Could not load the export range: ${error.message}`);
      return;
    }

    if (!state.bounds.last_ms) {
      setText(el.dataStatus, 'No samples stored yet. Start the collector, then open this again.');
      updateRangeUI();
      return;
    }
  }

  if (state.range.fromMs === null) {
    setRangePreset(DEFAULT_RANGE);
  }
}

/* --- analysis ----------------------------------------------------------- */

function resetAnalysis(message) {
  state.analysis = null;
  setText(el.analysisCold, '—');
  setText(el.analysisColdTime, '—');
  setText(el.analysisHot, '—');
  setText(el.analysisHotTime, '—');
  setText(el.analysisAvg, '—');
  setText(el.analysisAvgSpan, '—');
  setText(el.analysisColdMeta, 'per furnace');
  setText(el.analysisHotMeta, 'per furnace');

  for (const row of el.analysisRows) {
    row.root.classList.remove('is-cold', 'is-hot');
    setText(row.min, '—');
    setText(row.minTime, '');
    setText(row.max, '—');
    setText(row.maxTime, '');
    setText(row.avg, '—');
    setText(row.points, '—');
  }

  setText(el.analysisStatus, message);
}

async function loadAnalysis() {
  if (state.analysisLoading) {
    return;
  }

  const token = ++state.analysisToken;
  state.analysisLoading = true;
  setText(el.analysisStatus, 'Loading analysis…');

  try {
    // No range: the API analyses everything it has stored, which is never
    // empty once the first sample exists.
    const data = await api.summary();

    if (token !== state.analysisToken) {
      return;
    }

    state.analysis = data;
    renderAnalysis(data);
  } catch (error) {
    if (token === state.analysisToken) {
      resetAnalysis(`Could not load the analysis: ${error.message}`);
    }
  } finally {
    if (token === state.analysisToken) {
      state.analysisLoading = false;
    }
  }
}

function renderAnalysis(data) {
  if (!data || data.samples === 0) {
    resetAnalysis('No samples stored yet. The analysis fills as soon as the first sample arrives.');
    return;
  }

  const coldest = data.coldest;
  const hottest = data.hottest;

  setText(
    el.analysisCold,
    coldest ? `${formatTemp(coldest.value)} °C` : '—',
  );
  setText(
    el.analysisColdMeta,
    coldest ? CHANNELS[coldest.channel]?.name ?? '—' : 'no data',
  );
  setText(
    el.analysisColdTime,
    coldest?.at_ms ? formatDateTime(coldest.at_ms) : '—',
  );
  setText(
    el.analysisHot,
    hottest ? `${formatTemp(hottest.value)} °C` : '—',
  );
  setText(
    el.analysisHotMeta,
    hottest ? CHANNELS[hottest.channel]?.name ?? '—' : 'no data',
  );
  setText(
    el.analysisHotTime,
    hottest?.at_ms ? formatDateTime(hottest.at_ms) : '—',
  );
  setText(el.analysisAvg, data.average === null ? '—' : `${formatTemp(data.average)} °C`);
  setText(
    el.analysisAvgSpan,
    data.from_ms && data.to_ms
      ? `over ${formatShortDuration(data.to_ms - data.from_ms)}`
      : '—',
  );

  data.channels.forEach((channel, index) => {
    const row = el.analysisRows[index];

    if (!row) {
      return;
    }

    const hasSamples = channel.samples > 0;

    setText(row.min, hasSamples ? `${formatTemp(channel.min)}°` : '—');
    setText(row.minTime, hasSamples && channel.min_at_ms ? formatDateTime(channel.min_at_ms) : '');
    setText(row.max, hasSamples ? `${formatTemp(channel.max)}°` : '—');
    setText(row.maxTime, hasSamples && channel.max_at_ms ? formatDateTime(channel.max_at_ms) : '');
    setText(row.avg, hasSamples ? `${formatTemp(channel.avg)}°` : '—');
    setText(row.points, hasSamples ? formatCount(channel.samples) : '—');
    row.root.classList.toggle('is-cold', coldest?.channel === index);
    row.root.classList.toggle('is-hot', hottest?.channel === index);
  });

  const source = data.resolution === '1s' ? '1 s averages' : 'raw samples';
  const span = data.from_ms && data.to_ms
    ? ` · ${formatStamp(data.from_ms)} → ${formatStamp(data.to_ms)}`
    : '';
  setText(
    el.analysisStatus,
    `${formatCount(data.samples)} points · ${data.runs} ${data.runs === 1 ? 'run' : 'runs'} · ${source}${span}`,
  );
}

/* --- export ------------------------------------------------------------- */

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 KB';
  }

  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');

  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();

  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function downloadExport() {
  if (state.range.fromMs === null || state.range.toMs === null) {
    setText(el.dataStatus, 'Pick an export range first.');
    return;
  }

  const label = el.exportButton.querySelector('span') ?? el.exportButton;
  const original = label.textContent;

  el.exportButton.disabled = true;
  label.textContent = 'Preparing…';
  setText(el.dataStatus, 'Building the file on the server…');

  try {
    const { blob, filename } = await api.exportData({
      fromMs: state.range.fromMs,
      toMs: state.range.toMs,
      resolution: el.exportResolution.value,
    });

    saveBlob(blob, filename);
    setText(el.dataStatus, `Saved ${filename} · ${formatBytes(blob.size)}`);
    toast(`Exported ${filename}`);
  } catch (error) {
    setText(el.dataStatus, error.message);
  } finally {
    el.exportButton.disabled = false;
    label.textContent = original;
  }
}

/* --- run control -------------------------------------------------------- */

async function startNewRun() {
  if (!window.confirm('Start a new run? The recorded timer will restart from 00:00:00:00.')) {
    return;
  }

  el.newRun.disabled = true;

  try {
    const payload = await api.reset();
    historyRequest += 1;
    state.run = payload.run;
    state.latest = null;
    state.rawLatest = null;
    state.filteredLatest = null;
    state.lastPayload = null;
    const noiseSettings = loadNoiseSettings();
    noiseFilter.configure(noiseSettings);
    noiseFilter.reset();
    resetClock();
    chart?.clear();
    feed.selectRun(payload.run.id);
    syncClock(payload.run);
    renderRun(payload.run);
    renderCards(null);
    updateLegend(null);
    updateEmptyState();
    state.bounds = null;
    state.analysis = null;
    state.analysisToken += 1;
    state.analysisLoading = false;
    closeSettings();
    toast('New run started');
  } catch (error) {
    setText(el.dataStatus, `Could not start a run: ${error.message}`);
    toast(`Could not start a run: ${error.message}`, 'error');
  } finally {
    el.newRun.disabled = false;
  }
}

/* --- offline shell ------------------------------------------------------ */

function registerWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('./sw.js').catch((error) => {
      // Live data still works without a worker; say nothing on screen.
      console.warn('[sw]', error.message);
    });
  }
}

/* --- events ------------------------------------------------------------- */

function bindEvents() {
  el.settingsButton.addEventListener('click', () => openSettings('appearance'));
  el.dialogClose.addEventListener('click', closeSettings);

  // Clicking the dimmed area (the dialog element itself) closes the sheet.
  el.dialog.addEventListener('click', (event) => {
    if (event.target === el.dialog) {
      closeSettings();
    }
  });

  for (const tab of el.tabs) {
    tab.addEventListener('click', () => selectTab(tab.dataset.tab));
    tab.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
        return;
      }

      event.preventDefault();
      const current = el.tabs.indexOf(tab);
      const next = el.tabs[(current + (event.key === 'ArrowRight' ? 1 : el.tabs.length - 1)) % el.tabs.length];
      next.focus();
      selectTab(next.dataset.tab);
    });
  }

  for (const button of el.themeButtons) {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
  }

  el.noiseEnabled.addEventListener('change', saveNoiseSettings);
  el.noiseThreshold.addEventListener('change', saveNoiseSettings);

  el.newRun.addEventListener('click', startNewRun);

  for (const button of el.rangePresets) {
    button.addEventListener('click', () => {
      const value = button.dataset.range;
      setRangePreset(value === 'custom' ? 'custom' : Number(value));
    });
  }

  el.rangeApply.addEventListener('click', applyCustomRange);

  el.exportButton.addEventListener('click', downloadExport);

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

  // Keep the Paused/Recording chip honest even when the feed backs off.
  setInterval(updateRunChip, 1000);
}

function bootstrap() {
  applyTheme(state.theme, { persist: false });
  loadNoiseSettings();

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

  updateRangeUI();
  updateLegend(null);
  renderCards(null);
  renderRun(null);
  updateStatus();
  startCounter();
  registerWorker();
  bindEvents();

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
