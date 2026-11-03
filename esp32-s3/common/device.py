"""Heartbeat and remote commands for the ESP32-S3.

The board reports in every ``HEARTBEAT_S`` seconds: firmware version, IP, RSSI,
uptime and how much it has sent or buffered. The reply carries the commands an
operator queued in the admin panel:

* ``wifi``    -- save a new SSID/password to ``/wifi.json`` and reboot; the
                 next boot joins that network (``config.py`` is untouched).
* ``ota``     -- run the bundle check once; ``main.py`` reboots when it is staged.
* ``restart`` -- acknowledge and reboot.
* ``settings`` -- apply sample rate, noise filter, heartbeat interval and the
                 enabled/offset state of each furnace, then save
                 ``/settings.json`` so the change survives a reboot.

The heartbeat reply also carries the saved settings snapshot, which lets a
board that was reflashed recover the operator's configuration without a new
command.

A command stays queued on the server until it is acknowledged, so a board that
is offline for a while still receives it when it comes back.

The heartbeat runs in its own thread: a slow request cannot stretch the 100 ms
sampling grid.
"""

import json
import time

try:
    import gc
except ImportError:
    gc = None

try:
    import socket
except ImportError:  # the host tests never open a socket
    socket = None

import ingest

try:
    import ssl
except ImportError:  # a build without TLS still works over http://
    ssl = None

try:
    import _thread
except ImportError:  # no thread support: no heartbeat, sampling still works
    _thread = None

try:
    import machine
except ImportError:  # the host tests run without a board
    machine = None

MAX_REPLY_BYTES = 8192
RETRY_S = 10


def _sleep_ms(milliseconds):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()

    return int(time.monotonic() * 1000)


def _collect():
    """Release TLS/socket garbage before the next allocation."""
    if gc is None:
        return

    try:
        gc.collect()
    except Exception:
        pass


def _join(root, *parts):
    base = root.rstrip("/") if root else ""
    return base + "/" + "/".join(parts)


