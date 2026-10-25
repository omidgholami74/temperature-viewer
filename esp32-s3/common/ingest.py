"""Direct upload of 10 Hz samples to the PHP API.

This replaces ``collector/collector.py`` for the ESP32-S3: the board talks to
``POST /api/ingest`` itself, so no PC has to sit next to the furnace. The
payload is exactly what the collector sends:

    {"samples": [{"u": 1200, "at": 1792523131000, "t": [781.25, null, ...],
                  "f": [0, 1, 0, 0]}]}

Only ``socket`` and ``json`` are used, so nothing has to be installed with
``mip`` and the firmware runs on a stock MicroPython build.
"""

import json
import socket
import time

try:
    import ssl
except ImportError:  # a build without TLS still works over http://
    ssl = None

MAX_SAMPLES_PER_REQUEST = 600  # the API rejects a larger batch with 413
INGEST_PATH = "/api/ingest"
# A refused payload is dropped instead of retried forever: the server will
# never accept it, and keeping it would block the queue.
_DROP_STATUSES = (400, 413, 422)


def split_url(url):
    """http(s)://host[:port]/path -> (host, port, path, use_tls)."""
    text = str(url).strip().rstrip("/")

    if "://" not in text:
        raise ValueError("API_URL must start with http:// or https://")

    scheme, rest = text.split("://", 1)
    scheme = scheme.lower()

    if scheme not in ("http", "https"):
        raise ValueError("API_URL must use http or https")

    if "/" in rest:
        hostport, path = rest.split("/", 1)
        path = "/" + path
    else:
        hostport, path = rest, "/"

    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
        port = int(port)
    else:
        host, port = hostport, 443 if scheme == "https" else 80

    if not host:
        raise ValueError("API_URL has no host")

    return host, port, path, scheme == "https"


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


class Ingest:
    """Buffer samples and POST them in batches, tolerating a dead network."""

    def __init__(self, config, log=print):
        self.log = log
        host, port, path, use_tls = split_url(config.API_URL)
        self.host = host
        self.port = port
        # API_URL is the API host, so the endpoint is added here. A full path in
        # API_URL (a proxy, another route) is used as given.
        self.path = path if path not in ("", "/") else INGEST_PATH
        self.use_tls = use_tls
        self.key = getattr(config, "API_KEY", "") or ""
        self.timeout = float(getattr(config, "API_TIMEOUT_S", 8))
        self.verify = bool(getattr(config, "TLS_VERIFY", False))
        batch = int(getattr(config, "BATCH_SIZE", 20))
        self.batch = max(1, min(batch, MAX_SAMPLES_PER_REQUEST))
        limit = int(getattr(config, "BUFFER_LIMIT", 600))
        self.limit = max(self.batch, min(limit, 20 * MAX_SAMPLES_PER_REQUEST))

        self.pending = []
        self.sent = 0
        self.failures = 0
        self.dropped = 0
        self.last_error = ""

    # -- queue ------------------------------------------------------------

    def add(self, uptime_ms, temperatures, faults, at_ms=None):
        """Queue one frame. Flushes automatically when the batch is full."""
        sample = {
            "u": int(uptime_ms),
            "t": [_clean(value) for value in temperatures],
            "f": [_clean_fault(value) for value in faults],
        }

        if at_ms is not None:
            sample["at"] = int(at_ms)

        self.pending.append(sample)

        if len(self.pending) >= self.batch:
            self.flush()

    def flush(self):
        """Send everything queued. Returns True when the queue is empty."""
        if not self.pending:
            return True

        while self.pending:
            chunk = self.pending[:MAX_SAMPLES_PER_REQUEST]

            if not self._send(chunk):
                break

            self.pending = self.pending[len(chunk):]

        if len(self.pending) > self.limit:
            overflow = len(self.pending) - self.limit
            self.pending = self.pending[overflow:]
            self.dropped += overflow
            self.log("API: buffer full, dropped the %d oldest samples" % (overflow,))

        return not self.pending

    def stats(self):
        return "sent=%d pending=%d dropped=%d failures=%d" % (
            self.sent,
            len(self.pending),
            self.dropped,
            self.failures,
        )

    # -- transport --------------------------------------------------------

    def _send(self, samples):
        """True when the samples are done with (stored or refused for good)."""
        body = json.dumps({"samples": samples})
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "esp32-s3-furnace/1.0",
        }

        if self.key:
            headers["X-Api-Key"] = self.key

        try:
            status, payload = self._request(body, headers)
        except Exception as error:  # OSError, ssl errors, DNS failures
            self.failures += 1
            self.last_error = str(error)
            self.log("API: %d samples not sent: %s" % (len(samples), error))
            return False

        if status == 200:
            self.sent += len(samples)
            self.failures = 0
            self.last_error = ""
            return True

        self.failures += 1
        self.last_error = "HTTP %d" % (status,)
        self.log("API %d: %s" % (status, payload[:160]))

        if status in _DROP_STATUSES:
            # The server understood the request and rejected its content.
            self.dropped += len(samples)
            return True

        if status in (401, 403):
            self.log("API: check API_KEY against FURNACE_INGEST_KEY on the server")
        elif status in (404, 405):
            self.log("API: check API_URL (%s) — the endpoint is missing" % (self.path,))

        # 5xx, 401/403 and a wrong URL are retryable: the payload is kept.
        return False

    def _request(self, body, headers):
        """Send one POST over a plain socket; returns (status, body text)."""
        address = socket.getaddrinfo(self.host, self.port)[0][-1]
        sock = socket.socket()
        sock.settimeout(self.timeout)

        try:
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
                    self.log("API: TLS certificate not verified (TLS_VERIFY = False)")

            lines = [
                "POST %s HTTP/1.1" % (self.path,),
                "Host: %s" % (self.host,),
                "Connection: close",
            ]

            for name in headers:
                lines.append("%s: %s" % (name, headers[name]))

            lines.append("Content-Length: %d" % (len(body),))
            lines.append("")
            lines.append(body)

            # sendall/recv are the two calls MicroPython and CPython share.
            sock.sendall("\r\n".join(lines).encode("utf-8"))

            raw = b""

            while True:
                chunk = sock.recv(256)

                if not chunk:
                    break

                raw += chunk

                # The status line is enough to choose a retry strategy; stop
                # reading once the headers are complete and the body is small.
                if b"\r\n\r\n" in raw and len(raw) > 512:
                    break
        finally:
            try:
                sock.close()
            except Exception:
                pass

        return self._parse(raw)

    @staticmethod
    def _parse(raw):
        if not raw:
            raise OSError("empty response")

        head, _, body = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].split(b" ")

        if len(status_line) < 2:
            raise OSError("malformed response: %r" % (head[:40],))

        try:
            status = int(status_line[1])
        except ValueError:
            raise OSError("malformed status line: %r" % (status_line[:3],))

        return status, body.decode("utf-8", "ignore")


def uptime_ms_since(start_tick):
    """Milliseconds since ``start_tick``, safe across the ticks_ms wrap."""
    return time.ticks_diff(time.ticks_ms(), start_tick)
