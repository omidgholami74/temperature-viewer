import { API_LABEL, adminKey, api, setAdminKey } from './api.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const el = {
  api: $('[data-role="api"]'),
  key: $('[data-role="admin-key"]'),
  refresh: $('[data-role="refresh"]'),
  status: $('[data-role="status"]'),
  devices: $('[data-role="devices"]'),
  channelDevice: $('[data-role="channel-device"]'),
  channelEditor: $('[data-role="channel-editor"]'),
  saveChannels: $('[data-role="save-channels"]'),
  modulePanel: $('[data-role="module-panel"]'),
  settingsDevice: $('[data-role="settings-device"]'),
  moduleSource: $('[data-role="module-source"]'),
  sampleMs: $('[data-role="sample-ms"]'),
  heartbeatS: $('[data-role="heartbeat-s"]'),
  noiseSamples: $('[data-role="noise-samples"]'),
  noiseGap: $('[data-role="noise-gap"]'),
  emaAlpha: $('[data-role="ema-alpha"]'),
  saveSettings: $('[data-role="save-settings"]'),
  wifiDevice: $('[data-role="wifi-device"]'),
  wifiSsid: $('[data-role="wifi-ssid"]'),
  wifiPassword: $('[data-role="wifi-password"]'),
  sendWifi: $('[data-role="send-wifi"]'),
  otaDevice: $('[data-role="ota-device"]'),
  otaChannel: $('[data-role="ota-channel"]'),
  bundleInfo: $('[data-role="bundle-info"]'),
  sendOta: $('[data-role="send-ota"]'),
  sendRestart: $('[data-role="send-restart"]'),
  events: $('[data-role="events"]'),
  dashboardPanel: $('[data-role="dashboard-panel"]'),
  dashboardWindow: $('[data-role="dashboard-window"]'),
  dashboardThreshold: $('[data-role="dashboard-threshold"]'),
  dashboardNotice: $('[data-role="dashboard-notice"]'),
  dashboardNoise: $('[data-role="dashboard-noise"]'),
  saveDashboard: $('[data-role="save-dashboard"]'),
  profilePanel: $('[data-role="profile-panel"]'),
  profileDevice: $('[data-role="profile-device"]'),
  profileName: $('[data-role="profile-name"]'),
  profileLocation: $('[data-role="profile-location"]'),
  profileNotes: $('[data-role="profile-notes"]'),
  saveProfile: $('[data-role="save-profile"]'),
  uploadChannel: $('[data-role="upload-channel"]'),
  uploadVersion: $('[data-role="upload-version"]'),
  uploadNotes: $('[data-role="upload-notes"]'),
  uploadFiles: $('[data-role="upload-files"]'),
  uploadList: $('[data-role="upload-list"]'),
  uploadFirmware: $('[data-role="upload-firmware"]'),
  calChannel: $('[data-role="cal-channel"]'),
  calReference: $('[data-role="cal-reference"]'),
  calMeasured: $('[data-role="cal-measured"]'),
  calApply: $('[data-role="cal-apply"]'),
  statOnline: $('[data-role="stat-online"]'),
  statOnlineHint: $('[data-role="stat-online-hint"]'),
  statPending: $('[data-role="stat-pending"]'),
  statBundles: $('[data-role="stat-bundles"]'),
  statOffline: $('[data-role="stat-offline"]'),
};

const DEFAULT_SETTINGS = {
  sample_ms: 400,
  noise_samples: 5,
  noise_gap_us: 6000,
  ema_alpha: 0.35,
  heartbeat_s: 15,
};

const DEFAULT_DASHBOARD = {
  noise_enabled: true,
  noise_threshold: 60,
  default_window_s: 600,
  notice: '',
};

