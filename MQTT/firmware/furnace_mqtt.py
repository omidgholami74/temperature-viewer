"""MQTT transport for the ESP32-S3 furnace firmware (MicroPython).

This is a drop-in alternative to the HTTP uploader in ``esp32-s3/``: the same
frame batch is published to ``furnace/<site>/<device>/samples`` instead of being
POSTed to the API. The bridge stores it, so history, CSV export and the
dashboard do not change.

Why MQTT here
-------------
* One long-lived TCP connection, no per-batch HTTP handshake. A 10 Hz furnace
  spends a fraction of the radio time it spends on POSTs.
* The broker absorbs short API outages: the bridge queues batches and the
  device keeps its own buffer until it sees an ``ack``.
* A retained ``state`` topic plus MQTT Last Will makes "device offline" a
  broker fact instead of a guess from a timeout.

Install
-------
The only dependency is ``umqtt.simple`` from micropython-lib::

    mpremote mip install umqtt.simple

Typical use inside the sampling loop::

    transport = MqttTransport(config)
    transport.start()
    ...
    transport.enqueue([uptime_ms, [t1, t2, t3, t4], [f1, f2, f3, f4]])
    transport.poll()          # call every loop: handles acks and keepalive
"""

try:  # MicroPython ships ujson; CPython falls back to json.
    import ujson as json
except ImportError:  # pragma: no cover - only on CPython
    import json

try:
    import utime as time
except ImportError:  # pragma: no cover - only on CPython
    import time

try:
    from umqtt.simple import MQTTClient
except ImportError:  # pragma: no cover - allows unit tests on CPython
    MQTTClient = None

PROTOCOL_VERSION = 1
ROOT = "furnace"
MAX_BUFFERED_FRAMES = 3600   # ten minutes at 10 Hz
DEFAULT_BATCH = 10           # one second of frames per publish
KEEPALIVE_S = 60
RECONNECT_MIN_S = 2
RECONNECT_MAX_S = 60


def encode_batch(site, device, seq, frames, at=None):
    """Serialise a frame batch. Pure function, unit tested on CPython.

    ``frames`` is a list of ``[uptime_ms, [t1..t4], [f1..f4]]``. ``at`` is the
    wall clock of the first frame, omitted until NTP has synced.
    """
    payload = {
        "v": PROTOCOL_VERSION,
        "seq": int(seq),
        "frames": frames,
    }

    if at is not None:
        payload["at"] = int(at)

    return json.dumps(payload).encode()


def now_ms():
    try:
        return int(time.time() * 1000)
    except (AttributeError, OverflowError):  # pragma: no cover - very old builds
        return None


