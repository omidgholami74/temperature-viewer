"""Checks for the ESP32-S3 firmware modules that run without a board.

    python3 -m unittest discover -s esp32-s3/tests -v

``machine`` is stubbed so the MicroPython modules import in CPython; the socket
HTTP client is exercised against a real local HTTP server.
"""

import http.server
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def install_machine_stub():
    if "machine" in sys.modules:
        return

    machine = types.ModuleType("machine")

    class Pin:
        OUT = 1
        IN = 0

        def __init__(self, number, mode=None, value=1, **kwargs):
            self.number = number
            self.value_ = value

        def value(self, value=None):
            if value is None:
                return self.value_
            self.value_ = value

    class SPI:
        MSB = 1

        def __init__(self, bus=0, **kwargs):
            self.bus = bus
            self.options = kwargs

        def write(self, data):
            pass

        def read(self, count):
            return bytes(count)

    machine.Pin = Pin
    machine.SPI = SPI
    sys.modules["machine"] = machine


install_machine_stub()

# MicroPython's time module has millisecond helpers that CPython does not.
if not hasattr(__import__("time"), "sleep_ms"):
    __import__("time").sleep_ms = lambda ms: __import__("time").sleep(ms / 1000.0)

rtd = load("esp32s3_max31865", os.path.join(ROOT, "max31865", "max31865.py"))
ingest = load("esp32s3_ingest", os.path.join(ROOT, "common", "ingest.py"))
supervisor = load("esp32s3_supervisor", os.path.join(ROOT, "common", "supervisor.py"))

# ota.py imports the firmware's own ``ingest`` module by that short name.
sys.modules.setdefault("ingest", ingest)
ota = load("esp32s3_ota", os.path.join(ROOT, "common", "ota.py"))

# wifi.py imports network at module level; the board stub is enough here.
if "network" not in sys.modules:
    network = types.ModuleType("network")
    network.STA_IF = 0

    class _WLAN:
        def __init__(self, interface=0):
            self.interface = interface

        def active(self, value=None):
            return True

        def isconnected(self):
            return True

        def ifconfig(self):
            return ("192.168.1.42", "255.255.255.0", "192.168.1.1", "1.1.1.1")

        def status(self, name=None):
            return -57

        def config(self, **kwargs):
            pass

        def connect(self, ssid, password):
            pass

    network.WLAN = _WLAN
    sys.modules["network"] = network

wifi = load("esp32s3_wifi", os.path.join(ROOT, "common", "wifi.py"))
sys.modules.setdefault("wifi", wifi)
device = load("esp32s3_device", os.path.join(ROOT, "common", "device.py"))

# boot.py runs its diagnostics on import; the output is captured here.
with mock.patch("builtins.print"):
    boot = load("esp32s3_boot", os.path.join(ROOT, "boot.py"))


class FakeSPI:
    """Records writes and replays a canned RTD register block."""

    def __init__(self, block):
        self.block = bytes(block)
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, count):
        return self.block[:count]


class Recorder(http.server.BaseHTTPRequestHandler):
    received = []
    status = 200
    body = b'{"ok": true}'

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        Recorder.received.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "payload": json.loads(raw.decode()),
            }
        )
        self.send_response(Recorder.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(Recorder.body)))
        self.end_headers()
        self.wfile.write(Recorder.body)

    def log_message(self, *args):
        pass


class IngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Recorder.received = []
        Recorder.status = 200

    def config(self, **overrides):
        values = {
            "API_URL": "http://127.0.0.1:%d/" % (self.port,),
            "API_KEY": "secret",
            "API_TIMEOUT_S": 5,
            "BATCH_SIZE": 3,
            "BUFFER_LIMIT": 10,
            "TLS_VERIFY": False,
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def make(self, **overrides):
        """An Ingest that is closed when the test ends (it holds a socket)."""
        client = ingest.Ingest(self.config(**overrides), log=lambda *_: None)
        self.addCleanup(client.close)
        return client

    def test_split_url(self):
        self.assertEqual(
            ingest.split_url("https://apitemp.example.com"),
            ("apitemp.example.com", 443, "/", True),
        )
        self.assertEqual(
            ingest.split_url("http://127.0.0.1:8080/api/ingest"),
            ("127.0.0.1", 8080, "/api/ingest", False),
        )
        self.assertEqual(
            ingest.split_url("http://box.local/"),
            ("box.local", 80, "/", False),
        )

        for bad in ("apitemp.example.com", "ftp://host/x", "http:///x"):
            with self.assertRaises(ValueError):
                ingest.split_url(bad)

    def test_batch_reaches_the_api_as_the_collector_payload(self):
        client = self.make()
        client.add(100, [781.25, None, float("nan"), 545.754], [0, 1, 0, 0], at_ms=1792523131000)
        client.add(200, [781.5, 660.0, 940.0, 545.0], [0, 0, 0, 0], at_ms=1792523131100)
        self.assertEqual(Recorder.received, [], "a partial batch must not be sent")

        client.add(300, [781.75, 660.25, 940.25, 545.25], [0, 0, 0, 0], at_ms=1792523131200)

        self.assertEqual(len(Recorder.received), 1)
        request = Recorder.received[0]
        self.assertEqual(request["path"], "/api/ingest")
        self.assertEqual(request["headers"].get("X-Api-Key"), "secret")

        samples = request["payload"]["samples"]
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0]["u"], 100)
        self.assertEqual(samples[0]["at"], 1792523131000)
        # Faults are null (not NaN, which is invalid JSON) and masked.
        self.assertEqual(samples[0]["t"], [781.25, None, None, 545.75])
        self.assertEqual(samples[0]["f"], [0, 1, 0, 0])
        self.assertEqual(client.pending, [])
        self.assertEqual(client.sent, 3)

    def test_no_at_key_without_a_clock(self):
        client = self.make(BATCH_SIZE=1)
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])

        self.assertNotIn("at", Recorder.received[0]["payload"]["samples"][0])

    def test_unreachable_api_keeps_the_batch_for_a_retry(self):
        client = self.make(API_URL="http://127.0.0.1:1", BATCH_SIZE=1)
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])

        self.assertEqual(len(client.pending), 1)
        self.assertEqual(client.failures, 1)
        self.assertTrue(client.last_error)

    def test_rejected_payload_is_dropped_instead_of_blocking_the_queue(self):
        Recorder.status = 422
        client = self.make(BATCH_SIZE=1)
        client.add(100, [9999, 2, 3, 4], [0, 0, 0, 0])

        self.assertEqual(client.pending, [])
        self.assertEqual(client.dropped, 1)

    def test_buffer_limit_drops_the_oldest_samples(self):
        Recorder.status = 503
        client = self.make(BATCH_SIZE=2, BUFFER_LIMIT=4)

        for value in range(10):
            client.add(value * 100, [value, value, value, value], [0, 0, 0, 0])

        self.assertEqual(len(client.pending), 4)
        self.assertGreater(client.dropped, 0)
        self.assertEqual(client.pending[-1]["u"], 900)


class RawProtocolTests(unittest.TestCase):
    """The request is hand-written HTTP, so check the bytes on the wire."""

    def test_request_line_headers_and_length(self):
        captured = {}

        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve():
            connection, _ = server.accept()
            data = b""

            while b"\r\n\r\n" not in data:
                data += connection.recv(1024)

            head, _, body = data.partition(b"\r\n\r\n")
            length = 0

            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])

            while len(body) < length:
                body += connection.recv(1024)

            captured["head"] = head.decode()
            captured["body"] = body.decode()
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\": true}")
            connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        try:
            client = ingest.Ingest(
                types.SimpleNamespace(
                    API_URL="http://127.0.0.1:%d" % (port,),
                    API_KEY="k",
                    API_TIMEOUT_S=5,
                    BATCH_SIZE=1,
                    BUFFER_LIMIT=10,
                    TLS_VERIFY=False,
                ),
                log=lambda *_: None,
            )
            client.add(100, [1.0, 2.0, 3.0, 4.0], [0, 0, 0, 0])
            client.close()
        finally:
            thread.join(5)
            server.close()

        self.assertTrue(client.pending == [], "the flushed batch is still queued")

        lines = captured["head"].split("\r\n")
        self.assertEqual(lines[0], "POST /api/ingest HTTP/1.1")
        self.assertIn("Host: 127.0.0.1", captured["head"])
        self.assertIn("Content-Type: application/json", captured["head"])
        self.assertIn("X-Api-Key: k", captured["head"])
        self.assertIn("Connection: keep-alive", captured["head"])
        self.assertIn("Content-Length: %d" % (len(captured["body"]),), captured["head"])
        self.assertEqual(
            captured["body"],
            '{"samples": [{"u": 100, "t": [1.0, 2.0, 3.0, 4.0], "f": [0, 0, 0, 0]}]}',
        )


class ScriptedServer:
    """A small HTTP server that keeps its connections open like a real one."""

    def __init__(self, requests_per_connection=None):
        self.requests_per_connection = requests_per_connection
        self.connections = 0
        self.payloads = []
        self.stop = False
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(5)
        self.port = self.socket.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while not self.stop:
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return

            self.connections += 1
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        buffer = b""
        served = 0

        try:
            while not self.stop:
                while b"\r\n\r\n" not in buffer:
                    chunk = connection.recv(1024)

                    if not chunk:
                        return

                    buffer += chunk

                head, _, buffer = buffer.partition(b"\r\n\r\n")
                length = 0

                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])

                while len(buffer) < length:
                    chunk = connection.recv(1024)

                    if not chunk:
                        return

                    buffer += chunk

                body, buffer = buffer[:length], buffer[length:]
                self.payloads.append(json.loads(body.decode()))
                served += 1

                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 11\r\nConnection: keep-alive\r\n\r\n{\"ok\": true}"
                )

                if self.requests_per_connection is not None and served >= self.requests_per_connection:
                    return
        except OSError:
            return
        finally:
            connection.close()

    def close(self):
        self.stop = True

        try:
            self.socket.close()
        except OSError:
            pass