const PRESETS = {
  quiet: { sample_ms: 800, noise_samples: 9, noise_gap_us: 12000, ema_alpha: 0.2, heartbeat_s: 30 },
  balanced: { sample_ms: 400, noise_samples: 5, noise_gap_us: 6000, ema_alpha: 0.35, heartbeat_s: 15 },
  fast: { sample_ms: 200, noise_samples: 3, noise_gap_us: 3000, ema_alpha: 0.6, heartbeat_s: 10 },
};

const state = {
  devices: [],
  events: [],
  bundles: {},
  dashboard: { ...DEFAULT_DASHBOARD },
  onlineAfter: 45000,
  selected: null,
};

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
})[char]);

function age(ms) {
  const seconds = Math.max(0, Math.round(Number(ms) / 1000));

  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function uptime(ms) {
  const minutes = Math.floor(Math.max(0, Number(ms)) / 60000);

  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function clock(ms) {
  try {
    return new Date(Number(ms)).toLocaleString();
  } catch {
    return '—';
  }
}

function status(text, tone = '') {
  el.status.textContent = text;
  el.status.dataset.tone = tone;
}

function fillSelect(select, value) {
  const current = value ?? select.value;
  select.innerHTML = state.devices.length === 0
    ? '<option value="">No boards yet</option>'
    : state.devices.map((device) => (
      `<option value="${escape(device.id)}">${escape(device.display_name || device.name)} · ${escape(device.id)}</option>`
    )).join('');

  if (state.devices.some((device) => device.id === current)) {
    select.value = current;
  }
}

function deviceById(id) {
  return state.devices.find((device) => device.id === id) ?? null;
}

function settingsFor(device) {
  const stored = device?.settings && typeof device.settings === 'object' ? device.settings : {};
  const channels = Array.isArray(stored.channels) && stored.channels.length > 0
    ? stored.channels
    : (device?.channels ?? []).map((channel) => ({
      channel: channel.channel,
      enabled: channel.enabled,
      offset: channel.offset ?? 0,
      offset_ranges: channel.offset_ranges ?? [],
    }));

  return { ...DEFAULT_SETTINGS, ...stored, channels };
}

function editingInside(panel) {
  const active = document.activeElement;
  return Boolean(
    panel
    && active
    && panel.contains(active)
    && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName),
  );
}

function renderDashboardEditor() {
  if (!el.dashboardPanel || editingInside(el.dashboardPanel)) {
    return;
  }

  const settings = { ...DEFAULT_DASHBOARD, ...(state.dashboard ?? {}) };
  el.dashboardWindow.value = String(settings.default_window_s);
  el.dashboardThreshold.value = settings.noise_threshold;
  el.dashboardNotice.value = settings.notice ?? '';
  el.dashboardNoise.checked = settings.noise_enabled !== false;
}

function renderProfileEditor(force = false) {
  if (!el.profilePanel || (!force && editingInside(el.profilePanel))) {
    return;
  }

  const device = deviceById(el.profileDevice.value);

  if (!device) {
    el.profileName.value = '';
    el.profileLocation.value = '';
    el.profileNotes.value = '';
    return;
  }

  el.profileName.value = device.display_name ?? '';
  el.profileLocation.value = device.location ?? '';
  el.profileNotes.value = device.notes ?? '';
}

function renderModuleEditor() {
  const active = document.activeElement;

  if (active && active.tagName === 'INPUT' && el.modulePanel.contains(active)) {
    return;
  }

  const device = deviceById(el.settingsDevice.value);
  el.moduleSource.textContent = device?.source || 'not reported';

  const settings = settingsFor(device);
  el.sampleMs.value = settings.sample_ms;
  el.heartbeatS.value = settings.heartbeat_s;
  el.noiseSamples.value = settings.noise_samples;
  el.noiseGap.value = settings.noise_gap_us;
  el.emaAlpha.value = settings.ema_alpha;
  el.modulePanel.dataset.ready = device ? '1' : '';
}

function selectedDevice(select) {
  state.selected = select.value;
  renderDevices();
}

function renderDevices() {
  if (state.devices.length === 0) {
    el.devices.innerHTML = '<p class="empty">No board has reported in yet. Flash the firmware with a DEVICE_ID and wait for the first heartbeat.</p>';
    return;
  }

  el.devices.innerHTML = state.devices.map((device) => {
    const online = device.age_ms <= state.onlineAfter;
    const settings = settingsFor(device);
    const profileName = device.display_name || device.name;
    const channels = device.channels.length > 0
      ? device.channels.map((channel) => (
        `<span class="tag ${channel.present ? 'tag--present' : ''} ${channel.enabled ? '' : 'tag--off'}">
           ${channel.channel} · ${escape(channel.label)} · ${Number(channel.offset ?? 0).toFixed(1)} °C
         </span>`
      )).join('')
      : '<span class="tag">no channels reported</span>';
    const last = device.last_command
      ? `${escape(device.last_command.kind)} · ${escape(device.last_command.status)}`
      : 'no commands yet';
    const active = device.id === state.selected ? ' style="border-color: var(--line-strong)"' : '';

    return `
      <article class="panel device"${active}>
        <div class="device__head">
          <div>
            <p class="device__name">${escape(profileName)}</p>
            <p class="device__id">${escape(device.id)}${device.location ? ` · ${escape(device.location)}` : ''}</p>
          </div>
          <span class="device__state">
            <span class="dot ${online ? 'dot--online' : ''}" aria-hidden="true"></span>
            ${online ? 'online' : 'offline'}
          </span>
        </div>
        <dl class="device__facts">
          <div><dt>Last seen</dt><dd>${age(device.age_ms)}</dd></div>
          <div><dt>Firmware</dt><dd>${escape(device.firmware || 'usb')}</dd></div>
          <div><dt>Source</dt><dd>${escape(device.source || '—')}</dd></div>
          <div><dt>Frame / filter</dt><dd>${settings.sample_ms} ms · ${settings.noise_samples}× · α ${settings.ema_alpha}</dd></div>
          <div><dt>IP</dt><dd>${escape(device.ip || '—')}</dd></div>
          <div><dt>Signal</dt><dd>${device.rssi === null ? '—' : `${device.rssi} dBm`}</dd></div>
          <div><dt>Uptime</dt><dd>${uptime(device.uptime_ms)}</dd></div>
          <div><dt>Sent / buffer</dt><dd>${device.sent} / ${device.pending_samples}</dd></div>
          <div><dt>Last command</dt><dd>${last}</dd></div>
          <div><dt>Queue</dt><dd>${device.pending_commands} pending</dd></div>
        </dl>
        <div class="device__channels">${channels}</div>
        <div class="device__actions">
          <button class="btn" type="button" data-action="furnaces" data-id="${escape(device.id)}">Furnaces</button>
          <button class="btn" type="button" data-action="wifi" data-id="${escape(device.id)}">Wi-Fi</button>
          <button class="btn" type="button" data-action="module" data-id="${escape(device.id)}">Module</button>
          <button class="btn" type="button" data-action="firmware" data-id="${escape(device.id)}">Firmware</button>
          <button class="btn" type="button" data-action="profile" data-id="${escape(device.id)}">Profile</button>
        </div>
      </article>
    `;
  }).join('');
}

function rangeRow(channel, index, band = {}) {
  return `
    <div class="range-line" data-range-row>
      <input type="number" step="1" placeholder="min" value="${band.min ?? ''}" data-range-min aria-label="Furnace ${channel} band minimum">
      <span class="range-line__sep">→</span>
      <input type="number" step="1" placeholder="max" value="${band.max ?? ''}" data-range-max aria-label="Furnace ${channel} band maximum">
      <span class="range-line__unit">°C</span>
      <input type="number" step="0.1" placeholder="offset" value="${band.offset ?? 0}" data-range-offset aria-label="Furnace ${channel} band offset">
      <button type="button" class="range-line__remove" data-remove-range="${channel}" aria-label="Remove band">×</button>
    </div>
  `;
}

function renderChannelEditor() {
  const active = document.activeElement;

  if (active && active.tagName === 'INPUT' && el.channelEditor.contains(active)) {
    return;
  }

  // The panel refreshes every five seconds. Keep a calibration band section
  // open while the operator is reading it instead of snapping it shut.
  const openDetails = new Set(
    $$('.range-editor[open]', el.channelEditor).map((node) => node.dataset.channelDetails),
  );
  const device = deviceById(el.channelDevice.value);

  if (!device) {
    el.channelEditor.innerHTML = '<p class="admin-hint">Select a board.</p>';
    return;
  }

  const settings = settingsFor(device);

  el.channelEditor.innerHTML = [1, 2, 3, 4].map((number) => {
    const channel = device.channels.find((entry) => entry.channel === number)
      ?? { channel: number, label: `Furnace ${number}`, present: false, enabled: number <= 3, offset: 0 };
    const stored = settings.channels.find((entry) => entry.channel === number);
    const offset = stored?.offset ?? channel.offset ?? 0;
    const ranges = Array.isArray(stored?.offset_ranges)
      ? stored.offset_ranges
      : (Array.isArray(channel.offset_ranges) ? channel.offset_ranges : []);

    return `
      <div class="channel-item">
        <label class="channel-row">
          <input type="checkbox" data-channel="${number}" ${channel.enabled ? 'checked' : ''} aria-label="Enable furnace ${number}">
          <input type="text" maxlength="40" value="${escape(channel.label)}" data-label="${number}" aria-label="Label for furnace ${number}">
          <input type="number" min="-200" max="200" step="0.1" value="${Number(offset).toFixed(1)}" data-offset="${number}" aria-label="Base calibration offset for furnace ${number}">
          <span class="channel-row__meta">${channel.present ? 'probe' : 'no probe'}</span>
        </label>
        <details class="range-editor" data-channel-details="${number}" ${openDetails.has(String(number)) ? 'open' : ''}>
          <summary>
            Temperature bands
            <span>${ranges.length ? `${ranges.length} configured` : 'base offset everywhere'}</span>
          </summary>
          <p class="range-editor__hint">Each band replaces the base offset between its min and max (max exclusive). Bands must not overlap.</p>
          <div class="range-list" data-range-list="${number}">
            ${ranges.length ? ranges.map((band, index) => rangeRow(number, index, band)).join('') : '<p class="range-empty">No temperature bands. The base offset is used at every temperature.</p>'}
          </div>
          <button class="btn btn--compact range-add" type="button" data-add-range="${number}">+ Add temperature band</button>
        </details>
      </div>
    `;
  }).join('');
}

function collectRanges(channel) {
  const list = $(
    `[data-range-list="${channel}"]`,
    el.channelEditor,
  );

  if (!list) {
    return [];
  }

  return $$('.range-line', list).map((row) => ({
    min: Number($('[data-range-min]', row).value),
    max: Number($('[data-range-max]', row).value),
    offset: Number($('[data-range-offset]', row).value),
  }));
}

function updateRangeSummary(list) {
  const details = list?.closest('.range-editor');
  const summary = details ? $('summary span', details) : null;

  if (!summary) {
    return;
  }

  const count = $$('.range-line', list).length;
  summary.textContent = count ? `${count} configured` : 'base offset everywhere';
}

function validRanges(ranges) {
  if (ranges.length > 12) {
    return 'A furnace can have at most 12 temperature bands.';
  }

  const sorted = [...ranges].sort((left, right) => left.min - right.min);

  for (const [index, band] of sorted.entries()) {
    if (![band.min, band.max, band.offset].every(Number.isFinite)) {
      return 'Every temperature band needs finite min, max and offset numbers.';
    }

    if (band.min < -270 || band.max > 1800 || band.min >= band.max) {
      return 'A band must satisfy -270 ≤ min < max ≤ 1800 °C.';
    }

    if (band.offset < -200 || band.offset > 200) {
      return 'A band offset must be between -200 and 200 °C.';
    }

    if (index > 0 && band.min < sorted[index - 1].max) {
      return 'Temperature bands must not overlap.';
    }
  }

  return '';
}

function renderBundles() {
  const current = el.otaChannel.value;
  const channels = Object.keys(state.bundles);

  el.otaChannel.innerHTML = channels.length === 0
    ? '<option value="">nothing published</option>'
    : channels.map((channel) => (
      `<option value="${escape(channel)}">${escape(channel)} · ${escape(state.bundles[channel].version)}</option>`
    )).join('');

  if (state.bundles[current]) {
    el.otaChannel.value = current;
  }

  const chosen = el.otaChannel.value;
  const bundle = state.bundles[chosen];
  const details = bundle
    ? [
      `${bundle.files} source files`,
      bundle.version,
      bundle.created_at,
      bundle.notes ? `note: ${bundle.notes}` : '',
    ].filter(Boolean).map(escape).join(' · ')
    : '';
  el.bundleInfo.innerHTML = bundle
    ? `${details}<br><span class="bundle-files">${(bundle.names ?? []).map(escape).join(' · ') || 'manifest only'}</span>`
    : 'No bundle on the server yet. Upload one above or run tools/publish.py and deploy the folder.';
}

function renderEvents() {
  if (state.events.length === 0) {
    el.events.innerHTML = '<tr><td colspan="4">No events yet.</td></tr>';
    return;
  }

  el.events.innerHTML = state.events.map((event) => `
    <tr>
      <td>${clock(event.at_ms)}</td>
      <td>${escape(event.device_id)}</td>
      <td>${escape(event.kind)}</td>
      <td>${escape(event.detail || '—')}</td>
    </tr>
  `).join('');
}

function renderOverview() {
  const online = state.devices.filter((device) => device.age_ms <= state.onlineAfter).length;
  const pending = state.devices.reduce(
    (total, device) => total + (Number(device.pending_commands) || 0),
    0,
  );

  el.statOnline.textContent = String(online);
  el.statOnlineHint.textContent = state.devices.length === 0
    ? 'waiting for boards'
    : `${state.devices.length} board${state.devices.length === 1 ? '' : 's'} total`;
  el.statPending.textContent = String(pending);
  el.statBundles.textContent = String(Object.keys(state.bundles).length);
  el.statOffline.textContent = String(Math.max(0, state.devices.length - online));
}

function render() {
  fillSelect(el.channelDevice);
  fillSelect(el.wifiDevice);
  fillSelect(el.otaDevice);
  fillSelect(el.settingsDevice);
  fillSelect(el.profileDevice);
  renderOverview();
  renderDevices();
  renderDashboardEditor();
  renderProfileEditor();
  renderModuleEditor();
  renderChannelEditor();
  renderBundles();
  renderEvents();
}

async function refresh({ quiet = false } = {}) {
  if (!quiet) {
    status('Loading…');
  }

  try {
    const [devices, events, dashboard] = await Promise.all([
      api.devices(),
      api.events(40),
      // A missing public-settings endpoint must not hide the device list.
      api.dashboard().catch(() => null),
    ]);
    state.devices = Array.isArray(devices.devices) ? devices.devices : [];
    state.bundles = devices.bundles && typeof devices.bundles === 'object' ? devices.bundles : {};
    state.dashboard = dashboard?.settings && typeof dashboard.settings === 'object'
      ? dashboard.settings
      : { ...DEFAULT_DASHBOARD };
    state.onlineAfter = Number(devices.online_after_ms) || 45000;
    state.events = Array.isArray(events.events) ? events.events : [];
    render();
    status(`Updated ${new Date().toLocaleTimeString()} · ${state.devices.length} board(s)`, 'ok');
  } catch (error) {
    status(`${error.message}. Check the API origin and FURNACE_ALLOWED_ORIGINS.`, 'error');
  }
}

async function queueCommand(kind, extra = {}, deviceId = '') {
  const device = deviceById(deviceId || state.selected || el.otaDevice.value);

  if (!device) {
    status('Select a board first.', 'error');
    return;
  }

  try {
    await api.command(device.id, kind, extra);
    status(`Queued ${kind} for ${device.id}. It is delivered on the next heartbeat.`, 'ok');
    await refresh({ quiet: true });
  } catch (error) {
    status(error.status === 401 ? 'Set the admin key (X-Admin-Key) to manage boards.' : error.message, 'error');
  }
}

async function saveChannels() {
  const device = deviceById(el.channelDevice.value);

  if (!device) {
    status('Select a board first.', 'error');
    return;
  }

  let channels;

  try {
    channels = $$('[data-channel]', el.channelEditor).map((checkbox) => {
      const channel = Number(checkbox.dataset.channel);
      const offsetRanges = collectRanges(channel);
      const error = validRanges(offsetRanges);

      if (error) {
        throw new Error(`Furnace ${channel}: ${error}`);
      }

      return {
        channel,
        enabled: checkbox.checked,
        label: $(`[data-label="${channel}"]`, el.channelEditor).value.trim(),
        offset: Number($(`[data-offset="${channel}"]`, el.channelEditor).value) || 0,
        offset_ranges: offsetRanges,
      };
    });
  } catch (error) {
    status(error.message, 'error');
    return;
  }

  try {
    await api.saveChannels(device.id, channels);
    status('Furnace labels, enabled state and calibration bands queued for the board.', 'ok');
    await refresh({ quiet: true });
  } catch (error) {
    status(error.status === 401 ? 'Set the admin key (X-Admin-Key) to manage boards.' : error.message, 'error');
  }
}

async function saveDashboard() {
  const threshold = Number(el.dashboardThreshold.value);

  if (!Number.isInteger(threshold) || threshold < 5 || threshold > 95) {
    status('The drop threshold must be a whole number from 5 to 95.', 'error');
    return;
  }

  try {
    const payload = await api.saveDashboard({
      noise_enabled: el.dashboardNoise.checked,
      noise_threshold: threshold,
      default_window_s: Number(el.dashboardWindow.value),
      notice: el.dashboardNotice.value.trim(),
    });
    state.dashboard = payload.settings;
    status('Public dashboard settings saved. Every visitor sees them on the next load.', 'ok');
  } catch (error) {
    status(error.status === 401 ? 'Set the admin key (X-Admin-Key) to save public settings.' : error.message, 'error');
  }
}

async function saveProfile() {
  const device = deviceById(el.profileDevice.value);

  if (!device) {
    status('Select a board first.', 'error');
    return;
  }

  try {
    await api.saveProfile(device.id, {
      display_name: el.profileName.value.trim(),
      location: el.profileLocation.value.trim(),
      notes: el.profileNotes.value.trim(),
    });
    status('Board profile saved.', 'ok');
    await refresh({ quiet: true });
  } catch (error) {
    status(error.status === 401 ? 'Set the admin key (X-Admin-Key) to save the profile.' : error.message, 'error');
  }
}

async function uploadSourceBundle() {
  const files = Array.from(el.uploadFiles.files ?? []);
  const channel = el.uploadChannel.value.trim();

  if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(channel)) {
    status('Bundle name must contain lowercase letters, digits and dashes.', 'error');
    return;
  }

  if (files.length === 0) {
    status('Select the .py files for the bundle.', 'error');
    return;
  }

  if (files.some((file) => !file.name.endsWith('.py'))) {
    status('Only .py source files can be uploaded.', 'error');
    return;
  }

  const core = new Set([
    'boot.py', 'wifi.py', 'ingest.py', 'supervisor.py', 'ota.py', 'device.py', 'settings.py', 'config.py',
  ]);
  const protectedFile = files.find((file) => core.has(file.name));

  if (protectedFile) {
    status(`${protectedFile.name} is protected network/boot core. Upload only application files.`, 'error');
    return;
  }

  const form = new FormData();
  form.append('channel', channel);
  form.append('version', el.uploadVersion.value.trim());
  form.append('notes', el.uploadNotes.value.trim());

  for (const file of files) {
    form.append('files[]', file, file.name);
  }

  el.uploadFirmware.disabled = true;
  status('Uploading and verifying the source bundle…');

  try {
    const payload = await api.uploadFirmware(form);
    status(`Uploaded ${payload.bundle.channel} ${payload.bundle.version} (${payload.bundle.files} files). Queue an OTA update when ready.`, 'ok');
    el.uploadFiles.value = '';
    el.uploadVersion.value = '';
    el.uploadNotes.value = '';
    await refresh({ quiet: true });
  } catch (error) {
    status(
      error.status === 503
        ? 'Source upload is disabled until FURNACE_ADMIN_KEY is set on the API server.'
        : error.message,
      'error',
    );
  } finally {
    el.uploadFirmware.disabled = false;
  }
}