def _dechunk(raw):
    """Decode a chunked body already read into memory."""
    body = b""

    while True:
        line, _, raw = raw.partition(b"\r\n")

        try:
            size = int(line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            return body

        if size == 0:
            return body

        body += raw[:size]
        raw = raw[size + 2:]


def valid_id(value):
    """A device id is a short name that can appear in an API path."""
    if not isinstance(value, str) or not 1 <= len(value) <= 32:
        return False

    for char in value:
        if char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-":
            return False

    return True


class DeviceLink:
    """One heartbeat thread plus the command handlers it runs."""

    def __init__(self, config, wlan, ingest_client, updater=None, channels=None, log=print, root="", settings=None, source=""):
        self.log = log
        self.root = root
        self.wlan = wlan
        self.ingest = ingest_client
        self.updater = updater
        self.settings = settings
        self.source = str(source or getattr(config, "SOURCE", "") or "")
        self.channels = list(channels or [1, 2, 3])
        self.id = str(getattr(config, "DEVICE_ID", "esp32-1"))

        if not valid_id(self.id):
            raise ValueError("DEVICE_ID must be 1-32 letters, digits, _ or -")

        self.name = str(getattr(config, "DEVICE_NAME", "Furnace monitor"))
        self.heartbeat_s = max(5.0, float(getattr(config, "HEARTBEAT_S", 15)))
        self._sync_heartbeat()
        self.delay_s = max(0.0, float(getattr(config, "HEARTBEAT_DELAY_S", 5)))
        self.timeout = float(getattr(config, "API_TIMEOUT_S", 8))
        self.key = getattr(config, "API_KEY", "") or ""
        self.verify = bool(getattr(config, "TLS_VERIFY", False))
        host, port, path, use_tls = ingest.split_url(config.API_URL)
        self.host = host
        self.port = port
        self.use_tls = use_tls
        # Accept API_URL as either the host or the complete /api/ingest URL.
        self.prefix = ingest.api_base_path(path)
        self.acks = 0
        self.last_error = ""
        self._resolved = None
        self._connect_failures = 0
        self._worker = None
        self._stop = False

    # -- http -------------------------------------------------------------

    def _address(self, kind):
        return "%s/api/device/%s" % (self.prefix, kind)

    def _remote(self):
        """Cache the heartbeat address; DNS on every beat is slow and fragile."""
        if self._resolved is None:
            self._resolved = socket.getaddrinfo(self.host, self.port)[0][-1]

        return self._resolved

    def reset_network(self):
        """Forget a stale address after Wi-Fi comes back or a stall."""
        self._resolved = None
        self._connect_failures = 0

    def _post(self, path, payload):
        """Send one heartbeat/ack, sharing the TLS memory with the uploader."""
        lock = getattr(ingest, "NETWORK_LOCK", None)

        if lock is None:
            return self._post_network(path, payload)

        with lock:
            return self._post_network(path, payload)

    def _post_network(self, path, payload):
        if not hasattr(ingest, "parse_head"):
            raise OSError("this firmware build cannot speak HTTP")

        body = json.dumps(payload)
        lines = [
            "POST %s HTTP/1.1" % (path,),
            "Host: %s" % (self.host,),
            "Connection: close",
            "Content-Type: application/json",
            "Content-Length: %d" % (len(body),),
            "User-Agent: esp32-s3-furnace/1.0",
        ]

        if self.key:
            lines.append("X-Api-Key: %s" % (self.key,))

        if socket is None:
            raise OSError("this firmware build has no socket module")

        sock = None
        raw = b""

        try:
            address = self._remote()
            sock = socket.socket()
            sock.settimeout(self.timeout)

            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass

            sock.connect(address)

            if self.use_tls:
                if ssl is None:
                    raise OSError("this firmware build has no ssl module")

                if self.verify:
                    sock = ssl.wrap_socket(
                        sock,
                        server_hostname=self.host,
                        cert_reqs=ssl.CERT_REQUIRED,
                    )
                else:
                    sock = ssl.wrap_socket(sock, server_hostname=self.host)

            # Keep the read bounded even when the TLS implementation did not
            # inherit the plain socket timeout.
            sock.settimeout(self.timeout)
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8"))

            while True:
                chunk = sock.recv(512)

                if not chunk:
                    break

                raw += chunk

                if len(raw) > MAX_REPLY_BYTES:
                    raise OSError("heartbeat reply is too large")
        except Exception:
            self._connect_failures += 1

            if self._connect_failures >= 3:
                self._resolved = None

            raise
        else:
            self._connect_failures = 0
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

            _collect()

        head, _, reply = raw.partition(b"\r\n\r\n")
        status, fields = ingest.parse_head(head)

        if "chunked" in fields.get("transfer-encoding", "").lower():
            reply = _dechunk(reply)

        return status, reply

    # -- state ------------------------------------------------------------

    def firmware(self):
        if self.updater is not None and hasattr(self.updater, "local_version"):
            version = self.updater.local_version()

            if version:
                return version

        return "usb"

    def ip(self):
        try:
            return str(self.wlan.ifconfig()[0])
        except Exception:
            return ""

    def rssi(self):
        try:
            value = self.wlan.status("rssi")

            return int(value) if value is not None else None
        except Exception:
            return None

    def payload(self):
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "firmware": self.firmware(),
            "ip": self.ip(),
            "rssi": self.rssi(),
            "uptime_ms": _ticks_ms(),
            "sent": int(getattr(self.ingest, "sent", 0)),
            "pending": len(getattr(self.ingest, "pending", [])),
            "dropped": int(getattr(self.ingest, "dropped", 0)),
            "channels": self.channels,
        }

    # -- commands ---------------------------------------------------------

    def _ack(self, command_id, status, result=""):
        try:
            self._post(self._address("ack"), {
                "id": self.id,
                "command_id": int(command_id),
                "status": status,
                "result": str(result)[:200],
            })
            self.acks += 1
        except Exception as error:
            self.last_error = str(error)
            self.log("# device: ack failed: %s" % (error,))

    def _reboot(self):
        if machine is not None and hasattr(machine, "reset"):
            machine.reset()

        raise RuntimeError("machine.reset is not available")

    def _sync_heartbeat(self):
        """Follow a settings change without waiting for a restart."""

        if self.settings is None:
            return

        try:
            self.heartbeat_s = max(5.0, float(self.settings.snapshot()["heartbeat_s"]))
        except Exception:
            pass

    def _apply_settings(self, raw):
        """Apply one settings payload; returns True when it changed something."""

        if self.settings is None:
            raise OSError("settings module is not installed")

        changed = self.settings.apply(raw)
        self._sync_heartbeat()

        if changed:
            snapshot = self.settings.snapshot()
            self.log("# device: settings applied, %d ms frame, heartbeat %d s" % (
                snapshot["sample_ms"],
                snapshot["heartbeat_s"],
            ))

        return changed

    def _run(self, command):
        if not isinstance(command, dict):
            return

        kind = command.get("kind")
        command_id = command.get("id")
        payload = command.get("payload") or {}

        if not command_id:
            return

        if kind == "wifi":
            ssid = str(payload.get("ssid") or "")
            password = str(payload.get("password") or "")

            if ssid == "":
                self._ack(command_id, "failed", "empty ssid")
                return

            try:
                import wifi

                wifi.save_override(ssid, password, self.root)
            except Exception as error:
                self._ack(command_id, "failed", str(error))
                return

            self._ack(command_id, "done", "saved, restarting")
            self.log("# device: new Wi-Fi saved, restarting")
            _sleep_ms(300)
            self._reboot()

        elif kind == "restart":
            self._ack(command_id, "done", "restarting")
            _sleep_ms(300)
            self._reboot()

        elif kind == "ota":
            if self.updater is None:
                self._ack(command_id, "failed", "OTA module is not installed")
                return

            try:
                staged = self.updater.check()
            except Exception as error:
                self._ack(command_id, "failed", str(error))
                return

            if staged:
                self._ack(command_id, "done", "staged " + str(self.updater.ready))
            else:
                self._ack(command_id, "done", "already current")

        elif kind == "settings":
            raw = payload.get("settings") if isinstance(payload, dict) else None

            if not isinstance(raw, dict):
                self._ack(command_id, "failed", "missing settings payload")
                return

            try:
                changed = self._apply_settings(raw)
            except Exception as error:
                self._ack(command_id, "failed", str(error))
                return

            self._ack(command_id, "done", "applied" if changed else "unchanged")

    def heartbeat(self):
        """Send one heartbeat and run whatever the server queued."""
        status, reply = self._post(self._address("heartbeat"), self.payload())

        if status != 200:
            raise OSError("heartbeat answered HTTP %d" % (status,))

        data = json.loads(reply.decode("utf-8"))

        # Commands first, so a freshly queued settings change is acknowledged
        # as applied. The saved snapshot then covers a board that lost its
        # local file; applying it is idempotent, so it is safe on every poll.
        for command in data.get("commands") or []:
            self._run(command)

        stored = data.get("settings")

        if isinstance(stored, dict):
            try:
                self._apply_settings(stored)
            except Exception as error:
                self.log("# device: stored settings rejected: %s" % (error,))

        return data

    # -- thread -----------------------------------------------------------

    def start(self):
        if _thread is None:
            self.log("# device: no thread support, heartbeat disabled")
            return False

        if self._worker is not None:
            return True

        self._stop = False

        if hasattr(_thread, "stack_size"):
            try:
                _thread.stack_size(16384)
            except Exception:
                pass

        try:
            self._worker = _thread.start_new_thread(self._loop, ())
        except Exception as error:
            self.log("# device: heartbeat unavailable: %s" % (error,))
            self._worker = None
            return False

        return True

    def stop(self):
        self._stop = True

        for _ in range(50):
            if self._worker is None:
                break

            _sleep_ms(20)

        self._worker = None

    def alive(self):
        return self._worker is not None

    def _wait(self, seconds):
        remaining = int(max(0.0, seconds) * 1000)

        while remaining > 0 and not self._stop:
            step = 200 if remaining > 200 else remaining
            _sleep_ms(step)
            remaining -= step

    def _loop(self):
        try:
            self._wait(self.delay_s)

            while not self._stop:
                try:
                    self.heartbeat()
                    self.last_error = ""
                except Exception as error:
                    self.last_error = str(error)
                    self.log("# device: heartbeat failed: %s" % (error,))
                    _collect()
                    self._wait(RETRY_S)
                    continue

                self._wait(self.heartbeat_s)
        finally:
            self._worker = None

    def stats(self):
        return "id=%s acks=%d errors=%s" % (self.id, self.acks, self.last_error or "none")
