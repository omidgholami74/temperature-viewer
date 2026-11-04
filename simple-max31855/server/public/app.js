const CHANNELS = 3;
const POLL_MS = 150;
const HIDDEN_POLL_MS = 1000;
const RETRY_MS = 1000;
const REQUEST_TIMEOUT_MS = 15_000;
const WINDOW_MS = 60_000;
const MAX_POINTS = 900;
const STALE_MS = 2_000;
const LOST_MS = 8_000;
const SERIES_COLORS = ['#38e0a5', '#49bdf0', '#f3b45a'];
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
  counter: $('[data-role="counter"]'),
  samples: $('[data-role="samples"]'),
  rate: $('[data-role="rate"]'),
  lastUpdate: $('[data-role="last-update"]'),
  empty: $('[data-role="empty"]'),
  canvas: $('#chart'),
  cards: $$('.furnace-card').map((root) => ({
    root,
    value: $('[data-role="value"]', root),
    trend: $('[data-role="trend"]', root),
    delta: $('[data-role="delta"]', root),
    fault: $('[data-role="fault"]', root),
  })),
};

const state = {
  samples: [],
  lastId: 0,
  latest: null,
  total: 0,
  serverOffset: 0,
  error: null,
};

function setText(node, value) {
  if (node && node.textContent !== value) {
    node.textContent = value;
  }
}

function formatCount(value) {
  return Number.isFinite(value)
    ? Math.max(0, Math.round(value)).toLocaleString('en-US')
    : '0';
}

function serverNow() {
  return Date.now() + state.serverOffset;
}

function formatTemp(value) {
  return Number.isFinite(value) ? value.toFixed(1) : '--.-';
}

function formatAge(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)} min`;
}

function formatDelta(value) {
  if (!Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}°`;
}

function faultText(bits) {
  const labels = FAULT_BITS
    .filter(([bit]) => (Number(bits) & bit) === bit)
    .map(([, label]) => label);

  return labels.length ? labels.join(' · ') : 'No reading';
}

function trendFor(delta) {
  if (!Number.isFinite(delta)) return { label: 'Holding', tone: 'live' };
  if (delta > 1.5) return { label: 'Heating', tone: 'heating' };
  if (delta < -1.5) return { label: 'Cooling', tone: 'cooling' };
  return { label: 'Holding', tone: 'live' };
}

function lastValidTemperature(index, cutoff = Number.POSITIVE_INFINITY) {
  for (let position = state.samples.length - 1; position >= 0; position--) {
    const sample = state.samples[position];

    if (Number(sample.at) > cutoff) {
      continue;
    }

    const value = sample.t?.[index];

    if (Number.isFinite(value)) {
      return value;
    }
  }

  return null;
}