class KeepAliveTests(unittest.TestCase):
    """The socket is reused, so the TLS handshake is not paid per batch."""

    def config(self, port):
        return types.SimpleNamespace(
            API_URL="http://127.0.0.1:%d" % (port,),
            API_KEY="",
            API_TIMEOUT_S=5,
            BATCH_SIZE=1,
            BUFFER_LIMIT=10,
            TLS_VERIFY=False,
        )

    def test_batches_share_one_connection(self):
        server = ScriptedServer()
        client = ingest.Ingest(self.config(server.port), log=lambda *_: None)

        try:
            for step in range(3):
                client.add(step * 100, [1.0, 2.0, 3.0, 4.0], [0, 0, 0, 0])

            self.assertEqual(len(server.payloads), 3, "a batch never reached the server")
            self.assertEqual(server.connections, 1, "every batch opened a new TCP connection")
            self.assertEqual(client.pending, [])
        finally:
            client.close()
            server.close()

    def test_a_closed_idle_socket_is_redialled(self):
        server = ScriptedServer(requests_per_connection=1)
        client = ingest.Ingest(self.config(server.port), log=lambda *_: None)

        try:
            for step in range(3):
                client.add(step * 100, [1.0, 2.0, 3.0, 4.0], [0, 0, 0, 0])

            self.assertEqual(len(server.payloads), 3, "a batch was lost after the server hung up")
            self.assertGreaterEqual(server.connections, 2)
            self.assertEqual(client.pending, [])
        finally:
            client.close()
            server.close()

    def test_chunked_reply_is_read_completely(self):
        left, right = socket.socketpair()

        try:
            left.settimeout(0.2)
            # A trailer header after the last chunk must be consumed too,
            # otherwise it corrupts the next response on the same socket.
            right.sendall(b"5\r\nhello\r\n6\r\n world\r\n0\r\nX-Trace: 1\r\n\r\n")
            self.assertEqual(ingest.read_chunked(left, b""), b"hello world")

            with self.assertRaises(TimeoutError):
                left.recv(16)
        finally:
            left.close()
            right.close()

    def test_head_parsing(self):
        status, fields = ingest.parse_head(
            b"HTTP/1.1 422 Unprocessable Entity\r\nContent-Length: 12\r\nConnection: close"
        )

        self.assertEqual(status, 422)
        self.assertEqual(fields["content-length"], "12")
        self.assertEqual(fields["connection"], "close")

        with self.assertRaises(OSError):
            ingest.parse_head(b"garbage")


class BackgroundUploadTests(unittest.TestCase):
    """Sampling must keep its 100 ms grid while a request is in flight."""

    def make(self, **overrides):
        values = {
            "API_URL": "http://127.0.0.1:9",
            "API_KEY": "",
            "API_TIMEOUT_S": 5,
            "BATCH_SIZE": 10,
            "BUFFER_LIMIT": 600,
            "TLS_VERIFY": False,
        }
        values.update(overrides)
        client = ingest.Ingest(types.SimpleNamespace(**values), log=lambda *_: None)
        self.addCleanup(client.stop)
        return client

    def test_sampling_does_not_wait_for_the_upload(self):
        client = self.make()

        def slow_send(samples):
            # A real POST with a TLS handshake is slower than this.
            time.sleep(0.25)
            client.sent += len(samples)
            return True

        client._send = slow_send
        self.assertTrue(client.start())

        spacing = []
        previous = None
        started = time.monotonic()

        for step in range(30):  # 3 s of samples at 10 Hz
            client.add(step * 100, [700.0 + step, 710.0, 720.0, 730.0], [0, 0, 0, 0])
            now = time.monotonic()

            if previous is not None:
                spacing.append(now - previous)

            previous = now
            remaining = started + (step + 1) * 0.1 - now

            if remaining > 0:
                time.sleep(remaining)

        client.stop()

        # One slot is 100 ms; a send that blocked the loop would add 250 ms.
        self.assertLess(max(spacing), 0.16, "a sample slot was lost to an upload")
        self.assertEqual(client.sent, 30, "the background thread did not send everything")
        self.assertEqual(client.pending, [])

    def test_a_dead_api_keeps_the_queue_bounded_without_blocking(self):
        client = self.make(BATCH_SIZE=5, BUFFER_LIMIT=20)

        def offline(samples):
            client.failures += 1
            return False

        client._send = offline
        self.assertTrue(client.start())

        started = time.monotonic()

        for step in range(60):
            client.add(step * 100, [1, 2, 3, 4], [0, 0, 0, 0])

        elapsed = time.monotonic() - started
        client.stop()

        self.assertLess(elapsed, 0.2, "add() waited for the network")
        self.assertLessEqual(len(client.pending), 20)
        self.assertGreater(client.dropped, 0)

    def test_stop_sends_what_is_still_queued(self):
        client = self.make(BATCH_SIZE=50)
        sent = []
        client._send = lambda samples: (sent.append(len(samples)), True)[1]
        self.assertTrue(client.start())

        for step in range(7):
            client.add(step * 100, [1, 2, 3, 4], [0, 0, 0, 0])

        client.stop()

        self.assertEqual(sum(sent), 7, "the last samples were not flushed on stop")

    def test_inline_mode_still_sends_a_full_batch(self):
        client = self.make(BATCH_SIZE=2)
        sent = []
        client._send = lambda samples: (sent.append(len(samples)), True)[1]

        with mock.patch.object(ingest, "_thread", None):
            self.assertFalse(client.start(), "no thread module still started a thread")

        client.add(0, [1, 2, 3, 4], [0, 0, 0, 0])
        self.assertEqual(sent, [], "a partial batch was sent")
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])
        self.assertEqual(sent, [2])

    def test_background_upload_can_be_turned_off(self):
        client = self.make(BATCH_SIZE=2, BACKGROUND_UPLOAD=False)
        sent = []
        client._send = lambda samples: (sent.append(len(samples)), True)[1]

        self.assertFalse(client.start())
        client.add(0, [1, 2, 3, 4], [0, 0, 0, 0])
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])

        self.assertEqual(sent, [2])

    def test_alive_detects_a_dead_uploader(self):
        client = self.make(BATCH_SIZE=50)

        # Inline mode sends from the caller, so there is nothing to supervise.
        self.assertTrue(client.alive())

        client._send = lambda samples: True
        self.assertTrue(client.start())
        self.assertTrue(client.alive())

        # The thread dying (a stack overflow inside mbedtls) leaves the queue
        # unserved while background mode is still on.
        client._worker = None
        self.assertFalse(client.alive())

        self.assertTrue(client.start(), "the uploader could not be restarted")
        self.assertTrue(client.alive())

    def test_the_host_is_resolved_once(self):
        server = ScriptedServer()
        client = ingest.Ingest(
            types.SimpleNamespace(
                API_URL="http://127.0.0.1:%d" % (server.port,),
                API_KEY="",
                API_TIMEOUT_S=5,
                BATCH_SIZE=1,
                BUFFER_LIMIT=10,
                TLS_VERIFY=False,
            ),
            log=lambda *_: None,
        )
        calls = []
        real = socket.getaddrinfo

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        try:
            with mock.patch.object(socket, "getaddrinfo", counting):
                for step in range(3):
                    client.add(step * 100, [1.0, 2.0, 3.0, 4.0], [0, 0, 0, 0])

            self.assertEqual(len(server.payloads), 3)
            self.assertEqual(len(calls), 1, "the host was resolved for every batch")
        finally:
            client.close()
            server.close()


