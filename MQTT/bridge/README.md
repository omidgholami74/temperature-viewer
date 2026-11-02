# Furnace MQTT bridge

Subscribes to `furnace/<site>/<device>/samples`, validates each batch, stores it
through the existing `POST /api/ingest` and acknowledges it on
`furnace/<site>/<device>/ack`.

The bridge is the only component that talks to the database-facing API, so the
firmware never needs an HTTP connection and the dashboard never needs database
access.

```
device ──samples──▶ broker ──▶ bridge ──POST /api/ingest──▶ SQLite
   ▲                             │
   └────────── ack ◀─────────────┘
```

## Run

```bash
cd MQTT/bridge
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml   # edit host, site, API URL
python3 furnace_bridge.py --config config.toml
```

Without a config file, everything can come from the environment:

```bash
FURNACE_MQTT_HOST=broker.local \
FURNACE_MQTT_SITE=lab \
FURNACE_MQTT_API_URL=http://127.0.0.1:8000/api/ingest \
FURNACE_MQTT_API_KEY=change-me \
python3 furnace_bridge.py
```

`--dry-run` parses and validates batches and logs them without posting, which is
the fastest way to check a new firmware version. `--help` lists the individual
overrides.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `FURNACE_MQTT_HOST` | `127.0.0.1` | Broker host |
| `FURNACE_MQTT_PORT` | `1883` | Broker port (`8883` with TLS) |
| `FURNACE_MQTT_TLS` | `false` | Enable TLS; pair with `CA_FILE` |
| `FURNACE_MQTT_CA_FILE` | – | CA bundle for the broker certificate |
| `FURNACE_MQTT_USERNAME` / `_PASSWORD` | – | Bridge broker account |
| `FURNACE_MQTT_CLIENT_ID` | `furnace-bridge` | Must be unique per instance |
| `FURNACE_MQTT_SITE` | `lab` | Second topic level this instance owns |
| `FURNACE_MQTT_API_URL` | `http://127.0.0.1:8000/api/ingest` | API endpoint |
| `FURNACE_MQTT_API_KEY` | empty | Sent as `X-Api-Key` |
| `FURNACE_MQTT_QUEUE_LIMIT` | `500` | Batches buffered while the API is down |
| `FURNACE_MQTT_REQUEST_TIMEOUT` | `10` | HTTP timeout in seconds |
| `FURNACE_MQTT_STATUS_INTERVAL` | `15` | Retained status publish interval |
| `FURNACE_MQTT_LOG_LEVEL` | `INFO` | `DEBUG` logs every accepted batch |

## Delivery guarantees

* **At least once.** A batch leaves the queue only after the API answers 2xx.
  The device keeps the frames until the `ack` arrives, so the chain is
  end-to-end reliable rather than best-effort at one hop.
* **Deduplicated.** `seq` is per device. A repeated QoS 1 delivery is
  acknowledged again without a second POST. A device reboot resets `seq` to 0;
  a jump backwards by more than 1000 is treated as a reboot and accepted.
* **Ordered per device.** A failed batch goes back to the front of the queue, so
  a later batch cannot overtake it.
* **Bounded.** `queue_limit` caps memory. When it is full the *oldest* batch is
  dropped and `dropped` in the status topic increments; the device's own buffer
  (ten minutes at 10 Hz) is usually the first line of defence.

## Failure classification

| Response | Action | Why |
| --- | --- | --- |
| `2xx` | Ack the batch, publish status | Stored |
| `400`, `413`, `422` | Drop, ack `ok:false` | The payload can never be accepted; retrying would block the queue |
| `401`, `403`, `429` | Keep, retry with backoff | Wrong key or rate limit is operator-fixable; data is too valuable to drop |
| `5xx`, timeout, DNS | Keep, retry with backoff (1–30 s) | API down or restarting |

## Status topic

Retained on `furnace/<site>/_bridge/status`, refreshed every
`status_interval` seconds:

```json
{
  "online": true,
  "queued": 0,
  "received": 4821,
  "forwarded": 4820,
  "samples": 48120,
  "duplicates": 1,
  "rejected": 0,
  "dropped": 0,
  "failures": 0,
  "last_error": null,
  "uptime_s": 3600.2
}
```

The client also registers a Last Will on the same topic (`online: false`), so a
crashed bridge is visible in the dashboard without polling.

## Tests

```bash
python3 -m unittest discover -s MQTT/bridge/tests -v
```

33 tests cover topic parsing, payload validation, sentinel handling, dedupe,
reboot detection, the bounded queue, retry/permanent error classification,
acknowledgements and the retained status shape. They need no broker, no API and
no `paho` package: the MQTT client is injected.

## Layout

| File | Purpose |
| --- | --- |
| `furnace_bridge.py` | Broker client, validation, queue, HTTP poster, acks |
| `config.example.toml` | Documented configuration |
| `requirements.txt` | `paho-mqtt` 2.x |
| `Dockerfile` | Slim non-root image used by `../docker-compose.yml` |
| `tests/` | Pure-Python unit tests |