function renderCards() {
  const latest = state.latest;

  el.cards.forEach((card, index) => {
    const measured = latest?.t?.[index] ?? null;
    const bits = Number(latest?.f?.[index] ?? 0);
    const held = measured === null ? lastValidTemperature(index) : null;
    const value = measured ?? held;
    const fault = Boolean(latest) && (measured === null || bits > 0);
    const previous = latest && value !== null
      ? lastValidTemperature(index, Number(latest.at) - 10_000)
      : null;
    const delta = value !== null && previous !== null
      ? value - previous
      : Number.NaN;

    setText(card.value, formatTemp(value));
    card.root.classList.toggle('is-fault', fault);
    card.fault.hidden = !fault;
    setText(
      card.fault,
      fault
        ? `${measured === null && value !== null ? 'Held · ' : ''}${faultText(bits)}`
        : '',
    );

    if (!latest) {
      card.trend.className = 'chip';
      setText(card.trend, 'Waiting');
    } else if (fault) {
      card.trend.className = 'chip chip--fault';
      setText(card.trend, 'Fault');
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

function renderStatus() {
  const latest = state.latest;
  const age = latest ? Math.max(0, serverNow() - Number(latest.at)) : null;
  let tone = 'waiting';
  let label = 'Connecting';
  let meta = 'Waiting for the API';

  if (state.error) {
    tone = latest ? 'delayed' : 'offline';
    label = latest ? 'Reconnecting' : 'API offline';
    meta = state.error;
  } else if (latest) {
    if (age > LOST_MS) {
      tone = 'offline';
      label = 'No signal';
    } else if (age > STALE_MS) {
      tone = 'delayed';
      label = 'Delayed';
    } else {
      tone = 'live';
      label = 'Live';
    }

    meta = `Last sample ${formatAge(age)} ago`;
  }

  if (age !== null) {
    setText(el.lastUpdate, formatAge(age));
  } else {
    setText(el.lastUpdate, '—');
  }

  el.status.dataset.state = tone;
  setText(el.statusLabel, label);
  setText(el.statusMeta, meta);
}

function renderStats() {
  setText(el.counter, formatCount(state.total));
  setText(el.samples, formatCount(state.samples.length));

  if (state.samples.length > 1) {
    const first = state.samples[0];
    const last = state.samples[state.samples.length - 1];
    const seconds = (Number(last.at) - Number(first.at)) / 1000;

    if (seconds > 0) {
      setText(el.rate, `${(state.samples.length / seconds).toFixed(1)} /s`);
    } else {
      setText(el.rate, '—');
    }
  } else {
    setText(el.rate, '—');
  }
}

function drawChart() {
  const canvas = el.canvas;
  const rect = canvas.getBoundingClientRect();

  if (rect.width === 0 || rect.height === 0) {
    return;
  }

  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);

  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }

  const context = canvas.getContext('2d');

  if (!context) {
    return;
  }

  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  if (!state.latest) {
    el.empty.textContent = 'Waiting for the first sample…';
    el.empty.hidden = false;
    return;
  }

  const latest = state.latest;
  const end = Math.max(Number(latest.at), serverNow());
  const start = end - WINDOW_MS;
  const visible = state.samples.filter(
    (sample) => Number(sample.at) >= start && Number(sample.at) <= end,
  );
  const series = [];
  const values = [];

  for (let index = 0; index < CHANNELS; index++) {
    let held = lastValidTemperature(index, start);

    if (held === null) {
      for (const sample of visible) {
        if (Number.isFinite(sample.t?.[index])) {
          held = sample.t[index];
          break;
        }
      }
    }

    if (held === null) {
      series.push([]);
      continue;
    }

    const points = [{ at: start, value: held }];

    for (const sample of visible) {
      if (Number.isFinite(sample.t?.[index])) {
        held = sample.t[index];
      }

      points.push({ at: Number(sample.at), value: held });
    }

    if (points[points.length - 1].at < end) {
      points.push({ at: end, value: held });
    }

    series.push(points);

    for (const point of points) {
      values.push(point.value);
    }
  }

  if (values.length === 0) {
    el.empty.textContent = 'No valid readings — holding the last known line…';
    el.empty.hidden = false;
    return;
  }

  el.empty.hidden = true;

  let min = Math.min(...values);
  let max = Math.max(...values);

  if (max - min < 10) {
    const middle = (max + min) / 2;
    min = middle - 5;
    max = middle + 5;
  } else {
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;
  }

  const left = 44;
  const right = 12;
  const top = 12;
  const bottom = 25;
  const plotWidth = Math.max(1, width - left - right);
  const plotHeight = Math.max(1, height - top - bottom);

  const xFor = (at) => left + ((Number(at) - start) / WINDOW_MS) * plotWidth;
  const yFor = (value) => top + ((max - value) / (max - min)) * plotHeight;

  context.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  context.fillStyle = '#718499';
  context.strokeStyle = 'rgba(143, 172, 202, 0.13)';
  context.lineWidth = 1;

  for (let step = 0; step <= 3; step++) {
    const y = top + (plotHeight / 3) * step;
    const value = max - ((max - min) / 3) * step;

    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(width - right, y);
    context.stroke();
    context.fillText(`${Math.round(value)}°`, 2, y + 3);
  }

  context.fillStyle = '#718499';
  context.textAlign = 'center';
  context.fillText('-60 s', left, height - 6);
  context.fillText('-30 s', left + plotWidth / 2, height - 6);
  context.fillText('now', left + plotWidth, height - 6);
  context.textAlign = 'left';

  for (let index = 0; index < CHANNELS; index++) {
    const points = series[index];

    if (points.length === 0) {
      continue;
    }

    context.beginPath();
    context.strokeStyle = SERIES_COLORS[index];
    context.lineWidth = 2;
    context.lineJoin = 'round';
    context.lineCap = 'round';
    context.moveTo(xFor(points[0].at), yFor(points[0].value));

    for (let position = 1; position < points.length; position++) {
      context.lineTo(xFor(points[position].at), yFor(points[position].value));
    }

    context.stroke();
  }
}

function render() {
  renderCards();
  renderStatus();
  renderStats();
  drawChart();
}

function safeRender() {
  try {
    render();
  } catch (error) {
    console.error('Dashboard render failed', error);
  }
}

function ingestSamples(samples) {
  let added = 0;

  for (const sample of samples) {
    if (!sample || !Number.isFinite(Number(sample.at)) || !Array.isArray(sample.t)) {
      continue;
    }

    const id = Number(sample.id);

    if (!Number.isFinite(id) || id <= state.lastId) {
      continue;
    }

    state.samples.push({
      id,
      at: Number(sample.at),
      t: sample.t.slice(0, CHANNELS).map((value) => (Number.isFinite(value) ? value : null)),
      f: Array.isArray(sample.f) ? sample.f : [],
    });
    state.lastId = id;
    added += 1;
  }

  if (state.samples.length > MAX_POINTS) {
    state.samples.splice(0, state.samples.length - MAX_POINTS);
  }

  if (state.samples.length > 0) {
    state.latest = state.samples[state.samples.length - 1];
  }

  return added;
}

function errorText(error) {
  if (error?.name === 'AbortError') {
    return 'Request timed out; retrying';
  }

  return error instanceof Error ? error.message : 'API unreachable';
}

async function poll() {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const since = state.lastId;

  try {
    const response = await fetch(`/api/recent?since=${since}&limit=600`, {
      cache: 'no-store',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`API ${response.status}`);
    }

    const data = await response.json();

    if (Number.isFinite(Number(data.server_time_ms))) {
      state.serverOffset = Number(data.server_time_ms) - Date.now();
    }

    let added = 0;

    if (since > 0 && Number(data.last_id) < since) {
      state.samples = [];
      state.lastId = 0;
      state.latest = null;
      state.total = 0;
    } else if (Array.isArray(data.samples)) {
      added = ingestSamples(data.samples);
    }

    const serverTotal = Number(data.total_samples);

    if (Number.isFinite(serverTotal) && serverTotal >= 0) {
      state.total = serverTotal;
    } else {
      state.total += added;
    }

    state.error = null;
  } catch (error) {
    state.error = errorText(error);
  } finally {
    window.clearTimeout(timeout);
    safeRender();
    window.setTimeout(
      poll,
      state.error
        ? document.hidden ? HIDDEN_POLL_MS : RETRY_MS
        : document.hidden ? HIDDEN_POLL_MS : POLL_MS,
    );
  }
}

window.addEventListener('resize', drawChart);
poll();
