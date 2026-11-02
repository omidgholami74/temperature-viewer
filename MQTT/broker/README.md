# Broker configuration

The stack uses [Eclipse Mosquitto](https://mosquitto.org/) 2.x. It is small,
handles 10 Hz telemetry from dozens of devices on a single core, and supports
WebSocket clients, which the dashboard needs.

```
broker/
├── mosquitto.conf   # development listeners (plain 1883 + WebSocket 9001)
└── acl.example      # production ACL, kept as documentation
```

## Development

```bash
docker compose up -d broker
mosquitto_sub -h 127.0.0.1 -t 'furnace/#' -v      # watch everything
```

`mosquitto.conf` allows anonymous clients on purpose: it is a laptop stack, and
the compose file binds it to localhost only. **Do not expose this to the
internet.**

## Production checklist

1. **Disable anonymous access.**

   ```conf
   allow_anonymous false
   password_file /mosquitto/config/passwd
   ```

   ```bash
   mosquitto_passwd -c /mosquitto/config/passwd bridge
   mosquitto_passwd    /mosquitto/config/passwd dashboard
   mosquitto_passwd    /mosquitto/config/passwd kiln-1
   ```

2. **Apply ACLs** (copy `acl.example`, add one block per device).

   ```conf
   acl_file /mosquitto/config/acl
   ```

3. **Enable TLS** and drop the plain listener, or keep it bound to loopback for
   the bridge only.

   ```conf
   listener 8883
   certfile /mosquitto/certs/server.crt
   keyfile  /mosquitto/certs/server.key
   cafile   /mosquitto/certs/ca.crt
   require_certificate false

   listener 9002
   protocol websockets
   certfile /mosquitto/certs/server.crt
   keyfile  /mosquitto/certs/server.key
   ```

4. **Cap resources.** A runaway client must not take the broker down:

   ```conf
   max_connections -1
   max_queued_messages 1000
   message_size_limit 262144      # 256 KiB, eight times a 600-frame batch
   max_inflight_messages 20
   ```

5. **Keep persistence** only for retained state and queued QoS 1 messages. The
   database is the source of truth; `broker-data` is disposable.

## Why these listeners

| Port | Protocol | Consumer |
| --- | --- | --- |
| 1883 | MQTT | ESP32 firmware, bridge |
| 8883 | MQTT + TLS | Production devices over untrusted networks |
| 9001 | MQTT over WebSocket | Dashboard in a browser |
| 9002 | MQTT over WebSocket + TLS | Production dashboard (`wss://`) |

Mosquitto 2.x refuses to start without an explicit `listener` line; the shipped
config has both development listeners plus comments for the TLS ones.

## Verifying

```bash
# Publish a batch by hand and watch the ack
mosquitto_pub -h 127.0.0.1 -t furnace/lab/test/samples -q 1 \
  -m '{"v":1,"seq":1,"frames":[[0,[700,710,720,730],[0,0,0,0]]]}'

mosquitto_sub -h 127.0.0.1 -t 'furnace/lab/test/ack' -v -C 1
mosquitto_sub -h 127.0.0.1 -t 'furnace/lab/_bridge/status' -v -C 1
```

A malformed payload is rejected by the bridge and never reaches the API; the
`rejected` counter in the retained status is the signal to watch.
