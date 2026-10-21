import { TemperatureChart } from './chart-panel.js';
import { TemperatureDropFilter } from './drop-filter.js';
import {
  formatAge,
  formatCount,
  formatDateTime,
  formatDelta,
  formatDigits,
  formatHuman,
  formatShortDuration,
  formatTemp,
} from './format.js';

/**
 * Same dashboard as ../../ui, fed by the USB serial port instead of the API.
 *
 * The firmware prints one line per sample (10 per second):
 *   12300,781.25,660.00,940.00,-1000.00
 * The JSON format from the collector is accepted too, so both firmwares work.
 */

const CHANNELS = [
  { name: 'Furnace 1', fallback: '#38e0a5' },
  { name: 'Furnace 2', fallback: '#49bdf0' },
  { name: 'Furnace 3', fallback: '#f3b45a' },
  { name: 'Furnace 4', fallback: '#b491ff' },
];

// The port number (COM5) is never written in code: the browser shows a picker
// and the operator chooses the device once. Filters only narrow that list so
// the Raspberry Pi Pico appears alone. Set to [] if your board is not listed.
// Raspberry Pi USB vendor id is 0x2e8a (Pico, Pico W, Pico 2).
const PORT_FILTERS = [{ usbVendorId: 0x2e8a }];
const BAUD_RATE = 115200; // must match the firmware
// Reuse a port the browser already allowed, so a reload connects without the
// picker. Set to false to always choose the port by hand.
const AUTO_CONNECT = true;

const WINDOWS = [60, 600, 3600, 21600, 86400, 0];
const WINDOW_STORAGE_KEY = 'furnace.serial.window';
const NOISE_STORAGE_KEY = 'furnace.serial.noise-filter';
const DEFAULT_WINDOW = 600;
const FAULT_SENTINEL = -1000;
const STALE_MS = 2500;
const LOST_MS = 15000;
// The firmware prints one frame every 100 ms, so ten frames are one recorded
// second. Counting frames instead of wall-clock gaps keeps the timer exact and
// frozen while the port is quiet.
const SAMPLE_MS = 100;
const MIN_THRESHOLD = 1;
const MAX_THRESHOLD = 95;
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
  portButton: $('[data-role="port"]'),
  portLabel: $('[data-role="port-label"]'),
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
  settingsButton: $('[data-role="settings"]'),
  settingsDialog: $('[data-role="settings-dialog"]'),
  settingsClose: $('[data-role="settings-close"]'),
  noiseEnabled: $('[data-role="noise-enabled"]'),
  noiseThreshold: $('[data-role="noise-threshold"]'),
  noiseStatus: $('[data-role="noise-status"]'),
};

const state = {
  latest: null,
  samples: 0,
  windowSeconds: DEFAULT_WINDOW,
  session: null,
  connected: false,
};

let chart = null;
let port = null;
let reader = null;
let running = false;
let toastTimer = null;
const noiseFilter = new TemperatureDropFilter();
const NOISE_SETTINGS = { enabled: true, threshold: 60 };

/* --- formatting helpers (same wording as the API dashboard) ------------- */

function setText(node, value) {
  if (node && node.textContent !== value) {
    node.textContent = value;
  }
}

