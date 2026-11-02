#!/usr/bin/env python3
"""MQTT → furnace API bridge.

Subscribes to ``furnace/<site>/<device>/samples``, validates each batch and
forwards it to ``POST /api/ingest`` so history, CSV export, analysis and the
existing dashboard keep working unchanged. After a batch is stored the bridge
publishes an acknowledgement on ``furnace/<site>/<device>/ack``; the device only
drops a frame once that arrives, so a broker hiccup or an API restart never
loses a reading.

Design notes
------------
* One MQTT client, one HTTP poster, one bounded queue. Sampling devices only
  touch the broker, so an API outage cannot slow down a furnace.
* The queue is bounded on purpose: an unbounded one turns a week-long API
  outage into an out-of-memory crash. When it is full the oldest batch is
  dropped and counted, which is visible in the retained status topic.
* Deduplication uses the device ``seq``. A retried QoS 1 delivery has the same
  ``seq`` and is acknowledged without a second write.
* The module imports ``paho.mqtt`` lazily so the test suite runs on a plain
  Python 3.11+ interpreter with no third-party packages installed.

Run ``python3 furnace_bridge.py --help`` for the command line, or use the
config file: ``python3 furnace_bridge.py --config config.toml``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import ssl
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("furnace.bridge")

ROOT = "furnace"
SAMPLES_LEAF = "samples"
ACK_LEAF = "ack"
STATUS_LEAF = "_bridge/status"
PROTOCOL_VERSION = 1
MAX_FRAMES = 600           # matches the API's max_batch_samples
MAX_FRAME_MS = 120_000     # a frame older than two minutes is not live data
MIN_TEMP = -999.0          # the firmware's fault sentinel is -1000
MAX_TEMP = 1400.0

Publish = Callable[[str, bytes, int, bool], None]


class PayloadError(ValueError):
    """The topic or payload is not something this bridge can forward."""


class PermanentError(RuntimeError):
    """The API rejected the batch; retrying cannot help."""


class RetryableError(RuntimeError):
    """The API is down or unreachable; keep the batch and try again."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 1883
    tls: bool = False
    ca_file: str | None = None
    username: str | None = None
    password: str | None = None
    client_id: str = "furnace-bridge"
    site: str = "lab"
    api_url: str = "http://127.0.0.1:8000/api/ingest"
    api_key: str = ""
    queue_limit: int = 500
    request_timeout: float = 10.0
    status_interval: float = 15.0
    ack_qos: int = 1
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "Config":
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Config":
        broker = data.get("broker", {})
        api = data.get("api", {})
        bridge = data.get("bridge", {})

        return cls(
            host=str(broker.get("host", cls.host)),
            port=int(broker.get("port", cls.port)),
            tls=bool(broker.get("tls", cls.tls)),
            ca_file=broker.get("ca_file") or None,
            username=broker.get("username") or None,
            password=broker.get("password") or None,
            client_id=str(bridge.get("client_id", cls.client_id)),
            site=str(broker.get("site", cls.site)),
            api_url=str(api.get("url", cls.api_url)),
            api_key=str(api.get("key", cls.api_key)),
            queue_limit=int(bridge.get("queue_limit", cls.queue_limit)),
            request_timeout=float(bridge.get("request_timeout", cls.request_timeout)),
            status_interval=float(bridge.get("status_interval", cls.status_interval)),
            ack_qos=int(bridge.get("ack_qos", cls.ack_qos)),
            log_level=str(bridge.get("log_level", cls.log_level)),
        )

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        config = cls()

        def take(name: str, cast: Callable[[str], Any]) -> Any:
            raw = env.get(f"FURNACE_MQTT_{name}")
            return None if raw is None or raw == "" else cast(raw)

        for name, attribute, cast in (
            ("HOST", "host", str),
            ("PORT", "port", int),
            ("TLS", "tls", lambda value: value.lower() in {"1", "true", "yes", "on"}),
            ("CA_FILE", "ca_file", str),
            ("USERNAME", "username", str),
            ("PASSWORD", "password", str),
            ("CLIENT_ID", "client_id", str),
            ("SITE", "site", str),
            ("API_URL", "api_url", str),
            ("API_KEY", "api_key", str),
            ("QUEUE_LIMIT", "queue_limit", int),
            ("REQUEST_TIMEOUT", "request_timeout", float),
            ("STATUS_INTERVAL", "status_interval", float),
            ("LOG_LEVEL", "log_level", str),
        ):
            value = take(name, cast)
            if value is not None:
                setattr(config, attribute, value)

        return config


# --------------------------------------------------------------------------
# Topic and payload parsing
# --------------------------------------------------------------------------

def topic_for(site: str, device: str, leaf: str) -> str:
    return f"{ROOT}/{site}/{device}/{leaf}"


