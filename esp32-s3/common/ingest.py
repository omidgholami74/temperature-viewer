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

try:
    import _thread
except ImportError:  # no thread support: samples are sent inline
    _thread = None

MAX_SAMPLES_PER_REQUEST = 600  # the API rejects a larger batch with 413
INGEST_PATH = "/api/ingest"
# The API answers with a short JSON object; anything this big is not a sample.
MAX_RESPONSE_BYTES = 4096
# A refused payload is dropped instead of retried forever: the server will
# never accept it, and keeping it would block the queue.
_DROP_STATUSES = (400, 413, 422)


def _sleep_ms(milliseconds):
    """MicroPython has sleep_ms, CPython (the host tests) only has sleep."""
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()

    return int(time.monotonic() * 1000)


class _NoLock:
    """Stand-in for a lock on builds without ``_thread``."""

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


_NO_LOCK = _NoLock()


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
        batch = int(getattr(config, "BATCH_SIZE", 10))
        self.batch = max(1, min(batch, MAX_SAMPLES_PER_REQUEST))
        limit = int(getattr(config, "BUFFER_LIMIT", 600))
        self.limit = max(self.batch, min(limit, 20 * MAX_SAMPLES_PER_REQUEST))

        self.pending = []
        self.sent = 0
        self.failures = 0
        self.dropped = 0
        self.last_error = ""
        self._sock = None
        # The host name is resolved once: a DNS lookup costs a few hundred
        # milliseconds on this board and must not be paid for every batch.
        self._resolved = None
        # Uploading runs in its own thread so the sampling loop never waits
        # for a request (a TLS handshake here is 0.5-1 s, i.e. 5-10 samples).
        self.use_background = bool(getattr(config, "BACKGROUND_UPLOAD", True))
        self.background = False
        self._lock = _thread.allocate_lock() if _thread is not None else None
        self._send_lock = _thread.allocate_lock() if _thread is not None else None
        self._worker = None
        self._stop = False
        self._last_trim_log = 0

    # -- queue ------------------------------------------------------------

    def _guard(self):
        """Lock guarding the queue (a no-op on builds without threads)."""
        return self._lock if self._lock is not None else _NO_LOCK

    def add(self, uptime_ms, temperatures, faults, at_ms=None):
        """Queue one frame; returns at once when the upload runs in a thread."""
        sample = {
            "u": int(uptime_ms),
            "t": [_clean(value) for value in temperatures],
            "f": [_clean_fault(value) for value in faults],
        }

        if at_ms is not None:
            sample["at"] = int(at_ms)

        with self._guard():
            self.pending.append(sample)
            self._trim()
            full = len(self.pending) >= self.batch

        # Inline mode: the caller sends, exactly as before.
        if full and not self.background:
            self.flush()

    def _trim(self):
        """Drop the oldest samples beyond BUFFER_LIMIT (logged once a second).

        Called with the queue lock held.
        """
        overflow = len(self.pending) - self.limit

        if overflow <= 0:
            return 0

        self.pending = self.pending[overflow:]
        self.dropped += overflow
        now = _ticks_ms()

        if now - self._last_trim_log >= 1000:
            self._last_trim_log = now
            self.log("API: buffer full, dropped the %d oldest samples" % (overflow,))

        return overflow

    def flush(self):
        """Send everything queued. Returns True when the queue is empty."""
        if not self.pending:
            return True

        while True:
            with self._guard():
                chunk = self.pending[:MAX_SAMPLES_PER_REQUEST]

            if not chunk:
                break

            if not self._send(chunk):
                break

            with self._guard():
                self.pending = self.pending[len(chunk):]
                self._trim()

        return not self.pending

    # -- background uploader ----------------------------------------------

    def start(self):
        """Upload from a thread so sampling never waits. False = send inline."""
        if self._worker is not None:
            return True

        if _thread is None or not self.use_background:
            return False

        self._stop = False

        # The default thread stack is too small for an mbedtls handshake.
        if hasattr(_thread, "stack_size"):
            for size in (16384, 8192):
                try:
                    _thread.stack_size(size)
                    break
                except Exception:
                    continue

        try:
            self._worker = _thread.start_new_thread(self._run, ())
        except Exception as error:
            self.log("API: background upload unavailable (%s), sending inline" % (error,))
            return False

        self.background = True
        return True

    def stop(self):
        """Stop the uploader, send what is left and release the socket."""
        self._stop = True

        for _ in range(100):
            if self._worker is None:
                break

            _sleep_ms(10)

        self.flush()
        self.close()
        self.background = False

    def _run(self):
        """Worker body: drain the queue, pace retries, never busy-spin."""
        try:
            while not self._stop:
                if not self.pending:
                    _sleep_ms(20)
                    continue

                self.flush()

                if self.pending:
                    _sleep_ms(200)  # offline: retry slowly, keep the queue
        finally:
            self._worker = None

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

        lock = self._send_lock if self._send_lock is not None else _NO_LOCK

        try:
            # One request at a time on the shared socket, even when stop()
            # flushes while the worker is still finishing a send.
            with lock:
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

    def close(self):
        """Drop the kept-alive connection (shutdown, or after an error)."""
        sock = self._sock
        self._sock = None

        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _remote(self):
        """The cached server address; a DNS lookup per batch is too slow here."""
        if self._resolved is None:
            self._resolved = socket.getaddrinfo(self.host, self.port)[0][-1]

        return self._resolved

    def _connect(self):
        """Dial the API, with TLS when API_URL is https."""
        address = self._remote()
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
        except Exception:
            # The network changed (DHCP, DNS): resolve again on the next try.
            self._resolved = None
            try:
                sock.close()
            except Exception:
                pass
            raise

        return sock

    def _request(self, body, headers):
        """POST over the kept-alive socket; returns (status, body text).

        A TCP + TLS handshake costs a few hundred milliseconds on an ESP32,
        which is several skipped samples on the 10 Hz grid, so the socket is
        reopened only after an error or when the server closes it. A request
        that fails on a reused socket is retried once on a fresh one: the
        server had almost certainly closed an idle connection.
        """
        reused = self._sock is not None

        sock = self._sock if reused else self._connect()
        self._sock = sock

        try:
            return self._exchange(sock, body, headers)
        except Exception:
            self.close()

            if not reused:
                raise

        # Stale keep-alive connection: dial again and send once more.
        sock = self._connect()
        self._sock = sock

        try:
            return self._exchange(sock, body, headers)
        except Exception:
            self.close()
            raise

    def _exchange(self, sock, body, headers):
        """Write one POST and read the whole reply so the socket stays usable."""
        lines = [
            "POST %s HTTP/1.1" % (self.path,),
            "Host: %s" % (self.host,),
            "Connection: keep-alive",
        ]

        for name in headers:
            lines.append("%s: %s" % (name, headers[name]))

        lines.append("Content-Length: %d" % (len(body),))
        lines.append("")
        lines.append(body)

        # sendall/recv are the two calls MicroPython and CPython share.
        sock.sendall("\r\n".join(lines).encode("utf-8"))

        return self._read_response(sock)

    def _read_response(self, sock):
        """Read one complete response: status, headers and the whole body."""
        raw = b""

        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(512)

            if not chunk:
                raise OSError("empty response")

            raw += chunk

            if len(raw) > MAX_RESPONSE_BYTES:
                raise OSError("response headers are too large")

        head, _, body = raw.partition(b"\r\n\r\n")
        status, fields = parse_head(head)

        if "chunked" in fields.get("transfer-encoding", "").lower():
            body = read_chunked(sock, body)
        else:
            length = fields.get("content-length")

            if length is None:
                # No length and no chunking: the reply ends when the socket does.
                while True:
                    chunk = sock.recv(512)

                    if not chunk:
                        break

                    body += chunk

                    if len(body) > MAX_RESPONSE_BYTES:
                        break

                self.close()
            else:
                try:
                    expected = int(length)
                except (TypeError, ValueError):
                    raise OSError("bad Content-Length: %r" % (length,))

                while len(body) < expected:
                    chunk = sock.recv(expected - len(body))

                    if not chunk:
                        raise OSError("connection closed inside the response")

                    body += chunk

                body = body[:expected]

        if fields.get("connection", "").lower() == "close":
            self.close()

        return status, body.decode("utf-8", "ignore")