function faultText(bits) {
  if (bits === null) {
    return 'Sensor fault';
  }

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

function elapsedMs() {
  return state.samples * SAMPLE_MS;
}

/** Wall-clock span of the session, used only to show idle time. */
function wallMs() {
  if (!state.session) return 0;

  const reference = state.session.stoppedAt ?? performance.now();

  return Math.max(0, reference - state.session.startedAt);
}

/* --- rendering --------------------------------------------------------- */

function renderCards(latest) {
  el.cards.forEach((card, index) => {
    const value = latest?.temps?.[index] ?? null;
    const bits = latest?.faults?.[index] ?? 0;
    // CSV sends no fault bits, so a null temperature is the only fault signal.
    const fault = Boolean(latest) && (value === null || bits > 0);
    let delta = Number.NaN;

    if (latest && value !== null) {
      const previous = chart?.sampleAt(index, latest.t_ms - 10000);
      delta = previous === null || previous === undefined ? Number.NaN : value - previous;
    }

    setText(card.value, formatTemp(value));
    card.root.classList.toggle('is-fault', fault);
    card.fault.hidden = !fault;
    setText(card.fault, fault ? faultText(bits > 0 ? bits : null) : '');

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

function renderSession() {
  const session = state.session;
  const chipTone = !session ? 'chip--idle' : running ? 'chip--live' : 'chip--idle';

  el.runState.className = `chip ${chipTone}`;
  setText(el.runState, !session ? 'Waiting' : running ? 'Recording' : 'Stopped');
  setText(
    el.started,
    session ? `Started ${formatDateTime(session.startedAtEpoch)}` : 'No session started',
  );
  setText(el.samples, formatCount(state.samples));

  const elapsed = elapsedMs();
  const rate = session && elapsed > 5000 ? (state.samples / elapsed) * 1000 : null;
  setText(el.rate, rate ? `${rate.toFixed(1)} /s` : '—');
  setText(el.idle, session ? formatShortDuration(Math.max(0, wallMs() - elapsed)) : '—');
}

function updateStatus() {
  if (!state.session || !state.latest) {
    el.status.dataset.state = 'waiting';
    setText(el.statusLabel, state.connected ? 'Waiting' : 'Ready');
    setText(
      el.statusMeta,
      state.connected ? 'No sample received yet' : 'Select the COM port',
    );
    setText(el.age, '—');
    return;
  }

  const age = Math.max(0, performance.now() - state.latest.received_at);
  const tone = !state.connected || age > LOST_MS ? 'offline' : age > STALE_MS ? 'delayed' : 'live';

  el.status.dataset.state = tone;
  setText(el.statusLabel, tone === 'live' ? 'Live' : tone === 'delayed' ? 'Delayed' : 'No signal');
  setText(el.statusMeta, `last sample ${formatAge(age)}`);
  setText(el.age, formatAge(age));
}

function updateEmptyState() {
  if (el.empty) {
    el.empty.hidden = Boolean(state.latest);
  }
}

/* --- serial input ------------------------------------------------------ */

function toTemperature(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }

  const number = Number(value);

  if (!Number.isFinite(number) || number === FAULT_SENTINEL) {
    return null;
  }

  return number;
}

/** CSV (firmware default) or JSON lines (collector format). */
function parseLine(line) {
  const text = String(line).trim();
  if (!text) return null;

  if (text.startsWith('{')) {
    let frame;
    try {
      frame = JSON.parse(text);
    } catch {
      return null;
    }

    if (!frame || !Array.isArray(frame.t)) return null;

    return {
      u: Number(frame.u),
      temps: frame.t.slice(0, 4).map(toTemperature),
      faults: Array.isArray(frame.f) ? frame.f.slice(0, 4) : [0, 0, 0, 0],
    };
  }

  const parts = text.split(',').map((part) => part.trim());
  const numeric = parts.every((part) => part !== '' && Number.isFinite(Number(part)));

  if (!numeric) return null; // header row or chatter

  const temps = parts.slice(1, 5).map(toTemperature);

  return {
    u: Number(parts[0]),
    temps,
    faults: temps.map((value) => (value === null ? null : 0)),
  };
}

function startSession(uptime) {
  state.session = {
    startedAt: performance.now(),
    startedAtEpoch: Date.now(),
    baseUptime: uptime,
    lastUptime: uptime,
    stoppedAt: undefined,
  };
  state.samples = 0;
  state.latest = null;
  noiseFilter.configure(NOISE_SETTINGS);
  noiseFilter.reset();
  chart?.clear();
  renderCards(null);
  updateLegend(null);
  updateEmptyState();
}

function applyFrame(frame) {
  if (!state.session || frame.u < state.session.lastUptime) {
    startSession(frame.u);
  }

  const tMs = frame.u - state.session.baseUptime;
  state.session.lastUptime = frame.u;

  const filteredTemps = noiseFilter.process(frame.temps);
  chart?.appendSamples([{ t_ms: tMs, temps: filteredTemps }]);

  state.samples += 1;
  state.latest = {
    t_ms: tMs,
    rawTemps: frame.temps,
    temps: filteredTemps,
    faults: frame.faults,
    received_at: performance.now(),
  };

  renderCards(state.latest);
  updateLegend(state.latest);
  renderSession();
  updateStatus();
  updateEmptyState();
}

/* --- port handling ----------------------------------------------------- */

function matchesFilter(candidate) {
  if (!PORT_FILTERS.length) return true;

  const info = candidate.getInfo ? candidate.getInfo() : {};

  return PORT_FILTERS.some(
    (filter) =>
      (filter.usbVendorId === undefined || filter.usbVendorId === info.usbVendorId) &&
      (filter.usbProductId === undefined || filter.usbProductId === info.usbProductId),
  );
}

async function openPort(candidate) {
  try {
    await candidate.open({ baudRate: BAUD_RATE });
  } catch (error) {
    notify(`Could not open the port: ${error.message}`, 'error');
    return false;
  }

  port = candidate;
  running = true;
  state.connected = true;
  state.session = null;
  state.latest = null;
  state.samples = 0;
  chart?.clear();
  renderCards(null);
  updateLegend(null);
  renderSession();
  updateStatus();
  updateEmptyState();

  el.portLabel.textContent = 'Disconnect';
  notify('Serial port connected');
  readLoop();

  return true;
}

async function connect() {
  if (!('serial' in navigator)) {
    notify('Web Serial is not available in this browser. Use Chrome or Edge.', 'error');
    return;
  }

  let candidate;

  try {
    candidate = await navigator.serial.requestPort({ filters: PORT_FILTERS });
  } catch (error) {
    // Cancelling the picker is not an error worth a toast.
    if (error.name !== 'NotFoundError') {
      notify(`Could not open the port: ${error.message}`, 'error');
    }
    return;
  }

  await openPort(candidate);
}

/** Reopen a port the operator already granted, so a reload needs no click. */
async function autoConnect() {
  if (!AUTO_CONNECT || !('serial' in navigator)) return;

  try {
    const granted = await navigator.serial.getPorts();
    const candidate = granted.find(matchesFilter);

    if (candidate && (await openPort(candidate))) {
      notify('Reconnected to the saved port');
    }
  } catch {
    // No permission yet, or the API refused: the button still works.
  }
}

async function disconnect() {
  running = false;
  state.connected = false;

  try {
    if (reader) await reader.cancel();
  } catch {
    // already closed
  }

  try {
    if (port) await port.close();
  } catch {
    // already closed
  }

  if (state.session && state.session.stoppedAt === undefined) {
    state.session.stoppedAt = performance.now();
  }

  reader = null;
  port = null;
  el.portLabel.textContent = 'Connect port';
  renderSession();
  updateStatus();
}

async function readLoop() {
  const decoder = new TextDecoderStream();
  port.readable.pipeTo(decoder.writable).catch(() => {});
  reader = decoder.readable.getReader();

  let buffer = '';

  try {
    while (running) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += value;

      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const frame = parseLine(buffer.slice(0, index));
        buffer = buffer.slice(index + 1);

        if (frame) applyFrame(frame);
      }
    }
  } catch (error) {
    if (running) notify(`Read error: ${error.message}`, 'error');
  } finally {
    if (running) {
      running = false;
      state.connected = false;

      if (state.session && state.session.stoppedAt === undefined) {
        state.session.stoppedAt = performance.now();
      }
      try {
        await port?.close();
      } catch {
        // ignore
      }
      port = null;
      el.portLabel.textContent = 'Connect port';
      updateStatus();
      renderSession();
    }
  }
}

/* --- window and legend controls ---------------------------------------- */

function selectWindow(seconds, { persist = true } = {}) {
  state.windowSeconds = seconds;

  for (const button of el.windowButtons) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.window) === seconds));
  }

  if (persist) {
    try {
      localStorage.setItem(WINDOW_STORAGE_KEY, String(seconds));
    } catch {
      // private mode: the choice simply does not persist
    }
  }

  const windowMs = seconds === 0
    ? Math.max(state.latest?.t_ms ?? 0, 60000)
    : seconds * 1000;

  chart?.setWindow(windowMs);
}