def parse_topic(topic: str) -> tuple[str, str]:
    """Return ``(site, device)`` for a samples/ack topic, else raise."""
    parts = topic.split("/")

    if len(parts) != 4 or parts[0] != ROOT or parts[3] not in {SAMPLES_LEAF, ACK_LEAF}:
        raise PayloadError(f"unexpected topic {topic!r}")

    site, device = parts[1], parts[2]

    if not site or not device:
        raise PayloadError(f"empty site or device in {topic!r}")

    return site, device


def clean_temp(value: Any, fault: int) -> float | None:
    """Return a usable temperature, or ``None`` for a faulted channel.

    The firmware reports -1000 for a faulted MAX31855 channel and also sets the
    fault bit; either signal is enough. ``None`` is what the API stores, and it
    is what the dashboard renders as *held* rather than as a number.
    """
    if fault:
        return None

    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number) or number <= MIN_TEMP or number > MAX_TEMP:
        return None

    return round(number, 3)


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise PayloadError(f"{field_name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise PayloadError(f"{field_name} must be an integer") from error
    return number


def parse_payload(payload: bytes | str, device: str) -> tuple[int, list[dict[str, Any]]]:
    """Validate a device batch and convert it to API samples.

    Expected JSON::

        {"v": 1, "seq": 42, "at": 1730000000000,
         "frames": [[uptime_ms, [t1..t4], [f1..f4]], ...]}

    ``at`` is the wall clock of the batch's **last** frame (the moment it was
    published). Earlier frames are placed by their uptime distance from it, so
    a 1 s batch keeps its 0.1 s spacing. Without ``at`` the API anchors the
    batch on arrival, which is what the firmware does while NTP is unsynced.
    """
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PayloadError("payload is not UTF-8") from error

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise PayloadError(f"payload is not JSON: {error.msg}") from error

    if not isinstance(data, dict):
        raise PayloadError("payload must be a JSON object")
    if _as_int(data.get("v", PROTOCOL_VERSION), "v") != PROTOCOL_VERSION:
        raise PayloadError("unsupported protocol version")

    seq = _as_int(data.get("seq"), "seq")
    if seq < 0:
        raise PayloadError("seq must be >= 0")

    frames = data.get("frames")
    if not isinstance(frames, list) or not frames:
        raise PayloadError("frames must be a non-empty list")
    if len(frames) > MAX_FRAMES:
        raise PayloadError(f"at most {MAX_FRAMES} frames per batch")

    absolute = data.get("at")
    if absolute is not None:
        absolute = _as_int(absolute, "at")
        if absolute <= 0:
            raise PayloadError("at must be a positive Unix time in ms")

    samples: list[dict[str, Any]] = []
    last_uptime = _as_int(frames[-1][0], "frame uptime")

    for index, frame in enumerate(frames):
        if not isinstance(frame, (list, tuple)) or len(frame) != 3:
            raise PayloadError(f"frame {index} must be [uptime_ms, temps, faults]")

        uptime = _as_int(frame[0], f"frame {index} uptime")
        if uptime < 0 or uptime > MAX_FRAME_MS * 1000:
            raise PayloadError(f"frame {index} uptime out of range")

        temps, faults = frame[1], frame[2]

        if not isinstance(temps, (list, tuple)) or len(temps) != 4:
            raise PayloadError(f"frame {index} needs 4 temperatures")
        if not isinstance(faults, (list, tuple)) or len(faults) != 4:
            raise PayloadError(f"frame {index} needs 4 fault codes")

        fault_codes = [_as_int(code, "fault") for code in faults]
        if any(code < 0 or code > 255 for code in fault_codes):
            raise PayloadError(f"frame {index} fault code out of range")

        sample: dict[str, Any] = {
            "u": uptime,
            "t": [clean_temp(temps[channel], fault_codes[channel]) for channel in range(4)],
            "f": fault_codes,
        }

        if absolute is not None:
            # `at` is the publish time of the last frame; earlier frames sit
            # that many uptime milliseconds before it.
            sample["at"] = absolute - (last_uptime - uptime)

        samples.append(sample)

    LOG.debug("accepted %s frames from %s seq=%s", len(samples), device, seq)
    return seq, samples


# --------------------------------------------------------------------------
# Bounded queue
# --------------------------------------------------------------------------

class BoundedQueue:
    """Thread-safe bounded FIFO that drops the oldest item when full."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._items: list[Any] = []
        self._lock = threading.Condition()
        self._closed = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def push(self, item: Any) -> bool:
        """Queue an item; returns ``False`` when the oldest one was dropped."""
        with self._lock:
            dropped = False
            if len(self._items) >= self.limit:
                self._items.pop(0)
                dropped = True
            self._items.append(item)
            self._lock.notify()
            return not dropped

    def pop(self, timeout: float = 0.5) -> Any | None:
        with self._lock:
            if not self._items:
                self._lock.wait(timeout)
            if not self._items:
                return None
            return self._items.pop(0)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed


# --------------------------------------------------------------------------
# HTTP poster
# --------------------------------------------------------------------------

class ApiPoster:
    """POSTs sample batches to the furnace API and classifies failures."""

    def __init__(self, url: str, api_key: str = "", timeout: float = 10.0) -> None:
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def post(self, samples: list[dict[str, Any]]) -> int:
        body = json.dumps({"samples": samples}, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")

        if self.api_key:
            request.add_header("X-Api-Key", self.api_key)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            detail = _safe_body(error)
            message = f"API {error.code}: {detail}"

            # A rejected payload cannot be fixed by retrying; an auth problem
            # can (the key is rotated), so it stays queued.
            if error.code in (401, 403, 429) or error.code >= 500:
                raise RetryableError(message) from error

            raise PermanentError(message) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RetryableError(f"API unreachable: {error}") from error

        accepted = payload.get("accepted", len(samples))
        return int(accepted)


def _safe_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001 - diagnostics must never mask the error
        return error.reason or "no body"


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------

@dataclass
class Stats:
    received: int = 0
    forwarded: int = 0
    samples: int = 0
    duplicates: int = 0
    rejected: int = 0
    dropped: int = 0
    failures: int = 0
    last_error: str | None = None
    started_at: float = field(default_factory=time.time)

    def status(self, queue_depth: int, online: bool) -> dict[str, Any]:
        return {
            "online": online,
            "queued": queue_depth,
            "received": self.received,
            "forwarded": self.forwarded,
            "samples": self.samples,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "failures": self.failures,
            "last_error": self.last_error,
            "uptime_s": round(time.time() - self.started_at, 1),
        }


class Bridge:
    """MQTT messages in, API batches out, acknowledgements back."""

    def __init__(
        self,
        config: Config,
        poster: ApiPoster,
        publish: Publish | None = None,
    ) -> None:
        self.config = config
        self.poster = poster
        self.publish = publish or (lambda *_: None)
        self.queue = BoundedQueue(config.queue_limit)
        self.stats = Stats()
        self.last_seq: dict[str, int] = {}
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    # -- messaging --------------------------------------------------------

    def handle_message(self, topic: str, payload: bytes) -> None:
        try:
            site, device = parse_topic(topic)
        except PayloadError as error:
            self.stats.rejected += 1
            self.stats.last_error = str(error)
            LOG.warning("%s", error)
            return

        if f"{ROOT}/{site}" != f"{ROOT}/{self.config.site}":
            # Wildcard subscription in one broker may carry other sites; this
            # bridge only owns the site it was configured for.
            LOG.debug("ignoring topic for another site: %s", topic)
            return

        self.stats.received += 1

        try:
            seq, samples = parse_payload(payload, device)
        except PayloadError as error:
            self.stats.rejected += 1
            self.stats.last_error = str(error)
            LOG.warning("rejected %s: %s", topic, error)
            self._ack(site, device, None, False, str(error))
            return

        if not self._accept_seq(device, seq):
            self.stats.duplicates += 1
            LOG.debug("duplicate seq %s from %s", seq, device)
            self._ack(site, device, seq, True, None)
            return

        if not self.queue.push({"site": site, "device": device, "seq": seq, "samples": samples}):
            self.stats.dropped += 1
            self.stats.last_error = "queue full, oldest batch dropped"
            LOG.error("queue full: dropping the oldest batch")

    def _accept_seq(self, device: str, seq: int) -> bool:
        last = self.last_seq.get(device)

        if last is not None and last - 1000 < seq <= last:
            return False

        self.last_seq[device] = seq
        return True

    def _ack(
        self,
        site: str,
        device: str,
        seq: int | None,
        ok: bool,
        error: str | None,
        accepted: int = 0,
    ) -> None:
        if seq is None:
            return

        body = json.dumps(
            {"v": PROTOCOL_VERSION, "seq": seq, "ok": ok, "accepted": accepted, "error": error},
            separators=(",", ":"),
        ).encode("utf-8")
        self.publish(topic_for(site, device, ACK_LEAF), body, self.config.ack_qos, False)

    def publish_status(self, online: bool = True) -> None:
        body = json.dumps(
            self.stats.status(len(self.queue), online), separators=(",", ":")
        ).encode("utf-8")
        self.publish(f"{ROOT}/{self.config.site}/{STATUS_LEAF}", body, 0, True)

    # -- delivery ---------------------------------------------------------

    def flush_once(self) -> bool:
        """Deliver one batch. Returns ``False`` when the queue is empty."""
        batch = self.queue.pop(timeout=0.5)

        if batch is None:
            return False

        try:
            accepted = self.poster.post(batch["samples"])
        except PermanentError as error:
            self.stats.failures += 1
            self.stats.last_error = str(error)
            LOG.error("API refused seq=%s from %s: %s", batch["seq"], batch["device"], error)
            self._ack(batch["site"], batch["device"], batch["seq"], False, str(error))
            return True
        except RetryableError as error:
            self.stats.failures += 1
            self.stats.last_error = str(error)
            LOG.warning("API unavailable, keeping seq=%s: %s", batch["seq"], error)
            # Put it back at the front so ordering per device is preserved.
            self.queue.push(batch)
            raise
        else:
            self.stats.forwarded += 1
            self.stats.samples += accepted
            self._ack(batch["site"], batch["device"], batch["seq"], True, None, accepted)
            LOG.info(
                "stored %s samples from %s seq=%s",
                accepted,
                batch["device"],
                batch["seq"],
            )
            return True

    def run_forever(self) -> None:
        self._worker = threading.Thread(target=self._deliver_loop, name="bridge-worker", daemon=True)
        self._worker.start()

        last_status = 0.0

        while not self._stop.is_set():
            now = time.monotonic()

            if now - last_status >= self.config.status_interval:
                self.publish_status(True)
                last_status = now

            self._stop.wait(0.5)

        self.queue.close()

        if self._worker:
            self._worker.join(timeout=2.0)

    def _deliver_loop(self) -> None:
        backoff = 1.0

        while not self._stop.is_set():
            try:
                delivered = self.flush_once()
                backoff = 1.0
                if not delivered:
                    continue
            except RetryableError:
                self._stop.wait(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)

        # A short drain keeps a restart from dropping fresh data.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(self.queue):
            try:
                self.flush_once()
            except RetryableError:
                break

    def stop(self) -> None:
        self._stop.set()
        self.queue.close()


# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------

def build_client(config: Config, bridge: Bridge):
    """Create the paho client. Imported here so tests need no dependencies."""
    import paho.mqtt.client as mqtt

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        protocol=mqtt.MQTTv311,
    )

    if config.username:
        client.username_pw_set(config.username, config.password or None)

    if config.tls:
        client.tls_set(ca_certs=config.ca_file, tls_version=ssl.PROTOCOL_TLS_CLIENT)

    status_topic = f"{ROOT}/{config.site}/{STATUS_LEAF}"
    client.will_set(
        status_topic,
        json.dumps({"online": False, "client_id": config.client_id}),
        qos=1,
        retain=True,
    )

    def on_connect(client_, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            LOG.error("broker rejected the connection: %s", reason_code)
            return
        LOG.info("connected to %s:%s", config.host, config.port)
        client_.subscribe(f"{ROOT}/{config.site}/+/{SAMPLES_LEAF}", qos=1)
        bridge.publish_status(True)

    def on_disconnect(client_, userdata, flags, reason_code, properties=None):
        LOG.warning("disconnected from the broker: %s", reason_code)

    def on_message(client_, userdata, message):
        bridge.handle_message(message.topic, message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    bridge.publish = lambda topic, payload, qos, retain: client.publish(
        topic, payload, qos=qos, retain=retain
    )

    return client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge MQTT furnace samples into the HTTP API")
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    parser.add_argument("--host", help="broker host")
    parser.add_argument("--port", type=int, help="broker port")
    parser.add_argument("--site", help="site name used in topics")
    parser.add_argument("--api-url", help="POST /api/ingest endpoint")
    parser.add_argument("--dry-run", action="store_true", help="log batches, never post")
    args = parser.parse_args(argv)

    config = Config.from_env()

    if args.config:
        config = Config.from_file(args.config)

    for attribute, value in (
        ("host", args.host),
        ("port", args.port),
        ("site", args.site),
        ("api_url", args.api_url),
    ):
        if value is not None:
            setattr(config, attribute, value)

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    class DryRunPoster(ApiPoster):
        def post(self, samples: list[dict[str, Any]]) -> int:
            LOG.info("dry run: would store %s samples", len(samples))
            return len(samples)

    poster: ApiPoster = DryRunPoster(config.api_url) if args.dry_run else ApiPoster(
        config.api_url, config.api_key, config.request_timeout
    )
    bridge = Bridge(config, poster)
    client = build_client(config, bridge)

    client.connect(config.host, config.port, keepalive=60)
    client.loop_start()

    try:
        bridge.run_forever()
    except KeyboardInterrupt:
        LOG.info("stopping")
    finally:
        bridge.stop()
        bridge.publish_status(False)
        client.loop_stop()
        client.disconnect()

    LOG.info(
        "forwarded=%s samples=%s dropped=%s rejected=%s duplicates=%s",
        bridge.stats.forwarded,
        bridge.stats.samples,
        bridge.stats.dropped,
        bridge.stats.rejected,
        bridge.stats.duplicates,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
