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
import threading
import time
import types
import unittest
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


if __name__ == "__main__":
    unittest.main()
