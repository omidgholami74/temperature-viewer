"""Publish the sample queue to an MQTT broker instead of POSTing to the API.

A drop-in for ``ingest.Ingest``: ``add()``, ``flush()``, ``poll()``,
``stats()`` and ``alive()`` keep the same meaning, so switching a board over is
one import and two lines in ``main.py`` (see ``docs/mqtt-setup.md``).

The message is the v2 telemetry envelope from ``docs/mqtt-protocol.md``, and
the server-side bridge (``backend/mqtt/bridge.py``) turns it back into the same
``POST /api/ingest`` request, so the database and the dashboard do not change.

``umqtt.simple`` is frozen into the stock ESP32-S3 build. If the board reports
``ImportError: no module named 'umqtt'``, install it once with the board
connected:

    mpremote connect COM6 mip install umqtt.simple
"""

import json
import time

try:
    from umqtt.simple import MQTTClient
except ImportError:  # a bare build; start() reports it instead of crashing
    MQTTClient = None

try:
    import ubinascii
except ImportError:  # CPython, used by the host tests
    ubinascii = None

try:
    import network
except ImportError:  # the host tests load this file without a board
    network = None

try:
    import os
except ImportError:  # pragma: no cover
    os = None

MAX_FRAMES = 600  # the API rejects more than this per request
CHANNELS = 4
# One status heartbeat every minute, as the protocol asks for. Without it a
# consumer that subscribes late sees only the retained message from connect.
STATUS_INTERVAL_S = 60
QOS = 1
FIRMWARE = "esp32-s3-furnace/1.2.0"


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()

    return int(time.monotonic() * 1000)


def _ticks_diff(new, old):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(new, old)

    return new - old


def _sleep_ms(milliseconds):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def _boot_id():
    """Six hex characters, new on every boot: sequences restart with it."""
    if ubinascii is None or os is None:
        return "%06x" % (_ticks_ms() & 0xFFFFFF,)

    try:
        return ubinascii.hexlify(os.urandom(3)).decode()
    except Exception:
        return "%06x" % (_ticks_ms() & 0xFFFFFF,)


def _clean(value):
    """A temperature for JSON: ``None`` for a fault, otherwise two decimals."""
    if value is None:
        return None

    number = float(value)

    if number != number:  # NaN means the channel reported a fault
        return None

    return round(number, 2)


def _clean_fault(value):
    try:
        return int(value) & 0xFF
    except (TypeError, ValueError):
        return 8


