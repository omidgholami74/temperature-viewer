# MQTT protocol — device ⇄ server

Wire contract between the ESP32-S3 boards and the backend when devices publish
over MQTT instead of `POST /api/ingest`.

Status: **implemented, not enabled**. `backend/mqtt/bridge.py` consumes these
messages and feeds the existing `POST /api/ingest`, and
`esp32-s3/common/mqtt_uplink.py` publishes them from the board; the running
system still uses HTTP ingest directly (see [architecture.md](architecture.md)
§4, decision D1). Setup: [mqtt-setup.md](mqtt-setup.md). The contract is shaped
so that the existing storage and API do not have to change.

---

## 1. Why MQTT at all

HTTP ingest is a fine fit for one board: the API is the only writer, keep-alive
keeps the handshake cost down and failures are simple to reason about. MQTT adds
value when there is more than one device, or when the server must *know* a board
went away:

| Need | HTTP ingest | MQTT |
| --- | --- | --- |
| Per-device identity/credentials | shared key only | username + ACL per board |
| "Device went offline" signal | server infers from silence | broker `will` message, immediately |
| Server → device commands | device must poll | publish to a subscribed topic |
| Fan-out to many consumers | each polls the API | broker fans out |
| Operational cost | none extra | a broker to run, secure, monitor |

Rule: stay on HTTP until a second device or a command channel is actually
required.

## 2. Topic tree

```
factory/{site}/{device_id}/telemetry      device → server   QoS 1, not retained
factory/{site}/{device_id}/status         device → server   QoS 1, retained (LWT)
factory/{site}/{device_id}/event          device → server   QoS 1, not retained
factory/{site}/{device_id}/cmd            server → device   QoS 1, not retained
factory/{site}/{device_id}/cmd/ack        device → server   QoS 1, not retained
```

- `site` — installation namespace, e.g. `razi`. Keeps two factories on one
  broker apart.
- `device_id` — stable, lowercase, `[a-z0-9_-]{3,32}`, e.g. `esp32_001`. Must
  match a row in `devices.id`.
- A device may **publish only** under its own prefix and **subscribe only** to
  its own `cmd`. The broker enforces this with an ACL; the firmware does not
  need to check it.
- Wildcard used by the server consumer: `factory/+/+/telemetry`,
  `factory/+/+/status`, `factory/+/+/event`.

## 3. Telemetry payload (v2)

One message carries one second of frames — the same batching the firmware
already does for HTTP (10 frames at 10 Hz). Per-frame publishing at 10 Hz costs
10× the messages for no benefit.

```json
{
  "v": 2,
  "device_id": "esp32_001",
  "boot_id": "0f3a9c",
  "seq": 12580,
  "at": 1792523131000,
  "source": "ntp",
  "interval_ms": 100,
  "rssi": -61,
  "frames": [
    {"u": 1200000, "t": [781.25, 660.5, null, 545.75], "f": [0, 0, 1, 0]},
    {"u": 1200100, "t": [781.5,  660.75, null, 545.5], "f": [0, 0, 1, 0]}
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `v` | int | payload version, `2`. Unknown major version → reject and log, never guess |
| `device_id` | string | must equal the topic segment, otherwise the message is rejected |
| `boot_id` | string | random per boot; sequence is only monotonic **within** a boot |
| `seq` | int | per-device, per-boot, starts at 0, +1 per message. Gaps = loss |
| `at` | int \| null | device Unix time in ms of the **first** frame, or `null` when NTP has not synced |
| `source` | string | `ntp` \| `uptime` — how `at` was obtained |
| `interval_ms` | int | current sampling interval, so the server can detect a config change |
| `rssi` | int | Wi-Fi strength, for the device health view |
| `frames[].u` | int | device uptime in ms |
| `frames[].t` | array | one temperature per channel in °C; `null` for a faulted channel, **never** `NaN` |
| `frames[].f` | array | fault bitmask per channel: `1` open circuit, `2` short to GND, `4` short to VCC, `8` read error |

Compatibility: `frames[]` entries have exactly the shape of the existing HTTP
ingest samples (`u`, `at`, `t`, `f`). The MQTT consumer therefore decodes one
envelope and calls the same validation and storage code as `/api/ingest`; no
second data path into the database.

Frame timestamps are derived as `at + u - u_first` when `source = ntp`,
otherwise the server anchors the batch to its receive time (already implemented
in `POST /api/ingest`).

## 4. Status payload (retained + last will)

Published by the device on connect, on change, and every 60 s as a heartbeat.
The same document is registered as the **last will** so the broker publishes
`offline` if the device vanishes without disconnecting.

```json
{
  "v": 2,
  "device_id": "esp32_001",
  "state": "online",
  "boot_id": "0f3a9c",
  "fw": "esp32-s3-furnace/1.2.0",
  "interval_ms": 100,
  "channels": 4,
  "sensor": "MAX31855",
  "rssi": -61,
  "uptime_ms": 1200000,
  "ts": 1792523131000
}
```

| `state` | Set when |
| --- | --- |
| `boot` | shortly after reset, before Wi-Fi/NTP |
| `online` | joined, NTP synced, telemetry flowing |
| `degraded` | online but at least one channel faults, or NTP unsynced |
| `offline` | last will, or a clean `disconnect` |

The server writes this into `devices` (`last_seen`, `boot_id`, `firmware`,
`rssi`, `state`) and into `device_events` when `state` changes. An `offline`
from the will is a *fast* signal; silence detection (3 × interval, no message)
stays as a fallback in case the broker itself is restarted.

## 5. Event payload

Discrete, low-rate, worth keeping:

```json
{"v": 2, "device_id": "esp32_001", "kind": "sensor_fault",
 "channel": 2, "detail": "open circuit", "ts": 1792523131000}
