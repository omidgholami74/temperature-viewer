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
import types
import unittest

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
        client = ingest.Ingest(self.config(), log=lambda *_: None)
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
        client = ingest.Ingest(self.config(BATCH_SIZE=1), log=lambda *_: None)
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])

        self.assertNotIn("at", Recorder.received[0]["payload"]["samples"][0])

    def test_unreachable_api_keeps_the_batch_for_a_retry(self):
        client = ingest.Ingest(
            self.config(API_URL="http://127.0.0.1:1", BATCH_SIZE=1),
            log=lambda *_: None,
        )
        client.add(100, [1, 2, 3, 4], [0, 0, 0, 0])

        self.assertEqual(len(client.pending), 1)
        self.assertEqual(client.failures, 1)
        self.assertTrue(client.last_error)

    def test_rejected_payload_is_dropped_instead_of_blocking_the_queue(self):
        Recorder.status = 422
        client = ingest.Ingest(self.config(BATCH_SIZE=1), log=lambda *_: None)
        client.add(100, [9999, 2, 3, 4], [0, 0, 0, 0])

        self.assertEqual(client.pending, [])
        self.assertEqual(client.dropped, 1)

    def test_buffer_limit_drops_the_oldest_samples(self):
        Recorder.status = 503
        client = ingest.Ingest(self.config(BATCH_SIZE=2, BUFFER_LIMIT=4), log=lambda *_: None)

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
        finally:
            thread.join(5)
            server.close()

        self.assertTrue(client.pending == [], "the flushed batch is still queued")

        lines = captured["head"].split("\r\n")
        self.assertEqual(lines[0], "POST /api/ingest HTTP/1.1")
        self.assertIn("Host: 127.0.0.1", captured["head"])
        self.assertIn("Content-Type: application/json", captured["head"])
        self.assertIn("X-Api-Key: k", captured["head"])
        self.assertIn("Connection: close", captured["head"])
        self.assertIn("Content-Length: %d" % (len(captured["body"]),), captured["head"])
        self.assertEqual(
            captured["body"],
            '{"samples": [{"u": 100, "t": [1.0, 2.0, 3.0, 4.0], "f": [0, 0, 0, 0]}]}',
        )


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
