# MQTT transport for the ESP32-S3 firmware

`furnace_mqtt.py` publishes the same `[uptime_ms, [t1..t4], [f1..f4]]` frames the
HTTP uploader sends, but over a single long-lived MQTT session. The bridge
stores them through the API, so history, CSV export and the dashboard work
unchanged.

## Install on the board

```bash
# once, on the board
mpremote mip install umqtt.simple
```

Copy `furnace_mqtt.py` next to the firmware modules and construct the transport:

```python
from furnace_mqtt import MqttTransport

transport = MqttTransport(
    site="lab",
    device="kiln-1",
    host="192.168.1.50",
    port=1883,
    username="kiln-1",
    password="...",
    batch_size=10,          # one second at 10 Hz
    buffer_limit=3600,      # ten minutes if the broker is unreachable
)
transport.start()

while True:
    frame = read_max31855()             # [uptime_ms, [t...], [f...]]
    transport.enqueue(frame)
    transport.poll()                    # acks + keepalive, never raises
    sleep_until_next_slot()
```

`poll()` is deliberately total: a broken socket is logged, the client is
dropped and a retry is scheduled with exponential backoff (2–60 s). Sampling
must never stop because the network did.

## What it publishes

| Topic | QoS | Retain | Payload |
| --- | --- | --- | --- |
| `furnace/<site>/<device>/samples` | 1 | no | `{"v":1,"seq":N,"at":ms,"frames":[[u,[t1..t4],[f1..f4]],...]}` |
| `furnace/<site>/<device>/state` | 1 | **yes** | `{"v":1,"online":true,"buffered":N,...}` |
| `furnace/<site>/<device>/ack` | 1 | no | Subscribed; trims the local buffer |

The Last Will is the retained `state` topic with `online: false`, so an
unplugged board shows as offline in the dashboard the moment the broker's
keepalive expires — no polling timeout to tune.

## Buffer and acknowledgements

* Frames are appended to a list. Every `batch_size` **unsent** frames a batch is
  published with the next `seq`.
* A batch is only removed from the list when the bridge acknowledges its `seq`
  with `ok: true`; `accepted` tells the transport how many frames were stored.
* The buffer is capped at `buffer_limit` frames (3600 = ten minutes at 10 Hz).
  Beyond that the **oldest** frames are dropped and counted in `dropped`, which
  the next batch reports in `state`.
* `at` is the wall clock of the newest frame in the batch. If NTP has not
  synced, `now_ms()` returns `None` and the field is omitted; the API then
  anchors the batch on arrival.

## Tests

```bash
python3 -m unittest discover -s MQTT/firmware/tests -v
```

11 tests run on CPython with a fake client: topic names, retained will, QoS 1,
ack trimming, duplicate acks, failed acks, buffer overflow and publish failure
recovery. The hardware imports (`machine`, `network`) are not touched.
