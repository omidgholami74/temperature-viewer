# Operations

## Deployments

### Development (compose)

```bash
cd MQTT
docker compose up -d broker bridge dashboard
docker compose logs -f bridge
```

Ports: `1883` MQTT, `9001` MQTT/WebSocket, `8080` dashboard. Anonymous access is
enabled and the broker is reachable from the host only.

### Production (systemd + broker)

| Piece | Where | Notes |
| --- | --- | --- |
| Mosquitto | Same host as the bridge, or a managed VM | Needs ports 8883/9002 open to devices/browsers |
| Bridge | Any host that can reach the broker and the API | Stateless; run **one per site** |
| Dashboard | Static hosting behind HTTPS | `config.js` points at `wss://broker:9002/mqtt` |
| API | Existing PHP host | Nothing changes |

Bridge unit:

```ini
[Unit]
Description=Furnace MQTT bridge
After=network-online.target

[Service]
User=furnace
WorkingDirectory=/srv/furnace/MQTT/bridge
Environment=FURNACE_MQTT_HOST=127.0.0.1
Environment=FURNACE_MQTT_SITE=lab
Environment=FURNACE_MQTT_API_URL=https://api.example.com/api/ingest
EnvironmentFile=/etc/furnace/mqtt-bridge.env
ExecStart=/srv/furnace/MQTT/bridge/.venv/bin/python furnace_bridge.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`mqtt-bridge.env` holds `FURNACE_MQTT_PASSWORD` and `FURNACE_MQTT_API_KEY` with
mode `0600`. One bridge per site keeps the queue small and the failure domain
obvious.

## Security

1. `allow_anonymous false`, `password_file`, `acl_file`.
2. One broker account per device, one for the bridge, one read-only for the
   dashboard.
3. TLS on 8883 (devices) and 9002 (browsers). Plain 1883 stays on loopback for
   the bridge when the broker is local.
4. `message_size_limit` above the largest legitimate batch (600 frames ≈ 30 KiB;
   256 KiB is a safe ceiling).
5. The API key lives only in the bridge environment, never in the firmware.
6. Do not expose the broker's WebSocket port to the internet without TLS: the
   dashboard credentials travel in `CONNECT`.

Certificate notes:

```bash
# Private CA for devices; Mosquitto needs the server cert + key
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout ca.key -out ca.crt -subj "/CN=Furnace CA"
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr \
  -subj "/CN=mqtt.example.com"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256
```

Give devices `ca.crt` only; give browsers a certificate signed by a public CA.

## Monitoring

| Signal | Where | Alert when |
| --- | --- | --- |
| Bridge online | Retained `furnace/<site>/_bridge/status` | `online:false` for > 2 min |
| Queue depth | `queued` in status | > 100 for 5 min (API is slow/down) |
| Dropped batches | `dropped` | Any increase |
| Rejected payloads | `rejected` + `last_error` | Any increase |
| Device presence | Retained `state` per device | `online:false` while a run is expected |
| Broker | `$SYS/broker/clients/connected`, `$SYS/broker/messages/*` | Connected clients drop to 0 |
| API | `GET /api/health`, `ingest.last_received_at_ms` | No new samples for 30 s during a firing |

A five-line shell alert is enough:

```bash
mosquitto_sub -h localhost -t 'furnace/+/_bridge/status' -C 1 \
  | jq -e '.online and .queued < 100 and .dropped == 0' >/dev/null \
  || echo "bridge unhealthy" | mail -s "furnace mqtt" ops@example.com
```

## Runbooks

**Dashboard stuck on "Connecting".**
Check `listener 9001` and `protocol websockets` in `mosquitto.conf`, then
`mosquitto_sub -h host -p 9001 -t '#' -v` (yes, `mosquitto_sub` speaks
WebSockets only in builds with support; use a browser console instead if not).
A TLS dashboard needs `wss://` and a matching port.

**Bridge logs `Not authorized`.**
The broker has `allow_anonymous false` but the bridge has no credentials, or the
ACL lacks `topic read furnace/+/+/samples`. Verify with
`mosquitto_pub -u bridge -P … -t furnace/lab/kiln-1/samples -m '{}'`.

**Queue grows, API is up.**
The API is probably returning 5xx or timing out. `last_error` in the status
topic carries the HTTP code and body prefix. A 422 means a firmware payload
mismatch; a 401/403 means the ingest key was rotated on one side only.

**Device offline but still sampling.**
`state` is retained and updated only on connect/LWT; check the broker log for
keepalive expiry. A board behind a flaky NAT may need a shorter `keepalive` or
a broker on the local network.

**Everything stored twice.**
Look for two bridges on the same site. Both subscribe to the same wildcard and
both post; the API sees interleaved batches. Run one bridge per site (or give
each a distinct `FURNACE_MQTT_SITE`).

**Disk filling.**
Mosquitto persistence stores retained state and queued QoS 1 messages. Cap
`max_queued_messages`, and remember the database — not the broker — is the
source of truth: `broker-data` can be deleted while clients are stopped.

## Upgrade procedure

1. Deploy the new bridge next to the old one on a different `client_id` and a
   test site, or run it with `--dry-run`.
2. Update firmware on one device; it publishes v1 until a protocol bump.
3. Roll the dashboard by replacing `config.js` and the static files.
4. Retire the old bridge after the queue drains (`queued: 0`).

Broker upgrades: drain queues first (`max_queued_messages`), then restart. QoS 1
messages in flight are re-delivered by clients, and the retained state topics
are rewritten by publishers on their next connect.

## Capacity planning

| Furnaces | Messages/s | HTTP req/s | Bridge CPU | Notes |
| --- | --- | --- | --- | --- |
| 1 | ~1 | 1 | negligible | Laptop / Pi |
| 10 | ~10 | 10 | one core, ~5 % | PHP-FPM handles it |
| 50 | ~50 | 50 | one core, ~25 % | Watch SQLite write latency |
| 200 | ~200 | 200 | bridge per site, or batch > 10 | Shard by site |

The dominant cost is the API transaction, not MQTT. Raising `batch_size` in the
firmware to 30 reduces requests by 3× at the cost of 3 s of latency for the
dashboard's HTTP view (the MQTT dashboard still sees each batch immediately).
