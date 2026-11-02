/**
 * Dashboard runtime configuration.
 *
 * Served as a plain script so an operator can change the broker URL on the
 * deployed copy without rebuilding anything.
 */
window.FURNACE_MQTT_CONFIG = {
  // MQTT over WebSocket exposed by the broker's websockets listener.
  // Use wss:// and port 9002 behind TLS.
  url: 'ws://localhost:9001/mqtt',

  // Only topics for this site are displayed. Must match FURNACE_MQTT_SITE.
  site: 'lab',

  // Read-only broker account (see broker/acl.example).
  username: 'dashboard',
  password: '',

  clientId: `furnace-dash-${Math.random().toString(16).slice(2, 8)}`,

  // Subscriptions. Wildcards are allowed; the client filters the site.
  topics: [
    'furnace/+/+/samples',
    'furnace/+/+/state',
    'furnace/+/_bridge/status',
  ],

  // Visible window of the live chart, in seconds.
  windowSeconds: 600,

  // Sample buffers per device; 4000 frames at 10 Hz is under seven minutes.
  pointsPerChannel: 4000,

  channels: [
    { name: 'Furnace 1', short: 'F1' },
    { name: 'Furnace 2', short: 'F2' },
    { name: 'Furnace 3', short: 'F3' },
    { name: 'Furnace 4', short: 'F4' },
  ],
};
