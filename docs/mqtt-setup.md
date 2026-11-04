# MQTT setup — step by step

How to put a broker in front of this project without touching the PHP API, the
SQLite database or the dashboard.

```
ESP32-S3 ──MQTT──▶ mosquitto ──▶ backend/mqtt/bridge.py ──HTTP──▶ POST /api/ingest
```

The bridge speaks the payload from [mqtt-protocol.md](mqtt-protocol.md) and
re-posts it to the endpoint that already exists, so there is still exactly one
writer into `readings` and the dashboard keeps working unchanged. Only the
transport is new.

Two useful properties fall out of this shape:

- **The server-side key stays private.** The board only knows a broker password
  on the local network; `FURNACE_INGEST_KEY` never reaches the device.
- **A board can disappear visibly.** The broker publishes its `will`, so
  "offline" is known in 30 s instead of being inferred from missing samples.

You can go back to HTTP at any time by reverting the two-line firmware change;
both paths store into the same tables and must not run at once for one board.

---

## 0. Know the box first

Run these three on the server you SSH into; everything below branches on the
answer.

```bash
cat /etc/os-release | head -3          # Debian/Ubuntu? Rocky/Alma/RHEL?
sudo -v && echo "sudo ok"             # a broker needs root to install
command -v apt-get dnf                 # which package manager exists
command -v ufw firewall-cmd            # which firewall is in charge
systemctl is-active apache2 nginx      # what serves the API
```

The commands below are written for `apt` (Debian/Ubuntu/Raspberry Pi OS); the
`dnf` equivalents are noted where they differ.

## 1. Broker on the server

On the host that runs the PHP API:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
systemctl status mosquitto --no-pager
```

On RHEL family the package is `mosquitto` from EPEL:

```bash
sudo dnf install -y epel-release && sudo dnf install -y mosquitto
sudo systemctl enable --now mosquitto
```

Mosquitto 2.x listens on `127.0.0.1:1883` only until a listener is declared —
that is why the config below is required, not optional.

## 2. Two users: the bridge and each board

```bash
# The consumer (only it may read every device and write commands).
sudo mosquitto_passwd -c /etc/mosquitto/passwd furnace-bridge

# One user per board. Read the password without echoing it and without
# leaving it in the shell history (-b takes it as an argument, and bash
# records that line):
read -rsp 'password for esp32_001: ' DEVPASS; echo
sudo mosquitto_passwd -b /etc/mosquitto/passwd esp32_001 "$DEVPASS"
unset DEVPASS
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 600 /etc/mosquitto/passwd
```

A second board is one more `mosquitto_passwd` line and one more ACL block. That
is the whole point of moving to MQTT for more than one device.

## 3. ACL — a board may only touch its own topics

```bash
sudo cp infrastructure/mqtt/aclfile /etc/mosquitto/aclfile
sudo nano /etc/mosquitto/aclfile     # set your site and device ids
sudo chown mosquitto:mosquitto /etc/mosquitto/aclfile
```

The template gives `furnace-bridge` read access to `factory/+/+/{telemetry,
status,event,cmd/ack}` and write access to `factory/+/+/cmd`, and gives each
device write access to its own three topics plus its own `cmd` read.

## 4. Broker configuration

```bash
sudo cp infrastructure/mqtt/furnace.conf /etc/mosquitto/conf.d/furnace.conf
sudo systemctl restart mosquitto
sudo ss -ltnp | grep 1883          # 0.0.0.0:1883, owned by mosquitto
```

- Do **not** forward port 1883 on the router. The board is on the same LAN as
  the broker, and the bridge is on the same host as the broker; the traffic
  never needs to leave the network.
- The bridge is a client of the broker, so it connects to `127.0.0.1` even when
  the API is public.

### 4a. Firewall — who may reach 1883

Only the boards need it, and only from their own network:

```bash
# ufw (Ubuntu/Debian)
sudo ufw status
sudo ufw allow from 192.168.1.0/24 to any port 1883 proto tcp

# firewalld (RHEL/Rocky/Alma)
sudo firewall-cmd --permanent --add-rich-rule=\
'rule family=ipv4 source address=192.168.1.0/24 port port=1883 protocol=tcp accept'
sudo firewall-cmd --reload
```

If the boards are **not** on the server's network, do not open 1883 to the
world: a password-only broker on a public port is found by scanners within
hours. Use the TLS listener on 8883 below, allow only the factory's public IP,
or put both ends on a VPN.

### 4b. TLS (only when the broker is not on the same LAN)

Uncomment the `8883` listener at the bottom of `furnace.conf`, then create a
certificate signed by a CA the board can trust:

```bash
sudo mkdir -p /etc/mosquitto/certs
sudo openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout /etc/mosquitto/certs/server.key \
  -out /etc/mosquitto/certs/server.crt \
  -subj "/CN=furnace-broker.local"
