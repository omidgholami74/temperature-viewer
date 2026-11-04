#!/usr/bin/env python3
"""MQTT -> ``POST /api/ingest`` bridge.

A board publishes one second of frames on
``factory/{site}/{device_id}/telemetry`` (see ``docs/mqtt-protocol.md``). This
process turns each message back into the ingest request the PHP API already
validates and stores, so the database, the API and the dashboard need no
change: MQTT replaces the *transport*, not the data path. There is still
exactly one writer into ``readings``.

Run it next to the broker and the API:

    python3 backend/mqtt/bridge.py

Everything is environment-only, because a systemd service has nowhere to put
arguments (see ``infrastructure/mqtt/mqtt-bridge.env.example``):

    MQTT_HOST        broker host                    127.0.0.1
    MQTT_PORT        broker port                    1883
    MQTT_USERNAME    broker user                   (empty = anonymous)
    MQTT_PASSWORD    broker password               (empty)
    MQTT_TLS         "1" to connect with TLS         0
    MQTT_CAFILE      CA bundle for TLS              (system default)
    MQTT_SITE        only this site namespace       (empty = every site)
    API_URL          ingest endpoint                http://127.0.0.1/api/ingest
    API_KEY          must equal FURNACE_INGEST_KEY  (empty = API is open)
    API_TIMEOUT_S    per request timeout            10
    API_RETRIES      retries on 5xx/network          3
    MAX_FRAMES       frames accepted per message     600

What is deliberately *not* here yet: persisting ``status``/``event`` messages.
They need the ``devices``/``device_events`` tables from
``docs/database-schema.md``; until those exist the bridge logs them (state
changes and sequence gaps end up in the journal, not in SQLite).
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

# The API validates exactly this many channels; a device with fewer must send
# nulls (see docs/mqtt-protocol.md, "3-channel device").
CHANNELS = 4
TEMP_MIN = -270.0
TEMP_MAX = 1800.0
MAX_FAULT_FLAG = 15

# 32-bit uptime in ms, so a message is at most ~24 days old. Anything larger
# is a clock confusion, not data.
MAX_UPTIME_MS = 0x7FFFFFFF

_IDENT = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
_PAYLOAD_VERSION = 2


class Rejected(Exception):
    """A message the server refuses; retrying it would fail identically."""


def _is_int(value):
    """A JSON integer, not a bool and not a float with a fractional part."""
    return isinstance(value, int) and not isinstance(value, bool)


def _decode(payload):
    """JSON object from a raw MQTT payload, or a Rejected error."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Rejected("payload is not UTF-8: %s" % (error,))

    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise Rejected("payload is not JSON: %s" % (error,))

    if not isinstance(document, dict):
        raise Rejected("payload must be a JSON object")

    return document