function applyCalibration() {
  const channel = Number(el.calChannel.value);
  const reference = Number(el.calReference.value);
  const measured = Number(el.calMeasured.value);
  const offset = $(`[data-offset="${channel}"]`, el.channelEditor);

  if (!offset) {
    status('Select a board and open its furnaces first.', 'error');
    return;
  }

  if (!Number.isFinite(reference) || !Number.isFinite(measured)) {
    status('Enter both the reference and the displayed temperature.', 'error');
    return;
  }

  const current = Number(offset.value) || 0;
  const next = Math.max(-200, Math.min(200, current + reference - measured));
  offset.value = next.toFixed(1);
  status(`Furnace ${channel} offset set to ${offset.value} °C. Press Save furnaces to queue it.`, 'ok');
}

async function saveSettings() {
  const device = deviceById(el.settingsDevice.value);

  if (!device) {
    status('Select a board first.', 'error');
    return;
  }

  const settings = {
    sample_ms: Number(el.sampleMs.value),
    heartbeat_s: Number(el.heartbeatS.value),
    noise_samples: Number(el.noiseSamples.value),
    noise_gap_us: Number(el.noiseGap.value),
    ema_alpha: Number(el.emaAlpha.value),
  };

  if (Object.values(settings).some((value) => !Number.isFinite(value))) {
    status('Every module field must be a number.', 'error');
    return;
  }

  try {
    await api.saveSettings(device.id, settings);
    status('Module settings queued; the board applies them on the next heartbeat.', 'ok');
    await refresh({ quiet: true });
  } catch (error) {
    status(error.status === 401 ? 'Set the admin key (X-Admin-Key) to manage boards.' : error.message, 'error');
  }
}