sudo chown -R mosquitto:mosquitto /etc/mosquitto/certs
```

On the board, set `MQTT_SSL = True` and `MQTT_PORT = 8883`. MicroPython's
`ssl` module has a small root store, so a self-signed certificate needs
`ssl_params={"cert_reqs": ssl.CERT_NONE}` inside `mqtt_uplink.py` — the same
trade-off as `TLS_VERIFY = False` on the HTTP path.

## 5. The bridge

```bash
# A service account and a copy of the repository next to the API.
sudo useradd -r -s /usr/sbin/nologin furnace
sudo mkdir -p /opt/furnace /etc/furnace
sudo rsync -a --exclude .git --exclude .venv ./ /opt/furnace/
sudo chown -R furnace:furnace /opt/furnace

# paho-mqtt is the only dependency, and it lives in a virtual environment.
sudo -u furnace python3 -m venv /opt/furnace/.venv
sudo -u furnace /opt/furnace/.venv/bin/pip install paho-mqtt

# Secrets and endpoints.
sudo install -m 600 -o furnace -g furnace \
  /opt/furnace/infrastructure/mqtt/mqtt-bridge.env.example \
  /etc/furnace/mqtt-bridge.env
sudo nano /etc/furnace/mqtt-bridge.env    # MQTT_PASSWORD, API_KEY

sudo cp /opt/furnace/infrastructure/mqtt/furnace-mqtt-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now furnace-mqtt-bridge
journalctl -u furnace-mqtt-bridge -f
```

A healthy start looks like this:

```
mqtt: connecting to 127.0.0.1:1883
mqtt: subscribed factory/+/+/telemetry
mqtt: subscribed factory/+/+/status
mqtt: subscribed factory/+/+/event
```

`API_URL` should be `http://127.0.0.1/api/ingest` when the bridge runs on the
API host: no TLS handshake, no DNS, no public internet in the path.

If the API answers on a **name-based virtual host**, a loopback request without
the matching `Host` header lands on the default vhost and returns `404`. Point
the name at loopback instead of using a bare IP:

```bash
echo '127.0.0.1 apitemp.futurefounderco.com' | sudo tee -a /etc/hosts
# API_URL=http://apitemp.futurefounderco.com/api/ingest
```

That keeps the right `Host` header, stays on loopback, and still works when the
vhost is HTTPS.

## 6. Test the whole path with no hardware

Publish what a board would publish and watch it land in the database.

```bash
cat > /tmp/telemetry.json <<'JSON'
{
  "v": 2, "device_id": "esp32_001", "boot_id": "0f3a9c", "seq": 0,
  "at": 1792523131000, "source": "ntp", "interval_ms": 100, "rssi": -61,
  "frames": [
    {"u": 1200000, "t": [781.25, 660.5, null, 545.75], "f": [0, 0, 1, 0]},
    {"u": 1200100, "t": [781.5, 660.75, null, 545.5],  "f": [0, 0, 1, 0]}
  ]
}
JSON

mosquitto_pub -h 127.0.0.1 -u esp32_001 -P 'the-device-password' \
  -t factory/razi/esp32_001/telemetry -q 1 -f /tmp/telemetry.json
```

Then check the three layers in order:

```bash
# 1. did the bridge store it?
journalctl -u furnace-mqtt-bridge -n 5 --no-pager
#    mqtt: subscribed ...        (once at start)
#    (nothing else on success — samples are logged only when something fails)

# 2. did the API store it? (the Host header picks the right vhost on loopback)
curl -s -H 'Host: apitemp.futurefounderco.com' http://127.0.0.1/api/health
curl -s -H 'Host: apitemp.futurefounderco.com' \
  'http://127.0.0.1/api/live?since=0' | head -c 600

# 3. is it visible?
#    open https://temp.futurefounderco.com
```

`/api/live` showing `sample_count: 2` and the two temperatures is the proof
that MQTT reached SQLite.

Test the failure cases too, because they are the ones that bite later:

| Publish this | Expected |
| --- | --- |
| `"v": 3` | bridge logs `rejected … unsupported payload version`; nothing stored |
| `"device_id": "esp32_002"` on the `esp32_001` topic | `rejected … does not match the topic` |
| `"seq": 0` twice | stored once; bridge counts a duplicate |
| `"seq": 5` after `seq: 0` | bridge logs `lost 4 message(s)` |
| 4 channels with a `null` | stored, that channel empty in the chart |
| `"t": [700, 1900, null, null]` | `rejected … outside -270..1800 C` |

## 7. The board

### 7a. Configuration

Add to `config.py` on the ESP32-S3 (the file is git-ignored, like `API_KEY`):

```python
# --- MQTT (alternative to API_URL) --------------------------------------
MQTT_DEVICE_ID = "esp32_001"    # [a-z0-9_-]{3,32}; also the broker username
MQTT_SITE = "razi"
MQTT_HOST = "192.168.1.10"      # the broker on your LAN
MQTT_PORT = 1883
MQTT_USER = MQTT_DEVICE_ID
MQTT_PASSWORD = "the-device-password"
MQTT_SSL = False                # True with the 8883 listener
SAMPLE_INTERVAL_MS = 100        # reported in every message, for the health view
```

### 7b. Upload the module

```bash
mpremote connect COM6 cp esp32-s3/common/mqtt_uplink.py :mqtt_uplink.py
```