/* --- misc -------------------------------------------------------------- */

function notify(message, tone = 'info') {
  if (!el.toast) return;

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

  const tick = () => {
    const elapsed = elapsedMs();
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

    if (state.latest && state.connected) {
      updateStatus();
      renderSession();
    }
  };

  const frame = () => {
    tick();
    requestAnimationFrame(frame);
  };

  tick();
  setInterval(tick, 200); // requestAnimationFrame pauses in a background tab
  requestAnimationFrame(frame);
}

/** Keep the typed drop threshold a whole percentage inside the allowed range. */
function normalizeThreshold(value, fallback = 60) {
  const numeric = Number(value);

  return Number.isFinite(numeric)
    ? Math.min(MAX_THRESHOLD, Math.max(MIN_THRESHOLD, Math.round(numeric)))
    : fallback;
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
  NOISE_SETTINGS.enabled = enabled;
  NOISE_SETTINGS.threshold = threshold;
  noiseFilter.configure({ enabled, threshold });
  el.noiseEnabled.checked = enabled;
  el.noiseThreshold.value = String(threshold);
  setText(el.noiseStatus, noiseStatusText(enabled, threshold));
}

function noiseStatusText(enabled, threshold) {
  return `${enabled ? 'Enabled' : 'Disabled'} · ${enabled ? `ignores isolated drops of ${threshold}% or more` : 'all readings shown'}; raw frames and timer are unchanged.`;
}