def parse_telemetry(topic, payload, max_frames=600):
    """Validate one telemetry message and map it to ingest samples.

    Returns a dict with ``site``, ``device_id``, ``boot_id``, ``seq`` and
    ``samples``; each sample has the exact shape ``POST /api/ingest`` accepts
    (``u``, ``t``, ``f`` and, when the device knows its clock, ``at``).
    """
    parts = str(topic).split("/")

    if len(parts) != 4 or parts[0] != "factory" or parts[3] != "telemetry":
        raise Rejected("unexpected topic %r" % (topic,))

    site, topic_device = parts[1], parts[2]

    if not _IDENT.match(site):
        raise Rejected("invalid site %r" % (site,))

    if not _IDENT.match(topic_device):
        raise Rejected("invalid device_id %r" % (topic_device,))

    document = _decode(payload)

    if document.get("v") != _PAYLOAD_VERSION:
        raise Rejected(
            "unsupported payload version %r (this bridge speaks %d)"
            % (document.get("v"), _PAYLOAD_VERSION)
        )

    device_id = document.get("device_id")

    if device_id != topic_device:
        raise Rejected(
            "device_id %r does not match the topic %r" % (device_id, topic_device)
        )

    frames = document.get("frames")

    if not isinstance(frames, list) or not frames:
        raise Rejected('"frames" must be a non-empty list')

    if len(frames) > max_frames:
        raise Rejected(
            "too many frames in one message (%d > %d)" % (len(frames), max_frames)
        )

    base_at = document.get("at")

    if base_at is not None:
        if not _is_int(base_at) or base_at <= 0:
            raise Rejected('"at" must be a positive Unix time in ms or null')

    samples = []
    first_u = None

    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise Rejected("frames[%d] must be an object" % (index,))

        uptime = frame.get("u")

        if not _is_int(uptime) or uptime < 0 or uptime > MAX_UPTIME_MS:
            raise Rejected("frames[%d].u must be an uptime in ms" % (index,))

        if first_u is None:
            first_u = uptime

        temperatures = frame.get("t")

        if not isinstance(temperatures, list) or len(temperatures) != CHANNELS:
            raise Rejected(
                "frames[%d].t must contain exactly %d values" % (index, CHANNELS)
            )

        channels = []

        for channel, value in enumerate(temperatures):
            if value is None:
                channels.append(None)  # a faulted channel, never NaN
                continue

            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise Rejected(
                    "frames[%d].t[%d] must be a number or null" % (index, channel)
                )

            value = float(value)

            if value != value:  # NaN slipped past the JSON encoder
                raise Rejected(
                    "frames[%d].t[%d] is NaN; send null for a fault"
                    % (index, channel)
                )

            if value < TEMP_MIN or value > TEMP_MAX:
                raise Rejected(
                    "frames[%d].t[%d] is outside %g..%g C"
                    % (index, channel, TEMP_MIN, TEMP_MAX)
                )

            channels.append(round(value, 2))

        faults = frame.get("f", [0, 0, 0, 0])

        if not isinstance(faults, list) or len(faults) != CHANNELS:
            raise Rejected(
                "frames[%d].f must contain exactly %d flags" % (index, CHANNELS)
            )

        flags = []

        for channel, flag in enumerate(faults):
            if not _is_int(flag) or flag < 0 or flag > MAX_FAULT_FLAG:
                raise Rejected(
                    "frames[%d].f[%d] must be an integer 0..%d"
                    % (index, channel, MAX_FAULT_FLAG)
                )

            flags.append(flag)

        sample = {"u": uptime, "t": channels, "f": flags}

        if base_at is not None:
            # at is the time of the first frame; the rest follow the uptime
            # grid, which is what keeps a 0.1 s spacing across a batch.
            sample["at"] = max(1, base_at + (uptime - first_u))

        samples.append(sample)

    return {
        "site": site,
        "device_id": device_id,
        "boot_id": str(document.get("boot_id") or ""),
        "seq": document.get("seq"),
        "interval_ms": document.get("interval_ms"),
        "samples": samples,
    }


class SequenceTracker:
    """Which messages arrived, which are missing, which came twice.

    Sequence numbers only mean anything inside one boot, so the state is keyed
    by ``(device_id, boot_id)``. Memory is bounded: the oldest boot is dropped
    once ``max_boots`` are tracked, which at one boot per device is plenty.
    """

    def __init__(self, max_boots=64):
        self.max_boots = max(1, int(max_boots))
        self.seen = {}

    def observe(self, device_id, boot_id, seq):
        """Return ``(verdict, missing)`` for one message.

        Verdict is ``first``, ``ok``, ``gap``, ``duplicate``, ``reordered`` or
        ``unknown`` (no usable seq). ``missing`` is the number of messages the
        device published but the broker never delivered.
        """
        if not _is_int(seq) or seq < 0:
            return "unknown", 0

        key = (device_id, boot_id)
        entry = self.seen.get(key)

        if entry is None:
            self.seen[key] = {
                "last": seq,
                "gaps": 0,
                "duplicates": 0,
                "reordered": 0,
            }

            if len(self.seen) > self.max_boots:
                self.seen.pop(next(iter(self.seen)))

            return "first", 0

        last = entry["last"]

        if seq == last + 1:
            entry["last"] = seq
            return "ok", 0

        if seq == last:
            entry["duplicates"] += 1
            return "duplicate", 0

        if seq < last:
            # Arrived late but is not a repeat: worth knowing separately, since
            # it usually means two uplinks on the same board or a broker queue.
            entry["reordered"] += 1
            return "reordered", 0

        missing = seq - last - 1
        entry["last"] = seq
        entry["gaps"] += missing

        return "gap", missing

    def totals(self):
        gaps = sum(entry["gaps"] for entry in self.seen.values())
        duplicates = sum(entry["duplicates"] for entry in self.seen.values())
        reordered = sum(entry["reordered"] for entry in self.seen.values())

        return {
            "gaps": gaps,
            "duplicates": duplicates,
            "reordered": reordered,
            "boots": len(self.seen),
        }


