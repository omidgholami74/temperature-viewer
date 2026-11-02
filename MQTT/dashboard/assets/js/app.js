/**
 * Furnace MQTT dashboard.
 *
 * Subscribes to the broker over WebSocket, keeps a short in-memory history per
 * device and renders it. The chart holds each channel's last good value, so a
 * faulted frame does not cut a line: the curve continues and the card is
 * marked *held*.
 */
(() => {
  'use strict';

  const config = window.FURNACE_MQTT_CONFIG ?? {};
  const topicBase = ['furnace', config.site || '+'];
  const CHANNELS = [0, 1, 2, 3];
  const THEME_KEY = 'furnace-mqtt.theme';
  const MAX_LOG = 9;
  const RATE_WINDOW_MS = 5000;

  const el = {
    connection: document.querySelector('[data-role="connection"]'),
    device: document.querySelector('[data-role="device"]'),
    theme: document.querySelector('[data-role="theme"]'),
    frames: document.querySelector('[data-role="frames"]'),
    framesMeta: document.querySelector('[data-role="frames-meta"]'),
    rate: document.querySelector('[data-role="rate"]'),
    stored: document.querySelector('[data-role="stored"]'),
    storedMeta: document.querySelector('[data-role="stored-meta"]'),
    loss: document.querySelector('[data-role="loss"]'),
    cards: Array.from(document.querySelectorAll('.furnace-card')).map((root) => ({
      root,
      value: root.querySelector('[data-role="value"]'),
      state: root.querySelector('[data-role="state"]'),
      age: root.querySelector('[data-role="age"]'),
    })),
    legend: document.querySelector('[data-role="legend"]'),
    canvas: document.querySelector('#chart'),
    empty: document.querySelector('[data-role="empty"]'),
    tooltip: document.querySelector('[data-role="tooltip"]'),
    deviceCount: document.querySelector('[data-role="device-count"]'),
    devices: document.querySelector('[data-role="devices"]'),
    devicesEmpty: document.querySelector('[data-role="devices-empty"]'),
    broker: document.querySelector('[data-role="broker"]'),
    log: document.querySelector('[data-role="log"]'),
  };

  const state = {
    client: null,
    connected: false,
    device: null,
    devices: new Map(),
    series: new Map(),
    lastGood: new Map(),
    hidden: new Set(),
    frameCount: 0,
    frameTimes: [],
    stored: 0,
    forwarded: 0,
    dropped: 0,
    duplicates: 0,
    windowMs: (Number(config.windowSeconds) || 600) * 1000,
    hover: null,
    dirty: true,
    theme: localStorage.getItem(THEME_KEY) || 'dark',
  };

  const limit = Number(config.pointsPerChannel) || 7200;

  /* --- helpers ---------------------------------------------------------- */

  const fmtTemp = (value) => (value === null || !Number.isFinite(value) ? '--.-' : value.toFixed(1));

  const fmtAge = (ms) => {
    if (!Number.isFinite(ms) || ms < 0) return 'no reading';
    if (ms < 1500) return 'just now';
    if (ms < 60000) return `${(ms / 1000).toFixed(1)} s ago`;
    if (ms < 3600000) return `${Math.round(ms / 60000)} min ago`;
    return `${(ms / 3600000).toFixed(1)} h ago`;
  };

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function deviceEntry(name) {
    if (!state.devices.has(name)) {
      state.devices.set(name, {
        name,
        frames: 0,
        faults: 0,
        online: null,
        lastSeen: 0,
        lastT: 0,
      });
      state.series.set(name, CHANNELS.map(() => []));
      state.lastGood.set(name, CHANNELS.map(() => null));
    }
    return state.devices.get(name);
  }

  /* --- theme ------------------------------------------------------------ */

  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    state.dirty = true;
  }

  /* --- connection ------------------------------------------------------- */

  function setConnection(kind, label) {
    el.connection.className = `chip chip--${kind}`;
    el.connection.textContent = label;
  }

  function connect() {
    if (!window.mqtt) {
      setConnection('offline', 'MQTT library missing');
      log('error', 'assets/js/mqtt.min.js', 'library failed to load');
      return;
    }

    setConnection('delayed', 'Connecting');
    el.broker.textContent = config.url?.replace(/^wss?:\/\//, '') || 'broker';

    const client = window.mqtt.connect(config.url, {
      clientId: config.clientId,
      username: config.username || undefined,
      password: config.password || undefined,
      clean: true,
      reconnectPeriod: 3000,
      connectTimeout: 8000,
      keepalive: 30,
    });

    state.client = client;
    state.connected = false;

    client.on('connect', () => {
      state.connected = true;
      setConnection('live', 'Connected');
      for (const topic of config.topics ?? ['furnace/+/+/samples']) {
        client.subscribe(topic, { qos: 1 });
      }
      log('info', 'client', 'subscribed to the broker');
    });

    client.on('reconnect', () => setConnection('delayed', 'Reconnecting'));
    client.on('offline', () => {
      state.connected = false;
      setConnection('offline', 'Offline');
    });
    client.on('error', (error) => {
      setConnection('offline', 'Broker error');
      log('error', 'client', error.message || 'broker error');
    });
    client.on('message', onMessage);
  }

  /* --- messages --------------------------------------------------------- */

  function onMessage(topic, payload) {
    const parts = topic.split('/');

    if (parts[0] !== 'furnace') return;
    if (config.site && config.site !== '*' && parts[1] !== config.site) return;

    // furnace/<site>/_bridge/status is the bridge's retained health topic.
    if (parts[3] === 'status' && parts[2] === '_bridge') {
      onBridgeStatus(payload);
      return;
    }

    const device = parts[2];
    if (!device || device === '_bridge') return;

    if (parts[3] === 'samples') {
      onSamples(device, payload, topic);
      return;
    }

    if (parts[3] === 'state') {
      onDeviceState(device, payload);
    }
  }

  function onBridgeStatus(payload) {
    try {
      const status = JSON.parse(payload.toString());
      state.stored = Number(status.samples) || 0;
      state.forwarded = Number(status.forwarded) || 0;
      state.dropped = Number(status.dropped) || 0;
      state.duplicates = Number(status.duplicates) || 0;
      el.stored.textContent = formatCount(state.stored);
      el.storedMeta.textContent = `${formatCount(state.forwarded)} batches forwarded`;
      el.loss.textContent = `${formatCount(state.dropped)} / ${formatCount(state.duplicates)}`;
      log('info', 'bridge', `${status.online ? 'online' : 'offline'} · ${state.dropped} dropped`);
    } catch {
      log('error', 'bridge', 'malformed status payload');
    }
  }

  function onDeviceState(device, payload) {
    try {
      const status = JSON.parse(payload.toString());
      const entry = deviceEntry(device);
      entry.online = status.online === true;
      renderDevices();
      log('info', `${device}/state`, status.online ? 'online' : 'offline');
    } catch {
      log('error', `${device}/state`, 'malformed state payload');
    }
  }

  function onSamples(device, payload, topic) {
    let data;

    try {
      data = JSON.parse(payload.toString());
    } catch {
      log('error', topic, 'payload is not JSON');
      return;
    }

    if (!Array.isArray(data.frames) || !data.frames.length) {
      log('error', topic, 'batch has no frames');
      return;
    }

    const entry = deviceEntry(device);
    const series = state.series.get(device);
    const good = state.lastGood.get(device);
    const frames = data.frames;
    const lastU = Number(frames.at(-1)?.[0]) || 0;
    // `at` is the wall clock of the last frame of the batch. Older firmware
    // omits it while NTP is unsynced; anchor on arrival in that case.
    const anchor = Number(data.at) > 0 ? Number(data.at) : Date.now();

    for (const frame of frames) {
      const uptime = Number(frame?.[0]) || 0;
      const temps = frame?.[1] ?? [];
      const faults = frame?.[2] ?? [];
      const t = anchor - (lastU - uptime);

      CHANNELS.forEach((channel) => {
        const fault = Number(faults[channel]) || 0;
        const raw = temps[channel];
        const numeric = Number(raw);
        const valid = !fault && raw !== null && raw !== undefined && Number.isFinite(numeric) && numeric > -999;

        if (valid) {
          good[channel] = numeric;
        }

        // Faulted frames hold the last good value so the line stays continuous.
        const value = valid ? numeric : good[channel];
        series[channel].push({ t, v: value });
      });

      // Fault codes only exist on the newest frame; the table shows the last.
      entry.faults = CHANNELS.reduce((sum, channel) => sum + (Number(faults[channel]) ? 1 : 0), 0);
      state.frameCount += 1;
      state.frameTimes.push(Date.now());
    }

    entry.frames += frames.length;
    entry.lastSeen = Date.now();
    entry.lastT = lastU;

    for (const channel of CHANNELS) {
      if (series[channel].length > limit) {
        series[channel].splice(0, series[channel].length - limit);
      }
    }

    trimFrameTimes();

    renderDeviceOptions();

    if (!state.device) {
      selectDevice(device);
    }

    el.empty.hidden = true;
    state.dirty = true;
    renderCards();
    renderDevices();
    renderMetrics();
  }

  function trimFrameTimes() {
    const cutoff = Date.now() - RATE_WINDOW_MS;
    while (state.frameTimes.length && state.frameTimes[0] < cutoff) {
      state.frameTimes.shift();
    }
  }

  /* --- rendering -------------------------------------------------------- */

  function formatCount(value) {
    return new Intl.NumberFormat('en-US').format(Number(value) || 0);
  }

  function renderMetrics() {
    el.frames.textContent = formatCount(state.frameCount);
    const device = state.device ? state.devices.get(state.device) : null;
    el.framesMeta.textContent = device ? `${device.name} · ${formatCount(device.frames)} frames` : 'waiting for the broker';
    el.rate.textContent = (state.frameTimes.length / (RATE_WINDOW_MS / 1000)).toFixed(1);
  }

  function renderCards() {
    const device = state.device ? state.devices.get(state.device) : null;
    const series = device ? state.series.get(device.name) : null;

    el.cards.forEach((card, channel) => {
      const points = series?.[channel] ?? [];
      const latest = points.at(-1);
      const value = latest?.v ?? null;
      const age = latest ? Date.now() - latest.t : Number.NaN;

      card.value.textContent = fmtTemp(value);
      card.root.classList.toggle('is-held', value !== null && age > 2500);
      card.state.textContent = value === null ? 'offline' : age > 2500 ? 'held' : 'live';
      card.state.className = `chip chip--${value === null ? 'offline' : age > 2500 ? 'delayed' : 'live'}`;
      card.age.textContent = fmtAge(age);
    });
  }

  function renderLegend() {
    el.legend.textContent = '';
    const device = state.device ? state.devices.get(state.device) : null;
    const series = device ? state.series.get(device.name) : null;

    CHANNELS.forEach((channel) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'legend__item button';
      item.setAttribute('aria-pressed', String(!state.hidden.has(channel)));
      item.style.setProperty('--channel', `var(--ch-${channel + 1})`);

      const dot = document.createElement('span');
      dot.className = 'legend__dot';

      const label = document.createElement('span');
      label.textContent = config.channels?.[channel]?.short ?? `F${channel + 1}`;

      const value = document.createElement('span');
      value.className = 'legend__value';
      value.textContent = fmtTemp(series?.[channel]?.at(-1)?.v ?? null);

      item.append(dot, label, value);
      item.addEventListener('click', () => {
        if (state.hidden.has(channel)) state.hidden.delete(channel);
        else state.hidden.add(channel);
        renderLegend();
        state.dirty = true;
      });

      el.legend.append(item);
    });
  }

  function relativeAge(ms) {
    if (!ms) return '—';
    const delta = Date.now() - ms;
    if (delta < 2000) return 'live';
    return fmtAge(delta);
  }

  function renderDevices() {
    if (!state.devices.size) {
      el.devicesEmpty.hidden = false;
      el.devices.textContent = '';
      el.deviceCount.textContent = '0 online';
      return;
    }

    el.devicesEmpty.hidden = true;
    el.devices.textContent = '';

    const cutoff = Date.now() - 15000;
    let online = 0;

    for (const device of state.devices.values()) {
      const live = device.online !== false && device.lastSeen > cutoff;
      if (live) online += 1;

      // Device names come from MQTT topics, which are not trusted input: build
      // the cells with textContent instead of interpolating markup.
      const row = document.createElement('tr');
      const cells = [
        { text: device.name },
        { chip: live ? 'live' : 'offline', label: live ? 'online' : 'offline' },
        { text: formatCount(device.frames) },
        { text: relativeAge(device.lastSeen) },
        { text: String(device.faults) },
      ].map((cell) => {
        const td = document.createElement('td');
        td.className = 'table__mono';

        if (cell.chip) {
          const chip = document.createElement('span');
          chip.className = `chip chip--${cell.chip}`;
          chip.textContent = cell.label;
          td.append(chip);
        } else {
          td.textContent = cell.text;
        }

        return td;
      });
      row.append(...cells);
      row.addEventListener('click', () => selectDevice(device.name));
      row.style.cursor = 'pointer';
      el.devices.append(row);
    }

    el.deviceCount.textContent = `${online} online`;
  }

  function selectDevice(name) {
    if (!state.devices.has(name)) return;
    state.device = name;
    el.device.value = name;
    renderCards();
    renderLegend();
    renderChart();
  }

  function renderDeviceOptions() {
    const names = Array.from(state.devices.keys()).sort();

    if (el.device.options.length === names.length && names.every((name, index) => el.device.options[index].value === name)) {
      return;
    }

    el.device.textContent = '';

    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      el.device.append(option);
    }

    el.device.disabled = names.length === 0;

    if (state.device) {
      el.device.value = state.device;
    }
  }

  function log(kind, topic, text) {
    const item = document.createElement('li');
    item.className = 'log__item';

    const time = document.createElement('span');
    time.className = 'log__time';
    time.textContent = new Date().toLocaleTimeString('en-GB', { hour12: false });

    const body = document.createElement('span');
    const label = document.createElement('span');
    label.className = 'log__topic';
    label.textContent = topic;
    const message = document.createElement('span');
    message.textContent = text;
    message.style.marginLeft = '8px';
    message.style.color = kind === 'error' ? 'var(--danger)' : 'var(--text-2)';
    body.append(label, message);
    item.append(time, body);
    el.log.prepend(item);

    while (el.log.children.length > MAX_LOG) {
      el.log.lastElementChild.remove();
    }
  }

  /* --- chart ------------------------------------------------------------ */

  const chart = {
    canvas: el.canvas,
    ctx: el.canvas.getContext('2d'),
    width: 0,
    height: 0,
    points: [],
    plot: { left: 48, right: 14, top: 14, bottom: 26 },
  };

  const px = () => window.devicePixelRatio || 1;

  function resizeChart() {
    const ratio = px();
    const width = chart.canvas.clientWidth;
    const height = chart.canvas.clientHeight;

    if (chart.width === width && chart.height === height && chart.ratio === ratio) return;

    chart.width = width;
    chart.height = height;
    chart.ratio = ratio;
    chart.canvas.width = Math.round(width * ratio);
    chart.canvas.height = Math.round(height * ratio);
    state.dirty = true;
  }

  function collectVisible(now) {
    const device = state.device ? state.devices.get(state.device) : null;
    const series = device ? state.series.get(device.name) : null;
    const from = now - state.windowMs;
    const visible = [];

    CHANNELS.forEach((channel) => {
      if (state.hidden.has(channel)) {
        visible.push([]);
        return;
      }
      const points = series?.[channel] ?? [];
      let index = points.length;

      while (index > 0 && points[index - 1].t > from) index -= 1;

      visible.push(points.slice(index));
    });

    return visible;
  }

  function niceStep(span, target = 4) {
    const raw = span / target;
    const pow = 10 ** Math.floor(Math.log10(Math.max(raw, 0.001)));
    const normal = raw / pow;
    const step = normal >= 5 ? 5 : normal >= 2 ? 2 : 1;
    return step * pow;
  }

  function renderChart() {
    resizeChart();

    if (!chart.width || !chart.height) return;

    const ctx = chart.ctx;
    const ratio = chart.ratio || 1;
    const { width, height } = chart;
    const style = getComputedStyle(document.documentElement);
    const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    const text = color('--text-3', '#6d8198');
    const grid = color('--line', 'rgba(146,173,202,.12)');

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const { left, right, top, bottom } = chart.plot;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const now = Date.now();
    const from = now - state.windowMs;
    const visible = collectVisible(now);
    chart.points = visible;

    const values = visible.flat().map((point) => point.v).filter((value) => Number.isFinite(value));
    let min = values.length ? Math.min(...values) : 0;
    let max = values.length ? Math.max(...values) : 100;

    if (max - min < 5) {
      const middle = (max + min) / 2;
      min = middle - 2.5;
      max = middle + 2.5;
    }

    const pad = (max - min) * 0.08;
    min -= pad;
    max += pad;

    const x = (t) => left + ((t - from) / (now - from)) * plotWidth;
    const y = (value) => top + plotHeight - ((value - min) / (max - min)) * plotHeight;

    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.lineWidth = 1;
    ctx.strokeStyle = grid;
    ctx.fillStyle = text;

    const step = niceStep(max - min, 5);

    for (let value = Math.ceil(min / step) * step; value <= max; value += step) {
      const py = y(value);
      ctx.beginPath();
      ctx.moveTo(left, py);
      ctx.lineTo(left + plotWidth, py);
      ctx.stroke();
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${Math.round(value)}`, left - 8, py);
    }

    const minutes = state.windowMs / 60000;
    const tickCount = Math.max(3, Math.min(7, Math.round(plotWidth / 120)));

    for (let index = 0; index <= tickCount; index += 1) {
      const t = from + ((now - from) * index) / tickCount;
      const tickX = x(t);
      ctx.strokeStyle = grid;
      ctx.beginPath();
      ctx.moveTo(tickX, top);
      ctx.lineTo(tickX, top + plotHeight);
      ctx.stroke();

      ctx.fillStyle = text;
      ctx.textAlign = index === 0 ? 'left' : index === tickCount ? 'right' : 'center';
      ctx.textBaseline = 'top';
      const ago = Math.round((now - t) / 60000);
      ctx.fillText(ago === 0 ? 'now' : `-${ago}m`, tickX, top + plotHeight + 8);
    }

    // Bridge gaps by construction: every stored point has the last good value,
    // so a faulted frame never inserts a hole in a series.
    visible.forEach((points, channel) => {
      if (points.length < 2) return;

      ctx.beginPath();
      points.forEach((point, index) => {
        const method = index === 0 ? 'moveTo' : 'lineTo';
        ctx[method](x(point.t), y(point.v));
      });
      ctx.strokeStyle = color(`--ch-${channel + 1}`, '#38e0a5');
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.stroke();

      const last = points.at(-1);
      ctx.beginPath();
      ctx.arc(x(last.t), y(last.v), 3, 0, Math.PI * 2);
      ctx.fillStyle = color(`--ch-${channel + 1}`, '#38e0a5');
      ctx.fill();
    });

    if (state.hover !== null && chart.points.some((points) => points.length)) {
      drawHover(ctx, { from, now, left, top, plotHeight, plotWidth, x, y });
    }

    if (minutes < 0.5) {
      ctx.fillStyle = text;
    }
  }

  function valueAt(points, t) {
    let low = 0;
    let high = points.length - 1;
    let found = null;

    while (low <= high) {
      const middle = (low + high) >> 1;
      if (points[middle].t <= t) {
        found = points[middle];
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }

    return found;
  }

  function drawHover(ctx, geometry) {
    const { from, now, left, top, plotHeight, plotWidth, x, y } = geometry;
    const time = from + (state.hover / plotWidth) * (now - from);
    const css = getComputedStyle(document.documentElement);
    const strong = css.getPropertyValue('--line-strong').trim() || 'rgba(146,173,202,.22)';

    ctx.strokeStyle = strong;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x(time), top);
    ctx.lineTo(x(time), top + plotHeight);
    ctx.stroke();

    const rows = [];

    chart.points.forEach((points, channel) => {
      const point = valueAt(points, time);
      if (!point || point.v === null) return;

      ctx.beginPath();
      ctx.arc(x(time), y(point.v), 3.5, 0, Math.PI * 2);
      ctx.fillStyle = css.getPropertyValue(`--ch-${channel + 1}`).trim() || '#38e0a5';
      ctx.fill();

      rows.push({
        color: ctx.fillStyle,
        label: config.channels?.[channel]?.name ?? `Furnace ${channel + 1}`,
        value: point.v,
      });
    });

    if (!rows.length || !el.tooltip) return;

    const stamp = new Date(time).toLocaleTimeString('en-GB', { hour12: false });
    el.tooltip.innerHTML = `
      <div class="chart-tooltip__time">${stamp}</div>
      ${rows.map((row) => `
        <div class="chart-tooltip__row">
          <span><span class="chart-tooltip__dot" style="display:inline-block;--dot:${row.color}"></span>${row.label}</span>
          <b>${row.value.toFixed(1)} °C</b>
        </div>`).join('')}
    `;
    el.tooltip.hidden = false;
    el.tooltip.style.left = `${x(time)}px`;
    el.tooltip.style.top = `${top + 24}px`;
  }

  /* --- loop ------------------------------------------------------------- */

  function frame() {
    if (state.dirty) {
      state.dirty = false;
      renderDeviceOptions();
      renderLegend();
      renderMetrics();
      renderChart();
    }

    if (!document.hidden) {
      renderCards();
      renderDevices();
    }

    requestAnimationFrame(frame);
  }

  /* --- events ----------------------------------------------------------- */

  el.theme.addEventListener('click', () => applyTheme(state.theme === 'dark' ? 'light' : 'dark'));

  el.device.addEventListener('change', () => selectDevice(el.device.value));

  el.canvas.addEventListener('mousemove', (event) => {
    const rect = el.canvas.getBoundingClientRect();
    state.hover = clamp(event.clientX - rect.left - chart.plot.left, 0, chart.width - chart.plot.left - chart.plot.right);
    state.dirty = true;
  });

  el.canvas.addEventListener('mouseleave', () => {
    state.hover = null;
    el.tooltip.hidden = true;
    state.dirty = true;
  });

  window.addEventListener('resize', () => {
    state.dirty = true;
  });

  applyTheme(state.theme);
  renderLegend();
  connect();
  requestAnimationFrame(frame);
})();
