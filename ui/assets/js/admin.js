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
};

const state = {
  devices: [],
  events: [],
  bundles: {},
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
      `<option value="${escape(device.id)}">${escape(device.name)} · ${escape(device.id)}</option>`
    )).join('');

  if (state.devices.some((device) => device.id === current)) {
    select.value = current;
  }
}

function deviceById(id) {
  return state.devices.find((device) => device.id === id) ?? null;
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
    const channels = device.channels.length > 0
      ? device.channels.map((channel) => (
        `<span class="tag ${channel.present ? 'tag--present' : ''} ${channel.enabled ? '' : 'tag--off'}">
           ${channel.channel} · ${escape(channel.label)}
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
            <p class="device__name">${escape(device.name)}</p>
            <p class="device__id">${escape(device.id)}</p>
          </div>
          <span class="device__state">
            <span class="dot ${online ? 'dot--online' : ''}" aria-hidden="true"></span>
            ${online ? 'online' : 'offline'}
          </span>
        </div>
        <dl class="device__facts">
          <div><dt>Last seen</dt><dd>${age(device.age_ms)}</dd></div>
          <div><dt>Firmware</dt><dd>${escape(device.firmware || 'usb')}</dd></div>
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
          <button class="btn" type="button" data-action="firmware" data-id="${escape(device.id)}">Firmware</button>
        </div>
      </article>
    `;
  }).join('');
}

function renderChannelEditor() {
  const device = deviceById(el.channelDevice.value);

  if (!device) {
    el.channelEditor.innerHTML = '<p class="admin-hint">Select a board.</p>';
    return;
  }

  el.channelEditor.innerHTML = [1, 2, 3, 4].map((number) => {
    const channel = device.channels.find((entry) => entry.channel === number)
      ?? { channel: number, label: `Furnace ${number}`, present: false, enabled: number <= 3 };

    return `
      <label class="channel-row">
        <input type="checkbox" data-channel="${number}" ${channel.enabled ? 'checked' : ''} aria-label="Enable furnace ${number}">
        <input type="text" maxlength="40" value="${escape(channel.label)}" data-label="${number}" aria-label="Label for furnace ${number}">
        <span class="channel-row__meta">${channel.present ? 'probe' : 'no probe'}</span>
      </label>
    `;
  }).join('');
}

function renderBundles() {
  const channels = Object.keys(state.bundles);

  el.otaChannel.innerHTML = channels.length === 0
    ? '<option value="">nothing published</option>'
    : channels.map((channel) => (
      `<option value="${escape(channel)}">${escape(channel)} · ${escape(state.bundles[channel].version)}</option>`
    )).join('');

  const chosen = el.otaChannel.value;
  const bundle = state.bundles[chosen];
  el.bundleInfo.textContent = bundle
    ? `${bundle.files} files · published ${escape(bundle.version)}`
    : 'No bundle on the server yet. Run tools/publish.py and deploy the folder.';
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

function render() {
  fillSelect(el.channelDevice);
  fillSelect(el.wifiDevice);
  fillSelect(el.otaDevice);
  renderDevices();
  renderChannelEditor();
  renderBundles();
  renderEvents();
}

async function refresh({ quiet = false } = {}) {
  if (!quiet) {
    status('Loading…');
  }

  try {
    const [devices, events] = await Promise.all([api.devices(), api.events(40)]);
    state.devices = Array.isArray(devices.devices) ? devices.devices : [];
    state.bundles = devices.bundles && typeof devices.bundles === 'object' ? devices.bundles : {};
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

  const channels = $$('[data-channel]', el.channelEditor).map((checkbox) => ({
    channel: Number(checkbox.dataset.channel),
    enabled: checkbox.checked,
    label: $(`[data-label="${checkbox.dataset.channel}"]`, el.channelEditor).value.trim(),
  }));

  try {
    await api.saveChannels(device.id, channels);
    status('Channel labels saved.', 'ok');
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
    } else if (button.dataset.action === 'wifi') {
      el.wifiDevice.value = device.id;
      el.wifiSsid.value = '';
      el.wifiPassword.value = '';
      el.wifiSsid.focus();
      el.wifiDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (button.dataset.action === 'firmware') {
      el.otaDevice.value = device.id;
      el.otaDevice.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    renderDevices();
  });

  el.channelDevice.addEventListener('change', renderChannelEditor);
  el.saveChannels.addEventListener('click', saveChannels);

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