class BootTests(unittest.TestCase):
    """boot.py is what tells the user why main.py stayed silent."""

    def test_missing_lists_what_main_py_imports(self):
        absent = boot.missing(["boot.py", "main.py", "config.py", "wifi.py", "ingest.py"])

        self.assertEqual(absent, ["supervisor.py"])

    def test_a_complete_board_has_nothing_missing(self):
        self.assertEqual(boot.missing(list(boot.REQUIRED)), [])
        # os.listdir() also returns the log files and the folders on the board.
        self.assertEqual(boot.missing(["data", "README.md"] + list(boot.REQUIRED)), [])


class SupervisorTests(unittest.TestCase):
    """A board on a charger has no console, so it has to recover by itself."""

    def test_reset_cause_is_named(self):
        fake = types.SimpleNamespace(
            WDT_RESET=8,
            PWRON_RESET=1,
            HARD_RESET=2,
            reset_cause=lambda: 8,
        )

        with mock.patch.object(supervisor, "machine", fake):
            self.assertEqual(supervisor.reset_cause(), "WDT_RESET")

        fake.reset_cause = lambda: 1
        with mock.patch.object(supervisor, "machine", fake):
            self.assertEqual(supervisor.reset_cause(), "PWRON_RESET")

    def test_reset_cause_without_hardware(self):
        with mock.patch.object(supervisor, "machine", None):
            self.assertIsNone(supervisor.reset_cause())

    def test_watchdog_is_armed_with_the_configured_timeout(self):
        feeds = []

        class FakeWDT:
            def __init__(self, timeout=None):
                self.timeout = timeout

            def feed(self):
                feeds.append(time.monotonic())

        with mock.patch.object(supervisor, "machine", types.SimpleNamespace(WDT=FakeWDT)):
            watch = supervisor.Watchdog(30)

        self.assertEqual(watch.wdt.timeout, 30000)
        watch.feed()
        watch.feed()
        self.assertEqual(len(feeds), 2)

    def test_watchdog_can_be_turned_off(self):
        # WATCHDOG_S = 0 must leave the board alone, and feeding must be safe.
        watch = supervisor.Watchdog(0)
        self.assertIsNone(watch.wdt)
        watch.feed()

    def test_a_crashing_session_is_restarted(self):
        calls = []
        watches = []
        watch = types.SimpleNamespace(feed=lambda: None)

        def session(given):
            calls.append(len(calls))
            watches.append(given)

            if len(calls) < 3:
                raise OSError("sensor bus is stuck")

            raise SystemExit  # ends the run, like Ctrl-C would

        with self.assertRaises(SystemExit):
            supervisor.run_forever(session, watch, delay_s=0.01, log=lambda *_: None)

        self.assertEqual(len(calls), 3, "a crash stopped the firmware instead of a restart")
        self.assertEqual(watches, [watch, watch, watch], "the watchdog was lost on restart")

    def test_the_restart_delay_is_fed_to_the_watchdog(self):
        feeds = []

        class Stop(Exception):
            pass

        def feed():
            feeds.append(1)

            if len(feeds) >= 2:
                raise Stop  # end the run once the wait has been fed

        def session(_):
            raise RuntimeError("boom")

        # 0.5 s of backoff is two 250 ms steps, so the wait is fed twice.
        with self.assertRaises(Stop):
            supervisor.run_forever(
                session, types.SimpleNamespace(feed=feed), delay_s=0.5, log=lambda *_: None
            )

        self.assertGreaterEqual(len(feeds), 2, "the watchdog starved during the restart wait")