`umqtt.simple` is frozen into the stock ESP32-S3 build. If the board says
`ImportError: no module named 'umqtt'`:

```bash
mpremote connect COM6 mip install umqtt.simple
```

### 7c. Swap the transport in `main.py`

Two edits, no other change:

```python
# was: from ingest import Ingest
from mqtt_uplink import MqttUplink

# was: ingest = Ingest(config)
uplink = MqttUplink(config)
uplink.start()
```

and where the loop queues a frame:

```python
# was: ingest.add(uptime_ms, temperatures, faults, at_ms)
uplink.add(uptime_ms, temperatures, faults, at_ms)
uplink.tick()      # publishes a full batch, the heartbeat, and reads commands
```

`add()` buffers, `tick()` publishes once `BATCH_SIZE` frames are queued — the
same one-second cadence as `ingest.py`, so nothing about the sampling loop
changes. Keep the `supervisor.py` watchdog as it is.

### 7d. Reset and watch

```bash
mpremote connect COM6 reset
mpremote connect COM6 repl
```

Expected:

```
MQTT: connected to 192.168.1.10:1883 as razi/esp32_001 (boot 0f3a9c)
```

and on the server, within a second:

```
mqtt: esp32_001 is online (fw esp32-s3-furnace/1.2.0, rssi -61, boot 0f3a9c)
```

## 8. See it online

Nothing else to configure: the dashboard reads `/api/live` and `/api/history`,
and those are fed by the same rows either way.

1. Open <https://temp.futurefounderco.com>.
2. The four cards fill within one batch (1 s) and the chip reads *Recording*.
3. `https://apitemp.futurefounderco.com/api/health` confirms the row count is
   growing.

If the dashboard is empty while `/api/live` returns samples, the problem is
CORS (the UI origin is not in `FURNACE_ALLOWED_ORIGINS`), not MQTT — see
[../backend/README.md](../backend/README.md#cors-and-write-protection).

## 9. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Board logs `Connection Refused: not authorised` | Wrong password, or the ACL has no `user` block for that id. `sudo tail -f /var/log/mosquitto/mosquitto.log`. |
| Board connects but `publish` seems ignored | The ACL gives `write` on another topic prefix; the broker silently drops it. Compare the exact topic strings. |
| `ImportError: no module named 'umqtt'` | `mpremote connect COM6 mip install umqtt.simple`. |
| Bridge logs `API 401` | `API_KEY` in `/etc/furnace/mqtt-bridge.env` does not equal `FURNACE_INGEST_KEY`. |
| Bridge logs `API 404` | `API_URL` points at the dashboard host or a path without `/api/ingest`. |
| Bridge logs `rejected … unsupported payload version` | The board sends a newer envelope than the bridge knows; upgrade the bridge before the firmware. |
| Bridge logs `lost N message(s)` | The broker did not deliver, or the board's queue overflowed. Compare with the board's `dropped=` counter. |
| `100 samples not stored` with `status None` | The API is down or `API_URL` is wrong; the bridge retried `API_RETRIES` times. |
| Data is 1 s late | Expected: one message is one second of frames, exactly like `BATCH_SIZE = 10` on the HTTP path. Lower `BATCH_SIZE` for a faster screen at more messages per second. |
| Dashboard shows a new run after switching | `at` jumped (NTP) or the clock reset; see [../backend/README.md](../backend/README.md). |
| No `at` in the messages (`source: uptime`) | NTP has not synced. Data still lands, anchored to arrival time. |

## 10. Security notes

- One password per board, in `config.py` on the board only. Never in Git.
- `furnace-bridge` may write `cmd` for every device: it is the only client that
  can ask a board to do something, so protect `/etc/furnace/mqtt-bridge.env`
  (`chmod 600`, owner `furnace`).
- Plain MQTT (1883) on the LAN is the same trust level as the HTTP ingest key
  it replaces. Over an untrusted link, use the 8883 listener.
- The broker knows every topic, so its logs are operational data: keep
  `log_type error/warning/notice` (as in `furnace.conf`) rather than `debug`.

## 11. What this does not do yet

MQTT is a transport, not a data model. These are still open, and they are the
reason [database-schema.md](database-schema.md) proposes `devices` and
`device_events`:

- `status` messages are **logged, not stored** — there is no `devices` table to
  update, so "when did board 1 go offline" lives in journald, not in SQLite.
- `event` messages (sensor fault, buffer overflow) are logged the same way.
- Commands are answered but almost none are implemented: `ping` works, and
  anything else is refused with `ok: false` instead of being faked.
- The board still publishes without a per-device identity in storage: samples
  land in the same run, so one board per run is still the rule.

## 12. Tests

```bash
python3 -m unittest backend/tests/test_mqtt_bridge.py -v      # bridge, no broker
python3 -m unittest discover -s esp32-s3/tests -v             # firmware, no board
```

The first covers payload validation, sequence gaps/duplicates, the HTTP
forward, and a refused payload not being retried. The second checks the
published envelope, batching, the `will`, and command acknowledgements against
a fake client.