```

`kind` ∈ `sensor_fault`, `sensor_recovered`, `ntp_sync`, `ntp_lost`,
`wifi_reconnect`, `buffer_overflow`, `config_applied`, `boot`.

`buffer_overflow` carries `dropped` and matters: it is the one case where the
device knowingly discarded data.

## 6. Commands

Server → device, only when someone asked for it (settings UI, ops script).
Every command carries an id so the ack can be matched.

```json
{"v": 2, "cmd_id": "c-8841", "op": "set_interval",
 "args": {"interval_ms": 1000}}
```

| `op` | `args` | Effect |
| --- | --- | --- |
| `set_interval` | `interval_ms` (100–60000) | new sampling period, persisted |
| `set_offset` | `channel` (0–3), `offset_c` | per-channel calibration offset |
| `reset_run` | — | device marks a new run boundary |
| `reboot` | — | controlled restart |
| `ping` | — | liveness check with status reply |

Ack:

```json
{"v": 2, "cmd_id": "c-8841", "ok": true, "applied": {"interval_ms": 1000}}
{"v": 2, "cmd_id": "c-8841", "ok": false, "error": "interval_ms out of range"}
```

Rules:

- Commands are **not** retained: a device that connects later must not receive a
  stale instruction.
- A device applies a command within 5 s or acks `ok: false` with a reason; the
  server shows a timeout if neither arrives.
- Every command and ack is written to `device_events` (`config_applied`,
  `config_rejected`), because "who changed the sample rate" is a question that
  gets asked.
- `cmd_id` is unique per attempt; a retried command with the same id must be
  idempotent on the device.

## 7. QoS, keep-alive, session

| Topic | QoS | Retained | Rationale |
| --- | --- | --- | --- |
| `telemetry` | 1 | no | at-least-once; duplicates are dropped by `(device_id, boot_id, seq)` and by the `(run_id, t_ms)` unique index |
| `status` | 1 | yes | the server must read current state right after subscribing |
| `event` | 1 | no | low rate, must not be lost |
| `cmd` | 1 | no | one delivery is expected while the device is connected |
| `cmd/ack` | 1 | no | matched by `cmd_id` |

- Keep-alive: **30 s**. Last will delay: 5 s.
- Clean session `true` (no broker-side queue): the device already buffers in
  RAM, and a day of queued telemetry replayed after an outage is worse than a
  gap that is recorded as a gap.
- Client id: `{site}-{device_id}-{boot_id}` so two boards cannot evict each
  other by accident.
- Duplicates: the consumer is idempotent. `(device_id, boot_id, seq)` is stored
  in `device_messages` for a short window, and rows are inserted with
  `ON CONFLICT DO NOTHING` on `(run_id, t_ms)`.

## 8. Authentication and ACL

- TLS on 8883 only; no plaintext listener in production.
- One broker user per device, password provisioned with the board (never in
  Git — the same rule as `module/config.py`).
- ACL, per device:

```
user esp32_001
topic write factory/razi/esp32_001/telemetry
topic write factory/razi/esp32_001/status
topic write factory/razi/esp32_001/event
topic write factory/razi/esp32_001/cmd/ack
topic read  factory/razi/esp32_001/cmd
```

- The server consumer has read access to `factory/+/+/#` and write access to
  `factory/+/+/cmd` only.
- Rotating a credential for one board touches one broker user and one board —
  the concrete reason MQTT becomes worthwhile at a second device.

## 9. Server-side processing

```
broker ──▶ mqtt consumer ──▶ validate(§3 shapes, ranges, device identity)
                          ├─▶ device state upsert + device_events
                          └─▶ storage.insertBatch(run_id, rows)   ← same call /api/ingest uses
```

Ordering is **not** assumed across messages from different devices; per device
it is (one connection, QoS 1). Out-of-order `seq` values from the same boot are
accepted but recorded as an `event`.

If the consumer dies, the broker keeps nothing (clean sessions), so the backlog
lives on the device's RAM buffer — which is exactly where a bounded,
sample-rate-aware buffer belongs.

## 10. Example session

```
device  → CONNECT  client=razi-esp32_001-0f3a9c, will=status{state:offline}
device  → PUBLISH  status  {state:"online", ...}              (retained)
device  → PUBLISH  telemetry {seq:0, at:…, frames:[10]}        (1 s later)
device  → PUBLISH  telemetry {seq:1, …}
device  → PUBLISH  event   {kind:"sensor_fault", channel:2}
server  → PUBLISH  cmd     {cmd_id:"c-8841", op:"set_interval", args:{interval_ms:1000}}
device  → PUBLISH  cmd/ack {cmd_id:"c-8841", ok:true}
device  → DISCONNECT
broker  → PUBLISH  status  {state:"offline", ...}              (retained, from will)
```

## 11. Testing without hardware

The consumer is testable from a laptop:

```bash
mosquitto_pub -h localhost -t factory/razi/esp32_001/telemetry -q 1 -f telemetry.json
```

`mosquitto_pub` with a fixture file covers: valid batch, unknown payload version,
`device_id`/topic mismatch, 3-channel device with `null` channel 4, sequence gap,
and a duplicate `seq` (must not double-count samples).