class RtdTests(unittest.TestCase):
    def test_resistance_to_celsius_matches_iec_60751(self):
        cases = [
            (100.0, 0.0),
            (138.5055, 100.0),
            (175.856, 200.0),
            (60.256, -100.0),
            (18.52, -200.0),
        ]

        for resistance, expected in cases:
            celsius = rtd.resistance_to_celsius(resistance)
            self.assertAlmostEqual(celsius, expected, delta=0.2, msg="R=%s" % (resistance,))

        # PT1000 uses the same curve with a 1000 ohm base.
        self.assertAlmostEqual(
            rtd.resistance_to_celsius(1000.0, r_nominal=1000.0), 0.0, delta=0.05
        )

    def test_invalid_resistance_is_none(self):
        self.assertIsNone(rtd.resistance_to_celsius(0))
        self.assertIsNone(rtd.resistance_to_celsius(-5))
        self.assertIsNone(rtd.resistance_to_celsius(None))

    def test_fault_bits_follow_the_api_masks(self):
        self.assertEqual(rtd.fault_bits(0x00), 0)
        self.assertEqual(rtd.fault_bits(0x84), 1)  # RTDIN- low: open element
        self.assertEqual(rtd.fault_bits(0x82), 2)  # FORCE- open
        self.assertEqual(rtd.fault_bits(0x81), 4)  # over/under voltage
        self.assertEqual(rtd.fault_bits(0x90), 4)  # REFIN- high
        self.assertEqual(rtd.fault_bits(0xC0), 8)  # RTD threshold trip

    def test_reading_a_frame_from_the_register_block(self):
        # 100 ohm with a 430 ohm reference: code 7620 -> 0x1DC4, shifted left 1.
        code = int(100.0 * 32768 / 430.0)
        block = bytes([(code << 1) >> 8, (code << 1) & 0xFF, 0x00, 0, 0, 0, 0, 0])
        sensor = rtd.MAX31865(FakeSPI(block), cs=15, r_ref=430.0, wire_mode=3, filter_hz=50)

        celsius, fault = sensor.read()

        self.assertEqual(fault, 0)
        self.assertAlmostEqual(celsius, 0.0, delta=0.1)

    def test_fault_clears_and_reports_none(self):
        block = bytes([0x3B, 0x88, 0x84, 0, 0, 0, 0, 0])
        spi = FakeSPI(block)
        sensor = rtd.MAX31865(spi, cs=16)

        celsius, fault = sensor.read()

        self.assertIsNone(celsius)
        self.assertEqual(fault, 1)
        # The driver must clear the latch, otherwise every later read is a fault.
        self.assertTrue(
            any(len(write) >= 2 and write[1] & 0x02 for write in spi.writes),
            "config was never written with the fault-clear bit",
        )

    def test_wire_mode_and_filter_land_in_the_config_register(self):
        spi = FakeSPI(bytes(8))
        three_wire = rtd.MAX31865(spi, cs=17, wire_mode=3, filter_hz=50)
        three_wire.begin()
        self.assertEqual(spi.writes[-1][0], 0x80)  # config write
        self.assertEqual(spi.writes[-1][1], 0x80 | 0x40 | 0x10 | 0x01)

        four_wire = rtd.MAX31865(spi, cs=18, wire_mode=4, filter_hz=60)
        four_wire.begin()
        self.assertEqual(spi.writes[-1][1], 0x80 | 0x40)


class OtaManifestTests(unittest.TestCase):
    """The manifest is the contract; half of it must never be applied."""

    def entry(self, **overrides):
        values = {"name": "main.py", "size": 12, "sha256": "a" * 64}
        values.update(overrides)
        return values

    def test_a_complete_manifest_is_accepted(self):
        manifest = ota.parse_manifest(
            {"version": "20261103-120000", "files": [self.entry()]}
        )

        self.assertEqual(manifest["version"], "20261103-120000")
        self.assertEqual(manifest["files"], [{"name": "main.py", "size": 12, "sha256": "a" * 64}])

    def test_unsafe_file_names_are_rejected(self):
        for name in ("../config.py", "/etc/passwd", "a/b.py", "", ".hidden", "x" * 65):
            with self.assertRaises(ValueError, msg=name):
                ota.parse_manifest({"version": "v1", "files": [self.entry(name=name)]})

    def test_a_truncated_file_list_is_rejected(self):
        for files in (
            [],
            None,
            [self.entry(sha256="abc")],
            [self.entry(size=999999)],
            [self.entry(size=-1)],
            ["main.py"],
        ):
            with self.assertRaises(ValueError):
                ota.parse_manifest({"version": "v1", "files": files})

    def test_a_manifest_without_a_version_is_rejected(self):
        with self.assertRaises(ValueError):
            ota.parse_manifest({"files": [self.entry()]})

    def test_bundle_channels_are_lower_case_names(self):
        self.assertTrue(ota.valid_channel("max31855"))
        self.assertTrue(ota.valid_channel("max31865"))
        self.assertFalse(ota.valid_channel("Max31855"))
        self.assertFalse(ota.valid_channel("../x"))
        self.assertFalse(ota.valid_channel("-"))