class MqttTransport:
    """Buffers frames, publishes batches and drops them only after an ``ack``."""

    def __init__(
        self,
        site,
        device,
        host,
        port=1883,
        username=None,
        password=None,
        ssl=False,
        ssl_params=None,
        batch_size=DEFAULT_BATCH,
        buffer_limit=MAX_BUFFERED_FRAMES,
        client_factory=None,
        clock=time,
    ):
        self.site = site
        self.device = device
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = ssl
        self.ssl_params = ssl_params or {}
        self.batch_size = max(1, int(batch_size))
        self.buffer_limit = max(self.batch_size, int(buffer_limit))
        self._client_factory = client_factory or self._default_client
        self.clock = clock

        self.client = None
        self.seq = 0
        self.buffer = []          # frames not yet acknowledged
        # Frames already published, counted from the head of the buffer. An
        # unacknowledged frame must not be published again on the next flush,
        # otherwise every batch would re-send everything still in the buffer.
        self.sent = 0
        self.last_acked_seq = -1
        self.dropped = 0
        self.published = 0
        self._backoff = RECONNECT_MIN_S
        self._next_attempt = 0
        self._connected_at = 0

    # -- topics -----------------------------------------------------------

    def topic(self, leaf):
        return "{}/{}/{}/{}".format(ROOT, self.site, self.device, leaf)

    # -- connection -------------------------------------------------------

    def _default_client(self):
        client = MQTTClient(
            client_id="furnace-" + self.device,
            server=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            keepalive=KEEPALIVE_S,
            ssl=self.ssl,
            ssl_params=self.ssl_params,
        )
        return client

    def _state_payload(self, online):
        body = {
            "v": PROTOCOL_VERSION,
            "online": online,
            "device": self.device,
            "buffered": len(self.buffer),
            "published": self.published,
        }
        return json.dumps(body).encode()

    def connect(self):
        """Connect and publish the retained online state."""
        self.client = self._client_factory()

        # The will is what makes a dead device visible: the broker publishes it
        # with retain when the keepalive expires.
        set_will = getattr(self.client, "set_last_will", None)
        if set_will is not None:
            set_will(self.topic("state"), self._state_payload(False), qos=1, retain=True)

        self.client.set_callback(self._on_message)
        self.client.connect()
        self.client.subscribe(self.topic("ack"), qos=1)
        self.client.publish(self.topic("state"), self._state_payload(True), qos=1, retain=True)
        self._backoff = RECONNECT_MIN_S
        self._connected_at = self._now()
        return True

    def start(self):
        try:
            return self.connect()
        except Exception as error:  # noqa: BLE001 - the loop must keep sampling
            print("[mqtt] connect failed:", error)
            self._schedule_retry()
            return False

    def _schedule_retry(self):
        self._next_attempt = self._now() + self._backoff
        self._backoff = min(self._backoff * 2, RECONNECT_MAX_S)

    def _now(self):
        try:
            return int(self.clock.time())
        except (AttributeError, TypeError):  # pragma: no cover
            return 0

    # -- data path --------------------------------------------------------

    def enqueue(self, frame):
        """Add one ``[uptime_ms, temps, faults]`` frame."""
        self.buffer.append(frame)

        if len(self.buffer) > self.buffer_limit:
            overflow = len(self.buffer) - self.buffer_limit
            del self.buffer[:overflow]
            self.sent = max(0, self.sent - overflow)
            self.dropped += overflow

        if len(self.buffer) - self.sent >= self.batch_size:
            self.flush()

    def flush(self):
        """Publish the oldest unacknowledged batch. Returns frames sent."""
        if len(self.buffer) - self.sent <= 0:
            return 0

        if self.client is None:
            if self._now() < self._next_attempt:
                return 0
            if not self.start():
                return 0

        frames = self.buffer[self.sent : self.sent + self.batch_size]
        self.seq += 1
        payload = encode_batch(self.site, self.device, self.seq, frames, at=now_ms())

        try:
            self.client.publish(self.topic("samples"), payload, qos=1)
        except Exception as error:  # noqa: BLE001
            print("[mqtt] publish failed:", error)
            self.seq -= 1
            self._drop_connection()
            self._schedule_retry()
            return 0

        self.sent += len(frames)
        self.published += len(frames)
        return len(frames)

    def poll(self):
        """Handle incoming acks and keepalive. Never raises into the sampler."""
        if self.client is None:
            if self._now() >= self._next_attempt:
                self.start()
            return

        try:
            self.client.check_msg()
            return

        except Exception as error:  # noqa: BLE001
            print("[mqtt] connection lost:", error)
            self._drop_connection()
            self._schedule_retry()

    def _on_message(self, topic, payload):
        leaf = topic.decode() if isinstance(topic, bytes) else topic

        if not leaf.endswith("/" + "ack"):
            return

        try:
            body = json.loads(payload)
        except Exception:  # noqa: BLE001 - a malformed ack must not kill the loop
            return

        seq = int(body.get("seq", -1))
        accepted = int(body.get("accepted", 0))

        if seq < 0 or seq <= self.last_acked_seq:
            return

        self.last_acked_seq = seq

        if body.get("ok"):
            # Batches are published in order, so the oldest accepted frames are
            # exactly the ones this ack covers.
            del self.buffer[:accepted]
            self.sent = max(0, self.sent - accepted)

    def _drop_connection(self):
        try:
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None