function wire() {
  el.api.textContent = API_LABEL;
  el.key.value = adminKey();

  el.key.addEventListener('change', () => {
    setAdminKey(el.key.value.trim());
    status(el.key.value.trim() ? 'Admin key saved for this tab.' : 'Admin key cleared.');
  });

  el.refresh.addEventListener('click', () => refresh());

  el.devices.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');

    if (!button) {
      return;
    }

    const device = deviceById(button.dataset.id);

    if (!device) {
      return;
    }

    state.selected = device.id;

    if (button.dataset.action === 'furnaces') {
      el.channelDevice.value = device.id;
      renderChannelEditor();
      el.channelDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (button.dataset.action === 'module') {
      el.settingsDevice.value = device.id;
      renderModuleEditor();
      el.settingsDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (button.dataset.action === 'wifi') {
      el.wifiDevice.value = device.id;
      el.wifiSsid.value = '';
      el.wifiPassword.value = '';
      el.wifiSsid.focus();
      el.wifiDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (button.dataset.action === 'firmware') {
      el.otaDevice.value = device.id;
      el.otaDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (button.dataset.action === 'profile') {
      el.profileDevice.value = device.id;
      renderProfileEditor(true);
      el.profilePanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    renderDevices();
  });

  el.channelDevice.addEventListener('change', renderChannelEditor);
  el.saveChannels.addEventListener('click', saveChannels);
  el.settingsDevice.addEventListener('change', renderModuleEditor);
  el.saveSettings.addEventListener('click', saveSettings);
  el.saveDashboard.addEventListener('click', saveDashboard);
  el.profileDevice.addEventListener('change', () => renderProfileEditor(true));
  el.saveProfile.addEventListener('click', saveProfile);
  el.uploadFirmware.addEventListener('click', uploadSourceBundle);
  el.uploadFiles.addEventListener('change', () => {
    const names = Array.from(el.uploadFiles.files ?? []).map((file) => file.name);
    el.uploadList.innerHTML = names.length
      ? names.map((name) => `<code>${escape(name)}</code>`).join(' · ')
      : 'Choose <code>main.py</code>, a driver or any helper <code>.py</code> module.';
  });
  el.channelEditor.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove-range]');

    if (remove) {
      const list = remove.closest('[data-range-list]');
      remove.closest('.range-line')?.remove();
      updateRangeSummary(list);
      return;
    }

    const add = event.target.closest('[data-add-range]');

    if (!add) {
      return;
    }

    const channel = add.dataset.addRange;
    const list = $(`[data-range-list="${channel}"]`, el.channelEditor);

    if (!list) {
      return;
    }

    $('.range-empty', list)?.remove();
    list.insertAdjacentHTML('beforeend', rangeRow(channel, list.children.length));
    updateRangeSummary(list);
  });
  el.calApply.addEventListener('click', applyCalibration);

  for (const button of $$('[data-preset]')) {
    button.addEventListener('click', () => {
      const preset = PRESETS[button.dataset.preset];

      if (!preset) {
        return;
      }

      el.sampleMs.value = preset.sample_ms;
      el.heartbeatS.value = preset.heartbeat_s;
      el.noiseSamples.value = preset.noise_samples;
      el.noiseGap.value = preset.noise_gap_us;
      el.emaAlpha.value = preset.ema_alpha;
      status(`${button.textContent} preset filled. Review it, then press Save module settings.`, 'ok');
    });
  }

  el.sendWifi.addEventListener('click', () => {
    const ssid = el.wifiSsid.value.trim();
    const password = el.wifiPassword.value;

    if (!ssid) {
      status('SSID is required.', 'error');
      return;
    }

    if (!window.confirm(`Queue new Wi-Fi "${ssid}" for ${el.wifiDevice.value}? The board reboots when it applies it.`)) {
      return;
    }

    queueCommand('wifi', { ssid, password }, el.wifiDevice.value);
    el.wifiPassword.value = '';
  });

  el.otaDevice.addEventListener('change', () => {
    state.selected = el.otaDevice.value;
    renderDevices();
  });

  el.otaChannel.addEventListener('change', renderBundles);

  el.sendOta.addEventListener('click', () => {
    if (!el.otaChannel.value) {
      status('Publish a bundle first.', 'error');
      return;
    }

    queueCommand('ota', { channel: el.otaChannel.value }, el.otaDevice.value);
  });

  el.sendRestart.addEventListener('click', () => {
    const device = deviceById(el.otaDevice.value);

    if (!device) {
      return;
    }

    if (window.confirm(`Restart ${device.name} (${device.id})?`)) {
      queueCommand('restart', {}, device.id);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refresh({ quiet: true });
    }
  });
}

wire();
refresh();
window.setInterval(() => {
  if (!document.hidden) {
    refresh({ quiet: true });
  }
}, 5000);