class OtaBundleTests(unittest.TestCase):
    """Staging, applying, confirming and rolling back on a real directory."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="furnace-ota-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        os.makedirs(os.path.join(self.root, "ota", "stage"))
        os.makedirs(os.path.join(self.root, "ota", "backup"))

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def write(self, path, text):
        with open(path, "wb") as handle:
            handle.write(text.encode() if isinstance(text, str) else text)

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read().decode()

    def stage(self, version, files):
        entries = []

        for name, text in files.items():
            data = text.encode()
            self.write(self.path("ota", "stage", name), data)
            entries.append({"name": name, "size": len(data), "sha256": ota.sha256_hex(data)})

        self.write(
            self.path("ota", "pending.json"),
            json.dumps({"version": version, "files": entries}),
        )

    def test_applying_replaces_files_and_keeps_a_backup(self):
        self.write(self.path("wifi.py"), "OLD = 1\n")
        self.stage("v2", {"wifi.py": "NEW = 2\n", "ota.py": "OTA = 1\n"})

        version = ota.apply_pending(self.root, log=lambda *_: None)

        self.assertEqual(version, "v2")
        self.assertEqual(self.read(self.path("wifi.py")), "NEW = 2\n")
        self.assertEqual(self.read(self.path("ota", "backup", "wifi.py")), "OLD = 1\n")
        self.assertEqual(self.read(self.path("ota", "version")), "v2")
        self.assertFalse(os.path.exists(self.path("ota", "pending.json")))
        self.assertEqual(os.listdir(self.path("ota", "stage")), [])

    def test_a_wrong_checksum_is_never_applied(self):
        self.write(self.path("main.py"), "OLD = 1\n")
        self.write(self.path("ota", "stage", "main.py"), "TAMPERED = 1\n")
        self.write(
            self.path("ota", "pending.json"),
            json.dumps({"version": "v2", "files": [
                {"name": "main.py", "size": 12, "sha256": "a" * 64},
            ]}),
        )

        with self.assertRaises(ValueError):
            ota.apply_pending(self.root, log=lambda *_: None)

        self.assertEqual(self.read(self.path("main.py")), "OLD = 1\n")

    def test_a_syntax_error_is_never_applied(self):
        self.write(self.path("ota", "stage", "main.py"), "def broken(:\n")
        data = b"def broken(:\n"
        self.write(
            self.path("ota", "pending.json"),
            json.dumps({"version": "v2", "files": [
                {"name": "main.py", "size": len(data), "sha256": ota.sha256_hex(data)},
            ]}),
        )

        with self.assertRaises(SyntaxError):
            ota.apply_pending(self.root, log=lambda *_: None)

        self.assertFalse(os.path.exists(self.path("main.py")))

    def test_confirm_keeps_the_update_and_drops_the_backup(self):
        self.write(self.path("wifi.py"), "OLD = 1\n")
        self.stage("v2", {"wifi.py": "NEW = 2\n"})
        ota.apply_pending(self.root, log=lambda *_: None)
        self.write(self.path("ota", "boots"), "1")

        self.assertTrue(ota.confirm(self.root, log=lambda *_: None))
        self.assertFalse(os.path.exists(self.path("ota", "backup.json")))
        self.assertFalse(os.path.exists(self.path("ota", "boots")))
        self.assertEqual(os.listdir(self.path("ota", "backup")), [])

    def test_three_unconfirmed_boots_roll_back(self):
        self.write(self.path("wifi.py"), "BROKEN = 1\n")
        self.write(self.path("ota", "backup", "wifi.py"), "GOOD = 1\n")
        self.write(self.path("ota", "backup.json"), json.dumps({"files": ["wifi.py"]}))
        self.write(self.path("ota", "boots"), "3")
        resets = []

        class Reboot(Exception):
            pass

        fake = types.SimpleNamespace(reset=lambda: (resets.append(1), (_ for _ in ()).throw(Reboot()))[1])

        with mock.patch.object(ota, "machine", fake):
            with self.assertRaises(Reboot):
                ota.boot(self.root, log=lambda *_: None)

        self.assertEqual(resets, [1])
        self.assertEqual(self.read(self.path("wifi.py")), "GOOD = 1\n")
        self.assertFalse(os.path.exists(self.path("ota", "backup.json")))

    def test_a_staged_bundle_is_applied_before_main_py(self):
        self.stage("v3", {"main.py": "VALUE = 3\n"})
        resets = []

        class Reboot(Exception):
            pass

        fake = types.SimpleNamespace(reset=lambda: (resets.append(1), (_ for _ in ()).throw(Reboot()))[1])

        with mock.patch.object(ota, "machine", fake):
            with self.assertRaises(Reboot):
                ota.boot(self.root, log=lambda *_: None)

        self.assertEqual(self.read(self.path("main.py")), "VALUE = 3\n")
        self.assertEqual(resets, [1])


class FirmwareServer(http.server.BaseHTTPRequestHandler):
    """The two OTA endpoints, close enough to the PHP API for the client."""

    manifest = {}
    files = {}
    requests = []

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        FirmwareServer.requests.append({
            "path": parsed.path,
            "query": query,
            "key": self.headers.get("X-Api-Key"),
        })

        if parsed.path == "/api/firmware/manifest":
            body = json.dumps(FirmwareServer.manifest).encode()
            status = 200
        elif parsed.path == "/api/firmware/file":
            body = FirmwareServer.files.get((query.get("name") or [""])[0])
            status = 200 if body is not None else 404
        else:
            body = b"{}"
            status = 404

        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class OtaClientTests(unittest.TestCase):
    """The board side: fetch, verify and stage; never touch a bad file."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FirmwareServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="furnace-ota-client-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        FirmwareServer.requests = []
        FirmwareServer.files = {"main.py": b"VALUE = 2\n"}
        FirmwareServer.manifest = {
            "version": "v2",
            "files": [{
                "name": "main.py",
                "size": len(b"VALUE = 2\n"),
                "sha256": ota.sha256_hex(b"VALUE = 2\n"),
            }],
        }

    def updater(self, **overrides):
        values = {
            "API_URL": "http://127.0.0.1:%d" % (self.port,),
            "API_KEY": "secret",
            "OTA_ENABLED": True,
            "OTA_CHANNEL": "max31855",
            "OTA_TIMEOUT_S": 5,
            "TLS_VERIFY": False,
            "OTA_CHECK_DELAY_S": 0,
            "OTA_CHECK_S": 60,
        }
        values.update(overrides)

        return ota.Updater(types.SimpleNamespace(**values), root=self.root, log=lambda *_: None)

    def test_a_bundle_is_downloaded_verified_and_staged(self):
        updater = self.updater()

        self.assertTrue(updater.check())
        self.assertEqual(updater.ready, "v2")

        with open(os.path.join(self.root, "ota", "stage", "main.py"), "rb") as handle:
            self.assertEqual(handle.read(), b"VALUE = 2\n")

        with open(os.path.join(self.root, "ota", "pending.json"), "rb") as handle:
            pending = json.loads(handle.read())

        self.assertEqual(pending["version"], "v2")
        self.assertEqual(FirmwareServer.requests[0]["key"], "secret")
        self.assertEqual(FirmwareServer.requests[0]["query"]["channel"], ["max31855"])

    def test_the_same_version_is_not_downloaded_again(self):
        os.makedirs(os.path.join(self.root, "ota"))
        with open(os.path.join(self.root, "ota", "version"), "wb") as handle:
            handle.write(b"v2")

        self.assertFalse(self.updater().check())
        self.assertEqual([request["path"] for request in FirmwareServer.requests], ["/api/firmware/manifest"])

    def test_files_that_already_match_are_not_downloaded(self):
        FirmwareServer.files = {}
        FirmwareServer.manifest.update({"version": "v9"})
        with open(os.path.join(self.root, "main.py"), "wb") as handle:
            handle.write(b"VALUE = 2\n")

        self.assertFalse(self.updater().check())
        self.assertEqual([request["path"] for request in FirmwareServer.requests], ["/api/firmware/manifest"])

        with open(os.path.join(self.root, "ota", "version"), "rb") as handle:
            self.assertEqual(handle.read(), b"v9")

    def test_a_bad_checksum_is_rejected(self):
        FirmwareServer.files = {"main.py": b"EVIL = 1\n"}

        with self.assertRaises(OSError):
            self.updater().check()

        self.assertFalse(os.path.exists(os.path.join(self.root, "ota", "pending.json")))

    def test_a_missing_bundle_is_not_an_error(self):
        # The PHP API answers 404 when nothing has been published yet; the
        # board must keep sampling and try again at the next interval.
        updater = self.updater()
        updater._get = lambda path: (404, b"")

        self.assertFalse(updater.check())
        self.assertIn("no bundle", updater.last_error)