class ApiForwarder:
    """POST a validated batch to the existing ingest endpoint.

    Retries cover a server that is restarting (5xx) and a network that dropped.
    A 4xx is *not* retried: the API understood the payload and refused it, so
    sending it again would produce the same answer forever.
    """

    DROP_STATUSES = (400, 413, 422)

    def __init__(
        self,
        url,
        key="",
        timeout=10.0,
        retries=3,
        backoff_s=1.0,
        log=print,
        sleep=time.sleep,
    ):
        self.url = url
        self.key = key or ""
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.backoff_s = float(backoff_s)
        self.log = log
        self.sleep = sleep
        self.sent = 0
        self.refused = 0
        self.failed = 0

    def post(self, samples):
        """Return ``(stored, status, detail)``; ``status`` is None on a timeout."""
        body = json.dumps({"samples": samples}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "furnace-mqtt-bridge/1.0",
        }

        if self.key:
            headers["X-Api-Key"] = self.key

        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(
                    self.url, data=body, headers=headers, method="POST"
                )

                with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                    detail = reply.read(4096).decode("utf-8", "replace")

                self.sent += len(samples)
                return True, 200, detail
            except urllib.error.HTTPError as error:
                status = error.code
                detail = error.read(4096).decode("utf-8", "replace")

                if status in self.DROP_STATUSES:
                    self.refused += len(samples)
                    self.log(
                        "mqtt: API %d refused %d samples: %s"
                        % (status, len(samples), detail[:160])
                    )
                    return False, status, detail

                if status in (401, 403):
                    self.log(
                        "mqtt: API %d — check API_KEY against FURNACE_INGEST_KEY"
                        % (status,)
                    )
                else:
                    self.log(
                        "mqtt: API %d, attempt %d/%d"
                        % (status, attempt + 1, self.retries + 1)
                    )
            except (urllib.error.URLError, OSError) as error:
                detail = str(error)
                self.log(
                    "mqtt: API unreachable, attempt %d/%d: %s"
                    % (attempt + 1, self.retries + 1, detail)
                )

            if attempt < self.retries:
                self.sleep(self.backoff_s * (attempt + 1))

        self.failed += len(samples)
        return False, None, "gave up after %d attempt(s)" % (self.retries + 1,)


class Bridge:
    """Route one MQTT message to the right handler."""

    def __init__(
        self,
        forwarder,
        max_frames=600,
        log=print,
        tracker=None,
        status_interval_s=0,
        clock=time.time,
    ):
        self.forwarder = forwarder
        self.max_frames = max(1, int(max_frames))
        self.log = log
        self.tracker = tracker or SequenceTracker()
        # The broker only sends a retained status when the consumer subscribes;
        # a second consumer after that point sees nothing until the device's
        # next heartbeat, so the distance between heartbeats is worth logging.
        self.status_interval_s = max(0, int(status_interval_s))
        self.clock = clock
        self.stats = {"telemetry": 0, "stored": 0, "rejected": 0, "failed": 0}
        self._device_state = {}

    def handle(self, topic, payload):
        """Process one message and return a short verdict (used by tests)."""
        topic = str(topic)
        kind = topic.rsplit("/", 1)[-1]

        if kind == "telemetry":
            return self._telemetry(topic, payload)

        if kind == "status":
            return self._status(topic, payload)

        if kind == "event":
            return self._event(topic, payload)

        return "ignored"

    def _telemetry(self, topic, payload):
        try:
            batch = parse_telemetry(topic, payload, self.max_frames)
        except Rejected as error:
            self.stats["rejected"] += 1
            self.log("mqtt: rejected %s: %s" % (topic, error))
            return "rejected"

        verdict, missing = self.tracker.observe(
            batch["device_id"], batch["boot_id"], batch["seq"]
        )

        if verdict == "gap":
            self.log(
                "mqtt: %s lost %d message(s) before seq %s"
                % (batch["device_id"], missing, batch["seq"])
            )
        elif verdict == "reordered":
            self.log(
                "mqtt: %s delivered seq %s after a newer one"
                % (batch["device_id"], batch["seq"])
            )

        stored, status, detail = self.forwarder.post(batch["samples"])
        self.stats["telemetry"] += 1

        if stored:
            self.stats["stored"] += len(batch["samples"])
            return "stored"

        self.stats["failed"] += 1
        self.log(
            "mqtt: %d samples from %s not stored (status %s): %s"
            % (len(batch["samples"]), batch["device_id"], status, detail[:160])
        )
        return "failed"

    def _status(self, topic, payload):
        """Log state changes. Persisting them needs the ``devices`` table."""
        parts = topic.split("/")
        device = parts[2] if len(parts) == 4 else "?"

        try:
            document = _decode(payload)
        except Rejected as error:
            self.log("mqtt: bad status on %s: %s" % (topic, error))
            return "rejected"

        state = str(document.get("state") or "?")
        previous = self._device_state.get(device)

        if previous != state:
            self._device_state[device] = state
            self.log(
                "mqtt: %s is %s (fw %s, rssi %s, boot %s)"
                % (
                    device,
                    state,
                    document.get("fw") or "?",
                    document.get("rssi", "?"),
                    document.get("boot_id") or "?",
                )
            )
        elif self.status_interval_s and document.get("ts"):
            age = self.clock() - float(document["ts"]) / 1000.0

            if age > 3 * self.status_interval_s:
                self.log(
                    "mqtt: %s heartbeat is %.0f s old" % (device, max(0.0, age))
                )

        return "status:" + state

    def _event(self, topic, payload):
        try:
            document = _decode(payload)
        except Rejected as error:
            self.log("mqtt: bad event on %s: %s" % (topic, error))
            return "rejected"

        parts = topic.split("/")
        device = parts[2] if len(parts) == 4 else "?"

        self.log(
            "mqtt: %s event %s%s"
            % (
                device,
                document.get("kind") or "?",
                " channel %s" % (document["channel"],)
                if "channel" in document
                else "",
            )
        )
        return "event:" + str(document.get("kind") or "?")

    def summary(self):
        """One line for the log on shutdown."""
        totals = self.tracker.totals()

        return (
            "telemetry=%d samples_stored=%d rejected=%d failed=%d "
            "gaps=%d duplicates=%d reordered=%d"
            % (
                self.stats["telemetry"],
                self.stats["stored"],
                self.stats["rejected"],
                self.stats["failed"],
                totals["gaps"],
                totals["duplicates"],
                totals["reordered"],
            )
        )