class MqttUplink:
    """Buffer frames and publish them in one-second telemetry messages."""

    def __init__(self, config, log=print):
        self.log = log
        self.device_id = str(getattr(config, "MQTT_DEVICE_ID", "") or "")

        if not self.device_id:
            raise ValueError("MQTT_DEVICE_ID is not set in config.py")

        self.site = str(getattr(config, "MQTT_SITE", "") or "site")
        self.prefix = "factory/%s/%s" % (self.site, self.device_id)
        self.topic_telemetry = self.prefix + "/telemetry"
        self.topic_status = self.prefix + "/status"
        self.topic_event = self.prefix + "/event"
        self.topic_cmd = self.prefix + "/cmd"
        self.topic_ack = self.prefix + "/cmd/ack"

        self.host = str(getattr(config, "MQTT_HOST", "") or "")

        if not self.host:
            raise ValueError("MQTT_HOST is not set in config.py")

        self.port = int(getattr(config, "MQTT_PORT", 1883))
        self.user = getattr(config, "MQTT_USER", None) or self.device_id
        self.password = getattr(config, "MQTT_PASSWORD", "") or ""
        self.use_tls = bool(getattr(config, "MQTT_SSL", False))
        self.keepalive = int(getattr(config, "MQTT_KEEPALIVE_S", 30))
        self.interval_ms = int(getattr(config, "SAMPLE_INTERVAL_MS", 100))
        self.channels = int(getattr(config, "CHANNELS", CHANNELS))
        self.status_interval_s = int(
            getattr(config, "MQTT_STATUS_INTERVAL_S", STATUS_INTERVAL_S)
        )

        batch = int(getattr(config, "BATCH_SIZE", 10))
        self.batch = max(1, min(batch, MAX_FRAMES))
        limit = int(getattr(config, "BUFFER_LIMIT", 600))
        self.limit = max(self.batch, limit)

        self.boot_id = _boot_id()
        self.seq = 0
        self.pending = []
        self.sent = 0
        self.failures = 0
        self.dropped = 0
        self.last_error = ""
        self.client = None
        self.connected = False
        self._started_ms = _ticks_ms()
        self._last_status_ms = 0
        self._last_trim_log = 0

    # -- lifecycle --------------------------------------------------------

    def start(self):
        """Connect, publish ``online`` and subscribe to commands."""
        if MQTTClient is None:
            self.log("MQTT: umqtt.simple is missing (mip install umqtt.simple)")
            return False

        if not self._connect():
            return False

        self.publish_status("online")
        self.log(
            "MQTT: connected to %s:%d as %s/%s (boot %s)"
            % (self.host, self.port, self.site, self.device_id, self.boot_id)
        )

        return True

    def stop(self):
        """Publish what is queued, then ``offline`` and disconnect cleanly."""
        if self.client is not None:
            self.flush(everything=True)
            self.publish_status("offline")

            try:
                self.client.disconnect()
            except Exception:
                pass

        self._close()
        self.log("MQTT: stopped (%s)" % (self.stats(),))

    def alive(self):
        """True while the broker connection is up.

        ``flush()`` reconnects on its own, so a False here is not fatal: it is
        the signal the supervisor uses to log a reconnection.
        """
        return self.connected

    def _close(self):
        client = self.client
        self.client = None
        self.connected = False

        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def _connect(self):
        if self.client is not None:
            self._close()

        will = json.dumps(
            {
                "v": 2,
                "device_id": self.device_id,
                "state": "offline",
                "boot_id": self.boot_id,
                "fw": FIRMWARE,
            }
        )

        try:
            client = MQTTClient(
                client_id="%s-%s-%s" % (self.site, self.device_id, self.boot_id),
                server=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                keepalive=self.keepalive,
                ssl=self.use_tls,
            )
            # The broker publishes this for us if the board vanishes: a dead
            # board looks dead within the keep-alive, not after an hour.
            client.set_last_will(self.topic_status, will, retain=True, qos=QOS)
            client.set_callback(self._on_message)
            client.connect()
            client.subscribe(self.topic_cmd, qos=QOS)
        except Exception as error:
            self.failures += 1
            self.last_error = str(error)
            self.log("MQTT: connect failed: %s" % (error,))
            self._close()
            return False

        self.client = client
        self.connected = True
        self._last_status_ms = _ticks_ms()

        return True

    # -- queue ------------------------------------------------------------

    def add(self, uptime_ms, temperatures, faults, at_ms=None):
        """Queue one frame; a full batch is published by ``flush()``."""
        frame = {
            "u": int(uptime_ms),
            "t": [_clean(value) for value in temperatures],
            "f": [_clean_fault(value) for value in faults],
        }

        if at_ms is not None:
            frame["at"] = int(at_ms)

        self.pending.append(frame)

        if len(self.pending) > self.limit:
            overflow = len(self.pending) - self.limit
            # The oldest frames go first: a gap with a known size is better
            # than a queue that never drains.
            self.pending = self.pending[overflow:]
            self.dropped += overflow
            self._note_drop(overflow)

        return len(self.pending) >= self.batch

    def _note_drop(self, count):
        now = _ticks_ms()

        if _ticks_diff(now, self._last_trim_log) < 1000:
            return

        self._last_trim_log = now
        self.log("MQTT: buffer full, dropped %d oldest frame(s)" % (count,))

    def flush(self, everything=False):
        """Publish queued batches. Returns True when nothing is left waiting.

        ``everything`` publishes a short tail too; the periodic call only sends
        complete batches, so one message stays one second of data.
        """
        if not self.pending:
            return True

        if not self.connected and not self._connect():
            return False

        while self.pending and (everything or len(self.pending) >= self.batch):
            chunk = self.pending[: self.batch]

            if not self._publish(chunk):
                return False

            self.pending = self.pending[len(chunk) :]

        self._heartbeat()

        return not self.pending

    def tick(self):
        """Called from the sampling loop: keep the connection fed."""
        if not self.connected:
            return self.flush()

        self.poll()
        self._heartbeat()

        return True

    def _heartbeat(self):
        if not self.connected or self.status_interval_s <= 0:
            return

        if _ticks_diff(_ticks_ms(), self._last_status_ms) >= self.status_interval_s * 1000:
            self.publish_status("online")

    # -- publishing -------------------------------------------------------

    def _publish(self, chunk):
        base = chunk[0].get("at")
        frames = []

        for frame in chunk:
            frames.append({"u": frame["u"], "t": frame["t"], "f": frame["f"]})

        message = {
            "v": 2,
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "seq": self.seq,
            "at": base,
            "source": "ntp" if base else "uptime",
            "interval_ms": self.interval_ms,
            "rssi": self._rssi(),
            "frames": frames,
        }

        try:
            self.client.publish(self.topic_telemetry, json.dumps(message), qos=QOS)
        except Exception as error:
            self.failures += 1
            self.last_error = str(error)
            self.log("MQTT: publish failed: %s" % (error,))
            # A broken socket is the usual cause; the next flush reconnects and
            # the frames in this chunk are still at the head of the queue.
            self._close()
            return False

        self.seq += 1
        self.sent += len(chunk)
        self.failures = 0
        self.last_error = ""

        return True

    def publish_status(self, state):
        """Retained ``status`` document: what the dashboard's device list reads."""
        if self.client is None:
            return False

        message = {
            "v": 2,
            "device_id": self.device_id,
            "state": state,
            "boot_id": self.boot_id,
            "fw": FIRMWARE,
            "interval_ms": self.interval_ms,
            "channels": self.channels,
            "rssi": self._rssi(),
            "uptime_ms": _ticks_diff(_ticks_ms(), self._started_ms),
            "ts": int(time.time() * 1000) if _clock_is_set() else None,
        }

        try:
            self.client.publish(self.topic_status, json.dumps(message), retain=True, qos=QOS)
        except Exception as error:
            self.log("MQTT: status publish failed: %s" % (error,))
            self._close()
            return False

        self._last_status_ms = _ticks_ms()

        return True

    def publish_event(self, kind, detail=None, channel=None):
        """A discrete event worth keeping (sensor fault, buffer overflow, ...)."""
        if self.client is None:
            return False

        message = {"v": 2, "device_id": self.device_id, "kind": kind}

        if channel is not None:
            message["channel"] = int(channel)

        if detail is not None:
            message["detail"] = str(detail)

        if _clock_is_set():
            message["ts"] = int(time.time() * 1000)

        try:
            self.client.publish(self.topic_event, json.dumps(message), qos=QOS)
        except Exception as error:
            self.log("MQTT: event publish failed: %s" % (error,))
            self._close()
            return False

        return True

    # -- commands ---------------------------------------------------------

    def poll(self):
        """Handle one pending command, if any (never blocks for long)."""
        if not self.connected:
            return

        try:
            self.client.check_msg()
        except Exception as error:
            self.log("MQTT: receive failed: %s" % (error,))
            self._close()

    def _on_message(self, topic, payload):
        try:
            command = json.loads(payload)
        except Exception:
            self.log("MQTT: ignoring a command that is not JSON")
            return

        if not isinstance(command, dict):
            return

        cmd_id = command.get("cmd_id")
        op = command.get("op")

        if op == "ping":
            self._ack(cmd_id, True, applied={"ping": "pong"})
            self.publish_status("online")
            return

        # Applying a new rate or offset means rewriting config.py on flash; do
        # that deliberately rather than halfway, and say so instead of lying
        # with an ok ack.
        self._ack(cmd_id, False, error="unsupported op %r on this firmware" % (op,))

    def _ack(self, cmd_id, ok, error=None, applied=None):
        if self.client is None:
            return

        message = {"v": 2, "cmd_id": cmd_id, "ok": bool(ok)}

        if applied is not None:
            message["applied"] = applied

        if error is not None:
            message["error"] = error

        try:
            self.client.publish(self.topic_ack, json.dumps(message), qos=QOS)
        except Exception as exception:
            self.log("MQTT: ack failed: %s" % (exception,))

    # -- misc -------------------------------------------------------------

    def _rssi(self):
        if network is None:
            return None

        try:
            return int(network.WLAN(network.STA_IF).status("rssi"))
        except Exception:
            return None

    def stats(self):
        return "sent=%d pending=%d dropped=%d failures=%d seq=%d" % (
            self.sent,
            len(self.pending),
            self.dropped,
            self.failures,
            self.seq,
        )


def _clock_is_set():
    """True once NTP has moved the clock past 2020 (1970 means 'no time')."""
    try:
        return time.time() > 1577836800
    except Exception:
        return False