class PublishTests(unittest.TestCase):
    """The bundle publisher used on the operator's machine."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="furnace-publish-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.out, ignore_errors=True))
        self.publish = load(
            "esp32s3_publish",
            os.path.join(ROOT, "tools", "publish.py"),
        )

    def test_a_bundle_has_the_firmware_but_never_config_py(self):
        manifest = self.publish.publish("max31855", out_dir=self.out, version="test-1")
        target = os.path.join(self.out, "max31855")
        names = {entry["name"] for entry in manifest["files"]}

        self.assertEqual(
            names,
            {"boot.py", "wifi.py", "ingest.py", "supervisor.py", "ota.py", "device.py", "main.py"},
        )
        self.assertNotIn("config.py", names)
        self.assertTrue(os.path.isfile(os.path.join(target, "manifest.json")))

        with open(os.path.join(target, "manifest.json"), encoding="utf-8") as handle:
            stored = json.load(handle)

        self.assertEqual(stored["version"], "test-1")
        self.assertEqual(stored["channel"], "max31855")

        for entry in stored["files"]:
            with open(os.path.join(target, entry["name"]), "rb") as handle:
                data = handle.read()

            self.assertEqual(len(data), entry["size"])
            self.assertEqual(ota.sha256_hex(data), entry["sha256"])

    def test_the_rtd_bundle_carries_its_driver(self):
        manifest = self.publish.publish("max31865", out_dir=self.out, version="test-2")
        names = {entry["name"] for entry in manifest["files"]}

        self.assertIn("max31865.py", names)
        self.assertIn("main.py", names)


class WifiOverrideTests(unittest.TestCase):
    """A Wi-Fi change must not require editing config.py on the board."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="furnace-wifi-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.config = types.SimpleNamespace(WIFI_SSID="home", WIFI_PASSWORD="secret")

    def test_config_py_is_used_without_an_override(self):
        self.assertEqual(wifi.credentials(self.config, root=self.root), ("home", "secret"))

    def test_a_saved_override_wins(self):
        wifi.save_override("furnace-net", "newpass", root=self.root)

        self.assertEqual(
            wifi.credentials(self.config, root=self.root),
            ("furnace-net", "newpass"),
        )