def _env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_flag(name, default=False):
    value = _env(name).strip().lower()

    if not value:
        return default

    return value in ("1", "true", "yes", "on")


def _topic_filter(site):
    """Subscriptions for one site or for every site on the broker."""
    base = "factory/%s/+/" % (site,) if site else "factory/+/+/"

    return [base + suffix for suffix in ("telemetry", "status", "event")]


def build_from_env(log=print):
    """The bridge as configured by the process environment."""
    forwarder = ApiForwarder(
        _env("API_URL", "http://127.0.0.1/api/ingest"),
        key=_env("API_KEY"),
        timeout=_env_int("API_TIMEOUT_S", 10),
        retries=_env_int("API_RETRIES", 3),
        log=log,
    )
    bridge = Bridge(
        forwarder,
        max_frames=_env_int("MAX_FRAMES", 600),
        log=log,
        status_interval_s=_env_int("MQTT_STATUS_INTERVAL_S", 60),
    )

    return bridge, _topic_filter(_env("MQTT_SITE").strip())


def main(argv=None):
    """Connect, subscribe and run until SIGINT/SIGTERM."""

    def log(message):
        print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message))

    try:
        from paho.mqtt import client as mqtt
    except ImportError:
        print(
            "paho-mqtt is missing: pip install paho-mqtt (see docs/mqtt-setup.md)",
            file=sys.stderr,
        )
        return 2

    host = _env("MQTT_HOST", "127.0.0.1")
    port = _env_int("MQTT_PORT", 1883)
    username = _env("MQTT_USERNAME")
    password = _env("MQTT_PASSWORD")
    bridge, topics = build_from_env(log)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="furnace-bridge-%d" % (os.getpid(),),
        protocol=mqtt.MQTTv311,
    )

    if username:
        client.username_pw_set(username, password or None)

    if _env_flag("MQTT_TLS"):
        client.tls_set(ca_certs=_env("MQTT_CAFILE") or None)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log("mqtt: connect refused: %s" % (reason_code,))
            return

        for topic in topics:
            client.subscribe(topic, qos=1)
            log("mqtt: subscribed %s" % (topic,))

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        log("mqtt: disconnected (%s); paho reconnects on its own" % (reason_code,))

    def on_message(client, userdata, message):
        bridge.handle(message.topic, message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # Back off between reconnects instead of hammering a broker that is down.
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def shutdown(signum, frame):
        log("mqtt: signal %d, stopping" % (signum,))
        client.disconnect()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log("mqtt: connecting to %s:%d" % (host, port))
    client.connect_async(host, port, keepalive=30)

    try:
        client.loop_forever(retry_first_connection=True)
    finally:
        log("mqtt: bridge stopped (%s)" % (bridge.summary(),))

    return 0


if __name__ == "__main__":
    sys.exit(main())