def parse_head(head):
    """Status code and lower-cased field names from an HTTP header block."""
    lines = head.split(b"\r\n")
    status_line = lines[0].split(b" ")

    if len(status_line) < 2:
        raise OSError("malformed response: %r" % (head[:40],))

    try:
        status = int(status_line[1])
    except ValueError:
        raise OSError("malformed status line: %r" % (status_line[:3],))

    fields = {}

    for line in lines[1:]:
        name, separator, value = line.partition(b":")

        if separator:
            fields[name.strip().lower().decode("utf-8", "ignore")] = value.strip().decode(
                "utf-8", "ignore"
            )

    return status, fields


def read_chunked(sock, buffered):
    """De-chunk a ``Transfer-Encoding: chunked`` body (small replies only)."""
    body = b""
    raw = buffered

    while True:
        while b"\r\n" not in raw:
            chunk = sock.recv(512)

            if not chunk:
                raise OSError("connection closed inside a chunked response")

            raw += chunk

        line, _, raw = raw.partition(b"\r\n")

        try:
            size = int(line.split(b";")[0].strip(), 16)
        except ValueError:
            raise OSError("bad chunk size: %r" % (line[:20],))

        if size == 0:
            # Trailers (usually none) end with an empty line. Leaving them in the
            # socket would corrupt the next response on the same connection.
            while True:
                while b"\r\n" not in raw:
                    chunk = sock.recv(512)

                    if not chunk:
                        return body

                    raw += chunk

                line, _, raw = raw.partition(b"\r\n")

                if not line:
                    return body

        while len(raw) < size + 2:
            chunk = sock.recv(512)

            if not chunk:
                raise OSError("connection closed inside a chunk")

            raw += chunk

        body += raw[:size]
        raw = raw[size + 2:]

        if len(body) > MAX_RESPONSE_BYTES:
            return body


def uptime_ms_since(start_tick):
    """Milliseconds since ``start_tick``, safe across the ticks_ms wrap."""
    return time.ticks_diff(time.ticks_ms(), start_tick)