function saveNoiseSettings() {
  const enabled = el.noiseEnabled.checked;
  const threshold = normalizeThreshold(el.noiseThreshold.value);
  el.noiseThreshold.value = String(threshold);
  NOISE_SETTINGS.enabled = enabled;
  NOISE_SETTINGS.threshold = threshold;
  noiseFilter.configure({ enabled, threshold });

  try {
    localStorage.setItem(NOISE_STORAGE_KEY, JSON.stringify({ enabled, threshold }));
  } catch {
    // Apply for the current session if browser storage is unavailable.
  }
  // The next frame uses the new setting. Previously displayed history remains
  // unchanged, and configuration never alters session time or raw frame count.
  // Retain the currently displayed value as the baseline so changing settings
  // during a pending spike cannot suddenly reveal that spike in the card.
  noiseFilter.reset(state.latest?.temps ?? []);
  setText(el.noiseStatus, noiseStatusText(enabled, threshold));
  notify(`Noise filter ${enabled ? 'enabled' : 'disabled'}`);
}

function bindEvents() {
  el.settingsButton?.addEventListener('click', () => el.settingsDialog.showModal());
  el.settingsClose?.addEventListener('click', () => el.settingsDialog.close());
  el.settingsDialog?.addEventListener('click', (event) => {
    if (event.target === el.settingsDialog) el.settingsDialog.close();
  });
  el.noiseEnabled?.addEventListener('change', saveNoiseSettings);
  el.noiseThreshold?.addEventListener('change', saveNoiseSettings);

  el.portButton?.addEventListener('click', () => {
    if (running) disconnect();
    else connect();
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
}

function bootstrap() {
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

  chart?.setWindow(state.windowSeconds === 0 ? 60000 : state.windowSeconds * 1000);

  bindEvents();
  renderSession();
  renderCards(null);
  updateLegend(null);
  updateStatus();
  updateEmptyState();
  startCounter();
}

// Test hook: feed a line without a serial port (see README).
window.furnaceSerial = { parseLine, applyFrame, state };

bootstrap();
autoConnect();

navigator.serial?.addEventListener('disconnect', (event) => {
  if (port && event.target === port) {
    notify('Serial device unplugged', 'error');
    disconnect();
  }
});