class DeviceLinkTests(unittest.TestCase):
    """Heartbeat payload and the three commands the admin panel can send."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="furnace-device-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def make(self, **overrides):
        values = {
            "API_URL": "http://127.0.0.1:9",
            "API_KEY": "",
            "DEVICE_ID": "esp32-1",
            "DEVICE_NAME": "Furnace monitor",
            "HEARTBEAT_S": 15,
            "HEARTBEAT_DELAY_S": 0,
            "API_TIMEOUT_S": 2,
            "TLS_VERIFY": False,
        }
        values.update(overrides)
        wlan = types.SimpleNamespace(
            ifconfig=lambda: ("192.168.1.42", "255.255.255.0", "192.168.1.1", "1.1.1.1"),
            status=lambda name=None: -57,
        )
        ingest_client = types.SimpleNamespace(sent=10, pending=[], dropped=1)
        updater = types.SimpleNamespace(
            local_version=lambda: "v1",
            check=lambda: True,
            ready="v2",
        )
        link = device.DeviceLink(
            types.SimpleNamespace(**values),
            wlan,
            ingest_client,
            updater,
            channels=[1, 2, 3],
            log=lambda *_: None,
            root=self.root,
        )

        return link, updater

    def test_the_payload_reports_the_board(self):
        link, _ = self.make()
        payload = link.payload()

        self.assertEqual(payload["id"], "esp32-1")
        self.assertEqual(payload["channels"], [1, 2, 3])
        self.assertEqual(payload["rssi"], -57)
        self.assertEqual(payload["ip"], "192.168.1.42")
        self.assertEqual(payload["firmware"], "v1")
        self.assertEqual(payload["sent"], 10)

    def test_an_invalid_device_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make(DEVICE_ID="../esp32")

    def test_a_wifi_command_is_saved_and_acknowledged(self):
        link, _ = self.make()
        acks = []
        reboots = []
        link._ack = lambda *args: acks.append(args)
        link._reboot = lambda: reboots.append(1)

        link._run({"id": 7, "kind": "wifi", "payload": {"ssid": "new-net", "password": "pw"}})

        self.assertEqual(acks, [(7, "done", "saved, restarting")])
        self.assertEqual(reboots, [1])

        with open(os.path.join(self.root, "wifi.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"ssid": "new-net", "password": "pw"})

    def test_a_wifi_command_without_an_ssid_fails(self):
        link, _ = self.make()
        acks = []
        reboots = []
        link._ack = lambda *args: acks.append(args)
        link._reboot = lambda: reboots.append(1)

        link._run({"id": 8, "kind": "wifi", "payload": {"ssid": ""}})

        self.assertEqual(acks, [(8, "failed", "empty ssid")])
        self.assertEqual(reboots, [])

    def test_an_ota_command_runs_one_check(self):
        link, updater = self.make()
        calls = []
        updater.check = lambda: (calls.append(1), True)[1]
        acks = []
        link._ack = lambda *args: acks.append(args)

        link._run({"id": 9, "kind": "ota", "payload": {}})

        self.assertEqual(calls, [1], "the command must trigger exactly one check")
        self.assertEqual(acks, [(9, "done", "staged v2")])

    def test_a_restart_command_reboots_after_acking(self):
        link, _ = self.make()
        acks = []
        reboots = []
        link._ack = lambda *args: acks.append(args)
        link._reboot = lambda: reboots.append(1)

        link._run({"id": 10, "kind": "restart", "payload": {}})

        self.assertEqual(acks, [(10, "done", "restarting")])
        self.assertEqual(reboots, [1])


if __name__ == "__main__":
    unittest.main()
